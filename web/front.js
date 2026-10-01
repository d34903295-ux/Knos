// Front door: paste an agent PR, see in seconds whether its "tests pass" claim is true -- from GitHub's own CI at the
// PR's head commit, read in the browser with the public GitHub REST API (no login, no install). The claim regexes are
// ported from scripts/agent_pr_ci.py (the Agent PR Index uses the same ones), so the page and the index agree.

const $ = (id) => document.getElementById(id);
const API = "https://api.github.com";
const KNOS_REF = "drexthealpha/Knos/.github/workflows/prove.yml@v0.3.7";

// ---- claim detection (port of scripts/agent_pr_ci.py) -----------------------------------------------------------
const PASS = String.raw`(?:pass(?:es|ed|ing)?|green)`;
const CLAIM_RE = new RegExp(
  String.raw`(?:\ball\s+(?:\w+\s+){0,2}tests?\s+(?:are\s+|now\s+)*` + PASS +
  String.raw`|\btests?\s+(?:are\s+|now\s+|still\s+)*` + PASS + String.raw`\b` +
  String.raw`|\btests?\b[^\n.]{0,40}?\band\s+passing\b` +
  String.raw`|\bCI\b(?:[\s:/,]+(?:is|are|now|run|runs|job|jobs|checks?|build|pipeline|workflows?|all|CD|#?\d+))*[\s:,]+` +
  PASS + String.raw`\b` +
  String.raw`|\b(?:all\s+(?:CI\s+)?checks?|(?:CI\s+)?checks)\s+(?:are\s+|have\s+)?` + PASS + String.raw`\b` +
  String.raw`|\bpass(?:es|ed|ing)?\s+all\s+(?:\w+\s+){0,2}(?:tests|checks|CI)\b` +
  String.raw`|` + "`" + String.raw`[^` + "`" + String.raw`\n]*test[^` + "`" + String.raw`\n]*` + "`" +
  String.raw`\s*(?:[-:—]\s*)?(?:all\s+)?` + PASS + String.raw`\b` +
  String.raw`|\b\d[\d,]*\s*(?:/\s*\d[\d,]*\s*)?(?:\w+\s+){0,2}passed\b` +
  String.raw`|✅\s*[^\n]{0,40}?\btests?\b` +
  String.raw`|\btests?\b[^\n]{0,30}?✅)`, "i");
const NONCLAIM_RE = new RegExp(
  String.raw`- \[ \]|\b(ensure|make sure|verify that|should|would|will|to confirm|until|` +
  String.raw`once|if|before|whether|need|needs|must|expect|expected|todo|not|` +
  String.raw`fail|fails|failing|failed|failure|failures|errors?|except|unless|pending|flaky|skip|` +
  String.raw`red|broken)\b|n't\b`, "i");
const BOILER_RE = new RegExp(String.raw`\*\*Your PR cannot be merged unless tests pass\*\*|` +
  String.raw`\bfail[- ](?:closed|safe|fast|open)\b|\b0 failed\b`, "gi");

function stripBody(body) {
  return (body || "").replace(/<!--[\s\S]*?-->/g, " ").replace(
    /<details>\s*<summary>[^<]*(original prompt|original issue)[^<]*<\/summary>[\s\S]*?<\/details>/gi, " ");
}

export function findClaim(body) {
  for (const line of stripBody(body).split(/\r?\n/)) {
    const m = CLAIM_RE.exec(line);
    if (!m || NONCLAIM_RE.test(line.replace(BOILER_RE, " "))) continue;
    return { phrase: m[0].trim(), line: line.trim().slice(0, 200) };
  }
  return null;
}

// check runs that are the agent's own session, not project CI (same list as agent_pr_ci.py)
const AGENT_RUN_RE = /^(copilot|claude|claude[-_ ]?(code|review|code[-_ ]review|pr[-_ ]review)|codex|devin)$/i;
const FAIL = new Set(["failure", "timed_out", "startup_failure"]);
const OK = new Set(["success", "neutral", "skipped"]);

export function ciVerdict(runs, statuses) {
  const ci = runs.filter((x) => !AGENT_RUN_RE.test(x.name.trim()));
  const failed = ci.filter((x) => FAIL.has(x.conclusion)).map((x) => x.name)
    .concat(statuses.filter((s) => s.state === "failure" || s.state === "error").map((s) => s.context));
  if (failed.length) return { cls: "failed", failed };
  if (!ci.length && !statuses.length) return { cls: "no-ci", failed };
  if (ci.some((x) => x.status !== "completed") || statuses.some((s) => s.state === "pending")) return { cls: "pending", failed };
  if (ci.every((x) => OK.has(x.conclusion)) && statuses.every((s) => s.state === "success")) return { cls: "passed", failed };
  return { cls: "other", failed };
}

export function agentOf(pr) {
  const login = (pr.user?.login || "").toLowerCase(), body = pr.body || "";
  if (login.startsWith("copilot")) return "copilot";
  if (login.startsWith("devin-ai-integration")) return "devin";
  if (login === "claude[bot]" || login === "claude") return "claude-bot";
  if (/Generated with Claude Code/.test(body)) return "claude-code";
  if (/chatgpt\.com\/codex\/tasks/.test(body)) return "codex";
  return null;
}

// ---- GitHub, unauthenticated ------------------------------------------------------------------------------------
class RateLimited extends Error {}

