//! knos-oidc, second deployment: OIDC on Solana. Verifies an RS256 token from GitHub Actions or GitLab CI on chain,
//! so that any program can require a fact the issuer signed ("this workflow, at this commit, ran in this repository
//! and said X") with no oracle.
//!
//! What it trusts is in pins.rs: GitHub's four keys of 2 Oct 2026, and after them only a key that GitHub's own
//! signature names (RegisterKey). Such a key waits a day and the guardian's approval before it verifies anything,
//! and every key expires 30 days after it was registered or last named by GitHub's signature (Refresh). That
//! signature is itself verified here under one of GitHub's keys, so one of them has to be refreshed in every 30
//! days: once the last one has expired nothing can be attested, and nothing verifies.
//!
//! Who can change what:
//!   - Anyone: write, verify and close their own token accounts; register or refresh a key that an attestation
//!     names; send a key's Montgomery constants (they are checked here).
//!   - The guardian (pins::GUARDIAN, a multisig vault) can approve a key that an attestation admitted and can
//!     revoke any key, for ever. It cannot add a key (that takes GitHub's signature), cannot change a delay or an
//!     expiry, cannot undo a revocation, and has no instruction that touches a token account or moves anything
//!     else. So on its own it can stop tokens from verifying, and it cannot make this program accept a token the
//!     issuer did not sign.
//!   - The upgrade authority (a second multisig vault): the program is upgradeable only through that multisig, with
//!     a public 48-hour delay, until an outside review. After the review the upgrade authority is removed.
//!
//! Instructions (first byte is the tag):
//!   0 Write        payer(s,w) token(w) system            data: id[32] total u16 offset u16 chunk
//!                  Writes the token (the compact JWT) into the PDA ["tok", payer, id]; created on first use, sized
//!                  for `total` bytes (at most MAX_JWT). Chunks may be sent in any order, several per transaction.
//!   1 Step         payer(s) token(w) key                 data: id[32] squarings u8
//!                  Computes s^65537 mod n in steps: the first call puts the signature in Montgomery form, each call
//!                  does up to `squarings` of the 16 squarings, and the call that finishes them checks the PKCS#1
//!                  v1.5 SHA-256 encoding against sha256(header.payload), that alg is RS256 and that `iss` is the
//!                  key's issuer, then decodes the payload in place and marks the account VERIFIED.
//!                  Every call refuses a key that is not usable now: not ready, revoked, neither a genesis key nor
//!                  approved, before its active_at, or at or past its expires_at.
//!                  The finishing call refuses a token whose `exp` is more than AHEAD (a day) ahead of the clock.
//!                  2048-bit keys: Step(8), Step(8). 4096-bit keys: Step(2), Step(3) three times, Step(4), Step(1):
//!                  the finishing call also hashes and decodes the whole token, so it is given one squaring only.
//!   2 Close        payer(s,w) token(w)                   data: id[32]        (the rent goes back to the payer)
//!   3 RegisterKey  payer(s,w) key(w) system [attest]     data: issuer u8, n (256 or 512 bytes, big-endian)
//!                  Creates ["key", [issuer], sha256(n)]. A hash in GENESIS: usable at once (after KeyParams), until
//!                  now + KEY_TTL. Any other hash needs `attest`: a VERIFIED GitHub token, at most an hour past its
//!                  expiry, of the pinned rotate workflow (job_workflow_ref, job_workflow_sha), run on a
//!                  GitHub-hosted runner by the schedule or by hand (event_name) in one of the attester's
//!                  repositories (repository_owner_id, repository_id), whose audience is
//!                  "knos-oidc:key:<issuer>:<sha256(n) hex>". That key verifies from now + KEY_DELAY, and only
//!                  once the guardian has approved it; it expires KEY_TTL after that time. Anyone may send it.
//!   4 KeyParams    payer(s) key(w)                       data: n0inv u32, r2 (as long as n, big-endian)
//!                  The key's Montgomery constants, checked on chain; the key is ready after this. Anyone may send it.
//!   5 Refresh      payer(s) key(w) attest
//!                  The same attestation, for a key that exists and is not revoked: its expiry becomes
//!                  now + KEY_TTL if that is later. It never moves an expiry earlier. Anyone may send it.
//!   6 Approve      guardian(s) key(w)
//!                  Marks a key approved. Refused for a revoked key.
//!   7 Revoke       guardian(s) key(w)
//!                  Marks a key revoked, for ever: no instruction clears the mark, and the account stays, so the
//!                  same modulus can never be registered for that issuer again.
//!
//! A consumer program takes the token account, checks that its owner is this program, and calls `verified(&data)`
//! for the issuer, the expiry and the decoded payload, then reads claims with `claims::fields`. Replay protection
//! (and the expiry check against the clock, `fresh`) is the consumer's: the same token can be verified into any
//! number of accounts. A token account that is VERIFIED stays so if its key is revoked or expires afterwards.
//! `fresh` bounds that to an hour past the token's own expiry, and Step bounds that expiry to AHEAD past the moment
//! the key was last asked about: whatever expiry its signer wrote, no token account passes `fresh` more than
//! AHEAD + LATE (25 hours) after its key stopped being usable. RegisterKey and Refresh rely on exactly that for their
//! attestation. A consumer that wants no such tail takes the key account the token names (T_KEY) and asks
//! `key_usable` itself.
pub mod claims;
pub mod pins;
pub mod rsa;

