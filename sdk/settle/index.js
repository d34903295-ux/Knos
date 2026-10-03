// Knos settle, in JavaScript: addresses, audiences, instructions and account layouts of knos-oidc and knos-pay, the
// unsigned transaction a wallet signs, and the call that asks an injected wallet to sign and send it.
// No dependency: it runs as is in a browser (the Knos web app loads this file) and in Node 20+.
// The Python client (src/knos/settle) is the authority; sdk/settle/test.mjs checks this file against the
// byte-exact fixtures it wrote (sdk/settle/fixtures.json).
//
// The second deployment (programs-v2; upgradeable only through a multisig with a public 48-hour delay, until an
// outside review; then made immutable) is under `v2`:
//
//   import * as knos from "./index.js";
//   const k = knos.v2.client(ids);                                      // ids: src/knos/settle/v2/program_ids.json
//   const balance = await k.balance(ownerId, wallet, mint);             // a wallet's Balance for one GitHub owner
//   const state = knos.v2.readBalance(await knos.account(RPC, balance));
//   const ix = await k.openBalanceIx({ authority: wallet, ownerId, mint, cap, spenders });
//   const tx = knos.serializeTx([ix], wallet, recentBlockhash);         // unsigned legacy transaction
//   const [w] = knos.wallets();                                         // Wallet Standard, then Phantom, then Solflare
//   await w.connect(); const signature = await w.signAndSend(tx, "solana:devnet");
//
// The first deployment (programs/, immutable; jobs funded there finish there) keeps the names it had:
//
//   const k1 = knos.client({ knos_oidc: "...", knos_pay: "..." });      // src/knos/settle/program_ids.json
//   const job = knos.parseJob(await knos.account(RPC, await k1.job(repoId, issue)));

export const TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
export const TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
export const ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
export const SYSTEM = "11111111111111111111111111111111";
export const LOADER = "BPFLoaderUpgradeab1e11111111111111111111111";
export const SQUADS = "SQDS4ep65T869zMMBKyuUq6aD6EgTu8psMjkvj52pCf";      // Squads v4, the multisig program
export const FEE_OWNER = "4G3cznCnwCUPBCZwzKiLupjdgB5pSoCcGWNGuFv4TYFo";   // receives fees, nothing else
export const USDC_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU";  // Circle's devnet USDC
export const MERGE = 0, TESTS = 1;
export const FEE_BPS = 250, FEE_MIN = 50_000, MIN_AMOUNT = 1_000_000, MAX_AMOUNT = 500_000_000;
export const JOB_LEN = 256, DUE_LEN = 48, REP_LEN = 32;                    // the first deployment's accounts

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

/** A Solana address: base58 that decodes to exactly 32 bytes, written the one way those bytes are written. */
export function isAddress(s) {
  if (typeof s !== "string" || s.length < 32 || s.length > 44 || [...s].some((c) => !B58.includes(c))) return false;
  try { const raw = unb58(s); return raw.some((b) => b !== 0) && b58(raw) === s; } catch { return false; }
}

export const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");
export const unhex = (s) => Uint8Array.from(s.match(/../g) || [], (h) => parseInt(h, 16));
const key = (k) => (typeof k === "string" ? unb58(k) : k);
const u64 = (v) => { const b = new Uint8Array(8); new DataView(b.buffer).setBigUint64(0, BigInt(v), true); return b; };
const i64 = (v) => { const b = new Uint8Array(8); new DataView(b.buffer).setBigInt64(0, BigInt(v), true); return b; };
const u32 = (v) => { const b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, Number(v), true); return b; };
const u16 = (v) => Uint8Array.of(v & 255, (v >> 8) & 255);
const cat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
};
const view = (raw) => new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
// A 64-bit field as a number; as a BigInt when it is too large to be one exactly (ids and amounts never are).
const num = (big) => (big >= -9007199254740991n && big <= 9007199254740991n ? Number(big) : big);
const ascii = (raw) => [...raw].map((b) => (b < 128 ? String.fromCharCode(b) : String.fromCharCode(0xfffd))).join("");

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

/** The owner's associated token account of a mint (`tokenProgram`: TOKEN, or TOKEN_2022 for a Token-2022 mint). */
export const ata = async (owner, mint, tokenProgram = TOKEN) =>
  (await findProgramAddress([key(owner), key(tokenProgram), key(mint)], ATA_PROGRAM))[0];
export const programData = async (program) => (await findProgramAddress([key(program)], LOADER))[0];

// ---- the first deployment: audiences, fees, account layouts ---------------------------------------------------------
const ZERO = "0".repeat(64);
export const fundAudience = (issue, amount, mode = MERGE, checksHex = ZERO, workS = 14 * 86400, reviewS = 0) =>
  `knos:fund:${issue}:${amount}:${mode}:${checksHex}:${workS}:${reviewS}`;
export const payAudience = (repoId, issue, authorId, headSha, checksHex = ZERO, mode = MERGE) =>
  `knos:pay:${repoId}:${issue}:${authorId}:${headSha}:${checksHex}:${mode}`;
export const vetoAudience = (repoId, issue) => `knos:veto:${repoId}:${issue}`;
export const claimAudience = (address) => `knos:claim:${address}`;

/** The fee on a payment: 2.5%, at least 0.05, never more than the amount. The same in both deployments. */
export function feeOf(amount) {
  const a = BigInt(amount);
  const pct = (a * BigInt(FEE_BPS)) / 10000n;
  const fee = pct > BigInt(FEE_MIN) ? pct : BigInt(FEE_MIN);
  return Number(fee < a ? fee : a);
}

/** sha256 of "owner/name": the repository whose prove.yml and fund.yml a job pins. */
export const wfRepoHash = (repository) => sha256(enc.encode(repository));

const STATES = { 1: "open", 2: "proven" };

export function parseJob(raw) {
  if (!raw || raw.length !== JOB_LEN) return null;
  const dv = view(raw);
  const u = (o) => Number(dv.getBigUint64(o, true)), i = (o) => Number(dv.getBigInt64(o, true));
  return { state: STATES[raw[0]] || "?", mode: raw[1], tokenFunded: raw[2] === 1, repoId: u(8), issue: u(16), amount: u(24),
    deadline: i(32), review: i(40), payAfter: i(48), authorId: u(56), funderId: u(64), notBefore: i(72),
    vetoes: dv.getUint32(80, true), funder: b58(raw.slice(88, 120)), mint: b58(raw.slice(120, 152)),
    checks: hex(raw.slice(152, 184)), wfRepoHash: hex(raw.slice(184, 216)), wfSha: new TextDecoder().decode(raw.slice(216, 256)) };
}

export function parseDue(raw) {
  if (!raw || raw.length !== DUE_LEN) return null;
  const dv = view(raw);
  return { amount: Number(dv.getBigUint64(0, true)), userId: Number(dv.getBigUint64(8, true)), mint: b58(raw.slice(16, 48)) };
}

