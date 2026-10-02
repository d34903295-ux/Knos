//! 0.3.7: a GitHub Actions OIDC token, verified on chain. A "github" job (PostGithub) names a repository and a ref
//! (their sha256). Its worker proves the work by a token GitHub signed (RS256) for a run of the pinned prove.yml
//! workflow on that repository and ref, with audience "knos:<job id hex>". The RSA-2048 check (s^65537 mod n) runs in
//! two transactions (VerifyStep1, VerifyStep2) against a key the admin registered from GitHub's JWKS (RegisterKey);
//! the token is written to a proof buffer first (BufferWrite). On a valid proof the worker is paid (price - fee +
//! stake back), the job and the buffer close. With no proof by the deadline, Refund / Settle refund the buyer.
//!
//!   15 RegisterKey  admin(s,w) config2 key(w) system
//!                   data: kid_len u8, kid, n[256] (big-endian), r2[256] (R^2 mod n, big-endian), n0inv u32
//!   16 BufferWrite  prover(s,w) buffer(w) system           data: job_id[32] total_len u16 offset u16 chunk
//!   17 VerifyStep1  prover(s) buffer(w) key                data: job_id[32]
//!   18 VerifyStep2  prover(s,w) buffer(w) key job(w) vault_token(w) vault_auth worker_token(w) fee_token(w)
//!                   buyer(w) config2 token                 data: job_id[32]
//!   19 PostGithub   buyer(s,w) job(w) buyer_token(w) vault_token(w) config2 token system
//!                   data: id[32] amount u64 work i64 review i64 brief[32] repo_hash[32] ref_hash[32]
use super::{close_job, create_pda, err, token_owner_mint, token_transfer, Config, Job, JOB_LEN, S, TOKEN_PROGRAM};
use solana_program::{
    account_info::{next_account_info, AccountInfo},
    clock::Clock,
    entrypoint::ProgramResult,
    hash::hashv,
    program_error::ProgramError,
    pubkey::Pubkey,
    system_program,
    sysvar::Sysvar,
};

pub const GH_JOB_LEN: usize = JOB_LEN + 32 + 32; // a github job: the job, then sha256(repository), sha256(ref)
pub const ISSUER: &[u8] = b"https://token.actions.githubusercontent.com";
pub const WF_PROVE: u8 = 0; // prove.yml
pub const WF_FUND: u8 = 1;  // fund.yml
pub const WF_LEN: usize = 1 + 40; // kind, sha (40 lowercase hex)

