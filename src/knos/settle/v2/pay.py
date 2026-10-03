"""Client for knos-pay, second deployment (programs-v2/knos_pay): the escrow that pays the author of a pull request
on a GitHub-signed token verified on chain by knos-oidc. Pure instruction builders, audiences and account readers;
no network. The program is upgradeable only through a multisig with a public 48-hour delay, until an outside review;
then made immutable.

A job is one bounty on one issue of one repository, funded from a Balance (`balance_pda`: money a wallet set aside
for the repositories of one GitHub owner, spent by GitHub-signed comments) or straight from a wallet:
`job_pda(repository_id, issue, source)`. Its terms are fixed at funding (`terms_json`, `terms_hash`). A signed token pays the
payee's bound wallet (`bind_pda`), else the address the pay token carries, else the job is held for the payee.

Tokens are verified with the second deployment's verifier client, `knos.settle.v2.oidc` (write_ixs, step_ix). Every
instruction here that takes a token also takes `key`, the verifier's account of the key that verified it:
`oidc.key_pda(issuer, n)`, which is also `oidc.read_token(token account data).key`. A token is refused once that key
is revoked (error 78) or has expired (77), however fresh the token is.

What a relay does with a verified token (anyone may relay; the money goes where the token and the chain say):

    fund  knos2:fund:...   the audience names the Balance it spends (`named_balance`): the funder's workflow chose it.
                           fund_balance_ix on that Balance, with the mint read_balance gives. On devnet, when it names
                           faucet_balance_pda(owner id): faucet_open_ix first, in the same transaction, then
                           fund_balance_ix on it with faucet_mint(). A fund token works once, only from its run's
                           first attempt, and a Balance takes its tokens in the order GitHub issued them.
                           With terms of MAX_TERMS bytes and one compute budget instruction the two together are 1223
                           of a transaction's 1232 bytes: with a second one (a priority fee) send faucet_open_ix in a
                           transaction of its own first.
    pay   knos2:pay:...    for each open job on (repository id, issue) with the token's terms hash and mode:
                           pay_ix(..., destination(read_bind(...), audience)); create the fee account and the wallet's
                           token account with create_ata_ix first.
    bind  knos2:bind:...   bind_ix; then settle_ix for each held job whose payee_id is the token's actor.
    no token               refund_ix for every open job past its deadline and every held job past its hold.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import resources

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

IDS = json.loads(resources.files(__package__).joinpath("program_ids.json").read_text())
PAY_ID = Pubkey.from_string(IDS["knos_pay"])
OIDC_ID = Pubkey.from_string(IDS["knos_oidc"])          # the verifier whose token accounts this escrow accepts
FEE_OWNER = Pubkey.from_string(IDS["fee_owner"])        # Knos's Squads vault: receives fees, nothing else
GUARDIAN = Pubkey.from_string(IDS["guardian"])          # may pause new funding, nothing else
SYSTEM = Pubkey.from_string("11111111111111111111111111111111")
TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
USDC_DEVNET = Pubkey.from_string("4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")  # Circle's devnet USDC

MERGE, TESTS = 0, 1
FEE_BPS, FEE_MIN, MIN_AMOUNT, MAX_AMOUNT, FAUCET_CAP = 250, 50_000, 1_000_000, 500_000_000, 100_000_000
MIN_WORK, MAX_WORK, HOLD, PAUSE_MAX = 60, 90 * 86_400, 180 * 86_400, 7 * 86_400
FUND_PERIOD, CLOCK_SLACK, MAX_TERMS = 60, 30, 600
TOKEN_AHEAD, TOKEN_LIFE = 300, 3600     # a token's iat is at most this far ahead of the chain's clock; its exp at most this long after its iat
JOB_LEN, BALANCE_LEN, BIND_LEN, REP_LEN = 320, 160, 56, 64
STATES = {1: "open", 3: "held"}
ERRORS = {76: "the key that signed this token is not active on chain yet: it waits for its delay and the guardian's approval; try again after that",
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
          99: "a faucet balance holds test USDC and cannot be withdrawn"}


def fee_of(amount: int) -> int:
    return min(max(amount * FEE_BPS // 10_000, FEE_MIN), amount)


def terms_json(terms: dict) -> bytes:
    """The canonical JSON of a job's terms: keys sorted, no spaces, ASCII. This is what the funding instruction
    carries and logs, and what `terms_hash` hashes."""
    raw = json.dumps(terms, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    if len(raw) > MAX_TERMS:
        raise ValueError(f"the terms are {len(raw)} bytes; a job takes at most {MAX_TERMS}")
    return raw


def terms_hash(terms: bytes) -> bytes:
    """sha256 of the terms JSON bytes: what a job stores and what the fund and pay audiences carry."""
    return hashlib.sha256(terms).digest()


def wf_repo_hash(repository: str) -> bytes:
    """sha256 of "owner/name": the repository whose fund.yml and prove.yml a job pins."""
    return hashlib.sha256(repository.encode()).digest()


def funder_key(funder: Pubkey | int) -> bytes:
    """A funder as the record counts it: the funding wallet, or the GitHub owner id of a Balance."""
    return bytes(funder) if isinstance(funder, Pubkey) else hashlib.sha256(b"gh" + _u64(funder)).digest()


def _u64(v: int) -> bytes:
    return v.to_bytes(8, "little")


def _pda(seeds: list[bytes], program: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(seeds, program)[0]


# -- addresses --------------------------------------------------------------------------------------------------------
def auth_pda(program: Pubkey = PAY_ID) -> Pubkey:
    """The owner of every vault and of every Balance's token account. Only the program signs for it."""
    return _pda([b"auth"], program)


