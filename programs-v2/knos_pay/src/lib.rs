//! knos-pay, the second deployment: pay on proof. A funder puts a bounty for one issue of one GitHub repository in
//! escrow. It is paid to the author of the pull request that meets the terms fixed at funding, when GitHub itself
//! signs a token saying the funder's pinned prove.yml workflow saw every funded condition met at the merged commit.
//! The token is verified on chain by knos-oidc (OIDC_ID). This program is upgradeable only through a multisig with a
//! public 48-hour delay, until an outside review; then made immutable. In the code below no key of Knos's moves money
//! or chooses where it goes: the guardian (another multisig) can refuse NEW funding for at most seven days per
//! signature (Pause), and nothing else here. In knos-oidc its guardian can revoke a signing key; this program then
//! refuses every token that key verified (TOKENS). A proof signed with it pays nothing: the job waits for a proof
//! under a key that is good, or goes back to its funder at the deadline (Refund needs no token). A revocation can
//! only make this program refuse: nothing makes it accept a token GitHub did not sign.
//!
//! WHERE MONEY CAN GO. Tokens leave an account of this program only by these six transfers, each a TransferChecked
//! (the mint is named, its decimals checked) between accounts of one mint:
//!   a Balance's token account -> the mint's vault         FundBalance, on a fund token: the job is credited
//!   a wallet's token account  -> the mint's vault         FundWallet, the wallet signs: the job is credited
//!   a Balance's token account -> a token account of the wallet that opened that Balance       Withdraw, that wallet signs
//!   the vault -> a token account of FEE_OWNER             Pay, Settle: the fee, fee_of(amount)
//!   the vault -> a token account of the payee's wallet    Pay, Settle: the rest of the job's amount
//!   the vault -> where the job's money came from          Refund: that Balance's token account, or a token account of the funding wallet
//! A job is credited with what its vault received (the vault's balance after the transfer minus before). A payment
//! and a refund move exactly the job's amount and close the job in the same instruction. So in every mint the open
//! and held jobs add up to exactly what these instructions have left in the vault. Tokens someone sends to a vault
//! directly belong to no job and no instruction moves them; tokens a mint's issuer takes out of a vault (a permanent
//! delegate can) leave the jobs as they were, unpaid until the tokens are back. The devnet faucet mints test USDC,
//! into a faucet Balance only. Lamports: a job's rent goes back to whoever paid it (`rent_to`) when the job closes;
//! the other accounts keep theirs.
//!
//! ACCOUNTS. All are PDAs of this program, created by it; layouts are in state.rs; ids in seeds are u64 little-endian.
//!   auth     ["auth"]                            owns every token account below and is the faucet mint's authority; holds nothing
//!   vault    ["vault", mint]                     token account: the money of every open and held job in that mint
//!   Balance  ["bal", owner id, authority, mint]  money a wallet (authority) set aside for bounties in the repositories of one
//!                                                GitHub owner (a user or an organisation), who may spend it, how much per job
//!   baltok   ["baltok", balance]                 the Balance's token account. Anyone adds money with a plain transfer to it.
//!   Job      ["job", repo id, issue, source]     one bounty; source is the Balance it was funded from, or the funding wallet
//!   Bind     ["bind", user id]                   the wallet a GitHub user is paid at
//!   Rep      ["rep", user id]                    a GitHub user's public record of payments
//!   Pair     ["pair", payee id, funder key]      exists once this funder has paid this payee in real money
//!   Pause    ["pause"]                           new funding is refused until this time
//!   Rate     ["rate", repo id]                   devnet: the faucet's last use by this repository
//!   mint     ["mint"]                            devnet: the faucet's test-USDC mint (SPL Token, 6 decimals)
//!
//! TOKENS. A token is a GitHub Actions OIDC token in an account that knos-oidc owns and marked VERIFIED (GitHub's RS256
//! signature was checked on chain). Every instruction that takes one requires, in gh.rs: issuer GitHub; at most
//! knos_oidc::LATE (an hour) past its expiry; `iat` at most TOKEN_AHEAD ahead of the chain's clock and `exp` at most
//! TOKEN_LIFE after `iat` (GitHub's tokens live five minutes; nothing works for ever); `runner_environment`
//! github-hosted. And the key that verified the token must still be good: the instruction takes knos-oidc's key
//! account (`key`) right after the token account, and it must be the account the token account names (its bytes
//! 18..50), owned by knos-oidc, and usable at the chain's time by knos_oidc::key_usable. So a token is refused from
//! the moment its key is revoked or expires, with knos-oidc's own code (76 not active, 77 expired, 78 revoked); once
//! an expired key is attested again (knos-oidc's Refresh) its tokens work for what is left of their own time. A token
//! is not a secret: anyone may relay one, and what it can do is fixed by its claims and its audience (`aud`):
//!   fund  knos2:fund:<issue>:<amount units>:<mode 0|1>:<terms hash hex>:<work seconds>:<balance address>         from fund.yml
//!   pay   knos2:pay:<repository id>:<issue>:<payee github id>:<head sha>:<terms hash hex>:<mode>:<address or ->  from prove.yml
//!   bind  knos2:bind:<address>                                                                                   from the pinned claim.yml
//! A fund token names the Balance it spends, by its address as Solana prints it: the funder's own workflow chooses
//! the Balance when it asks GitHub for the token (it reads the chain), and a relayer cannot spend another one with it.
//! A job pins its workflows at funding: the repository that holds them (sha256 of "owner/name", from the fund token's
//! `job_workflow_ref`) and their commit (`job_workflow_sha`). Only that repository's prove.yml at that commit proves
//! the job. The terms (what must hold for the bounty to be paid) are a JSON document: the funding instruction carries
//! it, stores its sha256 in the job and logs it, so the terms are public and cannot change, and a pay token must carry
//! the same hash. mode 0 (merge) and 1 (tests) are the two ways prove.yml judges; this program only matches them.
//!
//! INSTRUCTIONS. The first byte of the data is the tag; integers are little-endian; (s) signs, (w) is writable.
//!   0 OpenBalance  authority(s,w) balance(w) baltok(w) mint auth token_program system
//!                  data: owner_id u64, cap u64, spenders [u64; 4]
//!                  Any wallet, for any GitHub owner id but 0: it is the wallet's own money. Creates the Balance and its
//!                  token account, both empty. The mint must pass the mint rules. No money moves.
//!   1 SetBalance   authority(s) balance(w)
//!                  data: cap u64, spenders [u64; 4]
//!                  The wallet that opened the Balance sets its cap per job (0: none) and its spenders (0: empty).
//!   2 Withdraw     authority(s) balance baltok(w) dest_token(w) mint auth token_program
//!                  data: amount u64
//!                  The wallet that opened the Balance takes unspent money back (amount 0: all of it). dest_token must be
//!                  a token account of the Balance's mint owned by that same wallet. A faucet Balance cannot withdraw.
//!                  Never paused.
//!   3 FundBalance  relayer(s,w) fund_token key balance(w) baltok(w) job(w) vault(w) mint auth token_program system pause
//!                  data: terms bytes
//!                  Anyone relays. Not paused. The token: workflow file fund.yml; event `issue_comment` or `issues`, and
//!                  `run_attempt` 1 (a re-run keeps the first actor's name whoever starts it); the audience names this
//!                  Balance; `repository_owner_id` is the Balance's owner; `actor_id` is that owner or one of the
//!                  Balance's spenders (a faucet Balance: any actor); `iat` is later than the Balance's last, which is
//!                  then set to it: a fund token works once, and a Balance takes its tokens in the order GitHub issued
//!                  them (of two issued in the same second, one). The audience:
//!                  amount MIN_AMOUNT..=MAX_AMOUNT and at most the Balance's cap, work MIN_WORK..=MAX_WORK seconds, and
//!                  the hash of the terms in the data (at most MAX_TERMS bytes, printable ASCII). The Balance's token
//!                  account holds the amount; the job ["job", repository_id, issue, balance] does not exist; the mint
//!                  passes the mint rules. Money: the amount, baltok -> vault. The job refunds to baltok, its rent is the
//!                  relayer's, its workflows are the token's, its deadline is now + work, proofs count from the token's `iat`.
//!   4 FundWallet   funder(s,w) job(w) funder_token(w) vault(w) mint auth token_program system pause
//!                  data: repo_id u64, issue u64, amount u64, work i64, mode u8, wf_repo [u8; 32], wf_sha [u8; 40], terms bytes
//!                  The funding wallet signs. Not paused. The same bounds; repo_id not 0; wf_sha 40 hex characters; the job
//!                  ["job", repo_id, issue, funder] does not exist; the mint passes the mint rules. Money: the amount,
//!                  funder_token -> vault (the token program checks the wallet's authority over funder_token). The job
//!                  refunds to the wallet, its rent is the wallet's, proofs count from now less CLOCK_SLACK.
//!   5 Pay          relayer(s,w) pay_token key job(w) bind dest_token(w) rep(w) pair(w) vault(w) fee_token(w) auth rent_to(w) mint token_program system
//!                  Anyone relays. The job is OPEN and its deadline has not passed. The token: workflow file prove.yml of
//!                  the job's pinned repository at the job's pinned commit; `repository_id` is the job's; `iat` not
//!                  before the job's `not_before`. The audience: the job's repository, issue, terms hash and mode; a payee
//!                  id that is not 0; a head sha of 40 hex characters; an address or "-". Destination: the wallet in the
//!                  payee's Bind (the account must be ["bind", payee], so a Bind cannot be hidden); with no Bind, the
//!                  address in the audience; with neither, no money moves and the job becomes HELD for that payee until
//!                  now + HOLD (the accounts after `bind` are then not used). A proof may be run again: any run of that
//!                  workflow counts, a re-run included. Paying is described under Settle.
//!   6 Settle       relayer(s,w) job(w) bind dest_token(w) rep(w) pair(w) vault(w) fee_token(w) auth rent_to(w) mint token_program system
//!                  Anyone relays. A HELD job, not past its hold, whose payee now has a Bind: paid to that wallet.
//!                  Paying (Pay with a destination, and Settle): dest_token is a token account of the job's mint owned by
//!                  the destination wallet, which cannot be ["auth"]; fee_token is one owned by FEE_OWNER; mint and
//!                  token_program are the job's; vault is the mint's; rent_to is the job's. Money: fee_of(amount) from the
//!                  vault to fee_token, the rest to dest_token. Then the record (Rep, Pair) is updated and the job is
//!                  closed. After a proof there is no veto and no waiting time: the payment is final.
//!   7 Refund       relayer(s,w) job(w) vault(w) refund_token(w) auth rent_to(w) mint token_program
//!                  Anyone relays, and no token is needed: it works whatever happens to GitHub or to Knos. The job is OPEN
//!                  past its deadline, or HELD past its hold. Money: the job's amount from the vault to refund_token,
//!                  which is exactly the Balance's token account (a Balance's job) or a token account of the job's mint
//!                  owned by the funding wallet (a wallet's job). The job is closed.
//!   8 Bind         relayer(s,w) bind_token key bind(w) system
//!                  Anyone relays. The token: `job_workflow_ref` starts with CLAIM_REF and `job_workflow_sha` is CLAIM_SHA
//!                  (the pinned claim workflow); `actor_id` equals `repository_owner_id` and is not 0; `repository` ends
//!                  with "/knos-claim"; event `workflow_dispatch` only (a run its owner started by hand: a push, even
//!                  the first, is refused, because a prefilled new-repository link can choose what a first push
//!                  says); `run_attempt` 1; the audience's address is a key. Sets ["bind", actor_id] to that address.
//!                  A Bind that exists is replaced only by a token with a later `iat`. No money moves.
//!   9 Pause        guardian(s) payer(s,w) pause(w) system
//!                  data: seconds u32
//!                  GUARDIAN signs. FundBalance and FundWallet are refused until now + seconds (at most PAUSE_MAX; 0 lifts
//!                  it). A pause ends by itself; keeping one needs the guardian's signature again. Every other instruction
//!                  ignores it: payments, refunds, withdrawals and binds cannot be paused.
//!   10 InitFaucet  payer(s,w) mint(w) auth token_program system
//!                  Devnet builds only. Anyone, once. Creates the faucet's test-USDC mint; its mint authority is ["auth"].
//!   11 FaucetOpen  relayer(s,w) fund_token key balance(w) baltok(w) mint(w) auth token_program system rate(w)
//!                  Devnet builds only. Anyone relays. The same fund token as FundBalance (fund.yml, event, first attempt,
//!                  bounds), amount at most FAUCET_CAP, once per repository per FUND_PERIOD and only with a later `iat`
//!                  than the last (Rate). The Balance its audience names must be the faucet Balance ["bal",
//!                  repository_owner_id, ["auth"], faucet mint]: a token that names a Balance of real money mints
//!                  nothing. Mints the amount of test USDC into that Balance (created on first use and flagged: any
//!                  actor spends it, nobody withdraws it). The token is not used up: FundBalance then spends that
//!                  Balance with it.
//!
//! MINT RULES (token.rs). The mint's owner is SPL Token or Token-2022, and the token program passed is that owner.
//! Token accounts of a Token-2022 mint are created with the size GetAccountDataSize returns. When money enters
//! (OpenBalance, FundBalance, FundWallet, FaucetOpen) a Token-2022 mint's extensions are walked, and refused are:
//! NonTransferable, a frozen default account state, a transfer hook with a program set, a transfer fee above zero now
//! or scheduled. PermanentDelegate, ConfidentialTransfer and a transfer hook or fee with nothing in it are accepted
//! (the regulated stablecoins carry them). Money leaving escrow is never held to these rules: what entered can go
//! out. A mint's issuer keeps the powers its mint gives it over every holder (freezing, a permanent delegate, a later
//! fee or hook), and this program cannot take them away.
//!
//! THE RECORD. Rep counts a payment of a job in the faucet's mint under test_paid/test_total; one whose funder is the
//! payee (the Balance's owner or the funding commenter is the payee, or a wallet's job is paid back to that wallet)
//! under self_paid; any other under paid/total (what reached the payee's wallet) and first/last, and under funders
//! when its Pair account is new. A funder is the funding wallet, or a Balance's GitHub owner (sha256("gh" || owner id)).
//!
//! LOGS, one line each, for an indexer (ids and amounts in decimal, addresses in base58, times in unix seconds):
//!   knos2:balance owner= authority= mint=          a Balance was opened
//!   knos2:funded repo= issue= amount= mode= by= source= faucet=      amount: what the vault received; by: the commenter's
//!                                                  GitHub id (0: a wallet); source: the Balance or the wallet
//!   knos2:terms <json>                             follows knos2:funded: the terms whose hash the job stores
//!   knos2:paid repo= issue= payee= amount= fee= to=                  amount: what reached the wallet `to`; fee: what FEE_OWNER got
//!   knos2:held repo= issue= payee= until=          knos2:refunded repo= issue= amount=
//!   knos2:bound user= wallet=                      knos2:withdrawn owner= authority= mint= amount=      knos2:paused until=
pub mod fund;
pub mod gh;
pub mod pay;
pub mod state;
pub mod token;