use claims::{err, fields, number, text};
use solana_program::{
    account_info::{next_account_info, AccountInfo},
    clock::Clock,
    entrypoint::ProgramResult,
    hash::hashv,
    program::invoke_signed,
    program_error::ProgramError,
    pubkey::Pubkey,
    rent::Rent,
    system_instruction, system_program,
    sysvar::Sysvar,
};

pub const MAX_JWT: usize = 8192;
/// How long after its `exp` a token is still accepted, here and by consumers that call `fresh`. The issuers' tokens
/// live five minutes, which is shorter than it can take a public relay to find one and land several transactions.
/// A token is not a bearer secret in this design: its audience names one action, and every consumer keeps its own
/// replay guard, so an hour of lateness gives a reader of the token nothing but the gas bill.
pub const LATE: i64 = 3600;
pub fn fresh(exp: i64, now: i64) -> bool { now < exp.saturating_add(LATE) }
/// How far ahead of the clock a token's `exp` may be when Step finishes. `exp` is whatever the holder of the key
/// signed, and a key is asked about while Step runs and never again. Without this bound the holder of a leaked key
/// could verify a token that expires in a hundred years, and the account would pass `fresh` for a hundred years after
/// the guardian revoked the key: as an attestation it would refresh and register keys all that time. With it, a
/// token account is of no use AHEAD + LATE after its last Step. An issuer's own token is far inside the bound.
pub const AHEAD: i64 = 86_400;
// token account: stage u8 (0 writing, 1 stepping, 2 VERIFIED), issuer u8, squarings done u8, limbs u8, jwt_len u16,
// payload_off u16, payload_len u16, exp i64, key[32], payer[32], id[32], x (the running power, 512 bytes), jwt
pub const T_STAGE: usize = 0;
pub const T_ISSUER: usize = 1;
pub const T_DONE: usize = 2;
pub const T_LIMBS: usize = 3;
pub const T_LEN: usize = 4;
pub const T_POFF: usize = 6;
pub const T_PLEN: usize = 8;
pub const T_EXP: usize = 10;
pub const T_KEY: usize = 18;
pub const T_PAYER: usize = 50;
pub const T_ID: usize = 82;
pub const T_X: usize = 114;
pub const T_JWT: usize = 626;
pub const VERIFIED: u8 = 2;
// key account: state u8 (0 created, 1 ready: Montgomery constants checked), issuer u8, limbs u8, bump u8, n0inv u32,
// active_at i64, expires_at i64, flags u8, 15 zero bytes, then n (limbs, LE) and r2 (limbs, LE).
// Both kinds of account are owned by this program. They are told apart by their first and third bytes: a key's state
// is never 2, so `verified` never reads a key account as a token; a token's third byte counts squarings (at most
// 16) where a key's holds its limb count (64 or 128), so a token account is never read as a key.
pub const K_STATE: usize = 0;
pub const K_ISSUER: usize = 1;
pub const K_LIMBS: usize = 2;
pub const K_BUMP: usize = 3;
pub const K_N0INV: usize = 4;
pub const K_ACTIVE: usize = 8;
pub const K_EXPIRES: usize = 16;
pub const K_FLAGS: usize = 24;
pub const K_HDR: usize = 40;
pub const F_APPROVED: u8 = 1;
pub const F_REVOKED: u8 = 2;
pub const F_GENESIS: u8 = 4;

