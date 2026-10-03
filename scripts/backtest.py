#!/usr/bin/env python3
"""backtest.py -- merged agent pull requests that said tests pass while a check had failed.

A Knos bounty pays a merged pull request only when every check its terms require passed at the head commit
(src/knos/terms.py). So, of the agent pull requests that said tests or CI pass and were merged anyway, how many had
a failed check at the head commit? A bounty that required that check would not have paid the merge; which checks a
bounty would have required is not in this data, since none of these pull requests had one. Same agents, claim
patterns and CI verdict as the Agent PR Index (agent_pr_ci.py, agent_pr_index.py), and the same two counts under the
same names.

    python scripts/backtest.py                                  # docs/backtest.json from docs/agent_pr_ci.json
    python scripts/backtest.py --index index.json               # and from a published index, where merges are known
    python scripts/backtest.py fetch --index index.json --pulls pulls.json    # read them from GitHub (needs gh)
    python scripts/backtest.py --index index.json --pulls pulls.json          # then: the whole index, with times

What the data in the repository has: docs/agent_pr_ci.json (the 1 Oct 2026 sample) says for each pull request whether
it was merged when it was read, and when it was created; not when it was closed. index.json lists no merge state at
all. `fetch` reads both from GitHub (GET /repos/{repo}/pulls/{number}: merged, created_at, closed_at, merged_at) for
every pull request an index lists, through the same disk cache, and a run that is stopped continues where it was.
The output says, in `cannot_show`, what it could not work out from what it was given.
"""
import argparse
import datetime as dt
import json
import os
import statistics
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_pr_ci  # noqa: E402
import agent_pr_index  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "docs", "agent_pr_ci.json")
OUT = os.path.join(ROOT, "docs", "backtest.json")

DEFINITIONS = {
    "prs": "pull requests by the listed AI coding agents whose description says tests or CI pass, whose CI had "
           "finished at the head commit when it was read, and whose merge state is known",
    "merged": "of those, the ones that had been merged when they were read; the counts under it are over these",
    "any_check_failed": agent_pr_index.DEFINITIONS["any_check_failed"] + ". A Knos bounty whose terms require a check "
                        "that failed does not pay such a merge",
    "test_or_build_check_failed": agent_pr_index.DEFINITIONS["test_or_build_check_failed"],
    "cancelled_no_failure": "no failed check, but one that was cancelled, waits for a maintainer's approval or went "
                            "stale (the index's class `other`). A Knos bounty that requires such a check counts it "
                            "as failed; they are not in any_check_failed",
    "fate": "what had become of the pull requests when they were read, with a failed check and without one: merged, "
            "closed without a merge, or still open",
    "time_open": "hours from a pull request's creation to its merge or close, for those that were closed, with a "
                 "failed check and without one; those still open are counted, not timed",
    "share, ci95": "the count over its total, and the 95% Wilson interval of that share",
}


def _key(r):
    return f"{r['repo']}#{r['number']}".lower()


def _when(text):
    return dt.datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None


def _state(r):
    """merged | closed_unmerged | open, or None when the row does not say."""
    if r.get("merged") is None and not r.get("merged_at"):
        return None
    if r.get("merged") or r.get("merged_at"):
        return "merged"
    state = r.get("state") or r.get("pr_state")
    return "open" if state == "open" else "closed_unmerged" if state == "closed" or r.get("closed_at") else None


def _failed(r):
    return agent_pr_index.FAILED["any_check_failed"](r)


def time_open(rows):
    """Hours from creation to merge or close, with a failed check and without; None when no row says when it closed."""
    def hours(r):
        made, done = _when(r.get("created_at")), _when(r.get("merged_at") or r.get("closed_at"))
        return (done - made).total_seconds() / 3600 if made and done else None

    if not any(hours(r) is not None for r in rows):
        return None
    out = {}
    for name, mine in (("any_check_failed", [r for r in rows if _failed(r)]), ("no_check_failed", [r for r in rows if not _failed(r)])):
        took = sorted(h for h in map(hours, mine) if h is not None)
        cut = statistics.quantiles(took, n=4, method="inclusive") if len(took) > 1 else took * 3
        out[name] = {"prs": len(mine), "closed": len(took), "still_open": sum(1 for r in mine if _state(r) == "open"),
                     "median_hours": round(cut[1], 1) if took else None,
                     "quartile_hours": [round(cut[0], 1), round(cut[2], 1)] if took else None}
    return out