pub use knos_oidc::claims::err;
use solana_program::{account_info::AccountInfo, clock::Clock, entrypoint::ProgramResult, program_error::ProgramError, pubkey, pubkey::Pubkey, sysvar::Sysvar};

/// The knos-oidc program whose VERIFIED token accounts this program accepts (programs-v2/program_ids.json).
pub const OIDC_ID: Pubkey = pubkey!("FkwZdsYCmzicJMtHLTkPK76bYNVG4WNwkWJBiVWNtF3W");
/// Fees go to a token account owned by this address (Knos's Squads vault). It has no other power.
pub const FEE_OWNER: Pubkey = pubkey!("4G3cznCnwCUPBCZwzKiLupjdgB5pSoCcGWNGuFv4TYFo");
/// May pause new funding (Pause), and nothing else: a Squads vault.
pub const GUARDIAN: Pubkey = pubkey!("AT1aKj1DpgaWerxmS4YjDkNpWPNUtCCKVDvxLhFxg5Jc");
/// The claim workflow: a Bind token's `job_workflow_ref` must start with CLAIM_REF and its `job_workflow_sha` must be
/// CLAIM_SHA. A commit sha fixes the workflow file's content.
pub const CLAIM_REF: &[u8] = b"drexthealpha/knos-oidc-rotate/.github/workflows/claim.yml@";
pub const CLAIM_SHA: &[u8; 40] = b"80e796d341f65060767939c4e65a38ede5fa1ac8";