async function gh(path) {
  const r = await fetch(API + path, { headers: { Accept: "application/vnd.github+json" } });
  if ((r.status === 403 || r.status === 429) && r.headers.get("x-ratelimit-remaining") === "0") {
    const reset = Number(r.headers.get("x-ratelimit-reset")) * 1000;
    throw new RateLimited(reset ? new Date(reset).toLocaleTimeString() : "");
  }
  if (r.status === 404) throw new Error("not found (private repo, or no such PR)");
  if (!r.ok) throw new Error(`GitHub said ${r.status}`);
  return r.json();
}

let indexP = null;
const loadIndex = () => (indexP ??= fetch("index.json").then((r) => (r.ok ? r.json() : null)).catch(() => null));

export function parsePr(s) {
  const m = /github\.com\/([\w.-]+)\/([\w.-]+)\/pull\/(\d+)/i.exec(s || "") || /^([\w.-]+)\/([\w.-]+)#(\d+)$/.exec((s || "").trim());
  return m ? { owner: m[1], repo: m[2], number: Number(m[3]) } : null;
}

export function protectUrl(owner, repo, branch) {
  const wf = `name: knos
# Knos: an agent's "tests pass" only counts when GitHub's own signature, checked by Solana, proves it.
on:
  pull_request:
permissions:
  contents: read
  id-token: write
jobs:
  prove:
    uses: ${KNOS_REF}
    with:
      job: \${{ vars.KNOS_JOB || '' }}
`;
  return `https://github.com/${owner}/${repo}/new/${encodeURIComponent(branch)}?filename=.github/workflows/knos.yml&value=${encodeURIComponent(wf)}`;
}

// ---- UI ------------------------------------------------------------------------------------------------------------
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const CI_TEXT = { passed: "passed", failed: "FAILED", pending: "still running", "no-ci": "no CI ran", other: "no clear result (cancelled or waiting)" };

function agentRecord(index, agent) {
  if (!index) return `<p class="fine">Agent PR Index not loaded here (it is built with the site).</p>`;
  const a = agent && index.agents?.[agent];
  if (!a) return `<p class="fine">No record for ${agent ? esc(agent) : "this author"} in the Agent PR Index (${esc(index.date || "")}).</p>`;
  const pct = a.share == null ? "n/a" : `${Math.round(a.share * 100)}%`;
  return `<p><strong>${esc(agent)}</strong> in the Agent PR Index (${esc(index.date)}): claimed tests pass on
    <strong>${a.claimed_green}</strong> PRs with CI; CI actually failed on <strong>${a.actually_failed}</strong>
    (<strong>${pct}</strong>).</p>`;
}

async function check(ev) {
  ev?.preventDefault();
  const out = $("pr-result"), ref = parsePr($("pr-url").value);
  if (!ref) { out.innerHTML = `<p class="status bad">Paste a PR link like https://github.com/owner/repo/pull/123</p>`; return; }
  out.innerHTML = `<p class="status">Reading GitHub…</p>`;
  const base = `/repos/${ref.owner}/${ref.repo}`;
  try {
    const pr = await gh(`${base}/pulls/${ref.number}`);
    const sha = pr.head.sha;
    const [cr, st, index] = await Promise.all([gh(`${base}/commits/${sha}/check-runs?per_page=100`),
      gh(`${base}/commits/${sha}/status`), loadIndex()]);
    const claim = findClaim(pr.body), ci = ciVerdict(cr.check_runs || [], st.statuses || []), agent = agentOf(pr);
    let verdict, cls;
    if (!claim) { verdict = "No tests-pass claim in the PR description"; cls = ""; }
    else if (ci.cls === "failed") { verdict = "claim FALSE"; cls = "bad"; }
    else if (ci.cls === "passed") { verdict = "claim true"; cls = "ok"; }
    else { verdict = "claim unproven"; cls = ""; }
    const repo = pr.base.repo;
    out.innerHTML = `
      <p id="verdict" class="verdict ${cls}" data-verdict="${esc(verdict)}">${esc(verdict)}</p>
      <dl class="facts">
        <dt>PR</dt><dd><a href="${esc(pr.html_url)}">${esc(repo.full_name)}#${pr.number}</a> by ${esc(pr.user.login)}${agent ? ` (${esc(agent)})` : ""}</dd>
        <dt>Claims</dt><dd>${claim ? `“${esc(claim.line)}”` : "nothing about tests passing"}</dd>
        <dt>CI at <code>${esc(sha.slice(0, 7))}</code></dt><dd>${esc(CI_TEXT[ci.cls])}${ci.failed.length
          ? `: ${ci.failed.slice(0, 8).map(esc).join(", ")}` : ""}</dd>
      </dl>
      ${agentRecord(index, agent)}
      <a class="button" id="protect" href="${esc(protectUrl(repo.owner.login, repo.name, repo.default_branch))}" target="_blank" rel="noopener">Protect ${esc(repo.full_name)}</a>
      <p class="fine">Adds one workflow file; GitHub asks you to commit it. From then on an agent is paid only when
        GitHub's own signature, checked by Solana, proves its PR passed. 2.5% fee, only on proven work.</p>`;
  } catch (e) {
    out.innerHTML = e instanceof RateLimited
      ? `<p class="status bad">GitHub's free limit for this network is used up (60 reads an hour without login).
         Try again after ${esc(e.message || "an hour")}, or open the PR's
         <a href="https://github.com/${esc(ref.owner)}/${esc(ref.repo)}/pull/${ref.number}/checks">checks on GitHub</a>.</p>`
      : `<p class="status bad">Could not read that PR: ${esc(e.message)}</p>`;
  }
}

if (typeof document !== "undefined" && $("pr-form")) {
  $("pr-form").addEventListener("submit", check);
  $("pr-url").addEventListener("paste", () => setTimeout(() => check(), 0));
  loadIndex();
}
