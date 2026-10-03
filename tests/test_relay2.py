"""What carries GitHub-signed tokens to the second deployment: knos.settle.v2.relay (one token), knos.chain.Ledger
(the cluster) and knos.proof.ghrelay (the public worker), on LiteSVM and with fakes for GitHub and for the RPC.

A comment funds a bounty from the faucet or from a Balance, a proof pays it or holds it, a claim binds a wallet and
the held payment follows, an unproven bounty goes back, a key token reaches both deployments. Every refusal that can
be known from reads is given before a fee is spent. A token relayed twice, or by two relayers, is done once. The
transactions and the waits of each kind are counted and printed, beside the first deployment's."""
from __future__ import annotations

import base64
import json
import re

import pytest

pytest.importorskip("solders.litesvm")

from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from solders.instruction import AccountMeta, Instruction  # noqa: E402
from solders.keypair import Keypair  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402

from _oidc2 import ROTATE_REF, TEST_ROTATE_SHA, Chain2  # noqa: E402
from _pay2 import GUARDIAN, TEST_CLAIM_SHA, WF_REPO, WF_SHA, Chain, github_claims  # noqa: E402
from _settle import FIX, b64, modulus, sign_jwt, signing_key  # noqa: E402

from knos import chain  # noqa: E402
from knos.settle import oidc as oidc1  # noqa: E402
from knos.settle import pay as pay1  # noqa: E402
from knos.settle import relay as relay1  # noqa: E402
from knos.settle.v2 import oidc, pay, relay  # noqa: E402

ROOT = FIX.parents[1]
REPO, OWNER, MAINT = 987654321, 424242, 555000      # the harness's repository, its owner, a maintainer
USDC = 1_000_000
TERMS = pay.terms_json({"accept": "", "checks": [{"app": 15368, "name": "test"}], "deny": [".github/**", ".knos/**"], "mode": "merge",
                        "paths": [], "reserve": 7, "v": 1})
TH = pay.terms_hash(TERMS)
JWKS = {oidc.GITHUB: {"keys": [{"kty": "RSA", "alg": "RS256", "e": "AQAB", "kid": "k", "n": b64(oidc.modulus_bytes(modulus(signing_key())))}]}}
_COUNT = [1000, 3_000_000]


def issue() -> int:
    _COUNT[0] += 1
    return _COUNT[0]


def user() -> int:
    """A GitHub id nobody has used yet (binds, records and faucet balances are per id, and the tests share a chain)."""
    _COUNT[1] += 1
    return _COUNT[1]


class Net(chain.Ledger):
    """knos.chain.Ledger with LiteSVM for a cluster. A transaction is signed exactly as the real Ledger signs it
    (chain.sign), so it weighs what it would on devnet. Counts what a relay costs: transactions, waits (rounds of
    confirmation: `send` is one, `send_all` is one for the lot) and reads."""

    def __init__(self, c: Chain):
        super().__init__("litesvm")
        self.c = c
        self.txs = self.waits = self.reads = 0
        self.sizes: list[int] = []
        self.units: list[int] = []
        self.shape: list[int] = []                      # transactions per wait, in order
        self.said: dict[str, list[str]] = {}            # signature -> the transaction's log
        self.when: dict[str, int] = {}                  # signature -> the chain's time when it landed
        self.named: dict[Pubkey, list[str]] = {}        # address -> the signatures that named it, oldest first

    def _one(self, ixs, payer, signers) -> str:
        tx = chain.sign(ixs, payer, signers, self.c.svm.latest_blockhash())
        r = self.c.svm.send_transaction(tx)
        self.c.svm.expire_blockhash()
        ok = "Failed" not in type(r).__name__
        meta = r if ok else r.meta()
        logs = list(meta.logs())
        if not ok:
            raise chain.RpcError(f"transaction failed: {r.err()}", {"logs": logs})
        sig = str(tx.signatures[0])
        used = meta.compute_units_consumed
        self.txs += 1
        self.sizes.append(len(bytes(tx)))
        self.units.append(used() if callable(used) else used)
        self.said[sig], self.when[sig] = logs, self.c.now()
        for key in tx.message.account_keys:
            self.named.setdefault(key, []).append(sig)
        return sig

    def send(self, ixs, payer, signers=None) -> str:
        sig = self._one(list(ixs), payer, signers)
        self.waits += 1
        self.shape.append(1)
        return sig

    def send_all(self, groups, payer, signers=None) -> list[str]:
        sent, refused = [], None
        for ixs in groups:
            try:
                sent.append(self._one(list(ixs), payer, signers))
            except chain.RpcError as why:
                refused = refused or why
        self.waits += 1
        self.shape.append(len(sent))
        if refused is not None:
            raise refused
        return sent

    def infos(self, addresses):
        self.reads += 1
        out = []
        for a in addresses:
            acc = self.c.svm.get_account(a)
            out.append((acc.owner, bytes(acc.data)) if acc is not None and acc.lamports > 0 else None)
        return out

    def account(self, address):
        got = self.infos([address])[0]
        return got[1] if got else None

    def program_accounts(self, program, size=None, memcmp=None):
        self.reads += 1
        out = []
        for addr, acc in self.c.svm.get_program_accounts(program):
            d = bytes(acc.data)
            if acc.lamports > 0 and (size is None or len(d) == size) and all(d[o:o + len(b)] == b for o, b in (memcmp or {}).items()):
                out.append((addr, d))
        return out

    def recent(self, address, limit=20):
        self.reads += 1
        return [(sig, self.when[sig]) for sig in list(reversed(self.named.get(address, [])))[:limit]]

    def history(self, address, most=500):
        self.reads += 1
        yield from list(reversed(self.named.get(address, [])))[:most]

    def logs(self, signature):
        self.reads += 1
        return self.said.get(signature, [])

    def touched(self, address):
        self.reads += 1
        return self.when[self.named[address][-1]] if self.named.get(address) else None

    def now(self) -> int:
        self.reads += 1
        return self.c.now()

    def spent(self):
        """(transactions, waits, reads) since the last call."""
        got = (self.txs, self.waits, self.reads)
        self.txs = self.waits = self.reads = 0
        self.shape.clear()
        return got


@pytest.fixture(scope="module")
def env():
    c = Chain()
    net = Net(c)
    assert c.send([pay.init_faucet_ix(c.payer.pubkey())]), c.err
    c.usdc = c.new_mint()
    c.owner, c.owner_tok = c.wallet(c.usdc, 1_000_000 * USDC)
    assert c.send([pay.open_balance_ix(c.owner.pubkey(), OWNER, c.usdc, spenders=[MAINT])], c.owner), c.err
    c.bal = pay.balance_pda(OWNER, c.owner.pubkey(), c.usdc)
    transfer(c, c.owner_tok, pay.baltok_pda(c.bal), 10_000 * USDC, c.owner)
    return c, net


def transfer(c: Chain, source: Pubkey, dest: Pubkey, amount: int, owner: Keypair, mint: Pubkey | None = None) -> None:
    """A plain TransferChecked, as any wallet would send it."""
    mint = mint or c.usdc
    ix = Instruction(c.token_program(mint), bytes([12]) + amount.to_bytes(8, "little") + bytes([6]),
                     [AccountMeta(source, False, True), AccountMeta(mint, False, False), AccountMeta(dest, False, True), AccountMeta(owner.pubkey(), True, False)])
    assert c.send([ix], signers=[owner]), c.err


@pytest.fixture(autouse=True)
def _test_pins(monkeypatch):
    """The test builds accept a test claim pin, a test rotate pin and the harness's repository as an attester."""
    monkeypatch.setattr(relay, "CLAIM_SHAS", relay.CLAIM_SHAS | {TEST_CLAIM_SHA})
    monkeypatch.setattr(relay, "ROTATE_SHAS", relay.ROTATE_SHAS | {TEST_ROTATE_SHA})
    monkeypatch.setattr(relay, "ATTESTERS", relay.ATTESTERS | {(OWNER, REPO)})


def token(c: Chain, aud: str, file: str = "prove.yml", wf_repo: str = WF_REPO, wf_sha: str = WF_SHA, key=None, **over) -> str:
    """What GitHub would sign for a run of <wf_repo>/.github/workflows/<file> at wf_sha, issued now."""
    now = c.now()
    c._n += 1
    claims = dict(aud=aud, iat=now, nbf=now - 600, exp=now + 300, jti=f"r{c._n}",
                  job_workflow_ref=f"{wf_repo}/.github/workflows/{file}@refs/tags/v0.3.12", job_workflow_sha=wf_sha)
    claims.update(over)
    return sign_jwt(key or signing_key(), github_claims(**claims))


def fund_jwt(c: Chain, n: int, amount: int = 5 * USDC, balance: Pubkey | None = None, terms: bytes = TERMS, work: int = 14 * 86_400,
             mode: int = pay.MERGE, **over) -> str:
    """The token of a maintainer's `/knos fund` on issue n (default: it spends the owner's Balance)."""
    c.warp(1)       # a Balance takes its fund tokens in the order GitHub issued them
    claims = {"file": "fund.yml", "event_name": "issue_comment", "actor_id": MAINT, **over}
    return token(c, pay.fund_audience(n, amount, mode, pay.terms_hash(terms), balance or c.bal, work), **claims)


def faucet_jwt(c: Chain, n: int, org: int, repo: int, amount: int = 5 * USDC, **over) -> str:
    """The same comment in a repository whose owner has no Balance: on devnet it spends the owner's faucet Balance."""
    return fund_jwt(c, n, amount, pay.faucet_balance_pda(org), repository_owner_id=org, repository_id=repo, **over)


def pay_jwt(c: Chain, repo: int, n: int, payee: int, address: Pubkey | None = None, terms: bytes = TERMS, mode: int = pay.MERGE, **over) -> str:
    """What the pinned prove.yml asks GitHub to sign once the merged pull request met the terms."""
    return token(c, pay.pay_audience(repo, n, payee, "a" * 40, pay.terms_hash(terms), mode, address), repository_id=repo, **over)


def bind_jwt(c: Chain, who: int, wallet: Pubkey, **over) -> str:
    """What the pinned claim workflow asks GitHub to sign when `who` runs it in their own repository named knos-claim."""
    c.warp(1)
    claims = dict(file="claim.yml", wf_repo="drexthealpha/knos-oidc-rotate", wf_sha=TEST_CLAIM_SHA, event_name="workflow_dispatch",
                  actor_id=who, repository_owner_id=who, repository=f"user{who}/knos-claim", repository_id=70_000_000 + who % 1_000_000)
    claims.update(over)
    return token(c, pay.bind_audience(wallet), **claims)


def go(env, jwt: str, terms: bytes | None = None, payer: Keypair | None = None) -> dict:
    c, net = env
    return relay.submit(net, payer or c.payer, jwt, terms, JWKS, now=c.now())




def funded(env, n: int | None = None, amount: int = 5 * USDC, **kw) -> tuple[int, Pubkey]:
    """A comment funds issue n from the owner's Balance, through the relay. Returns (issue, job)."""
    c, _net = env
    n = n or issue()
    r = go(env, fund_jwt(c, n, amount, **kw), kw.get("terms", TERMS))
    assert r["ok"], r
    return n, Pubkey.from_string(r["job"])


def lamports(c: Chain, who: Keypair | None = None) -> int:
    return c.lamports((who or c.payer).pubkey())


