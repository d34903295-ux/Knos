// node sdk/settle/test.mjs
// The JavaScript client against the fixtures the Python client wrote: every address, audience, fee, instruction,
// account reader and transaction must match byte for byte, for both deployments. Then what the client adds for a
// browser: the wallets it can ask, with stand-ins for each kind. Exit 1 on the first mismatch.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as knos from "./index.js";

const here = dirname(fileURLToPath(import.meta.url));
const fx = JSON.parse(readFileSync(join(here, "fixtures.json"), "utf8"));
const i = fx.inputs, k = knos.client(fx.programs);
let n = 0;

function same(name, got, want) {
  n++;
  const text = (v) => JSON.stringify(v, (_k, x) => (typeof x === "bigint" ? `${x}n` : x));
  if (text(got) !== text(want)) {
    console.error(`MISMATCH ${name}\n  got  ${text(got)}\n  want ${text(want)}`);
    process.exit(1);
  }
}
const plain = (ix) => ({ program: ix.program, data: knos.hex(ix.data),
  accounts: ix.accounts.map((a) => ({ pubkey: a.pubkey, signer: a.signer, writable: a.writable })) });
const throws = async (name, f) => { let threw = false; try { await f(); } catch { threw = true; } same(name, threw, true); };

// ==== the first deployment ==========================================================================================
// base58 and the curve check
same("b58 round trip", knos.b58(knos.unb58(i.funder)), i.funder);
same("system program", knos.b58(new Uint8Array(32)), knos.SYSTEM);

// addresses
same("auth", await k.auth(), fx.addresses.auth);
same("vault", await k.vault(i.mint), fx.addresses["vault(mint)"]);
same("faucet mint", await k.faucetMint(), fx.addresses.faucet_mint);
same("job (own)", await k.job(i.repo_id, i.issue), fx.addresses["job(repo, issue) own"]);
same("job (wallet)", await k.job(i.repo_id, i.issue, i.funder), fx.addresses["job(repo, issue, funder)"]);
same("due", await k.due(i.author_id, i.mint), fx.addresses["due(author, mint)"]);
same("rep", await k.rep(i.author_id), fx.addresses["rep(author)"]);
same("rate", await k.rate(i.repo_id), fx.addresses["rate(repo)"]);
same("ata", await knos.ata(i.address, i.mint), fx.addresses["ata(address, mint)"]);
same("fee account", await knos.ata(knos.FEE_OWNER, i.mint), fx.addresses["fee_account(mint)"]);

// audiences, the repository hash, fees
same("fund merge", knos.fundAudience(i.issue, i.amount), fx.audiences["fund merge"]);
same("fund tests", knos.fundAudience(i.issue, i.amount, knos.TESTS, i.checks, 14 * 86400, 86400), fx.audiences["fund tests"]);
same("pay merge", knos.payAudience(i.repo_id, i.issue, i.author_id, "a".repeat(40)), fx.audiences["pay merge"]);
same("pay tests", knos.payAudience(i.repo_id, i.issue, i.author_id, "a".repeat(40), i.checks, knos.TESTS), fx.audiences["pay tests"]);
same("veto", knos.vetoAudience(i.repo_id, i.issue), fx.audiences.veto);
same("claim", knos.claimAudience(i.address), fx.audiences.claim);
same("wf repo hash", knos.hex(await knos.wfRepoHash(i.wf_repo)), fx.wf_repo_hash);
for (const [amount, fee] of Object.entries(fx.fees)) same(`fee of ${amount}`, knos.feeOf(Number(amount)), fee);

// instructions
const base = { funder: i.funder, funderToken: i.funder_token, mint: i.mint, repoId: i.repo_id, issue: i.issue,
  amount: i.amount, wfRepo: i.wf_repo, wfSha: i.wf_sha };
same("fund merge ix", plain(await k.fundIx(base)), fx.instructions["pay.fund merge"]);
same("fund tests ix", plain(await k.fundIx({ ...base, mode: knos.TESTS, checksHex: i.checks, workS: 7 * 86400, reviewS: 86400 })),
  fx.instructions["pay.fund tests"]);
same("veto ix", plain(k.vetoIx(i.funder, fx.addresses["job(repo, issue, funder)"])), fx.instructions["pay.veto (wallet)"]);
same("create ata ix", plain(await knos.createAtaIx(i.relayer, i.address, i.mint)), fx.instructions.create_ata);

// the verifier's instructions (the same five in both deployments), at the first deployment's address
const tid1 = knos.unhex(i.token_id), n1 = BigInt(i.n2048);
same("token id is 32 bytes", (await knos.tokenId(i.jwt)).length, 32);
same("v1 token account", await k.oidc.tokenPda(i.payer, tid1), fx.addresses["token(payer, token_id)"]);
same("v1 key account", await k.oidc.keyPda(knos.GITHUB, n1), fx.addresses["key(github, n2048)"]);
same("v1 rotate audience", await knos.rotateAudience(knos.GITHUB, n1), fx.audiences.rotate);
const w1 = await k.oidc.writeIxs(i.payer, tid1, i.jwt);
same("v1 write, first chunk", plain(w1[0]), fx.instructions["oidc.write (first chunk)"]);
same("v1 write, last chunk", plain(w1.at(-1)), fx.instructions["oidc.write (last chunk)"]);
same("v1 step", plain(await k.oidc.stepIx(i.payer, tid1, fx.addresses["key(github, n2048)"], 8)), fx.instructions["oidc.step"]);
same("v1 close", plain(await k.oidc.closeIx(i.payer, tid1)), fx.instructions["oidc.close"]);
same("v1 register key", plain(await k.oidc.registerKeyIx(i.payer, knos.GITHUB, n1)), fx.instructions["oidc.register_key"]);
same("v1 register key, attested", plain(await k.oidc.registerKeyIx(i.payer, knos.GITHUB, n1, i.token_account)), fx.instructions["oidc.register_key (attested)"]);
same("v1 key params", plain(await k.oidc.keyParamsIx(i.payer, knos.GITHUB, n1)), fx.instructions["oidc.key_params"]);

