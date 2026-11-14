"""M7.5 opt-in tabbed workspace for the existing CueWire terminal.

The original renderer owns the header/dock/footer and terminal lifecycle.
This layer replaces ONLY the upper content area. No input-device listener,
global key registration, PipeWire setters, audio processing or new process.
Live gain controls are deliberately disabled until the audio engine offers
four independent, measurable gain stages. Demo changes are in-memory only.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import textwrap
import time

import cuewire_tui as ui
import cuewire_keymap as keymap

TABS=("Overview","Devices","Bindings","Library","Mixer")
GAIN_NAMES=("Voice","Music local","Music send","Master send")
MAX_ENTRIES=512


def display(value:object)->str:
    """ASCII-safe, single-column terminal text; no control or wide glyphs."""
    return str(value).encode("ascii","backslashreplace").decode("ascii").replace(
        "\n","\\n").replace("\r","\\r").replace("\t","\\t").replace("\x1b","?")


def scan_wavs(folder:str)->tuple[Path,...]:
    """Explicit bounded top-level scan only. No decoding, playback or writes."""
    path=Path(folder).expanduser()
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError("Use an existing absolute non-symlink directory")
    found=[]
    with os.scandir(path) as items:
        for count,entry in enumerate(items,1):
            if count>MAX_ENTRIES:
                raise ValueError("Folder exceeds 512 entries; choose a smaller folder")
            if entry.name.lower().endswith(".wav") and entry.is_file(follow_symlinks=False):
                found.append(Path(entry.path))
    return tuple(sorted(found,key=lambda p:(p.name.casefold(),p.name)))


def cue_number(model:ui.Model)->int|None:
    if isinstance(model,ui.LiveModel):
        if not model.report.connected:
            return None
        match=re.fullmatch(r"Some\(([0-9])\)",model.report.active_cue)
        return int(match.group(1)) if match else None
    return model.active_cue


class Workspace:
    def __init__(self,model:ui.Model,cues:tuple[str,...]=()):
        self.model=model
        self.tab=0
        self.scroll=0
        self.gain_row=0
        self.gains=[100,45,35,80]
        self.mapping=keymap.defaults()
        self.keymap_error=""
        self.folder=""
        self.files:tuple[Path,...]=()
        self.staged:dict[int,Path]={}
        # Only the managed launcher knows the exact paths given to Rust.
        # Do not infer active filenames from an edited next-session profile.
        self.running_cues={int(v[0]):Path(v[2:]) for v in cues
                           if re.match(r"^[0-9]=",v)}
        if not self.live:
            self.running_cues={4:Path("Demo - Thunderstruck.wav")}
            self.files=(Path("/demo/Thunderstruck.wav"),Path("/demo/Sample 2.wav"))
            self.folder="Simulated library"
        self.last_press:int|None=None
        self.press_until=0.0

    @property
    def live(self)->bool:
        return isinstance(self.model,ui.LiveModel)

    def refresh_mapping(self)->None:
        try:
            self.mapping=keymap.read_profile() if self.live else (
                self.model.demo_keymap if self.model.demo_keymap is not None
                else keymap.defaults())
            self.keymap_error=""
        except (keymap.KeymapError,OSError) as exc:
            self.mapping={}
            self.keymap_error=display(exc)[:120]

    def switch(self,index:int)->None:
        self.tab=index%len(TABS)
        self.scroll=0
        if self.tab==2:
            self.refresh_mapping()

    def command(self,command:str)->bool:
        """Handle workspace commands only. Original dock/history remain intact."""
        entered=command.strip()
        m=self.model
        if entered=="library" or entered.startswith("library "):
            self.switch(3)
            if entered=="library":
                m.add("library /absolute/music/folder | slot 4 1 | save library")
            elif not self.live:
                m.add("DEMO: sample library only; no directory was accessed")
            else:
                try:
                    folder=entered.partition(" ")[2].strip()
                    files=scan_wavs(folder)
                    self.folder=str(Path(folder).expanduser())
                    self.files=files
                    m.add(f"Library: {len(files)} WAV files; nothing played")
                except (ValueError,OSError) as exc:
                    m.add("REFUSED LIBRARY: "+display(exc)[:72])
            return True
        if entered.startswith("slot "):
            parts=entered.split()
            self.switch(3)
            if (len(parts)!=3 or not re.fullmatch("[0-9]",parts[1])
                    or not re.fullmatch("[0-9]{1,3}",parts[2])
                    or not 1<=int(parts[2])<=len(self.files)):
                m.add("REFUSED: slot 4 1 (slot 0..9, listed file number)")
                return True
            slot=int(parts[1])
            self.staged[slot]=self.files[int(parts[2])-1]
            m.add(f"STAGED slot {slot}; running audio unchanged")
            return True
        if entered=="save library":
            self.switch(3)
            if not self.staged:
                m.add("No staged cue assignments")
            elif not self.live:
                self.running_cues.update(self.staged)
                self.staged.clear()
                m.add("DEMO: library saved in memory only")
            else:
                try:
                    import cuewire_session as session
                    # Merge with the current local next-session profile.
                    # Explicit command is the only write, never during scan.
                    current={int(v[0]):v[2:] for v in session.private_profile_cues()}
                    if not current:
                        current={n:str(p) for n,p in self.running_cues.items()}
                    current.update({n:str(p) for n,p in self.staged.items()})
                    session.write_private_profile(
                        [f"{n}={p}" for n,p in sorted(current.items())],replace=True)
                    self.staged.clear()
                    m.add("Saved private cue profile; applies on next session")
                except (session.SessionError,OSError,ValueError) as exc:
                    m.add("REFUSED SAVE: "+display(exc)[:72])
            return True
        return False

    def handle(self,key:str)->None:
        m=self.model
        if m.terminated:
            return
        if m.focus=="command":
            # Quit confirmation always belongs to the original model.
            if key=="ENTER" and not m.waiting_quit:
                entered=m.command.strip()
                if self.command(entered):
                    m.command=""
                    return
                ui.apply_key(m,key)
                if entered=="keys" or entered.startswith(("keys ","bind ","unbind ")):
                    self.switch(2)
                return
            ui.apply_key(m,key)
            return
        if key in ("TAB","BACKTAB"):
            self.switch(self.tab+(1 if key=="TAB" else -1))
            return
        if self.tab==0:
            ui.apply_key(m,key)
            return
        if self.tab==4:
            if key in ("UP","DOWN"):
                self.gain_row=(self.gain_row+(1 if key=="DOWN" else -1))%4
                return
            if key in ("LEFT","RIGHT"):
                if not self.live:
                    self.gains[self.gain_row]=max(0,min(100,
                        self.gains[self.gain_row]+(5 if key=="RIGHT" else -5)))
                else:
                    m.add("Gain control unavailable in the current live engine")
                return
            if key in "0123456789" and len(key)==1:
                self.last_press=int(key)
                self.press_until=time.monotonic()+.35
                m.execute("toggle "+key)
                return
            if key==".":
                m.execute("stop")
                return
        elif key in ("UP","DOWN"):
            self.scroll=max(0,self.scroll+(1 if key=="DOWN" else -1))
            return
        ui.apply_key(m,key)

    def rows(self)->list[str]:
        m=self.model
        if self.tab==1:
            if self.live and m.report.connected:
                r=m.report
                return ["LIVE / server-reported node names","",
                        "Physical microphone: "+r.mic,
                        "Monitor: "+r.monitor,
                        "Virtual source: "+r.virtual_source,
                        "Mix input: "+r.mix_input,"",
                        "Socket ACL: "+r.socket_acl,
                        "Control protocol: "+r.protocol,
                        "Run doctor in the dock for graph registration checks.",
                        "Sample rate, signal levels, XRUNs and measured latency: unavailable."]
            return ["Devices / "+("OFFLINE" if self.live else "DEMO"),
                    "No verified physical node names available.",
                    "No device, route or default is changed by this view."]
        if self.tab==2:
            return ["LOCAL keymap / registered state NOT verified",
                    "bind cue:4 F8 | unbind cue:4 | keys cue:4",
                    *([self.keymap_error] if self.keymap_error else []),
                    *[f"{a:<8} {self.mapping.get(a,'unassigned')}" for a in keymap.ACTIONS],
                    "PANIC    SUPER+C (protected)",
                    "Global registration/cleanup: separate acceptance gate."]
        if self.tab==3:
            return ["Library / "+(self.folder or "no folder connected"),
                    "library /absolute/path | slot 4 1 | save library",
                    "WAV only; bounded top-level scan, no playback during scan.",
                    "NEXT SESSION: staged assignments do not change the live engine.",
                    *[f"RUN {n} -> {p.name}" for n,p in sorted(self.running_cues.items())],
                    *[f"NEXT {n} -> {p.name}" for n,p in sorted(self.staged.items())],
                    *[f"{i+1:3}  {p.name}" for i,p in enumerate(self.files)]]
        return []


def write_rows(canvas:ui.Canvas,rect:ui.Rect,rows:list[str],offset:int)->int:
    width=max(1,rect.w-4)
    # Wrap instead of cutting device IDs / filenames; scroll over all rows.
    wrapped=[]
    for row in rows:
        wrapped.extend(textwrap.wrap(display(row),width=width,
                                     break_long_words=True,break_on_hyphens=False)
                       or [""])
    capacity=max(0,rect.h-3)
    offset=min(max(0,offset),max(0,len(wrapped)-capacity))
    for i,row in enumerate(wrapped[offset:offset+capacity]):
        canvas.write(rect.x+2,rect.y+2+i,row,ui.MUTED,maxlen=width)
    if len(wrapped)>capacity:
        label=f" {offset+1}-{min(len(wrapped),offset+capacity)}/{len(wrapped)} UP/DOWN "
        canvas.write(rect.x+2,rect.y+rect.h-1,label,ui.SITE_ACCENT,maxlen=width)
    return offset


def paint(work:Workspace,width:int,height:int,seconds:float=0.0):
    m=work.model
    canvas,geo=ui.paint(m,width,height,seconds)
    if not geo.adequate:
        return canvas,geo
    # Retain the original command dock and footer byte-for-byte.
    # Only the upper header/navigation and workspace content are changed.
    canvas.write(2,1,"QUENDORIS / CUEWIRE M7.5".ljust(30),ui.SITE_ACCENT,True)
    for x in range(2,width-2):
        canvas.cell(x,2," ")
    x=2
    for i,name in enumerate(TABS):
        label=("["+name+"]" if i==work.tab else " "+name+" ")
        canvas.write(x,2,label,ui.SITE_ACCENT if i==work.tab else ui.MUTED,
                     bold=i==work.tab)
        x+=len(label)+1
    if work.tab==0:
        return canvas,geo
    area=ui.Rect(2,4,width-4,geo.dock.y-5)
    # Clear the old six-panel glyphs but retain the atmospheric backdrop.
    for yy in range(area.y,area.y+area.h):
        for xx in range(area.x,area.x+area.w):
            canvas.cell(xx,yy," ",bg=ui.field_color(xx,yy,width,height,
                                                  seconds if m.motion else 0.0))
    canvas.rect(area,True,fill_alpha=.66)
    canvas.write(area.x+2,area.y," "+TABS[work.tab]+" ",ui.SITE_ACCENT,True)
    if work.tab!=4:
        work.scroll=write_rows(canvas,area,work.rows(),work.scroll)
        return canvas,geo
    left=area.x+2
    right=area.x+area.w//2+1
    left_width=right-left-2
    right_width=area.x+area.w-2-right
    canvas.write(left,area.y+1,"GAIN / "+("UNAVAILABLE" if work.live else "DEMO"),
                 ui.WARN if work.live else ui.SITE_ACCENT,True,maxlen=left_width)
    tall=area.h>=19
    for i,label in enumerate(GAIN_NAMES):
        y=area.y+2+i*(3 if tall else 1)
        mark=">" if i==work.gain_row else " "
        amount=work.gains[i]
        if tall:
            n=max(4,left_width-10)
            filled=amount*n//100
            bar="─"*n if work.live else "━"*filled+"─"*(n-filled)
            value="N/A" if work.live else f"{amount:3}%"
            canvas.write(left,y,f"{mark} {label}  {value}",
                         ui.SITE_ACCENT if i==work.gain_row else ui.MUTED,
                         maxlen=left_width)
            canvas.write(left+2,y+1,bar,
                         ui.BORDER if work.live else ui.SITE_ACCENT,
                         maxlen=left_width-2)
        else:
            bar="--------" if work.live else (
                "#"*(amount*8//100)+"-"*(8-amount*8//100))
            value=" N/A" if work.live else f"{amount:3}%"
            canvas.write(left,y,f"{mark}{label:<12} [{bar}] {value}",
                         ui.SITE_ACCENT if i==work.gain_row else ui.MUTED,
                         maxlen=left_width)
    hint_y=area.y+(15 if tall else 7)
    canvas.write(left,hint_y,"Live gains need engine support" if work.live else
                 "UP/DOWN select; LEFT/RIGHT +/-5%",ui.MUTED,maxlen=left_width)
    canvas.write(right,area.y+1,"NUMPAD / CUE STATE",ui.SITE_ACCENT,True,maxlen=right_width)
    active=cue_number(m)
    big_keys=tall and right_width>=23
    for row,nums in enumerate(((7,8,9),(4,5,6),(1,2,3),(0,None,-1))):
        for col,num in enumerate(nums):
            if num is None:
                continue
            pressed=(num==work.last_press and time.monotonic()<work.press_until)
            playing=(num==active)
            if big_keys:
                key_w=15 if num==0 else 7
                box=ui.Rect(right+col*8,area.y+2+row*3,key_w,3)
                canvas.rect(box,playing or pressed,fill_alpha=.85)
                label="." if num==-1 else str(num)
                canvas.write(box.x+box.w//2,box.y+1,label,
                             ui.SITE_ACCENT if playing or pressed else ui.MUTED,
                             bold=playing or pressed,maxlen=1)
            else:
                label=("<"+str(num)+">" if playing else
                       " . " if num==-1 else "["+str(num)+"]")
                canvas.write(right+col*5,area.y+2+row,label,
                             ui.SITE_ACCENT if playing or pressed else ui.MUTED,
                             bold=playing or pressed,maxlen=3)
    title_y=area.y+(15 if big_keys else 6)
    canvas.write(right,title_y,"NOW PLAYING / "+("LIVE" if work.live else "DEMO"),
                 ui.SITE_ACCENT,True,maxlen=right_width)
    if active is None:
        track="Unknown / disconnected" if work.live and not m.report.connected else "No active cue"
    else:
        path=work.running_cues.get(active)
        track=f"#{active} "+(path.name if path else "(filename not reported)")
    # Full names remain available in the Library / managed start profile.
    max_lines=max(1,area.y+area.h-1-(title_y+1))
    for i,line in enumerate(textwrap.wrap(display(track),width=max(1,right_width))[:max_lines]):
        canvas.write(right,title_y+1+i,line,ui.TEXT,maxlen=right_width)
    return canvas,geo
