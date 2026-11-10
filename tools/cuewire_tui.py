#!/usr/bin/env python3
"""CueWire M7.1 compact operator TUI: six panels and focus-aware command dock.

Pure Python standard library. Demo/snapshot ONLY: NO PipeWire, sockets,
microphone/keyboard device access, engine startup, file logging or autostart.
Keyboard bytes are consumed only from this foreground TTY, never from evdev.
The exact Quendoris 420x420 mark is rasterized from the accepted SVG geometry.
When interactive, terminal raw/alternate-screen state is restored on exit.

Use:
    python3 tools/cuewire_tui.py --snapshot       # no TTY/devices needed
    python3 tools/cuewire_tui.py --demo           # interactive, TTY required
    python3 tools/cuewire_tui.py --demo --no-motion
    python3 tools/cuewire_tui.py --demo --mono
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import math
import os
import select
import shutil
import sys
import termios
import time
import tty
from typing import Iterable

# Source: quendoris/site/dist/assets/b-reference-mark.svg
# Unaltered original polygon, two rectangular eye holes, and the lower notch.
MARK_PATH = (
    "M 35 35 L 386 35 L 386 246 L 316 246 L 316 386 "
    "L 245 386 L 245 316 L 176 316 L 176 386 L 105 386 "
    "L 105 246 L 35 246 Z M 106 106 L 175 106 L 175 175 "
    "L 106 175 Z M 246 106 L 315 106 L 315 175 L 246 175 Z"
)
# Colors from the exact site's B reference CSS, not unrelated RGB decoration.
SITE_DARK = (20, 32, 36)       # #142024
SITE_FIELD = (32, 57, 64)      # #203940
SITE_SECOND = (51, 70, 72)     # #334648
SITE_PANEL = (26, 42, 46)      # #1a2a2e
SITE_ACCENT = (211, 224, 139) # #d3e08b
TEXT = (233, 240, 229)
MUTED = (155, 179, 170)
BORDER = (80, 104, 104)
WARN = (229, 185, 125)

NAMES = ("Runtime", "Devices", "Research", "Cue State", "Safety", "Audit / Checks")
MIN_WIDTH = 68
MIN_HEIGHT = 23
PANEL_GAP = 1
COLS = 3
ROWS = 2
FRAME_INTERVAL = .25 # 4 fps, restrained atmosphere; no timer thread
MAX_COMMAND = 96
MAX_FEED = 48


def blend(a: tuple[int,int,int], b: tuple[int,int,int], amount: float):
    amount = max(0.0, min(1.0, amount))
    return tuple(round(a[i]*(1-amount)+b[i]*amount) for i in range(3))


def mark_at(px: float, py: float) -> bool:
    """Exact even-odd fill of 420x420 Quendoris polygon (no approximation)."""
    if not (35 <= px < 386 and 35 <= py < 386):
        return False
    if py >= 246 and (105 > px or px >= 316):
        return False
    if py >= 316 and 176 <= px < 245:
        return False
    if 106 <= px < 175 and 106 <= py < 175:
        return False
    if 246 <= px < 315 and 106 <= py < 175:
        return False
    return True


def mark_at_screen(x: int, y: int, width: int, height: int) -> bool:
    """Place exact 420 SVG raster on the central atmospheric field."""
    mark_w = min(46, max(28, width * 2 // 5))
    mark_h = min(22, max(13, height * 3 // 5))
    left = (width-mark_w)//2
    top = (height-mark_h)//2
    if not (left <= x < left+mark_w and top <= y < top+mark_h):
        return False
    # Cell-centre sampling keeps both SVG eye openings and lower notch.
    px = ((x-left)+.5)*420/mark_w
    py = ((y-top)+.5)*420/mark_h
    return mark_at(px,py)


def field_color(x: int, y: int, width: int, height: int, seconds: float):
    """Continuous dark gradient, finite, deterministic, gently animated."""
    nx = x / max(1,width-1)
    ny = y / max(1,height-1)
    # Period > 1 minute; primary background still graphite.
    phase = seconds * (2*math.pi/79)
    wave = .5 + .5*math.sin(phase + nx*2.3 + ny*.9)
    shade = .17 + .31*wave
    background = blend(SITE_DARK, SITE_FIELD, shade)
    glow = .10 + .15*(.5+.5*math.sin(phase*.62 - nx*3.3 + ny*.7))
    background = blend(background,SITE_SECOND,glow)
    if mark_at_screen(x,y,width,height):
        background = blend(background,SITE_ACCENT,.13)
    return background


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int
    def contains(self, x:int, y:int)->bool:
        return self.x<=x<self.x+self.w and self.y<=y<self.y+self.h


@dataclass(frozen=True)
class Layout:
    header: Rect
    panels: tuple[Rect,...]
    dock: Rect
    footer: Rect
    adequate: bool


def layout(width: int, height: int, expanded: bool = False) -> Layout:
    width, height = max(0,int(width)),max(0,int(height))
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return Layout(Rect(0,0,width,min(2,height)),(),
                      Rect(0,max(0,height-2),width,min(1,height)),
                      Rect(0,max(0,height-1),width,min(1,height)),False)
    margin=2
    header=Rect(margin,1,width-2*margin,2)
    footer=Rect(margin,height-1,width-2*margin,1)
    dock_h = min(9,max(6,height//4)) if expanded else 1
    dock = Rect(margin,height-2-dock_h,width-2*margin,dock_h)
    region_y=4
    usable_h=dock.y-region_y-1
    # At min height, use 2 * 5 row panes plus 1 central gutter.
    row1=max(5,(usable_h-1)//2)
    row2=usable_h-1-row1
    if row2 < 5:
        return Layout(header,(),dock,footer,False)
    inner_w=width-2*margin
    col_w=(inner_w-2*PANEL_GAP)//COLS
    rem=inner_w-2*PANEL_GAP-col_w*COLS
    col_sizes=[col_w+(1 if i<rem else 0) for i in range(COLS)]
    xs=[margin]
    for i in range(1,COLS):
        xs.append(xs[-1]+col_sizes[i-1]+PANEL_GAP)
    rectangles=tuple(
        Rect(xs[col], region_y if row==0 else region_y+row1+1,
             col_sizes[col],row1 if row==0 else row2)
        for row in range(ROWS) for col in range(COLS))
    return Layout(header,rectangles,dock,footer,True)


@dataclass
class Model:
    mode: str = "DEMO"
    selected: int = 0
    focus: str = "window" # window or command
    command: str = ""
    active_cue: int | None = None
    waiting_quit: bool = False
    terminated: bool = False
    motion: bool = True
    mono: bool = False
    checks: tuple[str,...] = (
        "PASS / demo isolation",
        "PASS / no device access",
        "SKIP / socket ACL (offline)",
        "SKIP / live audio checks",
    )
    feed: list[str] = field(default_factory=lambda: [
        "M6-007: archived research reference",
        "M7.1: simulated operator console",
        "No physical device or socket opened",
    ])

    def add(self,message:str)->None:
        # Messages are internal status only, not key/text history, no file IO.
        self.feed.append(message[:96].replace("\x1b","?"))
        del self.feed[:-MAX_FEED]

    def execute(self, cmd: str) -> None:
        """No shell; no PipeWire; no remote socket; explicit fixed demo protocol."""
        entered=cmd.strip()
        self.command=""
        if self.waiting_quit:
            self.waiting_quit=False
            if entered=="CONFIRM":
                self.add("DEMO: shutdown confirmed")
                self.terminated=True
            else:
                self.add("DEMO: shutdown cancelled")
            return
        if entered in ("", "status"):
            self.add("Status: simulated engine OFFLINE")
        elif entered in ("help", "?"):
            self.add("Tab = windows / Enter = command / Esc = collapse")
            self.add("play 0..9 | stop | status | research | doctor | quit | exit")
        elif entered=="research":
            self.selected=2
            self.add("M6-007 frozen; not loaded into a live microphone")
        elif entered=="doctor":
            self.selected=5
            self.add("PASS demo isolation; PASS read-only memory state")
            self.add("SKIP hardware/socket/DSP tests (no runtime)")
        elif entered=="stop":
            self.active_cue=None
            self.add("SIMULATED: cue stopped; no audio played")
        elif entered.startswith("play ") and len(entered)==6 and entered[5] in "0123456789":
            self.active_cue=int(entered[5])
            self.add(f"SIMULATED: cue {self.active_cue}; no audio played")
        elif entered=="exit":
            self.add("Console exit requested; no engine touched")
            self.terminated=True
        elif entered=="quit":
            self.waiting_quit=True
            self.add("Type CONFIRM to exit demo, or anything else to cancel")
        else:
            self.add("REFUSED: unrecognized command; see help")


def panel_lines(model: Model, name: str) -> list[str]:
    if name=="Runtime":
        return ["Mode      DEMO / OFFLINE",
                "Engine    not started",
                "Socket    not contacted",
                "DSP       not loaded"]
    if name=="Devices":
        return ["Mic       no device",
                "Monitor   no device",
                "Virtual   not created",
                "Routing   unchanged"]
    if name=="Research":
        return ["M4        offline",
                "M5        research",
                "M6-007    ACCEPTED",
                "M7        UI prototype"]
    if name=="Cue State":
        return ["Active    "+("none" if model.active_cue is None else str(model.active_cue)),
                "Playback  simulated",
                "Output    no sound",
                "Gain      not measured"]
    if name=="Safety":
        return ["Autostart no",
                "Daemon    no",
                "Key log   no",
                "Defaults  untouched"]
    if name=="Audit / Checks":
        return list(model.checks)
    raise ValueError("Unknown panel")


def crop(s: str, width: int) -> str:
    return s[:max(0,width)]


@dataclass
class Cell:
    char: str = " "
    fg: tuple[int,int,int] = TEXT
    bg: tuple[int,int,int] = SITE_DARK
    bold: bool = False


class Canvas:
    def __init__(self,width:int,height:int,seconds:float):
        self.width=width
        self.height=height
        self.cells=[
            [Cell(bg=field_color(x,y,width,height,seconds)) for x in range(width)]
            for y in range(height)]

    def cell(self,x:int,y:int,char:str=" ",fg=TEXT,bg=None,bold=False):
        if 0<=x<self.width and 0<=y<self.height:
            target=self.cells[y][x]
            target.char=char
            target.fg=fg
            target.bold=bold
            if bg is not None:
                target.bg=bg

    def write(self,x:int,y:int,value:str,fg=TEXT,bold=False,maxlen:int|None=None):
        length=self.width-x if maxlen is None else maxlen
        for i,char in enumerate(value[:max(0,length)]):
            if ord(char)<32 or ord(char)==127:
                char="?"
            self.cell(x+i,y,char,fg,bold=bold)

    def rect(self,r:Rect,focused:bool=False,fill_alpha:float=.74):
        if r.w<2 or r.h<2:return
        edge=SITE_ACCENT if focused else BORDER
        for yy in range(r.y,r.y+r.h):
            for xx in range(r.x,r.x+r.w):
                if 0<=xx<self.width and 0<=yy<self.height:
                    b=self.cells[yy][xx].bg
                    self.cells[yy][xx].bg=blend(b,SITE_PANEL,fill_alpha)
        for xx in range(r.x+1,r.x+r.w-1):
            self.cell(xx,r.y,"─",edge)
            self.cell(xx,r.y+r.h-1,"─",edge)
        for yy in range(r.y+1,r.y+r.h-1):
            self.cell(r.x,yy,"│",edge)
            self.cell(r.x+r.w-1,yy,"│",edge)
        for xx,yy,c in (
            (r.x,r.y,"╭"),(r.x+r.w-1,r.y,"╮"),
            (r.x,r.y+r.h-1,"╰"),(r.x+r.w-1,r.y+r.h-1,"╯")):
            self.cell(xx,yy,c,edge)

    def plain(self)->str:
        return "\n".join("".join(c.char for c in row) for row in self.cells)

    def ansi(self)->str:
        out=["\x1b[H"]
        for row in self.cells:
            prev=None
            for cell in row:
                key=(cell.fg,cell.bg,cell.bold)
                if key!=prev:
                    fg,bg,bold=key
                    out.append(
                        f"\x1b[{'1' if bold else '22'};"
                        f"38;2;{fg[0]};{fg[1]};{fg[2]};"
                        f"48;2;{bg[0]};{bg[1]};{bg[2]}m")
                    prev=key
                out.append(cell.char)
            out.append("\x1b[0m\n")
        return "".join(out)+"\x1b[0m"


def paint(model:Model,width:int,height:int,seconds:float=0.0)->tuple[Canvas,Layout]:
    result=Canvas(width,height, seconds if model.motion else 0.0)
    geometry=layout(width,height,model.focus=="command")
    if not geometry.adequate:
        result.write(1,1,"CueWire M7.1 / terminal too small",SITE_ACCENT,bold=True)
        result.write(1,3,f"Minimum {MIN_WIDTH}x{MIN_HEIGHT}; resize or press Ctrl+C",MUTED)
        return result,geometry
    result.write(2,1,"QUENDORIS   /   CUEWIRE  M7.1",SITE_ACCENT,True)
    mode=f"[ {model.mode} / NO LIVE DSP ]"
    result.write(max(2,width-len(mode)-3),1,mode,MUTED)
    result.write(2,2,"COMPACT OPERATOR  •  TAB NAVIGATION  •  M6 RESEARCH FROZEN",MUTED)
    for index,frame in enumerate(geometry.panels):
        is_focused=model.focus=="window" and model.selected==index
        result.rect(frame,is_focused)
        title=f" {index+1:02d} / {NAMES[index]} "
        result.write(frame.x+2,frame.y,title,SITE_ACCENT if is_focused else TEXT,
                     True,maxlen=frame.w-4)
        rows=panel_lines(model,NAMES[index])
        # If the terminal is tight, leave at least a one-line breathing gap.
        for j,value in enumerate(rows[:max(0,frame.h-3)]):
            fg=SITE_ACCENT if ("ACCEPTED" in value or "PASS" in value) else (
                WARN if "SKIP" in value else MUTED)
            result.write(frame.x+2,frame.y+2+j,
                         crop(value,frame.w-4),fg,maxlen=frame.w-4)
    dock=geometry.dock
    if model.focus=="command":
        result.rect(dock,True,fill_alpha=.86)
        result.write(dock.x+2,dock.y," COMMAND / EXPANDED ",SITE_ACCENT,True)
        rows=model.feed[-max(0,dock.h-4):]
        for i,msg in enumerate(rows):
            result.write(dock.x+2,dock.y+2+i,crop(msg,dock.w-4),MUTED)
        prompt="confirm> " if model.waiting_quit else "cuewire> "
        result.write(dock.x+2,dock.y+dock.h-2,
                     crop(prompt+model.command+"▌",dock.w-4),
                     SITE_ACCENT,maxlen=dock.w-4)
    else:
        # Exactly one line when the command dock does not own focus.
        dock_fill=blend(SITE_DARK,SITE_PANEL,.82)
        for xx in range(dock.x,dock.x+dock.w):
            result.cell(xx,dock.y," ",bg=dock_fill)
        short=crop(model.feed[-1] if model.feed else "ready",dock.w-29)
        result.write(dock.x+1,dock.y,"cuewire>  ",SITE_ACCENT,True)
        result.write(dock.x+11,dock.y,short,MUTED)
        result.write(max(dock.x+16,dock.x+dock.w-19),dock.y,
                     "[ Enter to expand ]",SITE_ACCENT)
    foot="TAB/ARROWS windows   ENTER command   ESC collapse   CTRL+C close"
    result.write(2,geometry.footer.y,crop(foot,width-4),MUTED)
    return result,geometry


def focus_index(index:int, direction:str)->int:
    if direction=="tab":return (index+1)%6
    if direction=="backtab":return (index-1)%6
    row,col=divmod(index,3)
    if direction=="left":return row*3+(col-1)%3
    if direction=="right":return row*3+(col+1)%3
    if direction=="up":return (row-1)%2*3+col
    if direction=="down":return (row+1)%2*3+col
    return index


def apply_key(model:Model,key:str)->None:
    """Deterministic focus/command state machine; no OS or device effects."""
    if model.terminated:return
    if key in ("CTRL_C","CTRL_D"):
        model.terminated=True
        return
    if key in ("TAB","BACKTAB","UP","DOWN","LEFT","RIGHT"):
        if model.focus=="command":
            if key in ("TAB","BACKTAB"):
                model.focus="window"
                model.command=""
            return
        model.selected=focus_index(model.selected,
                                   "tab" if key=="TAB"
                                   else "backtab" if key=="BACKTAB"
                                   else key.lower())
        return
    if key=="ESC":
        model.focus="window"
        model.command=""
        return
    if model.focus=="window":
        if key in ("ENTER", ":", "/"):
            model.focus="command"
            model.command=""
        return
    if key=="ENTER":
        model.execute(model.command)
    elif key=="BACKSPACE":
        model.command=model.command[:-1]
    elif key=="CTRL_U":
        model.command=""
    elif len(key)==1 and 32<=ord(key)<127 and len(model.command)<MAX_COMMAND:
        model.command+=key


class KeyReader:
    """Small CSI parser; reads ONLY characters typed to this terminal."""
    def __init__(self):
        self.sequence=bytearray()
    def feed(self, data:bytes)->list[str]:
        results=[]
        for byte in data:
            if self.sequence:
                self.sequence.append(byte)
                seq=bytes(self.sequence)
                mapping={
                    b"\x1b[A":"UP",b"\x1b[B":"DOWN",
                    b"\x1b[C":"RIGHT",b"\x1b[D":"LEFT",
                    b"\x1b[Z":"BACKTAB",b"\x1bOa":"UP",
                    b"\x1bOb":"DOWN",b"\x1bOc":"RIGHT",b"\x1bOd":"LEFT",
                }
                if seq in mapping:
                    results.append(mapping[seq]);self.sequence.clear()
                elif len(seq)>8 or not (
                    seq==b"\x1b" or
                    seq.startswith(b"\x1b[") or
                    seq.startswith(b"\x1bO")
                ):
                    results.append("ESC");self.sequence.clear()
                continue
            if byte==27:
                self.sequence.append(byte)
            elif byte in (10,13):results.append("ENTER")
            elif byte==9:results.append("TAB")
            elif byte in (127,8):results.append("BACKSPACE")
            elif byte==3:results.append("CTRL_C")
            elif byte==4:results.append("CTRL_D")
            elif byte==21:results.append("CTRL_U")
            elif 32<=byte<=126:results.append(chr(byte))
        return results
    def flush_esc(self)->list[str]:
        if bytes(self.sequence)==b"\x1b":
            self.sequence.clear()
            return ["ESC"]
        return []


class TerminalSession:
    """Restore exact original TTY mode/visibility in the finally block."""
    def __init__(self,stream=None,output=None):
        self.input=sys.stdin if stream is None else stream
        self.output=sys.stdout if output is None else output
        self.previous=None
    def __enter__(self):
        if not self.input.isatty() or not self.output.isatty():
            raise RuntimeError("M7.1 animated TUI requires an interactive terminal")
        fd=self.input.fileno()
        self.previous=termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            self.output.write("\x1b[?1049h\x1b[?25l\x1b[0m")
            self.output.flush()
        except BaseException:
            termios.tcsetattr(fd,termios.TCSADRAIN,self.previous)
            raise
        return self
    def __exit__(self,exc_type,exc,tb):
        if self.previous is not None:
            try:termios.tcsetattr(self.input.fileno(),termios.TCSADRAIN,self.previous)
            finally:
                self.output.write("\x1b[0m\x1b[?25h\x1b[?1049l")
                self.output.flush()


def run_demo(no_motion:bool=False,mono:bool=False)->int:
    state=Model(motion=not no_motion,mono=mono)
    reader=KeyReader()
    last_key=time.monotonic()
    last_frame=0.0
    with TerminalSession():
        while not state.terminated:
            now=time.monotonic()
            w,h=shutil.get_terminal_size((100,32))
            if now-last_frame >= (FRAME_INTERVAL if state.motion else 5):
                canvas,_=paint(state,w,h,now)
                sys.stdout.write("\x1b[H" + (
                    canvas.plain() if mono else canvas.ansi()))
                sys.stdout.flush()
                last_frame=now
            ready,_,_=select.select([sys.stdin.fileno()],[],[],.045)
            if ready:
                data=os.read(sys.stdin.fileno(),256)
                if not data:break
                for key in reader.feed(data):
                    apply_key(state,key)
                    last_key=now
                last_frame=0.0
            elif now-last_key>=.06:
                for key in reader.flush_esc():
                    apply_key(state,key)
                    last_key=now
                    last_frame=0.0
    return 0


def main(argv:Iterable[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo",action="store_true",
                      help="full-screen interactive TUI, no device/socket access")
    mode.add_argument("--snapshot",action="store_true",
                      help="static 100x32 uncolored text preview, no TTY required")
    parser.add_argument("--no-motion",action="store_true",
                        help="freeze gradient for reduced motion")
    parser.add_argument("--mono",action="store_true",
                        help="no truecolor, retain compact window navigation")
    a=parser.parse_args(argv)
    if a.snapshot:
        canvas,_=paint(Model(motion=False),100,32)
        print(canvas.plain())
        return 0
    try:return run_demo(a.no_motion or bool(os.environ.get("NO_COLOR")),
                        a.mono or os.environ.get("TERM")=="dumb")
    except (OSError,RuntimeError) as exc:
        print(f"cuewire-tui: {exc}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
