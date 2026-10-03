"""The workflows a repository calls, as drexthealpha/knos-workflows publishes them, and the one commit of that
repository that everything here names.

    python scripts/pinned_workflows.py build DIR                    write the published set into DIR
    python scripts/pinned_workflows.py build DIR --source REQ       a rehearsal variant: knos installed from REQ
    python scripts/pinned_workflows.py stamp SHA                    name the published commit everywhere it is named
    python scripts/pinned_workflows.py check [DIR [--source REQ]]   exit 1 unless all of it is consistent

The source of truth is this repository: .github/workflows/fund.yml, prove.yml and check.yml, where they are tested.
A repository names them by a commit of drexthealpha/knos-workflows, and a bounty records that commit on chain. That
repository holds copies and nothing else: the three files, a short README and the LICENSE.

At a release: `build` into a checkout of drexthealpha/knos-workflows and commit it there; `stamp` that commit's sha
here; `check <the checkout>`. `stamp` replaces the placeholder KNOS_WORKFLOWS_SHA (or the commit named before) in
examples/, in Knos's own knos.yml and knos-check.yml, in README.md and docs/, and regenerates the site's templates
(scripts/front_workflow.py writes web/front.js from the examples). `check` says every place that disagrees.

The published files install knos as the exact PyPI release they name. Before that release exists nothing can install
it, so a staging repository rehearses with --source: REQ is one requirement uv can install, such as
"git+https://github.com/drexthealpha/Knos@<commit>" or the URL of a wheel. The variant's workflows differ from the
published ones in that requirement and in nothing else; its README says what it is. It is for rehearsal only: a
bounty funded through it is pinned to the staging repository's commit, not to the published one.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "drexthealpha/knos-workflows"
WORKFLOWS = ("fund.yml", "prove.yml", "check.yml")
PLACEHOLDER = "KNOS_WORKFLOWS_SHA"                  # stands for the published commit until `stamp` names it
OWN = {"knos-workflow.yml": "knos.yml", "knos-check.yml": "knos-check.yml"}     # an example, and Knos's own copy of it
CLAIM_COPY = "src/knos/settle/knos-claim.yml"                                   # what `knos claim` commits: the example
# the install step's one line that names what is installed, the same in every job
LINE = re.compile(r'^(?P<lead> +run: uv tool install [^"\n]*)"knos==(?P<release>\d+\.\d+\.\d+)"$', re.M)
REQ = re.compile(r"[A-Za-z0-9][A-Za-z0-9 @+:/._=<>~!,\[\]-]*")      # one requirement; nothing a shell or YAML would read
NAMED = re.compile(re.escape(REPO) + r"/\.github/workflows/([\w.-]+)@(\w+)")


def _front():
    """scripts/front_workflow.py, which holds the site's templates and knows the commit the examples name."""
    spec = importlib.util.spec_from_file_location("front_workflow", Path(__file__).with_name("front_workflow.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT, mod.FRONT = ROOT, ROOT / "web" / "front.js"       # the same tree as this script works on
    return mod


def sources() -> dict[str, str]:
    return {name: (ROOT / ".github" / "workflows" / name).read_bytes().decode("utf-8") for name in WORKFLOWS}


def release(texts: dict[str, str]) -> str:
    """The one knos release every install line names."""
    named = {m.group("release") for text in texts.values() for m in LINE.finditer(text)}
    lines = sum(len(re.findall(r"^ +run: uv tool install ", text, re.M)) for text in texts.values())
    if len(named) != 1 or lines != sum(len(LINE.findall(text)) for text in texts.values()):
        raise SystemExit(f"the install lines of {', '.join(WORKFLOWS)} must all name one release as \"knos==X.Y.Z\"; "
                         f"they name {sorted(named) or 'none'}")
    return named.pop()


def readme(version: str, spec: str | None) -> str:
    note = "" if spec is None else (
        "> **REHEARSAL VARIANT.** These are not the published workflows: knos is installed from\n"
        f"> `{spec}`\n"
        "> instead of the PyPI release. For a staging repository only: a bounty funded through these files is pinned\n"
        "> to this repository's commit, not to the published one.\n\n")
    installs = f"knos {version} from PyPI, as exactly that release" if spec is None else "knos from the rehearsal source named above"
    return f"""# knos-workflows

{note}The workflows that [Knos](https://github.com/drexthealpha/Knos) bounties pin by commit. A comment funds an issue,
and the pull request that closes it is paid from an escrow on Solana when GitHub itself signs the facts. A bounty on
chain names this repository and one commit of it: the commit its `fund.yml` ran at. Only `prove.yml` of this
repository at that same commit can pay it.

| File | Called for | Runs |
|---|---|---|
| `.github/workflows/fund.yml` | a `/knos` comment, or a new issue that funds itself | `knos command` |
| `.github/workflows/prove.yml` | a merge, a run started by hand, `/knos settle`, and the review after the check | `knos settle`, `knos review`, `knos proof judge` |
| `.github/workflows/check.yml` | every pull request (optional, read-only) | `knos check` |

Call them by a full commit sha, never by a branch or a tag. The workflows take no inputs, and every job installs
{installs}, so a caller cannot change which code judges.

The files to copy into your repository (`knos.yml`, and optionally `knos-check.yml`) are in
[drexthealpha/Knos/examples](https://github.com/drexthealpha/Knos/tree/main/examples). Their comments say what each
trigger does and what the file can and cannot do in your repository.

Nothing is edited here. These files are copied from `.github/workflows/` in drexthealpha/Knos at a release, where they
are tested. In a checkout of that repository, `python scripts/pinned_workflows.py check <a checkout of this one>`
shows that this copy is the same, byte for byte.
"""


def rehearsal(text: str, spec: str) -> str:
    """The workflow with knos installed from `spec`: the requirement, and nothing else, changed."""
    return LINE.sub(lambda m: f'{m.group("lead")}"{spec}"', text)


def published(spec: str | None = None) -> dict[str, bytes]:
    """Every file of the published set, by its path in drexthealpha/knos-workflows."""
    texts = sources()
    version = release(texts)
    if spec is not None:
        if not REQ.fullmatch(spec) or ": " in spec or spec != spec.rstrip(" :"):
            raise SystemExit("--source takes one requirement, such as git+https://github.com/drexthealpha/Knos@<commit> "
                             "or a wheel's URL: letters, digits and @+:/._=<>~!,[]- only")
        texts = {name: rehearsal(text, spec) for name, text in texts.items()}
    files = {f".github/workflows/{name}": text.encode("utf-8") for name, text in texts.items()}
    files["README.md"] = readme(version, spec).encode("utf-8")
    files["LICENSE"] = (ROOT / "LICENSE").read_bytes()
    return files


def differences(folder: Path, want: dict[str, bytes]) -> list[str]:
    """Why `folder` is not the published set, one line each: a file missing, changed, or not part of the set."""
    said = []
    for rel, data in want.items():
        path = folder / rel
        if not path.is_file():
            said.append(f"{rel} is missing")
        elif path.read_bytes() != data:
            said.append(f"{rel} is not the file this repository publishes")
    have = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and ".git" not in p.relative_to(folder).parts}
    said += [f"{rel} is not part of the published set" for rel in sorted(have - set(want))]
    return said


# ---- the one commit everything names ---------------------------------------------------------------------------------

def _text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):       # a picture in docs/ names no commit
        return None