// account layouts
const job = new Uint8Array(256);
const dv = new DataView(job.buffer);
job[0] = 2; job[1] = 1; job[2] = 1;
dv.setBigUint64(8, BigInt(i.repo_id), true); dv.setBigUint64(16, BigInt(i.issue), true); dv.setBigUint64(24, BigInt(i.amount), true);
dv.setBigInt64(48, 1790000000n, true); dv.setBigUint64(56, BigInt(i.author_id), true);
job.set(knos.unb58(i.funder), 88); job.set(knos.unb58(i.mint), 120); job.set(new TextEncoder().encode(i.wf_sha), 216);
const parsed = knos.parseJob(job);
same("job layout", [parsed.state, parsed.mode, parsed.tokenFunded, parsed.repoId, parsed.issue, parsed.amount, parsed.payAfter,
  parsed.authorId, parsed.funder, parsed.mint, parsed.wfSha], ["proven", 1, true, i.repo_id, i.issue, i.amount, 1790000000,
  i.author_id, i.funder, i.mint, i.wf_sha]);
same("job of the wrong size", knos.parseJob(new Uint8Array(255)), null);
const due = new Uint8Array(48);
new DataView(due.buffer).setBigUint64(0, 4875000n, true); new DataView(due.buffer).setBigUint64(8, BigInt(i.author_id), true);
due.set(knos.unb58(i.mint), 16);
same("due layout", knos.parseDue(due), { amount: 4875000, userId: i.author_id, mint: i.mint });
const pd = new Uint8Array(60); pd[0] = 3;
same("immutable program", knos.upgradeAuthority(pd), null);
pd[12] = 1; pd.set(knos.unb58(i.funder), 13);
same("upgradeable program", knos.upgradeAuthority(pd), i.funder);
same("not a program-data account", knos.upgradeAuthority(new Uint8Array(60)), undefined);
same("no account at all", knos.upgradeAuthority(null), undefined);

// the transaction a wallet signs: header, the payer first, then the accounts of each kind in the order of their bytes
const tx = knos.serializeTx([await knos.createAtaIx(i.funder, i.funder, i.mint), await k.fundIx(base)], i.funder, i.mint);
same("tx: one empty signature", [tx[0], knos.hex(tx.slice(1, 65))], [1, "00".repeat(64)]);
same("tx: header", [...tx.slice(65, 68)], [1, 0, 6]);
same("tx: accounts", tx[68], 11);
same("tx: payer first", knos.b58(tx.slice(69, 101)), i.funder);
if (process.argv[2]) writeFileSync(process.argv[2], Buffer.from(tx));
const first = n;

// ==== the second deployment =========================================================================================
const s = fx.second, j = s.inputs, v2 = knos.v2, k2 = v2.client(s.programs);
const tid = knos.unhex(j.token_id), n2048 = BigInt(j.n2048), n4096 = BigInt(j.n4096);
const terms = v2.termsJson(j.terms), termsTests = v2.termsJson(j.terms_tests);
const th = knos.hex(await v2.termsHash(terms)), thTests = knos.hex(await v2.termsHash(termsTests));

// constants, error words, issuers
for (const [name, want] of Object.entries(s.constants)) same(`constant ${name}`, name in v2 ? v2[name] : knos[name], want);
same("the words for every error code", Object.fromEntries(Object.entries(v2.ERRORS).map(([c, w]) => [String(c), w])), s.errors);
same("what a refusal means", v2.errorWords({ InstructionError: [1, { Custom: 93 }] }), s.errors["93"]);
same("an error that is not the program's has no words", [v2.errorWords({ InstructionError: [0, "InvalidArgument"] }), v2.errorWords(null)], [null, null]);
for (const id of Object.keys(s.issuers)) same(`issuer ${id}`, { issuer: knos.ISSUERS[id], jwks: knos.JWKS[id] }, s.issuers[id]);

// addresses
const bal = await k2.balance(j.owner_id, j.authority, j.mint), bal22 = await k2.balance(j.owner_id, j.authority, j.mint22);
const faucetBal = await k2.faucetBalance(j.owner_id);
const jobBal = await k2.job(j.repo_id, j.issue, bal), jobWallet = await k2.job(j.repo_id, j.issue, j.funder);
const key2048 = await k2.oidc.keyPda(knos.GITHUB, n2048), key4096 = await k2.oidc.keyPda(knos.GITLAB, n4096);
const addresses = {
  auth: await k2.auth(), "vault(mint)": await k2.vault(j.mint), faucet_mint: await k2.faucetMint(), "balance(owner, authority, mint)": bal,
  "faucet_balance(owner)": faucetBal, "baltok(balance)": await k2.baltok(bal), "job(repo, issue, balance)": jobBal,
  "job(repo, issue, funder)": jobWallet, "bind(payee)": await k2.bind(j.payee_id), "rep(payee)": await k2.rep(j.payee_id),
  "pair(payee, owner id)": await k2.pair(j.payee_id, j.owner_id), "pair(payee, funder)": await k2.pair(j.payee_id, j.funder),
  "rate(repo)": await k2.rate(j.repo_id), pause: await k2.pause(), "ata(address, mint)": await knos.ata(j.address, j.mint),
  "ata(address, mint22) token-2022": await knos.ata(j.address, j.mint22, knos.TOKEN_2022), "fee_account(mint)": await knos.ata(knos.FEE_OWNER, j.mint),
  "token(payer, token_id)": await k2.oidc.tokenPda(j.payer, tid), "key(github, n2048)": key2048, "key(gitlab, n4096)": key4096,
};
same("second: every address has a check", Object.keys(addresses).sort(), Object.keys(s.addresses).sort());
for (const [name, got] of Object.entries(addresses)) same(`second address ${name}`, got, s.addresses[name]);
same("the two deployments derive different addresses", (await k.auth()) !== (await k2.auth()), true);

