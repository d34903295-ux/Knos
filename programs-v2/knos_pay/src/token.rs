//! The two token programs this escrow accepts (SPL Token and Token-2022), what it checks of a mint and of a token
//! account, and the only token instructions it ever sends: TransferChecked, InitializeAccount3, GetAccountDataSize
//! and, for the devnet faucet, InitializeMint2 and MintTo.
use crate::{err, state::*, E_ACCOUNTS, E_MINT};
use solana_program::{
    account_info::AccountInfo,
    entrypoint::ProgramResult,
    instruction::{AccountMeta, Instruction},
    program::{get_return_data, invoke, invoke_signed},
    program_error::ProgramError,
    pubkey,
    pubkey::Pubkey,
};

pub const TOKEN: Pubkey = pubkey!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
pub const TOKEN_2022: Pubkey = pubkey!("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");
pub const MINT_LEN: usize = 82;
pub const ACCOUNT_LEN: usize = 165;
const MULTISIG_LEN: usize = 355; // Token-2022 never gives a mint or an account with extensions this length

pub struct Mint { pub decimals: u8, pub t22: bool }

/// The mint, checked: `token` is SPL Token or Token-2022 and owns it, and it is an initialised mint. With `funding`
/// (money is about to enter in this mint) a Token-2022 mint's extensions are walked too, see `extensions_ok`. Money
/// leaving is never held to that: what is in escrow must always be able to go out.
pub fn mint_of(mint: &AccountInfo, token: &AccountInfo, funding: bool) -> Result<Mint, ProgramError> {
    let t22 = *token.key == TOKEN_2022;
    if (!t22 && *token.key != TOKEN) || mint.owner != token.key { return Err(err(E_MINT)); }
    let d = mint.try_borrow_data()?;
    let extended = t22 && d.len() > ACCOUNT_LEN && d.len() != MULTISIG_LEN && d[ACCOUNT_LEN] == 1; // account type 1: a mint
    if !(d.len() == MINT_LEN || extended) || d[45] != 1 { return Err(err(E_MINT)); }
    if funding && extended && !extensions_ok(&d[ACCOUNT_LEN + 1..]) { return Err(err(E_MINT)); }
    Ok(Mint { decimals: d[44], t22 })
}

/// A Token-2022 mint's extensions are a list of (type u16, length u16, value). Refused: NonTransferable (9), a
/// DefaultAccountState (6) that is not "initialized" (new accounts would start frozen), a TransferHook (14) with a
/// program set (it would run on every transfer and could refuse a payout), and a TransferFeeConfig (1) whose older or
/// newer fee takes anything (a fee now, or one already scheduled). Every other extension is accepted, among them
/// PermanentDelegate, ConfidentialTransfer and a transfer hook or a transfer fee that is configured but empty.
fn extensions_ok(mut t: &[u8]) -> bool {
    while t.len() >= 4 {
        let (ty, len) = (u16::from_le_bytes([t[0], t[1]]), u16::from_le_bytes([t[2], t[3]]) as usize);
        if ty == 0 { break; } // uninitialised: the end of the list
        let Some(v) = t.get(4..4 + len) else { return false };
        let refused = match ty {
            1 => len != 108 || charges(&v[72..90]) || charges(&v[90..108]),
            6 => *v != [1u8],
            9 => true,
            14 => len != 64 || v[32..64] != [0u8; 32],
            _ => false,
        };
        if refused { return false; }
        t = &t[4 + len..];
    }
    true
}
/// A TransferFee (epoch u64, maximum_fee u64, basis_points u16) that takes more than nothing.
fn charges(fee: &[u8]) -> bool { fee[8..16] != [0u8; 8] && fee[16..18] != [0u8; 2] }

/// (mint, owner, amount) of an initialised token account of the program `token`; None for anything else.
pub fn token_account(a: &AccountInfo, token: &Pubkey) -> Option<(Pubkey, Pubkey, u64)> {
    if a.owner != token { return None; }
    let d = a.try_borrow_data().ok()?;
    let extended = *token == TOKEN_2022 && d.len() > ACCOUNT_LEN && d.len() != MULTISIG_LEN && d[ACCOUNT_LEN] == 2; // account type 2
    if !(d.len() == ACCOUNT_LEN || extended) || d[108] == 0 { return None; }
    Some((key_at(&d, 0), key_at(&d, 32), u64_at(&d, 64)))
}
/// What a token account holds; `bad` when it is not a token account of `token`.
pub fn amount_of(a: &AccountInfo, token: &Pubkey, bad: u32) -> Result<u64, ProgramError> {
    token_account(a, token).map(|t| t.2).ok_or_else(|| err(bad))
}
/// Whether `a` is a token account of `mint` that belongs to `owner`.
pub fn is_owned(a: &AccountInfo, token: &Pubkey, mint: &Pubkey, owner: &Pubkey) -> bool {
    matches!(token_account(a, token), Some((m, o, _)) if m == *mint && o == *owner)
}

