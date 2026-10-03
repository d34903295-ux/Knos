//! Money coming in, and a funder's own money going back out before it is in a job: OpenBalance, SetBalance, Withdraw,
//! FundBalance, FundWallet, Pause, and the devnet faucet (InitFaucet, FaucetOpen).
use crate::{err, gh::*, state::*, token::*, *};
use knos_oidc::claims;
use solana_program::{account_info::AccountInfo, entrypoint::ProgramResult, hash::hashv, msg, program_error::ProgramError, pubkey::Pubkey};

fn terms_ok(amount: u64, work: i64, mode: u8) -> bool {
    (MIN_AMOUNT..=MAX_AMOUNT).contains(&amount) && (MIN_WORK..=MAX_WORK).contains(&work) && mode <= 1
}
/// sha256 of the terms JSON a funding instruction carries: at most MAX_TERMS bytes of printable ASCII, because it is
/// logged as one line for everyone to read.
fn terms_hash(json: &[u8]) -> Result<[u8; 32], ProgramError> {
    if json.is_empty() || json.len() > MAX_TERMS || !json.iter().all(|c| (0x20..0x7f).contains(c)) { return Err(err(E_TERMS)); }
    Ok(hashv(&[json]).to_bytes())
}
/// New funding is refused while the guardian's pause lasts. The account must be ["pause"] itself, so a pause cannot
/// be hidden by passing another account.
fn not_paused(program_id: &Pubkey, pause: &AccountInfo, now: i64) -> ProgramResult {
    if *pause.key != Pubkey::find_program_address(&[b"pause"], program_id).0 { return Err(err(E_ACCOUNTS)); }
    if pause.owner == program_id && pause.data_len() == PAUSE_LEN && now < i64_at(&pause.try_borrow_data()?, 0) { return Err(err(E_PAUSED)); }
    Ok(())
}
/// A fund token is from fund.yml, for a comment on an issue or an issue event: both run the workflow of the
/// repository's default branch, in the repository, with the commenter as `actor_id`. And from the run's first
/// attempt: a re-run keeps the first actor's name whoever starts it, so it would let anyone with write access spend
/// as the commenter again. Its audience names `balance`, the Balance the instruction was given.
fn fund_token(tok: &AccountInfo, key: &AccountInfo, balance: &Pubkey, now: i64) -> Result<(Gh, FundAud), ProgramError> {
    let g = github(tok, key, now)?;
    if g.wf_file != b"fund.yml" { return Err(err(E_WORKFLOW)); }
    if (g.event != b"issue_comment" && g.event != b"issues") || !g.first_attempt { return Err(err(E_CLAIMS)); }
    let f = fund_aud(&g.aud, balance)?;
    Ok((g, f))
}

/// Fills a Balance that `open` just created, and creates its token account ["baltok", balance].
#[allow(clippy::too_many_arguments)]
fn new_balance<'a>(program_id: &Pubkey, payer: &AccountInfo<'a>, balance: &AccountInfo<'a>, baltok: &AccountInfo<'a>, mint: &AccountInfo<'a>,
                   auth: &AccountInfo<'a>, token: &AccountInfo<'a>, sys: &AccountInfo<'a>, m: &Mint, bump: u8, owner_id: u64, authority: &Pubkey,
                   faucet: bool, limits: &[u8]) -> ProgramResult {
    let (tk, tb) = baltok_key(program_id, balance.key);
    if *baltok.key != tk { return Err(err(E_ACCOUNTS)); }
    ensure_token_pda(payer, baltok, mint, auth, token, sys, m, &[b"baltok", balance.key.as_ref(), &[tb]])?;
    let mut d = balance.try_borrow_mut_data()?;
    d[B_VERSION] = 1; d[B_BUMP] = bump; d[B_FAUCET] = faucet as u8;
    put_u64(&mut d, B_OWNER_ID, owner_id); put_key(&mut d, B_AUTHORITY, authority); put_key(&mut d, B_MINT, mint.key);
    set_limits(&mut d, limits);
    msg!("knos2:balance owner={} authority={} mint={}", owner_id, b58(authority), b58(mint.key));
    Ok(())
}
/// `limits`: cap u64, spenders [u64; 4], as OpenBalance and SetBalance carry them.
fn set_limits(d: &mut [u8], limits: &[u8]) {
    d[B_CAP..B_CAP + 8].copy_from_slice(&limits[0..8]);
    d[B_SPENDERS..B_SPENDERS + 32].copy_from_slice(&limits[8..40]);
}

