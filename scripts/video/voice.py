"""Narration: each scene's words turned into speech, one sentence at a time, so that the length of every sentence is known
(measured from the audio, never guessed) and the subtitles and the scene lengths follow from it.

Two voices, both run as programs (this tool does not import either, both are GPL-3.0):
  piper:NAME   Piper, offline. NAME is a voice such as en_US-lessac-medium; its file is downloaded once into the voices
               folder (KNOS_VOICES, else a knos-video folder in the user's cache folder). pip install piper-tts
  edge:NAME    Microsoft Edge's online voices through edge-tts, such as en-US-AriaNeural. Needs the internet and works as
               long as Microsoft's service accepts it. pip install edge-tts

A storyboard may name several voices, separated by commas; the first one that speaks the whole video is used and the others
are not tried. Everything is converted to mono 16-bit audio at 48,000 samples a second and joined by counting samples, so the
narration track is exactly as long as the scenes.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

RATE = 48_000               # samples a second of the narration track
GAP = 0.22                  # seconds of quiet between two sentences of a scene


class VoiceError(RuntimeError):
    """A voice could not speak the words; the message says why and what to do."""


def split_sentences(text: str) -> list[str]:
    """The sentences of `text`: a split after . ! or ? (and a closing quote or bracket) that is followed by a space and a
    capital letter, a digit or an opening quote. `e.g. the` and `2.5%` are not split."""
    parts = re.split(r"(?<=[.!?][\"')\]])\s+(?=[A-Z0-9\"'(\[])|(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])", " ".join(text.split()))
    return [part.strip() for part in parts if part.strip()]


def parse_spec(spec: str) -> tuple[str, str]:
    engine, _, name = spec.partition(":")
    return ("edge" if engine == "edge-tts" else engine), name


def voices_dir() -> Path:
    if os.environ.get("KNOS_VOICES"):
        return Path(os.environ["KNOS_VOICES"])
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CACHE_HOME")
    return Path(base or Path.home() / ".cache") / "knos-video" / "voices"


def _module_or_program(module: str, program: str, how: str) -> list[str]:
    if importlib.util.find_spec(module) is not None:
        return [sys.executable, "-m", module]
    found = shutil.which(program)
    if found:
        return [found]
    raise VoiceError(f"{program} is not installed: {how}")


def to_wav(source: Path, target: Path) -> Path:
    """`source` (wav or mp3) as mono 16-bit audio at RATE."""
    done = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(target)],
                          capture_output=True, text=True)
    if done.returncode or not target.is_file():
        raise VoiceError(f"ffmpeg could not read the voice's audio {source.name}: {done.stderr.strip()[-300:]}")
    return target


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, RATE):
            raise VoiceError(f"{path.name} is not mono 16-bit {RATE} Hz audio")
        return w.readframes(w.getnframes())


def write_pcm(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE)
        w.writeframes(pcm)


def silence(seconds: float) -> bytes:
    return bytes(2 * round(seconds * RATE))


def piper(name: str, sentences: list[str], work: Path, speed: float = 1.0,
          run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> list[Path]:
    """One wav per sentence, from a single run of Piper (the voice is loaded once)."""
    command = _module_or_program("piper", "piper", "pip install piper-tts (it is GPL-3.0 licensed; this tool only runs it), or use an edge: voice")
    model = voices_dir() / f"{name}.onnx"
    if not model.is_file():
        model.parent.mkdir(parents=True, exist_ok=True)
        print(f"  downloading the voice {name} once, into {model.parent}", flush=True)
        done = run([sys.executable, "-m", "piper.download_voices", name, "--download-dir", str(model.parent)], capture_output=True, text=True, timeout=900)
        if done.returncode or not model.is_file():
            raise VoiceError(f"the Piper voice {name} is not in {model.parent} and could not be downloaded ({(done.stderr or '').strip()[-200:]}). "
                             "Download it by hand (python -m piper.download_voices NAME --download-dir FOLDER) and set KNOS_VOICES to FOLDER")
    out = work / "piper"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    done = run([*command, "-m", str(model), "-d", str(out), "--length-scale", f"{1 / speed:.3f}"], input="\n".join(sentences) + "\n",
               capture_output=True, text=True, timeout=900)
    if done.returncode:
        raise VoiceError(f"Piper failed: {(done.stderr or '').strip()[-300:]}")
    wrote = [Path(m) for m in re.findall(r"Wrote (.+\.wav)\s*$", done.stderr or "", re.M)]
    if len(wrote) != len(sentences):
        wrote = sorted(out.glob("*.wav"), key=lambda p: p.name)       # a version that logs differently: the files, in the order they were made
    if len(wrote) != len(sentences) or not all(p.is_file() for p in wrote):
        raise VoiceError(f"Piper made {len(wrote)} audio files for {len(sentences)} sentences")
    return [to_wav(p, work / f"piper-{k:04d}.wav") for k, p in enumerate(wrote)]


def edge(name: str, sentences: list[str], work: Path, speed: float = 1.0,
         run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> list[Path]:
    """One wav per sentence, each asked of Microsoft's service (three tries each)."""
    command = _module_or_program("edge_tts", "edge-tts", "pip install edge-tts")
    rate = f"--rate={round((speed - 1) * 100):+d}%"
    out = []
    for k, text in enumerate(sentences):
        mp3 = work / f"edge-{k:04d}.mp3"
        for attempt in range(1, 4):
            done = run([*command, "--voice", name, rate, "--text", text, "--write-media", str(mp3)], capture_output=True, text=True, timeout=90)
            if done.returncode == 0 and mp3.is_file() and mp3.stat().st_size > 0:
                break
            if attempt == 3:
                raise VoiceError(f"edge-tts could not speak sentence {k + 1} ({(done.stderr or '').strip().splitlines()[-1:] or ['no message']}): "
                                 "the service needs the internet and may refuse a client; use piper: instead")
            time.sleep(2 * attempt)
        out.append(to_wav(mp3, work / f"edge-{k:04d}.wav"))
    return out


