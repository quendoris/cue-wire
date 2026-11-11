# CueWire M7.4 — safely update an existing hyprland.conf

**Compatibility:** only for legacy Hyprland 0.54 and earlier.
Hyprland 0.55+ changed its configuration language to Lua. The updater
refuses legacy .conf modifications on newer versions. A filename
alone cannot establish which configuration is actually active.

Official references:
- https://wiki.hypr.land/0.54.0/configuring/binds/
- https://wiki.hypr.land/0.54.0/configuring/keywords/
- https://wiki.hypr.land/configuring/core/

## Integration design

CueWire will NOT replace the user's existing monitors, animations,
window bindings, keyboard layouts, touchpad settings or other options.
The updater adds one marked source block to an existing .conf:

~~~ini
# >>> CueWire M7.4 managed include (user-approved) >>>
source = /data/cue-wire/.audit/cuewire-keymap-v1.conf
# <<< CueWire M7.4 managed include <<<
~~~

The sourced owner-only 0600 file is generated from the existing private
CueWire action map. Example for the original NumPad profile:

~~~ini
bindipd = , KP_4, CueWire M7.4 cue:4, exec, /data/cue-wire/target/release/cuewire ctl toggle 4
bindipd = , KP_Decimal, CueWire M7.4 stop, exec, /data/cue-wire/target/release/cuewire ctl stop
bindpd = SUPER, C, CueWire M7.4 emergency, exec, /usr/bin/bash /data/cue-wire/tools/cuewire_scope.sh emergency
~~~

Flags: i ignores held modifiers, p bypasses app-requested shortcut
inhibition, d adds an auditable description. No lockscreen binds,
autostart, audio capture or key logging. Custom exact chords have
bindpd instead of bindipd. Super+C is protected and cannot be assigned
to a sound cue.

All original config lines remain, a unique owner-only same-directory
backup is created BEFORE updating the main file, and the updater
refuses unknown versions, foreign bind collisions, edited source
blocks, unsafe symlinks or file ownerships. It does NOT reload
the compositor, install a service or change audio device defaults.

## Read-only checks

~~~bash
cd /data/cue-wire
git switch feature/m7-terminal-interface
git pull --ff-only

python3 -m unittest discover -s tests -p 'test_cuewire_hypr_conf.py' -v
python3 -m unittest discover -s tests -p 'test_cuewire_keymap.py' -v
cargo test --locked --offline
cargo build --release --locked --offline

hyprctl version
python3 tools/cuewire_keymap.py show
python3 tools/cuewire_hypr_conf.py inspect
python3 tools/cuewire_hypr_conf.py preview
~~~

If the actual active file isn't the standard XDG config location,
add --config /actual/absolute/path/hyprland.conf to both commands.
Inspect checks current runtime version, config eligibility and
foreign shortcut conflicts. Preview produces a full standalone
CueWire .conf plus a unified diff. Neither writes files.

## Explicit config update (only after 0.54-or-older verification)

~~~bash
python3 tools/cuewire_hypr_conf.py apply
~~~

This creates the private generated .conf, then backs up and replaces
only the selected main .conf with the single CueWire source block.
The exact backup path is printed. The operation is idempotent.

If the compositor has not yet auto-reloaded the changed config,
the user may manually run:

~~~bash
hyprctl reload
python3 tools/cuewire_keymap.py verify
~~~

Hyprland verify checks that the registered keys, modifiers,
descriptions and fixed CueWire command match the intended profile.
A generated file without verified active binds is NOT a success.

Never include both the older static CueWire Lua file and the new
generated legacy .conf at the same time.

## Rollback / limits

Restore the exact printed backup over the EXACT active .conf and
run hyprctl reload. The .audit keymap file is inert without the
source directive. Do not apply the legacy update if Hyprland is
0.55+ or if the actual live configuration path is uncertain.
Even when all unit tests pass, real game key delivery and the
emergency scope shutdown require their own physical verification.
