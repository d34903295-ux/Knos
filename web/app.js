// The Knos web app beyond the front door (front.js): fund an issue, read an escrow, see what is waiting for a GitHub
// account, claim it, the public numbers. Everything is read in the browser from the public GitHub API and Solana
// devnet; the only writes are links to GitHub (where GitHub asks you to confirm) and, for a sponsor, one transaction
// your own wallet signs. No Knos server exists.
import * as knos from "./settle.js";
import { KNOS_SHA } from "./front.js";

const $ = (id) => document.getElementById(id);
const RPC = "https://api.devnet.solana.com";
const GH = "https://api.github.com";
const EXPLORER = (kind, id) => `https://explorer.solana.com/${kind}/${id}?cluster=devnet`;
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const usdc = (units) => (units / 1e6).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const say = (el, html, kind = "") => { el.innerHTML = `<p class="status ${kind}">${html}</p>`; };

// ---- theme and routing ---------------------------------------------------------------------------------------------
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("knos-theme", t); } catch { /* private mode */ }
}
try { const t = localStorage.getItem("knos-theme"); if (t) setTheme(t); } catch { /* private mode */ }
$("theme").onclick = () => setTheme((document.documentElement.dataset.theme
  || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark" ? "light" : "dark");

const VIEWS = ["check", "bounty", "claim", "network", "build"];
function route() {
  const [name, arg] = location.hash.replace(/^#/, "").split("=");
  const view = VIEWS.includes(name) ? name : "check";
  for (const v of VIEWS) $(`view-${v}`).hidden = v !== view;
  for (const a of document.querySelectorAll("nav a")) a.toggleAttribute("aria-current", a.getAttribute("href") === `#${view}`);
  if (view === "network") loadNetwork();
  if (view === "bounty" && arg) { $("st-issue").value = decodeURIComponent(arg); readEscrow(); }
  if (view === "claim" && arg) { $("due-login").value = decodeURIComponent(arg); readDue(); }
}
addEventListener("hashchange", route);

// ---- the deployed programs -----------------------------------------------------------------------------------------
let idsP;
const ids = () => (idsP ||= fetch("program_ids.json").then((r) => r.json()));
const client = async () => knos.client(await ids());

async function gh(path) {
  const r = await fetch(GH + path, { headers: { Accept: "application/vnd.github+json" } });
  if ((r.status === 403 || r.status === 429) && r.headers.get("x-ratelimit-remaining") === "0") {
    throw new Error("GitHub's free limit for this network is used up (60 reads an hour without login). Try again in an hour.");
  }
  if (r.status === 404) throw new Error("GitHub has no such public repository, issue or user.");
  if (!r.ok) throw new Error(`GitHub said ${r.status}`);
  return r.json();
}

export function parseIssue(s) {
  const m = /^(?:https:\/\/github\.com\/)?([\w.-]+)\/([\w.-]+?)(?:#|\/issues\/)(\d+)\/?$/.exec((s || "").trim());
  return m ? { owner: m[1], repo: m[2], number: Number(m[3]) } : null;
}
export function parseRepo(s) {
  const m = /^(?:https:\/\/github\.com\/)?([\w.-]+)\/([\w.-]+?)(?:\.git)?\/?$/.exec((s || "").trim());
  return m ? { owner: m[1], repo: m[2] } : null;
}

// ---- fund: a new issue whose description carries the command -------------------------------------------------------
export function newIssueUrl(owner, repo, title, amount) {
  const body = `<!-- describe what done looks like; whoever's pull request is merged for this issue is paid -->\n\n\n/knos bounty ${amount}`;
  return `https://github.com/${owner}/${repo}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
}

$("new-issue-form").onsubmit = (ev) => {
  ev.preventDefault();
  const ref = parseRepo($("ni-repo").value), amount = Number($("ni-amount").value);
  if (!ref) return say($("ni-result"), "Enter the repository as owner/repo.", "bad");
  if (!(amount >= 1 && amount <= 100)) return say($("ni-result"), "On devnet a bounty is 1 to 100 test USDC.", "bad");
  const url = newIssueUrl(ref.owner, ref.repo, $("ni-title").value.trim(), amount);
  $("ni-result").innerHTML = `<a class="button" id="ni-open" href="${esc(url)}" target="_blank" rel="noopener">Open it on ${esc(ref.owner)}/${esc(ref.repo)}</a>
    <p class="fine">GitHub shows the issue with <code>/knos bounty ${esc(amount)}</code> on its last line: submit it. The repo
      must already run Knos (<a href="#check">protect it first</a>), and you need write access to it.</p>`;
};

// ---- read an issue's escrow ----------------------------------------------------------------------------------------
function jobHtml(address, job, now, author) {
  const left = Math.max(0, job.deadline - now), hours = Math.floor(left / 3600);
  const held = job.review ? `, held ${job.review >= 3600 ? `${Math.round(job.review / 3600)} h` : `${job.review} s`} first` : "";
  const how = (job.mode === knos.MERGE ? "paid when a maintainer merges the pull request that closes the issue"
    : "paid when its acceptance checks pass") + held;
  let state;
  if (job.state === "open") state = `<strong class="status ok">open</strong>, ${how}`;
  else {
    const wait = Math.max(0, job.payAfter - now);
    state = `<strong>proven</strong> for ${author ? esc(author) : `GitHub user ${job.authorId}`}; released in `
      + `${wait >= 3600 ? `${Math.ceil(wait / 3600)} h` : `${wait} s`} unless a maintainer vetoes`;
  }
  return `<dl class="facts">
    <dt>In escrow</dt><dd><strong>${usdc(job.amount)} USDC</strong> (fee when paid: ${usdc(knos.feeOf(job.amount))})</dd>
    <dt>State</dt><dd>${state}</dd>
    <dt>Funded by</dt><dd>${job.tokenFunded ? "the repository, with a GitHub-signed comment" : `wallet <span class="mono">${esc(job.funder)}</span>`}</dd>
    <dt>If unproven</dt><dd>refunded in ${hours} h</dd>
    <dt>Pinned workflow</dt><dd class="mono">${esc(job.wfSha)}</dd>
    <dt>Account</dt><dd><a class="mono" href="${EXPLORER("address", address)}" target="_blank" rel="noopener">${esc(address)}</a></dd>
  </dl>`;
}

async function readEscrow(ev) {
  ev?.preventDefault();
  const out = $("st-result"), ref = parseIssue($("st-issue").value);
  if (!ref) return say(out, "Enter the issue as owner/repo#7.", "bad");
  say(out, "Reading GitHub and Solana…");
  try {
    const [repo, k, slot] = await Promise.all([gh(`/repos/${ref.owner}/${ref.repo}`), client(), knos.rpc(RPC, "getSlot", [{ commitment: "confirmed" }])]);
    const now = (await knos.rpc(RPC, "getBlockTime", [slot])) || Math.floor(Date.now() / 1000);
    const key = new Uint8Array(16);
    new DataView(key.buffer).setBigUint64(0, BigInt(repo.id), true);
    new DataView(key.buffer).setBigUint64(8, BigInt(ref.number), true);
    const found = await knos.programAccounts(RPC, k.ids.knos_pay, knos.JOB_LEN, 8, key);
    if (!found.length) {
      out.innerHTML = `<p class="status">Nothing is in escrow for ${esc(ref.owner)}/${esc(ref.repo)}#${ref.number}.</p>
        <p class="fine">A maintainer funds it by commenting <code>/knos bounty 20</code> on the issue.</p>`;
      return;
    }
    const parts = [];
    for (const f of found) {
      const job = knos.parseJob(f.data);
      let author = null;
      if (job.state === "proven") author = await gh(`/user/${job.authorId}`).then((u) => u.login).catch(() => null);
      parts.push(jobHtml(f.address, job, now, author));
    }
    out.innerHTML = parts.join("<hr>");
  } catch (e) { say(out, esc(e.message), "bad"); }
}
$("status-form").onsubmit = readEscrow;

// ---- a sponsor adds a bounty from a wallet -------------------------------------------------------------------------
async function wallet() {
  const { getWallets } = await import("https://cdn.jsdelivr.net/npm/@wallet-standard/app@1.1.0/+esm");
  const list = getWallets().get().filter((w) => w.chains.some((c) => c.startsWith("solana:")) && w.features["standard:connect"]
    && w.features["solana:signAndSendTransaction"]);
  if (!list.length) throw new Error("No Solana wallet found in this browser. Install Phantom, Solflare or Backpack and set it to devnet.");
  const w = list[0];
  const { accounts } = await w.features["standard:connect"].connect();
  if (!accounts.length) throw new Error("The wallet shared no account.");
  return { w, account: accounts[0] };
}

$("sp-go").onclick = async () => {
  const st = $("sp-status"), ref = parseIssue($("sp-issue").value), units = Math.round(Number($("sp-amount").value) * 1e6);
  const tell = (text, kind = "") => { st.textContent = text; st.className = `status ${kind}`; };
  if (!ref) return tell("Enter the issue as owner/repo#7.", "bad");
  if (!(units >= knos.MIN_AMOUNT && units <= knos.MAX_AMOUNT)) return tell("A bounty is 1 to 500 USDC.", "bad");
  try {
    tell("Checking the repo runs Knos…");
    const repo = await gh(`/repos/${ref.owner}/${ref.repo}`);
    const wf = await fetch(`https://raw.githubusercontent.com/${ref.owner}/${ref.repo}/${repo.default_branch}/.github/workflows/knos.yml`)
      .then((r) => (r.ok ? r.text() : ""));
    const pin = /drexthealpha\/Knos\/\.github\/workflows\/prove\.yml@([0-9a-f]{40})/.exec(wf);
    if (!pin) throw new Error("That repo does not run Knos's workflow yet, so nothing could ever pay this bounty. Ask its maintainer to protect it first.");
    const { w, account } = await wallet();
    const me = account.address, k = await client(), mint = knos.USDC_DEVNET;
    const mine = await knos.ata(me, mint);
    const bal = await knos.rpc(RPC, "getTokenAccountBalance", [mine, { commitment: "confirmed" }]).then((r) => Number(r.value.amount)).catch(() => 0);
    if (bal < units) throw new Error(`This wallet has ${usdc(bal)} devnet USDC; the bounty needs ${usdc(units)}. Circle's faucet gives 10 at a time.`);
    const ix = await k.fundIx({ funder: me, funderToken: mine, mint, repoId: repo.id, issue: ref.number, amount: units,
      wfRepo: "drexthealpha/Knos", wfSha: pin[1], reviewS: 3600 });
    const { blockhash } = (await knos.rpc(RPC, "getLatestBlockhash", [{ commitment: "confirmed" }])).value;
    tell("Approve the transaction in your wallet…");
    const [res] = await w.features["solana:signAndSendTransaction"].signAndSendTransaction({ account, chain: "solana:devnet",
      transaction: knos.serializeTx([ix], me, blockhash) });
    const sig = knos.b58(res.signature);
    st.className = "status ok";
    st.innerHTML = `Funded. <a href="${EXPLORER("tx", sig)}" target="_blank" rel="noopener">Transaction</a> ·
      <a href="#bounty=${encodeURIComponent(`${ref.owner}/${ref.repo}#${ref.number}`)}">see the escrow</a>`;
  } catch (e) { tell(e.message, "bad"); }
};

// ---- what is waiting for a GitHub account --------------------------------------------------------------------------
async function readDue(ev) {
  ev?.preventDefault();
  const out = $("due-result"), login = $("due-login").value.trim().replace(/^@/, "");
  if (!/^[\w-]+(\[bot\])?$/.test(login)) return say(out, "Enter a GitHub login.", "bad");
  say(out, "Reading GitHub and Solana…");
  try {
    const [user, k] = await Promise.all([gh(`/users/${login}`), client()]);
    const key = new Uint8Array(8);
    new DataView(key.buffer).setBigUint64(0, BigInt(user.id), true);
    const [dues, repRaw, faucet] = await Promise.all([knos.programAccounts(RPC, k.ids.knos_pay, knos.DUE_LEN, 8, key),
      k.rep(user.id).then((a) => knos.account(RPC, a)), k.faucetMint()]);
    const waiting = dues.map((d) => knos.parseDue(d.data)).filter((d) => d && d.amount > 0);
    const rep = knos.parseRep(repRaw);
    const name = (mint) => (mint === faucet ? "test USDC" : mint === knos.USDC_DEVNET ? "devnet USDC" : `of mint ${mint}`);
    const lines = waiting.length
      ? waiting.map((d) => `<p class="verdict ok">${usdc(d.amount)} ${esc(name(d.mint))} is waiting for ${esc(user.login)}.</p>`).join("")
      : `<p class="status">Nothing is waiting for ${esc(user.login)} right now.</p>`;
    out.innerHTML = `${lines}<p class="fine">GitHub account id ${user.id}. Paid for <strong>${rep.paidJobs}</strong> merged or proven
      pull request${rep.paidJobs === 1 ? "" : "s"} in ${rep.repositories} repositor${rep.repositories === 1 ? "y" : "ies"},
      ${usdc(rep.totalPaid)} in all. This record is on chain and nobody can buy it: only a paid proof adds to it.</p>`;
    if (!$("claim-repo").value) $("claim-repo").placeholder = `${user.login}/any-repo-you-own`;
  } catch (e) { say(out, esc(e.message), "bad"); }
}
$("due-form").onsubmit = readDue;

// ---- claim: the workflow file, prefilled on GitHub -----------------------------------------------------------------
let claimP;
const claimWorkflow = () => (claimP ||= fetch("knos-claim.yml").then((r) => { if (!r.ok) throw new Error("claim workflow not found"); return r.text(); }));

$("claim-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const out = $("claim-result"), ref = parseRepo($("claim-repo").value), branch = $("claim-branch").value.trim() || "main";
  if (!ref) return say(out, "Enter the repository as owner/repo.", "bad");
  try {
    const add = `https://github.com/${ref.owner}/${ref.repo}/new/${encodeURIComponent(branch)}?filename=.github/workflows/knos-claim.yml&value=${encodeURIComponent(await claimWorkflow())}`;
    const run = `https://github.com/${ref.owner}/${ref.repo}/actions/workflows/knos-claim.yml`;
    out.innerHTML = `<a class="button" id="claim-add" href="${esc(add)}" target="_blank" rel="noopener">1. Commit knos-claim.yml to ${esc(ref.owner)}/${esc(ref.repo)}</a>
      <a class="button" id="claim-run" href="${esc(run)}" target="_blank" rel="noopener">2. Run it with your address</a>
      <p class="fine">The run posts its result on a new issue in that repository within a few minutes.</p>`;
  } catch (e) { say(out, esc(e.message), "bad"); }
};

// ---- the public numbers --------------------------------------------------------------------------------------------
const stat = (n, label) => `<div class="stat"><b>${esc(n)}</b><span>${esc(label)}</span></div>`;
const took = (s) => (s == null ? "n/a" : s < 120 ? `${s} s` : s < 7200 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);

async function immutability() {
  const all = await ids(), rows = [];
  for (const name of ["knos_oidc", "knos_pay"]) {
    const data = await knos.account(RPC, await knos.programData(all[name])).catch(() => null);
    const auth = knos.upgradeAuthority(data);
    const verdict = auth === null ? `<strong class="status ok">no upgrade authority: nobody can change it</strong>`
      : auth === undefined ? `<span class="status">not deployed on devnet yet</span>`
      : `<strong class="status bad">upgradeable by ${esc(auth)}</strong>`;
    rows.push(`<dt>${name.replace("_", "-")}</dt><dd><a class="mono" href="${EXPLORER("address", all[name])}" target="_blank" rel="noopener">${esc(all[name])}</a><br>${verdict}</dd>`);
  }
  return `<h2>The programs, checked now</h2><dl class="facts">${rows.join("")}</dl>`;
}

let networkLoaded = false;
async function loadNetwork() {
  if (networkLoaded) return;
  networkLoaded = true;
  const box = $("network-stats");
  immutability().then((h) => { $("network-programs").innerHTML = h; }).catch((e) => { $("network-programs").textContent = e.message; });
  const s = await fetch("stats.json").then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!s || !s.outside) {
    box.innerHTML = `<p class="status">${esc(s?.error || "The numbers are built with the site; none here yet.")}</p>`;
    return;
  }
  const o = s.outside, own = s.own, self = s.self_funded;
  box.innerHTML = `<div class="split"><h2>Outside use</h2><p class="fine">Funded and earned by GitHub accounts that are not Knos's, two different accounts each time.</p></div>`
    + stat(o.paid, "pull requests paid") + stat(usdc(o.amount), "USDC paid out") + stat(o.repositories, "repositories")
    + stat(o.funders, "funders") + stat(o.authors, "authors paid") + stat(took(o.median_seconds_fund_to_paid), "median, funded to paid")
    + `<div class="split"><h2>Everything else, kept apart</h2></div>`
    + stat(own.paid, "paid, Knos's own accounts") + stat(self.paid, "paid, funder paid themselves")
    + stat(s.funded, "bounties funded in all") + stat(s.refunded, "refunded unproven") + stat(s.vetoed, "vetoed")
    + stat(`${s.live?.open ?? 0}`, `open now (${usdc(s.live?.open_amount ?? 0)} USDC)`)
    + stat(usdc(s.live?.unclaimed_amount ?? 0), "USDC waiting to be claimed");
  const rows = (s.recent || []).map((p) => `<tr><td>${esc(p.kind)}</td><td>${/^[1-9A-HJ-NP-Za-km-z]{60,90}$/.test(p.tx || "") ? `<a href="${EXPLORER("tx", p.tx)}">${usdc(p.amount)}</a>` : usdc(p.amount)}</td><td>repo ${esc(p.repo)} #${esc(p.issue)}</td>
    <td>GitHub user ${esc(p.author)}</td><td>${took(p.seconds)}</td></tr>`).join("");
  $("network-recent").innerHTML = rows ? `<table><tr><th>kind</th><th>USDC</th><th>issue</th><th>paid to</th><th>funded to paid</th></tr>${rows}</table>` : "";
  $("network-note").textContent = `Counted ${s.updated} by scripts/network_stats.py from the program's transaction history.`
    + (s.error ? ` ${s.error}.` : "");
}

route();