# -- every kind's happy path, and what it costs --------------------------------------------------------------------------
def test_a_comment_funds_from_the_faucet_a_proof_pays_and_a_claim_binds(env):
    c, net = env
    org, repo, n, payee, wallet = user(), user(), issue(), user(), Keypair().pubkey()
    jwt = faucet_jwt(c, n, org, repo)
    fbal = pay.faucet_balance_pda(org)
    job = pay.job_pda(repo, n, fbal)
    assert relay.precheck(net, c.payer, jwt, TERMS, JWKS, now=c.now()) is None       # nothing stands in its way
    r = go(env, jwt, TERMS)
    assert r == {"ok": True, "kind": "fund", "sigs": r["sigs"], "job": str(job), "repo_id": repo, "issue": n, "amount": 5 * USDC, "mode": 0,
                 "faucet": True, "balance": str(fbal), "deadline": c.now() + 14 * 86_400}, r
    j = pay.read_job(c.data(job))
    assert (j.state, j.faucet, j.amount, j.funder_id, j.terms) == ("open", True, 5 * USDC, MAINT, TH)
    # the terms are read back from the funding transaction's log
    assert net.terms_of(job) == TERMS and net.log_of(job, "knos2:terms ") == "knos2:terms " + TERMS.decode()
    assert net.log_of(job, "knos2:funded ").startswith(f"knos2:funded repo={repo} issue={n} amount=5000000 mode=0 by={MAINT} source={fbal} faucet=1")
    assert net.log_of(job, "knos2:paid") is None and net.log_of(Keypair().pubkey(), "knos2:terms ") is None
    # the proof names the address the author gave in the pull request: paid at once, less the fee
    r = go(env, pay_jwt(c, repo, n, payee, wallet))
    assert r == {"ok": True, "kind": "pay", "sigs": r["sigs"], "repo_id": repo, "issue": n, "payee_id": payee, "head": "a" * 40,
                 "paid": [{"job": str(job), "amount": 5 * USDC, "fee": 125_000, "mint": str(pay.faucet_mint()), "to": str(wallet), "held_until": None}]}, r
    assert c.balance(pay.ata(wallet, pay.faucet_mint())) == 4_875_000 and c.data(job) is None
    assert c.balance(pay.ata(pay.FEE_OWNER, pay.faucet_mint())) >= 125_000         # the relay made the fee account too
    # the payee binds a wallet from their own knos-claim repository: nothing is held, so nothing else moves
    bound = Keypair().pubkey()
    r = go(env, bind_jwt(c, payee, bound))
    assert r == {"ok": True, "kind": "bind", "sigs": r["sigs"], "user_id": payee, "wallet": str(bound), "settled": []}, r
    assert pay.read_bind(c.data(pay.bind_pda(payee))).wallet == bound
    # every token account was closed behind its token: the relayer's rent came back
    assert net.program_accounts(oidc.OIDC_ID, None, {50: bytes(c.payer.pubkey())}) == []


def test_a_comment_spends_a_balance_of_real_money_in_either_token_program(env):
    c, net = env
    n, job = funded(env, amount=20 * USDC)
    j = pay.read_job(c.data(job))
    assert (j.faucet, j.from_balance, j.source, j.mint, j.owner_id, j.funder_id) == (False, True, c.bal, c.usdc, OWNER, MAINT)
    payee, wallet = user(), Keypair().pubkey()
    r = go(env, pay_jwt(c, REPO, n, payee, wallet))
    assert r["ok"] and r["paid"][0]["mint"] == str(c.usdc) and c.balance(pay.ata(wallet, c.usdc)) == 19_500_000, r
    # a Token-2022 stablecoin with its usual extensions: the relay passes the Balance's token program, and makes the
    # payee's and the fee owner's token accounts with it
    me = c.payer.pubkey()
    coin = c.new_mint22(fee=(0, 0), confidential=True, permanent_delegate=me, hook=(me, None))
    w, wtok = c.wallet(coin, 100 * USDC)
    org, repo, n = user(), user(), issue()
    assert c.send([pay.open_balance_ix(w.pubkey(), org, coin, token_program=pay.TOKEN_2022)], w), c.err
    bal = pay.balance_pda(org, w.pubkey(), coin)
    transfer(c, wtok, pay.baltok_pda(bal), 50 * USDC, w, coin)
    r = go(env, fund_jwt(c, n, 10 * USDC, bal, actor_id=org, repository_owner_id=org, repository_id=repo), TERMS)
    assert r["ok"] and not r["faucet"] and pay.read_job(c.data(Pubkey.from_string(r["job"]))).token_program == pay.TOKEN_2022, r
    assert relay.balances_for(net, org) == [(bal, pay.read_balance(c.data(bal)), 40 * USDC)]
    r = go(env, pay_jwt(c, repo, n, payee, wallet))
    assert r["ok"], r
    assert c.balance(pay.ata(wallet, coin, pay.TOKEN_2022)) == 9_750_000 and c.balance(pay.ata(pay.FEE_OWNER, coin, pay.TOKEN_2022)) == 250_000


def test_with_no_wallet_the_payment_is_held_then_bound_then_settled(env):
    c, net = env
    n, job = funded(env, amount=8 * USDC)
    n2, job2 = funded(env, amount=4 * USDC)
    payee = user()
    r = go(env, pay_jwt(c, REPO, n, payee))                    # the author gave no address and has bound no wallet
    until = c.now() + pay.HOLD
    assert r["ok"] and r["paid"] == [{"job": str(job), "amount": 8 * USDC, "fee": 200_000, "mint": str(c.usdc), "to": None, "held_until": until}], r
    assert go(env, pay_jwt(c, REPO, n2, payee))["paid"][0]["held_until"] == c.now() + pay.HOLD
    assert [a for a, _j in relay.held_for(net, payee)] == sorted([job, job2], key=str) and relay.held_for(net, user()) == []
    assert [(a, j.state) for a, j in relay.jobs_for(net, REPO, n)] == [(job, "held")]
    # the claim binds the wallet and pays everything that was waiting, in the same relay
    wallet = Keypair().pubkey()
    r = go(env, bind_jwt(c, payee, wallet))
    assert r["ok"] and r["wallet"] == str(wallet) and {(s["job"], s["amount"], s["fee"]) for s in r["settled"]} == \
        {(str(job), 8 * USDC, 200_000), (str(job2), 4 * USDC, 100_000)}, r
    assert c.balance(pay.ata(wallet, c.usdc)) == 7_800_000 + 3_900_000 and relay.held_for(net, payee) == []
    # from now on a proof for this payee pays the bound wallet, whatever address it carries
    n3, job3 = funded(env)
    r = go(env, pay_jwt(c, REPO, n3, payee, Keypair().pubkey()))
    assert r["ok"] and r["paid"][0]["to"] == str(wallet) and c.balance(pay.ata(wallet, c.usdc)) == 7_800_000 + 3_900_000 + 4_875_000, r


def test_one_proof_pays_every_open_job_of_the_issue_that_pins_its_workflow(env):
    c, net = env
    n, own = funded(env, amount=5 * USDC)
    added = []
    for _ in range(6):                                          # six sponsors' wallets add their own bounty to the issue
        w, wtok = c.wallet(c.usdc, 100 * USDC)
        assert c.send([pay.fund_wallet_ix(w.pubkey(), wtok, c.usdc, REPO, n, 20 * USDC, WF_REPO, WF_SHA, TERMS)], w), c.err
        added.append(pay.job_pda(REPO, n, w.pubkey()))
    squatter, qtok = c.wallet(c.usdc, 5 * USDC)                 # and a stranger pins a workflow of their own
    assert c.send([pay.fund_wallet_ix(squatter.pubkey(), qtok, c.usdc, REPO, n, 5 * USDC, "evil/workflows", "f" * 40, TERMS)], squatter), c.err
    theirs = pay.job_pda(REPO, n, squatter.pubkey())
    assert {a for a, _j in relay.jobs_for(net, REPO, n)} == {own, theirs, *added}
    payee, wallet = user(), Keypair().pubkey()
    jwt = pay_jwt(c, REPO, n, payee, wallet)
    net.spent()
    r = go(env, jwt)
    assert r["ok"] and {p["job"] for p in r["paid"]} == {str(own), *map(str, added)} and all(p["to"] == str(wallet) for p in r["paid"]), r
    assert c.balance(pay.ata(wallet, c.usdc)) == 4_875_000 + 6 * 19_500_000
    assert [(a, j.state) for a, j in relay.jobs_for(net, REPO, n)] == [(theirs, "open")]      # the stranger's job is not this proof's to pay
    # two payments rode with the last Step; the others went in two transactions side by side, one wait for both; then the Close
    assert net.shape == [2, 1, 1, 2, 1] and c.data(oidc.token_pda(c.payer.pubkey(), oidc.token_id(jwt))) is None, net.shape


def test_the_longest_terms_do_not_fit_beside_the_last_step(env):
    c, net = env
    terms = b'{"paths":["' + b"x" * 580 + b'"],"v":1}'
    assert len(terms) == pay.MAX_TERMS
    org, repo, n = user(), user(), issue()
    jwt = faucet_jwt(c, n, org, repo, terms=terms)
    net.spent()
    net.sizes.clear()
    r = go(env, jwt, terms)
    assert r["ok"] and net.terms_of(Pubkey.from_string(r["job"])) == terms, r
    # the faucet's instruction rides with the last Step; the funding, with its 600 bytes, has a transaction to itself,
    # and not even the Close fits beside it: two transactions and two waits more than a bounty with ordinary terms
    assert net.shape == [2, 1, 1, 1, 1] and max(net.sizes) <= relay.ROOM < chain.MAX_TX_BYTES, (net.shape, net.sizes)
    assert net.sizes[4] == chain.tx_size([pay.fund_balance_ix(c.payer.pubkey(), oidc.token_pda(c.payer.pubkey(), oidc.token_id(jwt)), c.key,
                                                              pay.faucet_balance_pda(org), pay.faucet_mint(), repo, n, terms)], c.payer.pubkey()) == 1175


# -- refused before any fee ----------------------------------------------------------------------------------------------
def _capped(c: Chain) -> Pubkey:
    """A second wallet's Balance for the same owner: 30 test USDC in it, at most 10 for one bounty."""
    if not hasattr(c, "capped"):
        w, wtok = c.wallet(c.usdc, 30 * USDC)
        assert c.send([pay.open_balance_ix(w.pubkey(), OWNER, c.usdc, cap=10 * USDC, spenders=[MAINT])], w), c.err
        c.capped = pay.balance_pda(OWNER, w.pubkey(), c.usdc)
        transfer(c, wtok, pay.baltok_pda(c.capped), 30 * USDC, w)
    return c.capped


def _raw_fund(c: Chain, aud: str) -> str:
    c.warp(1)
    return token(c, aud, file="fund.yml", event_name="issue_comment", actor_id=MAINT)


def _gitlab(c: Chain, n: int) -> str:
    """A token GitLab's key signed, saying everything a fund token says."""
    return token(c, pay.fund_audience(n, 5 * USDC, 0, TH, c.bal), key=signing_key(4096), iss=oidc.ISSUERS[oidc.GITLAB],
                 file="fund.yml", event_name="issue_comment", actor_id=MAINT)


def _forged(c: Chain, n: int) -> str:
    """Everything a fund token says, signed by a key that is not GitHub's but calls itself by GitHub's key id."""
    c.warp(1)
    return token(c, pay.fund_audience(n, 5 * USDC, 0, TH, c.bal), key=_STRANGER, file="fund.yml", event_name="issue_comment", actor_id=MAINT)