fn is_sha40(s: &[u8]) -> bool { s.len() == 40 && s.iter().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f')) }

/// The workflow registry entry ["workflow", kind, sha] exists (owned by the program) for this sha.
fn workflow_registered(program_id: &Pubkey, wf: &AccountInfo, kind: u8, sha: &[u8]) -> bool {
    if !is_sha40(sha) { return false; }
    let (k, _) = Pubkey::find_program_address(&[b"workflow", &[kind], &sha[..20], &sha[20..]], program_id);
    *wf.key == k && wf.owner == program_id && wf.data_len() == WF_LEN
}

/// 24 SetWorkflow  admin(s,w) config2 workflow(w) system      data: kind u8 (0 prove.yml, 1 fund.yml) add u8 sha[40]
/// Adds (creates ["workflow", kind, sha]) or removes (closes it, rent to the admin) an allowed workflow commit sha.
pub fn set_workflow(program_id: &Pubkey, accounts: &[AccountInfo], rest: &[u8], config_key: &Pubkey) -> ProgramResult {
    let it = &mut accounts.iter();
    let admin = next_account_info(it)?; let config = next_account_info(it)?; let wf = next_account_info(it)?;
    let sys = next_account_info(it)?;
    let cfg = Config::load(config, program_id, config_key)?;
    if !admin.is_signer || !admin.is_writable || *admin.key != cfg.admin { return Err(err(15, "admin only")); }
    if *sys.key != system_program::ID || rest.len() != 2 + 40 { return Err(ProgramError::InvalidInstructionData); }
    let (kind, add, sha) = (rest[0], rest[1] != 0, &rest[2..42]);
    if kind > WF_FUND || !is_sha40(sha) { return Err(err(73, "workflow kind or sha")); }
    let (k, bump) = Pubkey::find_program_address(&[b"workflow", &[kind], &sha[..20], &sha[20..]], program_id);
    if *wf.key != k { return Err(err(5, "workflow address")); }
    if add {
        if !wf.data_is_empty() || *wf.owner != system_program::ID { return Err(err(6, "workflow exists")); }
        create_pda(admin, wf, sys, program_id, WF_LEN, &[b"workflow", &[kind], &sha[..20], &sha[20..], &[bump]])?;
        let mut d = wf.try_borrow_mut_data()?;
        d[0] = kind; d[1..].copy_from_slice(sha);
    } else {
        if wf.owner != program_id { return Err(err(73, "workflow not registered")); }
        let lamports = wf.lamports();
        **admin.try_borrow_mut_lamports()? = admin.lamports().checked_add(lamports).ok_or(ProgramError::ArithmeticOverflow)?;
        **wf.try_borrow_mut_lamports()? = 0;
        wf.resize(0)?;
        wf.assign(&system_program::ID);
    }
    Ok(())
}
const L: usize = 64; // 32-bit limbs of an RSA-2048 number
type Big = [u32; L];
const KEY_LEN: usize = 256 + 4 + 256; // n limbs (LE), n0inv, r2 limbs
pub const MAX_JWT: usize = 2048;
// proof buffer: stage u8 (0 written, 1 step 1 done), jwt_len u16, key[32], x limbs[256], jwt
const B_STAGE: usize = 0;
const B_LEN: usize = 1;
const B_KEY: usize = 3;
const B_X: usize = 35;
const B_JWT: usize = 291;
pub const BUF_LEN: usize = B_JWT + MAX_JWT;
const DIGEST_INFO: [u8; 19] = [0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01, 0x05, 0x00, 0x04, 0x20];

// -- RSA-2048 in Montgomery form (CIOS) ------------------------------------------------------------------------------
fn be_to_limbs(b: &[u8]) -> Big {
    let mut x = [0u32; L];
    for (i, xi) in x.iter_mut().enumerate() {
        let o = 256 - 4 * (i + 1);
        *xi = u32::from_be_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]]);
    }
    x
}
fn limbs_to_be(x: &Big) -> [u8; 256] {
    let mut b = [0u8; 256];
    for (i, xi) in x.iter().enumerate() {
        let o = 256 - 4 * (i + 1);
        b[o..o + 4].copy_from_slice(&xi.to_be_bytes());
    }
    b
}
fn load(d: &[u8], off: usize) -> Big {
    let mut x = [0u32; L];
    for (i, xi) in x.iter_mut().enumerate() {
        *xi = u32::from_le_bytes(d[off + 4 * i..off + 4 * i + 4].try_into().unwrap());
    }
    x
}
fn store(d: &mut [u8], off: usize, x: &Big) {
    for (i, xi) in x.iter().enumerate() {
        d[off + 4 * i..off + 4 * i + 4].copy_from_slice(&xi.to_le_bytes());
    }
}
fn geq(a: &Big, b: &Big) -> bool {
    for i in (0..L).rev() {
        if a[i] != b[i] { return a[i] > b[i]; }
    }
    true
}
fn sub_in(a: &mut Big, b: &Big) {
    let mut borrow = 0u64;
    for i in 0..L {
        let d = (a[i] as u64).wrapping_sub(b[i] as u64).wrapping_sub(borrow);
        a[i] = d as u32;
        borrow = (d >> 63) & 1;
    }
}