export function parseRep(raw) {
  if (!raw || raw.length !== REP_LEN) return { paidJobs: 0, totalPaid: 0, repositories: 0 };
  const dv = view(raw);
  return { paidJobs: dv.getUint32(0, true), totalPaid: Number(dv.getBigUint64(8, true)), repositories: dv.getUint32(16, true) };
}

// ---- who can change a program ---------------------------------------------------------------------------------------
/** The upgrade authority in a program-data account: an address, null when the program is immutable, undefined when
 *  the bytes are not a program-data account (the program is not deployed). */
export function upgradeAuthority(programDataBytes) {
  if (!programDataBytes || programDataBytes.length < 45 || programDataBytes[0] !== 3) return undefined;
  return programDataBytes[12] === 1 ? b58(programDataBytes.slice(13, 45)) : null;
}

/** A Squads multisig's vault: the address that signs what the multisig approved (index 0 unless it made more). */
export const squadsVault = async (multisig, index = 0) =>
  (await findProgramAddress([enc.encode("multisig"), key(multisig), enc.encode("vault"), Uint8Array.of(index)], SQUADS))[0];

const MULTISIG = [224, 116, 121, 186, 68, 161, 79, 236];     // sha256("account:Multisig")[0..8], Anchor's account tag

/** A Squads v4 multisig account: how many of its members must approve, and how long an approved transaction waits
 *  before it can run (`timeLock`, seconds). null for any other account. */
export function readMultisig(raw) {
  if (!raw || raw.length < 100 || MULTISIG.some((b, i) => raw[i] !== b)) return null;
  const dv = view(raw);
  let o = 94;
  const rentCollector = raw[o] === 1 ? b58(raw.slice(o + 1, o + 33)) : null;
  o += raw[o] === 1 ? 33 : 1;
  const count = dv.getUint32(o + 1, true);
  if (o + 5 + 33 * count > raw.length) return null;
  const members = [];
  for (let m = 0; m < count; m++) members.push({ key: b58(raw.slice(o + 5 + 33 * m, o + 37 + 33 * m)), permissions: raw[o + 37 + 33 * m] });
  return { createKey: b58(raw.slice(8, 40)), configAuthority: b58(raw.slice(40, 72)), threshold: dv.getUint16(72, true),
    timeLock: dv.getUint32(74, true), transactionIndex: num(dv.getBigUint64(78, true)),
    staleTransactionIndex: num(dv.getBigUint64(86, true)), rentCollector, members };
}

// ---- knos-oidc: the verifier (both deployments send it the same Write, Step, Close, RegisterKey and KeyParams) -------
export const GITHUB = 0, GITLAB = 1;
export const ISSUERS = { [GITHUB]: "https://token.actions.githubusercontent.com", [GITLAB]: "https://gitlab.com" };
export const JWKS = { [GITHUB]: "https://token.actions.githubusercontent.com/.well-known/jwks", [GITLAB]: "https://gitlab.com/oauth/discovery/keys" };
export const MAX_JWT = 8192;
export const LATE = 3600;       // a token is accepted up to an hour past its exp (each instruction has its own replay guard)
export const T_JWT = 626;       // where the token's bytes start in its account
export const CHUNK = 880;       // token bytes per Write instruction (one per transaction)

const bytesOf = (jwt) => (typeof jwt === "string" ? enc.encode(jwt) : jwt);

/** What names a token on chain: sha256 of its bytes. */
export const tokenId = (jwt) => sha256(bytesOf(jwt));

/** An RSA modulus (a BigInt) as 256 or 512 big-endian bytes. */
export function modulusBytes(n) {
  let bits = 0;
  for (let v = BigInt(n); v > 0n; v >>= 1n) bits++;
  const k = Math.ceil(bits / 8);
  if (k !== 256 && k !== 512) throw new Error("only 2048- and 4096-bit RSA keys are supported");
  const out = new Uint8Array(k);
  let v = BigInt(n);
  for (let i = k - 1; i >= 0; i--) { out[i] = Number(v & 255n); v >>= 8n; }
  return out;
}

export const keyHash = (n) => sha256(modulusBytes(n));

/** { n0inv, r2 }: -n^-1 mod 2^32 and R^2 mod n (R = 2^bits), which KeyParams takes and checks on chain. */
export function keyParams(n) {
  n = BigInt(n);
  const k = modulusBytes(n).length, W = 1n << 32n;
  let inv = n % W;                                      // Newton's iteration: each round doubles the correct bits
  for (let i = 0; i < 5; i++) inv = (inv * (2n + W - ((n * inv) % W))) % W;
  let r2 = 1n, b = 2n % n;
  for (let e = BigInt(16 * k); e > 0n; e >>= 1n) { if (e & 1n) r2 = (r2 * b) % n; b = (b * b) % n; }
  const out = new Uint8Array(k);
  for (let i = k - 1; i >= 0; i--) { out[i] = Number(r2 & 255n); r2 >>= 8n; }
  return { n0inv: Number((W - inv) % W), r2: out };
}

/** Squarings per Step transaction, 16 in all, so that every transaction stays under the 1,400,000 compute units a
 *  transaction can have, for a token of any size up to MAX_JWT. */
export const stepPlan = (bits) => (bits === 2048 ? [8, 8] : [2, 3, 3, 3, 4, 1]);

/** The audience the pinned rotate workflow asks GitHub to sign for a key it found in the issuer's JWKS. */
export const rotateAudience = async (issuer, n) => `knos-oidc:key:${issuer}:${hex(await keyHash(n))}`;

function unb64url(s) {
  const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4));
  return Uint8Array.from(b, (c) => c.charCodeAt(0));
}

/** [kid, modulus] for each RS256 key with exponent 65537 in a JWKS document. */
export function jwksKeys(jwks) {
  const out = [];
  for (const k of jwks.keys || []) {
    if (k.kty !== "RSA" || k.e !== "AQAB" || (k.alg ?? "RS256") !== "RS256") continue;
    out.push([k.kid ?? "", BigInt("0x" + (hex(unb64url(k.n)) || "0"))]);
  }
  return out;
}

/** A token account: { stage (0 writing, 1 stepping, 2 verified), issuer, done, exp, key, payer, payload, verified,
 *  claims() }. `payload` is the decoded claims (JSON bytes), only when verified. null for anything shorter. */
export function readToken(raw) {
  if (!raw || raw.length < T_JWT) return null;
  const dv = view(raw);
  const off = dv.getUint16(6, true), len = dv.getUint16(8, true);
  const payload = raw[0] === 2 ? raw.slice(off, off + len) : new Uint8Array(0);
  return { stage: raw[0], issuer: raw[1], done: raw[2], exp: num(dv.getBigInt64(10, true)), key: b58(raw.slice(18, 50)),
    payer: b58(raw.slice(50, 82)), payload, verified: raw[0] === 2, claims: () => JSON.parse(new TextDecoder().decode(payload)) };
}

const meta = (pubkey, signer, writable) => ({ pubkey, signer, writable });

