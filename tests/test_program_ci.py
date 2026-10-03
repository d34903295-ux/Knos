"""The program workflow keeps a branch push short and honest. Every job runs beside the others (a second deployment is
a second job, not more steps), every Rust build starts from a pinned cache action, the long random walk is four short
ones from seeds of their own, the container build waits for main, and the tests can only load a binary this commit's
source built. And the build scripts, driven with a stand-in for cargo: what each builds, under which names, and that
the build left to deploy is the real one."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "program.yml"
FIXTURES = ROOT / "tests" / "fixtures"


def _doc() -> dict:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))   # YAML 1.1 reads the key `on` as True
    return doc


def _runs(job: dict) -> str:
    return "\n".join(str(s.get("run", "")) for s in job["steps"])


def _legs(job: dict) -> dict[str, dict]:
    """What the job's matrix says about each deployment."""
    return {entry["deployment"]: entry for entry in job["strategy"]["matrix"]["include"]}


def _bash() -> str:
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("the build steps are bash; the programs are built on Linux")
    return bash


def test_a_second_deployment_is_a_job_beside_the_first_not_more_steps_in_it():
    doc = _doc()
    jobs = doc["jobs"]
    assert not [name for name, job in jobs.items() if "needs" in job]      # nothing waits: the wait is the longest one job
    build = jobs["build-test"]
    legs = _legs(build)
    assert set(legs) == {"first", "second"} and build["strategy"]["fail-fast"] is False
    assert (legs["first"]["workspace"], legs["second"]["workspace"]) == ("programs", "programs-v2")
    assert (legs["first"]["suffix"], legs["second"]["suffix"]) == ("", "-v2")   # the first deployment's artifact names stay
    for leg in legs.values():
        assert (ROOT / leg["build"]).is_file() and (ROOT / leg["cargo-test"] / "Cargo.toml").is_file(), leg
        assert (ROOT / leg["workspace"] / "Cargo.lock").is_file(), leg       # what the cache key and cargo audit read
    # cargo test, the build, the LiteSVM tests and the fixture check, in that order, for whichever deployment it is
    run = _runs(build)
    at = [run.index(s) for s in ('cd "$CARGO_TEST" && cargo test --release', 'bash "$BUILD"', "pytest -q -s $TESTS",
                                 "python scripts/interface_fixture.py --check")]
    assert at == sorted(at)
    assert build["env"] == {"DEPLOYMENT": "${{ matrix.deployment }}", "CARGO_TEST": "${{ matrix.cargo-test }}",
                            "BUILD": "${{ matrix.build }}", "TESTS": "${{ matrix.tests }}"}
    assert "matrix." not in run and "github." not in run                    # values reach the scripts through env only
    # the first deployment's tests are named; the second's are every test file of its two programs
    assert all((ROOT / t).is_file() for t in legs["first"]["tests"].split())
    assert legs["second"]["tests"] == "tests/test_oidc2_*.py tests/test_pay2_*.py"
    for pattern, harness in zip(legs["second"]["tests"].split(), ("_oidc2.py", "_pay2.py")):
        if (ROOT / "tests" / harness).is_file():                             # a program's tests arrive with its harness
            assert list(ROOT.glob(pattern)), f"{pattern} matches no file: pytest would fail the second deployment's job"
    paths = set(doc["on"]["push"]["paths"])
    assert {"programs/**", "programs-v2/**", "scripts/build_programs*.sh", "tests/test_oidc*.py", "tests/test_pay*.py"} <= paths
    audit = _runs(jobs["cargo-audit"])
    assert "(cd programs && cargo audit)" in audit and "(cd programs-v2 && cargo audit)" in audit
    # a newer push to a branch cancels the run of the push before it; on main every run finishes
    assert doc["concurrency"] == {"group": "program-${{ github.ref }}", "cancel-in-progress": "${{ github.ref != 'refs/heads/main' }}"}


