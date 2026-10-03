"""Tamper benchmark: test-weakening pull requests against three sample repositories, each judged three ways.

    python scripts/tamper_bench.py [--out docs/TAMPER.md] [--only python|node|ruby]

The samples (tests/bench_tamper/) are the same small project with the same bug, in Python (pytest), JavaScript
(node:test) and Ruby (minitest), and each has its own 21 attacks: the same 21 ideas, each as that language's tools
would meet it (attacks.py, attacks_node.py, attacks_ruby.py).

"CI green" runs the PR's own CI test steps (the `run:` lines of its .github/workflows/ci.yml) in the PR's tree and
passes on exit 0, as a branch protection rule would. "Knos, tests" runs knos.judge.judge(base, pr, cfg) on the
acceptance bundle written in the repository's language (.knos/acceptance/1), the same function prove.yml's check job
runs. "Knos, black box" runs it on the black-box bundle (.knos/acceptance/2), whose check never loads the pull
request's code. An attack "fools" a judge when the judge passes it although slugify was not fixed.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "tests" / "bench_tamper"
sys.path.insert(0, str(ROOT / "src"))

from knos import judge as prove  # noqa: E402


class Sample(NamedTuple):
    key: str
    title: str          # the language and its test runner
    folder: str         # under tests/bench_tamper
    attacks: str        # the module with CONTROL, ATTACKS and OUT_OF_SCOPE
    test_dirs: list     # what the base's .knos/proof.toml says
    needs: tuple        # the commands its CI and its runner use
    versions: tuple     # (name, argv that prints its version): what the report was measured with


SAMPLES = {s.key: s for s in (
    Sample("python", "Python, pytest", "sample", "attacks.py", ["tests"], (),
           (("Python", [sys.executable, "-c", "import platform; print(platform.python_version())"]),
            ("pytest", [sys.executable, "-c", "import pytest; print(pytest.__version__)"]))),
    Sample("node", "JavaScript, node:test", "sample_node", "attacks_node.py", ["test"], ("node", "npm"),
           (("Node", ["node", "-p", "process.versions.node"]), ("npm", ["npm", "--version"]))),
    Sample("ruby", "Ruby, minitest", "sample_ruby", "attacks_ruby.py", ["test"], ("ruby",),
           (("Ruby", ["ruby", "-e", "print RUBY_VERSION"]), ("minitest", ["ruby", "-e", 'require "minitest"; print Minitest::VERSION']))),
)}


def available(sample: Sample) -> bool:
    """Whether this machine can run that sample at all (its language is installed)."""
    return all(shutil.which(tool) for tool in sample.needs)


def measured_with(sample: Sample) -> str:
    """"Python 3.12.3 and pytest 8.4.2": the versions on this machine. What plain CI lets through depends on them."""
    def version(argv: list) -> str:
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=60).stdout.strip() or "?"
        except (OSError, subprocess.SubprocessError):
            return "?"
    return " and ".join(f"{name} {version(argv)}" for name, argv in sample.versions)


def _attacks(sample: Sample):
    spec = importlib.util.spec_from_file_location(f"tamper_{sample.attacks[:-3]}", BENCH / sample.attacks)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ci_green(pr: Path) -> bool:
    wf = pr / ".github" / "workflows" / "ci.yml"
    if not wf.is_file():
        return True   # no CI step: nothing fails
    runs = re.findall(r"^\s*-\s*run:\s*(.+)$", wf.read_text(encoding="utf-8"), re.M)
    for cmd in runs:
        cmd = re.sub(r"^python\b", lambda _: f'"{sys.executable}"', cmd.strip())
        env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
               "NPM_CONFIG_UPDATE_NOTIFIER": "false", "NPM_CONFIG_FUND": "false", "NPM_CONFIG_AUDIT": "false"}
        r = subprocess.run(cmd, shell=True, cwd=pr, env=env, capture_output=True, timeout=300)
        if r.returncode != 0:
            return False
    return True


def one(sample: Sample, base: Path, tmp: Path, i: int, name: str, fn, cache: dict) -> dict:
    pr = tmp / f"pr{i}"
    shutil.copytree(base, pr)
    fn(pr)
    green = ci_green(pr)
    shutil.rmtree(pr / "__pycache__", ignore_errors=True)
    v = prove.judge(base, pr, {"issue": "1", "test_dirs": sample.test_dirs}, cache=cache)
    out = {"name": name, "ci": green, "knos": v["passed"], "why": "; ".join(v["reasons"]) or "-", "box": None}
    if BLACKBOX:
        b = prove.judge(base, pr, {"issue": "2", "test_dirs": sample.test_dirs}, cache=cache)
        out.update({"box": b["passed"], "box_why": "; ".join(b["reasons"]) or "-"})
    return out


BLACKBOX = os.name != "nt"   # the black-box check reaches the tree through a shell wrapper


def run(key: str = "python") -> tuple[dict, list[dict]]:
    """(the control's row, the attacks' rows) for one sample repository."""
    sample = SAMPLES[key]
    mod = _attacks(sample)
    with tempfile.TemporaryDirectory(prefix="knos-tamper-") as t:
        tmp = Path(t)
        base = tmp / "base"
        shutil.copytree(BENCH / sample.folder, base, ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
        jobs = [mod.CONTROL, *mod.ATTACKS]
        cache: dict = {}
        first = one(sample, base, tmp, 0, *jobs[0], cache)   # the control also runs the base side once, for every PR
        with ThreadPoolExecutor(max_workers=8) as ex:
            got = [first, *ex.map(lambda a: one(sample, base, tmp, a[0], *a[1], cache), list(enumerate(jobs))[1:])]
    for g in got:
        g["out_of_scope"] = g["name"] in mod.OUT_OF_SCOPE     # no in-process test run can stop it
        g["gap"] = g["name"] in getattr(mod, "GAP", ())        # this language's runner misses it; another catches it
    return got[0], got[1:]


def _fooled(rows: list[dict]) -> tuple[int, int, int]:
    return sum(r["ci"] for r in rows), sum(r["knos"] for r in rows), sum(bool(r["box"]) for r in rows)


_LISTS = re.compile(r"^(pr: acceptance checks[^:]*|pass-to-pass broken): (.+)$")


def _stable(why: str) -> str:
    """The judge lists checks in the order the runner ran them, and minitest runs them in a random order. Each list
    is sorted here, so the same verdict prints the same line every time."""
    def one(reason: str) -> str:
        m = _LISTS.match(reason)
        return f"{m.group(1)}: {', '.join(sorted(m.group(2).split(', ')))}" if m else reason
    return "; ".join(one(r) for r in why.split("; "))


def section(sample: Sample, control: dict, rows: list[dict]) -> list[str]:
    n = len(rows)
    ci, kn, bx = _fooled(rows)
    yes = lambda b: "n/a" if b is None else "PASS (fooled)" if b else "fail"  # noqa: E731
    out = [f"## {sample.title}", "",
           f"tests/bench_tamper/{sample.folder}, attacked by tests/bench_tamper/{sample.attacks}. "
           f"Measured with {measured_with(sample)}.", "",
           f"**CI green fooled {ci}/{n}. Knos, tests: fooled {kn}/{n}. Knos, black box: fooled {bx}/{n}.**", "",
           f"Control (the honest fix): CI green {'passes' if control['ci'] else 'fails'}, "
           f"Knos, tests {'passes' if control['knos'] else 'fails'}, "
           f"Knos, black box {'passes' if control['box'] else 'n/a' if control['box'] is None else 'fails'}.", "",
           "| # | attack | CI green | Knos, tests | Knos, black box | Knos's reason (tests) |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        why = _stable(r["why"]).replace("|", "\\|")
        if len(why) > 120:
            why = why[:117] + "..."
        out.append(f"| {i} | {r['name']} | {yes(r['ci'])} | {yes(r['knos'])} | {yes(r['box'])} | {why} |")
    note = getattr(_attacks(sample), "NOTE", "")
    return out + ["", *([note, ""] if note else [])]


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def render(results: dict[str, tuple[dict, list[dict]]]) -> str:
    """docs/TAMPER.md from {sample key: (control, rows)}, in SAMPLES' order."""
    done = [(SAMPLES[k], *results[k]) for k in SAMPLES if k in results]
    total = [r for _, _, rows in done for r in rows]
    ci, kn, bx = _fooled(total)
    count = {1: "One sample repository", 2: "Two sample repositories", 3: "Three sample repositories"}[len(done)]
    out = ["# Tamper benchmark", "",
           "Generated by `python scripts/tamper_bench.py`; checked by tests/test_tamper_bench.py.", "",
           f"{count} (tests/bench_tamper/): the same small project, in {_and([s.title.split(',')[0] for s, _, _ in done])},",
           "with one bug: `slugify` keeps punctuation. Each attack is a pull request that does NOT fix the bug and instead",
           "weakens what judges it: the same 21 ideas for each repository, each as that language's tools would meet it.",
           "Three judges see each one:", "",
           "- **CI green**: the PR's own CI test steps (its `.github/workflows/ci.yml`), run in the PR tree; pass on exit 0.",
           "- **Knos, tests**: `knos.judge.judge` on the funder's acceptance tests `.knos/acceptance/1/`, written in the",
           "  repository's language: protected paths, overlay of the base's tests and configuration, a random sentinel test",
           "  and a failing canary, fail-to-pass, pass-to-pass and test count. The code under test shares a process with",
           "  the test runner.",
           "- **Knos, black box**: `knos.judge.judge` on the funder's black-box bundle `.knos/acceptance/2/`: the check",
           "  runs as the judge and never loads the pull request's code; it asks that code for answers through the",
           "  sandbox and compares them with its own reference on generated inputs.", "",
           "| repository | attacks | CI green fooled | Knos, tests fooled | Knos, black box fooled |", "|---|---|---|---|---|"]
    for sample, _, rows in done:
        a, b, c = _fooled(rows)
        out.append(f"| {sample.title} | {len(rows)} | {a} | {b} | {c} |")
    out += [f"| **all** | **{len(total)}** | **{ci}** | **{kn}** | **{bx}** |", ""]
    for sample, control, rows in done:
        out += section(sample, control, rows)
    fooled = [r for r in total if r["knos"]]
    gaps = [r for r in fooled if r.get("gap")]
    out += [f"Of the {len(fooled)} that fool an in-process test run, {len(fooled) - len(gaps)} are limits of running tests "
            "at all, not bugs to patch:",
            "a stub that returns the expected constants passes any check made of fixed examples, and code that shares",
            "a process with the test runner can stop the acceptance bodies from running, or from asserting anything,",
            "while leaving every other test alone."
            + (f" The other {'one is a gap' if len(gaps) == 1 else f'{len(gaps)} are gaps'} in one runner that the "
               "others do not have: see the" if gaps else ""),
            *(["note under that repository's table."] if gaps else []),
            "The black-box check has none of these weaknesses, because the pull request's code is never in the process",
            "that decides.",
            "A bounty paid without a merge has no second look: when its check passes it is paid, with no review window",
            "and no veto. So payment without a merge (tests mode) is offered only when the acceptance bundle is black-box:",
            "the submission runs as a separate process and only its output is compared. Everything else is funded in merge",
            "mode, which pays on a maintainer's merge.", ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "TAMPER.md"))
    ap.add_argument("--only", choices=list(SAMPLES), help="run one sample and print its section (docs/TAMPER.md is not written)")
    a = ap.parse_args(argv)
    if a.only:
        print("\n".join(section(SAMPLES[a.only], *run(a.only))))
        return 0
    missing = [s.title for s in SAMPLES.values() if not available(s)]
    if missing:
        print(f"not installed here: {', '.join(missing)}. docs/TAMPER.md reports every sample, so it is not written; "
              "run one sample with --only", file=sys.stderr)
        return 1
    text = render({key: run(key) for key in SAMPLES})
    Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
