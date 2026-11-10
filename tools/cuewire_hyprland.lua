-- CueWire opt-in keyboard profile for the Hyprland LUA config provider.
--
-- This file is NOT loaded automatically. Install ONLY by explicitly adding
--   dofile("/data/cue-wire/tools/cuewire_hyprland.lua")
-- to your own checked Hyprland Lua config after conflict review.
--
-- Ctrl+C in TUI closes only the UI; SUPER+C runs the already-audited
-- emergency shutdown of the single transient CueWire cgroup.
-- No key identity, text or pointer coordinates are logged or collected.
--
-- IMPORTANT: these are persistent CONFIG bindings, not temporary dynamic
-- registration. To disable, remove the dofile and reload Hyprland.
-- The keys also get consumed when CueWire is inactive; review first.
-- Their exec commands fail without a running owned CueWire instance.
--
-- Avoid blocking Lua callbacks. hl.dsp.exec_cmd dispatches asynchronously.
-- A Lua hotkey does NOT prove Steam/game input-inhibitor behavior.
local root = "/data/cue-wire"
local bin = root .. "/target/release/cuewire"
local scoped = root .. "/tools/cuewire_scope.sh"

-- Existing handles are unbound only if this same Quendoris fragment
-- was already loaded in this compositor. Never unbind a foreign key.
if _G.__cuewire_hotkey_handles then
    for _, handle in ipairs(_G.__cuewire_hotkey_handles) do
        handle:unbind()
    end
end

local handles = {}
for n = 0, 9 do
    handles[#handles + 1] = hl.bind(
        "KP_" .. tostring(n),
        hl.dsp.exec_cmd(bin .. " ctl play " .. tostring(n)),
        { ignore_mods = true }
    )
end

handles[#handles + 1] = hl.bind(
    "KP_Decimal",
    hl.dsp.exec_cmd(bin .. " ctl stop"),
    { ignore_mods = true }
)

-- A single emergency shortcut kills only the independently MARKED
-- transient cgroup. This has no killall/pkill, shell redirection,
-- compositor configuration mutation or autostart.
handles[#handles + 1] = hl.bind(
    "SUPER + C",
    hl.dsp.exec_cmd("bash " .. scoped .. " emergency")
)

_G.__cuewire_hotkey_handles = handles