def test_every_rust_build_starts_from_the_pinned_cache_action():
    doc = _doc()
    pin = json.loads((ROOT / "scripts" / "action_pins.json").read_text(encoding="utf-8"))["pins"]["Swatinem/rust-cache@v2"]
    assert re.fullmatch(r"[0-9a-f]{40}", pin)
    for name in ("build-test", "fuzz", "interface"):
        steps = doc["jobs"][name]["steps"]
        cache = [i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("Swatinem/rust-cache@")]
        cargo = [i for i, s in enumerate(steps) if re.search(r"\bcargo (test|build)|\$BUILD", str(s.get("run", "")))]
        assert len(cache) == 1 and steps[cache[0]]["uses"] == f"Swatinem/rust-cache@{pin}", name
        assert cargo and cache[0] < min(cargo), name                       # restored before the first compile
    build, fuzz = (next(s for s in doc["jobs"][j]["steps"] if str(s.get("uses", "")).startswith("Swatinem/")) for j in ("build-test", "fuzz"))
    # one cache per deployment: runs on main save it from build-test, branches and the walks read it
    key = "programs-${{ matrix.deployment }}-agave-${{ env.SOLANA_VERSION }}"
    assert build["with"]["shared-key"] == fuzz["with"]["shared-key"] == key
    assert build["with"]["workspaces"] == fuzz["with"]["workspaces"] == "${{ matrix.workspace }}\n${{ matrix.also }}\n"
    assert build["with"]["save-if"] == "${{ github.ref == 'refs/heads/main' }}" and fuzz["with"]["save-if"] is False
    for deployment, leg in _legs(doc["jobs"]["build-test"]).items():       # the same workspaces, or the key would differ
        walk = _legs(doc["jobs"]["fuzz"])[deployment]
        assert [walk[k] for k in ("workspace", "also", "build")] == [leg[k] for k in ("workspace", "also", "build")]
    # the container build compiles with no cache, so it is not on a branch push's path
    assert doc["jobs"]["verified-build"]["if"] == "github.event_name != 'push' || github.ref == 'refs/heads/main'"
    assert not any(str(s.get("uses", "")).startswith("Swatinem/") for s in doc["jobs"]["verified-build"]["steps"])
    assert sorted(doc["jobs"]["verified-build"]["strategy"]["matrix"]["workspace"]) == ["programs", "programs-v2"]


def test_ten_thousand_random_steps_are_four_walks_side_by_side_each_from_its_own_seed():
    fuzz = _doc()["jobs"]["fuzz"]
    matrix = fuzz["strategy"]["matrix"]
    assert matrix["deployment"] == ["first", "second"] and len(set(matrix["walk"])) == 4
    step = next(s for s in fuzz["steps"] if "random_walk" in str(s.get("run", "")))
    assert len(matrix["walk"]) * int(step["env"]["KNOS_FUZZ_N"]) == 10_000
    assert step["env"]["KNOS_FUZZ_SEED"] == "${{ github.run_id }}${{ github.run_attempt }}${{ matrix.walk }}"
    assert "pytest -q -s $TESTS -k random_walk" in step["run"] and 'bash "$BUILD"' in _runs(fuzz)   # this commit's binaries
    legs = _legs(fuzz)
    assert (legs["first"]["tests"], legs["second"]["tests"]) == ("tests/test_pay_chain.py", "tests/test_pay2_*.py")
    # and each walk takes the seed (the first deployment's used to walk one fixed path whatever the workflow set)
    walks = [p for pattern in (leg["tests"] for leg in legs.values()) for p in sorted(ROOT.glob(pattern))
             if "random_walk" in p.read_text(encoding="utf-8")]
    assert ROOT / "tests" / "test_pay_chain.py" in walks
    for path in walks:
        text = path.read_text(encoding="utf-8")
        assert re.search(r'FUZZ_SEED = int\(os\.environ\.get\("KNOS_FUZZ_SEED", "\d+"\)\)', text), path.name
        assert re.search(r'FUZZ_N = int\(os\.environ\.get\("KNOS_FUZZ_N", "\d+"\)\)', text), path.name
        assert "random.Random(FUZZ_SEED)" in text and not re.search(r"random\.Random\(\d", text), path.name