def backtest(rows):
    """rows: classified pull requests with a claim. Those with finished CI and a known merge state are counted."""
    known = [r for r in rows if r.get("class") in agent_pr_index.COMPLETED and _state(r)]
    merged = [r for r in known if _state(r) == "merged"]

    def over(mine):
        k = sum(1 for r in mine if r["class"] == "other")
        return {**agent_pr_index.counts(mine, "prs"),
                "cancelled_no_failure": {"prs": k, "share": round(k / len(mine), 4) if mine else None,
                                         "ci95": agent_pr_index.wilson(k, len(mine))}}

    fate = {}
    for name, mine in (("any_check_failed", [r for r in known if _failed(r)]), ("no_check_failed", [r for r in known if not _failed(r)])):
        fate[name] = {"prs": len(mine), **{s: sum(1 for r in mine if _state(r) == s) for s in ("merged", "closed_unmerged", "open")}}
        fate[name]["merged_share"] = round(fate[name]["merged"] / len(mine), 4) if mine else None
        fate[name]["merged_ci95"] = agent_pr_index.wilson(fate[name]["merged"], len(mine))
    return {"prs": len(known),
            "merged": {"overall": over(merged),
                       "agents": {name: over([r for r in merged if r["agent"] == name]) for name, _ in agent_pr_ci.AGENTS}},
            "fate": fate, "time_open": time_open(known)}


def _author_owned(r):
    return r["repo"].split("/")[0].lower() == str(r.get("author") or "").lower()


def from_sample(sample):
    """The 1 Oct 2026 sample (docs/agent_pr_ci.json): every pull request has `merged` and `pr_state`."""
    rows = sample["prs"]
    created = sorted(r["created_at"][:10] for r in rows)
    done = [r for r in rows if r.get("class") in agent_pr_index.COMPLETED]
    return {"source": "docs/agent_pr_ci.json", "read": sample["generated_utc"][:10], "created": [created[0], created[-1]],
            "claimed_prs": len(rows), "with_finished_ci": len(done), **backtest(rows),
            # the index leaves out repositories the author (or the person who assigned the agent) owns; this sample
            # did not. Who assigned an agent is not in it, so only the author's own repositories can be taken out.
            "without_author_owned_repos": backtest([r for r in rows if not _author_owned(r)])}


def from_index(index, pulls=None, sample=None):
    """A published index. Its records carry no merge state: it comes from `pulls` (backtest.py fetch) or, failing
    that, from the sample for the pull requests that are in both."""
    states = pulls if pulls is not None else {_key(r): {"merged": r["merged"], "state": r["pr_state"], "created_at": r["created_at"]}
                                              for r in (sample or {}).get("prs", [])}
    rows = [{**r, **states[_key(r)]} for r in index["prs"] if _key(r) in states and "error" not in states[_key(r)]]
    return {"source": "index.json", "date": index["date"], "window": index["window"], "root": index["root"],
            "listed_prs": index["n_prs"], "merge_state_known": len(rows),
            "merge_state_from": "GitHub (backtest.py fetch)" if pulls is not None else "docs/agent_pr_ci.json, for the pull requests in both",
            **backtest(rows)}