/// a * b * R^-1 mod n (R = 2^2048), for a, b < n.
pub fn mont_mul(a: &Big, b: &Big, n: &Big, n0inv: u32) -> Big {
    let mut t = [0u32; L + 2];
    for i in 0..L {
        let ai = a[i] as u64;
        let mut c = 0u64;
        for j in 0..L {
            let s = t[j] as u64 + ai * b[j] as u64 + c;
            t[j] = s as u32;
            c = s >> 32;
        }
        let s = t[L] as u64 + c;
        t[L] = s as u32;
        t[L + 1] = (s >> 32) as u32;
        let m = t[0].wrapping_mul(n0inv) as u64;
        let s = t[0] as u64 + m * n[0] as u64;
        let mut c = s >> 32;
        for j in 1..L {
            let s = t[j] as u64 + m * n[j] as u64 + c;
            t[j - 1] = s as u32;
            c = s >> 32;
        }
        let s = t[L] as u64 + c;
        t[L - 1] = s as u32;
        t[L] = t[L + 1].wrapping_add((s >> 32) as u32);
        t[L + 1] = 0;
    }
    let mut r = [0u32; L];
    r.copy_from_slice(&t[..L]);
    if t[L] != 0 || geq(&r, n) { sub_in(&mut r, n); }
    r
}

/// a^2 * R^-1 mod n for a < n: the square by symmetry (each cross product once, doubled), then a separate
/// Montgomery reduction. About 1.5 L^2 limb products instead of mont_mul's 2 L^2.
pub fn mont_sqr(a: &Big, n: &Big, n0inv: u32) -> Big {
    let mut t = [0u32; 2 * L + 1];
    for i in 0..L {
        let ai = a[i] as u64;
        let mut c = 0u64;
        for j in i + 1..L {
            let s = t[i + j] as u64 + ai * a[j] as u64 + c;
            t[i + j] = s as u32;
            c = s >> 32;
        }
        t[i + L] = c as u32;
    }
    let mut top = 0u32;
    for v in t.iter_mut().take(2 * L) {
        let w = *v;
        *v = (w << 1) | top;
        top = w >> 31;
    }
    let mut c = 0u64;
    for i in 0..L {
        let d = a[i] as u64 * a[i] as u64;
        let s = t[2 * i] as u64 + (d & 0xffff_ffff) + c;
        t[2 * i] = s as u32;
        let s = t[2 * i + 1] as u64 + (d >> 32) + (s >> 32);
        t[2 * i + 1] = s as u32;
        c = s >> 32;
    }
    let mut hi = 0u64;
    for i in 0..L {
        let m = t[i].wrapping_mul(n0inv) as u64;
        let mut c = 0u64;
        for j in 0..L {
            let s = t[i + j] as u64 + m * n[j] as u64 + c;
            t[i + j] = s as u32;
            c = s >> 32;
        }
        let s = t[i + L] as u64 + c + hi;
        t[i + L] = s as u32;
        hi = s >> 32;
    }
    let mut r = [0u32; L];
    r.copy_from_slice(&t[L..2 * L]);
    if hi != 0 || geq(&r, n) { sub_in(&mut r, n); }
    r
}

// -- base64url and a flat JSON object --------------------------------------------------------------------------------
fn b64v(c: u8) -> Option<u32> {
    Some(match c {
        b'A'..=b'Z' => c - b'A',
        b'a'..=b'z' => c - b'a' + 26,
        b'0'..=b'9' => c - b'0' + 52,
        b'-' => 62,
        b'_' => 63,
        _ => return None,
    } as u32)
}
pub fn b64url(s: &[u8]) -> Result<Vec<u8>, ProgramError> {
    if s.len() % 4 == 1 { return Err(err(60, "bad base64url")); }
    let mut out = Vec::with_capacity(s.len() * 3 / 4 + 3);
    let bad = || err(60, "bad base64url");
    for ch in s.chunks(4) {
        let mut v = 0u32;
        for (k, &c) in ch.iter().enumerate() { v |= b64v(c).ok_or_else(bad)? << (18 - 6 * k as u32); }
        out.push((v >> 16) as u8);
        if ch.len() > 2 { out.push((v >> 8) as u8); }
        if ch.len() > 3 { out.push(v as u8); }
    }
    Ok(out)
}