/// The size a token account of this mint needs: 165 bytes for SPL Token, what GetAccountDataSize says for Token-2022
/// (a mint's extensions can require account extensions).
fn account_size<'a>(mint: &AccountInfo<'a>, token: &AccountInfo<'a>, m: &Mint) -> Result<usize, ProgramError> {
    if !m.t22 { return Ok(ACCOUNT_LEN); }
    let ix = Instruction { program_id: *token.key, data: vec![21], accounts: vec![AccountMeta::new_readonly(*mint.key, false)] };
    invoke(&ix, &[mint.clone(), token.clone()])?;
    match get_return_data() {
        Some((by, size)) if by == *token.key && size.len() == 8 => Ok(u64::from_le_bytes(size.try_into().unwrap()) as usize),
        _ => Err(err(E_MINT)),
    }
}

/// The token account at a PDA of this program (`seeds`, bump included; the caller has checked `acct` is that
/// address): of `mint`, owned by ["auth"]. Created on first use (rent from `payer`), then checked to be exactly that.
#[allow(clippy::too_many_arguments)]
pub fn ensure_token_pda<'a>(payer: &AccountInfo<'a>, acct: &AccountInfo<'a>, mint: &AccountInfo<'a>, auth: &AccountInfo<'a>, token: &AccountInfo<'a>,
                            sys: &AccountInfo<'a>, m: &Mint, seeds: &[&[u8]]) -> ProgramResult {
    if acct.data_is_empty() {
        create_pda(payer, acct, sys, token.key, account_size(mint, token, m)?, seeds)?;
        let mut d = vec![18u8]; d.extend_from_slice(auth.key.as_ref()); // InitializeAccount3: the owner is in the data
        invoke(&Instruction { program_id: *token.key, data: d, accounts: vec![AccountMeta::new(*acct.key, false), AccountMeta::new_readonly(*mint.key, false)] },
               &[acct.clone(), mint.clone(), token.clone()])?;
    }
    if !is_owned(acct, token.key, mint.key, auth.key) { return Err(err(E_ACCOUNTS)); }
    Ok(())
}

/// TransferChecked: `amount` of `mint` from `from` to `to`. With `auth_bump` the ["auth"] PDA signs (money leaving a
/// vault or a Balance); without it `authority` signed the transaction itself (a wallet funding a job).
#[allow(clippy::too_many_arguments)]
pub fn transfer<'a>(token: &AccountInfo<'a>, from: &AccountInfo<'a>, mint: &AccountInfo<'a>, to: &AccountInfo<'a>, authority: &AccountInfo<'a>,
                    amount: u64, decimals: u8, auth_bump: Option<u8>) -> ProgramResult {
    let mut d = vec![12u8]; d.extend_from_slice(&amount.to_le_bytes()); d.push(decimals);
    let ix = Instruction { program_id: *token.key, data: d,
        accounts: vec![AccountMeta::new(*from.key, false), AccountMeta::new_readonly(*mint.key, false), AccountMeta::new(*to.key, false),
                       AccountMeta::new_readonly(*authority.key, true)] };
    let infos = [from.clone(), mint.clone(), to.clone(), authority.clone(), token.clone()];
    match auth_bump { Some(b) => invoke_signed(&ix, &infos, &[&[b"auth", &[b]]]), None => invoke(&ix, &infos) }
}

