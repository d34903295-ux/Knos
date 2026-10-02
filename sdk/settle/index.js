// Knos settle, in JavaScript: addresses, audiences, instructions and account layouts of knos-oidc and knos-pay.
// No dependency: it runs as is in a browser (the Knos web app loads this file) and in Node 20+.
// The Python client (src/knos/settle) is the authority; sdk/settle/test.mjs checks this file against the
// byte-exact fixtures it wrote (sdk/settle/fixtures.json).
//
//   import * as knos from "./index.js";
//   const k = knos.client({ knos_oidc: "...", knos_pay: "..." });       // program ids (src/knos/settle/program_ids.json)
//   const job = await k.job(repoId, issue);                             // the repository's own bounty for an issue
//   const state = knos.parseJob(accountBytes);                          // { state, amount, authorId, ... }
//   const ix = await k.fundIx({ funder, funderToken, mint, repoId, issue, amount, wfRepo, wfSha });
//   const tx = knos.serializeTx([ix], funder, recentBlockhash);         // unsigned legacy transaction, for a wallet

export const TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
export const ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
export const SYSTEM = "11111111111111111111111111111111";
export const LOADER = "BPFLoaderUpgradeab1e11111111111111111111111";
export const FEE_OWNER = "4G3cznCnwCUPBCZwzKiLupjdgB5pSoCcGWNGuFv4TYFo";   // receives fees, nothing else
export const USDC_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU";  // Circle's devnet USDC
export const MERGE = 0, TESTS = 1;
export const FEE_BPS = 250, FEE_MIN = 50_000, MIN_AMOUNT = 1_000_000, MAX_AMOUNT = 500_000_000;
export const JOB_LEN = 256, DUE_LEN = 48, REP_LEN = 32;

// ---- bytes ---------------------------------------------------------------------------------------------------------
const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
const enc = new TextEncoder();

export function b58(bytes) {
  let n = 0n;
  for (const b of bytes) n = n * 256n + BigInt(b);
  let out = "";
  while (n > 0n) { out = B58[Number(n % 58n)] + out; n /= 58n; }
  for (const b of bytes) { if (b !== 0) break; out = "1" + out; }
  return out;
}

export function unb58(s, size = 32) {
  let n = 0n;
  for (const c of s) {
    const i = B58.indexOf(c);
    if (i < 0) throw new Error(`not base58: ${s}`);
    n = n * 58n + BigInt(i);
  }
  const out = new Uint8Array(size);
  for (let i = size - 1; i >= 0; i--) { out[i] = Number(n & 255n); n >>= 8n; }
  if (n > 0n) throw new Error(`longer than ${size} bytes: ${s}`);
  return out;
}

export const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");
export const unhex = (s) => Uint8Array.from(s.match(/../g) || [], (h) => parseInt(h, 16));
const key = (k) => (typeof k === "string" ? unb58(k) : k);
const u64 = (v) => { const b = new Uint8Array(8); new DataView(b.buffer).setBigUint64(0, BigInt(v), true); return b; };
const i64 = (v) => { const b = new Uint8Array(8); new DataView(b.buffer).setBigInt64(0, BigInt(v), true); return b; };
const cat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
};

export async function sha256(bytes) {
  return new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes));
}

// ---- program addresses ---------------------------------------------------------------------------------------------
// A program address is a sha256 that is NOT a point on ed25519. The curve check: y is the low 255 bits; the point
// exists iff (y^2 - 1) / (d y^2 + 1) is a square mod p.
const P = 2n ** 255n - 19n;
const D = 37095705934669439343138083508754565189542113879843219016388785533085940283555n;
const mod = (a) => ((a % P) + P) % P;
function pow(b, e) {
  let r = 1n;
  b = mod(b);
  while (e > 0n) { if (e & 1n) r = mod(r * b); b = mod(b * b); e >>= 1n; }
  return r;
}
export function onCurve(bytes) {
  let y = 0n;
  for (let i = 31; i >= 0; i--) y = (y << 8n) | BigInt(bytes[i]);
  y &= (1n << 255n) - 1n;
  if (y >= P) return false;
  const u = mod(y * y - 1n), v = mod(D * y * y + 1n);
  const x2 = mod(u * pow(v, P - 2n));
  return x2 === 0n || pow(x2, (P - 1n) / 2n) === 1n;
}

export async function findProgramAddress(seeds, program) {
  const tail = cat(key(program), enc.encode("ProgramDerivedAddress"));
  for (let bump = 255; bump >= 0; bump--) {
    const h = await sha256(cat(...seeds, Uint8Array.of(bump), tail));
    if (!onCurve(h)) return [b58(h), bump];
  }
  throw new Error("no program address");
}