pub fn open_balance(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> ProgramResult {
    let [authority, balance, baltok, mint, auth, token, sys] = take(accounts)?;
    if data.len() != 48 { return Err(ProgramError::InvalidInstructionData); }
    let owner_id = u64_at(data, 0);
    if !authority.is_signer || !authority.is_writable || *auth.key != auth_key(program_id).0 { return Err(err(E_ACCOUNTS)); }
    if owner_id == 0 { return Err(err(E_TERMS)); }
    let m = mint_of(mint, token, true)?;
    let (new, bump) = open(program_id, authority, balance, sys, BALANCE_LEN, &[b"bal", &data[0..8], authority.key.as_ref(), mint.key.as_ref()], E_BALANCE)?;
    if !new { return Err(err(E_BALANCE)); }
    new_balance(program_id, authority, balance, baltok, mint, auth, token, sys, &m, bump, owner_id, authority.key, false, &data[8..48])
}

pub fn set_balance(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> ProgramResult {
    let [authority, balance] = take(accounts)?;
    if data.len() != 40 { return Err(ProgramError::InvalidInstructionData); }
    let b = load_balance(program_id, balance)?;
    if !authority.is_signer || *authority.key != b.authority { return Err(err(E_BALANCE)); }
    set_limits(&mut balance.try_borrow_mut_data()?, data);
    Ok(())
}

/// Unspent money goes back only to a token account of the wallet that opened the Balance. No pause applies.
pub fn withdraw(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> ProgramResult {
    let [authority, balance, baltok, dest, mint, auth, token] = take(accounts)?;
    if data.len() != 8 { return Err(ProgramError::InvalidInstructionData); }
    let b = load_balance(program_id, balance)?;
    // the faucet's test money never leaves through here (its authority is ["auth"], which signs no transaction either)
    if b.faucet { return Err(err(E_FAUCET)); }
    if !authority.is_signer || *authority.key != b.authority { return Err(err(E_BALANCE)); }
    let (ak, ab) = auth_key(program_id);
    if *auth.key != ak || *mint.key != b.mint || *baltok.key != baltok_key(program_id, balance.key).0 { return Err(err(E_ACCOUNTS)); }
    let m = mint_of(mint, token, false)?;
    let held = amount_of(baltok, token.key, E_ACCOUNTS)?;
    if !is_owned(dest, token.key, &b.mint, &b.authority) { return Err(err(E_PAYEE)); }
    let amount = match u64_at(data, 0) { 0 => held, a => a };
    if amount > held { return Err(err(E_FUNDS)); }
    transfer(token, baltok, mint, dest, auth, amount, m.decimals, Some(ab))?;
    msg!("knos2:withdrawn owner={} authority={} mint={} amount={}", b.owner_id, b58(&b.authority), b58(&b.mint), amount);
    Ok(())
}

/// What a new job is, beyond what its accounts say.
struct NewJob<'x> {
    repo: u64, issue: u64, amount: u64, work: i64, mode: u8, kind: u8, faucet: bool, funder_id: u64, owner_id: u64, not_before: i64,
    source: &'x Pubkey, refund_to: &'x Pubkey, terms: [u8; 32], wf_repo: &'x [u8], wf_sha: &'x [u8],
}
/// The accounts of an escrow. `auth_signs`: the money comes from a Balance's token account (["auth"] signs the
/// transfer); otherwise from a token account whose authority signed the transaction.
struct Escrow<'a, 'b> {
    payer: &'b AccountInfo<'a>, job: &'b AccountInfo<'a>, from: &'b AccountInfo<'a>, authority: &'b AccountInfo<'a>, vault: &'b AccountInfo<'a>,
    mint: &'b AccountInfo<'a>, auth: &'b AccountInfo<'a>, token: &'b AccountInfo<'a>, sys: &'b AccountInfo<'a>, auth_signs: bool,
}

/// Creates the job ["job", repo, issue, source] (it must not exist), moves the amount into the mint's vault
/// ["vault", mint] (created on first use) and credits the job with what the vault received: the open jobs of a mint
/// never add up to more than its vault holds.
fn escrow<'a>(program_id: &Pubkey, e: &Escrow<'a, '_>, m: &Mint, n: &NewJob, json: &[u8], now: i64) -> ProgramResult {
    let (ak, ab) = auth_key(program_id);
    let (vk, vb) = vault_key(program_id, e.mint.key);
    if *e.auth.key != ak || *e.vault.key != vk { return Err(err(E_ACCOUNTS)); }
    let (new, bump) = open(program_id, e.payer, e.job, e.sys, JOB_LEN, &[b"job", &n.repo.to_le_bytes(), &n.issue.to_le_bytes(), n.source.as_ref()], E_JOB)?;
    if !new { return Err(err(E_JOB)); }
    ensure_token_pda(e.payer, e.vault, e.mint, e.auth, e.token, e.sys, m, &[b"vault", e.mint.key.as_ref(), &[vb]])?;
    let before = amount_of(e.vault, e.token.key, E_ACCOUNTS)?;
    transfer(e.token, e.from, e.mint, e.vault, e.authority, n.amount, m.decimals, if e.auth_signs { Some(ab) } else { None })?;
    let got = amount_of(e.vault, e.token.key, E_ACCOUNTS)?.checked_sub(before).filter(|g| *g > 0).ok_or_else(|| err(E_TERMS))?;
    let mut d = e.job.try_borrow_mut_data()?;
    d[J_STATE] = OPEN; d[J_MODE] = n.mode; d[J_KIND] = n.kind; d[J_BUMP] = bump; d[J_TOKEN_PROGRAM] = m.t22 as u8; d[J_FAUCET] = n.faucet as u8;
    put_u64(&mut d, J_REPO, n.repo); put_u64(&mut d, J_ISSUE, n.issue); put_u64(&mut d, J_AMOUNT, got);
    put_i64(&mut d, J_DEADLINE, now.saturating_add(n.work)); put_u64(&mut d, J_FUNDER_ID, n.funder_id);
    put_i64(&mut d, J_NOT_BEFORE, n.not_before); put_u64(&mut d, J_OWNER_ID, n.owner_id);
    put_key(&mut d, J_SOURCE, n.source); put_key(&mut d, J_REFUND_TO, n.refund_to); put_key(&mut d, J_RENT_TO, e.payer.key);
    put_key(&mut d, J_MINT, e.mint.key);
    d[J_TERMS..J_TERMS + 32].copy_from_slice(&n.terms);
    d[J_WF_REPO..J_WF_REPO + 32].copy_from_slice(n.wf_repo);
    d[J_WF_SHA..J_WF_SHA + 40].copy_from_slice(n.wf_sha);
    msg!("knos2:funded repo={} issue={} amount={} mode={} by={} source={} faucet={}", n.repo, n.issue, got, n.mode, n.funder_id, b58(n.source), n.faucet as u8);
    msg!("knos2:terms {}", core::str::from_utf8(json).map_err(|_| err(E_TERMS))?);
    Ok(())
}

/// One comment funds a job from a Balance. Anyone may relay the token; what it can do is fixed by GitHub's signature.
pub fn fund_balance(program_id: &Pubkey, accounts: &[AccountInfo], json: &[u8], now: i64) -> ProgramResult {
    let [relayer, tok, key, balance, baltok, job, vault, mint, auth, token, sys, pause] = take(accounts)?;
    if !relayer.is_signer || !relayer.is_writable { return Err(err(E_ACCOUNTS)); }
    not_paused(program_id, pause, now)?;
    let b = load_balance(program_id, balance)?;
    if *mint.key != b.mint || *baltok.key != baltok_key(program_id, balance.key).0 { return Err(err(E_ACCOUNTS)); }
    let m = mint_of(mint, token, true)?;
    // the token names this Balance: the funder's workflow picked it, and a relayer cannot spend another one with it
    let (g, f) = fund_token(tok, key, balance.key, now)?;
    // the run was in a repository of the Balance's owner, and the comment is the owner's or a listed spender's. The
    // faucet's Balance is test money for any commenter the repository's own workflow lets through: GitHub's claims
    // do not say whether an owner is a person or an organisation, and nobody comments as an organisation.
    let may = b.faucet || g.actor_id == b.owner_id || b.spenders.contains(&g.actor_id);
    if g.owner_id != b.owner_id || g.actor_id == 0 || !may { return Err(err(E_SPENDER)); }
    if !terms_ok(f.amount, f.work, f.mode) || terms_hash(json)? != f.terms { return Err(err(E_TERMS)); }
    if b.cap != 0 && f.amount > b.cap { return Err(err(E_CAP)); }
    // a fund token works once: it names one Balance, and a Balance takes its tokens in the order GitHub issued them
    if g.iat <= b.last_iat { return Err(err(E_REPLAY)); }
    if amount_of(baltok, token.key, E_ACCOUNTS)? < f.amount { return Err(err(E_FUNDS)); }
    let faucet = b.faucet || (DEVNET && *mint.key == faucet_mint(program_id).0);
    escrow(program_id, &Escrow { payer: relayer, job, from: baltok, authority: auth, vault, mint, auth, token, sys, auth_signs: true }, &m,
           &NewJob { repo: g.repo_id, issue: f.issue, amount: f.amount, work: f.work, mode: f.mode, kind: 1, faucet, funder_id: g.actor_id,
                     owner_id: b.owner_id, not_before: g.iat, source: balance.key, refund_to: baltok.key, terms: f.terms,
                     wf_repo: &g.wf_repo, wf_sha: &g.wf_sha }, json, now)?;
    let mut d = balance.try_borrow_mut_data()?;
    put_i64(&mut d, B_LAST_IAT, g.iat);
    let spent = u64_at(&d, B_SPENT).saturating_add(f.amount);
    put_u64(&mut d, B_SPENT, spent);
    Ok(())
}

/// A wallet funds a job with its own money, naming the workflows that may prove it.
pub fn fund_wallet(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8], now: i64) -> ProgramResult {
    let [funder, job, funder_tok, vault, mint, auth, token, sys, pause] = take(accounts)?;
    if data.len() < 105 { return Err(ProgramError::InvalidInstructionData); }
    if !funder.is_signer || !funder.is_writable { return Err(err(E_ACCOUNTS)); }
    not_paused(program_id, pause, now)?;
    let m = mint_of(mint, token, true)?;
    let (repo, issue, amount, work, mode) = (u64_at(data, 0), u64_at(data, 8), u64_at(data, 16), i64_at(data, 24), data[32]);
    let (wf_repo, wf_sha, json) = (&data[33..65], &data[65..105], &data[105..]);
    if !terms_ok(amount, work, mode) || repo == 0 || !claims::is_hex(wf_sha, 40) { return Err(err(E_TERMS)); }
    let terms = terms_hash(json)?;
    let faucet = DEVNET && *mint.key == faucet_mint(program_id).0;
    escrow(program_id, &Escrow { payer: funder, job, from: funder_tok, authority: funder, vault, mint, auth, token, sys, auth_signs: false }, &m,
           &NewJob { repo, issue, amount, work, mode, kind: 0, faucet, funder_id: 0, owner_id: 0, not_before: now.saturating_sub(CLOCK_SLACK),
                     source: funder.key, refund_to: funder.key, terms, wf_repo, wf_sha }, json, now)
}

