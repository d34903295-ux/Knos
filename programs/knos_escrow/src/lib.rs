//! Knos escrow: pay-on-acceptance escrow for AI agent jobs on Solana (SPL tokens).
//! Same state machine as KnosEscrow.sol on Tempo: post -> claim -> deliver -> accept | reject | release | refund.
//! Native program (no framework). Measurement build for Knos 0.3.1.
use solana_program::{
    account_info::{next_account_info, AccountInfo},
    clock::Clock,
    entrypoint::ProgramResult,
    instruction::{AccountMeta, Instruction},
    msg,
    program::{invoke, invoke_signed},
    program_error::ProgramError,
    pubkey::Pubkey,
    rent::Rent,
    system_instruction, system_program,
    sysvar::Sysvar,
};

#[cfg(not(feature = "no-entrypoint"))]
solana_program::entrypoint!(process);

pub const TOKEN_PROGRAM: Pubkey = solana_program::pubkey!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
const CONFIG_LEN: usize = 32 + 32 + 32 + 2 + 1; // admin, mint, fee_token, fee_bps, bump
const JOB_LEN: usize = 1 + 32 + 32 + 8 + 8 + 8 + 32 + 32; // state, buyer, worker, amount, deadline, review, brief, result

#[repr(u8)]
#[derive(PartialEq, Clone, Copy)]
enum S { None = 0, Open = 1, Claimed = 2, Delivered = 3, Released = 4, Refunded = 5 }

fn err(code: u32, m: &str) -> ProgramError { msg!("knos_escrow: {}", m); ProgramError::Custom(code) }

struct Job { state: u8, buyer: Pubkey, worker: Pubkey, amount: u64, deadline: i64, review: i64, brief: [u8; 32], result: [u8; 32] }
impl Job {
    fn load(d: &[u8]) -> Job {
        let pk = |o: usize| Pubkey::new_from_array(d[o..o + 32].try_into().unwrap());
        let u = |o: usize| u64::from_le_bytes(d[o..o + 8].try_into().unwrap());
        Job { state: d[0], buyer: pk(1), worker: pk(33), amount: u(65), deadline: u(73) as i64, review: u(81) as i64,
              brief: d[89..121].try_into().unwrap(), result: d[121..153].try_into().unwrap() }
    }
    fn store(&self, d: &mut [u8]) {
        d[0] = self.state; d[1..33].copy_from_slice(self.buyer.as_ref()); d[33..65].copy_from_slice(self.worker.as_ref());
        d[65..73].copy_from_slice(&self.amount.to_le_bytes()); d[73..81].copy_from_slice(&(self.deadline as u64).to_le_bytes());
        d[81..89].copy_from_slice(&(self.review as u64).to_le_bytes()); d[89..121].copy_from_slice(&self.brief);
        d[121..153].copy_from_slice(&self.result);
    }
}

fn token_owner_mint(acc: &AccountInfo) -> Result<(Pubkey, Pubkey), ProgramError> {
    if *acc.owner != TOKEN_PROGRAM { return Err(err(10, "not a token account")); }
    let d = acc.try_borrow_data()?;
    if d.len() < 72 { return Err(err(11, "bad token account")); }
    Ok((Pubkey::new_from_array(d[32..64].try_into().unwrap()), Pubkey::new_from_array(d[0..32].try_into().unwrap())))
}

fn token_transfer<'a>(token_prog: &AccountInfo<'a>, from: &AccountInfo<'a>, to: &AccountInfo<'a>, auth: &AccountInfo<'a>, amount: u64,
                  seeds: Option<&[&[u8]]>) -> ProgramResult {
    let mut data = vec![3u8]; data.extend_from_slice(&amount.to_le_bytes()); // SPL Token Transfer
    let ix = Instruction { program_id: TOKEN_PROGRAM, data,
        accounts: vec![AccountMeta::new(*from.key, false), AccountMeta::new(*to.key, false), AccountMeta::new_readonly(*auth.key, true)] };
    match seeds {
        Some(s) => invoke_signed(&ix, &[from.clone(), to.clone(), auth.clone(), token_prog.clone()], &[s]),
        None => invoke(&ix, &[from.clone(), to.clone(), auth.clone(), token_prog.clone()]),
    }
}

