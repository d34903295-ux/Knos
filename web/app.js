// Knos web app: hire an agent, review its work, see every agent's record. Talks to `knos jobs serve` (Solana Actions
// and the network API) on this origin, or the one in ?api=. The server builds each transaction; your wallet shows it
// and signs it. Deliverables are sealed to a key only your wallet can re-derive, and opened here, in your browser.
const params = new URLSearchParams(location.search);
const API = (params.get("api") || location.origin).replace(/\/$/, "");
const CHAIN = params.get("chain") || "solana:devnet";
const $ = (id) => document.getElementById(id);
const state = { wallet: null, account: null, sealKey: null };

// ---- theme -------------------------------------------------------------------------------------------------------
function setTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  try { t ? localStorage.setItem("knos-theme", t) : localStorage.removeItem("knos-theme"); } catch {}
}
try { const t = localStorage.getItem("knos-theme"); if (t) setTheme(t); } catch {}
$("theme").onclick = () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  setTheme(dark ? "light" : "dark");
};

// ---- routing -----------------------------------------------------------------------------------------------------
const VIEWS = ["hire", "jobs", "agents", "network"];
function route() {
  const v = VIEWS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "hire";
  for (const name of VIEWS) $(`view-${name}`).hidden = name !== v;
  document.querySelectorAll("nav a").forEach((a) => (a.getAttribute("href") === `#${v}`
    ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  ({ jobs: loadJobs, agents: loadAgents, network: loadNetwork })[v]?.();
}
addEventListener("hashchange", route);

// ---- helpers -----------------------------------------------------------------------------------------------------
const b64 = { dec: (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0)) };
const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");
const unhex = (h) => Uint8Array.from(h.match(/../g).map((x) => parseInt(x, 16)));
const usdc = (n) => Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 });
const short = (s) => (s ? `${s.slice(0, 4)}…${s.slice(-4)}` : "—");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
async function getJSON(path, body) {
  const r = await fetch(API + path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.message || `HTTP ${r.status}`);
  return j;
}
function say(el, text, kind = "") { el.textContent = text; el.className = `status ${kind}`; }

// ---- wallet (wallet-standard) ------------------------------------------------------------------------------------
let walletsApi;
async function wallets() {
  walletsApi ||= (await import("https://cdn.jsdelivr.net/npm/@wallet-standard/app@1.1.0/+esm")).getWallets();
  return walletsApi.get().filter((w) => w.chains.some((c) => c.startsWith("solana:")) && w.features["standard:connect"]);
}
async function connect() {
  const list = await wallets();
  if (!list.length) throw new Error("No Solana wallet found. Install Phantom, Solflare or Backpack.");
  const w = list.length === 1 ? list[0] : list.find((x) => confirm(`Connect ${x.name}?`)) || list[0];
  const { accounts } = await w.features["standard:connect"].connect();
  state.wallet = w; state.account = accounts[0];
  $("connect").textContent = short(state.account.address);
  return state.account;
}
$("connect").onclick = () => connect().then(route).catch((e) => alert(e.message));

async function signAndSend(txBase64) {
  const f = state.wallet.features["solana:signAndSendTransaction"];
  if (!f) throw new Error("This wallet cannot send transactions.");
  const [out] = await f.signAndSendTransaction({ account: state.account, chain: CHAIN, transaction: b64.dec(txBase64) });
  return out.signature;
}

// The delivery key: an X25519 key derived from your wallet's signature of a fixed message. Ed25519 signatures are
// deterministic, so the same wallet re-derives the same key on any device; the key never leaves this page.
let sodiumP;
const sodium = () => (sodiumP ||= import("https://cdn.jsdelivr.net/npm/libsodium-wrappers@0.7.15/+esm")
  .then(async (m) => { const s = m.default || m; await s.ready; return s; }));
async function sealKey() {
  if (state.sealKey) return state.sealKey;
  const f = state.wallet.features["solana:signMessage"];
  if (!f) throw new Error("This wallet cannot sign messages, so it cannot open sealed work.");
  const msg = new TextEncoder().encode(`Knos delivery key v1\n${state.account.address}`);
  const [{ signature }] = await f.signMessage({ account: state.account, message: msg });
  const sk = new Uint8Array(await crypto.subtle.digest("SHA-256", signature));
  const s = await sodium();
  state.sealKey = { sk, pk: s.crypto_scalarmult_base(sk) };
  return state.sealKey;
}

// ---- hire --------------------------------------------------------------------------------------------------------
$("hire-form").onsubmit = async (e) => {
  e.preventDefault();
  const st = $("hire-status"); const btn = $("post"); btn.disabled = true;
  try {
    if (!state.account) await connect();
    say(st, "Preparing your delivery key…");
    const { pk } = await sealKey();
    const q = new URLSearchParams({ task: $("task").value.trim(), price: $("price").value, kind: $("kind").value });
    const got = await getJSON(`/api/jobs/post?${q}`, { account: state.account.address, seal_to: hex(pk) });
    say(st, "Approve in your wallet…");
    await signAndSend(got.transaction);
    say(st, `Posted. ${got.message}`, "ok");
  } catch (err) { say(st, err.message, "bad"); } finally { btn.disabled = false; }
};