/// Test builds only (`--features testkeys`, which also makes knos-oidc trust the test keys): a test claim pin, and a
/// test guardian, the ed25519 key of the seed [7; 32]. Never deploy a testkeys build.
#[cfg(feature = "testkeys")]
pub const TEST_CLAIM_SHA: Option<&[u8; 40]> = Some(b"2222222222222222222222222222222222222222");
#[cfg(not(feature = "testkeys"))]
pub const TEST_CLAIM_SHA: Option<&[u8; 40]> = None;
#[cfg(feature = "testkeys")]
pub const TEST_GUARDIAN: Option<Pubkey> = Some(Pubkey::new_from_array([
    234, 74, 108, 99, 226, 156, 82, 10, 190, 245, 80, 123, 19, 46, 197, 249, 149, 71, 118, 174, 190, 190, 123, 146, 66, 30, 234, 105, 20, 70, 210, 44,
]));
#[cfg(not(feature = "testkeys"))]
pub const TEST_GUARDIAN: Option<Pubkey> = None;

pub const FEE_BPS: u64 = 250;             // 2.5%
pub const FEE_MIN: u64 = 50_000;          // 0.05 of a 6-decimal mint; never more than the amount
pub const MIN_AMOUNT: u64 = 1_000_000;    // 1.00, in the mint's smallest units
pub const MAX_AMOUNT: u64 = 500_000_000;  // 500.00 per job until an outside review
pub const MIN_WORK: i64 = 60;
pub const MAX_WORK: i64 = 90 * 86_400;
pub const HOLD: i64 = 180 * 86_400;       // how long a proven job waits for its payee to bind a wallet
pub const FAUCET_CAP: u64 = 100_000_000;  // devnet: at most 100 test USDC per faucet use
pub const FUND_PERIOD: i64 = 60;          // devnet: one faucet use per repository per minute
pub const CLOCK_SLACK: i64 = 30;          // the chain's clock and GitHub's can differ by this much
pub const TOKEN_AHEAD: i64 = 300;         // a token's `iat` may be this far ahead of the chain's clock (the clock can lag)
pub const TOKEN_LIFE: i64 = 3600;         // a token's `exp` is at most this long after its `iat` (GitHub's is 300 seconds)
pub const PAUSE_MAX: u32 = 7 * 86_400;
pub const MAX_TERMS: usize = 600;
pub const DEVNET: bool = cfg!(feature = "devnet");