// terms, hashes, fees
same("terms json", new TextDecoder().decode(terms), s.terms.json);
same("terms hash", th, s.terms.hash);
same("terms json, with characters outside ASCII", new TextDecoder().decode(termsTests), s.terms["json tests"]);
same("terms hash, tests mode", thTests, s.terms["hash tests"]);
same("terms json: every character that is written as an escape", new TextDecoder().decode(v2.termsJson(s.terms.odd)), s.terms["json odd"]);
same("terms in another key order give the same bytes", knos.hex(v2.termsJson(Object.fromEntries(Object.entries(j.terms).reverse()))), knos.hex(terms));
await throws("terms over 600 bytes are refused", () => v2.termsJson({ ...j.terms, paths: Array(60).fill("a-long-glob/**") }));
same("hashes", { wf_repo_hash: knos.hex(await v2.wfRepoHash(j.wf_repo)), "funder_key(wallet)": knos.hex(await v2.funderKey(j.funder)),
  "funder_key(owner id)": knos.hex(await v2.funderKey(j.owner_id)), "token_id(jwt)": knos.hex(await knos.tokenId(j.jwt)),
  "key_hash(n2048)": knos.hex(await knos.keyHash(n2048)), "key_hash(n4096)": knos.hex(await knos.keyHash(n4096)) }, s.hashes);
for (const [amount, fee] of Object.entries(s.fees)) same(`second: fee of ${amount}`, v2.feeOf(Number(amount)), fee);

// audiences
const fundAud = v2.fundAudience(j.issue, j.amount, v2.MERGE, th, bal);
const payAud = v2.payAudience(j.repo_id, j.issue, j.payee_id, j.head_sha, th, v2.MERGE, j.address);
const payAudNone = v2.payAudience(j.repo_id, j.issue, j.payee_id, j.head_sha, thTests, v2.TESTS);
const bound = v2.readBind(knos.unhex(s.accounts.bind.data));
const audiences = {
  fund: fundAud, "fund tests, 30 days": v2.fundAudience(j.issue, j.amount, v2.TESTS, thTests, faucetBal, 30 * 86400),
  "pay to an address": payAud, "pay, no address": payAudNone, bind: v2.bindAudience(j.wallet),
  rotate: await knos.rotateAudience(knos.GITHUB, n2048), "rotate 4096": await knos.rotateAudience(knos.GITLAB, n4096),
  "named_balance(fund)": v2.namedBalance(fundAud), "destination(bind, pay to an address)": v2.destination(bound, payAud),
  "destination(no bind, pay to an address)": v2.destination(null, payAud), "destination(no bind, pay, no address)": v2.destination(null, payAudNone),
};
same("second: every audience has a check", Object.keys(audiences).sort(), Object.keys(s.audiences).sort());
for (const [name, got] of Object.entries(audiences)) same(`second audience ${name}`, got, s.audiences[name]);

// the verifier's arithmetic and its reading of a key set
same("step plan", { 2048: knos.stepPlan(2048), 4096: knos.stepPlan(4096) }, s.oidc.step_plan);
same("key params: three moduli", Object.keys(s.oidc.key_params).length, 3);
for (const [nn, want] of Object.entries(s.oidc.key_params)) {
  const p = knos.keyParams(BigInt(nn));
  same(`key params of ${nn.slice(0, 6)}...${nn.slice(-7)}`, { n0inv: p.n0inv, r2: knos.hex(p.r2) }, want);
}
same("jwks keys", knos.jwksKeys(s.oidc.jwks).map(([kid, nn]) => [kid, "0x" + nn.toString(16)]), s.oidc.jwks_keys);
await throws("a 1024-bit modulus is refused", () => knos.modulusBytes(1n << 1023n));
await throws("a token over 8192 bytes is refused", () => k2.oidc.writeIxs(j.payer, tid, "x".repeat(8193)));

// accounts: what each reader makes of the bytes
const snake = (o) => (o && typeof o === "object" && !Array.isArray(o)
  ? Object.fromEntries(Object.entries(o).map(([name, v]) => [name.replace(/[A-Z]/g, (c) => "_" + c.toLowerCase()), v])) : o);
const readers = {
  job: (raw) => { const got = v2.readJob(raw); if (got) delete got.funder; return snake(got); },
  balance: (raw) => snake(v2.readBalance(raw)), bind: (raw) => snake(v2.readBind(raw)), rep: (raw) => snake(v2.readRep(raw)),
  pause: v2.readPause, rate: v2.readRate,
  token: (raw) => { const t = knos.readToken(raw); return t && { stage: t.stage, issuer: t.issuer, done: t.done, exp: t.exp, key: t.key, payer: t.payer, payload: knos.hex(t.payload) }; },
};
for (const [name, a] of Object.entries(s.accounts)) same(`account ${name}`, readers[a.reader](a.data === null ? null : knos.unhex(a.data)), a.read);
const jBal = v2.readJob(knos.unhex(s.accounts["job (open, from a Balance)"].data));
const jWallet = v2.readJob(knos.unhex(s.accounts["job (held, from a wallet, Token-2022, tests mode)"].data));
same("a Balance's job is funded by its GitHub owner; a wallet's by the wallet", [jBal.funder, jWallet.funder], [j.owner_id, j.funder]);
const verified = knos.readToken(knos.unhex(s.accounts["token (verified)"].data));
same("a verified token's claims", [verified.verified, verified.claims().aud, verified.claims().actor_id], [true, fundAud, String(j.maintainer_id)]);
same("a token still being verified shows no claims", knos.readToken(knos.unhex(s.accounts["token (stepping)"].data)).payload.length, 0);

