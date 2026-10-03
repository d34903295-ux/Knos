"""scripts/video: the storyboard, the voices, the recorder's pure parts, the ffmpeg assembly and its checks, and the one command
that renders a storyboard.

Everything here runs offline with what a test machine has (ffmpeg for the assembly tests). The render of the sample storyboard
itself needs Playwright with Chromium, Piper with its voice file, and ffmpeg: it is skipped, with the reason, where they are
not installed.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "scripts" / "video"
sys.path.insert(0, str(VIDEO))

import assemble  # noqa: E402
import record  # noqa: E402
import render  # noqa: E402
import storyboard as sb  # noqa: E402
import voice  # noqa: E402

SAMPLE = VIDEO / "sample.storyboard"
VOICES = os.environ.get("KNOS_VOICES")      # read before the tests clear every KNOS_ setting: the folder where this machine keeps its Piper voices
ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg and ffprobe are not installed")


def tone(path: Path, seconds: float, rate: int = voice.RATE) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", round(8000 * math.sin(2 * math.pi * 440 * k / rate))) for k in range(round(seconds * rate))))
    return path


# ---- the storyboard ---------------------------------------------------------------------------------------------------

def test_the_sample_storyboard_parses_and_shows_the_three_kinds_of_scene():
    board = sb.parse(SAMPLE.read_text(encoding="utf-8"), SAMPLE)
    assert [s.id for s in board.scenes] == ["title", "page", "commands", "close"]
    assert [s.kind for s in board.scenes] == ["still", "url", "terminal", "still"]
    assert (board.size, board.fps, board.voices, board.env, board.cwd) == ((1280, 720), 30, ["piper:en_US-lessac-medium"], {"PYTHONPATH": "src"}, ROOT)
    title, page, commands, _close = board.scenes
    assert title.hold == 5 and title.say.startswith("This is a sample of the video tool. A storyboard says what to show and what to say. A voice")
    assert (page.target, page.scroll, page.wait) == ("sample/page.html", 1400, 0.5)
    assert [c.text.split()[0] for c in commands.commands] == ["{python}", "{python}"] and not any(c.allow_failure for c in commands.commands)
    for scene in board.scenes:                       # every file the sample names is in the repository
        if scene.kind != "terminal":
            assert (VIDEO / scene.target).is_file(), scene.target
    assert 40 < sb.estimate(board) < 70


def test_say_lines_go_on_from_each_other_and_comments_and_blank_lines_mean_nothing():
    board = sb.parse("# a comment\n\ntitle: T\nspeed: 1.1\npad: 0.2 0.3\n--- a\nsay: One two.\n  Three: four.\n\nsay: Five.\nshow: still x.png\n# the end\n")
    assert board.scenes[0].say == "One two. Three: four. Five." and (board.title, board.speed, board.pad) == ("T", 1.1, (0.2, 0.3))
    run = sb.parse("--- a\nsay: x\nrun: first\nrun!: second\n").scenes[0]
    assert (run.kind, [(c.text, c.allow_failure) for c in run.commands]) == ("terminal", [("first", False), ("second", True)])


@pytest.mark.parametrize("text, message", [
    ("", "no scenes"),
    ("--- a\nsay: x\n", "line 1: scene a shows nothing"),
    ("--- a\nshow: still x.png\n", "line 1: scene a has nothing to say"),
    ("--- a\nsay: x\nshow: url a\nrun: ls\n", "line 4: scene a shows one thing; it shows a url already"),
    ("--- a\nsay: x\nrun: ls\nshow: still b.png\n", "line 4: scene a shows one thing; it shows a terminal already"),
    ("--- a\nsay: x\nshow: still b.png\n--- a\n", "line 4: there is a scene called a already (line 1)"),
    ("--- a b\n", "expected `key: value` or a scene start"),
    ("title x\n", "line 1: expected `key: value`"),
    ("size: 1281x720\n", "line 1: size is WIDTHxHEIGHT, both even"),
    ("fps: 120\n", "line 1: fps is between 24 and 60, not 120"),
    ("voice: festival:x\n", "line 1: a voice is piper:NAME or edge:NAME"),
    ("--- a\nsay: x\nshow: movie z.mp4\n", "line 3: show: is `url ADDRESS` or `still FILE`"),
    ("--- a\nsay: x\nbogus: 1\n", "line 3: 'bogus' is not a line of a scene"),
    ("bogus: 1\n", "line 1: 'bogus' is not a header line"),
    ("--- a\nsay: x\nshow: still b.png\nscroll: 10\n", "line 1: scroll: is for a url scene"),
    ("--- a\nsay: x\nshow: url b.html\nscroll: lots\n", "line 4: scroll is a number, not 'lots'"),
    ("--- bad name\n", "expected `key: value`"),
    ("--- \nsay: x\n", "a scene starts with --- and a name"),
])
def test_a_storyboard_that_is_wrong_says_which_line(text, message):
    with pytest.raises(sb.StoryboardError, match=re.escape(message)):
        sb.parse(text)


# ---- the voice --------------------------------------------------------------------------------------------------------

def test_sentences_are_split_where_a_person_would_pause():
    assert voice.split_sentences("One. Two! Three? Four.") == ["One.", "Two!", "Three?", "Four."]
    assert voice.split_sentences("It costs 2.5% e.g. on a bounty. Then it stops.") == ["It costs 2.5% e.g. on a bounty.", "Then it stops."]
    assert voice.split_sentences('He said "stop." Then he left.') == ['He said "stop."', "Then he left."]
    assert voice.split_sentences("Version 0.3.12 is out. 48 hours pass.") == ["Version 0.3.12 is out.", "48 hours pass."]
    assert voice.split_sentences("  one\n  line   only  ") == ["one line only"]


def test_a_scene_track_is_counted_in_samples_and_its_cues_match_it():
    one, half = bytes(2 * voice.RATE), bytes(voice.RATE)                  # 1 s and 0.5 s of audio
    a = voice.scene_audio(["One.", "Two."], [one, half], lead=0.4, tail=0.6)
    assert len(a.pcm) // 2 == round(0.4 * voice.RATE) + voice.RATE + round(voice.GAP * voice.RATE) + voice.RATE // 2 + round(0.6 * voice.RATE)
    assert a.cues[0] == (0.4, 1.4, "One.")
    assert a.cues[1][0] == pytest.approx(1.4 + voice.GAP) and a.cues[1][1] == pytest.approx(1.4 + voice.GAP + 0.5)
    assert a.seconds == pytest.approx(0.4 + 1 + voice.GAP + 0.5 + 0.6)
    assert voice.scene_audio(["One."], [one], lead=0.4, tail=0.6, hold=5).seconds == 5.0      # a scene may be asked to last longer than its words


@ffmpeg
def test_piper_is_run_once_for_all_sentences_and_each_line_gets_its_own_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOS_VOICES", str(tmp_path / "voices"))
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "v.onnx").write_bytes(b"model")
    monkeypatch.setattr(voice, "_module_or_program", lambda *how: ["piper-fake"])
    calls, behaviour = [], {"mode": "ok"}

    def fake(command, **kw):
        calls.append((command, kw))
        out = Path(command[command.index("-d") + 1])
        lines = kw["input"].split("\n")[:-1]
        made = lines if behaviour["mode"] == "ok" else lines[:1]
        logged = "".join(f"INFO:__main__:Wrote {tone(out / f'{9000 + k}.wav', 0.1 * (k + 1), rate=22050)}\n" for k, _ in enumerate(made))
        return subprocess.CompletedProcess(command, 1 if behaviour["mode"] == "fail" else 0, "", "boom" if behaviour["mode"] == "fail" else logged)

    wavs = voice.piper("v", ["One.", "Two.", "Three."], tmp_path, speed=1.25, run=fake)
    command, kw = calls[0]
    assert len(calls) == 1 and kw["input"] == "One.\nTwo.\nThree.\n" and command[0] == "piper-fake"
    assert command[command.index("--length-scale") + 1] == "0.800" and command[command.index("-m") + 1] == str(tmp_path / "voices" / "v.onnx")
    assert [len(voice.read_pcm(w)) / 2 / voice.RATE for w in wavs] == pytest.approx([0.1, 0.2, 0.3], abs=0.01)
    behaviour["mode"] = "short"
    with pytest.raises(voice.VoiceError, match="Piper made 1 audio files for 3 sentences"):
        voice.piper("v", ["One.", "Two.", "Three."], tmp_path, run=fake)
    behaviour["mode"] = "fail"
    with pytest.raises(voice.VoiceError, match="Piper failed: boom"):
        voice.piper("v", ["One."], tmp_path, run=fake)


def test_a_piper_voice_that_is_not_there_is_downloaded_once_and_a_failure_to_do_so_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOS_VOICES", str(tmp_path / "voices"))
    monkeypatch.setattr(voice, "_module_or_program", lambda *how: ["piper-fake"])
    seen = []

    def no(command, **kw):
        seen.append(command)
        return subprocess.CompletedProcess(command, 1, "", "no network")
    with pytest.raises(voice.VoiceError, match=r"could not be downloaded \(no network\).*KNOS_VOICES"):
        voice.piper("en_US-x-medium", ["One."], tmp_path, run=no)
    assert seen[0][1:] == ["-m", "piper.download_voices", "en_US-x-medium", "--download-dir", str(tmp_path / "voices")]


@ffmpeg
def test_edge_asks_again_and_then_says_to_use_piper(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "_module_or_program", lambda *how: ["edge-fake"])
    monkeypatch.setattr(voice.time, "sleep", lambda seconds: None)
    attempts = []

    def flaky(command, **kw):
        attempts.append(command)
        target = Path(command[command.index("--write-media") + 1])
        if len(attempts) == 1:
            return subprocess.CompletedProcess(command, 1, "", "403")
        tone(target, 0.2, rate=24000)           # ffmpeg reads the audio by what it is, whatever the name
        return subprocess.CompletedProcess(command, 0, "", "")
    (wav,) = voice.edge("en-US-AriaNeural", ["One."], tmp_path, speed=1.1, run=flaky)
    assert len(attempts) == 2 and "--rate=+10%" in attempts[0] and attempts[0][attempts[0].index("--voice") + 1] == "en-US-AriaNeural"
    assert len(voice.read_pcm(wav)) / 2 / voice.RATE == pytest.approx(0.2, abs=0.01)
    attempts.clear()

    def refused(command, **kw):
        attempts.append(command)
        return subprocess.CompletedProcess(command, 1, "", "WSServerHandshakeError: 403")
    with pytest.raises(voice.VoiceError, match="could not speak sentence 1.*use piper: instead"):
        voice.edge("en-US-AriaNeural", ["One."], tmp_path, run=refused)
    assert len(attempts) == 3


def test_the_first_voice_that_speaks_the_whole_video_is_used_and_the_failure_of_the_others_is_said(tmp_path, monkeypatch):
    spoken = []

    def broken(name, sentences, work, speed=1.0):
        raise voice.VoiceError("403")

    def works(name, sentences, work, speed=1.0):
        spoken.append((name, list(sentences), speed))
        return [tone(work / f"s{k}.wav", 0.5 + k) for k in range(len(sentences))]
    said: list[str] = []
    monkeypatch.setattr(voice, "ENGINES", {"piper": broken, "edge": works})
    used, audio = voice.speak(["piper:a", "edge:b"], [("One two. Three.", 0.4, 0.6, 0.0), ("Four.", 0.4, 0.6, 0.0)], tmp_path, 1.2, said.append)
    assert used == "edge:b" and spoken == [("b", ["One two.", "Three.", "Four."], 1.2)]
    assert [len(a.cues) for a in audio] == [2, 1] and any("voice piper:a did not work (403); trying the next one" in line for line in said)
    assert audio[0].cues[1][0] == pytest.approx(0.4 + 0.5 + voice.GAP)
    monkeypatch.setattr(voice, "ENGINES", {"piper": broken, "edge": broken})
    with pytest.raises(voice.VoiceError, match=r"no voice could speak the video: piper:a: 403 \| edge:b: 403"):
        voice.speak(["piper:a", "edge:b"], [("One.", 0.4, 0.6, 0.0)], tmp_path)


# ---- the recorder's pure parts ----------------------------------------------------------------------------------------

def test_output_is_cleaned_as_a_terminal_would_leave_it():
    assert record.clean("\x1b[31mred\x1b[0m\nprogress 10%\rprogress 100%\n\n\n") == ["red", "progress 100%"]
    long = record.clean("x" * 500 + "\n" + "\n".join(str(k) for k in range(250)))
    assert long[0] == "x" * 300 + "..." and len(long) == 201 and long[-1] == "... (51 more lines)"


def test_commands_run_for_real_and_a_failure_stops_the_render_unless_it_was_written_run_bang(tmp_path):
    ok = record.run_commands([sb.Command('{python} -c "print(1 + 1)"')], tmp_path, {"A_VARIABLE": "x"})
    assert (ok[0].shown, ok[0].lines, ok[0].code) == ('python -c "print(1 + 1)"', ["2"], 0) and ok[0].seconds >= 0
    env = record.run_commands([sb.Command('{python} -c "import os; print(os.environ[\'A_VARIABLE\'], os.environ[\'NO_COLOR\'])"')], tmp_path, {"A_VARIABLE": "x"})
    assert env[0].lines == ["x 1"]
    fail = sb.Command('{python} -c "import sys; print(\'boom\'); sys.exit(3)"')
    with pytest.raises(record.RecordError, match=r"exited with status 3.*run!:.*\nboom"):
        record.run_commands([fail], tmp_path, {})
    fail.allow_failure = True
    assert record.run_commands([fail], tmp_path, {})[0].code == 3
    with pytest.raises(record.RecordError, match="did not finish in 0.5 seconds"):
        record.run_commands([sb.Command('{python} -c "import time; time.sleep(3)"')], tmp_path, {}, timeout=0.5)


def test_the_terminals_timeline_fits_the_scene_and_the_page_is_closed_to_the_outside():
    results = [record.Result("python a.py", [f"line {k}" for k in range(30)], 0, 1.234), record.Result("python b.py", ["only"], 1, 0.1)]
    long = record.terminal_events(results, duration=30.0, lead=0.4, tail=0.6)
    short = record.terminal_events(results, duration=4.0, lead=0.4, tail=0.6)
    for events, duration in ((long, 30.0), (short, 4.0)):
        assert [e["at"] for e in events] == sorted(e["at"] for e in events) and events[0]["at"] >= 0.4
        assert max(e["at"] + e.get("dur", 0) for e in events) <= duration - 0.6 + 1e-6
    assert [e["text"] for e in long if e["kind"] == "type"] == ["python a.py", "python b.py"]
    assert long[-1] == {"at": long[-1]["at"], "kind": "line", "text": "(exit status 1, 0.1 s)", "cls": "dim"}
    page = record.terminal_page([{"at": 0, "kind": "line", "text": "</script><b>x</b> & more"}], (1280, 720), "terminal")
    assert "</script><b>" not in page and "<\\/script><b>" in page and "http://" not in page and "https://" not in page
    assert "width:1200px;height:640px" in page


# ---- the assembly -----------------------------------------------------------------------------------------------------

def test_subtitles_have_the_times_the_voice_speaks_and_long_sentences_are_shown_in_pieces():
    assert assemble.srt_time(3661.5) == "01:01:01,500" and assemble.srt_time(0.4) == "00:00:00,400"
    text = assemble.srt([(0.4, 2.42, "This is a sample of the video tool."), (3.0, 11.0, "word " * 40)])
    assert text.startswith("1\n00:00:00,400 --> 00:00:02,420\nThis is a sample of the video tool.\n\n2\n00:00:03,000 --> ")
    cues = [c for c in text.strip().split("\n\n")]
    assert len(cues) == 1 + 3 and all(len(c.splitlines()) <= 4 for c in cues)           # number, time, at most two lines of text
    times = [re.search(r"(\S+) --> (\S+)", c).groups() for c in cues[1:]]
    assert times[0][0] == "00:00:03,000" and times[-1][1] == "00:00:11,000" and times[0][1] == times[1][0] and times[1][1] == times[2][0]


def test_the_list_for_ffmpeg_names_every_photograph_with_its_time_and_the_last_one_twice():
    text = assemble.ffconcat([[("a/1.jpg", 1.5), ("a/2.jpg", 0.25)], [("b/1.jpg", 3.0)]])
    assert text.splitlines() == ["ffconcat version 1.0", "file 'a/1.jpg'", "duration 1.500000", "file 'a/2.jpg'", "duration 0.250000",
                                 "file 'b/1.jpg'", "duration 3.000000", "file 'b/1.jpg'"]
    with pytest.raises(assemble.AssemblyError, match="nothing was recorded"):
        assemble.ffconcat([[]])


def test_a_video_over_the_limit_is_refused_before_anything_is_recorded_and_the_message_says_how_much_to_cut():
    assemble.check_length(180.0, 180, [("a", 100.0)])                                  # exactly the limit is allowed
    with pytest.raises(assemble.TooLong, match=r"the video would be 190\.0 s long and the limit is 180 s: cut about 25 words "
                                               r"\(the longest scenes: a 100\.0 s, b 60\.0 s, c 30\.0 s\)"):
        assemble.check_length(190.0, 180, [("c", 30.0), ("a", 100.0), ("b", 60.0)])
    assert issubclass(assemble.TooLong, assemble.AssemblyError)


def good(duration: float = 41.5) -> dict:
    return {"format": {"duration": str(duration)}, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "duration": str(duration)},
        {"codec_type": "audio", "codec_name": "aac", "duration": str(duration - 0.013)}]}


def test_what_is_wrong_with_a_file_is_said_in_words():
    assert assemble.verify(good(), (1280, 720), 30, 180, 41.5) == []
    assert assemble.verify(good(180.0), (1280, 720), 30, 180, 180.0) == []
    assert "the video is 181.0 s long and the limit is 180 s" in assemble.verify(good(181.0), (1280, 720), 30, 180, 181.0)[0]
    varying = good()
    varying["streams"][0]["avg_frame_rate"] = "2997/100"
    assert "the frame rate is not a constant 30" in assemble.verify(varying, (1280, 720), 30, 180, 41.5)[0]
    short = good()
    short["streams"][1]["duration"] = "30.0"
    assert "it was cut short" in assemble.verify(short, (1280, 720), 30, 180, 41.5)[0]
    none = good()
    del none["streams"][1]
    assert assemble.verify(none, (1280, 720), 30, 180, 41.5) == ["there is no audio stream"]
    other = good()
    other["streams"][0].update(codec_name="vp8", width=800)
    assert [p.split(" ")[1] for p in assemble.verify(other, (1280, 720), 30, 180, 41.5)] == ["video", "picture"]
    assert "scenes add up to 60.00 s" in assemble.verify(good(), (1280, 720), 30, 180, 60.0)[-1]


@ffmpeg
def test_photographs_and_narration_become_one_file_at_a_constant_rate_with_the_audio_never_cut_short(tmp_path):
    for name, colour in (("red", "red"), ("blue", "blue")):
        (tmp_path / "frames" / name).mkdir(parents=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={colour}:s=64x64", "-frames:v", "1", str(tmp_path / "frames" / name / "1.jpg")], check=True)
    frames = [[("frames/red/1.jpg", 1.0), ("frames/red/1.jpg", 0.75)], [("frames/blue/1.jpg", 1.25)]]
    (tmp_path / "list.ffconcat").write_text(assemble.ffconcat(frames), encoding="utf-8")
    (tmp_path / "subs.srt").write_text(assemble.srt([(0.2, 1.0, "Hello.")]), encoding="utf-8")
    for seconds in (3.0, 2.0):                                # narration as long as the video, and shorter: it is padded, not the video cut
        tone(tmp_path / "n.wav", seconds)
        out = tmp_path / f"out{seconds:g}.mp4"
        assemble.encode(tmp_path, tmp_path / "list.ffconcat", tmp_path / "n.wav", tmp_path / "subs.srt", out, 30, 3.0, "A title")
        info = assemble.probe(out)
        assert assemble.verify(info, (64, 64), 30, 180, 3.0) == [], info
        kinds = {s["codec_type"]: s for s in info["streams"]}
        assert kinds["video"]["nb_frames"] == "90" and kinds["subtitle"]["codec_name"] == "mov_text" and info["format"]["tags"]["title"] == "A title"
        assert float(kinds["audio"]["duration"]) >= 2.9
    assert any("limit is 2 s" in p for p in assemble.verify(info, (64, 64), 30, 2, 3.0))
    sheet = tmp_path / "contact.jpg"
    assert assemble.contact_sheet(tmp_path / "out3.mp4", sheet, 3.0) and sheet.stat().st_size > 0


# ---- the one command --------------------------------------------------------------------------------------------------

def test_estimate_checks_a_storyboard_without_a_voice_or_a_browser(capsys):
    assert render.main([str(SAMPLE), "--estimate"]) == 0
    out = capsys.readouterr().out
    assert "4 scenes" in out and "about 54 s at 150 words a minute" in out and "terminal" in out


def test_a_wrong_storyboard_or_limit_is_exit_2_and_names_the_line(tmp_path, capsys):
    bad = tmp_path / "bad.storyboard"
    bad.write_text("--- a\nsay: x\nshow: movie z\n", encoding="utf-8")
    assert render.main([str(bad)]) == 2
    assert "stopped: the storyboard is not right: line 3: show: is `url ADDRESS` or `still FILE`" in capsys.readouterr().err
    assert render.main([str(SAMPLE), "--limit", "200"]) == 2
    assert "the limit is at most 180 seconds, and not 200" in capsys.readouterr().err
    assert render.main([str(tmp_path / "missing.storyboard")]) == 2
    assert render.main([str(SAMPLE), "--voice", "festival:x"]) == 2


def stub_render(monkeypatch, tmp_path, scene_seconds: float):
    """The render with the voice, the browser and ffmpeg's encode replaced, to follow what it does with their results."""
    monkeypatch.setattr(render, "check_tools", lambda: None)
    scenes = sb.parse(SAMPLE.read_text(encoding="utf-8"), SAMPLE).scenes
    audio = [voice.scene_audio(["One."], [bytes(2 * voice.RATE)], 0.4, 0.6, hold=scene_seconds) for _ in scenes]
    monkeypatch.setattr(render.voice, "speak", lambda *a, **k: ("piper:test", audio))
    monkeypatch.setattr(render.record, "run_commands", lambda commands, cwd, env: [record.Result("x", ["y"], 0, 0.1)])
    calls = []

    def recorded(board, scenes, work, say):
        calls.append("record")
        return [[(f"frames/{k}.jpg", n)] for k, (_scene, n, _results) in enumerate(scenes)]
    monkeypatch.setattr(render.record, "record", recorded)
    return calls