def vault_pda(mint: Pubkey, program: Pubkey = PAY_ID) -> Pubkey:
    return _pda([b"vault", bytes(mint)], program)


def balance_pda(owner_id: int, authority: Pubkey, mint: Pubkey, program: Pubkey = PAY_ID) -> Pubkey:
    """The Balance a wallet (`authority`) opened for the repositories of the GitHub owner `owner_id`, in one mint."""
    return _pda([b"bal", _u64(owner_id), bytes(authority), bytes(mint)], program)


def faucet_mint(program: Pubkey = PAY_ID) -> Pubkey:
    """The devnet test-USDC mint (SPL Token, 6 decimals); its mint authority is the program."""
    return _pda([b"mint"], program)


def faucet_balance_pda(owner_id: int, program: Pubkey = PAY_ID) -> Pubkey:
    """The devnet faucet's Balance of a GitHub owner: test USDC that fund tokens of that owner's repositories spend."""
    return balance_pda(owner_id, auth_pda(program), faucet_mint(program), program)


def baltok_pda(balance: Pubkey, program: Pubkey = PAY_ID) -> Pubkey:
    """A Balance's token account. Add money to a Balance with a plain token transfer to this address."""
    return _pda([b"baltok", bytes(balance)], program)


def job_pda(repo_id: int, issue: int, source: Pubkey, program: Pubkey = PAY_ID) -> Pubkey:
    """A job's address. `source` is the Balance it is funded from, or the funding wallet. Anyone can add a bounty of
    their own to an issue: one signed token pays every job on the issue that pins the same workflow and terms."""
    return _pda([b"job", _u64(repo_id), _u64(issue), bytes(source)], program)


def bind_pda(user_id: int, program: Pubkey = PAY_ID) -> Pubkey:
    return _pda([b"bind", _u64(user_id)], program)


def rep_pda(user_id: int, program: Pubkey = PAY_ID) -> Pubkey:
    return _pda([b"rep", _u64(user_id)], program)


def pair_pda(payee_id: int, funder: Pubkey | int, program: Pubkey = PAY_ID) -> Pubkey:
    """Exists once this funder (a wallet, or the GitHub owner id of a Balance) has paid this payee in real money."""
    return _pda([b"pair", _u64(payee_id), funder_key(funder)], program)


def rate_pda(repo_id: int, program: Pubkey = PAY_ID) -> Pubkey:
    return _pda([b"rate", _u64(repo_id)], program)


def pause_pda(program: Pubkey = PAY_ID) -> Pubkey:
    return _pda([b"pause"], program)


def ata(owner: Pubkey, mint: Pubkey, token_program: Pubkey = TOKEN) -> Pubkey:
    return _pda([bytes(owner), bytes(token_program), bytes(mint)], ATA_PROGRAM)


def create_ata_ix(payer: Pubkey, owner: Pubkey, mint: Pubkey, token_program: Pubkey = TOKEN) -> Instruction:
    """Creates the owner's associated token account if it does not exist (idempotent)."""
    return Instruction(ATA_PROGRAM, b"\x01", [AccountMeta(payer, True, True), AccountMeta(ata(owner, mint, token_program), False, True),
                                              AccountMeta(owner, False, False), AccountMeta(mint, False, False),
                                              AccountMeta(SYSTEM, False, False), AccountMeta(token_program, False, False)])