// keys: what the header says and whether a token signed with the key would verify now
for (const [name, c] of Object.entries(s.keys)) {
  const key = v2.readKey(c.data === null ? null : knos.unhex(c.data));
  same(`key ${name}: header`, snake(key), c.read);
  same(`key ${name}: usable`, v2.keyUsable(key, c.now), c.usable);
}
same("a key account names its key by the hash of its modulus", await v2.keyAccountHash(knos.unhex(s.keys["ready, genesis"].data)), s.hashes["key_hash(n2048)"]);
same("a time outside the years a date can name", v2.keyUsable({ state: 1, genesis: true, activeAt: 4e14, expiresAt: 5e14 }, 0)[1],
  "this signing key is new: it can be used from time 400000000000000. Try again then.");

// instructions
const w = await k2.oidc.writeIxs(j.payer, tid, j.jwt);
const walletTok = await knos.ata(j.authority, j.mint), walletTok22 = await knos.ata(j.authority, j.mint22, knos.TOKEN_2022);
const open = await k2.openBalanceIx({ authority: j.authority, ownerId: j.owner_id, mint: j.mint, cap: j.cap, spenders: j.spenders });
const transfer = knos.transferCheckedIx(walletTok, j.mint, await k2.baltok(bal), j.authority, j.amount, 6);
const payArgs = { relayer: j.relayer, payToken: j.token_account, key: j.key };
const instructions = {
  "oidc.write (first chunk)": w[0], "oidc.write (last chunk)": w.at(-1),
  "oidc.step": await k2.oidc.stepIx(j.payer, tid, key2048, 8),
  "oidc.close": await k2.oidc.closeIx(j.payer, tid),
  "oidc.register_key": await k2.oidc.registerKeyIx(j.payer, knos.GITHUB, n2048),
  "oidc.register_key (attested, 4096)": await k2.oidc.registerKeyIx(j.payer, knos.GITLAB, n4096, j.attest),
  "oidc.key_params": await k2.oidc.keyParamsIx(j.payer, knos.GITHUB, n2048),
  "oidc.key_params (4096)": await k2.oidc.keyParamsIx(j.payer, knos.GITLAB, n4096),
  "oidc.refresh": await k2.oidc.refreshIx(j.payer, knos.GITHUB, n2048, j.attest),
  "oidc.approve": await k2.oidc.approveIx(s.programs.guardian, knos.GITLAB, n4096),
  "oidc.revoke": await k2.oidc.revokeIx(s.programs.guardian, knos.GITHUB, n2048),
  "pay.open_balance": await k2.openBalanceIx({ authority: j.authority, ownerId: j.owner_id, mint: j.mint }),
  "pay.open_balance (cap, spenders)": open,
  "pay.open_balance (token-2022, four spenders)": await k2.openBalanceIx({ authority: j.authority, ownerId: j.owner_id, mint: j.mint22,
    spenders: [1, 2, 3, 2n ** 64n - 1n], tokenProgram: knos.TOKEN_2022 }),
  "pay.set_balance": k2.setBalanceIx({ authority: j.authority, balance: bal, cap: j.cap, spenders: j.spenders }),
  "pay.set_balance (no cap, nobody)": k2.setBalanceIx({ authority: j.authority, balance: bal }),
  "pay.withdraw (everything)": await k2.withdrawIx({ authority: j.authority, balance: bal, mint: j.mint }),
  "pay.withdraw (amount, token account)": await k2.withdrawIx({ authority: j.authority, balance: bal, mint: j.mint, amount: j.amount, destToken: j.dest_token }),
  "pay.withdraw (token-2022)": await k2.withdrawIx({ authority: j.authority, balance: bal22, mint: j.mint22, tokenProgram: knos.TOKEN_2022 }),
  "pay.fund_balance": await k2.fundBalanceIx({ relayer: j.relayer, fundToken: j.token_account, key: j.key, balance: bal, mint: j.mint,
    repoId: j.repo_id, issue: j.issue, terms }),
  "pay.fund_balance (faucet)": await k2.fundBalanceIx({ relayer: j.relayer, fundToken: j.token_account, key: j.key, balance: faucetBal,
    mint: await k2.faucetMint(), repoId: j.repo_id, issue: j.issue, terms: s.terms.json }),
  "pay.fund_wallet merge": await k2.fundWalletIx({ funder: j.funder, funderToken: j.funder_token, mint: j.mint, repoId: j.repo_id, issue: j.issue,
    amount: j.amount, wfRepo: j.wf_repo, wfSha: j.wf_sha, terms }),
  "pay.fund_wallet tests (token-2022)": await k2.fundWalletIx({ funder: j.funder, funderToken: j.funder_token, mint: j.mint22, repoId: j.repo_id,
    issue: j.issue, amount: j.amount, wfRepo: j.wf_repo, wfSha: j.wf_sha, terms: termsTests, mode: v2.TESTS, workS: 7 * 86400, tokenProgram: knos.TOKEN_2022 }),
  "pay.pay (a Balance's job, to a wallet)": await k2.payIx({ ...payArgs, job: jobBal, j: jBal, payeeId: j.payee_id, wallet: j.wallet }),
  "pay.pay (a Balance's job, held)": await k2.payIx({ ...payArgs, job: jobBal, j: jBal, payeeId: j.payee_id, wallet: null }),
  "pay.pay (a wallet's job, token account)": await k2.payIx({ ...payArgs, job: jobWallet, j: jWallet, payeeId: j.payee_id, wallet: j.wallet, destToken: j.dest_token }),
  "pay.settle": await k2.settleIx({ relayer: j.relayer, job: jobWallet, j: jWallet, wallet: j.wallet }),
  "pay.refund (a Balance's job)": await k2.refundIx({ relayer: j.relayer, job: jobBal, j: jBal }),
  "pay.refund (a wallet's job)": await k2.refundIx({ relayer: j.relayer, job: jobWallet, j: jWallet }),
  "pay.refund (a wallet's job, token account)": await k2.refundIx({ relayer: j.relayer, job: jobWallet, j: jWallet, refundToken: j.dest_token }),
  "pay.bind": await k2.bindIx({ relayer: j.relayer, bindToken: j.token_account, key: j.key, userId: j.payee_id }),
  "pay.pause": await k2.pauseIx({ guardian: s.programs.guardian, payer: j.payer, seconds: 3 * 86400 }),
  "pay.pause (lift)": await k2.pauseIx({ guardian: s.programs.guardian, payer: j.payer, seconds: 0 }),
  "pay.init_faucet": await k2.initFaucetIx({ payer: j.payer }),
  "pay.faucet_open": await k2.faucetOpenIx({ relayer: j.relayer, fundToken: j.token_account, key: j.key, ownerId: j.owner_id, repoId: j.repo_id }),
  create_ata: await knos.createAtaIx(j.relayer, j.address, j.mint),
  "create_ata (token-2022)": await knos.createAtaIx(j.relayer, j.address, j.mint22, knos.TOKEN_2022),
  "token.transfer_checked": transfer,
};
same("second: every instruction has a check", Object.keys(instructions).sort(), Object.keys(s.instructions).sort());
for (const [name, ix] of Object.entries(instructions)) same(`second instruction ${name}`, plain(ix), s.instructions[name]);
await throws("a fifth spender is refused", () => k2.openBalanceIx({ authority: j.authority, ownerId: j.owner_id, mint: j.mint, spenders: [1, 2, 3, 4, 5] }));
await throws("a workflow commit that is not a full sha is refused", () => k2.fundWalletIx({ funder: j.funder, funderToken: j.funder_token, mint: j.mint,
  repoId: j.repo_id, issue: j.issue, amount: j.amount, wfRepo: j.wf_repo, wfSha: "main", terms }));

