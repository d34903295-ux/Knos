"""Replay real GitHub tokens against the real builds, off chain, before anything is deployed.

    python scripts/replay_tokens.py fund.txt proof.txt claim.txt [--oidc PATH.so] [--pay PATH.so] [--jwks FILE]

Each file holds one GitHub Actions OIDC token, as a repository's workflow minted it (the `knos-fund:` / `knos-proof:`
/ `knos-claim:` / `knos-veto:` comment, or the artifact's token.txt). The script loads the real knos-oidc and
knos-pay builds (the ones that trust only GitHub's and GitLab's published keys) into LiteSVM, sets the chain's clock
to each token's issue time, and hands the token to the same relay the always-on worker runs. So a wrong claim name,
an audience the program refuses, or a token the verifier cannot read shows up here, in a second, and not on devnet.

Exit 0 only if every token was accepted. Tokens expire after five minutes on GitHub's side; the clock is set, not
read, so a captured token can be replayed at any time.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from knos.proof import ghrelay  # noqa: E402
from knos.settle import oidc, pay, relay  # noqa: E402


def replay(tokens: list[str], oidc_so: Path, pay_so: Path, jwks: dict | None = None, say=print) -> bool:
    from _settle import Chain, ChainLedger
    chain = Chain(pay_build=str(Path(pay_so).resolve()), oidc_build=str(Path(oidc_so).resolve()))
    ledger = ChainLedger(chain)
    assert chain.send([pay.init_faucet_ix(chain.payer.pubkey())]), chain.err
    good = True
    for jwt in tokens:
        claims = relay.claims_of(jwt)
        aud = claims["aud"] if isinstance(claims["aud"], str) else claims["aud"][0]
        clock = chain.svm.get_clock()
        clock.unix_timestamp = max(int(claims.get("iat", 0)) + 5, int(clock.unix_timestamp) + 1)
        chain.svm.set_clock(clock)
        r = relay.submit(ledger, chain.payer, jwt, jwks, now=chain.now())
        say(f"{'ok  ' if r.get('ok') else 'FAIL'} {aud[:70]}")
        say(f"     workflow {claims.get('job_workflow_ref')} at {claims.get('job_workflow_sha')}; "
            f"runner {claims.get('runner_environment')}; token {len(jwt)} bytes")
        if r.get("ok"):
            say(f"     {ghrelay.note(r)}")
        else:
            good = False
            say(f"     refused: {r.get('why')}")
    return good


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tokens", nargs="+", type=Path)
    ap.add_argument("--oidc", type=Path, default=ROOT / "programs" / "target" / "deploy" / "knos_oidc.so")
    ap.add_argument("--pay", type=Path, default=ROOT / "programs" / "target" / "deploy" / "knos_pay.so")
    ap.add_argument("--jwks", type=Path, help="GitHub's JWKS as a file (default: fetched from GitHub)")
    a = ap.parse_args(argv)
    jwks = {oidc.GITHUB: json.loads(a.jwks.read_text(encoding="utf-8"))} if a.jwks else None
    tokens = []
    for f in a.tokens:
        text = f.read_text(encoding="utf-8")
        found = ghrelay.TOKEN.search(text)
        tokens.append(found.group(2) if found else text.strip())
    return 0 if replay(tokens, a.oidc, a.pay, jwks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
