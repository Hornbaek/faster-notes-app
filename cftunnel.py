"""Cloudflare Tunnel provisioning — make the home server reachable from anywhere.

Two halves, both dependency-light (only `httpx`, already a dep), no app import:

1. `ensure_cloudflared()` — download the official `cloudflared` connector binary on
   first use into the data dir (kept out of the PyInstaller bundle; cloudflared ships
   frequent updates). Mirrors the "download the model on first run" pattern.

2. An async Cloudflare REST client that provisions a **remotely-managed** named tunnel
   (`config_src: "cloudflare"`): create the tunnel, point its ingress at the local
   HTTPS service, and add the proxied DNS record. Idempotent so re-enabling reuses the
   existing tunnel. The connector runs via runner.py (subprocess with the tunnel token).

Security: the tunnel routes the public hostname only to `https://localhost:8766` (the
phone bridge + PWA). The control plane (`/api/*`) is blocked for proxied requests by
app.restrict_control_plane, so it never rides the tunnel. The CF API token + tunnel
token are stored server-side (config), never sent to the phone.
"""
import os

import httpx

import paths

API_BASE = "https://api.cloudflare.com/client/v4"
DOWNLOAD_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-windows-amd64.exe"
)
LOCAL_ORIGIN = "https://localhost:8766"  # the phone bridge + PWA (self-signed TLS)


class CloudflareError(Exception):
    """A Cloudflare API call returned success:false (or a transport error)."""


# ── connector binary ──────────────────────────────────────────────────────────

def ensure_cloudflared() -> str:
    """Return the path to cloudflared.exe, downloading it on first use."""
    exe = paths.CLOUDFLARED_EXE
    if os.path.exists(exe):
        return exe
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    tmp = exe + ".download"
    with httpx.stream("GET", DOWNLOAD_URL, follow_redirects=True, timeout=180.0) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(65536):
                f.write(chunk)
    os.replace(tmp, exe)  # atomic — never leave a half-written exe
    return exe


# ── Cloudflare REST client ────────────────────────────────────────────────────

async def _cf(method: str, path: str, token: str,
              json_body: dict | None = None, params: dict | None = None):
    """Call the Cloudflare API and unwrap the {success, result, errors} envelope."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.request(
                method, f"{API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=json_body, params=params,
            )
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CloudflareError(f"{method} {path}: {exc}") from exc
    if not data.get("success"):
        errs = "; ".join(str(e.get("message", e)) for e in data.get("errors", []))
        raise CloudflareError(f"{method} {path} failed: {errs or r.text}")
    return data.get("result")


async def verify_token(token: str) -> dict:
    """Validate the API token. Uses /user/tokens/verify, which works for any token
    scope (unlike listing /accounts, which a Tunnel+DNS-scoped token can't do)."""
    res = await _cf("GET", "/user/tokens/verify", token)
    if not res or res.get("status") != "active":
        raise CloudflareError("API token is not active")
    return res


async def list_zones(token: str) -> list[dict]:
    """The user's domains on Cloudflare. Each zone embeds its account id, so we never
    need the account-list permission (which a Tunnel/DNS token lacks)."""
    zones = await _cf("GET", "/zones", token, params={"per_page": 50})
    return [
        {"id": z["id"], "name": z["name"], "account_id": (z.get("account") or {}).get("id")}
        for z in (zones or [])
    ]


async def account_for_zone(token: str, zone_id: str) -> str:
    """Resolve the account id from a zone (the zone object carries account.id)."""
    zone = await _cf("GET", f"/zones/{zone_id}", token)
    acct = (zone.get("account") or {}).get("id") if zone else None
    if not acct:
        raise CloudflareError("Could not resolve the Cloudflare account for that zone")
    return acct


async def ensure_tunnel(token: str, account_id: str, name: str,
                        existing_id: str | None = None) -> tuple[str, str]:
    """Create (or reuse) a remotely-managed tunnel. Returns (tunnel_id, connector_token)."""
    tunnel_id = existing_id
    if tunnel_id:
        try:
            await _cf("GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}", token)
        except CloudflareError:
            tunnel_id = None  # stale/deleted — recreate
    if not tunnel_id:
        result = await _cf(
            "POST", f"/accounts/{account_id}/cfd_tunnel", token,
            json_body={"name": name, "config_src": "cloudflare"},
        )
        tunnel_id = result["id"]
    connector_token = await _cf(
        "GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token", token)
    return tunnel_id, connector_token


async def configure_ingress(token: str, account_id: str, tunnel_id: str, hostname: str) -> None:
    """Route the public hostname to the local bridge; everything else → 404.
    noTLSVerify accepts the local self-signed cert."""
    config = {"ingress": [
        {"hostname": hostname, "service": LOCAL_ORIGIN, "originRequest": {"noTLSVerify": True}},
        {"service": "http_status:404"},
    ]}
    await _cf("PUT", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
              token, json_body={"config": config})


async def ensure_dns(token: str, zone_id: str, hostname: str, tunnel_id: str) -> None:
    """Proxied CNAME hostname → <id>.cfargotunnel.com (create or update)."""
    content = f"{tunnel_id}.cfargotunnel.com"
    body = {"type": "CNAME", "name": hostname, "content": content, "proxied": True}
    existing = await _cf("GET", f"/zones/{zone_id}/dns_records", token,
                         params={"type": "CNAME", "name": hostname})
    if existing:
        await _cf("PUT", f"/zones/{zone_id}/dns_records/{existing[0]['id']}", token, json_body=body)
    else:
        await _cf("POST", f"/zones/{zone_id}/dns_records", token, json_body=body)


async def provision(token: str, account_id: str, zone_id: str, hostname: str,
                    name: str = "fasternotes", existing_id: str | None = None) -> dict:
    """End-to-end: create/reuse the tunnel, set ingress, add DNS. Returns the bits to
    persist (tunnel_id, tunnel_token, hostname)."""
    tunnel_id, tunnel_token = await ensure_tunnel(token, account_id, name, existing_id)
    await configure_ingress(token, account_id, tunnel_id, hostname)
    await ensure_dns(token, zone_id, hostname, tunnel_id)
    return {"tunnel_id": tunnel_id, "tunnel_token": tunnel_token, "hostname": hostname}


async def delete_tunnel(token: str, account_id: str, tunnel_id: str) -> None:
    """Full teardown: drop live connections then delete the tunnel (best-effort)."""
    try:
        await _cf("DELETE", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/connections", token)
    except CloudflareError:
        pass
    await _cf("DELETE", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}", token)