fn ws(b: &[u8], mut i: usize) -> usize {
    while i < b.len() && matches!(b[i], b' ' | b'\t' | b'\n' | b'\r') { i += 1; }
    i
}
/// b[i] == '"': the string's raw content range and the index after its closing quote.
fn jstr(b: &[u8], i: usize) -> Result<(usize, usize, usize), ProgramError> {
    if b.get(i) != Some(&b'"') { return Err(err(61, "bad json")); }
    let mut k = i + 1;
    while k < b.len() {
        match b[k] {
            b'\\' => k += 2,
            b'"' => return Ok((i + 1, k, k + 1)),
            _ => k += 1,
        }
    }
    Err(err(61, "bad json"))
}
fn skip_value(b: &[u8], i: usize) -> Result<usize, ProgramError> {
    match b.get(i) {
        Some(b'"') => Ok(jstr(b, i)?.2),
        Some(b'{') | Some(b'[') => {
            let mut depth = 0usize;
            let mut k = i;
            while k < b.len() {
                match b[k] {
                    b'"' => { k = jstr(b, k)?.2; continue; }
                    b'{' | b'[' => depth += 1,
                    b'}' | b']' => { depth -= 1; if depth == 0 { return Ok(k + 1); } }
                    _ => {}
                }
                k += 1;
            }
            Err(err(61, "bad json"))
        }
        Some(_) => {
            let mut k = i;
            while k < b.len() && !matches!(b[k], b',' | b'}' | b']' | b' ' | b'\t' | b'\n' | b'\r') { k += 1; }
            if k == i { Err(err(61, "bad json")) } else { Ok(k) }
        }
        None => Err(err(61, "bad json")),
    }
}
/// One pass over a flat JSON object: for each wanted key, its value (a string's raw content, or the raw token) and
/// whether it was a string. A wanted key that appears twice is refused.
fn fields<'a, const N: usize>(b: &'a [u8], want: [&[u8]; N]) -> Result<[Option<(&'a [u8], bool)>; N], ProgramError> {
    let mut got: [Option<(&'a [u8], bool)>; N] = [None; N];
    let mut i = ws(b, 0);
    if b.get(i) != Some(&b'{') { return Err(err(61, "bad json")); }
    i = ws(b, i + 1);
    if b.get(i) == Some(&b'}') { return Ok(got); }
    loop {
        let (ks, ke, next) = jstr(b, i)?;
        i = ws(b, next);
        if b.get(i) != Some(&b':') { return Err(err(61, "bad json")); }
        let vs = ws(b, i + 1);
        let ve = skip_value(b, vs)?;
        let key = &b[ks..ke];
        for (w, g) in want.iter().zip(got.iter_mut()) {
            if key == *w {
                if g.is_some() { return Err(err(62, "duplicate claim")); }
                *g = Some(if b[vs] == b'"' { (&b[vs + 1..ve - 1], true) } else { (&b[vs..ve], false) });
            }
        }
        i = ws(b, ve);
        match b.get(i) {
            Some(b',') => i = ws(b, i + 1),
            Some(b'}') => return Ok(got),
            _ => return Err(err(61, "bad json")),
        }
    }
}
fn want_str<'a>(f: Option<(&'a [u8], bool)>, what: &str) -> Result<&'a [u8], ProgramError> {
    match f { Some((v, true)) => Ok(v), _ => Err(err(63, what)) }
}

