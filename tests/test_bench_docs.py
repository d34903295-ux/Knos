"""Every benchmark number in the docs is generated from docs/bench.json: a doc that drifts fails here."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_docs_match_the_one_benchmark_source():
    p = Path(__file__).resolve().parents[1] / "scripts" / "bench_docs.py"
    spec = importlib.util.spec_from_file_location("bench_docs", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(check=True) == 0


def _claims():
    p = Path(__file__).resolve().parents[1] / "scripts" / "claims_check.py"
    spec = importlib.util.spec_from_file_location("claims_check", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_pitch_number_has_a_fact_that_holds(capsys):
    """README, the home page, the submission and both scripts say no number that docs/facts.json cannot back."""
    cc = _claims()
    assert cc.main(["--offline"]) == 0, capsys.readouterr().out


def test_a_number_without_a_fact_and_a_stale_fact_both_fail(tmp_path, monkeypatch, capsys):
    cc = _claims()
    for rel in [*cc.PITCH, "docs/facts.json", "docs/bench.json", "docs/BENCH.md", "docs/TAMPER.md",
                "programs/knos_pay/src/lib.rs", ".github/workflows/fund.yml", ".github/workflows/index.yml"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes((cc.ROOT / rel).read_bytes())
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    assert cc.main(["--offline"]) == 0
    readme = tmp_path / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nUsed by 9,999 repositories.\n", encoding="utf-8")
    capsys.readouterr()
    assert cc.main(["--offline"]) == 1
    assert "'9,999' has no fact" in capsys.readouterr().out
    readme.write_text(readme.read_text(encoding="utf-8").replace("\nUsed by 9,999 repositories.\n", ""), encoding="utf-8")
    tamper = tmp_path / "docs" / "TAMPER.md"
    tamper.write_text(tamper.read_text(encoding="utf-8").replace("fooled 17/21", "fooled 18/21"), encoding="utf-8")
    assert cc.main(["--offline"]) == 1
    assert "does not have" in capsys.readouterr().out