/// The guardian stops new funding (FundBalance, FundWallet) for at most PAUSE_MAX seconds from now; 0 lifts it. A
/// pause ends by itself; a longer one needs the guardian's signature again. Nothing else is in the guardian's reach.
pub fn pause(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8], now: i64) -> ProgramResult {
    let [guardian, payer, pause, sys] = take(accounts)?;
    if data.len() != 4 { return Err(ProgramError::InvalidInstructionData); }
    let seconds = u32_at(data, 0);
    if !guardian.is_signer || (*guardian.key != GUARDIAN && Some(*guardian.key) != TEST_GUARDIAN) { return Err(err(E_GUARDIAN)); }
    if seconds > PAUSE_MAX { return Err(err(E_TERMS)); }
    if !payer.is_signer || !payer.is_writable { return Err(err(E_ACCOUNTS)); }
    open(program_id, payer, pause, sys, PAUSE_LEN, &[b"pause"], E_ACCOUNTS)?;
    let until = if seconds == 0 { 0 } else { now.saturating_add(seconds as i64) };
    put_i64(&mut pause.try_borrow_mut_data()?, 0, until);
    msg!("knos2:paused until={}", until);
    Ok(())
}

pub fn init_faucet(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> ProgramResult {
    if !DEVNET { return Err(err(E_DEVNET)); }
    let [payer, mint, auth, token, sys] = take(accounts)?;
    let (mk, mb) = faucet_mint(program_id);
    if !data.is_empty() { return Err(ProgramError::InvalidInstructionData); }
    if !payer.is_signer || !payer.is_writable || *mint.key != mk || *auth.key != auth_key(program_id).0 || *token.key != TOKEN || !mint.data_is_empty() {
        return Err(err(E_ACCOUNTS));
    }
    init_mint(payer, mint, auth, token, sys, mb)
}

/// Devnet only. Mints the fund token's amount of test USDC into the faucet's Balance of the token's repository owner
/// (["bal", owner id, ["auth"], faucet mint], created here on first use), and only when that is the Balance the token
/// names: a token for a Balance of real money mints nothing. It does not use the token up: FundBalance does, when it
/// spends this Balance like any other.
pub fn faucet_open(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8], now: i64) -> ProgramResult {
    if !DEVNET { return Err(err(E_DEVNET)); }
    let [relayer, tok, key, balance, baltok, mint, auth, token, sys, rate] = take(accounts)?;
    if !data.is_empty() { return Err(ProgramError::InvalidInstructionData); }
    let (ak, ab) = auth_key(program_id);
    if !relayer.is_signer || !relayer.is_writable || *mint.key != faucet_mint(program_id).0 || *auth.key != ak { return Err(err(E_ACCOUNTS)); }
    let m = mint_of(mint, token, true)?;
    // the token names the account passed as the Balance; `open` below takes that account only at the address of the
    // repository owner's faucet Balance. So the audience's Balance is that one, and no other.
    let (g, f) = fund_token(tok, key, balance.key, now)?;
    if !terms_ok(f.amount, f.work, f.mode) || f.amount > FAUCET_CAP { return Err(err(E_TERMS)); }
    // one use per repository per FUND_PERIOD, in the order GitHub issued the tokens: a token seen in public cannot
    // mint a second time
    open(program_id, relayer, rate, sys, RATE_LEN, &[b"rate", &g.repo_id.to_le_bytes()], E_ACCOUNTS)?;
    {
        let mut d = rate.try_borrow_mut_data()?;
        if now < i64_at(&d, 0).saturating_add(FUND_PERIOD) || g.iat <= i64_at(&d, 8) { return Err(err(E_RATE)); }
        put_i64(&mut d, 0, now); put_i64(&mut d, 8, g.iat);
    }
    let (new, bump) = open(program_id, relayer, balance, sys, BALANCE_LEN, &[b"bal", &g.owner_id.to_le_bytes(), ak.as_ref(), mint.key.as_ref()], E_ACCOUNTS)?;
    if new {
        new_balance(program_id, relayer, balance, baltok, mint, auth, token, sys, &m, bump, g.owner_id, &ak, true, &[0u8; 40])?;
    } else if *baltok.key != baltok_key(program_id, balance.key).0 {
        return Err(err(E_ACCOUNTS));
    }
    mint_to(token, mint, baltok, auth, f.amount, ab)
}