def test_the_tests_can_only_load_a_binary_this_commit_built(tmp_path):
    """The build step as the workflow runs it, around a build script that builds one binary less than was committed:
    the deployment's committed binaries are gone before it builds, so the one it does not build is missing afterwards
    (a test that loads it fails) instead of being tested as it was committed. The other deployment's are not touched."""
    bash = _bash()
    doc = _doc()
    for job in ("build-test", "fuzz"):
        [step] = [s for s in doc["jobs"][job]["steps"] if 'bash "$BUILD"' in str(s.get("run", ""))]
        for deployment, builds, left in (("first", "knos_pay_test.so", "knos_pay_v2_test.so knos_old_v2_test.so"),
                                         ("second", "knos_pay_v2_test.so", "knos_pay_test.so knos_old_test.so")):
            tree = tmp_path / job / deployment
            (tree / "tests" / "fixtures").mkdir(parents=True)
            for name in ("knos_pay_test.so", "knos_old_test.so", "knos_pay_v2_test.so", "knos_old_v2_test.so", "SHA256SUMS", "keys.json"):
                (tree / "tests" / "fixtures" / name).write_text("committed", encoding="utf-8")
            (tree / "build.sh").write_text(f"echo built > tests/fixtures/{builds}\n", encoding="utf-8")
            env = {**os.environ, "DEPLOYMENT": deployment, "BUILD": "build.sh"}
            r = subprocess.run([bash, "-e", "-c", step["run"]], cwd=str(tree), env=env, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            got = {p.name: p.read_text(encoding="utf-8").strip() for p in (tree / "tests" / "fixtures").iterdir()}
            want = {builds: "built", "SHA256SUMS": "committed", "keys.json": "committed", **dict.fromkeys(left.split(), "committed")}
            assert got == want, (job, deployment)


def test_every_committed_test_binary_is_one_its_deployments_build_script_builds():
    """The other half of the test above, where it can be seen before a push: a binary in tests/fixtures that neither
    build script builds would be missing in the program workflow."""
    legs = _legs(_doc()["jobs"]["build-test"])
    binaries = sorted(FIXTURES.glob("*.so"))
    assert binaries
    for binary in binaries:
        script = ROOT / legs["second" if "_v2_" in binary.name else "first"]["build"]
        code = "\n".join(ln for ln in script.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))
        assert re.search(rf"(?<![\w.]){re.escape(binary.name)}(?![\w.])", code), (
            f"tests/fixtures/{binary.name}: scripts/{script.name} does not build it. The program workflow removes the "
            f"committed binaries before it builds, so a test that loads this one would fail there: add its build, or remove it")


# ---- the build scripts, with a stand-in for cargo -------------------------------------------------------------------

CARGO = """#!/bin/sh
# cargo build-sbf [arguments], as far as a build script can tell: it writes <target>/deploy/<crate>.so (here a file
# that holds the arguments of the build), and like the real one it leaves a file that is already there and newer.
[ "$1" = build-sbf ] || exit 9
shift
crate=$(basename "$PWD")
if [ -n "${CARGO_TARGET_DIR:-}" ]; then target=$CARGO_TARGET_DIR; elif grep -q workspace ../Cargo.toml 2>/dev/null; then target=../target; else target=target; fi
mkdir -p "$target/deploy"
[ -e "$target/deploy/$crate.so" ] || echo "$crate $*" > "$target/deploy/$crate.so"
"""


@pytest.fixture()
def tree(tmp_path):
    """The build scripts in a tree shaped like the repository, and a `cargo` that records what it is asked to build."""
    bash = _bash()
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    for script in ("build_programs.sh", "build_programs_v2.sh"):
        shutil.copy(ROOT / "scripts" / script, root / "scripts" / script)
    (root / "tests" / "fixtures").mkdir(parents=True)
    (root / "tests" / "fixtures" / "SHA256SUMS").write_text(f"{'0' * 64}  docs/other.json\n", encoding="utf-8")
    for ws in ("programs", "programs-v2"):
        (root / ws).mkdir()
        (root / ws / "Cargo.toml").write_text('[workspace]\nmembers = ["knos_oidc", "knos_pay"]\n', encoding="utf-8")
        for crate in ("knos_oidc", "knos_pay"):
            (root / ws / crate).mkdir()
            (root / ws / crate / "Cargo.toml").write_text(f'[package]\nname = "{crate}"\n', encoding="utf-8")
    (root / "examples" / "oidc_gate").mkdir(parents=True)
    (root / "examples" / "oidc_gate" / "Cargo.toml").write_text('[package]\nname = "oidc_gate"\n', encoding="utf-8")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "cargo").write_text(CARGO, encoding="utf-8")
    (fake / "cargo").chmod(0o755)

    def build(script: str, *args: str, target: Path | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, "PATH": str(fake) + os.pathsep + os.environ["PATH"]}
        env.pop("CARGO_TARGET_DIR", None)
        if target:
            env["CARGO_TARGET_DIR"] = str(target)
        return subprocess.run([bash, str(root / "scripts" / script), *args], env=env, capture_output=True, text=True)
    return root, build