/** The verifier at one program id: its addresses and instructions. `tid` is tokenId(jwt); `n` a modulus (BigInt). */
export function verifier(program) {
  const o = {
    program,
    tokenPda: async (payer, tid) => (await findProgramAddress([enc.encode("tok"), key(payer), tid], program))[0],
    keyPda: async (issuer, n) => (await findProgramAddress([enc.encode("key"), Uint8Array.of(issuer), await keyHash(n)], program))[0],
    /** The token into its account: one instruction per CHUNK bytes, each in a transaction of its own. */
    async writeIxs(payer, tid, jwt) {
      const raw = bytesOf(jwt);
      if (!raw.length || raw.length > MAX_JWT) throw new Error(`token is ${raw.length} bytes; the verifier takes up to ${MAX_JWT}`);
      const accounts = [meta(payer, true, true), meta(await o.tokenPda(payer, tid), false, true), meta(SYSTEM, false, false)];
      const out = [];
      for (let off = 0; off < raw.length; off += CHUNK) {
        out.push({ program, data: cat(Uint8Array.of(0), tid, u16(raw.length), u16(off), raw.slice(off, off + CHUNK)), accounts });
      }
      return out;
    },
    stepIx: async (payer, tid, keyAccount, squarings) => ({ program, data: cat(Uint8Array.of(1), tid, Uint8Array.of(squarings)),
      accounts: [meta(payer, true, false), meta(await o.tokenPda(payer, tid), false, true), meta(keyAccount, false, false)] }),
    closeIx: async (payer, tid) => ({ program, data: cat(Uint8Array.of(2), tid),
      accounts: [meta(payer, true, true), meta(await o.tokenPda(payer, tid), false, true)] }),
    /** A genesis key needs no `attest`. Any other needs a verified token account whose audience is
     *  rotateAudience(issuer, n), from the pinned rotate workflow run in the attester's repository. */
    async registerKeyIx(payer, issuer, n, attest = null) {
      const accounts = [meta(payer, true, true), meta(await o.keyPda(issuer, n), false, true), meta(SYSTEM, false, false)];
      if (attest) accounts.push(meta(attest, false, false));
      return { program, data: cat(Uint8Array.of(3, issuer), modulusBytes(n)), accounts };
    },
    async keyParamsIx(payer, issuer, n) {
      const { n0inv, r2 } = keyParams(n);
      return { program, data: cat(Uint8Array.of(4), u32(n0inv), r2), accounts: [meta(payer, true, false), meta(await o.keyPda(issuer, n), false, true)] };
    },
    // -- the second deployment only: how a key lives --
    /** The key's expiry becomes now + KEY_TTL if that is later. `attest` as for registerKeyIx. Anyone may send it. */
    refreshIx: async (payer, issuer, n, attest) => ({ program, data: Uint8Array.of(5),
      accounts: [meta(payer, true, false), meta(await o.keyPda(issuer, n), false, true), meta(attest, false, false)] }),
    approveIx: async (guardian, issuer, n) => ({ program, data: Uint8Array.of(6),
      accounts: [meta(guardian, true, false), meta(await o.keyPda(issuer, n), false, true)] }),
    /** For ever: no instruction undoes it, and the same modulus cannot be registered for that issuer again. */
    revokeIx: async (guardian, issuer, n) => ({ program, data: Uint8Array.of(7),
      accounts: [meta(guardian, true, false), meta(await o.keyPda(issuer, n), false, true)] }),
  };
  return o;
}