_STRANGER = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER = pay.terms_json({"checks": [], "v": 1})
# what is wrong -> (the token for issue n, the terms that come with it, what the relay says)
FUND_REFUSALS = {
    "a token from prove.yml": (lambda c, n: fund_jwt(c, n, file="prove.yml"), TERMS, "a fund token must come from fund.yml"),
    "a pull request event": (lambda c, n: fund_jwt(c, n, event_name="pull_request_target"), TERMS, "funded by a comment on an issue or by a new issue"),
    "a re-run of the commenter's run": (lambda c, n: fund_jwt(c, n, run_attempt=2), TERMS, "only a run's first attempt can fund"),
    "a self-hosted runner": (lambda c, n: fund_jwt(c, n, runner_environment="self-hosted"), TERMS, "not from a GitHub-hosted runner"),
    "terms that are not the ones GitHub signed for": (lambda c, n: fund_jwt(c, n), _OTHER, "not the terms GitHub signed for"),
    "no terms": (lambda c, n: fund_jwt(c, n), None, "the bounty's terms did not come with its token"),
    "less than the smallest bounty": (lambda c, n: fund_jwt(c, n, pay.MIN_AMOUNT - 1), TERMS, "a bounty is from 1.00 to 500.00; this token asks for 1.00"),
    "more than the largest bounty": (lambda c, n: fund_jwt(c, n, pay.MAX_AMOUNT + 1), TERMS, "a bounty is from 1.00 to 500.00"),
    "more than 90 days to do the work": (lambda c, n: fund_jwt(c, n, work=pay.MAX_WORK + 1), TERMS, "a bounty is open for a minute to 90 days"),
    "a balance that does not exist": (lambda c, n: fund_jwt(c, n, balance=pay.balance_pda(OWNER, Keypair().pubkey(), c.usdc)), TERMS, "does not exist"),
    "a comment in another owner's repository": (lambda c, n: fund_jwt(c, n, repository_owner_id=999_999, repository_id=555_555), TERMS, "another GitHub owner's repositories"),
    "a commenter the balance does not list": (lambda c, n: fund_jwt(c, n, actor_id=1234567), TERMS, "this commenter may not spend that balance"),
    "more than the balance allows for one bounty": (lambda c, n: fund_jwt(c, n, 11 * USDC, _capped(c)), TERMS, "that balance allows 10.00 for one bounty; this token asks for 11.00"),
    "more than the balance holds": (lambda c, n: fund_jwt(c, n, 10 * USDC, _capped(c)), TERMS, "the balance holds 0.00, less than this bounty; add money to it or fund less"),
    "more than the faucet gives": (lambda c, n: faucet_jwt(c, n, user(), user(), pay.FAUCET_CAP + 1), TERMS, "the faucet gives at most 100.00 test USDC for one bounty"),
    "an audience with a part missing": (lambda c, n: _raw_fund(c, f"knos2:fund:{n}:5000000:0:{TH.hex()}:1209600"), TERMS, "malformed audience or claims"),
    "a balance address that is not the way Solana prints it": (lambda c, n: _raw_fund(c, f"knos2:fund:{n}:5000000:0:{TH.hex()}:1209600:1{c.bal}"), TERMS, "malformed audience or claims"),
    "a mode that is neither merge nor tests": (lambda c, n: fund_jwt(c, n, mode=2), TERMS, "malformed audience or claims"),
    "a token dated ahead of the chain": (lambda c, n: fund_jwt(c, n, iat=c.now() + 302, exp=c.now() + 602), TERMS, "the token's times are not GitHub's"),
    "a token that claims to live for hours": (lambda c, n: fund_jwt(c, n, exp=c.now() + 3602), TERMS, "the token's times are not GitHub's"),
    "an expired token": (lambda c, n: fund_jwt(c, n, iat=c.now() - 4000, exp=c.now() - 3700), TERMS, "token expired"),
    "a GitLab token": (_gitlab, TERMS, "the escrow takes GitHub's tokens only"),
    "a signature that is not GitHub's": (_forged, TERMS, "the signature is not the issuer's"),
    "a key id GitHub does not publish": (lambda c, n: sign_jwt(signing_key(), github_claims(aud=pay.fund_audience(n, 5 * USDC, 0, TH, c.bal)), header={"alg": "RS256", "kid": "gone"}),
                                         TERMS, "the issuer's key set has no key 'gone'"),
    "an audience that is not Knos's": (lambda c, n: token(c, "sts.amazonaws.com"), TERMS, "not an audience of the second deployment: 'sts.amazonaws.com'"),
    "a first-deployment audience": (lambda c, n: token(c, f"knos:fund:{n}:5000000:0:{TH.hex()}:1209600:0"), TERMS, "not an audience of the second deployment"),
    "something that is not a token": (lambda c, n: "not.a.jwt", TERMS, "this is not a token GitHub Actions or GitLab CI issued"),
}
ALL_JWKS = {**JWKS, oidc.GITLAB: {"keys": [{"kty": "RSA", "alg": "RS256", "e": "AQAB", "kid": "k", "n": b64(oidc.modulus_bytes(modulus(signing_key(4096))))}]}}


def refused(env, jwt: str, terms: bytes | None, want: str | None, payer: Keypair | None = None) -> dict:
    """The relay's answer to a token the chain would refuse: the same from `precheck` and from `submit`, with no
    transaction and not a lamport spent."""
    c, net = env
    before, n0 = lamports(c, payer), net.txs
    r = relay.submit(net, payer or c.payer, jwt, terms, ALL_JWKS, now=c.now())
    assert not r["ok"] and (want is None or want in r["why"]), r
    assert (lamports(c, payer), net.txs) == (before, n0), f"a fee was spent on: {r['why']}"
    assert relay.precheck(net, payer or c.payer, jwt, terms, ALL_JWKS, now=c.now()) == r
    return r


@pytest.mark.parametrize("what", list(FUND_REFUSALS))
def test_a_fund_token_costs_nothing_with(env, what):
    c, _net = env
    make, terms, want = FUND_REFUSALS[what]
    n = issue()
    if what == "more than the balance holds":
        for _ in range(3):                  # the Balance holds 30; three bounties of 10 empty it
            assert go(env, fund_jwt(c, issue(), 10 * USDC, _capped(c)), TERMS)["ok"]
    r = refused(env, make(c, n), terms, want)
    assert r["kind"] == (None if what in ("an audience that is not Knos's", "a first-deployment audience", "something that is not a token") else "fund")
    assert c.data(pay.job_pda(REPO, n, c.bal)) is None


def test_a_fund_token_is_refused_by_what_the_chain_already_holds(env):
    c, net = env
    # an older comment's token, after a newer one was carried: the Balance takes its tokens in the order GitHub issued them
    n_old, n = issue(), issue()
    old = fund_jwt(c, n_old)
    new = fund_jwt(c, n)
    assert go(env, new, TERMS)["ok"]
    assert refused(env, old, TERMS, "an older fund token than the last one this balance took; comment again") == \
        {"ok": False, "kind": "fund", "why": "an older fund token than the last one this balance took; comment again"}
    # another comment on an issue that already has this Balance's bounty
    job = pay.job_pda(REPO, n, c.bal)
    assert refused(env, fund_jwt(c, n, 6 * USDC), TERMS, f"this issue already has a bounty from this balance (job {job})")
    # the faucet serves a repository once a minute, in the order of the tokens: the next comment waits, and is told how long
    org, repo = user(), user()
    assert go(env, faucet_jwt(c, issue(), org, repo), TERMS)["ok"]
    late = faucet_jwt(c, issue(), org, repo)
    c.warp(9)
    r = refused(env, late, TERMS, "the faucet serves a repository once a minute")
    assert r == {"ok": False, "kind": "fund", "why": "the faucet serves a repository once a minute", "retry": True, "wait": 50}
    c.warp(50)
    assert go(env, late, TERMS)["ok"]
    # the faucet opened for a token whose funding then never landed: a token GitHub issued before that one is too old for it
    org2, repo2 = user(), user()
    early = faucet_jwt(c, issue(), org2, repo2, 6 * USDC)
    c.warp(1)
    tok = c.gh(pay.fund_audience(issue(), 5 * USDC, 0, TH, pay.faucet_balance_pda(org2)), file="fund.yml", event_name="issue_comment", actor_id=MAINT,
               repository_owner_id=org2, repository_id=repo2)
    assert c.send([pay.faucet_open_ix(c.payer.pubkey(), tok, c.key, org2, repo2)]), c.err
    assert refused(env, early, TERMS, "an older fund token than the repository's last one; comment again")
    # test USDC that came back to the faucet Balance (an unproven bounty) is spent without asking the faucet again
    n_back = issue()
    c.warp(60)
    assert go(env, faucet_jwt(c, n_back, org, repo, work=60), TERMS)["ok"]
    c.warp(61)
    assert len(relay.refund_due(net, c.payer, c.now())) == 1 and c.balance(pay.baltok_pda(pay.faucet_balance_pda(org))) == 5 * USDC
    rate = c.data(pay.rate_pda(repo))
    assert go(env, faucet_jwt(c, issue(), org, repo), TERMS)["ok"]             # inside the faucet's minute: it was not needed
    assert c.data(pay.rate_pda(repo)) == rate and c.balance(pay.baltok_pda(pay.faucet_balance_pda(org))) == 0


def _job(env, **kw) -> tuple[int, Pubkey]:
    return funded(env, **kw)


# what is wrong -> (the pay token for the job on issue n, what the relay says)
PAY_REFUSALS = {
    "a fund.yml token": (lambda c, n: pay_jwt(c, REPO, n, 77, file="fund.yml"), "not from the workflow or the repository the audience names"),
    "a run in another repository": (lambda c, n: token(c, pay.pay_audience(REPO, n, 77, "a" * 40, TH, 0), repository_id=111), "not from the workflow or the repository the audience names"),
    "an issue with no bounty": (lambda c, n: pay_jwt(c, REPO, n + 100_000, 77), "no bounty is in escrow for this issue (never funded, or already paid or refunded)"),
    "prove.yml of another repository": (lambda c, n: pay_jwt(c, REPO, n, 77, wf_repo="evil/Knos"), "no open bounty on this issue pins this workflow at this commit"),
    "prove.yml at another commit": (lambda c, n: pay_jwt(c, REPO, n, 77, wf_sha="d" * 40), "no open bounty on this issue pins this workflow at this commit"),
    "other terms": (lambda c, n: pay_jwt(c, REPO, n, 77, terms=_OTHER), "the open bounty on this issue has other terms than the ones this token was made for"),
    "the other mode": (lambda c, n: pay_jwt(c, REPO, n, 77, mode=pay.TESTS), "the open bounty on this issue has other terms"),
    "a proof made before the funding": (lambda c, n: pay_jwt(c, REPO, n, 77, iat=c.now() - 1800, exp=c.now() - 1500), "this token is older than the bounty"),
    "payee 0": (lambda c, n: pay_jwt(c, REPO, n, 0), "malformed audience or claims"),
    "a head that is not a commit": (lambda c, n: token(c, pay.pay_audience(REPO, n, 77, "main", TH, 0)), "malformed audience or claims"),
    "an address that is not a key": (lambda c, n: token(c, pay.pay_audience(REPO, n, 77, "a" * 40, TH, 0)[:-1] + "0OIl"), "malformed audience or claims"),
    "the escrow's own account as the address": (lambda c, n: pay_jwt(c, REPO, n, 77, pay.auth_pda()), "the address this token names is the escrow's own account"),
    "a self-hosted runner": (lambda c, n: pay_jwt(c, REPO, n, 77, runner_environment="self-hosted"), "not from a GitHub-hosted runner"),
}


@pytest.mark.parametrize("what", list(PAY_REFUSALS))
def test_a_proof_costs_nothing_with(env, what):
    c, _net = env
    make, want = PAY_REFUSALS[what]
    n, job = _job(env)
    before = c.data(job)
    assert refused(env, make(c, n), None, want)["kind"] == "pay"
    assert c.data(job) == before


def test_a_proof_is_refused_by_what_the_chain_already_holds(env):
    c, net = env
    n, job = _job(env, work=60)
    late = pay_jwt(c, REPO, n, 77, Keypair().pubkey())
    c.warp(61)
    assert refused(env, late, None, "the bounty's deadline has passed: it goes back to its funder")
    # held for one user: a proof for another finds nothing to take
    n, job = _job(env)
    first = user()
    assert go(env, pay_jwt(c, REPO, n, first))["paid"][0]["to"] is None
    assert refused(env, pay_jwt(c, REPO, n, user(), Keypair().pubkey()), None, "the bounty on this issue is already held for another GitHub user")


# what is wrong -> (the bind token for user `who`, what the relay says)
BIND_REFUSALS = {
    "another workflow file of the pinned repository": (lambda c, who: bind_jwt(c, who, Keypair().pubkey(), file="rotate.yml"), "a wallet is bound only by Knos's claim workflow at its pinned commit"),
    "a claim.yml of another repository": (lambda c, who: bind_jwt(c, who, Keypair().pubkey(), wf_repo="evil/knos-oidc-rotate"), "a wallet is bound only by Knos's claim workflow"),
    "another commit of the claim workflow": (lambda c, who: bind_jwt(c, who, Keypair().pubkey(), wf_sha="3" * 40), "a wallet is bound only by Knos's claim workflow"),
    "a repository with another name": (lambda c, who: bind_jwt(c, who, Keypair().pubkey(), repository=f"user{who}/dotfiles"), "the claim must run in a repository named knos-claim that the claiming account owns"),
    "an actor who does not own the repository": (lambda c, who: bind_jwt(c, who, Keypair().pubkey(), repository_owner_id=who + 1), "the claim must run in a repository named knos-claim"),
    "a comment": (lambda c, who: bind_jwt(c, who, Keypair().pubkey(), event_name="issue_comment"), "the claim must be started by hand by the account's owner"),
    "the push that made the repository (its first run)": (lambda c, who: bind_jwt(c, who, Keypair().pubkey(), event_name="push", run_number=1), "the claim must be started by hand by the account's owner"),
    "the real pin's first push": (lambda c, who: bind_jwt(c, who, Keypair().pubkey(), wf_sha=pay.IDS["claim_sha"], event_name="push", run_number=1), "the claim must be started by hand by the account's owner"),
    "a later push": (lambda c, who: bind_jwt(c, who, Keypair().pubkey(), event_name="push", run_number=2), "the claim must be started by hand by the account's owner"),
    "a re-run": (lambda c, who: bind_jwt(c, who, Keypair().pubkey(), run_attempt=2), "and not be a re-run"),
    "an address that is not a key": (lambda c, who: token(c, "knos2:bind:" + "1" * 31, file="claim.yml", wf_repo="drexthealpha/knos-oidc-rotate", wf_sha=TEST_CLAIM_SHA), "malformed audience or claims"),
    "a self-hosted runner": (lambda c, who: bind_jwt(c, who, Keypair().pubkey(), runner_environment="self-hosted"), "not from a GitHub-hosted runner"),
}