// errors: 60-63 in claims.rs (63 also from Step: `exp` more than AHEAD ahead of the clock); 64 token too long or
// chunk out of range; 65 signature length or range; 66 key parameters; 67 accounts; 68 not a key of this program
// (for Step: not a ready one, or not the one the token started with); 69 wrong stage; 70 bad signature; 71 alg is
// not RS256; 72 issuer; 73 key not in genesis and no valid attestation; 74 attestation claims; 75 attestation
// expired; 76 key not active yet (its delay has not passed, or the guardian has not approved it); 77 key expired;
// 78 key revoked; 79 not the guardian
pub const E_LEN: u32 = 64;
pub const E_SIG: u32 = 65;
pub const E_PARAMS: u32 = 66;
pub const E_ACCOUNTS: u32 = 67;
pub const E_KEY: u32 = 68;
pub const E_STAGE: u32 = 69;
pub const E_BADSIG: u32 = 70;
pub const E_ALG: u32 = 71;
pub const E_ISS: u32 = 72;
pub const E_UNTRUSTED: u32 = 73;
pub const E_ATTEST: u32 = 74;
pub const E_EXPIRED: u32 = 75;
pub const E_INACTIVE: u32 = 76;
pub const E_KEY_EXPIRED: u32 = 77;
pub const E_REVOKED: u32 = 78;
pub const E_GUARDIAN: u32 = 79;

#[cfg(not(feature = "no-entrypoint"))]
solana_program::entrypoint!(process);

#[cfg(not(feature = "no-entrypoint"))]
solana_security_txt::security_txt! {
    name: "knos-oidc",
    project_url: "https://github.com/drexthealpha/Knos",
    contacts: "link:https://github.com/drexthealpha/Knos/security/advisories/new",
    policy: "https://github.com/drexthealpha/Knos/blob/main/SECURITY.md",
    source_code: "https://github.com/drexthealpha/Knos"
}

/// What a consumer reads from a token account it has checked is owned by this program: None unless VERIFIED.
pub struct Verified<'a> { pub issuer: u8, pub exp: i64, pub payload: &'a [u8] }
pub fn verified(d: &[u8]) -> Option<Verified<'_>> {
    if d.len() < T_JWT || d[T_STAGE] != VERIFIED { return None; }
    let off = u16::from_le_bytes([d[T_POFF], d[T_POFF + 1]]) as usize;
    let len = u16::from_le_bytes([d[T_PLEN], d[T_PLEN + 1]]) as usize;
    let payload = d.get(off..off + len)?;
    Some(Verified { issuer: d[T_ISSUER], exp: i64::from_le_bytes(d[T_EXP..T_EXP + 8].try_into().ok()?), payload })
}

/// Whether a ready key may verify at `now`, from its flags and times: Ok, or the error code Step returns. A revoked
/// key is refused whatever else is true of it; then a key that is not active yet; then one that has expired.
pub fn key_usable(flags: u8, active_at: i64, expires_at: i64, now: i64) -> Result<(), u32> {
    if flags & F_REVOKED != 0 { return Err(E_REVOKED); }
    if flags & (F_GENESIS | F_APPROVED) == 0 || now < active_at { return Err(E_INACTIVE); }
    if now >= expires_at { return Err(E_KEY_EXPIRED); }
    Ok(())
}

fn i64_at(d: &[u8], at: usize) -> i64 { i64::from_le_bytes(d[at..at + 8].try_into().unwrap()) }
fn put_i64(d: &mut [u8], at: usize, v: i64) { d[at..at + 8].copy_from_slice(&v.to_le_bytes()); }

fn create_pda<'a>(payer: &AccountInfo<'a>, acct: &AccountInfo<'a>, sys: &AccountInfo<'a>, program_id: &Pubkey,
                  space: usize, seeds: &[&[u8]]) -> ProgramResult {
    let need = Rent::get()?.minimum_balance(space);
    // an address can be sent lamports before it exists: top up, allocate and assign instead of create_account
    let have = acct.lamports();
    if have < need {
        solana_program::program::invoke(&system_instruction::transfer(payer.key, acct.key, need - have), &[payer.clone(), acct.clone(), sys.clone()])?;
    }
    invoke_signed(&system_instruction::allocate(acct.key, space as u64), &[acct.clone(), sys.clone()], &[seeds])?;
    invoke_signed(&system_instruction::assign(acct.key, program_id), &[acct.clone(), sys.clone()], &[seeds])
}