# -- audiences: what the workflows ask GitHub to sign ------------------------------------------------------------------
def fund_audience(issue: int, amount: int, mode: int, terms: bytes, balance: Pubkey, work_s: int = 14 * 86_400) -> str:
    """`terms` is terms_hash(...) (32 bytes). `balance` is the Balance this comment spends: the funder's workflow
    chooses it (a Balance of the repository's owner that lists the commenter; on devnet, with none,
    faucet_balance_pda(owner id)), and the program spends no other with this token."""
    return f"knos2:fund:{issue}:{amount}:{mode}:{terms.hex()}:{work_s}:{balance}"


def named_balance(audience: str) -> Pubkey:
    """The Balance a fund audience names: the one fund_balance_ix (and, when it is a faucet Balance, faucet_open_ix)
    must be given."""
    return Pubkey.from_string(audience.split(":")[-1])


def pay_audience(repo_id: int, issue: int, payee_id: int, head_sha: str, terms: bytes, mode: int, address: Pubkey | None = None) -> str:
    """`address`: where the payee asked to be paid in the pull request, or None (the job is then held for them unless
    they have bound a wallet)."""
    return f"knos2:pay:{repo_id}:{issue}:{payee_id}:{head_sha}:{terms.hex()}:{mode}:{address if address is not None else '-'}"


def bind_audience(address: Pubkey) -> str:
    return f"knos2:bind:{address}"


# -- accounts ---------------------------------------------------------------------------------------------------------
@dataclass
class Job:
    state: str              # "open", or "held" (the terms were met; waits for the payee to bind a wallet)
    mode: int
    from_balance: bool      # funded from a Balance by a comment (True) or by a wallet (False)
    token_program: Pubkey
    faucet: bool            # test money: the devnet faucet's mint
    repo_id: int
    issue: int
    amount: int
    deadline: int
    hold_until: int
    payee_id: int           # held: who the money waits for
    funder_id: int          # the GitHub id of whoever wrote the funding comment (0 for a wallet)
    not_before: int
    owner_id: int           # the Balance's GitHub owner (0 for a wallet)
    source: Pubkey          # the Balance, or the funding wallet
    refund_to: Pubkey       # the Balance's token account, or the funding wallet
    rent_to: Pubkey
    mint: Pubkey
    terms: bytes
    wf_repo_hash: bytes
    wf_sha: str

    @property
    def funder(self) -> Pubkey | int:
        """The funder the record counts: the Balance's GitHub owner id, or the funding wallet."""
        return self.owner_id if self.from_balance else self.source


def _i64(data: bytes, o: int) -> int:
    return int.from_bytes(data[o:o + 8], "little", signed=True)


def _n(data: bytes, o: int, size: int = 8) -> int:
    return int.from_bytes(data[o:o + size], "little")


def read_job(data: bytes | None) -> Job | None:
    if not data or len(data) != JOB_LEN:
        return None
    return Job(state=STATES.get(data[0], "?"), mode=data[1], from_balance=data[2] == 1, token_program=TOKEN_2022 if data[6] == 1 else TOKEN,
               faucet=data[7] == 1, repo_id=_n(data, 8), issue=_n(data, 16), amount=_n(data, 24), deadline=_i64(data, 32),
               hold_until=_i64(data, 48), payee_id=_n(data, 56), funder_id=_n(data, 64), not_before=_i64(data, 72), owner_id=_n(data, 80),
               source=Pubkey.from_bytes(data[88:120]), refund_to=Pubkey.from_bytes(data[120:152]), rent_to=Pubkey.from_bytes(data[152:184]),
               mint=Pubkey.from_bytes(data[184:216]), terms=bytes(data[216:248]), wf_repo_hash=bytes(data[248:280]),
               wf_sha=bytes(data[280:320]).decode("ascii", "replace"))


@dataclass
class Balance:
    faucet: bool
    owner_id: int
    authority: Pubkey
    mint: Pubkey
    cap_per_job: int        # 0: no cap
    last_iat: int
    spenders: tuple[int, ...]
    spent: int              # everything it ever put into jobs