@pytest.mark.parametrize("what", list(BIND_REFUSALS))
def test_a_claim_costs_nothing_with(env, what):
    c, _net = env
    make, want = BIND_REFUSALS[what]
    who = user()
    assert refused(env, make(c, who), None, want)["kind"] == "bind"
    assert c.data(pay.bind_pda(who)) is None


def test_an_older_claim_does_not_undo_a_newer_one_and_the_real_pin_binds(env):
    c, _net = env
    who, wallet = user(), Keypair().pubkey()
    old = bind_jwt(c, who, Keypair().pubkey())
    assert go(env, bind_jwt(c, who, wallet))["ok"]
    assert refused(env, old, None, "a newer claim has bound a wallet since this one; run the claim again to change it")
    assert go(env, bind_jwt(c, who, wallet, wf_sha=pay.IDS["claim_sha"]))["ok"]      # the real pin, started by hand


def test_a_token_whose_signing_key_the_chain_would_refuse_costs_nothing():
    """On a chain of its own: a revocation is for ever."""
    c = Chain()
    env = (c, Net(c))
    assert c.send([pay.init_faucet_ix(c.payer.pubkey())]), c.err
    org, repo = user(), user()
    # a key GitHub publishes, and that signed this token, which the chain has not been given: not GitHub's genesis keys, so not the relay's to register
    both = {oidc.GITHUB: {"keys": [*JWKS[oidc.GITHUB]["keys"], {"kty": "RSA", "alg": "RS256", "e": "AQAB", "kid": "new", "n": b64(oidc.modulus_bytes(modulus(_STRANGER)))}]}}
    jwt = sign_jwt(_STRANGER, github_claims(aud=pay.fund_audience(1, 5 * USDC, 0, TH, pay.faucet_balance_pda(org)), iat=c.now(), exp=c.now() + 300, actor_id=MAINT,
                                            event_name="issue_comment", repository_owner_id=org, repository_id=repo,
                                            job_workflow_ref=f"{WF_REPO}/.github/workflows/fund.yml@refs/tags/v0.3.12"), header={"alg": "RS256", "kid": "new"})
    n0 = env[1].txs
    r = relay.submit(env[1], c.payer, jwt, TERMS, both, now=c.now())
    assert r == {"ok": False, "kind": "fund", "why": "the token cannot be verified: this signing key is not on chain yet. Send RegisterKey with an "
                                                     "attestation from the rotate workflow."} and env[1].txs == n0
    good = faucet_jwt(c, 2, org, repo)
    assert c.revoke(oidc.GITHUB, c.github), c.err
    r = refused(env, good, TERMS, "the token cannot be verified: the guardian revoked this signing key. It cannot be used again.")
    assert "retry" not in r


def test_a_token_under_a_key_that_has_expired_costs_nothing():
    """On a chain of its own: the clock moves a month, and no rotate workflow refreshed GitHub's key."""
    c = Chain()
    env = (c, Net(c))
    assert c.send([pay.init_faucet_ix(c.payer.pubkey())]), c.err
    c.warp(oidc.KEY_TTL - 1)                                    # the key was registered when the chain began; the token is issued a second later
    r = refused(env, faucet_jwt(c, 1, user(), user()), TERMS, "the token cannot be verified: this signing key expired")
    assert "Run the rotate workflow and send Refresh with its token." in r["why"] and "retry" not in r


def test_while_new_funding_is_paused_a_fund_token_waits_and_payments_go_on():
    """On a chain of its own: the guardian's pause stops every new bounty there."""
    c = Chain()
    net = Net(c)
    env = (c, net)
    assert c.send([pay.init_faucet_ix(c.payer.pubkey())]), c.err
    org, repo, n, payee, wallet = user(), user(), issue(), user(), Keypair().pubkey()
    assert go(env, faucet_jwt(c, n, org, repo), TERMS)["ok"]
    pause = lambda seconds: c.send([pay.pause_ix(GUARDIAN.pubkey(), c.payer.pubkey(), seconds)], signers=[GUARDIAN]) or pytest.fail(c.err)  # noqa: E731
    pause(600)
    until = c.now() + 600
    c.warp(60)
    jwt = faucet_jwt(c, issue(), org, repo)
    # the pause ends while this token still works: the relay says how long to wait, and spends nothing meanwhile
    assert refused(env, jwt, TERMS, None) == {"ok": False, "kind": "fund", "retry": True, "wait": until - c.now(),
                                              "why": f"new funding is paused until {relay._when(until)}; payments and refunds go on"}
    assert go(env, pay_jwt(c, repo, n, payee, wallet))["ok"] and c.balance(pay.ata(wallet, pay.faucet_mint())) == 4_875_000
    pause(pay.PAUSE_MAX)                                         # a pause that outlasts the token: refused, and not to be tried again
    assert "retry" not in refused(env, jwt, TERMS, "new funding is paused until")
    pause(0)
    assert go(env, jwt, TERMS)["ok"]


def test_a_jobs_terms_are_the_ones_its_hash_names_whatever_else_was_logged_at_its_address(env):
    """knos-pay keeps the hash of a job's terms and logs the JSON when the job is funded. The log is read back by the
    job's address, which is used again when a bounty went back and the issue is funded anew, and which anyone can
    name in a transaction of their own."""
    c, net = env
    org, repo, n = user(), user(), issue()
    job = Pubkey.from_string(go(env, faucet_jwt(c, n, org, repo, work=60), TERMS)["job"])
    c.warp(61)
    assert relay.refund_due(net, c.payer, c.now()) and c.data(job) is None
    assert net.terms_of(job) == TERMS                           # a job that is gone: what it was last funded with
    assert go(env, faucet_jwt(c, n, org, repo, terms=_OTHER), _OTHER)["job"] == str(job)
    assert net.terms_of(job) == _OTHER and net.log_of(job, "knos2:terms ") == "knos2:terms " + _OTHER.decode()
    theirs = "4Nd1mBQtrMJVYVfKf2PJy9NZUZdTAsp7D4xWLs4gDB4T"
    net.named[job].append("someone else's")
    net.said["someone else's"] = [f"Program {theirs} invoke [1]", 'Program log: knos2:terms {"checks":[],"v":2}', f"Program {theirs} success"]
    assert net.log_of(job, "knos2:terms ") == 'knos2:terms {"checks":[],"v":2}'      # the newest line, unless told whose and which is meant
    assert net.log_of(job, "knos2:terms ", by=pay.PAY_ID) == "knos2:terms " + _OTHER.decode()
    assert net.log_of(job, "knos2:terms ", lambda line: '"v":1' in line) == "knos2:terms " + _OTHER.decode()
    assert net.terms_of(job) == _OTHER                          # the escrow's own line, and the hash in the job tells
    # knos-pay itself logging other terms in a transaction that names this address (someone funds a job of their own
    # and lists this one beside it) does not change the answer either
    net.named[job].append("their own job")
    net.said["their own job"] = [f"Program {pay.PAY_ID} invoke [1]", 'Program log: knos2:terms {"checks":[],"v":3}', f"Program {pay.PAY_ID} success"]
    assert net.log_of(job, "knos2:terms ", by=pay.PAY_ID) == 'knos2:terms {"checks":[],"v":3}' and net.terms_of(job) == _OTHER


def test_the_genesis_keys_are_the_ones_the_verifier_holds():
    """The relay registers a genesis key it meets with no attestation; it knows them by the same hashes as pins.rs."""
    pins = (ROOT / "programs-v2" / "knos_oidc" / "src" / "pins.rs").read_text(encoding="utf-8")
    body = pins[pins.index("pub const GENESIS"):]
    assert set(re.findall(r'\(0,\s*h\("([0-9a-f]{64})"\)\)', body[:body.index("];")])) == relay.GENESIS and len(relay.GENESIS) == 4
    github = oidc.jwks_keys(json.loads((FIX / "github_jwks_2026-10-02.json").read_text()))
    assert {oidc.key_hash(n).hex() for _kid, n in github} == relay.GENESIS
    # and the two workflows it knows a token by are the ones the programs pin
    escrow = (ROOT / "programs-v2" / "knos_pay" / "src" / "lib.rs").read_text(encoding="utf-8")
    assert f'CLAIM_REF: &[u8] = b"{relay.CLAIM_REF}"' in escrow and f'CLAIM_SHA: &[u8; 40] = b"{pay.IDS["claim_sha"]}"' in escrow
    assert f'ROTATE_REF: &[u8] = b"{relay.ROTATE_REF}"' in pins and f'ROTATE_SHA: &[u8; 40] = b"{oidc.IDS["rotate_sha"]}"' in pins
    assert relay.CLAIM_SHAS - {TEST_CLAIM_SHA} == {pay.IDS["claim_sha"]} and relay.ROTATE_SHAS - {TEST_ROTATE_SHA} == {oidc.IDS["rotate_sha"]}
    assert relay.ATTESTERS - {(OWNER, REPO)} == {(142920951, 1353152983), (142920951, 1401432540)}


# -- relayed twice, and by two relayers ------------------------------------------------------------------------------------
def test_a_token_relayed_twice_or_by_two_relayers_is_done_once(env):
    """The token is public: the worker and anyone else may carry it at once. Whoever comes second finds the work done,
    says so with the same result, and spends nothing."""
    c, net = env
    other = c.fund()                                            # a second relayer, with a key and SOL of its own
    org, repo, n = user(), user(), issue()
    fund = faucet_jwt(c, n, org, repo)
    first = go(env, fund, TERMS)
    assert first["ok"] and "already" not in first
    n0, sol = net.txs, (lamports(c), lamports(c, other))
    for payer in (other, c.payer):
        again = go(env, fund, TERMS, payer)
        assert again == {**first, "sigs": first["sigs"][-1:], "already": True}, again       # it names the transaction that did it
    assert go(env, fund, None, other)["already"]                # even with the terms lost on the way: the job is there
    # a proof with no address: held. The same proof again, from either relayer: held already, by that proof
    payee = user()
    proof = pay_jwt(c, repo, n, payee)
    held = go(env, proof)
    assert held["ok"] and held["paid"][0]["to"] is None
    n1 = net.txs
    for payer in (other, c.payer):
        again = go(env, proof, None, payer)
        assert again == {**held, "sigs": held["sigs"][-1:], "already": True}, again
    # the claim: bound, and the held payment sent. Again: bound already, nothing left to send
    wallet = Keypair().pubkey()
    claim = bind_jwt(c, payee, wallet)
    bound = go(env, claim)
    assert bound["ok"] and len(bound["settled"]) == 1 and c.balance(pay.ata(wallet, pay.faucet_mint())) == 4_875_000
    n2 = net.txs
    for payer in (other, c.payer):
        again = go(env, claim, None, payer)
        assert again == {**bound, "sigs": bound["sigs"][-1:], "already": True, "settled": []}, again
    assert (net.txs, lamports(c, other)) == (n2, sol[1]) and n1 - n0 == 4 and lamports(c) < sol[0]       # the second relayer never paid a lamport
    # a proof that paid a wallet leaves no job behind: the payee's record's last transactions say it was paid
    n = issue()
    c.warp(60)
    assert go(env, faucet_jwt(c, n, org, repo), TERMS)["ok"]
    proof = pay_jwt(c, repo, n, payee)
    paid = go(env, proof)
    assert paid["ok"] and paid["paid"][0]["to"] == str(wallet)
    n3 = net.txs
    again = go(env, proof, None, other)
    assert again == {"ok": True, "kind": "pay", "sigs": paid["sigs"][-1:], "already": True, "repo_id": repo, "issue": n, "payee_id": payee, "head": "a" * 40,
                     "paid": [{"job": "", "amount": 5 * USDC, "fee": 125_000, "mint": "", "to": str(wallet), "held_until": None}]}, again
    assert net.txs == n3 and relay.precheck(net, other, proof, None, JWKS, now=c.now()) == again
    # a proof for someone who was never paid for that issue is still what it was
    assert refused(env, pay_jwt(c, repo, n, user()), None, "no bounty is in escrow for this issue")