// what an instruction is, read back from its bytes: every knos-pay instruction under the IDL's names
for (const [name, ix] of Object.entries(instructions).filter(([name]) => name.startsWith("pay."))) {
  const e = k2.explain(ix), [want, names] = v2.PAY_IXS[ix.data[0]];
  same(`explain ${name}`, [e.program, e.address, e.name, e.accounts.map((a) => a.name), e.accounts.map((a) => a.pubkey)],
    ["knos-pay", s.programs.knos_pay, want, names, ix.accounts.map((a) => a.pubkey)]);
}
same("explain: open a balance", k2.explain(open).args, { ownerId: j.owner_id, cap: j.cap, spenders: j.spenders });
same("explain: set who may spend", k2.explain(instructions["pay.set_balance (no cap, nobody)"]).args, { cap: 0, spenders: [] });
same("explain: take an amount back", k2.explain(instructions["pay.withdraw (amount, token account)"]).args, { amount: j.amount });
same("explain: a wallet funds a job", k2.explain(instructions["pay.fund_wallet merge"]).args, { repoId: j.repo_id, issue: j.issue, amount: j.amount,
  workS: 14 * 86400, mode: 0, wfRepoHash: s.hashes.wf_repo_hash, wfSha: j.wf_sha, terms: s.terms.json });
same("explain: a comment funds a job", k2.explain(instructions["pay.fund_balance"]).args, { terms: s.terms.json });
same("explain: pause", k2.explain(instructions["pay.pause"]).args, { seconds: 3 * 86400 });
const moved = k2.explain(transfer);
same("explain: a transfer", [moved.program, moved.name, moved.args, moved.accounts.map((a) => a.name)],
  ["SPL Token", "TransferChecked", { amount: j.amount, decimals: 6 }, ["source", "mint", "destination", "owner"]]);
same("explain: a token account", [k2.explain(instructions.create_ata).name, k2.explain(instructions["create_ata (token-2022)"]).accounts[5].pubkey],
  ["CreateIdempotent", knos.TOKEN_2022]);
same("explain: anything else is unknown, with its bytes", [k2.explain(w[0]).program, k2.explain(w[0]).name, k2.explain(w[0]).args.data.slice(0, 4)],
  ["knos-oidc", "unknown", "00c0"]);
const idlPath = join(here, "..", "..", "idl", "knos_pay_v2.json");
if (existsSync(idlPath)) {      // in the repository: the names are the IDL's (the npm package does not carry it)
  const idl = JSON.parse(readFileSync(idlPath, "utf8"));
  same("the instruction table is the IDL's", v2.PAY_IXS, idl.instructions.map((x) => [x.name, x.accounts.map((a) => a.name)]));
  same("the instruction tags are the IDL's", v2.PAY_IXS.map((_x, at) => at), idl.instructions.map((x) => x.discriminant.value));
}

// transactions: the unsigned legacy transaction as the Solana SDK serialises the same instructions
const b64 = (u8) => Buffer.from(u8).toString("base64");
const create = await knos.createAtaIx(j.authority, j.authority, j.mint);
const transactions = {
  "open a balance": [[open], j.authority],
  "open a balance and put money in": [[open, transfer], j.authority],
  "add money": [[transfer], j.authority],
  "add money (token-2022, 9 decimals)": [[knos.transferCheckedIx(walletTok22, j.mint22, await k2.baltok(bal22), j.authority, 3_000_000_000, 9, knos.TOKEN_2022)], j.authority],
  "set who may spend": [[instructions["pay.set_balance"]], j.authority],
  "take everything back": [[create, instructions["pay.withdraw (everything)"]], j.authority],
  "take an amount back": [[create, await k2.withdrawIx({ authority: j.authority, balance: bal, mint: j.mint, amount: j.amount })], j.authority],
  "a wallet funds a job": [[await k2.fundWalletIx({ funder: j.funder, funderToken: await knos.ata(j.funder, j.mint), mint: j.mint, repoId: j.repo_id,
    issue: j.issue, amount: j.amount, wfRepo: j.wf_repo, wfSha: j.wf_sha, terms })], j.funder],
};
same("second: every transaction has a check", Object.keys(transactions).sort(), Object.keys(s.transactions).sort());
for (const [name, [ixs, payer]] of Object.entries(transactions)) {
  const want = s.transactions[name];
  same(`transaction ${name}: instructions`, ixs.map(plain), want.instructions);
  same(`transaction ${name}: message`, knos.hex(knos.serializeMessage(ixs, payer, j.blockhash)), want.message);
  const bytes = knos.serializeTx(ixs, payer, j.blockhash);
  same(`transaction ${name}: bytes`, b64(bytes), want.transaction);
  same(`transaction ${name}: the message inside it`, knos.hex(knos.messageOf(bytes)), want.message);
  same(`transaction ${name}: fits in a packet`, bytes.length <= 1232, true);
}

