"""Every benchmark number in the docs is generated from docs/bench.json: a doc that drifts fails here. Every number in
the pitch-facing text has a fact, and a number only the release run can measure is a slot until that run fills it."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _copy(tmp_path: Path, files) -> None:
    for rel in files:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes((ROOT / rel).read_bytes())


def test_docs_match_the_one_benchmark_source():
    assert _script("bench_docs").main(check=True) == 0


def test_every_pitch_number_has_a_fact_that_holds(capsys):
    """README, the home page, the submission and the three scripts say no number that docs/facts.json cannot back."""
    assert _script("claims_check").main(["--offline"]) == 0, capsys.readouterr().out


def test_a_number_without_a_fact_and_a_stale_fact_both_fail(tmp_path, monkeypatch, capsys):
    cc = _script("claims_check")
    facts = json.loads((ROOT / "docs" / "facts.json").read_text(encoding="utf-8"))["facts"]
    backing = {f.get("json") or f.get("file") for f in facts} - {None}
    assert "docs/TAMPER.md" in backing and "docs/bench.json" in backing
    _copy(tmp_path, {*cc.PITCH, "docs/facts.json", *backing})
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    assert cc.main(["--offline"]) == 0
    readme = tmp_path / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nUsed by 9,999 repositories.\n", encoding="utf-8")
    capsys.readouterr()
    assert cc.main(["--offline"]) == 1
    assert "'9,999' has no fact" in capsys.readouterr().out
    readme.write_text(readme.read_text(encoding="utf-8").replace("\nUsed by 9,999 repositories.\n", ""), encoding="utf-8")
    tamper = tmp_path / "docs" / "TAMPER.md"
    tamper.write_text(tamper.read_text(encoding="utf-8").replace("| **63** | **56** |", "| **63** | **57** |"), encoding="utf-8")
    assert cc.main(["--offline"]) == 1
    assert "does not have" in capsys.readouterr().out


def test_every_slot_in_the_submission_is_one_the_script_knows_and_holds_no_digit():
    """A slot is filled by name, so a name nobody defined would stay open for ever; and a digit in a name would read
    as a number with no fact."""
    bd = _script("bench_docs")
    assert {name for _doc, name in bd.slots()} <= set(bd.SLOTS)
    assert all(name.replace("_", "").isalpha() and what for name, (what, _path) in bd.SLOTS.items())
    assert {path for _what, path in bd.DEVNET} >= {path for _what, path in bd.SLOTS.values() if path}


def test_the_release_fills_a_slot_once_with_its_number_and_a_fact(tmp_path, monkeypatch, capsys):
    bd, cc = _script("bench_docs"), _script("claims_check")
    facts = json.loads((ROOT / "docs" / "facts.json").read_text(encoding="utf-8"))["facts"]
    _copy(tmp_path, {*cc.PITCH, "docs/facts.json", *({f.get("json") or f.get("file") for f in facts} - {None})})
    sub = tmp_path / "docs" / "submission"
    for doc in sub.glob("*.md"):
        doc.unlink()                                    # only this test's two files hold slots
    (sub / "a.md").write_text("Median: [[stat: seconds_from_merge_to_paid]] seconds over [[stat: payments_timed]] payments.\n"
                              "[[stat: tests_passing]] tests pass. [[stat: outside_funders]] funders.\n", encoding="utf-8")
    stats = tmp_path / "stats.json"
    stats.write_text(json.dumps({"updated": "2026-10-05 10:00 UTC", "by_deployment": {"second": {"funded": 4, "completed": 3}},
                                 "outside": {"funded": 0, "completed": 0, "funders": None, "repeat_funders": 0},
                                 "latency": {"merge_to_paid": {"count": 3, "median": 41, "p90": 77},
                                             "comment_to_funded": {"median": 19}}}), encoding="utf-8")
    left = bd.fill(str(stats), root=tmp_path)
    text = (sub / "a.md").read_text(encoding="utf-8")
    assert text.startswith("Median: 41 seconds over 3 payments.\n")
    # a number stats.json does not have, and one only the release run can give, stay slots
    assert left == ["outside_funders", "tests_passing"] and "[[stat: tests_passing]] tests pass. [[stat: outside_funders]]" in text
    bench = json.loads((tmp_path / "docs" / "bench.json").read_text(encoding="utf-8"))
    assert bench["devnet"]["stats"]["latency"]["merge_to_paid"] == {"count": 3, "median": 41, "p90": 77}
    assert bench["devnet"]["stats"]["by_deployment"]["second"] == {"funded": 4, "completed": 3}
    said = {tuple(f["say"]): f for f in json.loads((tmp_path / "docs" / "facts.json").read_text(encoding="utf-8"))["facts"]}
    assert said[("41",)]["path"] == "devnet.stats.latency.merge_to_paid.median" and said[("41",)]["equals"] == 41
    # the release run's own number, with where it was measured
    assert bd.fill(given={"tests_passing": (1234, "pytest -q on the release commit")}, root=tmp_path) == ["outside_funders"]
    assert "1,234 tests pass." in (sub / "a.md").read_text(encoding="utf-8")
    bench = json.loads((tmp_path / "docs" / "bench.json").read_text(encoding="utf-8"))
    assert bench["release"]["tests_passing"] == {"value": 1234, "source": "pytest -q on the release commit"}
    assert bench["devnet"]["stats"]["latency"]["merge_to_paid"]["median"] == 41         # an earlier fill is kept
    fact = [f for f in json.loads((tmp_path / "docs" / "facts.json").read_text(encoding="utf-8"))["facts"] if f["say"] == ["1,234"]]
    assert fact and fact[0]["path"] == "release.tests_passing.value" and "pytest -q on the release commit" in fact[0]["what"]
    # the `devnet` block now shows what was measured, and says "measured at release" only where nothing was
    block = bd.devnet(bench["devnet"])
    assert "| tasks paid | 3 |" in block and "| seconds from the merge to the payment, median | 41 |" in block
    assert "| funders among them | measured at release |" in block and "2026-10-05 10:00 UTC" in block
    assert bd.main(root=tmp_path) == 0 and bd.main(check=True, root=tmp_path) == 0      # the blocks, written again
    assert "| tasks paid | 3 |" in (tmp_path / "docs" / "BENCH.md").read_text(encoding="utf-8")
    # a slot nobody defined fails the check that the suite runs
    (sub / "a.md").write_text("[[stat: made_up_number]] things.\n", encoding="utf-8")
    capsys.readouterr()
    assert bd.main(check=True, root=tmp_path) == 1 and "made_up_number" in capsys.readouterr().out