def test_a_token_carried_before_is_known_by_what_it_left_on_chain_and_by_nothing_else(env):
    """What "already" rests on: a job keeps its fund token's time and commenter; a payment is in the escrow's own log,
    at the proof's own time. Not on what the Balance took last, and not on a line anyone can write."""
    c, net = env
    n = issue()
    fund = fund_jwt(c, n)
    first = go(env, fund, TERMS)
    assert first["ok"] and go(env, fund_jwt(c, issue()), TERMS)["ok"]      # the Balance has taken a newer comment's token since
    assert go(env, fund, TERMS, c.fund()) == {**first, "sigs": first["sigs"][-1:], "already": True}
    assert refused(env, fund_jwt(c, n), TERMS, "this issue already has a bounty from this balance")      # another comment on that issue is another matter
    # a stranger's bounty on the issue pins a workflow of their own, so it stays open whatever this proof does
    squatter, qtok = c.wallet(c.usdc, 5 * USDC)
    assert c.send([pay.fund_wallet_ix(squatter.pubkey(), qtok, c.usdc, REPO, n, 5 * USDC, "evil/workflows", "f" * 40, TERMS)], squatter), c.err
    payee, wallet = user(), Keypair().pubkey()
    proof = pay_jwt(c, REPO, n, payee, wallet)
    paid = go(env, proof)
    assert paid["ok"] and len(paid["paid"]) == 1 and len(relay.jobs_for(net, REPO, n)) == 1
    again = go(env, proof, None, c.fund())
    assert again == {"ok": True, "kind": "pay", "sigs": paid["sigs"][-1:], "already": True, "repo_id": REPO, "issue": n, "payee_id": payee, "head": "a" * 40,
                     "paid": [{"job": "", "amount": 5 * USDC, "fee": 125_000, "mint": "", "to": str(wallet), "held_until": None}]}, again
    # later the issue is funded anew with other terms. A proof made for the old ones pays nothing, and that earlier
    # payment is not taken for its doing; nor is a line somebody else's program wrote at the payee's record
    c.warp(pay.TOKEN_AHEAD + 60)
    assert go(env, fund_jwt(c, n, terms=_OTHER), _OTHER)["ok"]
    stale = pay_jwt(c, REPO, n, payee, wallet)
    assert refused(env, stale, None, "the open bounty on this issue has other terms than the ones this token was made for")
    theirs = "4Nd1mBQtrMJVYVfKf2PJy9NZUZdTAsp7D4xWLs4gDB4T"
    net.named[pay.rep_pda(payee)].append("forged")
    net.when["forged"] = c.now()
    net.said["forged"] = [f"Program {theirs} invoke [1]", f"Program log: knos2:paid repo={REPO} issue={n} payee={payee} amount=4875000 fee=125000 to={wallet}",
                          f"Program {theirs} success"]
    assert refused(env, stale, None, "the open bounty on this issue has other terms than the ones this token was made for")


def test_a_claim_another_relayer_bound_still_sends_what_was_left_held(env):
    """Two relayers carry one claim. The first one's Bind lands and its payments do not (it stopped there). The second
    finds the wallet bound by that very token, and sends the payments: they need no token."""
    c, net = env
    payee, wallet = user(), Keypair().pubkey()
    n, job = funded(env, amount=6 * USDC)
    assert go(env, pay_jwt(c, REPO, n, payee))["paid"][0]["to"] is None
    claim = bind_jwt(c, payee, wallet)
    tok = c.verify(claim, oidc.GITHUB, c.github)                 # the first relayer, by hand: verify, Bind, nothing more
    assert c.send([pay.bind_ix(c.payer.pubkey(), tok, c.key, payee)]), c.err
    other = c.fund()
    net.spent()
    r = go(env, claim, None, other)
    assert r == {"ok": True, "kind": "bind", "sigs": r["sigs"], "already": True, "user_id": payee, "wallet": str(wallet),
                 "settled": [{"job": str(job), "amount": 6 * USDC, "fee": 150_000, "mint": str(c.usdc)}]}, r
    assert (net.txs, net.waits) == (1, 1) and c.balance(pay.ata(wallet, c.usdc)) == 5_850_000      # one transaction: no token was verified for it


def test_a_claim_whose_token_is_verified_already_binds_before_it_sends_what_was_held(env):
    """A run was cut short after the claim's token was verified. The next one has no Step to send: the Bind still
    lands before the payments it frees, which need the wallet bound."""
    c, net = env
    payee, wallet = user(), Keypair().pubkey()
    held = [funded(env, amount=6 * USDC)[1], funded(env, amount=7 * USDC)[1], funded(env, amount=8 * USDC)[1]]
    for n in [pay.read_job(c.data(job)).issue for job in held]:
        assert go(env, pay_jwt(c, REPO, n, payee))["paid"][0]["to"] is None
    claim = bind_jwt(c, payee, wallet)
    assert c.verify(claim, oidc.GITHUB, c.github) == oidc.token_pda(c.payer.pubkey(), oidc.token_id(claim))
    net.spent()
    r = go(env, claim)
    assert r["ok"] and {s["job"] for s in r["settled"]} == set(map(str, held)) and "already" not in r, r
    assert net.shape[0] == 1 and sum(net.shape) == net.txs <= 3 and c.balance(pay.ata(wallet, c.usdc)) == 5_850_000 + 6_825_000 + 7_800_000
    assert c.data(oidc.token_pda(c.payer.pubkey(), oidc.token_id(claim))) is None


# -- when the cluster, not the token, is the reason ------------------------------------------------------------------------
class Flaky:
    """The ledger, with trouble: `fail(i, why)` makes the i-th send from now raise `why` (the transaction does not
    land), `lose(i)` lets it land and then says it was never confirmed."""

    def __init__(self, net: Net):
        self.net, self.trouble, self.i = net, {}, 0

    def __getattr__(self, name):
        return getattr(self.net, name)

    def fail(self, i: int, why: Exception) -> "Flaky":
        self.trouble[self.i + i] = (False, why)
        return self

    def lose(self, i: int) -> "Flaky":
        self.trouble[self.i + i] = (True, TimeoutError("sig not confirmed within 60s"))
        return self

    def send(self, ixs, payer, signers=None):
        self.i += 1
        lands, why = self.trouble.pop(self.i, (True, None))
        sig = self.net.send(ixs, payer, signers) if lands else None
        if why is not None:
            raise why
        return sig

    def send_all(self, groups, payer, signers=None):
        return [self.send(ixs, payer, signers) for ixs in groups]


def test_a_hiccup_of_the_cluster_is_tried_again_and_a_refusal_is_not(env):
    c, net = env
    org, repo, n = user(), user(), issue()
    fund = faucet_jwt(c, n, org, repo)
    twin = chain.RpcError("transaction failed: {'InstructionError': [1, {'Custom': 69}]}")       # a twin run moved the token account
    for nth, why, said in ((1, TimeoutError("sig not confirmed within 60s"), "TimeoutError: sig not confirmed within 60s"),
                           (3, twin, "another run moved this token's account (error 69)"),
                           (4, OSError("connection reset"), "OSError: connection reset")):
        r = relay.submit(Flaky(net).fail(nth, why), c.payer, fund, TERMS, JWKS, now=c.now())
        assert r == {"ok": False, "kind": "fund", "why": said, "retry": True, "transient": True}, r
        assert c.data(oidc.token_pda(c.payer.pubkey(), oidc.token_id(fund))) is None           # the rent came back each time
        assert c.data(pay.job_pda(repo, n, pay.faucet_balance_pda(org))) is None
    assert go(env, fund, TERMS)["ok"]                            # the same token, on the next pass
    assert relay.transient(TimeoutError()) and relay.transient(chain.RpcError("custom program error: 0x54")) and relay.transient(chain.RpcError("Blockhash not found"))
    assert not relay.transient(chain.RpcError("transaction failed: {'InstructionError': [0, {'Custom': 91}]}"))
    # the escrow's own refusal, in the words of its client, is a verdict: the job was funded between the read and the send
    n = issue()
    late = fund_jwt(c, n)

    class Raced(Flaky):
        def send(self, ixs, payer, signers=None):
            if len(ixs) > 1 and self.i == 2:                     # just before the last transaction, the newer comment's token lands
                assert go(env, fund_jwt(c, n), TERMS)["ok"]
            return super().send(ixs, payer, signers)
    r = relay.submit(Raced(net), c.payer, late, TERMS, JWKS, now=c.now())
    assert r == {"ok": False, "kind": "fund", "why": pay.ERRORS[91] + " (error 91)"} and c.data(oidc.token_pda(c.payer.pubkey(), oidc.token_id(late))) is None
    # the faucet's minute, taken by another token between the read and the send, is waited out
    assert relay._failed("fund", chain.RpcError("custom program error: 0x5a")) == {"ok": False, "kind": "fund", "why": "the faucet serves a repository once a minute",
                                                                                 "retry": True, "wait": 60}
    # a relayer whose key holds no SOL is told so, and the token is not blamed
    broke = Keypair()
    c.warp(60)
    r = go(env, faucet_jwt(c, issue(), org, repo), TERMS, broke)
    assert r == {"ok": False, "kind": "fund", "why": "the relayer's key has no SOL to pay the fees with", "retry": True, "transient": True}, r


def test_a_transaction_reported_lost_that_landed_is_seen_on_the_chain(env):
    c, net = env
    org, repo, n, wallet = user(), user(), issue(), Keypair().pubkey()
    r = relay.submit(Flaky(net).lose(4), c.payer, faucet_jwt(c, n, org, repo), TERMS, JWKS, now=c.now())      # the last transaction: Step, FaucetOpen, FundBalance, Close
    assert r["ok"] and r["job"] == str(pay.job_pda(repo, n, pay.faucet_balance_pda(org))) and len(r["sigs"]) == 4 and "already" not in r, r      # the lost transaction is named too
    payee = user()
    r = relay.submit(Flaky(net).lose(4), c.payer, pay_jwt(c, repo, n, payee, wallet), None, JWKS, now=c.now())
    assert r["ok"] and r["paid"][0]["to"] == str(wallet) and c.balance(pay.ata(wallet, pay.faucet_mint())) == 4_875_000, r
    r = relay.submit(Flaky(net).lose(4), c.payer, bind_jwt(c, payee, wallet), None, JWKS, now=c.now())
    assert r["ok"] and r["wallet"] == str(wallet) and pay.read_bind(c.data(pay.bind_pda(payee))).wallet == wallet, r


def test_a_verification_that_was_cut_short_is_carried_on_from_where_it_stands(env):
    c, net = env
    me = c.payer.pubkey()

    def cut_after(rounds: int, jwt: str) -> None:
        """What a run that died after `rounds` rounds of the verification left on chain."""
        t = relay._open(jwt, me, JWKS)
        for txs in relay._verification(me, t, None, [])[0][:rounds]:
            relay._round(net, c.payer, txs)
    for rounds, left, stage in ((1, [1, 1], 0), (2, [1], 1)):
        org, repo, n = user(), user(), issue()
        jwt = faucet_jwt(c, n, org, repo)
        cut_after(rounds, jwt)
        assert oidc.read_token(c.data(oidc.token_pda(me, oidc.token_id(jwt)))).stage == stage
        net.spent()
        assert go(env, jwt, TERMS)["ok"]
        assert net.shape == left, (rounds, net.shape)            # only what was missing was sent
    # half of the head only: the missing chunk is written, the one that is there is not sent again
    org, repo, n = user(), user(), issue()
    jwt = faucet_jwt(c, n, org, repo)
    t = relay._open(jwt, me, JWKS)
    head = relay._verification(me, t, None, [])[0][0]
    assert len(head) == 2
    net.send(head[1], c.payer)
    net.spent()
    assert go(env, jwt, TERMS)["ok"] and net.shape == [1, 1, 1]
    # an account that holds something else under this token's id (it cannot, by its address; a bug could): started again
    org, repo, n = user(), user(), issue()
    jwt = faucet_jwt(c, n, org, repo)
    cut_after(2, jwt)
    acct = oidc.token_pda(me, oidc.token_id(jwt))
    a = c.svm.get_account(acct)
    from solders.account import Account
    c.svm.set_account(acct, Account(lamports=a.lamports, data=bytes(a.data[:-1]) + b"!", owner=a.owner, executable=False))
    net.spent()
    assert go(env, jwt, TERMS)["ok"] and net.shape == [1, 2, 1, 1]      # Close, then the whole verification


def test_the_last_step_goes_alone_when_it_takes_more_compute_than_was_measured(env, monkeypatch):
    c, net = env
    org, repo, n = user(), user(), issue()
    jwt = faucet_jwt(c, n, org, repo)

    class Short(Flaky):
        def send(self, ixs, payer, signers=None):
            if len(ixs) > 2 and any(bytes(ix.data)[:1] == b"\x01" and ix.program_id == oidc.OIDC_ID for ix in ixs):
                raise chain.RpcError("Transaction simulation failed: Error processing Instruction 1: Program failed to complete",
                                     {"logs": ["Program FkwZ... failed: exceeded CUs meter at BPF instruction"]})
            return super().send(ixs, payer, signers)
    net.spent()
    r = relay.submit(Short(net), c.payer, jwt, TERMS, JWKS, now=c.now())
    # five transactions for the usual four: the Step alone, then the faucet, the funding and the Close together
    assert r["ok"] and net.txs == 5 and c.data(oidc.token_pda(c.payer.pubkey(), oidc.token_id(jwt))) is None, (r, net.shape)
    # and when the measure says beforehand that the Step leaves no room, nothing is tried: the same shape at once
    monkeypatch.setitem(relay._LAST_STEP, 2048, (1_300_000, 0))
    c.warp(60)
    net.spent()
    assert go(env, faucet_jwt(c, issue(), org, repo), TERMS)["ok"] and net.shape == [2, 1, 1, 1]