def test_over_the_limit_the_render_stops_before_recording_with_exit_3_and_leaves_no_video(tmp_path, monkeypatch, capsys):
    calls = stub_render(monkeypatch, tmp_path, scene_seconds=50.0)           # four scenes of 50 s: 200 s
    (tmp_path / "sample.mp4").write_bytes(b"an earlier render")
    assert render.main([str(SAMPLE), "--out", str(tmp_path)]) == 3
    assert "the video would be 200.0 s long and the limit is 180 s: cut about 50 words" in capsys.readouterr().err
    assert calls == [] and not (tmp_path / "sample.mp4").exists(), "nothing was recorded, and an old video is not left beside a failed render"


def test_a_file_that_comes_out_too_long_is_deleted_and_the_exit_is_3(tmp_path, monkeypatch, capsys):
    stub_render(monkeypatch, tmp_path, scene_seconds=5.0)

    def encoded(work, concat, narration, subtitles, out, fps, total, title=""):
        out.write_bytes(b"video")
    monkeypatch.setattr(render.assemble, "encode", encoded)
    monkeypatch.setattr(render.assemble, "probe", lambda path: good(181.0))
    assert render.main([str(SAMPLE), "--out", str(tmp_path)]) == 3
    assert "the video is not as promised, and was deleted: the video is 181.0 s long and the limit is 180 s" in capsys.readouterr().err
    assert not (tmp_path / "sample.mp4").exists() and not (tmp_path / "sample.srt").exists()