/// The limb count of a key account of this program, created or ready; E_KEY for any other account.
fn key_limbs(program_id: &Pubkey, key: &AccountInfo) -> Result<usize, ProgramError> {
    if key.owner != program_id { return Err(err(E_KEY)); }
    let d = key.try_borrow_data()?;
    let l = if d.len() >= K_HDR { d[K_LIMBS] as usize } else { 0 };
    if (l != 64 && l != 128) || d.len() != K_HDR + 8 * l { return Err(err(E_KEY)); }
    Ok(l)
}

struct Key { issuer: u8, l: usize, n0inv: u32, n: Vec<u32>, r2: Vec<u32> }
/// A ready key of this program that may verify at `now`.
fn load_key(program_id: &Pubkey, key: &AccountInfo, now: i64) -> Result<Key, ProgramError> {
    let l = key_limbs(program_id, key)?;
    let d = key.try_borrow_data()?;
    if d[K_STATE] != 1 { return Err(err(E_KEY)); }
    key_usable(d[K_FLAGS], i64_at(&d, K_ACTIVE), i64_at(&d, K_EXPIRES), now).map_err(err)?;
    let (mut n, mut r2) = (vec![0u32; l], vec![0u32; l]);
    rsa::load(&d[K_HDR..], &mut n);
    rsa::load(&d[K_HDR + 4 * l..], &mut r2);
    Ok(Key { issuer: d[K_ISSUER], l, n0inv: u32::from_le_bytes(d[K_N0INV..K_N0INV + 4].try_into().unwrap()), n, r2 })
}

/// GitHub's own signature names the key (issuer, kh): `attest` is a VERIFIED, fresh GitHub token of the pinned
/// rotate workflow, run on a GitHub-hosted runner by the schedule or by hand in one of the attester's repositories,
/// whose audience is "knos-oidc:key:<issuer>:<kh hex>". What the workflow's own code did is as trustworthy as the
/// account it ran in, which is why the account and the repository are part of the rule and not only the file.
#[inline(never)]
fn attested(program_id: &Pubkey, attest: &AccountInfo, issuer: u8, kh: &[u8; 32], now: i64) -> ProgramResult {
    if attest.owner != program_id { return Err(err(E_UNTRUSTED)); }
    let ad = attest.try_borrow_data()?;
    let v = verified(&ad).ok_or_else(|| err(E_UNTRUSTED))?;
    if v.issuer != pins::ISSUER_GITHUB { return Err(err(E_ATTEST)); }
    if !fresh(v.exp, now) { return Err(err(E_EXPIRED)); }
    let [wref, wsha, runner, owner, repo, event, aud] = fields(v.payload, [b"job_workflow_ref", b"job_workflow_sha",
        b"runner_environment", b"repository_owner_id", b"repository_id", b"event_name", b"aud"])?;
    let (wref, wsha, event) = (text(wref)?, text(wsha)?, text(event)?);
    let sha_ok = wsha == pins::ROTATE_SHA || pins::TEST_ROTATE_SHA.is_some_and(|t| wsha == t);
    if !wref.starts_with(pins::ROTATE_REF) || !sha_ok || text(runner)? != b"github-hosted" { return Err(err(E_ATTEST)); }
    if !pins::attester(number(owner)?, number(repo)?) || !pins::ATTEST_EVENTS.iter().any(|e| *e == &event[..]) { return Err(err(E_ATTEST)); }
    let mut want = Vec::with_capacity(80);
    want.extend_from_slice(b"knos-oidc:key:");
    want.push(b'0' + issuer);
    want.push(b':');
    let mut hx = [0u8; 64];
    claims::hex_into(kh, &mut hx);
    want.extend_from_slice(&hx);
    if text(aud)? != want { return Err(err(E_ATTEST)); }
    Ok(())
}