# -- what needs no token ----------------------------------------------------------------------------------------------------
def test_an_unproven_bounty_goes_back_and_a_held_one_follows_its_payees_wallet(env):
    c, net = env
    cranker = c.fund()                                           # anyone
    relay.refund_due(net, cranker, c.now())                      # what earlier tests left past its deadline on this chain
    n, from_balance = funded(env, amount=7 * USDC, work=600)
    sponsor, stok = c.wallet(c.usdc, 50 * USDC)
    assert c.send([pay.fund_wallet_ix(sponsor.pubkey(), stok, c.usdc, REPO, n, 11 * USDC, WF_REPO, WF_SHA, TERMS, work_s=600)], sponsor), c.err
    n2, proven = funded(env, amount=9 * USDC, work=600)
    payee = user()
    assert go(env, pay_jwt(c, REPO, n2, payee))["paid"][0]["held_until"]
    baltok0 = c.balance(pay.baltok_pda(c.bal))
    assert relay.refund_due(net, cranker, c.now()) == [] and relay.settle_held(net, cranker) == []
    c.warp(601)
    assert len(relay.refund_due(net, cranker, c.now())) == 2    # the two open jobs; the held one waits for its payee, not for the deadline
    assert c.balance(pay.baltok_pda(c.bal)) == baltok0 + 7 * USDC and c.balance(stok) == 50 * USDC
    assert [j.state for _a, j in relay.jobs_for(net, REPO, n)] == [] and pay.read_job(c.data(proven)).state == "held"
    # the payee binds a wallet (their claim, carried by someone who sent only the Bind): the crank pays the held job
    wallet = Keypair().pubkey()
    tok = c.verify(bind_jwt(c, payee, wallet), oidc.GITHUB, c.github)
    assert c.send([pay.bind_ix(c.payer.pubkey(), tok, c.key, payee)]), c.err
    assert len(relay.settle_held(net, cranker)) == 1 and c.balance(pay.ata(wallet, c.usdc)) == 8_775_000
    assert relay.settle_held(net, cranker) == [] and relay.refund_due(net, cranker, c.now()) == []


def test_a_held_bounty_goes_back_after_180_days():
    """On a chain of its own: nothing here needs a token after the clock has moved half a year."""
    c = Chain()
    net = Net(c)
    env = (c, net)
    assert c.send([pay.init_faucet_ix(c.payer.pubkey())]), c.err
    org, repo, n, payee = user(), user(), issue(), user()
    assert go(env, faucet_jwt(c, n, org, repo), TERMS)["ok"]
    assert go(env, pay_jwt(c, repo, n, payee))["paid"][0]["held_until"] == c.now() + pay.HOLD
    c.warp(pay.HOLD)
    assert relay.refund_due(net, c.payer, c.now()) == []
    c.warp(1)
    assert relay.settle_held(net, c.payer) == []       # too late to pay, even had the payee bound a wallet
    assert len(relay.refund_due(net, c.payer, c.now())) == 1
    assert c.balance(pay.baltok_pda(pay.faucet_balance_pda(org))) == 5 * USDC and relay.held_for(net, payee) == []


# -- key tokens, on both deployments ----------------------------------------------------------------------------------------
def _jwk(kid: str, n: int) -> dict:
    return {"kty": "RSA", "alg": "RS256", "e": "AQAB", "kid": kid, "n": b64(oidc.modulus_bytes(n))}


def attestation(c: Chain, issuer: int, n: int, **over) -> str:
    """What GitHub signs for a scheduled run of the pinned rotate workflow that found this key in the issuer's key set."""
    c.warp(1)
    return token(c, oidc.rotate_audience(issuer, n), **{"file": "rotate.yml", "wf_repo": "drexthealpha/knos-oidc-rotate", "wf_sha": TEST_ROTATE_SHA,
                                                        "event_name": "schedule", **over})


def test_a_key_token_is_carried_to_both_deployments():
    """On a chain of its own, with the first deployment's programs beside the second's. The rotate workflow's token
    names a key GitHub has added: the first deployment takes it as it always did; the second registers it (it then
    waits a day and the guardian), and on later days refreshes it."""
    c = Chain()
    for program, build in ((oidc1.OIDC_ID, "knos_oidc_test.so"), (pay1.PAY_ID, "knos_pay_test.so")):
        c.svm.add_program_from_file(program, str(FIX / build))
    net = Net(c)
    assert c.send([pay.init_faucet_ix(c.payer.pubkey())]), c.err
    assert ROTATE_REF.startswith(relay.ROTATE_REF)
    new = modulus(_STRANGER)
    jwks = {oidc.GITHUB: {"keys": [_jwk("k", modulus(signing_key())), _jwk("new", new)]}}
    submit = lambda jwt, keys=jwks: relay.submit(net, c.payer, jwt, None, keys, now=c.now())  # noqa: E731
    key2, key1 = oidc.key_pda(oidc.GITHUB, new), oidc1.key_pda(oidc.GITHUB, new)
    att = attestation(c, oidc.GITHUB, new)
    r = submit(att)
    assert r == {"ok": True, "kind": "key", "sigs": r["sigs"], "key": str(key2), "added": True, "refreshed": False,
                 "first": {"ok": True, "kind": "key", "sigs": r["first"]["sigs"], "key": str(key1), "added": True}}, r
    assert r["sigs"][:len(r["first"]["sigs"])] == r["first"]["sigs"] and len(r["sigs"]) > len(r["first"]["sigs"])
    k = oidc.read_key(c.data(key2))
    assert (k.state, k.genesis, k.approved, k.revoked, k.active_at, k.expires_at) == (1, False, False, False, c.now() + oidc.KEY_DELAY, c.now() + oidc.KEY_DELAY + oidc.KEY_TTL)
    assert c.data(key1)[0] == 1                                  # ready on the first deployment
    # a token that key signed costs nothing yet: the key waits for its day and for the guardian
    early = sign_jwt(_STRANGER, github_claims(aud=pay.fund_audience(1, 5 * USDC, 0, TH, pay.faucet_balance_pda(OWNER)), iat=c.now(), exp=c.now() + 300, actor_id=MAINT,
                                              event_name="issue_comment", job_workflow_ref=f"{WF_REPO}/.github/workflows/fund.yml@refs/tags/v0.3.12"),
                     header={"alg": "RS256", "kid": "new"})
    n0 = net.txs
    r = relay.submit(net, c.payer, early, TERMS, jwks, now=c.now())
    assert not r["ok"] and "this signing key is new and the guardian has not approved it yet. Its 24-hour wait ends" in r["why"] and net.txs == n0, r
    for program in (oidc.OIDC_ID, oidc1.OIDC_ID):                # and both token accounts were closed behind it
        assert net.program_accounts(program, None, {50: bytes(c.payer.pubkey())}) == []
    # the same token again, and the next day's: both deployments have the key, and a day gained is not worth a transaction
    n0 = net.txs
    for jwt in (att, attestation(c, oidc.GITHUB, new)):
        r = submit(jwt)
        assert r == {"ok": True, "kind": "key", "sigs": [], "key": str(key2), "added": False, "refreshed": False,
                     "first": {"ok": True, "kind": "key", "sigs": [], "key": str(key1), "added": False}} and net.txs == n0, r
    # two days on, the rotate workflow names the key again: its life on the second deployment starts over from now
    c.warp(2 * 86_400)
    r = submit(attestation(c, oidc.GITHUB, new))
    assert (r["ok"], r["added"], r["refreshed"], r["first"]["added"]) == (True, False, True, False) and oidc.read_key(c.data(key2)).expires_at == c.now() + oidc.KEY_TTL, r
    assert net.txs - n0 == 4
    # the same workflow called from someone else's repository: the first deployment takes a key from it, the second does not
    third = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    more = {oidc.GITHUB: {"keys": [*jwks[oidc.GITHUB]["keys"], _jwk("third", modulus(third))]}}
    r = submit(attestation(c, oidc.GITHUB, modulus(third), repository_id=111), more)
    assert r == {"ok": True, "kind": "key", "sigs": r["first"]["sigs"], "key": str(oidc1.key_pda(oidc.GITHUB, modulus(third))), "added": False, "refreshed": False,
                 "why": r["why"], "first": r["first"]} and r["first"]["added"] and "in the attester's own repository" in r["why"], r
    assert c.data(oidc.key_pda(oidc.GITHUB, modulus(third))) is None and c.data(oidc1.key_pda(oidc.GITHUB, modulus(third))) is not None
    # a key that is in nobody's key set: neither deployment takes it, and the answer is the plain refusal
    assert submit(attestation(c, oidc.GITHUB, modulus(third))) == {"ok": False, "kind": "key", "why": "the issuer's key set has no key with that hash"}
    # a key of GitLab's, 4096 bits: registered from the attestation, its parameters in a transaction of their own
    gitlab = json.loads((FIX / "gitlab_jwks_2026-10-02.json").read_text())
    big = next(n for _kid, n in oidc.jwks_keys(gitlab) if n.bit_length() == 4096)
    r = submit(attestation(c, oidc.GITLAB, big), {**jwks, oidc.GITLAB: gitlab})
    k = oidc.read_key(c.data(oidc.key_pda(oidc.GITLAB, big)))
    assert r["ok"] and r["added"] and not r["refreshed"] and (k.state, k.issuer, k.bits, k.approved) == (1, oidc.GITLAB, 4096, False), r
    # once the guardian has revoked a key, no attestation brings it back
    assert c.revoke(oidc.GITHUB, new), c.err
    c.warp(2 * 86_400)
    r = submit(attestation(c, oidc.GITHUB, new))
    assert r["ok"] and not r["added"] and not r["refreshed"] and r["sigs"] == [] and r["why"] == "the guardian revoked this key; it cannot be used again", r
    # every key of the second verifier, GitHub's first, each issuer's oldest first
    listed = relay.keys(net)
    assert [(k.issuer, k.bits, k.genesis, k.revoked) for _a, k, _n in listed] == [(0, 2048, True, False), (0, 2048, False, True), (1, 4096, False, False)]
    assert [n for _a, _k, n in listed] == [modulus(signing_key()), new, big]


def test_what_a_key_needs_and_no_token_gives_anyone_can_send(env):
    c, net = env
    seed4096 = modulus(signing_key(4096))                        # the test build takes this one with no attestation, as GitLab's
    assert c.send([oidc.register_key_ix(c.payer.pubkey(), oidc.GITLAB, seed4096)]), c.err
    assert oidc.read_key(c.data(oidc.key_pda(oidc.GITLAB, seed4096))).state == 0     # registered, its parameters never sent
    anyone = c.fund()
    assert len(relay.register_missing(net, anyone, JWKS)) == 1 and oidc.read_key(c.data(oidc.key_pda(oidc.GITLAB, seed4096))).state == 1
    assert relay.register_missing(net, anyone, JWKS) == []
    # GitHub's own four keys, when the chain has not seen one yet (here: none of them): registered, ready and usable at once
    real = json.loads((FIX / "github_jwks_2026-10-02.json").read_text())
    real_chain = Chain2("knos_oidc_v2_real.so")                 # the build that is deployed: it trusts GitHub's keys, not the tests'
    real_net = Net(real_chain)
    assert relay.keys(real_net) == []
    assert len(relay.register_missing(real_net, real_chain.payer, {oidc.GITHUB: real})) == 4
    got = relay.keys(real_net)
    assert len(got) == 4 and all(k.genesis and oidc.key_usable(k, real_chain.now()) == (True, "") for _a, k, _n in got)
    assert {oidc.key_hash(n).hex() for _a, _k, n in got} == relay.GENESIS
    assert relay.register_missing(real_net, real_chain.payer, {oidc.GITHUB: real}) == []