fn buffer_key(program_id: &Pubkey, job_id: &[u8; 32], prover: &Pubkey) -> (Pubkey, u8) {
    Pubkey::find_program_address(&[b"ghproof", job_id, prover.as_ref()], program_id)
}
fn job_id_of(rest: &[u8]) -> Result<[u8; 32], ProgramError> {
    rest.get(0..32).ok_or(ProgramError::InvalidInstructionData).map(|s| s.try_into().unwrap())
}
/// The JWT's three parts: header, payload, signature (base64url).
fn split_jwt(t: &[u8]) -> Result<(&[u8], &[u8], &[u8]), ProgramError> {
    let a = t.iter().position(|&c| c == b'.').ok_or_else(|| err(64, "not a jwt"))?;
    let b = a + 1 + t[a + 1..].iter().position(|&c| c == b'.').ok_or_else(|| err(64, "not a jwt"))?;
    Ok((&t[..a], &t[a + 1..b], &t[b + 1..]))
}
fn signature(sig_b64: &[u8], n: &Big) -> Result<Big, ProgramError> {
    let s = b64url(sig_b64)?;
    if s.len() != 256 { return Err(err(65, "signature is not 256 bytes")); }
    let s = be_to_limbs(&s);
    if geq(&s, n) { return Err(err(65, "signature out of range")); }
    Ok(s)
}