def cannot_show(out):
    """What this run could not work out, from what it was given. Each line says what is missing."""
    lines = []
    index = out.get("index")
    if out["sample"]["time_open"] is None and not (index and index["time_open"]):
        lines.append("How long pull requests stayed open: docs/agent_pr_ci.json records when each was created and "
                     "whether it was open, closed or merged when it was read, not when it was closed or merged, and "
                     "index.json records neither. `backtest.py fetch` reads those times from GitHub")
    if index and index["merge_state_known"] < index["listed_prs"]:
        lines.append(f"The merges of the whole index: index.json lists no merge state. It is known for "
                     f"{index['merge_state_known']:,} of its {index['listed_prs']:,} pull requests "
                     f"({index['merge_state_from']})")
    if not index:
        lines.append("Anything about the published index: this run was not given one (--index index.json)")
    lines += ["Whether the check was failing at the moment of the merge: CI was read at the head commit on the day "
              "of the scan. A check re-run later shows its last result, and a check can fail after a merge",
              "Why a check failed, or whether the maintainer saw it: a failed check is GitHub's record, not a "
              "judgment. any_check_failed counts deploy previews and label gates; test_or_build_check_failed "
              "is decided from check names",
              "Agent pull requests in general: these are pull requests whose description claims tests or CI pass, the "
              "newest per agent and date window that GitHub's search returned, not a random sample",
              "What a bounty would have changed: none of these pull requests had one, so which checks its terms would "
              "have required is not known. This counts merges with a failed check, not payments refused"]
    return lines


def fetch(index, max_seconds, get=None):
    """Merge state and times of every pull request the index lists, from GitHub: {"repo#number": {...}}. Answers are
    kept on disk (agent_pr_ci's cache); a pull request still open is asked again after a day."""
    agent_pr_ci.ARGS = SimpleNamespace(max_seconds=max_seconds)
    get = get or agent_pr_ci.gh_get
    out, left = {}, 0
    for r in index["prs"]:
        try:
            got = get(f"repos/{r['repo']}/pulls/{r['number']}")
            if got["ok"] and got["json"].get("state") == "open":
                got = get(f"repos/{r['repo']}/pulls/{r['number']}", max_age=86400)
        except agent_pr_ci.OutOfTime:
            left += 1
            continue
        if not got["ok"]:
            if not got.get("transient"):
                out[_key(r)] = {"error": got["error"][:120]}      # deleted, or its repository is gone
            else:
                left += 1
            continue
        pr = got["json"]
        out[_key(r)] = {"merged": bool(pr.get("merged_at")), "state": pr.get("state"), "created_at": pr.get("created_at"),
                        "closed_at": pr.get("closed_at"), "merged_at": pr.get("merged_at")}
    return out, left


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("step", nargs="?", default="build", choices=["build", "fetch"])
    ap.add_argument("--sample", default=SAMPLE)
    ap.add_argument("--index", help="a published index.json (its Merkle root is checked)")
    ap.add_argument("--pulls", help="merge state of the index's pull requests, written by `fetch`")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--max-seconds", type=int, default=3000)
    a = ap.parse_args(argv)
    index = agent_pr_index.check(a.index) if a.index else None
    if a.step == "fetch":
        if not index or not a.pulls:
            ap.error("fetch needs --index index.json and --pulls pulls.json")
        pulls, left = fetch(index, a.max_seconds)
        with open(a.pulls, "w", encoding="utf-8") as f:
            json.dump(pulls, f, ensure_ascii=False)
        print(f"read {len(pulls):,} of {index['n_prs']:,} pull requests"
              + (f"; {left:,} not answered yet: run this again to continue" if left else ""), file=sys.stderr)
        return 1 if left else 0
    sample = _load(a.sample)
    out = {"about": "Of merged pull requests by AI coding agents whose description said tests or CI pass, how many had a "
                    "failed check at the head commit. A Knos bounty whose terms required that check would not have "
                    "paid the merge. Written by scripts/backtest.py.",
           "definitions": DEFINITIONS, "sample": from_sample(sample)}
    if index:
        out["index"] = from_index(index, _load(a.pulls) if a.pulls and os.path.exists(a.pulls) else None, sample)
    out["cannot_show"] = cannot_show(out)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    m = out["sample"]["merged"]["overall"]
    print(f"backtest: {m['prs']} merged pull requests in the sample, {m['any_check_failed']['prs']} with a failed check "
          f"({m['test_or_build_check_failed']['prs']} a test or build check)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
