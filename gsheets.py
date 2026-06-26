"""Minimal Google Sheets client for the server-side one-way mirror.

Authenticates with a **service-account** JSON key (the kind you download from the
Google Cloud console) and appends rows to a spreadsheet you've shared with the
service account's email. Deliberately dependency-light: the service-account JSON
carries an RSA private key, so we mint the OAuth token ourselves (sign a JWT with
`cryptography`, exchange it for an access token) and call the Sheets REST API with
`httpx` — both already dependencies. No `google-api-python-client`.

Pure module: only stdlib + cryptography + httpx, no app import (like store.py /
skills.py). The caller (app.py) reads config and feeds in the credential path,
spreadsheet id, tab and row values.

Security note: the credential grants access only to spreadsheets explicitly shared
with the service account — never the whole Google account.
"""
import base64
import json
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

SCOPE = "https://www.googleapis.com/auth/spreadsheets"
_JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

# Cached access tokens keyed by client_email -> (token, expiry_epoch).
_token_cache: dict[str, tuple[str, float]] = {}
# Tabs we've already ensured a header row on, keyed by (sheet_id, tab).
_header_done: set[tuple[str, str]] = set()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def load_service_account(path: str) -> dict:
    """Parse + validate a service-account JSON key file."""
    with open(path, "r", encoding="utf-8") as f:
        sa = json.load(f)
    if not (sa.get("client_email") and sa.get("private_key")):
        raise ValueError("Not a service-account key (missing client_email/private_key)")
    sa.setdefault("token_uri", "https://oauth2.googleapis.com/token")
    return sa


def service_account_email(path: str) -> str | None:
    """The service account's email — shown in the dashboard so the user knows which
    address to share their Sheet with. None if the file is missing/invalid."""
    try:
        return load_service_account(path).get("client_email")
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _signed_jwt(sa: dict) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": sa["client_email"],
        "scope": SCOPE,
        "aud": sa["token_uri"],
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    ).encode("ascii")
    key = serialization.load_pem_private_key(sa["private_key"].encode("utf-8"), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return signing_input.decode("ascii") + "." + _b64url(signature)


async def _access_token(sa: dict) -> str:
    """A cached OAuth access token for the service account (re-minted ~5 min before
    it expires)."""
    email = sa["client_email"]
    cached = _token_cache.get(email)
    if cached and cached[1] - 300 > time.time():
        return cached[0]
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            sa["token_uri"],
            data={"grant_type": _JWT_GRANT, "assertion": _signed_jwt(sa)},
        )
        r.raise_for_status()
        body = r.json()
    token = body["access_token"]
    _token_cache[email] = (token, time.time() + int(body.get("expires_in", 3600)))
    return token


_API = "https://sheets.googleapis.com/v4/spreadsheets"


async def _ensure_header(cred_path: str, sa: dict, sheet_id: str, tab: str, header: list) -> None:
    """Write the header row once per server run if the tab's first row is empty."""
    key = (sheet_id, tab)
    if key in _header_done:
        return
    token = await _access_token(sa)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{_API}/{sheet_id}/values/{tab}!1:1", headers=headers)
        r.raise_for_status()
        if not r.json().get("values"):
            await client.put(
                f"{_API}/{sheet_id}/values/{tab}!A1",
                headers=headers,
                params={"valueInputOption": "RAW"},
                json={"values": [header]},
            )
    _header_done.add(key)


async def append_row(cred_path: str, sheet_id: str, tab: str, values: list,
                     header: list | None = None) -> None:
    """Append one row to the sheet (optionally writing a header row first)."""
    sa = load_service_account(cred_path)
    if header:
        await _ensure_header(cred_path, sa, sheet_id, tab, header)
    token = await _access_token(sa)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{_API}/{sheet_id}/values/{tab}!A1:append",
            headers={"Authorization": f"Bearer {token}"},
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"values": [values]},
        )
        r.raise_for_status()
