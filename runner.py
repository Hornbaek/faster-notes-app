"""In-process server runner.

Runs BOTH servers in one process (no `python -m uvicorn` subprocess — that breaks
in a frozen exe):
  • 127.0.0.1:8765  — dashboard + /api/* control plane (loopback only)
  • 0.0.0.0:8766    — phone PWA + bridge, TLS (LAN-facing)

Also: file logging, and self-signed cert generation that refreshes when the LAN IP
changes. Imported by tray.py (the app entry) and by start.py (dev).
"""
import asyncio
import datetime as dt
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
from ipaddress import IPv4Address

import uvicorn

import paths
import app as appmod
import cftunnel

log = logging.getLogger("faster_notes")

MDNS_HOST = "fasternotes.local"


def setup_logging() -> None:
    if getattr(setup_logging, "_done", False):
        return
    setup_logging._done = True
    root = logging.getLogger("faster_notes")
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        paths.LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr:  # console handler only when a console exists (dev)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        root.addHandler(ch)
    # Route uvicorn's own logs into the same file.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        ul = logging.getLogger(name)
        ul.handlers = [fh]
        ul.propagate = False


# ── Certificate ──────────────────────────────────────────────────────────────

def generate_cert(ip: str) -> None:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    log.info("Generating self-signed certificate for %s", ip)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Faster Notes")])
    sans = [
        x509.DNSName("localhost"),
        x509.DNSName("fasternotes.local"),
        x509.IPAddress(IPv4Address("127.0.0.1")),
    ]
    try:
        sans.append(x509.IPAddress(IPv4Address(ip)))
    except Exception:
        pass
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.utcnow())
        .not_valid_after(dt.datetime.utcnow() + dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(paths.CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(paths.KEY_FILE, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))


def ensure_cert(ip: str) -> None:
    """(Re)generate the cert if it's missing or the LAN IP has changed."""
    cfg = appmod.read_config()
    have = os.path.exists(paths.CERT_FILE) and os.path.exists(paths.KEY_FILE)
    if not have or cfg.get("cert_ip") != ip:
        generate_cert(ip)
        cfg["cert_ip"] = ip
        appmod.write_config(cfg)
    else:
        log.info("Using existing certificate (%s)", ip)


# ── mDNS: a stable hostname that survives IP changes ─────────────────────────
# Advertise `fasternotes.local` -> current IP. The phone pairs to the hostname
# once and keeps working when the laptop moves networks (work <-> home), because
# (a) the cert SAN already includes fasternotes.local and (b) the watcher below
# re-points the name at the new IP. Resolves natively on iOS/macOS/Windows;
# Android browsers are hit-or-miss, so the IP picker stays as a fallback.

_zc = None
_mdns_info = None


def start_mdns(ip: str) -> None:
    global _zc, _mdns_info
    try:
        from zeroconf import Zeroconf, ServiceInfo
        _zc = Zeroconf()
        _mdns_info = ServiceInfo(
            "_https._tcp.local.",
            "Faster Notes._https._tcp.local.",
            addresses=[socket.inet_aton(ip)],
            port=appmod.HTTPS_PORT,
            server=f"{MDNS_HOST}.",
        )
        _zc.register_service(_mdns_info)
        appmod.mdns_hostname = MDNS_HOST
        log.info("mDNS: %s -> %s", MDNS_HOST, ip)
    except Exception as exc:
        _zc = None
        log.warning("mDNS unavailable (%s) — phone pairing falls back to IP", exc)


def update_mdns(ip: str) -> None:
    if not _zc or not _mdns_info:
        return
    try:
        _mdns_info.addresses = [socket.inet_aton(ip)]
        _zc.update_service(_mdns_info)
        log.info("mDNS: %s -> %s (updated)", MDNS_HOST, ip)
    except Exception as exc:
        log.warning("mDNS update failed: %s", exc)


def _ip_watcher() -> None:
    """Re-point mDNS at the new IP when the network changes (e.g. work -> home)."""
    current = appmod.get_local_ip()
    while True:
        time.sleep(20)
        try:
            ip = appmod.get_local_ip()
        except Exception:
            continue
        if ip and ip != current and not ip.startswith("127."):
            log.info("LAN IP changed: %s -> %s", current, ip)
            current = ip
            update_mdns(ip)


# ── Cloudflare Tunnel connector (remote access) ───────────────────────────────
# Runs `cloudflared` as a managed subprocess so the phone can reach the server from
# anywhere. Modeled on the mDNS/_ip_watcher pattern: a daemon monitor thread keeps it
# alive, and the token is passed via the TUNNEL_TOKEN env var (not argv).

_cf_proc: "subprocess.Popen | None" = None
_cf_lock = threading.Lock()
_cf_token: "str | None" = None
_cf_want_running = False


def _cf_spawn_locked() -> None:
    """Launch cloudflared. Caller holds _cf_lock and has ensured the binary exists."""
    global _cf_proc
    env = dict(os.environ, TUNNEL_TOKEN=_cf_token or "")
    _cf_proc = subprocess.Popen(
        [paths.CLOUDFLARED_EXE, "tunnel", "--no-autoupdate", "run"],
        env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log.info("cloudflared started (pid %s)", _cf_proc.pid)


def start_cloudflared(token: str) -> None:
    """Start (or adopt) the connector with the given token. Idempotent; the monitor
    thread restarts it if it dies. Downloads the binary on first use."""
    global _cf_token, _cf_want_running
    cftunnel.ensure_cloudflared()  # may download (~slow first time) — outside the lock
    with _cf_lock:
        _cf_token = token
        _cf_want_running = True
        if _cf_proc and _cf_proc.poll() is None:
            return
        _cf_spawn_locked()


def stop_cloudflared() -> None:
    """Stop the connector and tell the monitor to leave it stopped."""
    global _cf_proc, _cf_want_running
    with _cf_lock:
        _cf_want_running = False
        proc, _cf_proc = _cf_proc, None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        log.info("cloudflared stopped")


def cloudflared_running() -> bool:
    with _cf_lock:
        return bool(_cf_proc and _cf_proc.poll() is None)


def _cloudflared_monitor() -> None:
    """Restart cloudflared if it exits while it's supposed to be running."""
    while True:
        time.sleep(5)
        with _cf_lock:
            if _cf_want_running and (not _cf_proc or _cf_proc.poll() is not None):
                log.warning("cloudflared not running — restarting")
                try:
                    _cf_spawn_locked()
                except Exception as exc:
                    log.error("cloudflared restart failed: %s", exc)


# ── Servers ──────────────────────────────────────────────────────────────────

def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


async def _serve() -> None:
    http = uvicorn.Server(uvicorn.Config(
        appmod.app, host="127.0.0.1", port=appmod.HTTP_PORT, log_config=None,
    ))
    https = uvicorn.Server(uvicorn.Config(
        appmod.app, host="0.0.0.0", port=appmod.HTTPS_PORT, log_config=None,
        ssl_certfile=paths.CERT_FILE, ssl_keyfile=paths.KEY_FILE,
    ))
    await asyncio.gather(http.serve(), https.serve())


def run() -> None:
    """Blocking: configure, then serve both ports until interrupted."""
    setup_logging()
    if port_in_use(appmod.HTTP_PORT) or port_in_use(appmod.HTTPS_PORT, "0.0.0.0"):
        raise RuntimeError(
            f"Port {appmod.HTTP_PORT} or {appmod.HTTPS_PORT} is already in use "
            "(is Faster Notes already running?)"
        )
    appmod.ensure_api_key()
    ip = appmod.get_local_ip()
    ensure_cert(ip)
    start_mdns(ip)
    threading.Thread(target=_ip_watcher, daemon=True).start()
    # Remote access via Cloudflare Tunnel: a monitor thread keeps the connector alive
    # whenever it's enabled (at startup here, or toggled later from the dashboard).
    threading.Thread(target=_cloudflared_monitor, daemon=True).start()
    _cfg = appmod.read_config()
    if _cfg.get("cloudflare_enabled") and _cfg.get("cloudflare_tunnel_token"):
        try:
            start_cloudflared(_cfg["cloudflare_tunnel_token"])
            log.info("Remote     https://%s", _cfg.get("cloudflare_hostname", "?"))
        except Exception as exc:
            log.error("cloudflared start failed: %s", exc)
    log.info("Dashboard  http://localhost:%s", appmod.HTTP_PORT)
    log.info("Phone      https://%s:%s   (or https://%s:%s)",
             ip, appmod.HTTPS_PORT, MDNS_HOST, appmod.HTTPS_PORT)
    asyncio.run(_serve())


if __name__ == "__main__":
    run()