def read_balance(data: bytes | None) -> Balance | None:
    if not data or len(data) != BALANCE_LEN or data[0] != 1:
        return None
    return Balance(faucet=data[2] == 1, owner_id=_n(data, 8), authority=Pubkey.from_bytes(data[16:48]), mint=Pubkey.from_bytes(data[48:80]),
                   cap_per_job=_n(data, 80), last_iat=_i64(data, 88), spenders=tuple(s for s in (_n(data, 96 + 8 * k) for k in range(4)) if s),
                   spent=_n(data, 128))


@dataclass
class Bind:
    user_id: int
    wallet: Pubkey
    iat: int


def read_bind(data: bytes | None) -> Bind | None:
    if not data or len(data) != BIND_LEN or data[0] != 1:
        return None
    return Bind(user_id=_n(data, 8), wallet=Pubkey.from_bytes(data[16:48]), iat=_i64(data, 48))


@dataclass
class Record:
    paid: int               # payments in real money from someone else
    funders: int            # distinct funders among them
    total: int              # what those payments put in the wallet
    test_paid: int          # payments in the faucet's test USDC
    self_paid: int          # payments whose funder was the payee
    test_total: int
    first: int              # time of the first and of the latest payment counted in `paid` (0: none yet)
    last: int


def read_rep(data: bytes | None) -> Record:
    if not data or len(data) != REP_LEN:
        return Record(0, 0, 0, 0, 0, 0, 0, 0)
    return Record(paid=_n(data, 0, 4), funders=_n(data, 4, 4), total=_n(data, 8), test_paid=_n(data, 16, 4), self_paid=_n(data, 20, 4),
                  test_total=_n(data, 32), first=_i64(data, 40), last=_i64(data, 48))


def read_pause(data: bytes | None) -> int:
    """The time until which new funding is refused (0: not paused, or never was). `data`: the account at pause_pda()."""
    return _i64(data, 0) if data and len(data) == 8 else 0


def read_rate(data: bytes | None) -> tuple[int, int]:
    """(when the devnet faucet last served a repository, that fund token's iat); (0, 0) when it never did. The next
    use is FUND_PERIOD later at the earliest, with a newer token. `data`: the account at rate_pda(repository id)."""
    return (_i64(data, 0), _i64(data, 8)) if data and len(data) == 16 else (0, 0)


def destination(bind: Bind | None, audience: str) -> Pubkey | None:
    """Where the program will pay a pay token: the payee's bound wallet, else the address the audience carries, else
    None (the job is held for the payee). `bind` is read_bind of bind_pda(payee id)."""
    if bind is not None:
        return bind.wallet
    address = audience.split(":")[-1]
    return None if address == "-" else Pubkey.from_string(address)


# -- instructions ---------------------------------------------------------------------------------------------------
def _limits(cap: int, spenders) -> bytes:
    ids = list(spenders)
    if len(ids) > 4:
        raise ValueError("a balance has at most 4 spenders")
    return _u64(cap) + b"".join(_u64(s) for s in ids + [0] * (4 - len(ids)))


def open_balance_ix(authority: Pubkey, owner_id: int, mint: Pubkey, cap: int = 0, spenders=(), token_program: Pubkey = TOKEN,
                    program: Pubkey = PAY_ID) -> Instruction:
    """Opens `authority`'s Balance for the repositories of the GitHub owner `owner_id`. `cap`: the most one job may
    take (0: no cap). `spenders`: up to 4 GitHub ids that may spend it by comment, besides the owner."""
    balance = balance_pda(owner_id, authority, mint, program)
    return Instruction(program, b"\x00" + _u64(owner_id) + _limits(cap, spenders),
                       [AccountMeta(authority, True, True), AccountMeta(balance, False, True), AccountMeta(baltok_pda(balance, program), False, True),
                        AccountMeta(mint, False, False), AccountMeta(auth_pda(program), False, False), AccountMeta(token_program, False, False),
                        AccountMeta(SYSTEM, False, False)])


def set_balance_ix(authority: Pubkey, balance: Pubkey, cap: int = 0, spenders=(), program: Pubkey = PAY_ID) -> Instruction:
    return Instruction(program, b"\x01" + _limits(cap, spenders), [AccountMeta(authority, True, False), AccountMeta(balance, False, True)])