# -- any other audience: verified, and nothing more ---------------------------------------------------------------------------
def test_any_other_audience_is_only_verified_and_its_account_is_swept_when_its_hour_is_over(env):
    """Another team's program can require a fact GitHub signed without running a relay: it reads the account."""
    c, net = env
    me = c.fund()                                                # a relayer whose accounts nobody else's test touches
    jwt = token(c, "sts.amazonaws.com", sub="repo:octo/widgets:ref:refs/heads/main")
    account = oidc.token_pda(me.pubkey(), oidc.token_id(jwt))
    net.spent()
    r = relay.verify_only(net, me, jwt, JWKS, now=c.now())
    assert r == {"ok": True, "kind": "verify", "account": str(account), "payer": str(me.pubkey()), "exp": c.now() + 300, "sigs": r["sigs"]}, r
    assert (net.txs, net.waits, net.shape) == (3, 3, [1, 1, 1]) and len(jwt) <= 1756      # a token this short: one Write, then Write and Step, then the last Step
    tok = oidc.read_token(c.data(account))
    assert tok.verified and tok.issuer == oidc.GITHUB and tok.payer == me.pubkey() and tok.claims()["aud"] == "sts.amazonaws.com"
    assert c.svm.get_account(account).owner == oidc.OIDC_ID      # what a consumer checks before it reads the claims
    again = relay.verify_only(net, me, jwt, JWKS, now=c.now())
    assert again == {**r, "sigs": [], "already": True} and net.txs == 3
    # a Knos audience is verified the same way when that is all that is asked; `submit` is what acts on one
    assert relay.verify_only(net, me, bind_jwt(c, user(), Keypair().pubkey()), JWKS, now=c.now())["ok"]
    # a GitLab token under a 4096-bit key: six steps, the token's tail riding with the first
    gl = signing_key(4096)
    if c.data(oidc.key_pda(oidc.GITLAB, modulus(gl))) is None:
        assert c.register(oidc.GITLAB, modulus(gl)), c.err
    from _settle import gitlab_claims
    glab = sign_jwt(gl, gitlab_claims(aud="https://vault.example.com", iat=c.now(), exp=c.now() + 300, jti="gl-verify"))
    net.spent()
    r = relay.verify_only(net, me, glab, ALL_JWKS, now=c.now())
    assert r["ok"] and oidc.read_token(c.data(Pubkey.from_string(r["account"]))).claims()["aud"] == "https://vault.example.com", r
    assert net.shape == [2, 1, 1, 1, 1, 1, 1], net.shape         # the head's Writes side by side, then one wait for each of the six Steps
    # nothing is spent on a token no published key signed, on one whose key the chain would refuse, or on an expired one
    n0, sol = net.txs, lamports(c, me)
    forged = sign_jwt(_STRANGER, github_claims(aud="sts.amazonaws.com", iat=c.now(), exp=c.now() + 300))
    assert relay.verify_only(net, me, forged, JWKS, now=c.now()) == {"ok": False, "kind": "verify", "why": "the signature is not the issuer's"}
    assert relay.verify_only(net, me, token(c, "x", exp=c.now() - 3601, iat=c.now() - 3901), JWKS, now=c.now())["why"] == "token expired"
    assert relay.verify_only(net, me, "garbage", JWKS, now=c.now())["why"] == "this is not a token GitHub Actions or GitLab CI issued"
    assert (net.txs, lamports(c, me)) == (n0, sol)
    # a verification that was cut short leaves an account too
    cut = token(c, "sts.amazonaws.com", jti="cut")
    for txs in relay._verification(me.pubkey(), relay._open(cut, me.pubkey(), JWKS), None, [])[0][:1]:
        relay._round(net, me, txs)
    mine = lambda: net.program_accounts(oidc.OIDC_ID, None, {50: bytes(me.pubkey())})  # noqa: E731
    assert len(mine()) == 4
    # the one that never got its whole token says nothing of its expiry: it goes an hour after its last transaction
    assert relay.sweep(net, me, c.now()) == []
    c.warp(oidc.LATE)
    before = lamports(c, me)
    assert len(relay.sweep(net, me, c.now())) == 1 and len(mine()) == 3 and lamports(c, me) > before
    # a verified account stays for its consumer while its token can be used: until an hour past its expiry. Then the rent comes back
    c.warp(again["exp"] + oidc.LATE - 1 - c.now())
    assert relay.sweep(net, me, c.now()) == [] and len(mine()) == 3
    c.warp(1)
    assert len(relay.sweep(net, me, c.now())) == 1 and len(mine()) == 2          # the first token's hour is over
    c.warp(10)
    assert len(relay.sweep(net, me, c.now())) == 2 and mine() == [] and relay.sweep(net, me, c.now()) == []


# -- what a token costs: transactions, waits for confirmation, compute units -------------------------------------------------------
def cost(env, send) -> tuple[int, int, list[int], int, int]:
    """(transactions, waits, transactions per wait, most compute units in one transaction, compute units in all) of
    what `send` relays."""
    _c, net = env
    net.spent()
    net.units.clear()
    send()
    return net.txs, net.waits, list(net.shape), max(net.units), sum(net.units)


def test_transactions_waits_and_compute_units_of_every_path(env):
    """A relay's time goes into waiting for confirmations, each after the one before. This relay sends a token's
    chunks side by side and puts the last verification step, the escrow's instruction and the Close into one
    transaction. Every path is counted here, so a change that costs a transaction or a wait fails; the compute units
    are LiteSVM's (they move by a few thousand with the addresses involved). Run with -s to see the table."""
    c, net = env
    relay.refund_due(net, c.payer, c.now())                     # what earlier tests left past its deadline
    ok = lambda r: r["ok"] or pytest.fail(str(r))  # noqa: E731
    rows: dict[str, tuple] = {}
    org, repo, n_real, n_faucet, n_held = user(), user(), issue(), issue(), issue()
    payee, waiting, wallet = user(), user(), Keypair().pubkey()
    rows["fund from a Balance"] = cost(env, lambda: ok(go(env, fund_jwt(c, n_real), TERMS)))
    rows["fund, the faucet opened on the way"] = cost(env, lambda: ok(go(env, faucet_jwt(c, n_faucet, org, repo), TERMS)))
    rows["pay to the address in the proof"] = cost(env, lambda: ok(go(env, pay_jwt(c, repo, n_faucet, payee, wallet))))
    rows["pay held (no wallet known)"] = cost(env, lambda: ok(go(env, pay_jwt(c, REPO, n_real, waiting))))
    rows["bind (nothing held)"] = cost(env, lambda: ok(go(env, bind_jwt(c, payee, wallet))))
    rows["bind, then settle the held job"] = cost(env, lambda: ok(go(env, bind_jwt(c, waiting, Keypair().pubkey()))))
    funded(env, n_held)
    rows["pay to a bound wallet"] = cost(env, lambda: ok(go(env, pay_jwt(c, REPO, n_held, payee))))
    # a key GitHub has added, named by the rotate workflow: registered, and two days later refreshed
    new = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = {oidc.GITHUB: {"keys": [*JWKS[oidc.GITHUB]["keys"], _jwk("added", modulus(new))]}}
    key = lambda: relay.submit(net, c.payer, attestation(c, oidc.GITHUB, modulus(new)), None, jwks, now=c.now())  # noqa: E731
    rows["key registered"] = cost(env, lambda: ok(key()))
    c.warp(2 * 86_400)
    rows["key refreshed"] = cost(env, lambda: ok(key()))
    # what needs no token
    n_late, _job = funded(env, work=60)
    funded(env, n_held)
    assert go(env, pay_jwt(c, REPO, n_held, user()))["paid"][0]["to"] is None
    late = user()
    assert go(env, pay_jwt(c, REPO, funded(env)[0], late))["paid"][0]["to"] is None
    tok = c.verify(bind_jwt(c, late, Keypair().pubkey()), oidc.GITHUB, c.github)
    assert c.send([pay.bind_ix(c.payer.pubkey(), tok, c.key, late)]), c.err
    c.warp(61)
    rows["refund of an unproven bounty"] = cost(env, lambda: relay.refund_due(net, c.payer, c.now()))
    rows["settle of a held bounty"] = cost(env, lambda: relay.settle_held(net, c.payer))
    rows["verify only (a 1,7xx-byte token)"] = cost(env, lambda: ok(relay.verify_only(net, c.fund(), token(c, "sts.amazonaws.com"), JWKS, now=c.now())))
    print()
    for path, (txs, waits, shape, most, total) in rows.items():
        print(f"{path:38} {txs} transactions  {waits} waits  per wait {shape!s:15}  compute units: {total:9,} in all, {most:9,} in the largest")
    assert {path: row[:3] for path, row in rows.items()} == {
        "fund from a Balance": (4, 3, [2, 1, 1]), "fund, the faucet opened on the way": (4, 3, [2, 1, 1]),
        "pay to the address in the proof": (4, 3, [2, 1, 1]), "pay held (no wallet known)": (4, 3, [2, 1, 1]),
        "bind (nothing held)": (4, 3, [2, 1, 1]), "bind, then settle the held job": (4, 3, [2, 1, 1]),
        "pay to a bound wallet": (4, 3, [2, 1, 1]), "key registered": (4, 3, [2, 1, 1]), "key refreshed": (4, 3, [2, 1, 1]),
        "refund of an unproven bounty": (1, 1, [1]), "settle of a held bounty": (1, 1, [1]), "verify only (a 1,7xx-byte token)": (3, 3, [1, 1, 1])}, rows
    # the last Step and the escrow together, with room to spare under the 1,400,000 a transaction may use
    assert all(row[3] < chain.MAX_COMPUTE_UNITS - 300_000 for row in rows.values()), rows


def test_the_first_deployments_relay_takes_a_transaction_and_a_wait_for_every_step():
    """The same three tokens through knos.settle.relay on its own programs: every chunk, step, instruction and close
    in a transaction of its own, each waited for. What the second deployment's relay is measured against."""
    from _settle import Chain as Chain1
    from _settle import ChainLedger as Ledger1
    from _settle import github_claims as claims1
    c1 = Chain1()
    ledger1 = Ledger1(c1)
    assert c1.send([pay1.init_faucet_ix(c1.payer.pubkey())]), c1.err
    jwks1 = {oidc1.GITHUB: {"keys": [_jwk("k0", modulus(signing_key()))]}}
    payee, wallet = user(), Keypair().pubkey()

    def token1(aud: str, file: str, **over) -> str:
        claims = claims1(aud=aud, iat=c1.now(), nbf=c1.now() - 600, exp=c1.now() + 300, jti=f"f{ledger1.n}", repository_id=str(REPO),
                         repository_owner_id=str(OWNER), job_workflow_ref=f"{WF_REPO}/.github/workflows/{file}@refs/tags/v0.3.10", job_workflow_sha=WF_SHA)
        claims.update(over)
        return sign_jwt(signing_key(), claims, header={"typ": "JWT", "alg": "RS256", "kid": "k0"})
    assert relay1.submit(ledger1, c1.payer, token1(pay1.fund_audience(1, 5 * USDC), "fund.yml"), jwks1, now=c1.now())["ok"]    # registers the key on the way
    c1.warp(61)
    first = {}
    for kind, jwt in (("fund", token1(pay1.fund_audience(2, 5 * USDC), "fund.yml")), ("pay", token1(pay1.pay_audience(REPO, 2, payee, "a" * 40), "prove.yml")),
                      ("claim", token1(pay1.claim_audience(wallet), "claim.yml", event_name="workflow_dispatch", actor_id=str(payee), repository_owner_id=str(payee)))):
        n0 = ledger1.n
        assert relay1.submit(ledger1, c1.payer, jwt, jwks1, now=c1.now())["ok"]
        first[kind] = (ledger1.n - n0, len(jwt))
    print()
    for kind, (txs, size) in first.items():
        print(f"first deployment   {kind:5}  token {size} bytes  {txs} transactions  {txs} waits")
    assert {k: v[0] for k, v in first.items()} == {"fund": 7, "pay": 7, "claim": 6}


def test_how_long_a_token_each_write_carries(env):
    """The head of a token goes in Writes side by side, its tail beside the first Step: so a token up to 1,756 bytes
    takes one Write before that Step, and the longest the verifier takes (8,192 bytes) nine."""
    c, net = env
    me, tid = c.payer.pubkey(), bytes(32)
    alone = 200 + relay.ROOM - chain.tx_size([relay._write_ix(me, tid, 2000, 0, bytes(200))], me)
    beside = 200 + relay.ROOM - chain.tx_size([relay._write_ix(me, tid, 2000, 0, bytes(200)), oidc.step_ix(me, tid, c.key, 8)], me)
    assert (alone, beside) == (914, 842)
    from _settle import sized_jwt
    for size, shape in ((alone + beside, [1, 1, 1]), (alone + beside + 2, [2, 1, 1]), (oidc.MAX_JWT, [9, 1, 1])):
        jwt = sized_jwt(signing_key(), github_claims(aud="x", iat=c.now(), exp=c.now() + 300, jti=f"size{size}", groups_direct=["g"]), size)
        net.spent()
        net.sizes.clear()
        assert relay.verify_only(net, c.fund(), jwt, JWKS, now=c.now())["ok"] and net.shape == shape, (len(jwt), net.shape)
        assert max(net.sizes) <= relay.ROOM