def test_a_good_render_reports_the_video_the_subtitles_and_the_contact_sheet(tmp_path, monkeypatch, capsys):
    stub_render(monkeypatch, tmp_path, scene_seconds=5.0)
    monkeypatch.setattr(render.assemble, "encode", lambda work, concat, narration, subtitles, out, fps, total, title="": out.write_bytes(b"video"))
    monkeypatch.setattr(render.assemble, "probe", lambda path: good(20.0))
    monkeypatch.setattr(render.assemble, "contact_sheet", lambda video, out, total: out.write_bytes(b"sheet") or True)
    assert render.main([str(SAMPLE), "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "20.0 s, 1280x720, 30 fps constant, H.264 and AAC (the limit is 180 s)" in out and "4 cues (also a track inside the video)" in out
    assert (tmp_path / "sample.mp4").is_file() and (tmp_path / "sample.srt").read_text(encoding="utf-8").startswith("1\n00:00:00,400 --> ")
    assert not (tmp_path / ".sample-work").exists(), "the working folder is removed when the render worked"


# ---- the sample, for real ---------------------------------------------------------------------------------------------

def _python_that_can_render() -> str | None:
    probe = "import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ('playwright', 'piper')) else 1)"
    for candidate in (sys.executable, shutil.which("python3"), shutil.which("python")):
        if candidate and subprocess.run([candidate, "-c", probe], capture_output=True).returncode == 0:
            return candidate
    return None


def test_the_sample_storyboard_renders_to_a_video_that_keeps_every_promise(tmp_path, monkeypatch):
    python = _python_that_can_render()
    if not python or not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("rendering needs Playwright with Chromium (pip install playwright; playwright install chromium), Piper (pip install piper-tts) and ffmpeg")
    if not VOICES or not (Path(VOICES) / "en_US-lessac-medium.onnx").is_file():
        pytest.skip("the Piper voice is not there: python -m piper.download_voices en_US-lessac-medium --download-dir FOLDER, then run the tests with KNOS_VOICES=FOLDER")
    monkeypatch.setenv("KNOS_VOICES", VOICES)
    done = subprocess.run([python, str(VIDEO / "render.py"), str(SAMPLE), "--out", str(tmp_path)], capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, f"{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
    mp4 = tmp_path / "sample.mp4"
    info = assemble.probe(mp4)
    assert assemble.verify(info, (1280, 720), 30, 180, float(info["format"]["duration"])) == []
    assert 30 < float(info["format"]["duration"]) < 70
    assert (tmp_path / "sample.srt").read_text(encoding="utf-8").count(" --> ") >= 9 and (tmp_path / "sample.contact.jpg").stat().st_size > 0
    over = subprocess.run([python, str(VIDEO / "render.py"), str(SAMPLE), "--out", str(tmp_path), "--limit", "20"], capture_output=True, text=True, timeout=300)
    assert over.returncode == 3 and "the limit is 20 s" in over.stderr and not mp4.exists()