def withdraw_ix(authority: Pubkey, balance: Pubkey, mint: Pubkey, amount: int = 0, dest_token: Pubkey | None = None, token_program: Pubkey = TOKEN,
                program: Pubkey = PAY_ID) -> Instruction:
    """Unspent money back to the wallet that opened the Balance. `amount` 0: everything. `dest_token`: a token account
    of that wallet (default: its associated token account)."""
    dest = dest_token if dest_token is not None else ata(authority, mint, token_program)
    return Instruction(program, b"\x02" + _u64(amount),
                       [AccountMeta(authority, True, False), AccountMeta(balance, False, False), AccountMeta(baltok_pda(balance, program), False, True),
                        AccountMeta(dest, False, True), AccountMeta(mint, False, False), AccountMeta(auth_pda(program), False, False),
                        AccountMeta(token_program, False, False)])


def fund_balance_ix(relayer: Pubkey, fund_token: Pubkey, key: Pubkey, balance: Pubkey, mint: Pubkey, repo_id: int, issue: int, terms: bytes,
                    token_program: Pubkey = TOKEN, program: Pubkey = PAY_ID) -> Instruction:
    """Funds the job a fund token describes from the Balance its audience names (`named_balance`). `key` is the
    verifier's account of the key that verified the token. `mint` is the Balance's; `repo_id` and `issue` are the
    token's; `terms` is the terms JSON whose hash the audience carries."""
    return Instruction(program, b"\x03" + terms,
                       [AccountMeta(relayer, True, True), AccountMeta(fund_token, False, False), AccountMeta(key, False, False),
                        AccountMeta(balance, False, True), AccountMeta(baltok_pda(balance, program), False, True),
                        AccountMeta(job_pda(repo_id, issue, balance, program), False, True), AccountMeta(vault_pda(mint, program), False, True),
                        AccountMeta(mint, False, False), AccountMeta(auth_pda(program), False, False), AccountMeta(token_program, False, False),
                        AccountMeta(SYSTEM, False, False), AccountMeta(pause_pda(program), False, False)])


def fund_wallet_ix(funder: Pubkey, funder_token: Pubkey, mint: Pubkey, repo_id: int, issue: int, amount: int, wf_repo: str, wf_sha: str, terms: bytes,
                   mode: int = MERGE, work_s: int = 14 * 86_400, token_program: Pubkey = TOKEN, program: Pubkey = PAY_ID) -> Instruction:
    """A wallet funds a job with its own money. `wf_repo` ("owner/name") and `wf_sha` pin the prove.yml whose signed run can pay it."""
    data = (b"\x04" + _u64(repo_id) + _u64(issue) + _u64(amount) + work_s.to_bytes(8, "little", signed=True) + bytes([mode])
            + wf_repo_hash(wf_repo) + wf_sha.encode() + terms)
    return Instruction(program, data,
                       [AccountMeta(funder, True, True), AccountMeta(job_pda(repo_id, issue, funder, program), False, True),
                        AccountMeta(funder_token, False, True), AccountMeta(vault_pda(mint, program), False, True), AccountMeta(mint, False, False),
                        AccountMeta(auth_pda(program), False, False), AccountMeta(token_program, False, False), AccountMeta(SYSTEM, False, False),
                        AccountMeta(pause_pda(program), False, False)])


def _payout_accounts(job: Pubkey, j: Job, payee_id: int, wallet: Pubkey | None, dest_token: Pubkey | None, program: Pubkey) -> list[AccountMeta]:
    vault = vault_pda(j.mint, program)
    # a job that will be held names no destination: the vault stands in, and the program does not touch it
    dest = dest_token if dest_token is not None else ata(wallet, j.mint, j.token_program) if wallet is not None else vault
    return [AccountMeta(job, False, True), AccountMeta(bind_pda(payee_id, program), False, False), AccountMeta(dest, False, True),
            AccountMeta(rep_pda(payee_id, program), False, True), AccountMeta(pair_pda(payee_id, j.funder, program), False, True),
            AccountMeta(vault, False, True), AccountMeta(ata(FEE_OWNER, j.mint, j.token_program), False, True),
            AccountMeta(auth_pda(program), False, False), AccountMeta(j.rent_to, False, True), AccountMeta(j.mint, False, False),
            AccountMeta(j.token_program, False, False), AccountMeta(SYSTEM, False, False)]