export const ata = async (owner, mint) => (await findProgramAddress([key(owner), key(TOKEN), key(mint)], ATA_PROGRAM))[0];
export const programData = async (program) => (await findProgramAddress([key(program)], LOADER))[0];

// ---- audiences: what the workflows ask GitHub to sign --------------------------------------------------------------
const ZERO = "0".repeat(64);
export const fundAudience = (issue, amount, mode = MERGE, checksHex = ZERO, workS = 14 * 86400, reviewS = 0) =>
  `knos:fund:${issue}:${amount}:${mode}:${checksHex}:${workS}:${reviewS}`;
export const payAudience = (repoId, issue, authorId, headSha, checksHex = ZERO, mode = MERGE) =>
  `knos:pay:${repoId}:${issue}:${authorId}:${headSha}:${checksHex}:${mode}`;
export const vetoAudience = (repoId, issue) => `knos:veto:${repoId}:${issue}`;
export const claimAudience = (address) => `knos:claim:${address}`;

/** The fee on a payment: 2.5%, at least 0.05, never more than the amount. */
export function feeOf(amount) {
  const a = BigInt(amount);
  const pct = (a * BigInt(FEE_BPS)) / 10000n;
  const fee = pct > BigInt(FEE_MIN) ? pct : BigInt(FEE_MIN);
  return Number(fee < a ? fee : a);
}

/** sha256 of "owner/name": the repository whose prove.yml and fund.yml a job pins. */
export const wfRepoHash = (repository) => sha256(enc.encode(repository));

// ---- account layouts -----------------------------------------------------------------------------------------------
const STATES = { 1: "open", 2: "proven" };

export function parseJob(raw) {
  if (!raw || raw.length !== JOB_LEN) return null;
  const dv = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  const u = (o) => Number(dv.getBigUint64(o, true)), i = (o) => Number(dv.getBigInt64(o, true));
  return { state: STATES[raw[0]] || "?", mode: raw[1], tokenFunded: raw[2] === 1, repoId: u(8), issue: u(16), amount: u(24),
    deadline: i(32), review: i(40), payAfter: i(48), authorId: u(56), funderId: u(64), notBefore: i(72),
    vetoes: dv.getUint32(80, true), funder: b58(raw.slice(88, 120)), mint: b58(raw.slice(120, 152)),
    checks: hex(raw.slice(152, 184)), wfRepoHash: hex(raw.slice(184, 216)), wfSha: new TextDecoder().decode(raw.slice(216, 256)) };
}

export function parseDue(raw) {
  if (!raw || raw.length !== DUE_LEN) return null;
  const dv = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  return { amount: Number(dv.getBigUint64(0, true)), userId: Number(dv.getBigUint64(8, true)), mint: b58(raw.slice(16, 48)) };
}

export function parseRep(raw) {
  if (!raw || raw.length !== REP_LEN) return { paidJobs: 0, totalPaid: 0, repositories: 0 };
  const dv = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  return { paidJobs: dv.getUint32(0, true), totalPaid: Number(dv.getBigUint64(8, true)), repositories: dv.getUint32(16, true) };
}

/** The upgrade authority in a program-data account, or null when the program is immutable. */
export function upgradeAuthority(programDataBytes) {
  if (!programDataBytes || programDataBytes.length < 45 || programDataBytes[0] !== 3) return undefined;
  return programDataBytes[12] === 1 ? b58(programDataBytes.slice(13, 45)) : null;
}

// ---- a client bound to the deployed program ids --------------------------------------------------------------------
const meta = (pubkey, signer, writable) => ({ pubkey, signer, writable });