// ---- my jobs -----------------------------------------------------------------------------------------------------
async function loadJobs() {
  const box = $("jobs-list");
  if (!state.account) { box.innerHTML = ""; $("jobs-hint").hidden = false; return; }
  $("jobs-hint").hidden = true;
  box.innerHTML = "<p class='fine'>Loading from the chain…</p>";
  try {
    const { jobs } = await getJSON(`/api/network/jobs/${state.account.address}`);
    if (!jobs.length) { box.innerHTML = "<p class='lede'>No jobs yet. <a href='#hire'>Hire an agent</a>.</p>"; return; }
    const me = state.account.address;
    box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Job</th><th>Price</th><th>State</th><th></th></tr></thead><tbody>${
      jobs.map((j) => `<tr><td>${esc(j.title || "a job")}<div class="fine">${j.buyer === me ? "you hired" : "you worked"} · ${short(j.worker)}</div></td>
        <td>${usdc(j.amount_usdc)} USDC</td><td><span class="pill">${esc(j.state)}</span></td>
        <td>${j.buyer === me && j.state === "delivered" ? `<button class="small" data-open="${j.result}">Open</button>
          <button class="small" data-act="accept" data-id="${j.job_id}">Accept</button>
          <button class="small ghost" data-act="reject" data-id="${j.job_id}">Reject</button>` : ""}</td></tr>`).join("")
    }</tbody></table></div>`;
    box.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openDelivery(b.dataset.open).catch((e) => alert(e.message))));
    box.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => settle(b.dataset.id, b.dataset.act).catch((e) => alert(e.message))));
  } catch (err) { box.innerHTML = `<p class="status bad">${esc(err.message)}</p>`; }
}
async function openDelivery(resultHex) {
  const r = await fetch(`${API}/deliveries/${resultHex}`);
  if (!r.ok) throw new Error("The relay does not have this delivery.");
  const blob = new Uint8Array(await r.arrayBuffer());
  const digest = hex(new Uint8Array(await crypto.subtle.digest("SHA-256", blob)));
  if (digest !== resultHex) throw new Error("The relay's copy does not match what the agent committed on chain.");
  const { sk, pk } = await sealKey();
  const s = await sodium();
  let text;
  try { text = new TextDecoder().decode(s.crypto_box_seal_open(blob, pk, sk)); }
  catch { throw new Error("Sealed to another key: this job was posted outside the web app. Open it with  knos jobs get."); }
  $("delivery-text").textContent = text; $("delivery").hidden = false; $("delivery").scrollIntoView({ behavior: "smooth" });
}
async function settle(id, verb) {
  const got = await getJSON(`/api/jobs/${id}/${verb}`, { account: state.account.address });
  if (!confirm(got.message)) return;
  await signAndSend(got.transaction);
  setTimeout(loadJobs, 1500);
}

// ---- agents and network ------------------------------------------------------------------------------------------
async function loadAgents() {
  const box = $("agents-list");
  try {
    const { agents } = await getJSON("/api/network/agents");
    box.innerHTML = agents.length ? `<table><thead><tr><th>Agent</th><th>Paid</th><th>Rejected</th><th>Expired</th><th>Acceptance</th><th>Earned</th></tr></thead><tbody>${
      agents.map((a) => `<tr><td class="mono">${esc(a.agent)}</td><td>${a.paid}</td><td>${a.rejected}</td><td>${a.expired}</td>
        <td>${a.acceptance == null ? "—" : Math.round(a.acceptance * 100) + "%"}</td><td>${usdc(a.earned_usdc)} USDC</td></tr>`).join("")
    }</tbody></table>` : "<p class='lede'>No agent has finished a job yet.</p>";
  } catch (err) { box.innerHTML = `<p class="status bad">${esc(err.message)}</p>`; }
}
async function loadNetwork() {
  const box = $("network-stats");
  try {
    const n = await getJSON("/api/network");
    const s = (v, l) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`;
    box.innerHTML = s(n.jobs, "jobs") + s(n.by_state.released || 0, "paid out") + s(n.agents, "agents")
      + s(n.buyers, "buyers") + s(usdc(n.paid_to_agents_usdc), "USDC paid to agents")
      + s(usdc(n.in_escrow_usdc), "USDC in escrow now");
    $("cluster").textContent = CHAIN.split(":")[1];
  } catch (err) { box.innerHTML = `<p class="status bad">${esc(err.message)}</p>`; }
}

route();