// ---- a client bound to the first deployment's program ids ----------------------------------------------------------
export function client(ids) {
  const PAY = ids.knos_pay;
  const pda = async (...seeds) => (await findProgramAddress(seeds, PAY))[0];
  const k = {
    ids,
    oidc: verifier(ids.knos_oidc),
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

// ---- SPL Token ------------------------------------------------------------------------------------------------------
/** Creates the owner's associated token account if it does not exist (idempotent). */
export async function createAtaIx(payer, owner, mint, tokenProgram = TOKEN) {
  return { program: ATA_PROGRAM, data: Uint8Array.of(1), accounts: [meta(payer, true, true), meta(await ata(owner, mint, tokenProgram), false, true),
    meta(owner, false, false), meta(mint, false, false), meta(SYSTEM, false, false), meta(tokenProgram, false, false)] };
}

/** A plain token transfer that names the mint and its decimals (TransferChecked). This is how money is added to a
 *  Balance: `dest` is the Balance's token account. `owner` signs for `source`. */
export const transferCheckedIx = (source, mint, dest, owner, amount, decimals, tokenProgram = TOKEN) => ({ program: tokenProgram,
  data: cat(Uint8Array.of(12), u64(amount), Uint8Array.of(decimals)),
  accounts: [meta(source, false, true), meta(mint, false, false), meta(dest, false, true), meta(owner, true, false)] });

/** A mint account's decimals, supply and mint authority (the fixed fields SPL Token and Token-2022 share). */
export function readMint(raw) {
  if (!raw || raw.length < 82) return null;
  const dv = view(raw);
  return { decimals: raw[44], supply: num(dv.getBigUint64(36, true)), mintAuthority: dv.getUint32(0, true) === 1 ? b58(raw.slice(4, 36)) : null };
}

/** A token account's mint, owner and amount (smallest units). */
export function readTokenAccount(raw) {
  if (!raw || raw.length < 165) return null;
  return { mint: b58(raw.slice(0, 32)), owner: b58(raw.slice(32, 64)), amount: num(view(raw).getBigUint64(64, true)) };
}

// ---- the second deployment (programs-v2): knos.settle.v2.pay and knos.settle.v2.oidc ------------------------------
const HOLD = 180 * 86_400, MAX_TERMS = 600, KEY_DELAY = 86_400, KEY_TTL = 30 * 86_400, K_HDR = 40;
const STATES2 = { 1: "open", 3: "held" };
const ERRORS = {
  76: "the key that signed this token is not active on chain yet: it waits for its delay and the guardian's approval; try again after that",
  77: "the key that signed this token has expired on chain; run the rotate workflow and send Refresh for the key, then relay the token again while it is fresh",
  78: "the key that signed this token was revoked on chain, so the token can do nothing; run the workflow again for a token signed by another key",
  80: "a wrong account, or a missing signature",
  81: "the amount, the work time, the mode, the workflow commit, the terms or the pause length is outside what is allowed",
  82: "this issue already has a job from this funder, or the account is not a job",
  83: "too early or too late: the job is not in the state this needs",
  84: "the token is not verified, not GitHub's, expired, or its times are not GitHub's; a new run gives a new one",
  85: "the token's claims do not allow this: the event, the runner, the repository or its owner, or a re-run instead of a first run",
  86: "the token is not from the workflow this needs",
  87: "the token's audience does not match",
  88: "wrong destination, fee or refund account, or the payee has not bound a wallet yet",
  89: "only on a devnet build",
  90: "the faucet gives test USDC once per repository per minute, in the order GitHub issued the tokens; wait a minute and comment again",
  91: "this token is not newer than the last one used here, and a token works once; comment again for a new one",
  92: "this comment cannot spend that balance: only its repository owner and the spenders its wallet listed can",
  93: "more than that balance allows for one job",
  94: "the balance does not hold that much; add money to it or fund less",
  95: "this mint cannot be used: it is not a mint of the token program passed, or it can block or tax a transfer",
  96: "new funding is paused; payments, refunds, withdrawals and binds go on",
  97: "only the guardian can pause",
  98: "the balance exists already, is not a balance, or the signer is not the wallet that opened it",
  99: "a faucet balance holds test USDC and cannot be withdrawn",
};

function sorted(value) {
  if (Array.isArray(value)) return value.map(sorted);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((name) => [name, sorted(value[name])]));
  return value;
}

/** The canonical JSON of a job's terms, as bytes: keys sorted, no spaces, ASCII (anything else written as an escape,
 *  the way Python's json writes it). This is what the funding instruction carries and logs, and what termsHash hashes. */
function termsJson(terms) {
  const json = JSON.stringify(sorted(terms));
  let text = "";
  for (let i = 0; i < json.length; i++) {
    const code = json.charCodeAt(i);
    text += code < 127 ? json[i] : "\\u" + code.toString(16).padStart(4, "0");
  }
  if (text.length > MAX_TERMS) throw new Error(`the terms are ${text.length} bytes; a job takes at most ${MAX_TERMS}`);
  return enc.encode(text);
}

/** sha256 of the terms JSON bytes: what a job stores and what the fund and pay audiences carry. */
const termsHash = (terms) => sha256(bytesOf(terms));

/** A funder as the record counts it: the funding wallet (an address), or the GitHub owner id of a Balance. */
const funderKey = async (funder) => (typeof funder === "string" ? unb58(funder) : sha256(cat(enc.encode("gh"), u64(funder))));

/** `termsHex` is hex(termsHash(...)). `balance` is the Balance this comment spends: the funder's workflow chooses
 *  it, and the program spends no other with this token. */
const fundAudience2 = (issue, amount, mode, termsHex, balance, workS = 14 * 86400) =>
  `knos2:fund:${issue}:${amount}:${mode}:${termsHex}:${workS}:${balance}`;
/** The Balance a fund audience names. */
const namedBalance = (audience) => audience.split(":").at(-1);
/** `address`: where the payee asked to be paid in the pull request, or null (the job is then held for them unless
 *  they have bound a wallet). */
const payAudience2 = (repoId, issue, payeeId, headSha, termsHex, mode, address = null) =>
  `knos2:pay:${repoId}:${issue}:${payeeId}:${headSha}:${termsHex}:${mode}:${address ?? "-"}`;
const bindAudience = (address) => `knos2:bind:${address}`;

/** Where the program will pay a pay token: the payee's bound wallet, else the address the audience carries, else
 *  null (the job is held for the payee). `bind` is readBind of the account at k.bind(payee id). */
function destination(bind, audience) {
  if (bind) return bind.wallet;
  const address = audience.split(":").at(-1);
  return address === "-" ? null : address;
}

/** A job: one bounty on one issue. `funder` is who the record counts as its funder: the Balance's GitHub owner id,
 *  or the funding wallet. null for anything that is not a job account. */
function readJob(raw) {
  if (!raw || raw.length !== 320) return null;
  const dv = view(raw);
  const u = (o) => num(dv.getBigUint64(o, true)), i = (o) => num(dv.getBigInt64(o, true));
  const j = { state: STATES2[raw[0]] || "?", mode: raw[1], fromBalance: raw[2] === 1, tokenProgram: raw[6] === 1 ? TOKEN_2022 : TOKEN,
    faucet: raw[7] === 1, repoId: u(8), issue: u(16), amount: u(24), deadline: i(32), holdUntil: i(48), payeeId: u(56), funderId: u(64),
    notBefore: i(72), ownerId: u(80), source: b58(raw.slice(88, 120)), refundTo: b58(raw.slice(120, 152)), rentTo: b58(raw.slice(152, 184)),
    mint: b58(raw.slice(184, 216)), terms: hex(raw.slice(216, 248)), wfRepoHash: hex(raw.slice(248, 280)), wfSha: ascii(raw.slice(280, 320)) };
  j.funder = j.fromBalance ? j.ownerId : j.source;
  return j;
}

/** A Balance: money a wallet (`authority`) set aside for the repositories of one GitHub owner. `capPerJob` 0: no cap.
 *  `spenders`: the GitHub ids that may spend it by comment, besides the owner. `spent`: everything it ever put into jobs. */
function readBalance(raw) {
  if (!raw || raw.length !== 160 || raw[0] !== 1) return null;
  const dv = view(raw);
  const spenders = [0, 1, 2, 3].map((s) => num(dv.getBigUint64(96 + 8 * s, true))).filter((s) => s);
  return { faucet: raw[2] === 1, ownerId: num(dv.getBigUint64(8, true)), authority: b58(raw.slice(16, 48)), mint: b58(raw.slice(48, 80)),
    capPerJob: num(dv.getBigUint64(80, true)), lastIat: num(dv.getBigInt64(88, true)), spenders, spent: num(dv.getBigUint64(128, true)) };
}

/** The wallet a GitHub user is paid at. */
function readBind(raw) {
  if (!raw || raw.length !== 56 || raw[0] !== 1) return null;
  const dv = view(raw);
  return { userId: num(dv.getBigUint64(8, true)), wallet: b58(raw.slice(16, 48)), iat: num(dv.getBigInt64(48, true)) };
}

/** A GitHub user's public record. `paid`, `funders`, `total`: payments from someone else that were not faucet money,
 *  the distinct funders among them, and what they put in the wallet. `testPaid`, `testTotal`: payments in the
 *  faucet's test USDC. `selfPaid`: payments whose funder was the payee. `first`, `last`: times of the first and the
 *  latest payment counted in `paid` (0: none yet). */
function readRep(raw) {
  if (!raw || raw.length !== 64) return { paid: 0, funders: 0, total: 0, testPaid: 0, selfPaid: 0, testTotal: 0, first: 0, last: 0 };
  const dv = view(raw);
  return { paid: dv.getUint32(0, true), funders: dv.getUint32(4, true), total: num(dv.getBigUint64(8, true)), testPaid: dv.getUint32(16, true),
    selfPaid: dv.getUint32(20, true), testTotal: num(dv.getBigUint64(32, true)), first: num(dv.getBigInt64(40, true)),
    last: num(dv.getBigInt64(48, true)) };
}

/** The time until which new funding is refused (0: not paused, or never was). */
const readPause = (raw) => (raw && raw.length === 8 ? num(view(raw).getBigInt64(0, true)) : 0);

/** [when the devnet faucet last served a repository, that fund token's iat]; [0, 0] when it never did. */
const readRate = (raw) => (raw && raw.length === 16 ? [num(view(raw).getBigInt64(0, true)), num(view(raw).getBigInt64(8, true))] : [0, 0]);

/** A key account's header: { state (1: ready), issuer, bits, activeAt, expiresAt, approved, revoked, genesis }.
 *  null for anything that is not a key account of the second deployment. */
function readKey(raw) {
  if (!raw || raw.length < K_HDR || (raw[2] !== 64 && raw[2] !== 128) || raw.length !== K_HDR + 8 * raw[2]) return null;
  const dv = view(raw), flags = raw[24];
  return { state: raw[0], issuer: raw[1], bits: 32 * raw[2], activeAt: num(dv.getBigInt64(8, true)), expiresAt: num(dv.getBigInt64(16, true)),
    approved: !!(flags & 1), revoked: !!(flags & 2), genesis: !!(flags & 4) };
}

/** sha256 of the modulus a key account holds, as hex: what names the key (the account stores it as 32-bit limbs,
 *  lowest first). */
async function keyAccountHash(raw) {
  const size = 4 * raw[2];
  return hex(await sha256(raw.slice(K_HDR, K_HDR + size).reverse()));
}

const pad = (v) => String(v).padStart(2, "0");
function when(t) {
  if (!(t >= -62135596800 && t <= 253402300799)) return `time ${t}`;    // the years a date can name: 1 to 9999
  const d = new Date(t * 1000);
  return `${String(d.getUTCFullYear()).padStart(4, "0")}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

/** [whether Step accepts this key at `now` (the chain's clock), and if not, why and what to do]. `k` is readKey's. */
function keyUsable(k, now) {
  if (!k) return [false, "this signing key is not on chain yet. Send RegisterKey with an attestation from the rotate workflow."];
  if (k.state !== 1) return [false, "this signing key is registered but not ready. Send KeyParams for it (anyone can)."];
  if (k.revoked) return [false, "the guardian revoked this signing key. It cannot be used again."];
  if (!(k.genesis || k.approved)) {
    const wait = now < k.activeAt ? ` Its ${KEY_DELAY / 3600}-hour wait ends ${when(k.activeAt)}.` : "";
    return [false, `this signing key is new and the guardian has not approved it yet.${wait} Try again after the approval.`];
  }
  if (now < k.activeAt) return [false, `this signing key is new: it can be used from ${when(k.activeAt)}. Try again then.`];
  if (now >= k.expiresAt) {
    return [false, `this signing key expired ${when(k.expiresAt)}: nothing attested it for ${KEY_TTL / 86_400} days. `
      + "Run the rotate workflow and send Refresh with its token."];
  }
  return [true, ""];
}

// the accounts of each knos-pay instruction, in order, under the names idl/knos_pay_v2.json gives them
const PAYOUT = ["job", "bind", "destToken", "rep", "pair", "vault", "feeToken", "auth", "rentTo", "mint", "tokenProgram", "systemProgram"];
const PAY_IXS = [
  ["OpenBalance", ["authority", "balance", "baltok", "mint", "auth", "tokenProgram", "systemProgram"]],
  ["SetBalance", ["authority", "balance"]],
  ["Withdraw", ["authority", "balance", "baltok", "destToken", "mint", "auth", "tokenProgram"]],
  ["FundBalance", ["relayer", "fundToken", "key", "balance", "baltok", "job", "vault", "mint", "auth", "tokenProgram", "systemProgram", "pause"]],
  ["FundWallet", ["funder", "job", "funderToken", "vault", "mint", "auth", "tokenProgram", "systemProgram", "pause"]],
  ["Pay", ["relayer", "payToken", "key", ...PAYOUT]],
  ["Settle", ["relayer", ...PAYOUT]],
  ["Refund", ["relayer", "job", "vault", "refundToken", "auth", "rentTo", "mint", "tokenProgram"]],
  ["Bind", ["relayer", "bindToken", "key", "bind", "systemProgram"]],
  ["Pause", ["guardian", "payer", "pause", "systemProgram"]],
  ["InitFaucet", ["payer", "mint", "auth", "tokenProgram", "systemProgram"]],
  ["FaucetOpen", ["relayer", "fundToken", "key", "balance", "baltok", "mint", "auth", "tokenProgram", "systemProgram", "rate"]],
];

function limits(cap, spenders) {
  const ids = [...spenders];
  if (ids.length > 4) throw new Error("a balance has at most 4 spenders");
  while (ids.length < 4) ids.push(0);
  return cat(u64(cap), ...ids.map(u64));
}

/** A client bound to the second deployment's ids (src/knos/settle/v2/program_ids.json). Every address and
 *  instruction is derived, nothing is fetched. Ids and amounts are numbers or BigInts; addresses are base58. */
function client2(ids) {
  const PAY = ids.knos_pay;
  const pda = async (...seeds) => (await findProgramAddress(seeds, PAY))[0];
  const tag = (s) => enc.encode(s);
  const k = {
    ids,
    oidc: verifier(ids.knos_oidc),
    /** The owner of every vault and of every Balance's token account. Only the program signs for it. */
    auth: () => pda(tag("auth")),
    vault: (mint) => pda(tag("vault"), key(mint)),
    /** The Balance a wallet (`authority`) opened for the repositories of the GitHub owner `ownerId`, in one mint. */
    balance: (ownerId, authority, mint) => pda(tag("bal"), u64(ownerId), key(authority), key(mint)),
    /** The devnet test-USDC mint (SPL Token, 6 decimals); its mint authority is the program. */
    faucetMint: () => pda(tag("mint")),
    /** The devnet faucet's Balance of a GitHub owner: test USDC that fund tokens of that owner's repositories spend. */
    faucetBalance: async (ownerId) => k.balance(ownerId, await k.auth(), await k.faucetMint()),
    /** A Balance's token account. Add money to a Balance with a plain token transfer to this address. */
    baltok: (balance) => pda(tag("baltok"), key(balance)),
    /** A job's address. `source` is the Balance it is funded from, or the funding wallet. */
    job: (repoId, issue, source) => pda(tag("job"), u64(repoId), u64(issue), key(source)),
    bind: (userId) => pda(tag("bind"), u64(userId)),
    rep: (userId) => pda(tag("rep"), u64(userId)),
    /** Exists once this funder (a wallet's address, or the GitHub owner id of a Balance) has paid this payee. */
    pair: async (payeeId, funder) => pda(tag("pair"), u64(payeeId), await funderKey(funder)),
    rate: (repoId) => pda(tag("rate"), u64(repoId)),
    pause: () => pda(tag("pause")),

    /** Opens `authority`'s Balance for the repositories of the GitHub owner `ownerId`. `cap`: the most one job may
     *  take (0: no cap). `spenders`: up to 4 GitHub ids that may spend it by comment, besides the owner. */
    async openBalanceIx({ authority, ownerId, mint, cap = 0, spenders = [], tokenProgram = TOKEN }) {
      const balance = await k.balance(ownerId, authority, mint);
      return { program: PAY, data: cat(Uint8Array.of(0), u64(ownerId), limits(cap, spenders)),
        accounts: [meta(authority, true, true), meta(balance, false, true), meta(await k.baltok(balance), false, true), meta(mint, false, false),
          meta(await k.auth(), false, false), meta(tokenProgram, false, false), meta(SYSTEM, false, false)] };
    },
    setBalanceIx: ({ authority, balance, cap = 0, spenders = [] }) => ({ program: PAY, data: cat(Uint8Array.of(1), limits(cap, spenders)),
      accounts: [meta(authority, true, false), meta(balance, false, true)] }),
    /** Unspent money back to the wallet that opened the Balance. `amount` 0: everything. `destToken`: a token account
     *  of that wallet (default: its associated token account). */
    async withdrawIx({ authority, balance, mint, amount = 0, destToken = null, tokenProgram = TOKEN }) {
      return { program: PAY, data: cat(Uint8Array.of(2), u64(amount)),
        accounts: [meta(authority, true, false), meta(balance, false, false), meta(await k.baltok(balance), false, true),
          meta(destToken ?? await ata(authority, mint, tokenProgram), false, true), meta(mint, false, false), meta(await k.auth(), false, false),
          meta(tokenProgram, false, false)] };
    },
    /** Funds the job a fund token describes from the Balance its audience names. `key` is the verifier's account of
     *  the key that verified the token. `terms` is the terms JSON (bytes, or the text) whose hash the audience carries. */
    async fundBalanceIx({ relayer, fundToken, key: keyAccount, balance, mint, repoId, issue, terms, tokenProgram = TOKEN }) {
      return { program: PAY, data: cat(Uint8Array.of(3), bytesOf(terms)),
        accounts: [meta(relayer, true, true), meta(fundToken, false, false), meta(keyAccount, false, false), meta(balance, false, true),
          meta(await k.baltok(balance), false, true), meta(await k.job(repoId, issue, balance), false, true), meta(await k.vault(mint), false, true),
          meta(mint, false, false), meta(await k.auth(), false, false), meta(tokenProgram, false, false), meta(SYSTEM, false, false),
          meta(await k.pause(), false, false)] };
    },
    /** A wallet funds a job with its own money. `wfRepo` ("owner/name") and `wfSha` pin the prove.yml that can prove it. */
    async fundWalletIx({ funder, funderToken, mint, repoId, issue, amount, wfRepo, wfSha, terms, mode = MERGE, workS = 14 * 86400,
      tokenProgram = TOKEN }) {
      if (!/^[0-9a-f]{40}$/.test(wfSha)) throw new Error("wfSha must be a full commit sha");
      const data = cat(Uint8Array.of(4), u64(repoId), u64(issue), u64(amount), i64(workS), Uint8Array.of(mode), await wfRepoHash(wfRepo),
        enc.encode(wfSha), bytesOf(terms));
      return { program: PAY, data, accounts: [meta(funder, true, true), meta(await k.job(repoId, issue, funder), false, true),
        meta(funderToken, false, true), meta(await k.vault(mint), false, true), meta(mint, false, false), meta(await k.auth(), false, false),
        meta(tokenProgram, false, false), meta(SYSTEM, false, false), meta(await k.pause(), false, false)] };
    },
    async payoutAccounts(job, j, payeeId, wallet, destToken) {
      const vault = await k.vault(j.mint);
      // a job that will be held names no destination: the vault stands in, and the program does not touch it
      const dest = destToken ?? (wallet ? await ata(wallet, j.mint, j.tokenProgram) : vault);
      return [meta(job, false, true), meta(await k.bind(payeeId), false, false), meta(dest, false, true), meta(await k.rep(payeeId), false, true),
        meta(await k.pair(payeeId, j.funder), false, true), meta(vault, false, true), meta(await ata(FEE_OWNER, j.mint, j.tokenProgram), false, true),
        meta(await k.auth(), false, false), meta(j.rentTo, false, true), meta(j.mint, false, false), meta(j.tokenProgram, false, false),
        meta(SYSTEM, false, false)];
    },
    /** `j` is readJob of `job`. `wallet` is destination(readBind(...), audience): the program pays a token account of
     *  that wallet (default: its associated token account; create it and FEE_OWNER's first), or holds the job when
     *  it is null. */
    payIx: async ({ relayer, payToken, key: keyAccount, job, j, payeeId, wallet, destToken = null }) => ({ program: PAY, data: Uint8Array.of(5),
      accounts: [meta(relayer, true, true), meta(payToken, false, false), meta(keyAccount, false, false),
        ...await k.payoutAccounts(job, j, payeeId, wallet, destToken)] }),
    /** Pays a held job once its payee has bound a wallet: `wallet` is readBind(k.bind(j.payeeId)).wallet. */
    settleIx: async ({ relayer, job, j, wallet, destToken = null }) => ({ program: PAY, data: Uint8Array.of(6),
      accounts: [meta(relayer, true, true), ...await k.payoutAccounts(job, j, j.payeeId, wallet, destToken)] }),
    /** An open job past its deadline, or a held job past its hold: the money back to the Balance it came from, or to
     *  a token account of the funding wallet (default: its associated token account). */
    async refundIx({ relayer, job, j, refundToken = null }) {
      const dest = refundToken ?? (j.fromBalance ? j.refundTo : await ata(j.refundTo, j.mint, j.tokenProgram));
      return { program: PAY, data: Uint8Array.of(7), accounts: [meta(relayer, true, true), meta(job, false, true), meta(await k.vault(j.mint), false, true),
        meta(dest, false, true), meta(await k.auth(), false, false), meta(j.rentTo, false, true), meta(j.mint, false, false),
        meta(j.tokenProgram, false, false)] };
    },
    /** `userId` is the token's actor_id. */
    bindIx: async ({ relayer, bindToken, key: keyAccount, userId }) => ({ program: PAY, data: Uint8Array.of(8),
      accounts: [meta(relayer, true, true), meta(bindToken, false, false), meta(keyAccount, false, false), meta(await k.bind(userId), false, true),
        meta(SYSTEM, false, false)] }),
    /** The guardian refuses new funding for `seconds` (at most 7 days) from now; 0 lifts the pause. */
    pauseIx: async ({ guardian, payer, seconds }) => ({ program: PAY, data: cat(Uint8Array.of(9), u32(seconds)),
      accounts: [meta(guardian, true, false), meta(payer, true, true), meta(await k.pause(), false, true), meta(SYSTEM, false, false)] }),
    initFaucetIx: async ({ payer }) => ({ program: PAY, data: Uint8Array.of(10), accounts: [meta(payer, true, true),
      meta(await k.faucetMint(), false, true), meta(await k.auth(), false, false), meta(TOKEN, false, false), meta(SYSTEM, false, false)] }),
    /** Devnet: mints the fund token's amount of test USDC into the faucet Balance of the token's repository owner,
     *  which the token's audience must name. Send fundBalanceIx on that Balance after it, in the same transaction. */
    async faucetOpenIx({ relayer, fundToken, key: keyAccount, ownerId, repoId }) {
      const balance = await k.faucetBalance(ownerId);
      return { program: PAY, data: Uint8Array.of(11), accounts: [meta(relayer, true, true), meta(fundToken, false, false), meta(keyAccount, false, false),
        meta(balance, false, true), meta(await k.baltok(balance), false, true), meta(await k.faucetMint(), false, true),
        meta(await k.auth(), false, false), meta(TOKEN, false, false), meta(SYSTEM, false, false), meta(await k.rate(repoId), false, true)] };
    },

    /** What an instruction is, read back from its own bytes, for a person to check before signing: { program (a
     *  name), address, name, args, accounts: [{ name, pubkey, signer, writable }] }. Amounts are in the mint's
     *  smallest units. Knows knos-pay, a token transfer and the creation of a token account; anything else comes
     *  back named "unknown" with its data in hex. */
    explain(ix) {
      const d = ix.data, dv = view(d);
      const named = (names) => ix.accounts.map((a, i) => ({ name: names[i] ?? `account ${i}`, ...a }));
      const u = (o) => num(dv.getBigUint64(o, true));
      const four = (o) => [0, 1, 2, 3].map((s) => u(o + 8 * s)).filter((s) => s);
      const out = (program, name, args, names) => ({ program, address: ix.program, name, args, accounts: named(names) });
      if (ix.program === PAY && d.length && d[0] < PAY_IXS.length) {
        const [name, names] = PAY_IXS[d[0]];
        const args = name === "OpenBalance" ? { ownerId: u(1), cap: u(9), spenders: four(17) }
          : name === "SetBalance" ? { cap: u(1), spenders: four(9) }
          : name === "Withdraw" ? { amount: u(1) }
          : name === "FundBalance" ? { terms: ascii(d.slice(1)) }
          : name === "FundWallet" ? { repoId: u(1), issue: u(9), amount: u(17), workS: num(dv.getBigInt64(25, true)), mode: d[33],
            wfRepoHash: hex(d.slice(34, 66)), wfSha: ascii(d.slice(66, 106)), terms: ascii(d.slice(106)) }
          : name === "Pause" ? { seconds: dv.getUint32(1, true) } : {};
        return out("knos-pay", name, args, names);
      }
      if ((ix.program === TOKEN || ix.program === TOKEN_2022) && d[0] === 12 && d.length === 10) {
        return out(ix.program === TOKEN ? "SPL Token" : "Token-2022", "TransferChecked", { amount: u(1), decimals: d[9] }, ["source", "mint", "destination", "owner"]);
      }
      if (ix.program === ATA_PROGRAM && d.length === 1 && d[0] === 1) {
        return out("Associated Token Account", "CreateIdempotent", {}, ["payer", "account", "owner", "mint", "systemProgram", "tokenProgram"]);
      }
      return out(ix.program === ids.knos_oidc ? "knos-oidc" : ix.program, "unknown", { data: hex(d) }, []);
    },
  };
  return k;
}

/** What a failed transaction's error means, in words, when knos-pay refused it: `err` as the cluster reports it
 *  ({ InstructionError: [index, { Custom: 93 }] }). null when it is not one of the program's own codes. */
function errorWords(err) {
  const custom = err?.InstructionError?.[1]?.Custom;
  return custom in ERRORS ? ERRORS[custom] : null;
}

export const v2 = Object.freeze({
  MERGE, TESTS, FEE_BPS, FEE_MIN, MIN_AMOUNT, MAX_AMOUNT, FAUCET_CAP: 100_000_000, MIN_WORK: 60, MAX_WORK: 90 * 86_400, HOLD, PAUSE_MAX: 7 * 86_400,
  FUND_PERIOD: 60, CLOCK_SLACK: 30, MAX_TERMS, TOKEN_AHEAD: 300, TOKEN_LIFE: 3600, JOB_LEN: 320, BALANCE_LEN: 160, BIND_LEN: 56, REP_LEN: 64,
  KEY_DELAY, KEY_TTL, K_HDR, ERRORS, PAY_IXS,
  feeOf, termsJson, termsHash, wfRepoHash, funderKey, fundAudience: fundAudience2, namedBalance, payAudience: payAudience2, bindAudience, destination,
  readJob, readBalance, readBind, readRep, readPause, readRate, readKey, keyAccountHash, keyUsable, errorWords, client: client2,
});

// ---- an unsigned legacy transaction, for a wallet to sign and send -------------------------------------------------
function shortvec(n) {
  const out = [];
  for (;;) { const b = n & 0x7f; n >>= 7; if (n) out.push(b | 0x80); else { out.push(b); return Uint8Array.from(out); } }
}

function byBytes(a, b) {
  for (let i = 0; i < 32; i++) if (a.raw[i] !== b.raw[i]) return a.raw[i] - b.raw[i];
  return 0;
}

/** The legacy message of a transaction: the header, every account once (the fee payer first, then the other
 *  signers, then the rest; in each group the writable ones first, in the order of their bytes, as the Solana SDK
 *  compiles it), the recent blockhash, and the instructions by index. */
export function serializeMessage(ixs, payer, recentBlockhash) {
  const seen = new Map();     // pubkey -> { signer, writable }
  const note = (pubkey, signer, writable) => {
    const have = seen.get(pubkey) || { raw: key(pubkey), signer: false, writable: false };
    seen.set(pubkey, { raw: have.raw, signer: have.signer || signer, writable: have.writable || writable });
  };
  for (const ix of ixs) {
    note(ix.program, false, false);
    for (const a of ix.accounts) note(a.pubkey, a.signer, a.writable);
  }
  seen.delete(payer);
  const rest = [...seen.entries()].map(([pubkey, v]) => ({ pubkey, ...v })).sort(byBytes);
  const group = (signer, writable) => rest.filter((a) => a.signer === signer && a.writable === writable);
  const keys = [{ pubkey: payer, raw: key(payer), signer: true, writable: true }, ...group(true, true), ...group(true, false),
    ...group(false, true), ...group(false, false)];
  if (keys.length > 255) throw new Error("too many accounts for one transaction");
  const names = keys.map((a) => a.pubkey);
  const count = (f) => keys.filter(f).length;
  const header = Uint8Array.of(count((a) => a.signer), count((a) => a.signer && !a.writable), count((a) => !a.signer && !a.writable));
  const body = ixs.map((ix) => cat(Uint8Array.of(names.indexOf(ix.program)), shortvec(ix.accounts.length),
    Uint8Array.from(ix.accounts.map((a) => names.indexOf(a.pubkey))), shortvec(ix.data.length), ix.data));
  return cat(header, shortvec(keys.length), ...keys.map((a) => a.raw), key(recentBlockhash), shortvec(ixs.length), ...body);
}

/** The unsigned transaction: one empty signature for each signer, then the message. At most 1232 bytes fit. */
export function serializeTx(ixs, payer, recentBlockhash) {
  const message = serializeMessage(ixs, payer, recentBlockhash);
  return cat(shortvec(message[0]), new Uint8Array(64 * message[0]), message);
}

/** The message inside a transaction serializeTx made (the part that is signed). */
export function messageOf(tx) {
  const signatures = tx[0];        // one byte: a transaction never has 128 signers
  return tx.slice(1 + 64 * signatures);
}

// ---- wallets: ask an injected wallet to sign and send ---------------------------------------------------------------
const registries = new WeakMap();     // window -> the wallets that registered through the Wallet Standard

// The Wallet Standard's handshake, both ways round: a wallet announces itself with a "register-wallet" event whose
// detail is a function that takes our { register }, and listens for our "app-ready" event, whose detail is that
// same { register }. So a wallet is found whether it loaded before this page's script or after.
function standardWallets(win) {
  if (!registries.has(win)) {
    const found = [];
    registries.set(win, found);
    const api = Object.freeze({ register: (...list) => { for (const w of list) if (!found.includes(w)) found.push(w); return () => {}; } });
    try {
      win.addEventListener("wallet-standard:register-wallet", (ev) => ev.detail(api));
      win.dispatchEvent(new win.CustomEvent("wallet-standard:app-ready", { detail: api }));
    } catch { /* no events here: not a browser window */ }
  }
  return registries.get(win).filter((w) => (w.chains || []).some((c) => c.startsWith("solana:"))
    && w.features?.["standard:connect"] && w.features?.["solana:signAndSendTransaction"]);
}

const text = (v) => (typeof v === "string" ? v : v?.toBase58 ? v.toBase58() : String(v));
const signatureOf = (got) => {
  const s = got?.signature ?? got;
  return typeof s === "string" ? s : b58(s);
};

/** The wallets this page can ask, in the order it prefers them: every wallet that registered through the Wallet
 *  Standard and can sign and send on Solana, then Phantom's and Solflare's own injected providers when they did not
 *  register that way. Each is { name, kind, connect(), signAndSend(tx, chain) }:
 *
 *    connect()                 asks the wallet for an account; resolves to its address
 *    signAndSend(tx, chain)    `tx` is serializeTx's bytes; the wallet shows it, signs and sends it; resolves to the
 *                              transaction's signature (base58). `chain`: "solana:devnet" or "solana:mainnet". An
 *                              injected provider sends to the network the wallet itself is set to.
 *
 *  Call it once when the page loads (wallets that load later are then heard) and again when the person asks to connect. */
export function wallets(win = globalThis.window) {
  if (!win) return [];
  const out = standardWallets(win).map((w) => {
    let account = null;
    return { name: w.name, kind: "standard", icon: w.icon,
      async connect(chain = "solana:devnet") {
        const got = await w.features["standard:connect"].connect();
        const accounts = got?.accounts?.length ? got.accounts : w.accounts || [];
        if (!accounts.length) throw new Error(`${w.name} shared no account.`);
        // a wallet or an account that lists its chains and not this one cannot sign for it: said now, not after a prompt
        const net = chain.replace("solana:", "");
        account = (w.chains || []).includes(chain) ? accounts.find((a) => !a.chains?.length || a.chains.includes(chain)) : null;
        if (!account) throw new Error(`${w.name} has no ${net} account to share. Set it to ${net} and connect again.`);
        return account.address;
      },
      async signAndSend(tx, chain = "solana:devnet") {
        if (!account) await this.connect(chain);
        const [got] = await w.features["solana:signAndSendTransaction"].signAndSendTransaction({ account, transaction: tx, chain });
        return signatureOf(got);
      } };
  });
  const has = (name) => out.some((w) => w.name.toLowerCase().includes(name));
  const phantom = win.phantom?.solana?.isPhantom ? win.phantom.solana : win.solana?.isPhantom ? win.solana : null;
  if (phantom && !has("phantom")) {
    out.push({ name: "Phantom", kind: "phantom",
      connect: async () => text((await phantom.connect())?.publicKey ?? phantom.publicKey),
      // Phantom's own request takes the message in base58 and needs no transaction object
      signAndSend: async (tx) => signatureOf(await phantom.request({ method: "signAndSendTransaction", params: { message: b58(messageOf(tx)) } })) });
  }
  const solflare = win.solflare?.isSolflare ? win.solflare : null;
  if (solflare && !has("solflare")) {
    out.push({ name: "Solflare", kind: "solflare",
      async connect() { await solflare.connect(); return text(solflare.publicKey); },
      // Solflare's provider serialises what it is given: an object that answers serialize() with the wire bytes
      signAndSend: async (tx) => signatureOf(await solflare.signAndSendTransaction({ serialize: () => tx, serializeMessage: () => messageOf(tx),
        signatures: [] })) });
  }
  return out;
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

/** An account's data, or null when it does not exist. */
export async function account(url, address) {
  const got = await rpc(url, "getAccountInfo", [address, { encoding: "base64", commitment: "confirmed" }]);
  return got.value ? fromB64(got.value.data[0]) : null;
}

/** An account's { owner, data, lamports }, or null when it does not exist. */
export async function accountInfo(url, address) {
  const got = await rpc(url, "getAccountInfo", [address, { encoding: "base64", commitment: "confirmed" }]);
  return got.value ? { owner: got.value.owner, data: fromB64(got.value.data[0]), lamports: got.value.lamports } : null;
}

/** Several accounts in one call: [{ owner, data, lamports } or null], in the order asked. */
export async function accounts(url, addresses) {
  const got = await rpc(url, "getMultipleAccounts", [addresses, { encoding: "base64", commitment: "confirmed" }]);
  return got.value.map((v) => (v ? { owner: v.owner, data: fromB64(v.data[0]), lamports: v.lamports } : null));
}

/** The chain's clock: the time of the latest confirmed block (what the programs see), never the local clock. */
export async function chainTime(url) {
  const slot = await rpc(url, "getSlot", [{ commitment: "confirmed" }]);
  for (const s of [slot, slot - 1, slot - 2]) {
    const t = await rpc(url, "getBlockTime", [s]).catch(() => null);
    if (t) return t;
  }
  throw new Error("the cluster gave no block time");
}

/** Every account of `program` with this size whose bytes at `offset` equal `bytes`: [{ address, data }]. */
export async function programAccounts(url, program, size, offset = null, bytes = null) {
  const filters = [{ dataSize: size }];
  if (bytes) filters.push({ memcmp: { offset, bytes: b58(bytes) } });
  const got = await rpc(url, "getProgramAccounts", [program, { encoding: "base64", commitment: "confirmed", filters }]);
  return (got || []).map((x) => ({ address: x.pubkey, data: fromB64(x.account.data[0]) }));
}

/** Waits until the cluster has confirmed a transaction: { ok, err } (err as the cluster reports it), or null when
 *  it was not seen within `seconds`. */
export async function confirmed(url, signature, seconds = 60, pause = (ms) => new Promise((r) => setTimeout(r, ms))) {
  for (let waited = 0; waited < seconds * 1000; waited += 1500) {
    const got = (await rpc(url, "getSignatureStatuses", [[signature], { searchTransactionHistory: true }])).value[0];
    if (got && (got.err || got.confirmationStatus === "confirmed" || got.confirmationStatus === "finalized")) return { ok: !got.err, err: got.err || null };
    await pause(1500);
  }
  return null;
}

export { u64 as u64le };
