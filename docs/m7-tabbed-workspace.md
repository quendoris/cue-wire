# CueWire M7.5 — tabbed operator workspace prototype

Opt-in UI extension on the already tested M7.3/M7.4 foreground owner.
The existing six-pane Overview and collapsed/expanded command dock remain
the baseline. The workspace replaces the upper region on demand; it
does not replace the Rust engine or the established process lifecycle.

## Five tabs

- Overview: original six panels.
- Devices: actual v0.1 mic, monitor, virtual source, mix names, socket
  and protocol; wrapped and scrollable, with recent doctor details.
  No measured signal levels, sample rate, XRUN count or latency yet.
  Device selection remains explicit --mic / --monitor on restart;
  no automatic device/default modification.
- Bindings: local symbolic keymap, NOT evidence of registered global
  bindings. Use the existing dock's keys / keys cue:4 / bind cue:4 F8 /
  unbind cue:4 commands. Multiword modifier chords are supported.
  Demo never reads or writes the real private keymap.
- Library: explicitly requested top-level WAV folder, bounded to 512
  directory entries; no symlink files, recursive traversal, decoding,
  network requests or automatic playback. Numbered list, RUN / STAGED /
  SAVED slots distinguish this Rust session from the next one.
- Mixer: four separately selectable gain strips (Voice, Music local,
  Music send, Master send), and adaptive outlined NumPad with wide zero
  key. Smaller terminals use compact buttons. The cue highlight comes
  from the active cue reported by Rust, not from a guessed key event.
  The managed launcher passes the actual configured cue filenames,
  not the mutable next-session profile. A standalone live TUI cannot
  infer cue filenames that v0.1 does not report.

The animated Quendoris field and exact skull geometry remain.
The original command dock and footer are rendered by the original
renderer, untouched by the new workspace overlay.

## Running without risk

~~~bash
cd /data/cue-wire
git pull --ff-only
python3 -m unittest discover -s tests -p 'test_cuewire_workspace.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_tui.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_tui_live.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_console.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_session.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_keymap.py' -v
cargo test --locked --offline
cargo build --release --locked --offline

python3 tools/cuewire_tui.py --snapshot --workspace --workspace-tab Mixer
python3 tools/cuewire_tui.py --demo --workspace --workspace-tab Mixer
~~~

Tab / Shift+Tab switch upper tabs in window focus.
Enter opens the existing command dock; Esc or Tab from command focus
collapses it without changing the selected tab. Up/Down scroll long
views. In Overview, arrows retain the six-panel navigation.
In Mixer, Up/Down selects one gain; Left/Right changes it by 5 percent
in DEMO only; digit keys 0..9 toggle cues, decimal stops cues.
These are terminal-local controls, not a global keyboard listener.

After all tests pass, stop any old CueWire session, then:

~~~bash
python3 tools/cuewire_session.py --run --workspace
~~~

The previous --run and standalone --live without --workspace remain
unchanged. An already running v0.1 Rust engine may also be inspected
with python3 tools/cuewire_tui.py --live --workspace.

## Music folder commands in the unchanged dock

Example:

~~~text
library /data/oae-research
slot 4 1
save library
~~~

The library command scans only the explicitly supplied directory.
The slot command stages one listed WAV onto a slot. save library is
an explicit request to merge assignments into the existing private
0600 cue profile under ignored .audit. It validates paths via the
existing session profile writer; no running cue is reconfigured.
Changes take effect on the next managed launch. In demo all edits
remain in memory and sample file names are fictional UI examples.

The existing console still accepts ASCII input with a 96-character
command limit. Unicode names are safely escaped in the list; use a
short absolute path when opening a directory. No automatic copying
or transcoding of library files.

## Gain contract (NOT implemented in the live engine)

The live mixer shows four N/A controls, not fictional current values.
The intended independent stages are:

    local_monitor = gain_music_local * cue
    virtual_mic = limiter(gain_master_send *
                         (gain_voice * processed_voice +
                          gain_music_send * cue))

The demo percentages are presentation state only. The actual live
engine must provide separately owned gain stages, acknowledged
control commands, clipping protection, sample/peak telemetry and
an independent verification gate. Do not substitute wpctl set-volume
on the system default device, or claim that M6-007 is live.

## Verification limits and next milestone

13 new synthetic tests cover unchanged dock/footer at different
sizes, tabs and focus, independent demo gains, disabled live gains,
confirmed cue status, strict toggle allowlist, explicit library
scan/staging/profile saves, no demo file reads, long name wrapping,
and static snapshot isolation. Tests are written, not a claim that
they have passed on the operator's Arch system.

Dynamic global hotkeys are NOT installed by this workspace.
They remain a separate session-ownership integration: explicit
Hyprland bind registration, conflict preflight, exact verification,
and removal by the surviving outer guardian on ordinary exit and
scope SIGKILL. The previously existing opt-in static Lua bindings
are not activated or modified by the new UI. No global key events
are captured. Terminal-local digits and server-reported cue state
do not prove game-wide hotkey delivery.

Preserve M6-007 as an accepted offline reference. No new daemon,
autostart, OS default modification, keylogging or physical audio
capture is introduced by the workspace. Physical device switching,
real independent mixer gains, hotkey session cleanup and audio
performance must be separately verified before production use.