def pay_ix(relayer: Pubkey, pay_token: Pubkey, key: Pubkey, job: Pubkey, j: Job, payee_id: int, wallet: Pubkey | None,
           dest_token: Pubkey | None = None, program: Pubkey = PAY_ID) -> Instruction:
    """`key` is the verifier's account of the key that verified the token. `j` is read_job of `job`. `wallet` is
    destination(read_bind(...), audience): the program pays a token account of that wallet (default: its associated
    token account; create it and FEE_OWNER's with create_ata_ix first), or holds the job when it is None."""
    return Instruction(program, b"\x05", [AccountMeta(relayer, True, True), AccountMeta(pay_token, False, False), AccountMeta(key, False, False),
                                          *_payout_accounts(job, j, payee_id, wallet, dest_token, program)])


def settle_ix(relayer: Pubkey, job: Pubkey, j: Job, wallet: Pubkey, dest_token: Pubkey | None = None, program: Pubkey = PAY_ID) -> Instruction:
    """Pays a held job once its payee has bound a wallet: `wallet` is read_bind(bind_pda(j.payee_id)).wallet."""
    return Instruction(program, b"\x06", [AccountMeta(relayer, True, True), *_payout_accounts(job, j, j.payee_id, wallet, dest_token, program)])


def refund_ix(relayer: Pubkey, job: Pubkey, j: Job, refund_token: Pubkey | None = None, program: Pubkey = PAY_ID) -> Instruction:
    """An open job past its deadline, or a held job past its hold: the money back to the Balance it came from, or to a
    token account of the funding wallet (default: its associated token account)."""
    dest = refund_token if refund_token is not None else j.refund_to if j.from_balance else ata(j.refund_to, j.mint, j.token_program)
    return Instruction(program, b"\x07", [AccountMeta(relayer, True, True), AccountMeta(job, False, True), AccountMeta(vault_pda(j.mint, program), False, True),
                                          AccountMeta(dest, False, True), AccountMeta(auth_pda(program), False, False), AccountMeta(j.rent_to, False, True),
                                          AccountMeta(j.mint, False, False), AccountMeta(j.token_program, False, False)])


def bind_ix(relayer: Pubkey, bind_token: Pubkey, key: Pubkey, user_id: int, program: Pubkey = PAY_ID) -> Instruction:
    """`key` is the verifier's account of the key that verified the token. `user_id` is the token's actor_id."""
    return Instruction(program, b"\x08", [AccountMeta(relayer, True, True), AccountMeta(bind_token, False, False), AccountMeta(key, False, False),
                                          AccountMeta(bind_pda(user_id, program), False, True), AccountMeta(SYSTEM, False, False)])


def pause_ix(guardian: Pubkey, payer: Pubkey, seconds: int, program: Pubkey = PAY_ID) -> Instruction:
    """The guardian refuses new funding for `seconds` (at most PAUSE_MAX) from now; 0 lifts the pause."""
    return Instruction(program, b"\x09" + seconds.to_bytes(4, "little"),
                       [AccountMeta(guardian, True, False), AccountMeta(payer, True, True), AccountMeta(pause_pda(program), False, True),
                        AccountMeta(SYSTEM, False, False)])


def init_faucet_ix(payer: Pubkey, program: Pubkey = PAY_ID) -> Instruction:
    return Instruction(program, b"\x0a", [AccountMeta(payer, True, True), AccountMeta(faucet_mint(program), False, True),
                                          AccountMeta(auth_pda(program), False, False), AccountMeta(TOKEN, False, False), AccountMeta(SYSTEM, False, False)])


def faucet_open_ix(relayer: Pubkey, fund_token: Pubkey, key: Pubkey, owner_id: int, repo_id: int, program: Pubkey = PAY_ID) -> Instruction:
    """Devnet: mints the fund token's amount of test USDC into the faucet Balance of the token's repository owner
    (`owner_id`, `repo_id` are the token's), which the token's audience must name. `key` is the verifier's account of
    the key that verified the token. Send fund_balance_ix(..., faucet_balance_pda(owner_id), faucet_mint(), ...) after
    it, in the same transaction."""
    balance = faucet_balance_pda(owner_id, program)
    return Instruction(program, b"\x0b", [AccountMeta(relayer, True, True), AccountMeta(fund_token, False, False), AccountMeta(key, False, False),
                                          AccountMeta(balance, False, True), AccountMeta(baltok_pda(balance, program), False, True),
                                          AccountMeta(faucet_mint(program), False, True), AccountMeta(auth_pda(program), False, False),
                                          AccountMeta(TOKEN, False, False), AccountMeta(SYSTEM, False, False),
                                          AccountMeta(rate_pda(repo_id, program), False, True)])
