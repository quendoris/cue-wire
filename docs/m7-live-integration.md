# CueWire M7.2 — live terminal control integration and verification

**Status:** implemented experimental UI integration with the EXISTING
foreground CueWire v0.1 engine. It does **not** enable M6 audio DSP.

## What is actually connected

The same six-pane M7.1 TUI and original Quendoris mark now support
TWO STRICTLY SEPARATE operator modes:

| CLI | Connects to engine? | Can change audio state? |
|---|---|---|
| --snapshot | Never | Never |
| --demo | Never | Never, purely simulated |
| --live-check | Read-only socket status and PipeWire graph | Never |
| --live | Yes, EXPLICIT opt-in | Only the already-implemented play N, stop and confirmed quit |

The --live mode DOES NOT start CueWire, select audio defaults, register
Hyprland hotkeys, open a microphone, record speech, generate a device,
change volume or install a service. It uses the EXISTING user-owned
XDG_RUNTIME_DIR/cuewire-v0.1.sock. The engine must already be running.

The live client reuses tools/cuewire_console.py, including its strict
command allowlist and same-user regular Unix socket mode 0600 checks.
It never executes shell commands or transmits arbitrary text.
Status is read every ~3 seconds on the foreground UI loop (a single
read is bounded by a 500ms socket timeout; action requests by 1.2s). Each read
is fresh; if it fails, the live panel immediately becomes OFFLINE
rather than displaying a stale "connected" state.

## Panels and truthful status

- Runtime: LIVE/RAW transport, runtime responding/offline,
  socket ACL result, **M6 is NOT in live DSP**.
- Devices: MIC, monitor and virtual-source identifiers obtained from
  the real CueWire v0.1 STATUS protocol. These are reported identifiers,
  not proof that audio samples are flowing.
- Research: M4 offline, M5 research, M6-007 offline accepted, M7 live
  control UI only.
- Cue State: current cue reported by Rust supervisor, not simulated.
  There is NO fake VU level, route throughput or gain telemetry.
- Safety: the UI itself starts no engine, checks socket mode, does
  not change defaults, and explicitly displays that cleanup is not
  independently tested yet.
- Audit: live read-only doctor checks socket ownership/0600, valid
  control status, and PipeWire presence of expected nodes. Runtime
  status alone does not prove the graph works or that voice is clean.

The M7.1 layout and gradient stay unchanged. Enter expands the
single-line command dock; Tab or Esc collapses. Quit requires the
EXACT next command CONFIRM, and unlike the demo it sends the existing
Rust "quit" request only AFTER a fresh connection check. It does
NOT claim that every process has terminated until separately verified.
Ctrl+C or "exit" closes only the UI and leaves the engine intact.

## Read-only doctor: verified vs not verifiable

tools/cuewire_tui_live.py implements doctor() as strictly read-only:

1. Verify XDG_RUNTIME_DIR is genuine/owned and the existing socket
   is a genuine same-user Unix socket with EXACT mode 0600.
2. Send only "status" over that socket. Reject invalid control
   messages and unexpected fields.
3. Run only "pw-dump", bounded to 2s. Parse its JSON without
   modifying PipeWire graph; check exact advertised node names for
   physical MIC, monitor, virtual source and mix input.
4. For the CueWire virtual source, require media.class Audio/Source;
   for input mix, Audio/Sink.
5. Mark absent, duplicated and malformed graph nodes FAIL; lack of
   verified connection marks graph SKIP. Echo suppression, physical
   signal flow, actual voice preservation, stream latency, CPU, XRUN,
   supervision and emergency Super+C are still UNVERIFIED / SKIP.

The --live-check command fails with exit status 2 on a missing
engine, a failing check or an incomplete graph; SKIP for unimplemented
M6 DSP is not falsely reported as PASS. It can be used without a TTY.

## Run tests before touching real audio

~~~bash
cd /data/cue-wire
git fetch origin
git switch feature/m7-terminal-interface
git pull --ff-only

python3 -m unittest discover -s tests -p 'test_cuewire_tui.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_tui_live.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_console.py' -v
cargo test --locked --offline
~~~

The 16 new integration tests use an entirely synthetic private
Unix socket server and MOCKED pw-dump; they do NOT open any
real microphone, launch PipeWire, play sounds or alter system audio.
The 17 prior M7.1 TUI tests and 11 previous console tests must remain
green too. These are source-level tests and do not by themselves
constitute hardware acceptance.

## Run the existing Rust audio engine MANUALLY

First inspect actual device node names on this machine. Names can
change after USB hubs, Bluetooth re-pairing or device reconnect:

~~~bash
./target/release/cuewire doctor
wpctl status
pw-dump | grep -E 'node.name|media.class'
~~~

In a SEPARATE foreground terminal, after building and selecting exact
known mic and monitor node names and a real local cue WAV:

~~~bash
cargo build --release --locked --offline
# Explicitly substitute the confirmed physical nodes and cue WAV:
./target/release/cuewire run \
  --mic 'REAL_MIC_NODE_NAME' \
  --monitor 'REAL_OUTPUT_NODE_NAME' \
  --cue '4=/absolute/path/to/test.wav' \
  --latency-ms 25
~~~

The 25ms flag is a REQUEST, not a measured latency. The source name
CueWire Microphone must be selected manually in Steam/game; never
replace default OS devices.

To use the existing separately implemented emergency cgroup mechanism
instead of directly launching the engine, use an EXPLICIT supervised
foreground scope (not a persistent service):

~~~bash
bash tools/cuewire_scope.sh check

bash tools/cuewire_scope.sh run -- \
  --mic 'REAL_MIC_NODE_NAME' \
  --monitor 'REAL_OUTPUT_NODE_NAME' \
  --cue '4=/absolute/path/to/test.wav' \
  --latency-ms 25
~~~

Emergency from a separate terminal is limited to the correctly marked
CueWire scope:

~~~bash
bash tools/cuewire_scope.sh emergency
~~~

This does NOT install or validate a global Hyprland Super+C binding,
and does not include the separately launched TUI in that cgroup.
Comprehensive cross-process Super+C acceptance remains pending.
 If it is desirable to tie emergency
Super+C to this live session, use the pre-existing opt-in supervised
tools/cuewire_scope.sh and separately validated Hyprland binding,
rather than assuming Ctrl+C in the TUI kills an external engine.

## Then start the REAL console, separately

~~~bash
python3 tools/cuewire_tui.py --live-check
python3 tools/cuewire_tui.py --live
~~~

- Check Runtime says responding and Devices shows actual names.
- Enter -> status: verify an unmodified Rust v0.1 status response.
- Enter -> doctor: verify socket PASS, protocol PASS, and that
  FOUR expected PipeWire nodes are registered; M6 DSP remains SKIP.
- Enter -> play 4: the already configured WAV is targeted into
  CueWire's existing owned virtual mic and named monitor.
- Enter -> stop: stop that cue, NOT the physical mic or engine.
- Enter -> quit, then CONFIRM: request engine shutdown. Inspect
  cleanup separately; do NOT assume requested means verified.
- "exit" and Ctrl+C close ONLY the operator UI. The running Rust
  owner remains running, by design. Super+C is a separate scope
  emergency requirement, not installed automatically here.

For an initial SAFE live UI smoke test with no engine running, invoke
--live: it will show OFFLINE and refuse play/stop/quit.
For a strictly non-audio demonstration, use --demo.

## Acceptance gates and explicit limits

- [ ] New 16 synthetic socket/doctor tests PASS on user's Arch.
- [ ] 17 TUI tests, 11 console tests and cargo tests remain green.
- [ ] Safe --live-check OFFLINE failure behaves as expected.
- [ ] Existing engine starts manually without changing defaults.
- [ ] Live Runtime/Devices match read-only PipeWire inspection.
- [ ] play/stop actually work on a real configured cue WAV.
- [ ] External engine exit/restart immediately updates LIVE status.
- [ ] Shutdown and Super+C cgroup cleanup independently verified;
      no owned child processes or leaked virtual audio nodes.
- [ ] Audio quality, latency, stability and hardware disconnect
      behavior independently tested before calling production ready.
- [ ] Full CAUSAL M6-007 port remains a separate streaming project.

No physical-test boxes are pre-checked in GitHub. The user should
report failures; do not bypass any safety gate just to gain green tests.

## Physical M7.2 interaction observation — 2026-11-10

The operator supplied actual Arch Linux / Hyprland screenshots showing
the six-pane LIVE interface, the expanded command dock and a separately
running Rust CueWire engine. This is real interaction evidence, not
synthetic test success.

Observed:
- Read-only live-check passed same-user socket 0600, strict v0.1 status,
  physical mic, monitor, virtual source and mix input (four registered
  PipeWire nodes). M6/latency correctly remained SKIP.
- The LIVE Runtime panel shows engine responding / socket PASS and the
  Devices panel shows actual (sometimes truncated) node names.
- The expanded dock displayed the acknowledgement
  LIVE ACK: OK cue stopped. This verifies a supervisor response to
  stop, not independently the resulting sound at every endpoint.
- After doctor the Audit panel showed PASS / 4 nodes registered, while
  DSP/latency remained SKIP.
- The operator described the interactive interface as working.

Still UNVERIFIED: audible play on both monitor and virtual mic, audio
quality, measured latency, preservation of system defaults, all child
process cleanup, global Super+C, and causal M6-007.

Next UI polish: long PipeWire node names and diagnostic details are
visibly truncated in compact panels. Add an explicit focused-pane
detail view without abandoning the six-panel overview.

No private recording, event timing or device-specific identifiers
have been committed to this public research note.