// ==== what a browser adds ============================================================================================
same("addresses: 32 bytes, written one way", [knos.isAddress(j.wallet), knos.isAddress(knos.SYSTEM), knos.isAddress("1" + j.wallet),
  knos.isAddress("0x" + "ab".repeat(20)), knos.isAddress(j.wallet.slice(0, 31)), knos.isAddress(null)], [true, false, false, false, false, false]);

// SPL Token accounts
const mintAccount = new Uint8Array(82);
new DataView(mintAccount.buffer).setUint32(0, 1, true); mintAccount.set(knos.unb58(j.authority), 4);
new DataView(mintAccount.buffer).setBigUint64(36, 1_000_000_000_000n, true); mintAccount[44] = 6; mintAccount[45] = 1;
same("a mint", knos.readMint(mintAccount), { decimals: 6, supply: 1_000_000_000_000, mintAuthority: j.authority });
const tokenAccount = new Uint8Array(165);
tokenAccount.set(knos.unb58(j.mint), 0); tokenAccount.set(knos.unb58(j.wallet), 32); new DataView(tokenAccount.buffer).setBigUint64(64, 19_500_000n, true);
same("a token account", knos.readTokenAccount(tokenAccount), { mint: j.mint, owner: j.wallet, amount: 19_500_000 });
same("neither", [knos.readMint(new Uint8Array(81)), knos.readTokenAccount(null)], [null, null]);

// Squads: the vault of a multisig, and two multisig accounts of other people's as devnet returned them on 2 Oct 2026
// (2 of 3 members, an approved transaction waits 172,800 seconds; one names a rent collector, one does not)
same("the upgrade authority is vault 0 of the upgrade multisig", await knos.squadsVault(s.programs.upgrade_multisig), s.programs.upgrade_authority);
same("the guardian is vault 0 of the guardian multisig", await knos.squadsVault(s.programs.guardian_multisig), s.programs.guardian);
same("the multisig program", knos.SQUADS, s.programs.squads_program);
const recorded = [       // [address, account data, what a reader written apart from this client found in it, its vault 0]
  ["K3u623LwUfpiQNuTXgmWW6Q9mgqh94pFm7nEn7W99Xb",
    "4HR5ukShT+xNivujmtQm1Okq4LPGr5F9K9R6wnKMB40nBPWTUnJizQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAowIAAAAAAAAAAAAAAAAAAAAAAAD8AwAAAFzX7Eqr3t/I9YmHAqNrbcQe5JG6oK0xuBJ+nuwfbv6XB6bGmWv4e9OsNIDl0EP50hgIhsHAwu1desJP3hHAWoN/B/E0PpnjG0Aha16GrzQ3JGOeSgYlL3JAUdC1aZvDb2i5BwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    { createKey: "6DhJyjCMs4pRkTvy1Dig6zd9enRpWNTJE6FLvif5AsMN", configAuthority: knos.SYSTEM, threshold: 2, timeLock: 172800, transactionIndex: 0,
      staleTransactionIndex: 0, rentCollector: null, members: ["7FRUMUKVu63e78hw1dNL4dMH1hYYDxASqUqVRpVq7Y1C",
        "CE2KZgRhHaEJRwVqMWiFfmUkgU9veAhY3VrU8Vqc8xNz", "HEZTi6du87sv9jdgzbtFJmfLVtUxqzRPxYvxX7MWcssN"].map((key) => ({ key, permissions: 7 })) },
    "C57XKxbywVVTSvJhskMRKWW8X978yeWhq4ZSJkEsVCGn"],
  ["3fw9nvFdzWc5WWYEFkDrMmyzRaoiuWzdMQohQjfyyDWH",
    "4HR5ukShT+zeTFOY+Knj2hv/ktfmAqTtGwF9DpxI7RCJol6xju8KzwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAowIAAAAAAAAAAAAAAAAAAAAAAAEUK252ngj3hv377tTD/e6S/z97nizjtHAXQsw8P8tqT/wDAAAARufEo6Wjv9wFFwh5GPPVqmqD+95HTf8pw5IUdPKj7IcHXHs3n5Lq8AUtUI3pZWmSQ7rQbB00SmU0LPBtoQUq8aIHy+sHmYsw5qefkZtWhsqNJAL022dZegpNrw0bY8FNgmYH",
    { createKey: "Fxm1j2bZzxvvw6NkWfQjKh1WuXkEgfPdFRtbfiYmy8jp", configAuthority: knos.SYSTEM, threshold: 2, timeLock: 172800, transactionIndex: 0,
      staleTransactionIndex: 0, rentCollector: "2MjZmdfqem7K4dej76hrXGVEixPVhrFkLCC2oNQZranr", members: ["5mnWuy7EcUvz7yzy1ftvp3bCXnUUhE273YAAuSsN7ZB4",
        "7E1Uy463eTennkhNS2febLu4XFPt1kvk3xmufJFSDbBf", "Ej1dCSoxxjBRUC9LfhoUcXg7TA93BWaE9seCgpeTscCm"].map((key) => ({ key, permissions: 7 })) },
    "Ei2YVhRhRsJXHAnfFqmkkdgRj5LQdCmy1B4iqG4t1ELZ"],
];
for (const [address, data, want, vault] of recorded) {
  same(`a multisig on devnet, ${address.slice(0, 6)}: members, threshold, delay`, knos.readMultisig(Uint8Array.from(Buffer.from(data, "base64"))), want);
  same(`a multisig on devnet, ${address.slice(0, 6)}: its vault`, await knos.squadsVault(address), vault);
}
const tag = knos.hex(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode("account:Multisig"))).slice(0, 8));
same("a multisig account starts with Anchor's tag for it", tag, "e07479ba44a14fec");
const ms = new Uint8Array(231);         // the layout of a 2-of-3 multisig with a 48-hour time lock and a rent collector
ms.set(knos.unhex(tag), 0); ms.set(knos.unb58(j.payer), 8); new DataView(ms.buffer).setUint16(72, 2, true);
new DataView(ms.buffer).setUint32(74, 172800, true); new DataView(ms.buffer).setBigUint64(78, 5n, true); ms[94] = 1; ms.set(knos.unb58(j.rent_to), 95);
ms[127] = 254; new DataView(ms.buffer).setUint32(128, 3, true);
[j.funder, j.relayer, j.address].forEach((m, at) => { ms.set(knos.unb58(m), 132 + 33 * at); ms[164 + 33 * at] = 7; });
same("a multisig: threshold, delay, members", knos.readMultisig(ms), { createKey: j.payer, configAuthority: knos.SYSTEM, threshold: 2, timeLock: 172800,
  transactionIndex: 5, staleTransactionIndex: 0, rentCollector: j.rent_to, members: [j.funder, j.relayer, j.address].map((m) => ({ key: m, permissions: 7 })) });
