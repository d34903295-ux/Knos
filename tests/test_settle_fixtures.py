"""sdk/settle/fixtures.json is what the Python client produces today (so other clients can test against it), and the
program ids the client uses are the ones the programs are built with."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_fixtures_are_current():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "settle_fixtures.py"), "--check"], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr or r.stdout


def test_program_ids_agree_everywhere():
    ids = json.loads((ROOT / "programs" / "program_ids.json").read_text())
    assert json.loads((ROOT / "src" / "knos" / "settle" / "program_ids.json").read_text()) == ids
    pay_rs = (ROOT / "programs" / "knos_pay" / "src" / "lib.rs").read_text(encoding="utf-8")
    assert re.search(r'OIDC_ID: Pubkey = pubkey!\("(\w+)"\)', pay_rs).group(1) == ids["knos_oidc"]


def test_the_rotate_pin_is_one_commit_everywhere():
    """ROTATE_SHA in the verifier's binary, the commit keys.yml calls the rotate workflow at, and the pin
    `knos mainnet-check` looks for are the same 40 hex characters."""
    ids = json.loads((ROOT / "programs" / "program_ids.json").read_text())
    pins = (ROOT / "programs" / "knos_oidc" / "src" / "pins.rs").read_text(encoding="utf-8")
    sha = re.search(r'pub const ROTATE_SHA: &\[u8; 40\] = b"([0-9a-f]{40})";', pins).group(1)
    keys = (ROOT / ".github" / "workflows" / "keys.yml").read_text(encoding="utf-8")
    assert re.search(r"drexthealpha/knos-oidc-rotate/\.github/workflows/rotate\.yml@([0-9a-f]{40})", keys).group(1) == sha
    assert ids["rotate_sha"] == sha


def test_genesis_keys_are_the_issuers_key_sets_of_2_october():
    """Every key in the saved GitHub and GitLab JWKS is a genesis constant in pins.rs, and nothing else is."""
    import base64
    import hashlib
    pins = (ROOT / "programs" / "knos_oidc" / "src" / "pins.rs").read_text(encoding="utf-8").split("pub const ROTATE_REF")[0]
    want = set(re.findall(r'\((\d), h\("([0-9a-f]{64})"\)\)', pins))
    got = set()
    for issuer, name in ((0, "github_jwks_2026-10-02.json"), (1, "gitlab_jwks_2026-10-02.json")):
        for k in json.loads((ROOT / "tests" / "fixtures" / name).read_text())["keys"]:
            n = base64.urlsafe_b64decode(k["n"] + "=" * (-len(k["n"]) % 4)).lstrip(b"\0")
            got.add((str(issuer), hashlib.sha256(n).hexdigest()))
    assert got == want and len(got) == 7


def test_the_javascript_client_matches_the_python_client(tmp_path):
    """sdk/settle/index.js (what the web app loads) against the fixtures, and its transaction read back by solders."""
    import shutil

    import pytest
    if not shutil.which("node"):
        pytest.skip("needs node")
    out = tmp_path / "tx.bin"
    r = subprocess.run(["node", str(ROOT / "sdk" / "settle" / "test.mjs"), str(out)], capture_output=True, text=True, check=False)
    assert r.returncode == 0 and "checks match the Python client" in r.stdout, r.stderr or r.stdout
    from solders.transaction import Transaction
    msg = Transaction.from_bytes(out.read_bytes()).message
    fx = json.loads((ROOT / "sdk" / "settle" / "fixtures.json").read_text())
    assert str(msg.account_keys[0]) == fx["inputs"]["funder"] and len(msg.instructions) == 2
    fund = msg.instructions[1]
    assert str(msg.account_keys[fund.program_id_index]) == fx["programs"]["knos_pay"]
    assert bytes(fund.data).hex() == fx["instructions"]["pay.fund merge"]["data"]
    assert [str(msg.account_keys[i]) for i in fund.accounts] == [a["pubkey"] for a in fx["instructions"]["pay.fund merge"]["accounts"]]


def test_the_site_is_built_at_one_commit(tmp_path):
    import os

    import pytest
    if os.name == "nt":
        pytest.skip("the Pages build runs on ubuntu-latest")
    sha = "ab" * 20
    r = subprocess.run(["bash", str(ROOT / "scripts" / "build_site.sh"), str(tmp_path / "site"), sha], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    site = tmp_path / "site"
    assert {"index.html", "app.js", "front.js", "settle.js", "knos-claim.yml", "program_ids.json"} <= {p.name for p in site.iterdir()}
    front = (site / "front.js").read_text(encoding="utf-8")
    assert "KNOS_COMMIT_SHA" not in front and f"prove.yml@${{KNOS_SHA}}" in front and f'KNOS_SHA = "{sha}"' in front
    assert (site / "settle.js").read_bytes() == (ROOT / "sdk" / "settle" / "index.js").read_bytes()
    assert subprocess.run(["bash", str(ROOT / "scripts" / "build_site.sh"), str(tmp_path / "x"), "main"], capture_output=True).returncode != 0
