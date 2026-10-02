//! Example consumer of knos-oidc: a release gate. It records, per GitHub repository, the last commit for which
//! GitHub signed "a workflow ran here on a GitHub-hosted runner with audience oidc-gate:release". Anything on Solana
//! (an upgrade multisig, a DAO, a token unlock) can then require "the commit being deployed is the one CI ran on".
//!
//! The whole integration is the 12 lines in `process`: check the token account's owner is knos-oidc, call
//! `knos_oidc::verified`, check freshness, read the claims you care about. No oracle, no CPI, no admin.
//!
//!   Record  payer(s,w) token gate(w) system        (no data)
//!           gate = ["gate", repository_id (u64 LE)] : repository_id u64, slot u64, sha[40]
use knos_oidc::claims::{fields, number, text};
use solana_program::{
    account_info::{next_account_info, AccountInfo},
    clock::Clock,
    entrypoint,
    entrypoint::ProgramResult,
    program::{invoke, invoke_signed},
    program_error::ProgramError,
    pubkey,
    pubkey::Pubkey,
    rent::Rent,
    system_instruction,
    sysvar::Sysvar,
};

/// The knos-oidc program. PIN: the deployed address (programs/program_ids.json).
pub const OIDC_ID: Pubkey = pubkey!("vpWym9azbPU5f2PH2a6n8c4RfmsyUeW2dMuWr1DSHcE");
pub const GATE_LEN: usize = 56;

entrypoint!(process);

pub fn process(program_id: &Pubkey, accounts: &[AccountInfo], _data: &[u8]) -> ProgramResult {
    let it = &mut accounts.iter();
    let payer = next_account_info(it)?; let token = next_account_info(it)?; let gate = next_account_info(it)?;
    let sys = next_account_info(it)?;
    let clock = Clock::get()?;
    // ---- the integration ----
    if *token.owner != OIDC_ID { return Err(ProgramError::IllegalOwner); }
    let data = token.try_borrow_data()?;
    let v = knos_oidc::verified(&data).ok_or(ProgramError::InvalidAccountData)?;
    if v.issuer != knos_oidc::pins::ISSUER_GITHUB || !knos_oidc::fresh(v.exp, clock.unix_timestamp) { return Err(ProgramError::InvalidAccountData); }
    let [repo, sha, runner, aud] = fields(v.payload, [b"repository_id", b"sha", b"runner_environment", b"aud"])?;
    if text(runner)? != b"github-hosted" || text(aud)? != b"oidc-gate:release" { return Err(ProgramError::InvalidArgument); }
    let (repo, sha) = (number(repo)?, text(sha)?);
    if sha.len() != 40 { return Err(ProgramError::InvalidArgument); }
    // ---- record it ----
    let rb = repo.to_le_bytes();
    let (gk, bump) = Pubkey::find_program_address(&[b"gate", &rb], program_id);
    if *gate.key != gk || !payer.is_signer { return Err(ProgramError::InvalidSeeds); }
    if gate.owner != program_id {
        let need = Rent::get()?.minimum_balance(GATE_LEN);
        invoke(&system_instruction::transfer(payer.key, gate.key, need.saturating_sub(gate.lamports())), &[payer.clone(), gate.clone(), sys.clone()])?;
        invoke_signed(&system_instruction::allocate(gate.key, GATE_LEN as u64), &[gate.clone(), sys.clone()], &[&[b"gate", &rb, &[bump]]])?;
        invoke_signed(&system_instruction::assign(gate.key, program_id), &[gate.clone(), sys.clone()], &[&[b"gate", &rb, &[bump]]])?;
    }
    let mut g = gate.try_borrow_mut_data()?;
    g[0..8].copy_from_slice(&rb);
    g[8..16].copy_from_slice(&clock.slot.to_le_bytes());
    g[16..56].copy_from_slice(&sha);
    Ok(())
}