ms[94] = 0; ms.copyWithin(96, 128, 231);      // no rent collector: the members follow one byte sooner
same("a multisig without a rent collector", [knos.readMultisig(ms).rentCollector, knos.readMultisig(ms).members.length, knos.readMultisig(ms).members[2].key], [null, 3, j.address]);
same("not a multisig", [knos.readMultisig(new Uint8Array(231)), knos.readMultisig(null), knos.readMultisig(ms.slice(0, 120))], [null, null, null]);

// wallets: a stand-in for each kind, in a window of our own
class Window extends EventTarget { CustomEvent = CustomEvent; }
const signature = Uint8Array.from({ length: 64 }, (_x, at) => at + 1);
function standardWallet(name, log, { wallet = ["solana:mainnet", "solana:devnet"], shared = ["solana:devnet", "solana:mainnet"] } = {}) {
  const account = { address: j.authority, publicKey: knos.unb58(j.authority), chains: shared, features: ["solana:signAndSendTransaction"] };
  return { version: "1.0.0", name, icon: "data:image/svg+xml,", chains: wallet, accounts: [],
    features: { "standard:connect": { version: "1.0.0", connect: async () => ({ accounts: [account] }) },
      "solana:signAndSendTransaction": { version: "1.0.0", supportedTransactionVersions: ["legacy", 0],
        signAndSendTransaction: async (...inputs) => { log.push(...inputs); return inputs.map(() => ({ signature })); } } } };
}
// what a wallet does to register (the Wallet Standard's registerWallet): announce itself, and listen for the app
function registerWallet(win, wallet) {
  const callback = ({ register }) => register(wallet);
  win.dispatchEvent(new CustomEvent("wallet-standard:register-wallet", { detail: callback }));
  win.addEventListener("wallet-standard:app-ready", ({ detail: api }) => callback(api));
}
const [, [openIxs, openPayer]] = Object.entries(transactions)[0];
const openTx = knos.serializeTx(openIxs, openPayer, j.blockhash);

