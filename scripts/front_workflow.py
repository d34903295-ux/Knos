"""Keep the workflow files the site hands out identical to the examples.

    python scripts/front_workflow.py           # rewrite the two templates in web/front.js from examples/
    python scripts/front_workflow.py --check   # exit 1 if they differ (the test suite runs this)

web/front.js carries examples/knos-workflow.yml (WORKFLOW) and examples/knos-check.yml (CHECK_WORKFLOW) as template
literals, with the commit placeholder turned into the constants the Pages build fills in.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "web" / "front.js"
TEMPLATES = {"WORKFLOW": "knos-workflow.yml", "CHECK_WORKFLOW": "knos-check.yml"}


def literal(text: str) -> str:
    """The file as the body of a JavaScript template literal."""
    out = text.replace("\\", "\\\\").replace("`", "\\`").replace("${{", "\\${{")
    out = re.sub(r"(relay\.yml)@KNOS_COMMIT_SHA", r"\1@${KNOS_RELAY_SHA}", out)
    return out.replace("@KNOS_COMMIT_SHA", "@${KNOS_SHA}")


def rewritten(js: str) -> str:
    for name, example in TEMPLATES.items():
        body = literal((ROOT / "examples" / example).read_text(encoding="utf-8"))
        pat = re.compile(r"(export const " + name + r" = `).*?(`;\n)", re.DOTALL)
        if not pat.search(js):
            raise SystemExit(f"web/front.js has no `export const {name}` template")
        js = pat.sub(lambda m, body=body: m.group(1) + body + m.group(2), js, count=1)
    return js


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