ENGINES = {"piper": piper, "edge": edge}


@dataclass
class SceneAudio:
    pcm: bytes                                  # the scene's narration track, quiet before and after included
    cues: list[tuple[float, float, str]]        # (start, end, sentence), seconds from the start of the scene

    @property
    def seconds(self) -> float:
        return len(self.pcm) / 2 / RATE


def scene_audio(sentences: list[str], pcms: list[bytes], lead: float, tail: float, hold: float = 0.0) -> SceneAudio:
    """The scene's track: `lead` seconds of quiet, the sentences with GAP between them, `tail` of quiet, and more quiet until
    the scene is at least `hold` seconds. The cues are counted in samples, so they match the audio exactly."""
    chunks, at, cues = [silence(lead)], round(lead * RATE), []
    for k, (sentence, pcm) in enumerate(zip(sentences, pcms)):
        if k:
            chunks.append(silence(GAP))
            at += round(GAP * RATE)
        cues.append((at / RATE, (at + len(pcm) // 2) / RATE, sentence))
        chunks.append(pcm)
        at += len(pcm) // 2
    chunks.append(silence(tail))
    track = b"".join(chunks)
    if len(track) // 2 < round(hold * RATE):
        track += bytes(2 * (round(hold * RATE) - len(track) // 2))
    return SceneAudio(track, cues)


def speak(voices: list[str], scenes: list[tuple[str, float, float, float]], work: Path, speed: float = 1.0,
          say: Callable[[str], None] = print) -> tuple[str, list[SceneAudio]]:
    """Every scene's narration with the first voice of `voices` that can speak all of it. `scenes`: (words, lead, tail, hold).
    Returns the voice used and one SceneAudio a scene."""
    texts = [split_sentences(words) for words, *_ in scenes]
    flat = [s for sentences in texts for s in sentences]
    failures: list[str] = []
    for spec in voices:
        engine, name = parse_spec(spec)
        say(f"  voice {spec}: {len(flat)} sentences")
        try:
            wavs = ENGINES[engine](name, flat, work, speed)
        except VoiceError as why:
            failures.append(f"{spec}: {why}")
            say(f"  voice {spec} did not work ({why})" + ("; trying the next one" if spec != voices[-1] else ""))
            continue
        pcms, audio, k = [read_pcm(w) for w in wavs], [], 0
        for sentences, (_words, lead, tail, hold) in zip(texts, scenes):
            audio.append(scene_audio(sentences, pcms[k:k + len(sentences)], lead, tail, hold))
            k += len(sentences)
        return spec, audio
    raise VoiceError("no voice could speak the video: " + " | ".join(failures))
