"""The recorder. Each scene is shown in Chromium (Playwright) and photographed as fast as the browser allows, and every
photograph is kept with the moment it was taken; assemble.py turns them into a video at a constant frame rate, so a slow
moment of the page costs a repeated frame and never a change in speed.

  url       a real page (an address, or a file); optionally scrolled smoothly while the words are spoken
  terminal  commands run for real, once, before the recording; the terminal page replays what they printed (the command
            typed, the output line by line, the exit status and the seconds it took)
  still     a picture, fitted to the frame

A scene that photographs nothing new (a still, a page that does not move) keeps one frame for as long as it lasts.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from storyboard import Command, Scene, Storyboard

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
MAX_LINES, MAX_WIDTH = 200, 300
TYPING = 30                         # characters a second, when a command is typed


class RecordError(RuntimeError):
    """The scene could not be shown; the message says which and why."""


@dataclass
class Result:
    shown: str                      # the command as the viewer sees it
    lines: list[str]
    code: int
    seconds: float


def shown(command: str) -> str:
    return command.replace("{python}", "python")


def clean(text: str) -> list[str]:
    """The lines of a command's output as a terminal would leave them: colour codes gone, a progress line that rewrote itself
    with a carriage return shown as its last version, trailing blank lines dropped, long lines and long output cut."""
    lines = [raw.split("\r")[-1].rstrip() for raw in ANSI.sub("", text).replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    cut = [line if len(line) <= MAX_WIDTH else line[:MAX_WIDTH] + "..." for line in lines]
    return cut if len(cut) <= MAX_LINES else cut[:MAX_LINES] + [f"... ({len(cut) - MAX_LINES} more lines)"]


def run_commands(commands: list[Command], cwd: Path, env: dict[str, str], timeout: float = 180.0) -> list[Result]:
    """Runs each command for real, in order. A failing command stops the render unless it was written `run!:`."""
    environment = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "110", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", **env}
    python = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
    results = []
    for command in commands:
        started = time.monotonic()
        try:
            done = subprocess.run(command.text.replace("{python}", python), shell=True, cwd=cwd, env=environment, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RecordError(f"`{shown(command.text)}` did not finish in {timeout:g} seconds") from None
        out = done.stdout.decode("utf-8", errors="replace")
        if done.returncode and not command.allow_failure:
            raise RecordError(f"`{shown(command.text)}` exited with status {done.returncode}, and the video would show a failure. "
                              f"Write it as `run!:` if the failure is what is to be shown. Its last output:\n" + "\n".join(clean(out)[-8:]))
        results.append(Result(shown(command.text), clean(out), done.returncode, time.monotonic() - started))
    return results


def terminal_events(results: list[Result], duration: float, lead: float, tail: float) -> list[dict]:
    """What the terminal page does and when (seconds from the start of the scene): each command typed, then its output a line
    at a time, then its status. The whole is fitted into the scene, so that everything is on screen before the words end."""
    t, events = 0.0, []
    for r in results:
        typing = max(0.6, len(r.shown) / TYPING)
        events.append({"at": t, "kind": "type", "text": r.shown, "dur": typing})
        t += typing + 0.35
        reveal = min(4.0, max(0.4, len(r.lines) / 14))
        for k, line in enumerate(r.lines):
            events.append({"at": t + reveal * k / max(1, len(r.lines)), "kind": "line", "text": line})
        t += reveal + 0.2
        events.append({"at": t, "kind": "line", "text": f"(exit status {r.code}, {r.seconds:.1f} s)", "cls": "dim"})
        t += 0.5
    scale = min(1.0, max(1.0, duration - lead - tail - 0.3) / t)
    for e in events:
        e["at"] = round(lead + e["at"] * scale, 3)
        if "dur" in e:
            e["dur"] = round(e["dur"] * scale, 3)
    return events


TERMINAL = """<!doctype html><html><head><meta charset="utf-8"><title>terminal</title><style>
html,body{margin:0;height:100%;background:#010409}
body{display:flex;align-items:center;justify-content:center}
.win{width:__WIDTH__px;height:__HEIGHT__px;background:#0d1117;border:1px solid #30363d;border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.bar{height:38px;background:#161b22;border-bottom:1px solid #30363d;display:flex;align-items:center;padding:0 14px;gap:8px;color:#8b949e;font:15px system-ui,sans-serif}
.dot{width:12px;height:12px;border-radius:50%;background:#30363d}
pre{margin:0;padding:18px 22px;flex:1;overflow:hidden;color:#c9d1d9;font:24px/1.45 ui-monospace,"Cascadia Code",Menlo,Consolas,"DejaVu Sans Mono",monospace;white-space:pre-wrap;word-break:break-all}
.p{color:#3fb950}.dim{color:#8b949e}.cur{background:#c9d1d9;color:#0d1117}
</style></head><body><div class="win"><div class="bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span style="margin-left:10px">__TITLE__</span></div><pre id="t"></pre></div>
<script>
const E=__EVENTS__;
const last=E.length?Math.max(...E.map(e=>e.at+(e.dur||0))):0;
const esc=s=>s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const pre=document.getElementById("t");
window.seek=function(t){
  let out="",typing=false;
  for(const e of E){
    if(t<e.at)break;
    if(e.kind==="type"){
      const n=e.dur>0?Math.min(e.text.length,Math.floor(e.text.length*(t-e.at)/e.dur)):e.text.length;
      out+='<span class="p">$ </span>'+esc(e.text.slice(0,n));
      if(n<e.text.length){out+='<span class="cur"> </span>';typing=true}
      out+="\\n";
    }else out+=(e.cls?'<span class="'+e.cls+'">':"")+esc(e.text)+(e.cls?"</span>":"")+"\\n";
  }
  if(!typing&&t>=last&&Math.floor(t*2)%2===0)out+='<span class="p">$ </span><span class="cur"> </span>';
  else if(!typing&&t>=last)out+='<span class="p">$ </span>';
  pre.innerHTML=out;pre.scrollTop=pre.scrollHeight;
};
window.seek(0);
</script></body></html>
"""

STILL = """<!doctype html><meta charset="utf-8"><title>still</title><body style="margin:0;background:#0d1117;height:100vh;overflow:hidden">
<img src="__SRC__" style="width:100vw;height:100vh;object-fit:contain"></body>
"""


def terminal_page(events: list[dict], size: tuple[int, int], title: str) -> str:
    return (TERMINAL.replace("__WIDTH__", str(size[0] - 80)).replace("__HEIGHT__", str(size[1] - 80))
            .replace("__TITLE__", title.replace("<", "&lt;")).replace("__EVENTS__", json.dumps(events).replace("</", "<\\/")))


def still_page(source: Path) -> str:
    return STILL.replace("__SRC__", source.resolve().as_uri())


def smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def capture(page, seek: Callable[[float], None] | None, duration: float, folder: Path, quality: int = 90) -> list[tuple[str, float]]:
    """Photographs the page for `duration` seconds. `seek(t)` puts the page in its state at second t (a scroll, the terminal).
    Returns (file, seconds the frame is shown): the first photograph is at second 0, each lasts until the next, the last until
    the scene's end. A photograph identical to the one before it is not kept; the one before just lasts longer."""
    folder.mkdir(parents=True, exist_ok=True)
    kept: list[tuple[Path, float]] = []
    previous, same, started = None, 0, time.perf_counter()
    while True:
        t = 0.0 if not kept else time.perf_counter() - started
        if kept and t >= duration:
            break
        if seek:
            seek(t)
        shot = page.screenshot(type="jpeg", quality=quality)
        if shot != previous:
            path = folder / f"{len(kept) + 1:06d}.jpg"
            path.write_bytes(shot)
            kept.append((path, t))
            previous, same = shot, 0
        else:
            same += 1
            if same > 5:
                time.sleep(0.15)        # nothing moves: look less often
    return [(path.name, (kept[k + 1][1] if k + 1 < len(kept) else duration) - at) for k, (path, at) in enumerate(kept)]


def record(board: Storyboard, scenes: list[tuple[Scene, float, list[Result]]], work: Path, say: Callable[[str], None] = print) -> list[list[tuple[str, float]]]:
    """The frames of every scene, as (path relative to `work`, seconds) lists. `scenes`: the scene, its length in seconds, and
    the results of its commands."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RecordError("Playwright for Python is not installed: pip install playwright, then (on your own machine) playwright install chromium. "
                          "In the sandbox the browser is already there: PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers") from None
    width, height = board.size
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    options = {"args": ["--hide-scrollbars", "--force-color-profile=srgb"]}
    if proxy:
        options["proxy"] = {"server": proxy, "bypass": "localhost,127.0.0.1"}
    out: list[list[tuple[str, float]]] = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(**options)
        except PlaywrightError as why:
            raise RecordError(f"Chromium would not start: {str(why).splitlines()[0]}. On your own machine: playwright install chromium") from None
        try:
            context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1, locale="en-US")
            for number, (scene, duration, results) in enumerate(scenes, 1):
                folder = work / "frames" / f"{number:02d}-{scene.id}"
                page = context.new_page()
                try:
                    frames = _scene(page, board, scene, duration, results, folder, work)
                except PlaywrightError as why:
                    raise RecordError(f"scene {scene.id} ({scene.kind} {scene.target}): {str(why).splitlines()[0]}") from None
                finally:
                    page.close()
                out.append([(f"frames/{folder.name}/{name}", seconds) for name, seconds in frames])
                say(f"  scene {scene.id}: {scene.kind}, {duration:.1f} s, {len(frames)} distinct frames")
        finally:
            browser.close()
    return out


def _scene(page, board: Storyboard, scene: Scene, duration: float, results: list[Result], folder: Path, work: Path) -> list[tuple[str, float]]:
    lead, tail = board.pad
    if scene.kind == "terminal":
        events = terminal_events(results, duration, lead, tail)
        html = work / f"terminal-{scene.id}.html"
        html.write_text(terminal_page(events, board.size, "terminal"), encoding="utf-8")
        page.goto(html.resolve().as_uri(), wait_until="load")
        return capture(page, lambda t: page.evaluate("t => window.seek(t)", t), duration, folder)
    if scene.kind == "still":
        source = (board.folder / scene.target).resolve()
        if not source.is_file():
            raise RecordError(f"scene {scene.id}: the picture {scene.target} is not a file (relative to {board.folder})")
        html = work / f"still-{scene.id}.html"
        html.write_text(still_page(source), encoding="utf-8")
        page.goto(html.resolve().as_uri(), wait_until="load")
        page.wait_for_timeout(300)
        return capture(page, None, duration, folder)
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", scene.target):
        address = scene.target
    else:
        local = (board.folder / scene.target).resolve()
        if not local.is_file():
            raise RecordError(f"scene {scene.id}: {scene.target} is not a file (relative to {board.folder})")
        address = local.as_uri()
    response = page.goto(address, wait_until="load", timeout=60_000)
    if response is not None and response.status >= 400:
        raise RecordError(f"scene {scene.id}: {address} answered {response.status}")
    page.wait_for_timeout(round(scene.wait * 1000))
    if not scene.scroll:
        return capture(page, None, duration, folder)
    room = max(0, page.evaluate("document.documentElement.scrollHeight - window.innerHeight"))
    target, span = min(scene.scroll, room), max(0.1, duration - lead - tail)
    return capture(page, lambda t: page.evaluate("y => window.scrollTo(0, y)", round(target * smooth((t - lead) / span))), duration, folder)