/// Create a PDA even if someone pre-funded its address with dust (create_account would fail on lamports > 0).
fn create_pda<'a>(payer: &AccountInfo<'a>, acc: &AccountInfo<'a>, sys: &AccountInfo<'a>, owner: &Pubkey, len: usize, seeds: &[&[u8]]) -> ProgramResult {
    let need = Rent::get()?.minimum_balance(len);
    if acc.lamports() == 0 {
        return invoke_signed(&system_instruction::create_account(payer.key, acc.key, need, len as u64, owner), &[payer.clone(), acc.clone(), sys.clone()], &[seeds]);
    }
    if acc.lamports() < need { invoke(&system_instruction::transfer(payer.key, acc.key, need - acc.lamports()), &[payer.clone(), acc.clone(), sys.clone()])?; }
    invoke_signed(&system_instruction::allocate(acc.key, len as u64), &[acc.clone(), sys.clone()], &[seeds])?;
    invoke_signed(&system_instruction::assign(acc.key, owner), &[acc.clone(), sys.clone()], &[seeds])
}

pub fn process(program_id: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> ProgramResult {
    let (&tag, rest) = data.split_first().ok_or(ProgramError::InvalidInstructionData)?;
    let it = &mut accounts.iter();
    let (config_key, _) = Pubkey::find_program_address(&[b"config"], program_id);
    let (vault_auth, vault_bump) = Pubkey::find_program_address(&[b"vault"], program_id);
    match tag {
        // 0 Init: admin(s,w), config(w), fee_token, system. data: fee_bps u16
        0 => {
            let admin = next_account_info(it)?; let config = next_account_info(it)?; let fee_token = next_account_info(it)?; let sys = next_account_info(it)?;
            if !admin.is_signer || *config.key != config_key || *sys.key != system_program::ID { return Err(err(1, "init accounts")); }
            let (_, mint) = token_owner_mint(fee_token)?;
            let fee_bps = u16::from_le_bytes(rest[0..2].try_into().map_err(|_| ProgramError::InvalidInstructionData)?);
            if fee_bps > 2000 { return Err(err(2, "fee too high")); }
            let (_, bump) = Pubkey::find_program_address(&[b"config"], program_id);
            invoke_signed(&system_instruction::create_account(admin.key, config.key, Rent::get()?.minimum_balance(CONFIG_LEN), CONFIG_LEN as u64, program_id),
                          &[admin.clone(), config.clone(), sys.clone()], &[&[b"config", &[bump]]])?;
            let mut d = config.try_borrow_mut_data()?;
            d[0..32].copy_from_slice(admin.key.as_ref()); d[32..64].copy_from_slice(mint.as_ref());
            d[64..96].copy_from_slice(fee_token.key.as_ref()); d[96..98].copy_from_slice(&fee_bps.to_le_bytes()); d[98] = bump;
            Ok(())
        }
        // 1 Post: buyer(s,w), job(w), buyer_token(w), vault_token(w), config, token, system. data: id[32] amount u64 work i64 review i64 brief[32]
        1 => {
            let buyer = next_account_info(it)?; let job = next_account_info(it)?; let buyer_tok = next_account_info(it)?;
            let vault_tok = next_account_info(it)?; let config = next_account_info(it)?; let token = next_account_info(it)?; let sys = next_account_info(it)?;
            if rest.len() < 32 + 8 + 8 + 8 + 32 { return Err(ProgramError::InvalidInstructionData); }
            let id: [u8; 32] = rest[0..32].try_into().unwrap();
            let amount = u64::from_le_bytes(rest[32..40].try_into().unwrap());
            let work = i64::from_le_bytes(rest[40..48].try_into().unwrap());
            let review = i64::from_le_bytes(rest[48..56].try_into().unwrap());
            let brief: [u8; 32] = rest[56..88].try_into().unwrap();
            if !buyer.is_signer || *config.key != config_key || *token.key != TOKEN_PROGRAM || *sys.key != system_program::ID { return Err(err(3, "post accounts")); }
            if amount == 0 || work <= 0 || review <= 0 { return Err(err(4, "bad terms")); }
            let (job_key, bump) = Pubkey::find_program_address(&[b"job", &id], program_id);
            if *job.key != job_key { return Err(err(5, "job address")); }
            if !job.data_is_empty() || *job.owner != system_program::ID { return Err(err(6, "job exists")); }
            let cfg = config.try_borrow_data()?; let mint = Pubkey::new_from_array(cfg[32..64].try_into().unwrap()); drop(cfg);
            let (vo, vm) = token_owner_mint(vault_tok)?; let (_, bm) = token_owner_mint(buyer_tok)?;
            if vo != vault_auth || vm != mint || bm != mint { return Err(err(7, "vault or mint")); }
            create_pda(buyer, job, sys, program_id, JOB_LEN, &[b"job", &id, &[bump]])?;
            token_transfer(token, buyer_tok, vault_tok, buyer, amount, None)?;
            let now = Clock::get()?.unix_timestamp;
            Job { state: S::Open as u8, buyer: *buyer.key, worker: Pubkey::default(), amount, deadline: now + work, review, brief, result: [0; 32] }
                .store(&mut job.try_borrow_mut_data()?);
            Ok(())
        }
        // 2 Claim: worker(s), job(w)  |  3 Deliver: worker(s), job(w); data result[32]
        2 | 3 => {
            let worker = next_account_info(it)?; let job = next_account_info(it)?;
            if *job.owner != *program_id || !worker.is_signer { return Err(err(8, "claim/deliver accounts")); }
            let mut j = Job::load(&job.try_borrow_data()?); let now = Clock::get()?.unix_timestamp;
            if tag == 2 {
                if j.state != S::Open as u8 || now > j.deadline { return Err(err(20, "not open")); }
                j.worker = *worker.key; j.state = S::Claimed as u8;
            } else {
                if j.state != S::Claimed as u8 || *worker.key != j.worker { return Err(err(21, "not worker")); }
                j.result = rest.get(0..32).ok_or(ProgramError::InvalidInstructionData)?.try_into().unwrap();
                j.state = S::Delivered as u8; j.deadline = now + j.review;
            }
            j.store(&mut job.try_borrow_mut_data()?); Ok(())
        }
        // 4 Accept: signer(s), job(w), vault_token(w), vault_auth, worker_token(w), fee_token(w), config, token
        // 5 Release: same accounts, anyone, after the review window
        4 | 5 => {
            let who = next_account_info(it)?; let job = next_account_info(it)?; let vault_tok = next_account_info(it)?; let vauth = next_account_info(it)?;
            let worker_tok = next_account_info(it)?; let fee_tok = next_account_info(it)?; let config = next_account_info(it)?; let token = next_account_info(it)?;
            if *job.owner != *program_id || !who.is_signer || *vauth.key != vault_auth || *config.key != config_key || *token.key != TOKEN_PROGRAM { return Err(err(9, "release accounts")); }
            let mut j = Job::load(&job.try_borrow_data()?); let now = Clock::get()?.unix_timestamp;
            if j.state != S::Delivered as u8 { return Err(err(30, "not delivered")); }
            if tag == 4 && *who.key != j.buyer { return Err(err(31, "not buyer")); }
            if tag == 5 && now <= j.deadline { return Err(err(32, "in review")); }
            let cfg = config.try_borrow_data()?; let fee_token = Pubkey::new_from_array(cfg[64..96].try_into().unwrap());
            let fee_bps = u16::from_le_bytes(cfg[96..98].try_into().unwrap()) as u64; drop(cfg);
            let (wo, _) = token_owner_mint(worker_tok)?;
            if wo != j.worker || *fee_tok.key != fee_token { return Err(err(33, "payee")); }
            let fee = j.amount * fee_bps / 10_000;
            j.state = S::Released as u8; j.store(&mut job.try_borrow_mut_data()?);
            token_transfer(token, vault_tok, worker_tok, vauth, j.amount - fee, Some(&[b"vault", &[vault_bump]]))?;
            if fee > 0 { token_transfer(token, vault_tok, fee_tok, vauth, fee, Some(&[b"vault", &[vault_bump]]))?; }
            Ok(())
        }
        // 6 Reject: buyer(s), job(w), vault_token(w), vault_auth, buyer_token(w), token  (inside review window)
        // 7 Refund: same accounts (open/claimed past the work deadline)
        6 | 7 => {
            let buyer = next_account_info(it)?; let job = next_account_info(it)?; let vault_tok = next_account_info(it)?; let vauth = next_account_info(it)?;
            let buyer_tok = next_account_info(it)?; let token = next_account_info(it)?;
            if *job.owner != *program_id || !buyer.is_signer || *vauth.key != vault_auth || *token.key != TOKEN_PROGRAM { return Err(err(12, "refund accounts")); }
            let mut j = Job::load(&job.try_borrow_data()?); let now = Clock::get()?.unix_timestamp;
            if *buyer.key != j.buyer { return Err(err(40, "not buyer")); }
            let ok = if tag == 6 { j.state == S::Delivered as u8 && now <= j.deadline }
                     else { (j.state == S::Open as u8 || j.state == S::Claimed as u8) && now > j.deadline };
            if !ok { return Err(err(41, "not refundable")); }
            let (bo, _) = token_owner_mint(buyer_tok)?;
            if bo != j.buyer { return Err(err(42, "refund to buyer only")); }
            j.state = S::Refunded as u8; j.store(&mut job.try_borrow_mut_data()?);
            token_transfer(token, vault_tok, buyer_tok, vauth, j.amount, Some(&[b"vault", &[vault_bump]]))
        }
        _ => Err(ProgramError::InvalidInstructionData),
    }
}
