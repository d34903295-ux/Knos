// node sdk/settle/test.mjs
// The JavaScript client against the fixtures the Python client wrote: every address, audience, fee and instruction
// must match byte for byte. Exit 1 on the first mismatch.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as knos from "./index.js";

const here = dirname(fileURLToPath(import.meta.url));
const fx = JSON.parse(readFileSync(join(here, "fixtures.json"), "utf8"));
const i = fx.inputs, k = knos.client(fx.programs);
let n = 0;

function same(name, got, want) {
  n++;
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    console.error(`MISMATCH ${name}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`);
    process.exit(1);
  }
}
const plain = (ix) => ({ program: ix.program, data: knos.hex(ix.data),
  accounts: ix.accounts.map((a) => ({ pubkey: a.pubkey, signer: a.signer, writable: a.writable })) });

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

// the transaction a wallet signs: header, accounts in signer/writable order, the payer first
const tx = knos.serializeTx([await knos.createAtaIx(i.funder, i.funder, i.mint), await k.fundIx(base)], i.funder, i.mint);
same("tx: one empty signature", [tx[0], knos.hex(tx.slice(1, 65))], [1, "00".repeat(64)]);
same("tx: header", [...tx.slice(65, 68)], [1, 0, 6]);
same("tx: accounts", tx[68], 11);
same("tx: payer first", knos.b58(tx.slice(69, 101)), i.funder);
if (process.argv[2]) (await import("node:fs")).writeFileSync(process.argv[2], Buffer.from(tx));

console.log(`sdk/settle: ${n} checks match the Python client`);