def test_the_harness_ledger_has_what_a_relay_and_a_settlement_use():
    """tests/_pay2.ChainLedger, the plain LiteSVM ledger other tests take: the relay runs on it, and a job's terms are
    read back from it as from a cluster."""
    from _pay2 import ChainLedger
    c = Chain()
    ledger = ChainLedger(c)
    assert c.send([pay.init_faucet_ix(c.payer.pubkey())]), c.err
    org, repo, payee, wallet = user(), user(), user(), Keypair().pubkey()
    r = relay.submit(ledger, c.payer, faucet_jwt(c, 7, org, repo), TERMS, JWKS, now=c.now())
    job = pay.job_pda(repo, 7, pay.faucet_balance_pda(org))
    assert r["ok"] and r["job"] == str(job) and ledger.n == 4, r
    assert ledger.log_of(job, "knos2:terms ") == "knos2:terms " + TERMS.decode() and ledger.log_of(job, "knos2:paid") is None
    assert ledger.log_of(job, "knos2:terms ", lambda line: False) is None and ledger.log_of(Keypair().pubkey(), "knos2:terms ") is None
    assert ledger.log_of(job, "knos2:terms ", by=pay.PAY_ID) == "knos2:terms " + TERMS.decode() and ledger.log_of(job, "knos2:terms ", by=pay.TOKEN) is None
    assert relay.submit(ledger, c.payer, pay_jwt(c, repo, 7, payee, wallet), None, JWKS)["ok"] and c.balance(pay.ata(wallet, pay.faucet_mint())) == 4_875_000
    assert ledger.log_of(pay.rep_pda(payee), "knos2:paid ").startswith(f"knos2:paid repo={repo} issue=7 payee={payee} amount=4875000 fee=125000 to={wallet}")
    assert ledger.infos([pay.faucet_mint(), Keypair().pubkey()]) == [(pay.TOKEN, c.data(pay.faucet_mint())), None]
    assert ledger.program_accounts(oidc.OIDC_ID, None, {50: bytes(c.payer.pubkey())}) == []      # the token accounts were closed behind the tokens


# -- knos.chain.Ledger, with a cluster's JSON-RPC faked where it is called ---------------------------------------------------
class Rpc:
    """What a cluster would answer knos.chain.call: transactions are kept, confirmed on the second time of asking,
    and refused (at preflight) or failed (on chain) when the test says so."""

    def __init__(self):
        self.calls: list[tuple[str, list]] = []
        self.sent: dict[str, object] = {}               # signature -> the transaction
        self.refuse: set[int] = set()                   # lamports of the transfers preflight refuses
        self.fail: set[int] = set()                     # and of those that fail on chain
        self.asked: list[list[str]] = []                # each getSignatureStatuses request
        self.rows: dict[str | None, list[dict]] = {}    # getSignaturesForAddress pages, by their `before`
        self.logs: dict[str, list[str]] = {}
        self.accounts: dict[str, tuple[str, bytes]] = {}

    def __call__(self, url, method, params, timeout=10.0):
        self.calls.append((method, params))
        return getattr(self, method)(*params)

    def count(self, method: str) -> int:
        return sum(1 for m, _p in self.calls if m == method)

    def getLatestBlockhash(self, opts):
        return {"value": {"blockhash": "4vJ9JU1bJJE96FWSJKvHsmmFADCg4gpZQff4P3bkLKi"}}

    def sendTransaction(self, raw, opts):
        from solders.transaction import Transaction
        tx = Transaction.from_bytes(base64.b64decode(raw))
        lamports = int.from_bytes(bytes(tx.message.instructions[-1].data)[4:12], "little")
        if lamports in self.refuse:
            raise chain.RpcError("Transaction simulation failed: custom program error: 0x5b", {"logs": ["Program log: no"]})
        self.sent[str(tx.signatures[0])] = tx
        return str(tx.signatures[0])

    def getSignatureStatuses(self, sigs):
        self.asked.append(list(sigs))
        if len(self.asked) % 2:                         # not there yet, the first time
            return {"value": [None] * len(sigs)}
        lamports = lambda s: int.from_bytes(bytes(self.sent[s].message.instructions[-1].data)[4:12], "little")  # noqa: E731
        return {"value": [{"confirmationStatus": "confirmed", "err": {"InstructionError": [1, {"Custom": 91}]} if lamports(s) in self.fail else None}
                          for s in sigs]}

    def getSignaturesForAddress(self, address, opts):
        return self.rows.get(opts.get("before"), [])[:opts["limit"]]

    def getTransaction(self, sig, opts):
        return {"meta": {"logMessages": self.logs[sig]}} if sig in self.logs else None

    def getAccountInfo(self, address, opts):
        got = self.accounts.get(address)
        return {"value": {"owner": got[0], "data": [base64.b64encode(got[1]).decode(), "base64"]} if got else None}

    def getMultipleAccounts(self, addresses, opts):
        return {"value": [self.getAccountInfo(a, opts)["value"] for a in addresses]}

    def getProgramAccounts(self, program, opts):
        return [{"pubkey": str(pay.auth_pda()), "account": {"data": [base64.b64encode(b"job").decode(), "base64"]}}]


@pytest.fixture
def rpc(monkeypatch):
    fake = Rpc()
    monkeypatch.setattr(chain, "call", fake)
    monkeypatch.setattr(chain.time, "sleep", lambda s: None)
    return fake, chain.Ledger("https://rpc.example")


def _transfers(payer: Keypair, *lamports: int) -> list[list[Instruction]]:
    from solders.system_program import TransferParams, transfer
    return [[transfer(TransferParams(from_pubkey=payer.pubkey(), to_pubkey=pay.FEE_OWNER, lamports=n))] for n in lamports]


def test_send_all_signs_over_one_blockhash_sends_without_waiting_and_waits_for_all_at_once(rpc):
    fake, ledger = rpc
    payer = Keypair()
    sigs = ledger.send_all(_transfers(payer, 1, 2, 3), payer)
    assert [fake.count(m) for m in ("getLatestBlockhash", "sendTransaction")] == [1, 3]
    assert [int.from_bytes(bytes(fake.sent[s].message.instructions[-1].data)[4:12], "little") for s in sigs] == [1, 2, 3]       # in the order given
    assert fake.asked == [sigs, sigs]                           # every signature in one request, until all are confirmed
    for tx in fake.sent.values():                               # each is what `send` would have signed: a compute-unit limit first
        assert tx.message.instructions[0].program_id_index == tx.message.account_keys.index(chain.COMPUTE_BUDGET) and len(bytes(tx)) <= chain.MAX_TX_BYTES
        assert len(bytes(tx)) == chain.tx_size(_transfers(payer, 1)[0], payer.pubkey())
    # one transaction: `send`, as ever
    fake.calls.clear()
    assert len(ledger.send_all(_transfers(payer, 4), payer)) == 1 and fake.count("sendTransaction") == 1
    assert ledger.send_all([], payer) == []


def test_send_all_waits_for_the_rest_before_it_raises_what_one_of_them_met(rpc):
    fake, ledger = rpc
    payer = Keypair()
    fake.refuse = {2}                                           # preflight refuses the second: it never reaches the chain
    with pytest.raises(chain.RpcError, match="0x5b.*Program log: no"):
        ledger.send_all(_transfers(payer, 1, 2, 3), payer)
    assert len(fake.sent) == 2 and sorted(fake.asked[-1]) == sorted(fake.sent)      # the other two were waited for all the same
    fake.refuse, fake.fail = set(), {5}                         # one fails on chain
    with pytest.raises(chain.RpcError, match="transaction failed: .*'Custom': 91"):
        ledger.send_all(_transfers(payer, 4, 5, 6), payer)
    assert len(fake.sent) == 5


def test_a_ledger_reads_the_clock_accounts_and_logs_in_few_requests(rpc):
    fake, ledger = rpc
    fake.accounts[str(chain.CLOCK)] = ("Sysvar1111111111111111111111111111111111111", bytes(32) + (1_790_000_123).to_bytes(8, "little"))
    assert ledger.now() == 1_790_000_123 and fake.count("getAccountInfo") == 1      # the clock the programs see, in one read
    keys = [Keypair().pubkey() for _ in range(150)]
    fake.accounts[str(keys[149])] = (str(pay.PAY_ID), b"x")
    got = ledger.infos(keys)
    assert got[:149] == [None] * 149 and got[149] == (pay.PAY_ID, b"x") and fake.count("getMultipleAccounts") == 2
    assert ledger.accounts(keys[148:]) == [None, b"x"] and ledger.owner(keys[149]) == pay.PAY_ID and ledger.owner(keys[0]) is None
    assert ledger.program_accounts(pay.PAY_ID, 320, {8: b"\x01\x02"}) == [(pay.auth_pda(), b"job")]
    assert fake.calls[-1][1][1]["filters"] == [{"dataSize": 320}, {"memcmp": {"offset": 8, "bytes": "5T"}}]
    assert ledger.program_accounts(pay.PAY_ID, None, {}) and fake.calls[-1][1][1]["filters"] == []
    # a line a program logged, found by an address: its transactions newest first, the failed ones passed over, a
    # hundred rows a request, and the next request from the last row whether it failed or not
    job = Keypair().pubkey()
    fake.rows[None] = [{"signature": f"new{i}", "err": {"x": 1} if i % 2 else None} for i in range(100)]
    fake.rows["new99"] = [{"signature": "paid", "err": None}, {"signature": "failed funding", "err": {"x": 1}}, {"signature": "funding", "err": None}]
    fake.logs = {"funding": ["Program 5y7i invoke [1]", "Program log: knos2:funded repo=1 issue=2", 'Program log: knos2:terms {"v":1}', "Program 5y7i success"],
                 "failed funding": ['Program log: knos2:terms {"v":0}'], "paid": ["Program log: knos2:paid repo=1 issue=2"]}
    assert list(ledger.history(job))[-2:] == ["paid", "funding"] and len(list(ledger.history(job))) == 52
    assert ledger.log_of(job, "knos2:terms ") == 'knos2:terms {"v":1}' and ledger.log_of(job, "knos2:paid") == "knos2:paid repo=1 issue=2"
    assert ledger.log_of(job, "knos2:held") is None and ledger.log_of(job, "knos2:terms ", lambda line: "v\":2" in line) is None
    assert ledger.signatures(job, 5) == ["new0", "new2", "new4"] and ledger.last_signature(job) == "new0" and ledger.recent(job, 2) == [("new0", None)]
    # who logged a line is read from the runtime's own lines, which no program can write
    logs = [f"Program {pay.PAY_ID} invoke [1]", "Program log: knos2:funded repo=1", f"Program {pay.TOKEN} invoke [2]", "Program log: Instruction: Transfer",
            f"Program {pay.TOKEN} consumed 4645 of 100 compute units", f"Program {pay.TOKEN} success", "Program log: knos2:terms {}", f"Program {pay.PAY_ID} success",
            f"Program {pay.TOKEN_2022} invoke [1]", f"Program log: knos2:paid repo=1\nProgram {pay.PAY_ID} invoke [1]", "Program log: knos2:paid repo=2",
            f"Program {pay.TOKEN_2022} failed: custom program error: 0x1", "Program log: nobody is running"]
    assert chain.said(logs, pay.PAY_ID) == ["knos2:funded repo=1", "knos2:terms {}"] and chain.said(logs, pay.TOKEN) == ["Instruction: Transfer"]
    assert len(chain.said(logs)) == 6 and chain.said(logs, pay.TOKEN_2022)[1] == "knos2:paid repo=2"
    # a history longer than five hundred rows is not read to its end
    fake.rows = {None: [{"signature": "same", "err": None}] * 100, "same": [{"signature": "same", "err": None}] * 100}
    assert len(list(ledger.history(job))) == 500


def test_a_rate_limited_request_is_waited_out_and_asked_again(monkeypatch):
    import io
    import urllib.error
    waits, answers = [], [urllib.error.HTTPError("u", 429, "Too Many Requests", {"Retry-After": "7"}, None),
                          urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None), io.BytesIO(b'{"result": 42}')]

    def urlopen(req, timeout=None):
        got = answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return got
    monkeypatch.setattr(chain.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(chain.time, "sleep", waits.append)
    assert chain.call("https://rpc.example", "getSlot", []) == 42 and waits == [7.0, 2]       # the endpoint's own figure, else a doubling wait
    answers[:] = [io.BytesIO(b'{"error": {"message": "boom", "data": {"logs": ["a", "b"]}}}')]
    with pytest.raises(chain.RpcError, match="boom \\| a \\| b"):
        chain.call("https://rpc.example", "sendTransaction", [])
