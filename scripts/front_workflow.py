"""Keep the workflow files the site hands out identical to the examples.

    python scripts/front_workflow.py           # rewrite the templates in web/front.js from examples/
    python scripts/front_workflow.py --check   # exit 1 if they differ (the test suite runs this)

web/front.js carries examples/knos-workflow.yml (WORKFLOW) and examples/knos-check.yml (CHECK_WORKFLOW) as template
literals: what a repository installs from the site is the example, byte for byte, with the commit of
drexthealpha/knos-workflows the example names. Until a release the examples carry a placeholder in its place;
`python scripts/pinned_workflows.py stamp <sha>` puts the commit into the examples and writes the page again through
this script, so the site never names a commit the examples do not. A page that declares
`export const KNOS_WORKFLOWS_SHA = "...";` is given the same value: the commit, for any other place the page shows it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "web" / "front.js"
TEMPLATES = {"WORKFLOW": "knos-workflow.yml", "CHECK_WORKFLOW": "knos-check.yml"}
PIN = re.compile(r"drexthealpha/knos-workflows/\.github/workflows/[\w.-]+@(\S+)")


def literal(text: str) -> str:
    """The file as the body of a JavaScript template literal that evaluates to exactly the file."""
    return text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")


def pin(texts: list[str]) -> str:
    """The one commit of drexthealpha/knos-workflows (or the placeholder) every example names."""
    named = {ref for text in texts for ref in PIN.findall(text)}
    if len(named) != 1:
        raise SystemExit(f"the examples must name drexthealpha/knos-workflows at one commit; they name {sorted(named) or 'none'}")
    return named.pop()


def rewritten(js: str) -> str:
    texts = {name: (ROOT / "examples" / example).read_text(encoding="utf-8") for name, example in TEMPLATES.items()}
    for name, text in texts.items():
        pat = re.compile(r"(export const " + name + r" = `).*?(`;\n)", re.DOTALL)
        if not pat.search(js):
            raise SystemExit(f"web/front.js has no `export const {name}` template")
        js = pat.sub(lambda m, body=literal(text): m.group(1) + body + m.group(2), js, count=1)
    commit = pin(list(texts.values()))
    return re.sub(r'(export const KNOS_WORKFLOWS_SHA = ")[^"\n]*(";)', lambda m: m.group(1) + commit + m.group(2), js, count=1)


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    js = FRONT.read_text(encoding="utf-8")
    new = rewritten(js)
    if new == js:
        return 0
    if check:
        print("web/front.js does not match examples/: run python scripts/front_workflow.py")
        return 1
    FRONT.write_text(new, encoding="utf-8", newline="\n")
    print("web/front.js updated from examples/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