def naming() -> list[Path]:
    """Every file that names the published commit as plain text: the examples, Knos's own copies of them, and the
    documents. (The site's templates are written from the examples by scripts/front_workflow.py.)"""
    docs = [p for p in sorted((ROOT / "docs").rglob("*")) if p.is_file()] + [ROOT / "README.md"]
    return [*sorted((ROOT / "examples").glob("*.yml")), *((ROOT / ".github" / "workflows" / own) for own in OWN.values()), *docs]


def pin() -> str:
    """The commit (or the placeholder) the examples name."""
    return _front().pin([(ROOT / "examples" / example).read_text(encoding="utf-8") for example in OWN])


def stamp(sha: str) -> list[str]:
    """Put the published commit wherever the placeholder, or the commit named before, stands. Returns what changed."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit("stamp takes the full sha of a commit of drexthealpha/knos-workflows: 40 hex digits")
    front, old, changed = _front(), pin(), []
    for path in naming():
        text = _text(path)
        if text is not None and text.replace(old, sha) != text:
            path.write_text(text.replace(old, sha), encoding="utf-8", newline="\n")
            changed.append(path.relative_to(ROOT).as_posix())
    js = front.FRONT.read_text(encoding="utf-8")
    new = front.rewritten(js)                   # the site's templates, from the examples as they now are
    if new != js:
        front.FRONT.write_text(new, encoding="utf-8", newline="\n")
        changed.append(front.FRONT.relative_to(ROOT).as_posix())
    return changed


def inconsistencies() -> list[str]:
    """Every place that disagrees, one line each: a copy that is not its example, a file that names another commit or
    a workflow that is not published, a placeholder left behind, site templates that are not the examples."""
    said, front = [], _front()
    for example, own in OWN.items():
        if (ROOT / "examples" / example).read_bytes() != (ROOT / ".github" / "workflows" / own).read_bytes():
            said.append(f".github/workflows/{own} is not examples/{example}: copy the example over it")
    if (ROOT / CLAIM_COPY).read_bytes() != (ROOT / "examples" / "knos-claim.yml").read_bytes():
        said.append(f"{CLAIM_COPY} is not examples/knos-claim.yml: copy the example over it")
    try:
        release(sources())
        named = pin()
    except SystemExit as why:
        return [*said, str(why)]
    if named != PLACEHOLDER and not re.fullmatch(r"[0-9a-f]{40}", named):
        said.append(f"the examples name {REPO} at {named}: a full commit sha, or {PLACEHOLDER} until the release")
    for path in [*naming(), front.FRONT]:
        text, rel = _text(path), path.relative_to(ROOT).as_posix()
        for name, ref in sorted(set(NAMED.findall(text or ""))):
            if name not in WORKFLOWS:
                said.append(f"{rel} names {name}, which {REPO} does not publish")
            elif ref != named:
                said.append(f"{rel} names {REPO} at {ref}; the examples name {named}")
        if text and named != PLACEHOLDER and path != front.FRONT and PLACEHOLDER in text:
            said.append(f"{rel} still says {PLACEHOLDER}: run python scripts/pinned_workflows.py stamp {named}")
    js = front.FRONT.read_text(encoding="utf-8")
    try:
        if front.rewritten(js) != js:
            said.append("web/front.js does not hand out examples/: run python scripts/front_workflow.py")
    except SystemExit as why:
        said.append(str(why))
    return said


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=f"Write, stamp and check the workflows {REPO} publishes.")
    sub = ap.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="write the published set into a directory")
    build.add_argument("dir", help=f"a checkout of {REPO}, or any directory")
    build.add_argument("--source", metavar="REQ", help="rehearsal variant: install knos from REQ instead of the PyPI release")
    sub.add_parser("stamp", help="name the published commit in the examples, Knos's own files, the site and the documents"
                   ).add_argument("sha", help=f"the commit of {REPO} that holds the published set")
    check = sub.add_parser("check", help="exit 1 unless every file names one commit and the copies are their sources")
    check.add_argument("dir", nargs="?", help=f"also compare this checkout of {REPO} with the published set")
    check.add_argument("--source", metavar="REQ", help="the checkout holds the rehearsal variant made with this REQ")
    a = ap.parse_args(argv)
    if a.command == "stamp":
        changed = stamp(a.sha)
        print(f"Named {REPO}@{a.sha} in {len(changed)} file(s)" + (": " + ", ".join(changed) if changed else "") + ".")
        return 0
    if a.command == "check":
        if a.source and not a.dir:
            ap.error("--source goes with the checkout to compare")
        said = inconsistencies()
        what = "the rehearsal variant" if a.source else "the published set"
        if a.dir:
            wrong = differences(Path(a.dir), published(a.source))
            said += [f"{a.dir}: {line}" for line in wrong]
            if wrong:
                again = f"python scripts/pinned_workflows.py build {a.dir}" + (f' --source "{a.source}"' if a.source else "")
                said.append(f"{a.dir} is not {what}. Write it again: {again}")
        for line in said:
            print(line)
        if not said:
            print(f"Consistent: every file names {REPO} at {pin()}" + (f", and {a.dir} is {what}, byte for byte." if a.dir else "."))
        return 1 if said else 0
    want = published(a.source)
    for rel, data in want.items():
        path = Path(a.dir) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"Wrote {'the rehearsal variant' if a.source else 'the published set'} ({len(want)} files) into {a.dir}.")
    if a.source:
        print("REHEARSAL VARIANT: for a staging repository only, never the commit a real bounty should record.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