pub fn process(program_id: &Pubkey, accounts: &[AccountInfo], tag: u8, rest: &[u8], config_key: &Pubkey,
               vault_auth: &Pubkey, vault_bump: u8) -> ProgramResult {
    let it = &mut accounts.iter();
    match tag {
        15 => {
            let admin = next_account_info(it)?; let config = next_account_info(it)?; let key = next_account_info(it)?;
            let sys = next_account_info(it)?;
            let cfg = Config::load(config, program_id, config_key)?;
            if !admin.is_signer || *admin.key != cfg.admin { return Err(err(15, "admin only")); }
            if *sys.key != system_program::ID { return Err(err(1, "system")); }
            let kl = *rest.first().ok_or(ProgramError::InvalidInstructionData)? as usize;
            if kl == 0 || kl > 64 || rest.len() != 1 + kl + 256 + 256 + 4 { return Err(ProgramError::InvalidInstructionData); }
            let kid = &rest[1..1 + kl];
            let n = be_to_limbs(&rest[1 + kl..1 + kl + 256]);
            let r2 = be_to_limbs(&rest[1 + kl + 256..1 + kl + 512]);
            let n0inv = u32::from_le_bytes(rest[1 + kl + 512..].try_into().unwrap());
            if n[0] & 1 == 0 || n[L - 1] >> 31 == 0 { return Err(err(66, "not a 2048-bit odd modulus")); }
            if n[0].wrapping_mul(n0inv) != u32::MAX { return Err(err(66, "n0inv")); }
            // r2 < n and r2 * R^-1 == R mod n (= 2^2048 - n, as n > 2^2047): r2 is R^2 mod n.
            if geq(&r2, &n) { return Err(err(66, "r2")); }
            let mut one = [0u32; L]; one[0] = 1;
            let r = mont_mul(&r2, &one, &n, n0inv);
            let mut r_mod_n = [0u32; L];
            sub_in(&mut r_mod_n, &n);
            if r != r_mod_n { return Err(err(66, "r2 is not R^2 mod n")); }
            let kh = hashv(&[kid]).to_bytes();
            let (kk, bump) = Pubkey::find_program_address(&[b"ghkey", &kh], program_id);
            if *key.key != kk { return Err(err(5, "key address")); }
            if !key.data_is_empty() || *key.owner != system_program::ID { return Err(err(6, "key exists")); }
            create_pda(admin, key, sys, program_id, KEY_LEN, &[b"ghkey", &kh, &[bump]])?;
            let mut d = key.try_borrow_mut_data()?;
            store(&mut d, 0, &n); d[256..260].copy_from_slice(&n0inv.to_le_bytes()); store(&mut d, 260, &r2);
            Ok(())
        }
        16 => {
            let prover = next_account_info(it)?; let buf = next_account_info(it)?; let sys = next_account_info(it)?;
            let job_id = job_id_of(rest)?;
            if rest.len() < 36 { return Err(ProgramError::InvalidInstructionData); }
            let total = u16::from_le_bytes(rest[32..34].try_into().unwrap()) as usize;
            let off = u16::from_le_bytes(rest[34..36].try_into().unwrap()) as usize;
            let chunk = &rest[36..];
            if total > MAX_JWT || off + chunk.len() > total { return Err(err(67, "proof too long")); }
            if !prover.is_signer || *sys.key != system_program::ID { return Err(err(8, "buffer accounts")); }
            let (bk, bump) = buffer_key(program_id, &job_id, prover.key);
            if *buf.key != bk { return Err(err(5, "buffer address")); }
            if buf.data_is_empty() {
                create_pda(prover, buf, sys, program_id, BUF_LEN, &[b"ghproof", &job_id, prover.key.as_ref(), &[bump]])?;
            } else if buf.owner != program_id || buf.data_len() != BUF_LEN {
                return Err(err(5, "buffer account"));
            }
            let mut d = buf.try_borrow_mut_data()?;
            d[B_STAGE] = 0;
            d[B_LEN..B_LEN + 2].copy_from_slice(&(total as u16).to_le_bytes());
            d[B_JWT + off..B_JWT + off + chunk.len()].copy_from_slice(chunk);
            Ok(())
        }
        17 | 18 => {
            let prover = next_account_info(it)?; let buf = next_account_info(it)?; let key = next_account_info(it)?;
            let job_id = job_id_of(rest)?;
            if !prover.is_signer { return Err(err(8, "prover signs")); }
            let (bk, _) = buffer_key(program_id, &job_id, prover.key);
            if *buf.key != bk || buf.owner != program_id || buf.data_len() != BUF_LEN { return Err(err(5, "buffer account")); }
            if key.owner != program_id || key.data_len() != KEY_LEN { return Err(err(68, "not a registered key")); }
            let (n, n0inv) = {
                let kd = key.try_borrow_data()?;
                (load(&kd, 0), u32::from_le_bytes(kd[256..260].try_into().unwrap()))
            };
            if tag == 17 {
                let mut d = buf.try_borrow_mut_data()?;
                let len = u16::from_le_bytes(d[B_LEN..B_LEN + 2].try_into().unwrap()) as usize;
                let (_, _, sig) = split_jwt(&d[B_JWT..B_JWT + len])?;
                let s = signature(sig, &n)?;
                let r2 = load(&key.try_borrow_data()?, 260);
                let mut x = mont_mul(&s, &r2, &n, n0inv); // s in Montgomery form
                for _ in 0..8 { x = mont_sqr(&x, &n, n0inv); }
                store(&mut d, B_X, &x);
                d[B_KEY..B_KEY + 32].copy_from_slice(key.key.as_ref());
                d[B_STAGE] = 1;
                return Ok(());
            }
            let job = next_account_info(it)?; let vault_tok = next_account_info(it)?; let vauth = next_account_info(it)?;
            let worker_tok = next_account_info(it)?; let fee_tok = next_account_info(it)?; let buyer = next_account_info(it)?;
            let config = next_account_info(it)?; let token = next_account_info(it)?; let wf = next_account_info(it)?;
            if !prover.is_writable || *vauth.key != *vault_auth || *token.key != TOKEN_PROGRAM { return Err(err(9, "verify accounts")); }
            let cfg = Config::load(config, program_id, config_key)?;
            let (jk, _) = Pubkey::find_program_address(&[b"job", &job_id], program_id);
            if *job.key != jk || job.owner != program_id || job.data_len() != GH_JOB_LEN { return Err(err(69, "not a github job")); }
            let now = Clock::get()?.unix_timestamp;
            {
                let d = buf.try_borrow_data()?;
                if d[B_STAGE] != 1 || d[B_KEY..B_KEY + 32] != key.key.as_ref()[..] { return Err(err(70, "run step 1 first")); }
                let len = u16::from_le_bytes(d[B_LEN..B_LEN + 2].try_into().unwrap()) as usize;
                let t = &d[B_JWT..B_JWT + len];
                let (h64, p64, sig) = split_jwt(t)?;
                let s = signature(sig, &n)?;
                // the key is the one the header names
                let header = b64url(h64)?;
                let [alg, kid] = fields(&header, [b"alg", b"kid"])?;
                if want_str(alg, "alg")? != b"RS256" { return Err(err(63, "alg")); }
                let kh = hashv(&[want_str(kid, "kid")?]).to_bytes();
                let (kk, _) = Pubkey::find_program_address(&[b"ghkey", &kh], program_id);
                if *key.key != kk { return Err(err(68, "the token's key is not this key")); }
                // s^65537 mod n: x = (s^256)_M from step 1; 8 more squarings -> (s^65536)_M; * s (plain) -> s^65537
                let mut x = load(&d, B_X);
                for _ in 0..8 { x = mont_sqr(&x, &n, n0inv); }
                let em = limbs_to_be(&mont_mul(&x, &s, &n, n0inv));
                let digest = hashv(&[&t[..h64.len() + 1 + p64.len()]]).to_bytes();
                let ps = 256 - 3 - DIGEST_INFO.len() - 32;
                let ok = em[0] == 0 && em[1] == 1 && em[2..2 + ps].iter().all(|&c| c == 0xff) && em[2 + ps] == 0
                    && em[3 + ps..3 + ps + 19] == DIGEST_INFO && em[256 - 32..] == digest;
                if !ok { return Err(err(71, "bad signature")); }
                // the claims
                let payload = b64url(p64)?;
                let [iss, wsha, repo, rf, aud, exp] =
                    fields(&payload, [b"iss", b"job_workflow_sha", b"repository", b"ref", b"aud", b"exp"])?;
                if want_str(iss, "iss")? != ISSUER { return Err(err(72, "issuer")); }
                // the run's workflow file is pinned by commit: its sha must be in the registry (SetWorkflow, kind 0)
                if !workflow_registered(program_id, wf, WF_PROVE, want_str(wsha, "job_workflow_sha")?) {
                    return Err(err(73, "workflow sha not registered"));
                }
                let jd = job.try_borrow_data()?;
                if hashv(&[want_str(repo, "repository")?]).to_bytes()[..] != jd[JOB_LEN..JOB_LEN + 32] { return Err(err(74, "repository")); }
                if hashv(&[want_str(rf, "ref")?]).to_bytes()[..] != jd[JOB_LEN + 32..JOB_LEN + 64] { return Err(err(74, "ref")); }
                let mut want_aud = [0u8; 5 + 64];
                want_aud[..5].copy_from_slice(b"knos:");
                const HEX: &[u8; 16] = b"0123456789abcdef";
                for (k, b) in job_id.iter().enumerate() { want_aud[5 + 2 * k] = HEX[(b >> 4) as usize]; want_aud[6 + 2 * k] = HEX[(b & 15) as usize]; }
                if want_str(aud, "aud")? != want_aud { return Err(err(75, "audience")); }
                let exp = match exp { Some((v, false)) if !v.is_empty() && v.len() <= 18 && v.iter().all(|c| c.is_ascii_digit()) =>
                    v.iter().fold(0i64, |a, c| a * 10 + (c - b'0') as i64), _ => return Err(err(63, "exp")) };
                if exp <= now { return Err(err(76, "token expired")); }
            }
            // pay the worker: price - fee + the stake back; the fee to the fee account; close the job and the buffer
            let mut j = Job::load(&job.try_borrow_data()?)?;
            if j.state != S::Claimed as u8 || now > j.deadline { return Err(err(20, "not claimed or past the deadline")); }
            let (wo, _) = token_owner_mint(worker_tok)?;
            if wo != j.worker || *fee_tok.key != cfg.fee_token { return Err(err(33, "payee")); }
            let fee = cfg.fee(j.amount).min(j.amount);
            let to_worker = (j.amount - fee).checked_add(j.stake).ok_or(ProgramError::ArithmeticOverflow)?;
            j.state = S::Released as u8; j.stake = 0; j.store(&mut job.try_borrow_mut_data()?);
            if to_worker > 0 { token_transfer(token, vault_tok, worker_tok, vauth, to_worker, Some(&[b"vault", &[vault_bump]]))?; }
            if fee > 0 { token_transfer(token, vault_tok, fee_tok, vauth, fee, Some(&[b"vault", &[vault_bump]]))?; }
            close_job(&j, job, buyer)?;
            let lamports = buf.lamports();
            **prover.try_borrow_mut_lamports()? = prover.lamports().checked_add(lamports).ok_or(ProgramError::ArithmeticOverflow)?;
            **buf.try_borrow_mut_lamports()? = 0;
            buf.resize(0)?;
            buf.assign(&system_program::ID);
            Ok(())
        }
        19 => {
            let buyer = next_account_info(it)?; let job = next_account_info(it)?; let buyer_tok = next_account_info(it)?;
            let vault_tok = next_account_info(it)?; let config = next_account_info(it)?; let token = next_account_info(it)?; let sys = next_account_info(it)?;
            if rest.len() != 32 + 8 + 8 + 8 + 32 + 64 { return Err(ProgramError::InvalidInstructionData); }
            let id: [u8; 32] = rest[0..32].try_into().unwrap();
            let amount = u64::from_le_bytes(rest[32..40].try_into().unwrap());
            let work = i64::from_le_bytes(rest[40..48].try_into().unwrap());
            let review = i64::from_le_bytes(rest[48..56].try_into().unwrap());
            let brief: [u8; 32] = rest[56..88].try_into().unwrap();
            if !buyer.is_signer || *token.key != TOKEN_PROGRAM || *sys.key != system_program::ID { return Err(err(3, "post accounts")); }
            let cfg = Config::load(config, program_id, config_key)?;
            if cfg.paused { return Err(err(17, "paused: no new jobs")); }
            if amount != 0 && amount < cfg.min_amount { return Err(err(18, "below the minimum job")); }
            if cfg.max_amount != 0 && amount > cfg.max_amount { return Err(err(19, "over the per-job cap")); }
            if work <= 0 || review <= 0 { return Err(err(4, "bad terms")); }
            let (job_key, bump) = Pubkey::find_program_address(&[b"job", &id], program_id);
            if *job.key != job_key { return Err(err(5, "job address")); }
            if !job.data_is_empty() || *job.owner != system_program::ID { return Err(err(6, "job exists")); }
            let (vo, vm) = token_owner_mint(vault_tok)?; let (_, bm) = token_owner_mint(buyer_tok)?;
            if vo != *vault_auth || vm != cfg.mint || bm != cfg.mint { return Err(err(7, "vault or mint")); }
            create_pda(buyer, job, sys, program_id, GH_JOB_LEN, &[b"job", &id, &[bump]])?;
            if amount > 0 { token_transfer(token, buyer_tok, vault_tok, buyer, amount, None)?; }
            let now = Clock::get()?.unix_timestamp;
            let mut d = job.try_borrow_mut_data()?;
            Job { state: S::Open as u8, buyer: *buyer.key, worker: Pubkey::default(), amount, deadline: now.saturating_add(work), review,
                  brief, result: [0; 32], verifier: Pubkey::default(), proof: [0; 32], stake: 0, mint: Pubkey::default() }
                .store(&mut d);
            d[JOB_LEN..GH_JOB_LEN].copy_from_slice(&rest[88..152]);
            Ok(())
        }
        _ => Err(ProgramError::InvalidInstructionData),
    }
}