/// Devnet faucet: creates the test-USDC mint ["mint"] (SPL Token, 6 decimals, mint authority ["auth"], no freeze authority).
pub fn init_mint<'a>(payer: &AccountInfo<'a>, mint: &AccountInfo<'a>, auth: &AccountInfo<'a>, token: &AccountInfo<'a>, sys: &AccountInfo<'a>,
                     bump: u8) -> ProgramResult {
    create_pda(payer, mint, sys, &TOKEN, MINT_LEN, &[b"mint", &[bump]])?;
    let mut d = vec![20u8, 6]; d.extend_from_slice(auth.key.as_ref()); d.push(0); // InitializeMint2
    invoke(&Instruction { program_id: TOKEN, data: d, accounts: vec![AccountMeta::new(*mint.key, false)] }, &[mint.clone(), token.clone()])
}
/// Devnet faucet: MintTo, signed by ["auth"].
pub fn mint_to<'a>(token: &AccountInfo<'a>, mint: &AccountInfo<'a>, to: &AccountInfo<'a>, auth: &AccountInfo<'a>, amount: u64, auth_bump: u8) -> ProgramResult {
    let mut d = vec![7u8]; d.extend_from_slice(&amount.to_le_bytes());
    invoke_signed(&Instruction { program_id: TOKEN, data: d,
        accounts: vec![AccountMeta::new(*mint.key, false), AccountMeta::new(*to.key, false), AccountMeta::new_readonly(*auth.key, true)] },
        &[mint.clone(), to.clone(), auth.clone(), token.clone()], &[&[b"auth", &[auth_bump]]])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tlv(ty: u16, value: &[u8]) -> Vec<u8> {
        let mut out = ty.to_le_bytes().to_vec();
        out.extend_from_slice(&(value.len() as u16).to_le_bytes());
        out.extend_from_slice(value);
        out
    }
    /// A TransferFeeConfig value: two authorities, the withheld amount, then the older and the newer fee.
    fn fee_config(older: (u64, u16), newer: (u64, u16)) -> Vec<u8> {
        let mut v = vec![7u8; 72];
        for (max, bps) in [older, newer] {
            v.extend_from_slice(&500u64.to_le_bytes());
            v.extend_from_slice(&max.to_le_bytes());
            v.extend_from_slice(&bps.to_le_bytes());
        }
        v
    }

    #[test]
    fn the_extensions_that_could_block_or_tax_a_payout_are_refused_and_no_other() {
        let hook = |program: u8| { let mut v = vec![9u8; 32]; v.extend_from_slice(&[program; 32]); v };
        let ok: Vec<Vec<u8>> = vec![
            vec![],
            tlv(1, &fee_config((0, 0), (0, 0))),
            tlv(1, &fee_config((1_000_000, 0), (0, 250))),        // a cap with no rate, a rate with no cap: nothing is taken
            tlv(6, &[1]),
            tlv(14, &hook(0)),
            tlv(12, &[5u8; 32]),                                    // PermanentDelegate
            tlv(4, &[5u8; 65]),                                     // ConfidentialTransferMint
            tlv(3, &[5u8; 32]),                                     // MintCloseAuthority
            tlv(999, &[1, 2, 3]),                                   // an extension this program has never heard of
            [tlv(1, &fee_config((0, 0), (0, 0))), tlv(4, &[5u8; 65]), tlv(12, &[5u8; 32]), tlv(14, &hook(0)), tlv(16, &[0u8; 129]), vec![0u8; 12]].concat(),
        ];
        for t in &ok { assert!(extensions_ok(t), "{t:?}"); }
        let refused: Vec<Vec<u8>> = vec![
            tlv(9, &[]),                                            // NonTransferable
            tlv(6, &[2]),                                           // new accounts start frozen
            tlv(6, &[0]),
            tlv(14, &hook(1)),                                      // a transfer hook program
            tlv(1, &fee_config((1, 1), (0, 0))),                    // a fee now
            tlv(1, &fee_config((0, 0), (5_000_000, 100))),          // a fee scheduled
            tlv(1, &[0u8; 107]),                                    // not a TransferFeeConfig at all
            tlv(14, &[0u8; 63]),
            vec![12, 0, 200, 0, 1, 2, 3],                           // a length that runs past the data
            [tlv(12, &[5u8; 32]), tlv(4, &[5u8; 65]), tlv(9, &[])].concat(),   // a refused one after accepted ones
        ];
        for t in &refused { assert!(!extensions_ok(t), "{t:?}"); }
        // what follows the uninitialised type 0 is padding, never read
        assert!(extensions_ok(&[tlv(12, &[5u8; 32]), vec![0, 0, 0, 0], tlv(9, &[])].concat()));
    }

    fn account<'a>(key: &'a Pubkey, owner: &'a Pubkey, lamports: &'a mut u64, data: &'a mut [u8]) -> AccountInfo<'a> {
        AccountInfo::new(key, false, false, lamports, data, owner, false, 0)
    }

    #[test]
    fn a_mint_is_an_initialised_mint_of_the_token_program_passed() {
        let (k, mut l, mut none) = (Pubkey::new_unique(), 1u64, [0u8; 0]);
        let (mut l2, mut none2) = (1u64, [0u8; 0]);
        let classic = account(&TOKEN, &TOKEN, &mut l, &mut none);
        let t22 = account(&TOKEN_2022, &TOKEN_2022, &mut l2, &mut none2);
        let mint = |len: usize, kind: u8| { let mut d = vec![0u8; len]; d[44] = 6; d[45] = 1; if len > ACCOUNT_LEN { d[ACCOUNT_LEN] = kind; } d };
        let check = |owner: &Pubkey, mut d: Vec<u8>, token: &AccountInfo, funding: bool| {
            let mut lam = 1u64;
            mint_of(&account(&k, owner, &mut lam, &mut d), token, funding).map(|m| (m.decimals, m.t22)).ok()
        };
        assert_eq!(check(&TOKEN, mint(82, 0), &classic, true), Some((6, false)));
        assert_eq!(check(&TOKEN_2022, mint(82, 0), &t22, true), Some((6, true)));
        assert_eq!(check(&TOKEN_2022, mint(300, 1), &t22, true), Some((6, true)));
        // the other program; a token account (type 2) or a multisig-sized account; an SPL Token account that is too long; not initialised
        assert_eq!(check(&TOKEN, mint(82, 0), &t22, true), None);
        assert_eq!(check(&TOKEN_2022, mint(82, 0), &classic, true), None);
        assert_eq!(check(&TOKEN_2022, mint(300, 2), &t22, true), None);
        assert_eq!(check(&TOKEN_2022, mint(355, 1), &t22, true), None);
        assert_eq!(check(&TOKEN_2022, mint(165, 0), &t22, true), None);
        assert_eq!(check(&TOKEN, mint(300, 1), &classic, true), None);
        assert_eq!(check(&TOKEN, vec![0u8; 82], &classic, true), None);
        assert_eq!(check(&k, mint(82, 0), &classic, true), None);
        // extensions are walked only when money is about to enter
        let mut bad = mint(166, 1);
        bad.extend_from_slice(&tlv(9, &[]));
        assert_eq!(check(&TOKEN_2022, bad.clone(), &t22, true), None);
        assert_eq!(check(&TOKEN_2022, bad, &t22, false), Some((6, true)));
    }

    #[test]
    fn a_token_account_is_read_only_from_its_own_program() {
        let (k, mint, owner) = (Pubkey::new_unique(), Pubkey::new_unique(), Pubkey::new_unique());
        let tok = |len: usize, state: u8, kind: u8| {
            let mut d = vec![0u8; len];
            d[0..32].copy_from_slice(mint.as_ref()); d[32..64].copy_from_slice(owner.as_ref()); d[64..72].copy_from_slice(&77u64.to_le_bytes());
            d[108] = state; if len > ACCOUNT_LEN { d[ACCOUNT_LEN] = kind; }
            d
        };
        let read = |program: &Pubkey, mut d: Vec<u8>, token: &Pubkey| { let mut lam = 1u64; token_account(&account(&k, program, &mut lam, &mut d), token) };
        assert_eq!(read(&TOKEN, tok(165, 1, 0), &TOKEN), Some((mint, owner, 77)));
        assert_eq!(read(&TOKEN_2022, tok(165, 2, 0), &TOKEN_2022), Some((mint, owner, 77)));      // frozen is still an account
        assert_eq!(read(&TOKEN_2022, tok(182, 1, 2), &TOKEN_2022), Some((mint, owner, 77)));
        assert_eq!(read(&TOKEN, tok(165, 1, 0), &TOKEN_2022), None);
        assert_eq!(read(&TOKEN, tok(165, 0, 0), &TOKEN), None);                                    // not initialised
        assert_eq!(read(&TOKEN, tok(182, 1, 2), &TOKEN), None);                                    // SPL Token accounts are 165 bytes
        assert_eq!(read(&TOKEN_2022, tok(182, 1, 1), &TOKEN_2022), None);                          // a mint
        assert_eq!(read(&TOKEN_2022, tok(355, 1, 2), &TOKEN_2022), None);
        assert_eq!(read(&TOKEN, vec![0u8; 82], &TOKEN), None);
        let mut lam = 1u64;
        let mut d = tok(165, 1, 0);
        let a = account(&k, &TOKEN, &mut lam, &mut d);
        assert!(is_owned(&a, &TOKEN, &mint, &owner) && !is_owned(&a, &TOKEN, &owner, &owner) && !is_owned(&a, &TOKEN, &mint, &mint));
    }
}
