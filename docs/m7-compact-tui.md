# CueWire M7.1 — compact Quendoris terminal prototype

**Implementation:** tools/cuewire_tui.py  
**Status:** runnable DEMO / STATIC SNAPSHOT, no PipeWire connection.

This is a real executable terminal UI prototype, not a mockup and not a
live virtual microphone. It deliberately keeps the already accepted M6
offline DSP untouched. There are NO third-party Python dependencies,
web assets, GUI windows, background timers, network clients or installs.

## Visual source and fidelity

The original Quendoris logo (the "черепок") and restrained dark colors
come from the independently verified public site source:

- https://github.com/quendoris/site/blob/main/dist/assets/b-reference-mark.svg
- https://github.com/quendoris/site/blob/main/dist/assets/b-reference-b.css
- https://github.com/quendoris/site/blob/main/design/reference-b-v06/README.md

The logo geometry is the **exact accepted 420x420 SVG fill polygon**,
including BOTH eye apertures and the lower notch, rasterized analytically
onto terminal character cells. It is NOT a newly invented cartoon skull.
The 420x420 filled bitmap has 89,249 pixels, matching the source site's
independent pixel-equivalence count. The shaded mark is a backdrop
watermark and is also represented with subtle dots in mono previews.

Colors stay close to the site:
- dark surface #142024;
- muted teal field #203940 and #334648;
- panel surface #1a2a2e;
- restrained primary accent #d3e08b.

A 79s phase slowly shifts the TrueColor light field, with a second
slower variation. It never changes status meanings or animates text.
Use --no-motion or NO_COLOR to freeze motion, or --mono for no
TrueColor. This is ANSI terminal artwork, not graphical image display:
the logo is a character-resolution raster of the same geometry.

## Exact compact layout

A two-row, three-column system with SIX panels at the top:

| Row | Left | Middle | Right |
|---|---|---|---|
| Upper | Runtime | Devices | Research |
| Lower | Cue State | Safety | Audit / Checks |

The bottom command dock is always present:
- WINDOW focus: dock is **exactly one terminal row**.
- Press Enter, colon or slash: focus enters command, dock expands
  to 5–9 rows according to terminal size, displaying latest audit
  messages and a text prompt.
- Press Tab or Shift+Tab FROM command: focus returns to windows and
  dock immediately collapses to one row.
- Press Esc: discard the uncommitted command, cancel shutdown
  confirmation and return to windows (dock collapses).
- In WINDOW focus, Tab and arrows select the six panels. In COMMAND
  focus, type permitted demo commands and press Enter.
- Enter alone never executes an uncommitted command from window focus.

No floating dialogs or GUI is required. Minimum terminal size is
68 columns by 23 rows; below that, show a readable resize warning.
Terminal resize is detected, and the screen is redrawn without
scrolling the bottom row.

## Demo commands — intentionally no real controls

- status: synthetic offline runtime status.
- play 0..9: only changes in-memory simulated cue state; NO sound.
- stop: clear simulated cue state; no audio process exists.
- help: display navigation and accepted command summary.
- research: select Research; M6-007 is accepted OFFLINE, not live.
- doctor: select Audit; mark demo-isolation checks only.
  Actual socket permissions, physical device state, live DSP,
  PipeWire, process cleanup and latency are deliberately SKIPPED.
- quit: require the exact next command CONFIRM before closing demo.
- exit or Ctrl+C: exit demo, restoring TTY in normal operation.

The console uses only stdin from its OWN foreground terminal and
never opens /dev/input, microphone, PipeWire or Unix sockets.
There are no logs, config changes, system audio changes, daemons,
persistent terminal settings or audio recording. User-entered commands
are held in local in-memory variables and are not saved to files.
Unknown commands are refused; no shell expansion/exec is implemented.

The TUI restores alternate-screen state, cursor visibility and the
prior termios configuration on normal exit and handled SIGTERM/SIGHUP.
SIGKILL by definition cannot execute cleanup; if the TTY is
forcibly killed in raw mode, run stty sane in that terminal.
The existing independently scoped Super+C mechanism is NOT installed
or modified by this prototype.

## On Arch / Hyprland — preview without any device access

~~~bash
cd /data/cue-wire
git fetch origin
git switch feature/m7-terminal-interface
git pull --ff-only

python3 -m unittest discover -s tests \
  -p 'test_cuewire_tui.py' -v

python3 tools/cuewire_tui.py --snapshot
python3 tools/cuewire_tui.py --demo
~~~

The snapshot prints an ordinary 100x32 text composition to stdout,
with no ANSI escape sequences and no terminal state changes.

For reduced motion or a limited terminal palette:

~~~bash
python3 tools/cuewire_tui.py --demo --no-motion
python3 tools/cuewire_tui.py --demo --mono
~~~

For review: resize the terminal, Tab through all six panes,
Enter to open the command dock, type play 4 then Enter, inspect Cue
State; type doctor, inspect Audit / Checks; Esc/Tab to collapse.
Try invalid commands. Confirm that the demo does not start any
audio node or process or change your existing PipeWire defaults.

## Test contract

17 synthetic unit tests:
1. Exact SVG geometry and 89,249-pixel filled area.
2. Recognizable logo cutouts in terminal raster.
3. Six nonoverlapping, responsive panels at multiple sizes,
   with collapsed and expanded command dock.
4. Dock focus, command dispatch and automatic collapse.
5. Window navigation through Tab/arrows/Shift+Tab.
6. Demo cue switching and honest research/doctor states.
7. Unknown/unsafe command refusal.
8. Quit CONFIRM, cancellation by Esc, no unconfirmed exit.
9. Command length bound, Ctrl+U, Backspace and Ctrl+C.
10. Escape-sequence parser for arrows, Tab and controls.
11. Stateless no-device snapshot output.
12. TrueColor positioning without last-row scroll.
13. Deterministic reduced-motion screenshot.
14. Safe tiny-terminal fallback.
15. Normal-exit restoration of termios, cursor and alternate-screen state.
16. Non-TTY rejection before TTY mutation.
17. NO_COLOR disables colors and needless animation.

*These tests are committed, but must be executed on the user's Arch
before being described as passing there.*

## Deliberate integration boundary

The existing foreground tool tools/cuewire_console.py already has
the safe v0.1 Unix control protocol and can be used separately for
real engine status/play/stop. This visual TUI currently has NO live
connection. It must not claim gain, VU-meter level, per-frame DSP,
latency, virtual microphone health or real process guarantees until
those telemetry values actually exist and are tested.

After visual/user approval, M7.2 can reuse the existing validated
control client as a deliberately opt-in LIVE backend with exact
same-user 0600 socket checks and read-only doctor probes, preserving
the simple safe --demo path. No speculative GUI or system-level
installation is needed.