def _built(folder: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8").strip() for p in sorted(folder.glob("*.so"))}


def test_the_first_deployments_script_builds_its_test_binaries_and_leaves_the_real_builds_to_deploy(tree, tmp_path):
    root, build = tree
    want = {"knos_oidc_test.so": "knos_oidc --features testkeys", "knos_pay_nodevnet.so": "knos_pay --no-default-features",
            "knos_pay_test.so": "knos_pay", "oidc_gate_test.so": "oidc_gate"}
    deploy = root / "programs" / "target" / "deploy"
    deploy.mkdir(parents=True)
    (deploy / "knos_oidc.so").write_text("left by a build before this one", encoding="utf-8")   # cargo would keep it
    r = build("build_programs.sh")
    assert r.returncode == 0, r.stderr
    assert _built(root / "tests" / "fixtures") == want
    # what is left in target/deploy is the real build of each program, and the second deployment was not touched
    assert _built(deploy) == {"knos_oidc.so": "knos_oidc", "knos_pay.so": "knos_pay"}
    assert not (root / "programs-v2" / "target").exists()
    # again, where the last run left its files: every binary is still the build it is named for
    assert build("build_programs.sh").returncode == 0 and _built(root / "tests" / "fixtures") == want
    # with CARGO_TARGET_DIR, cargo writes there and the script reads there
    for made in ("programs/target", "examples/oidc_gate/target", "tests/fixtures/oidc_gate_test.so"):
        path = root / made
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    shared = tmp_path / "shared target"
    r = build("build_programs.sh", target=shared)
    assert r.returncode == 0, r.stderr
    assert _built(shared / "deploy") == {"knos_oidc.so": "knos_oidc", "knos_pay.so": "knos_pay", "oidc_gate.so": "oidc_gate"}
    assert _built(root / "tests" / "fixtures") == want and not (root / "programs" / "target").exists()


def test_the_second_deployments_script_builds_test_binaries_with_test_keys_and_leaves_builds_without_them(tree):
    root, build = tree
    for run in (1, 2):                                                       # the second run starts where the first ended
        r = build("build_programs_v2.sh")
        assert r.returncode == 0, r.stderr
        got = _built(root / "tests" / "fixtures")
        assert got and all("_v2_" in name for name in got), got                # its binaries carry _v2_; the first's are not its business
        for name, how in got.items():
            assert how.split()[0] == ("knos_oidc" if name.startswith("knos_oidc_") else "knos_pay"), (name, how)
            # a binary named real is the program as it is deployed; every other one is a test build
            assert ("--features testkeys" in how) == ("_real" not in name), (name, how)
        assert {"knos_oidc_v2_test.so", "knos_pay_v2_test.so", "knos_pay_v2_nodevnet.so"} <= set(got)
        assert "--no-default-features" in got["knos_pay_v2_nodevnet.so"] and "--no-default-features" not in got["knos_pay_v2_test.so"]
        # what is left to try on a cluster trusts no test key, and the devnet escrow has its faucet
        assert _built(root / "programs-v2" / "target" / "deploy") == {"knos_oidc.so": "knos_oidc", "knos_pay.so": "knos_pay"}
        # each binary it built is pinned by its hash, once, and the other pins are as they were
        sums = dict(reversed(line.split("  ", 1)) for line in (root / "tests" / "fixtures" / "SHA256SUMS").read_text(encoding="utf-8").splitlines())
        assert sums.pop("docs/other.json") == "0" * 64
        assert sums == {f"tests/fixtures/{name}": hashlib.sha256((root / "tests" / "fixtures" / name).read_bytes()).hexdigest() for name in got}
    assert not (root / "programs" / "target").exists()
