"""Assembly: ffmpeg joins the photographs and the narration into one video, and what was promised about it is checked on the
file that came out, not on what was asked of ffmpeg.

  * a constant frame rate (the photographs have uneven gaps; the `fps` filter fills them by repeating frames)
  * the narration is never cut short: the audio is padded to the length of the video and the output is cut to the length the
    scenes add up to, never to the shorter stream (no -shortest)
  * subtitles twice: a .srt file beside the video, and a subtitle track inside it
  * never longer than the limit (180 seconds): checked on the narration before anything is recorded, and again on the file
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path


class AssemblyError(RuntimeError):
    """ffmpeg failed, or the file it made is not what was promised."""


class TooLong(AssemblyError):
    """The video is longer than the limit."""


class ToolMissing(RuntimeError):
    """A program the render needs is not installed."""


HOW = {"ffmpeg": "install ffmpeg: apt install ffmpeg, brew install ffmpeg, or winget install Gyan.FFmpeg",
       "ffprobe": "it comes with ffmpeg: apt install ffmpeg, brew install ffmpeg, or winget install Gyan.FFmpeg"}


def need(program: str) -> None:
    if shutil.which(program) is None:
        raise ToolMissing(f"{program} is not on PATH: {HOW[program]}")


def ffconcat(scenes: list[list[tuple[str, float]]]) -> str:
    """The list ffmpeg's concat demuxer reads: each photograph and how long it is on screen, scene after scene."""
    lines, last = ["ffconcat version 1.0"], None
    for frames in scenes:
        for path, seconds in frames:
            lines += [f"file '{path}'", f"duration {max(seconds, 0.001):.6f}"]
            last = path
    if last is None:
        raise AssemblyError("nothing was recorded")
    lines.append(f"file '{last}'")          # the demuxer ignores the last duration unless the file is named once more
    return "\n".join(lines) + "\n"


def srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt(cues: list[tuple[float, float, str]], width: int = 42, lines: int = 2) -> str:
    """SubRip subtitles: one cue a sentence, wrapped at `width` characters; a sentence of more than `lines` lines is shown in
    pieces of `lines` lines each, the time shared out by the length of the piece."""
    pieces: list[tuple[float, float, str]] = []
    for start, end, text in cues:
        wrapped = textwrap.wrap(text, width) or [text]
        groups = [wrapped[k:k + lines] for k in range(0, len(wrapped), lines)]
        weights = [sum(len(line) for line in group) for group in groups]
        at = start
        for number, (group, weight) in enumerate(zip(groups, weights), 1):
            until = end if number == len(groups) else at + (end - start) * weight / sum(weights)
            pieces.append((at, until, "\n".join(group)))
            at = until
    return "".join(f"{k}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n\n" for k, (a, b, text) in enumerate(pieces, 1))


def check_length(total: float, limit: float, scenes: list[tuple[str, float]], words_per_second: float = 2.5) -> None:
    """Fails, before anything is recorded, when the scenes add up to more than the limit."""
    if total <= limit:
        return
    longest = sorted(scenes, key=lambda s: -s[1])[:3]
    raise TooLong(f"the video would be {total:.1f} s long and the limit is {limit:g} s: cut about {round((total - limit) * words_per_second)} words "
                  f"(the longest scenes: {', '.join(f'{name} {seconds:.1f} s' for name, seconds in longest)})")


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def encode(work: Path, concat: Path, narration: Path, subtitles: Path | None, out: Path, fps: int, total: float, title: str = "") -> None:
    """One encode of all the photographs and the whole narration track into `out`, H.264 and AAC, cut at `total` seconds."""
    command = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", concat.name, "-i", str(narration.resolve())]
    if subtitles:
        command += ["-i", str(subtitles.resolve())]
    command += ["-filter_complex", f"[0:v]fps={fps}:round=near,format=yuv420p[v];[1:a]apad=whole_dur={total:.6f}[a]", "-map", "[v]", "-map", "[a]"]
    if subtitles:
        command += ["-map", "2:0", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng"]
    command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(fps), "-fps_mode", "cfr", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-t", f"{total:.3f}"]
    if title:
        command += ["-metadata", f"title={title}"]
    command.append(str(out.resolve()))
    done = run(command, cwd=work)
    if done.returncode or not out.is_file():
        raise AssemblyError(f"ffmpeg failed: {done.stderr.strip()[-600:]}")


def probe(path: Path) -> dict:
    done = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    if done.returncode:
        raise AssemblyError(f"ffprobe could not read {path.name}: {done.stderr.strip()[-300:]}")
    return json.loads(done.stdout)


def verify(info: dict, size: tuple[int, int], fps: int, limit: float, total: float) -> list[str]:
    """What is wrong with the file, in words: empty if it is as promised."""
    problems, streams = [], info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(info.get("format", {}).get("duration", 0))
    if duration > limit:
        problems.append(f"the video is {duration:.1f} s long and the limit is {limit:g} s")
    if video is None:
        problems.append("there is no video stream")
    else:
        if video.get("codec_name") != "h264":
            problems.append(f"the video is {video.get('codec_name')}, not h264")
        if (video.get("width"), video.get("height")) != size:
            problems.append(f"the picture is {video.get('width')}x{video.get('height')}, not {size[0]}x{size[1]}")
        if video.get("r_frame_rate") != f"{fps}/1" or video.get("avg_frame_rate") != f"{fps}/1":
            problems.append(f"the frame rate is not a constant {fps}: {video.get('r_frame_rate')} nominal, {video.get('avg_frame_rate')} average")
    if audio is None:
        problems.append("there is no audio stream")
    else:
        heard = float(audio.get("duration") or duration)
        seen = float((video or {}).get("duration") or duration)
        if heard < seen - 0.1 or heard < total - 0.1:
            problems.append(f"the audio ({heard:.2f} s) ends before the video ({seen:.2f} s, the scenes add up to {total:.2f} s): it was cut short")
    if abs(duration - total) > 0.5:
        problems.append(f"the video is {duration:.2f} s long, but the scenes add up to {total:.2f} s")
    return problems


def contact_sheet(video: Path, out: Path, total: float) -> bool:
    """A picture of twelve frames spread over the video, to look at before the video is watched. False if ffmpeg could not."""
    done = run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", f"fps=12/{max(total, 1):.3f},scale=320:-2,tile=4x3", "-frames:v", "1", "-q:v", "3", str(out)])
    return done.returncode == 0 and out.is_file()
