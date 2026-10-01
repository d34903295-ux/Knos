"""`knos prove --job JOB --jwt-file PATH`: pay a GitHub-posted job with the GitHub Actions OIDC token that proves it.

The token is minted by the `attest` job of .github/workflows/prove.yml (audience `knos:<job id hex>`) after the
`check` job ran the repo's .knos/proof.toml checks on the pull request. The escrow verifies the token's signature and
claims on chain (market.prove_github); this module only refuses early, with a clear reason, a token the chain would
refuse anyway, so a wrong audience or an expired token costs no transaction.
"""

from __future__ import annotations

import base64
import json
import time

ISSUER = "https://token.actions.githubusercontent.com"
WORKFLOW = "drexthealpha/Knos/.github/workflows/prove.yml@refs/tags/"


def claims(jwt: str) -> dict:
    """The token's payload, unverified (the chain verifies the signature)."""
    parts = jwt.strip().split(".")
    if len(parts) != 3:
        raise ValueError("not a JWT (expected header.payload.signature)")
    body = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        got = json.loads(base64.urlsafe_b64decode(body))
    except ValueError:
        raise ValueError("the JWT payload is not base64url JSON") from None
    if not isinstance(got, dict):
        raise ValueError("the JWT payload is not a JSON object")
    return got


def precheck(c: dict, job_id: bytes, now: float | None = None) -> None:
    """Raise ValueError naming the first claim the on-chain check would refuse."""
    now = time.time() if now is None else now
    want = "knos:" + job_id.hex()
    aud = c.get("aud")
    if (aud if isinstance(aud, list) else [aud]) != [want]:
        raise ValueError(f"audience is {aud!r}, not {want!r}: this token was minted for another job")
    if c.get("iss") != ISSUER:
        raise ValueError(f"issuer is {c.get('iss')!r}, not GitHub Actions ({ISSUER})")
    exp = c.get("exp")
    if not isinstance(exp, (int, float)) or exp <= now:
        raise ValueError("the token has expired: mint a new one (they last minutes)")
    ref = str(c.get("job_workflow_ref", ""))
    if not ref.startswith(WORKFLOW):
        raise ValueError(f"job_workflow_ref is {ref!r}: the token must come from Knos's prove.yml at a release tag")


def prove(ledger, payer, job_id: bytes, jwt: str) -> tuple[str, str]:
    """Pre-check, then hand the token to the escrow. Returns the two verify transaction signatures."""
    precheck(claims(jwt), job_id)
    from . import market
    fn = getattr(market, "prove_github", None)
    if fn is None:
        raise RuntimeError("this knos has no on-chain GitHub verification yet (market.prove_github)")
    return fn(ledger, payer, job_id, jwt)