same("no window, no wallets", knos.wallets(undefined), []);
{
  const win = new Window(), log = [];
  registerWallet(win, standardWallet("Early", log));                 // the wallet loaded before the page's script
  same("a window with one wallet", knos.wallets(win).map((x) => [x.name, x.kind]), [["Early", "standard"]]);
  registerWallet(win, standardWallet("Late", log));                  // and one that loaded after
  registerWallet(win, { name: "Other chain", chains: ["bip122:000000000019d6689c085ae165831e93"], features: { "standard:connect": {} } });
  registerWallet(win, { name: "Cannot send", chains: ["solana:devnet"], features: { "standard:connect": {}, "solana:signTransaction": {} } });
  const found = knos.wallets(win);
  same("wallets that can sign and send on Solana, in the order they registered", found.map((x) => x.name), ["Early", "Late"]);
  same("a wallet connects to an address", await found[1].connect(), j.authority);
  same("a wallet signs and sends: the signature, as Solana writes it", await found[1].signAndSend(openTx, "solana:devnet"), knos.b58(signature));
  same("what the wallet was handed", [log.length, log[0].chain, log[0].account.address, b64(log[0].transaction)],
    [1, "solana:devnet", j.authority, s.transactions["open a balance"].transaction]);
  same("signing connects first when nobody did", await found[0].signAndSend(openTx), knos.b58(signature));
  registerWallet(win, standardWallet("Mainnet only", log, { wallet: ["solana:mainnet"], shared: ["solana:mainnet"] }));
  registerWallet(win, standardWallet("Account on mainnet", log, { shared: ["solana:mainnet"] }));
  registerWallet(win, standardWallet("Account with no list", log, { shared: undefined }));
  const [, , mainnetOnly, otherAccount, unlisted] = knos.wallets(win);
  await throws("a wallet that does not offer devnet is refused for devnet, in words", () => mainnetOnly.connect("solana:devnet"));
  await throws("so is a wallet whose account is not on devnet", () => otherAccount.connect("solana:devnet"));
  same("each is as good as it says for the chain it does offer", [await mainnetOnly.connect("solana:mainnet"), await otherAccount.connect("solana:mainnet")], [j.authority, j.authority]);
  same("an account that lists no chains is taken as it is", await unlisted.connect("solana:devnet"), j.authority);
  try { await mainnetOnly.connect("solana:devnet"); } catch (e) { same("the refusal says what to do", e.message, "Mainnet only has no devnet account to share. Set it to devnet and connect again."); }
}
{
  const win = new Window(), asked = [];
  win.phantom = { solana: { isPhantom: true, publicKey: null,
    connect: async () => ({ publicKey: { toString: () => j.authority } }),
    request: async (r) => { asked.push(r); return { signature: knos.b58(signature), publicKey: j.authority }; } } };
  const [phantom, ...others] = knos.wallets(win);
  same("Phantom's own provider is used when it did not register", [phantom.name, phantom.kind, others.length], ["Phantom", "phantom", 0]);
  same("Phantom connects", await phantom.connect(), j.authority);
  same("Phantom signs and sends", await phantom.signAndSend(openTx), knos.b58(signature));
  same("Phantom is asked with the message in base58", [asked[0].method, knos.hex(knos.unb58(asked[0].params.message, knos.messageOf(openTx).length))],
    ["signAndSendTransaction", s.transactions["open a balance"].message]);
  registerWallet(win, standardWallet("Phantom", []));
  same("a wallet that registered is not listed twice", knos.wallets(win).map((x) => x.kind), ["standard"]);
}
{
  const win = new Window(), sent = [];
  win.solana = { isPhantom: true, connect: async () => ({ publicKey: j.wallet }), request: async () => ({ signature }) };
  win.solflare = { isSolflare: true, publicKey: null,
    async connect() { this.publicKey = { toString: () => j.authority }; return true; },
    async signAndSendTransaction(t) { sent.push(t.version, b64(t.serialize({ verifySignatures: false, requireAllSignatures: false })), knos.hex(t.serializeMessage())); return knos.b58(signature); } };
  const [phantom, solflare] = knos.wallets(win);
  same("Phantom under its older name, then Solflare", [phantom.kind, solflare.name, solflare.kind], ["phantom", "Solflare", "solflare"]);
  same("an address given as text, a signature given as bytes", [await phantom.connect(), await phantom.signAndSend(openTx)], [j.wallet, knos.b58(signature)]);
  same("Solflare connects", await solflare.connect(), j.authority);
  same("Solflare signs and sends", await solflare.signAndSend(openTx), knos.b58(signature));
  same("Solflare is handed a legacy transaction that serialises to the same bytes", sent,
    [undefined, s.transactions["open a balance"].transaction, s.transactions["open a balance"].message]);
}

// JSON-RPC: what is asked and what comes back, with a stand-in for fetch
{
  const asked = [], real = globalThis.fetch;
  const answers = { getAccountInfo: (p) => ({ value: p[0] === j.mint ? { owner: knos.TOKEN, lamports: 1461600, data: [b64(mintAccount), "base64"] } : null }),
    getProgramAccounts: () => [{ pubkey: bal, account: { data: [s.accounts.balance.data ? b64(knos.unhex(s.accounts.balance.data)) : "", "base64"] } }],
    getMultipleAccounts: (p) => ({ value: p[0].map((a) => (a === j.mint ? { owner: knos.TOKEN, lamports: 1461600, data: [b64(mintAccount), "base64"] } : null)) }),
    getSlot: () => 500, getBlockTime: (p) => (p[0] === 500 ? null : 1790000000 + p[0]),
    getSignatureStatuses: (p) => ({ value: [p[0][0] === "pending" ? null : p[0][0] === "failed" ? { err: { InstructionError: [0, { Custom: 98 }] }, confirmationStatus: "confirmed" }
      : { err: null, confirmationStatus: "confirmed" }] }) };
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    asked.push([url, body.method]);
    return { json: async () => (body.method in answers ? { jsonrpc: "2.0", id: 1, result: answers[body.method](body.params) } : { error: { message: "no such method" } }) };
  };
  try {
    same("an account's bytes", knos.readMint(await knos.account("rpc", j.mint)).decimals, 6);
    same("an account that does not exist", [await knos.account("rpc", j.wallet), await knos.accountInfo("rpc", j.wallet)], [null, null]);
    same("an account's owner", (await knos.accountInfo("rpc", j.mint)).owner, knos.TOKEN);
    const several = await knos.accounts("rpc", [j.wallet, j.mint]);
    same("several accounts, in the order asked", [several[0], several[1].owner, knos.readMint(several[1].data).decimals], [null, knos.TOKEN, 6]);
    same("the chain's clock is a block's time, the newest that has one", await knos.chainTime("rpc"), 1790000499);
    const got = await knos.programAccounts("rpc", s.programs.knos_pay, v2.BALANCE_LEN, 8, knos.u64le(j.owner_id));
    same("a program's accounts", [got.length, got[0].address, v2.readBalance(got[0].data).ownerId], [1, bal, j.owner_id]);
    same("a confirmed transaction", await knos.confirmed("rpc", "landed", 3, async () => {}), { ok: true, err: null });
    same("a refused transaction, in the program's words", v2.errorWords((await knos.confirmed("rpc", "failed", 3, async () => {})).err), s.errors["98"]);
    same("a transaction nobody saw", await knos.confirmed("rpc", "pending", 3, async () => {}), null);
    await throws("an RPC error is an error", () => knos.rpc("rpc", "nope", []));
    same("each call went to the endpoint", [asked.length > 6, asked.every(([url]) => url === "rpc")], [true, true]);
  } finally { globalThis.fetch = real; }
}

console.log(`sdk/settle: ${n} checks match the Python client (${first} for the first deployment, ${n - first} for the second and the browser)`);