export function client(ids) {
  const PAY = ids.knos_pay;
  const pda = async (...seeds) => (await findProgramAddress(seeds, PAY))[0];
  const k = {
    ids,
    auth: () => pda(enc.encode("auth")),
    vault: (mint) => pda(enc.encode("vault"), key(mint)),
    faucetMint: () => pda(enc.encode("mint")),
    /** A job's address. `funder` is the wallet that funded it; omit it for the repository's own token-funded bounty. */
    job: (repoId, issue, funder = null) => pda(enc.encode("job"), u64(repoId), u64(issue), funder ? key(funder) : new Uint8Array(32)),
    due: (userId, mint) => pda(enc.encode("due"), u64(userId), key(mint)),
    rep: (userId) => pda(enc.encode("rep"), u64(userId)),
    rate: (repoId) => pda(enc.encode("rate"), u64(repoId)),

    /** A wallet funds a bounty on any issue of a repository that runs Knos's workflow at commit `wfSha`. */
    async fundIx({ funder, funderToken, mint, repoId, issue, amount, wfRepo, wfSha, mode = MERGE, checksHex = ZERO,
      workS = 14 * 86400, reviewS = 0 }) {
      if (!/^[0-9a-f]{40}$/.test(wfSha)) throw new Error("wfSha must be a full commit sha");
      const data = cat(Uint8Array.of(0), u64(repoId), u64(issue), u64(amount), i64(workS), i64(reviewS), Uint8Array.of(mode),
        unhex(checksHex), await wfRepoHash(wfRepo), enc.encode(wfSha));
      return { program: PAY, data, accounts: [meta(funder, true, true), meta(await k.job(repoId, issue, funder), false, true),
        meta(funderToken, false, true), meta(await k.vault(mint), false, true), meta(mint, false, false),
        meta(await k.auth(), false, false), meta(TOKEN, false, false), meta(SYSTEM, false, false)] };
    },

    /** The funding wallet takes back a tests-mode payment during its review window. */
    vetoIx: (wallet, job) => ({ program: PAY, data: Uint8Array.of(4), accounts: [meta(wallet, true, false), meta(job, false, true)] }),
  };
  return k;
}

/** Creates the owner's associated token account if it does not exist (idempotent). */
export async function createAtaIx(payer, owner, mint) {
  return { program: ATA_PROGRAM, data: Uint8Array.of(1), accounts: [meta(payer, true, true), meta(await ata(owner, mint), false, true),
    meta(owner, false, false), meta(mint, false, false), meta(SYSTEM, false, false), meta(TOKEN, false, false)] };
}

// ---- an unsigned legacy transaction, for a wallet to sign and send -------------------------------------------------
function shortvec(n) {
  const out = [];
  for (;;) { const b = n & 0x7f; n >>= 7; if (n) out.push(b | 0x80); else { out.push(b); return Uint8Array.from(out); } }
}

export function serializeTx(ixs, payer, recentBlockhash) {
  const order = new Map();     // pubkey -> { signer, writable }
  const note = (pubkey, signer, writable) => {
    const have = order.get(pubkey) || { signer: false, writable: false };
    order.set(pubkey, { signer: have.signer || signer, writable: have.writable || writable });
  };
  note(payer, true, true);
  for (const ix of ixs) for (const a of ix.accounts) note(a.pubkey, a.signer, a.writable);
  for (const ix of ixs) note(ix.program, false, false);
  const rank = ({ signer, writable }) => (signer ? 0 : 2) + (writable ? 0 : 1);
  const keys = [...order.entries()].sort((a, b) => (a[0] === payer ? -1 : b[0] === payer ? 1 : rank(a[1]) - rank(b[1])));
  const names = keys.map(([pubkey]) => pubkey);
  const count = (f) => keys.filter(([, v]) => f(v)).length;
  const header = Uint8Array.of(count((v) => v.signer), count((v) => v.signer && !v.writable), count((v) => !v.signer && !v.writable));
  const body = ixs.map((ix) => cat(Uint8Array.of(names.indexOf(ix.program)), shortvec(ix.accounts.length),
    Uint8Array.from(ix.accounts.map((a) => names.indexOf(a.pubkey))), shortvec(ix.data.length), ix.data));
  const message = cat(header, shortvec(names.length), ...names.map((n) => key(n)), key(recentBlockhash), shortvec(ixs.length), ...body);
  return cat(shortvec(header[0]), new Uint8Array(64 * header[0]), message);
}

// ---- JSON-RPC ------------------------------------------------------------------------------------------------------
export async function rpc(url, method, params) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }) });
  const j = await r.json();
  if (j.error) throw new Error(j.error.message || "RPC error");
  return j.result;
}

const fromB64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

export async function account(url, address) {
  const got = await rpc(url, "getAccountInfo", [address, { encoding: "base64", commitment: "confirmed" }]);
  return got.value ? fromB64(got.value.data[0]) : null;
}

/** Every account of `program` with this size whose bytes at `offset` equal `bytes`: [{ address, data }]. */
export async function programAccounts(url, program, size, offset = null, bytes = null) {
  const filters = [{ dataSize: size }];
  if (bytes) filters.push({ memcmp: { offset, bytes: b58(bytes) } });
  const got = await rpc(url, "getProgramAccounts", [program, { encoding: "base64", commitment: "confirmed", filters }]);
  return (got || []).map((x) => ({ address: x.pubkey, data: fromB64(x.account.data[0]) }));
}

export { u64 as u64le };