// errors 60-63 are knos-oidc's claim reader's; 76-78 are knos-oidc's too: the key that verified a token is not active
// yet, has expired, or was revoked (gh.rs)
pub const E_ACCOUNTS: u32 = 80;  // a wrong account, or a missing signature
pub const E_TERMS: u32 = 81;     // amount, work time, mode, workflow commit, terms JSON or pause length not allowed
pub const E_JOB: u32 = 82;       // the job exists already, or this account is not a job
pub const E_STATE: u32 = 83;     // wrong state or time for this instruction
pub const E_TOKEN: u32 = 84;     // the token is not verified by knos-oidc, not GitHub's, or expired
pub const E_CLAIMS: u32 = 85;    // the token's claims do not allow this
pub const E_WORKFLOW: u32 = 86;  // the token is not from the workflow this instruction needs
pub const E_AUD: u32 = 87;       // the audience does not match
pub const E_PAYEE: u32 = 88;     // wrong destination, fee or refund account, or the payee has no bound wallet
pub const E_DEVNET: u32 = 89;    // devnet builds only
pub const E_RATE: u32 = 90;      // the faucet: one use per repository per minute, in the order GitHub issued the tokens
pub const E_REPLAY: u32 = 91;    // this token is not newer than the last one used here: a token works once
pub const E_SPENDER: u32 = 92;   // the token's repository owner or actor may not spend this Balance
pub const E_CAP: u32 = 93;       // more than the Balance's cap per job
pub const E_FUNDS: u32 = 94;     // the Balance does not hold that much
pub const E_MINT: u32 = 95;      // the mint or the token program is not accepted
pub const E_PAUSED: u32 = 96;    // new funding is paused
pub const E_GUARDIAN: u32 = 97;  // not the guardian
pub const E_BALANCE: u32 = 98;   // the Balance exists already, is not a Balance, or the signer is not its authority
pub const E_FAUCET: u32 = 99;    // a faucet Balance cannot withdraw