fn token_account(program_id: &Pubkey, payer: &Pubkey, id: &[u8], tok: &AccountInfo) -> Result<u8, ProgramError> {
    let (k, bump) = Pubkey::find_program_address(&[b"tok", payer.as_ref(), id], program_id);
    if *tok.key != k { return Err(err(E_ACCOUNTS)); }
    Ok(bump)
}

pub fn process(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> ProgramResult {
    let (&tag, rest) = data.split_first().ok_or(ProgramError::InvalidInstructionData)?;
    let it = &mut accounts.iter();
    match tag {
        0 => {
            let payer = next_account_info(it)?; let tok = next_account_info(it)?; let sys = next_account_info(it)?;
            if rest.len() < 36 { return Err(ProgramError::InvalidInstructionData); }
            let id = &rest[0..32];
            let total = u16::from_le_bytes([rest[32], rest[33]]) as usize;
            let off = u16::from_le_bytes([rest[34], rest[35]]) as usize;
            let chunk = &rest[36..];
            if total == 0 || total > MAX_JWT || off + chunk.len() > total { return Err(err(E_LEN)); }
            if !payer.is_signer || !payer.is_writable || *sys.key != system_program::ID { return Err(err(E_ACCOUNTS)); }
            let bump = token_account(program_id, payer.key, id, tok)?;
            if tok.owner != program_id {
                if !tok.data_is_empty() || *tok.owner != system_program::ID { return Err(err(E_ACCOUNTS)); }
                create_pda(payer, tok, sys, program_id, T_JWT + total, &[b"tok", payer.key.as_ref(), id, &[bump]])?;
                let mut d = tok.try_borrow_mut_data()?;
                d[T_LEN..T_LEN + 2].copy_from_slice(&(total as u16).to_le_bytes());
                d[T_PAYER..T_PAYER + 32].copy_from_slice(payer.key.as_ref());
                d[T_ID..T_ID + 32].copy_from_slice(id);
            }
            let mut d = tok.try_borrow_mut_data()?;
            if d.len() != T_JWT + total || d[T_STAGE] != 0 { return Err(err(E_STAGE)); }
            d[T_JWT + off..T_JWT + off + chunk.len()].copy_from_slice(chunk);
            Ok(())
        }
        1 => {
            let payer = next_account_info(it)?; let tok = next_account_info(it)?; let key = next_account_info(it)?;
            if rest.len() != 33 { return Err(ProgramError::InvalidInstructionData); }
            if !payer.is_signer { return Err(err(E_ACCOUNTS)); }
            token_account(program_id, payer.key, &rest[0..32], tok)?;
            if tok.owner != program_id { return Err(err(E_ACCOUNTS)); }
            // every call asks again whether the key may be used: a key revoked or expired between two calls stops here
            let now = Clock::get()?.unix_timestamp;
            let k = load_key(program_id, key, now)?;
            let l = k.l;
            let mut d = tok.try_borrow_mut_data()?;
            let len = u16::from_le_bytes([d[T_LEN], d[T_LEN + 1]]) as usize;
            if d.len() != T_JWT + len || d[T_STAGE] == VERIFIED { return Err(err(E_STAGE)); }
            let [hp, pp, sp] = claims::split_jwt(&d[T_JWT..T_JWT + len])?;
            // the signature: exactly as long as the modulus, and below it
            let mut sig = vec![0u8; 4 * l];
            if claims::b64_len(sp.1 - sp.0) != Some(4 * l) { return Err(err(E_SIG)); }
            claims::b64url_into(&d[T_JWT + sp.0..T_JWT + sp.1], &mut sig)?;
            let mut s = vec![0u32; l];
            rsa::be_to_limbs(&sig, &mut s);
            if rsa::geq(&s, &k.n) { return Err(err(E_SIG)); }
            let mut t = vec![0u32; 2 * l + 2];
            let (mut x, mut y) = (vec![0u32; l], vec![0u32; l]);
            if d[T_STAGE] == 0 {
                rsa::mont_mul(&mut x, &s, &k.r2, &k.n, k.n0inv, &mut t); // s in Montgomery form
                d[T_STAGE] = 1; d[T_DONE] = 0; d[T_ISSUER] = k.issuer; d[T_LIMBS] = l as u8;
                d[T_KEY..T_KEY + 32].copy_from_slice(key.key.as_ref());
            } else {
                if d[T_KEY..T_KEY + 32] != key.key.as_ref()[..] { return Err(err(E_KEY)); }
                rsa::load(&d[T_X..], &mut x);
            }
            let todo = (rest[32] as usize).min(16 - d[T_DONE] as usize);
            for _ in 0..todo {
                rsa::mont_sqr(&mut y, &x, &k.n, k.n0inv, &mut t);
                core::mem::swap(&mut x, &mut y);
            }
            d[T_DONE] += todo as u8;
            rsa::store(&mut d[T_X..], &x);
            if d[T_DONE] < 16 { return Ok(()); }
            // x = (s^65536) in Montgomery form; times s (plain) gives s^65537
            rsa::mont_mul(&mut y, &x, &s, &k.n, k.n0inv, &mut t);
            rsa::limbs_to_be(&y, &mut sig);
            let digest = hashv(&[&d[T_JWT..T_JWT + pp.1]]).to_bytes();
            if !rsa::pkcs1_sha256_ok(&sig, &digest) { return Err(err(E_BADSIG)); }
            let hl = claims::b64_len(hp.1 - hp.0).ok_or_else(|| err(claims::E_B64))?;
            let mut header = vec![0u8; hl];
            claims::b64url_into(&d[T_JWT + hp.0..T_JWT + hp.1], &mut header)?;
            let [alg] = fields(&header, [b"alg"])?;
            if text(alg)? != b"RS256" { return Err(err(E_ALG)); }
            let poff = T_JWT + pp.0;
            let plen = claims::b64url_in_place(&mut d, poff, pp.1 - pp.0)?;
            let exp = {
                let [iss, exp] = fields(&d[poff..poff + plen], [b"iss", b"exp"])?;
                if text(iss)? != pins::ISSUERS[k.issuer as usize] { return Err(err(E_ISS)); }
                match exp { Some(r) if !r.is_str => claims::parse_u64(r.bytes).ok_or_else(|| err(claims::E_CLAIM))? as i64, _ => return Err(err(claims::E_CLAIM)) }
            };
            // the key was usable a moment ago (load_key): a token verified now is fresh for at most AHEAD + LATE more
            if exp > now.saturating_add(AHEAD) { return Err(err(claims::E_CLAIM)); }
            d[T_POFF..T_POFF + 2].copy_from_slice(&(poff as u16).to_le_bytes());
            d[T_PLEN..T_PLEN + 2].copy_from_slice(&(plen as u16).to_le_bytes());
            d[T_EXP..T_EXP + 8].copy_from_slice(&exp.to_le_bytes());
            d[T_STAGE] = VERIFIED;
            Ok(())
        }
        2 => {
            let payer = next_account_info(it)?; let tok = next_account_info(it)?;
            if rest.len() != 32 || !payer.is_signer || !payer.is_writable { return Err(err(E_ACCOUNTS)); }
            token_account(program_id, payer.key, rest, tok)?;
            if tok.owner != program_id { return Err(err(E_ACCOUNTS)); }
            **payer.try_borrow_mut_lamports()? = payer.lamports().checked_add(tok.lamports()).ok_or(ProgramError::ArithmeticOverflow)?;
            **tok.try_borrow_mut_lamports()? = 0;
            tok.resize(0)?;
            tok.assign(&system_program::ID);
            Ok(())
        }
        3 => {
            let payer = next_account_info(it)?; let key = next_account_info(it)?; let sys = next_account_info(it)?;
            if rest.len() != 1 + 256 && rest.len() != 1 + 512 { return Err(ProgramError::InvalidInstructionData); }
            let (issuer, n_be) = (rest[0], &rest[1..]);
            if issuer as usize >= pins::ISSUERS.len() { return Err(err(E_ISS)); }
            if !payer.is_signer || !payer.is_writable || *sys.key != system_program::ID { return Err(err(E_ACCOUNTS)); }
            let l = n_be.len() / 4;
            let kh = hashv(&[n_be]).to_bytes();
            let (kk, bump) = Pubkey::find_program_address(&[b"key", &[issuer], &kh], program_id);
            // a key account is never closed, so a hash that was registered once (and perhaps revoked) stops here
            if *key.key != kk || !key.data_is_empty() || *key.owner != system_program::ID { return Err(err(E_ACCOUNTS)); }
            let now = Clock::get()?.unix_timestamp;
            let genesis = pins::GENESIS.iter().chain(pins::TEST_GENESIS.iter()).any(|(i, g)| *i == issuer && *g == kh);
            if !genesis {
                let attest = next_account_info(it).map_err(|_| err(E_UNTRUSTED))?;
                attested(program_id, attest, issuer, &kh, now)?;
            }
            let mut n = vec![0u32; l];
            rsa::be_to_limbs(n_be, &mut n);
            if n[0] & 1 == 0 || n[l - 1] >> 31 == 0 { return Err(err(E_PARAMS)); }
            create_pda(payer, key, sys, program_id, K_HDR + 8 * l, &[b"key", &[issuer], &kh, &[bump]])?;
            // a genesis key is the root and works at once; a key GitHub's signature admitted waits, and needs approval
            let (active, flags) = if genesis { (now, F_GENESIS | F_APPROVED) } else { (now.saturating_add(pins::KEY_DELAY), 0) };
            let mut d = key.try_borrow_mut_data()?;
            d[K_ISSUER] = issuer; d[K_LIMBS] = l as u8; d[K_BUMP] = bump; d[K_FLAGS] = flags;
            put_i64(&mut d, K_ACTIVE, active);
            put_i64(&mut d, K_EXPIRES, active.saturating_add(pins::KEY_TTL));
            rsa::store(&mut d[K_HDR..], &n);
            Ok(())
        }
        4 => {
            let payer = next_account_info(it)?; let key = next_account_info(it)?;
            if !payer.is_signer || key.owner != program_id { return Err(err(E_ACCOUNTS)); }
            let mut d = key.try_borrow_mut_data()?;
            if d.len() < K_HDR || d[K_STATE] != 0 { return Err(err(E_STAGE)); }
            let l = d[K_LIMBS] as usize;
            if d.len() != K_HDR + 8 * l || rest.len() != 4 + 4 * l { return Err(ProgramError::InvalidInstructionData); }
            let n0inv = u32::from_le_bytes(rest[0..4].try_into().unwrap());
            let (mut n, mut r2) = (vec![0u32; l], vec![0u32; l]);
            rsa::load(&d[K_HDR..], &mut n);
            rsa::be_to_limbs(&rest[4..], &mut r2);
            let (mut a, mut b, mut t) = (vec![0u32; l], vec![0u32; l], vec![0u32; l + 2]);
            if !rsa::params_ok(&n, &r2, n0inv, &mut a, &mut b, &mut t) { return Err(err(E_PARAMS)); }
            d[K_N0INV..K_N0INV + 4].copy_from_slice(&n0inv.to_le_bytes());
            rsa::store(&mut d[K_HDR + 4 * l..], &r2);
            d[K_STATE] = 1;
            Ok(())
        }
        5 => {
            let payer = next_account_info(it)?; let key = next_account_info(it)?; let attest = next_account_info(it)?;
            if !rest.is_empty() { return Err(ProgramError::InvalidInstructionData); }
            if !payer.is_signer { return Err(err(E_ACCOUNTS)); }
            let l = key_limbs(program_id, key)?;
            let now = Clock::get()?.unix_timestamp;
            // the hash GitHub must have named is the hash of the modulus this account holds
            let (issuer, kh) = {
                let d = key.try_borrow_data()?;
                if d[K_FLAGS] & F_REVOKED != 0 { return Err(err(E_REVOKED)); }
                let (mut n, mut n_be) = (vec![0u32; l], vec![0u8; 4 * l]);
                rsa::load(&d[K_HDR..], &mut n);
                rsa::limbs_to_be(&n, &mut n_be);
                (d[K_ISSUER], hashv(&[&n_be]).to_bytes())
            };
            attested(program_id, attest, issuer, &kh, now)?;
            let mut d = key.try_borrow_mut_data()?;
            let expires = i64_at(&d, K_EXPIRES).max(now.saturating_add(pins::KEY_TTL));
            put_i64(&mut d, K_EXPIRES, expires);
            Ok(())
        }
        6 => {
            let guardian = next_account_info(it)?; let key = next_account_info(it)?;
            if !rest.is_empty() { return Err(ProgramError::InvalidInstructionData); }
            if !guardian.is_signer || !pins::is_guardian(guardian.key) { return Err(err(E_GUARDIAN)); }
            key_limbs(program_id, key)?;
            let mut d = key.try_borrow_mut_data()?;
            if d[K_FLAGS] & F_REVOKED != 0 { return Err(err(E_REVOKED)); }
            d[K_FLAGS] |= F_APPROVED;
            Ok(())
        }
        7 => {
            let guardian = next_account_info(it)?; let key = next_account_info(it)?;
            if !rest.is_empty() { return Err(ProgramError::InvalidInstructionData); }
            if !guardian.is_signer || !pins::is_guardian(guardian.key) { return Err(err(E_GUARDIAN)); }
            key_limbs(program_id, key)?;
            let mut d = key.try_borrow_mut_data()?;
            d[K_FLAGS] |= F_REVOKED;
            Ok(())
        }
        _ => Err(ProgramError::InvalidInstructionData),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_key_is_usable_only_when_approved_or_genesis_active_unexpired_and_not_revoked() {
        let (active, expires) = (1_000, 2_000);
        for ok in [F_GENESIS | F_APPROVED, F_APPROVED, F_GENESIS] {
            assert_eq!(key_usable(ok, active, expires, 999), Err(E_INACTIVE));
            assert_eq!(key_usable(ok, active, expires, 1_000), Ok(()));
            assert_eq!(key_usable(ok, active, expires, 1_999), Ok(()));
            assert_eq!(key_usable(ok, active, expires, 2_000), Err(E_KEY_EXPIRED));
            assert_eq!(key_usable(ok, active, expires, i64::MAX), Err(E_KEY_EXPIRED));
        }
        // attested and waiting for the guardian: never, however much time passes
        for now in [0, 999, 1_000, 1_999, 2_000, i64::MAX] { assert_eq!(key_usable(0, active, expires, now), Err(E_INACTIVE)); }
        // revoked wins over everything
        for flags in 0..8u8 {
            for now in [0, 1_500, 5_000] {
                if flags & F_REVOKED != 0 { assert_eq!(key_usable(flags, active, expires, now), Err(E_REVOKED)); }
            }
        }
    }

    #[test]
    fn a_token_verified_while_its_key_was_usable_is_fresh_for_at_most_25_hours_more() {
        // Step accepts exp <= now + AHEAD at a moment `at` when the key is usable; `fresh` then holds until exp + LATE
        for at in [0i64, 1_790_000_000, i64::MAX - AHEAD - LATE] {
            let exp = at + AHEAD; // the furthest expiry Step lets through
            assert!(fresh(exp, at + AHEAD + LATE - 1) && !fresh(exp, at + AHEAD + LATE));
        }
        assert_eq!(AHEAD + LATE, 25 * 3600);
        assert!(fresh(i64::MAX, i64::MAX - 1)); // no overflow at the end of time
    }

    #[test]
    fn a_key_header_can_never_read_as_a_verified_token() {
        // a 4096-bit key account is longer than a token's header; its state byte is 0 or 1, never VERIFIED
        for state in [0u8, 1] {
            let mut d = vec![0xffu8; K_HDR + 8 * 128];
            d[K_STATE] = state;
            assert!(d.len() >= T_JWT && verified(&d).is_none());
        }
    }

    #[test]
    fn the_attester_is_the_pinned_account_and_its_two_repositories() {
        for repo in pins::ATTEST_REPO_IDS { assert!(pins::attester(pins::ATTEST_OWNER_ID, repo)); }
        assert!(!pins::attester(pins::ATTEST_OWNER_ID, 1) && !pins::attester(1, pins::ATTEST_REPO_IDS[0]) && !pins::attester(0, 0));
        assert!(pins::is_guardian(&pins::GUARDIAN) && !pins::is_guardian(&Pubkey::default()));
        // the test values exist only in a testkeys build
        assert_eq!(pins::attester(424_242, 987_654_321), cfg!(feature = "testkeys"));
        assert_eq!(pins::TEST_GUARDIAN.is_some(), cfg!(feature = "testkeys"));
        assert_eq!(pins::GENESIS.len(), 4);
        assert!(pins::GENESIS.iter().all(|(issuer, _)| *issuer == pins::ISSUER_GITHUB));
    }
}