#[cfg(not(feature = "no-entrypoint"))]
solana_program::entrypoint!(process);

#[cfg(not(feature = "no-entrypoint"))]
solana_security_txt::security_txt! {
    name: "knos-pay",
    project_url: "https://github.com/drexthealpha/Knos",
    contacts: "link:https://github.com/drexthealpha/Knos/security/advisories/new",
    policy: "https://github.com/drexthealpha/Knos/blob/main/SECURITY.md",
    source_code: "https://github.com/drexthealpha/Knos"
}

/// The fee of a payment: FEE_BPS of the amount, at least FEE_MIN, never more than the amount.
pub fn fee_of(amount: u64) -> u64 { (amount / 10_000 * FEE_BPS + amount % 10_000 * FEE_BPS / 10_000).max(FEE_MIN).min(amount) }

pub fn process(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> ProgramResult {
    let (&tag, rest) = data.split_first().ok_or(ProgramError::InvalidInstructionData)?;
    let now = Clock::get()?.unix_timestamp;
    match tag {
        0 => fund::open_balance(program_id, accounts, rest),
        1 => fund::set_balance(program_id, accounts, rest),
        2 => fund::withdraw(program_id, accounts, rest),
        3 => fund::fund_balance(program_id, accounts, rest, now),
        4 => fund::fund_wallet(program_id, accounts, rest, now),
        5 => pay::pay(program_id, accounts, rest, now),
        6 => pay::settle(program_id, accounts, rest, now),
        7 => pay::refund(program_id, accounts, rest, now),
        8 => pay::bind(program_id, accounts, rest, now),
        9 => fund::pause(program_id, accounts, rest, now),
        10 => fund::init_faucet(program_id, accounts, rest),
        11 => fund::faucet_open(program_id, accounts, rest, now),
        _ => Err(ProgramError::InvalidInstructionData),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_fee_is_two_and_a_half_percent_with_a_floor_and_never_more_than_the_amount() {
        for (amount, fee) in [(0, 0), (1, 1), (49_999, 49_999), (50_000, 50_000), (1_000_000, 50_000), (2_000_000, 50_000), (2_000_040, 50_001),
                              (5_000_000, 125_000), (500_000_000, 12_500_000), (u64::MAX, u64::MAX / 10_000 * 250 + (u64::MAX % 10_000) * 250 / 10_000)] {
            assert_eq!(fee_of(amount), fee, "{amount}");
            assert!(fee_of(amount) <= amount);
        }
    }

    #[test]
    fn the_bounds_are_the_ones_the_design_fixes() {
        assert_eq!((MIN_AMOUNT, MAX_AMOUNT, MAX_WORK, HOLD, PAUSE_MAX, MAX_TERMS), (1_000_000, 500_000_000, 7_776_000, 15_552_000, 604_800, 600));
        assert!(CLAIM_REF.ends_with(b"/.github/workflows/claim.yml@") && knos_oidc::claims::is_hex(CLAIM_SHA, 40));
        const { assert!(TOKEN_LIFE >= 300 && TOKEN_AHEAD >= CLOCK_SLACK) };
    }
}
