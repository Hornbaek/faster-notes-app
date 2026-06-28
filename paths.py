"""Single source of truth for every filesystem path.

Splits **read-only bundled assets** (shipped inside the install dir / PyInstaller
bundle) from **writable runtime data** (which must live under %LOCALAPPDATA% once
installed, because an app folder under Program Files is read-only).

Override the data location with the FASTER_NOTES_DATA env var (handy for dev/tests).
"""
import os
import sys

APP_NAME = "FasterNotes"


def _resource_dir() -> str:
    """Where bundled read-only assets live (static/, PWA build, skill, projects)."""
    if getattr(sys, "frozen", False):
        # PyInstaller sets _MEIPASS for both one-file and one-folder builds.
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _data_dir() -> str:
    """Where writable runtime data lives (config, cert, history, uploads, logs)."""
    override = os.environ.get("FASTER_NOTES_DATA")
    if override:
        d = override
    else:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


RESOURCE_DIR = _resource_dir()
DATA_DIR = _data_dir()

MODELS_DIR = os.path.join(DATA_DIR, "models")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
SKILLS_DIR = os.path.join(DATA_DIR, "skills")  # user-authored / edited skills
CONNECTORS_DIR = os.path.join(DATA_DIR, "connectors")  # user-authored / edited output connectors
BIN_DIR = os.path.join(DATA_DIR, "bin")        # downloaded helper binaries (cloudflared)
MEDIA_DIR = os.path.join(DATA_DIR, "media")    # persisted recordings (kept for re-transcribe)
for _d in (MODELS_DIR, UPLOADS_DIR, LOGS_DIR, SKILLS_DIR, CONNECTORS_DIR, BIN_DIR, MEDIA_DIR):
    os.makedirs(_d, exist_ok=True)

CLOUDFLARED_EXE = os.path.join(BIN_DIR, "cloudflared.exe")

# Writable runtime data
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
NOTES_FILE = os.path.join(DATA_DIR, "notes.json")
ACTIVITY_FILE = os.path.join(DATA_DIR, "activity.json")
CERT_FILE = os.path.join(DATA_DIR, "cert.pem")
KEY_FILE = os.path.join(DATA_DIR, "key.pem")
LOG_FILE = os.path.join(LOGS_DIR, "server.log")
GSHEET_CRED_FILE = os.path.join(DATA_DIR, "gsheet-credentials.json")  # Google service-account key
# Projects are user-editable, so the live copy is writable (a frozen install's
# bundle dir is read-only). The bundled file below is just the first-run seed.
PROJECTS_FILE = os.path.join(DATA_DIR, "projects.json")

# Read-only bundled assets
STATIC_DIR = os.path.join(RESOURCE_DIR, "static")
PWA_DIR = os.path.join(RESOURCE_DIR, "pwa", "dist", "client")
SKILL_FILE = os.path.join(RESOURCE_DIR, "SKILL_token_optimized_v2.md")
BUNDLED_PROJECTS_FILE = os.path.join(RESOURCE_DIR, "projects.json")  # seed for PROJECTS_FILE
BUNDLED_SKILLS_DIR = os.path.join(RESOURCE_DIR, "skills")  # shipped default skills
BUNDLED_CONNECTORS_DIR = os.path.join(RESOURCE_DIR, "connectors")  # shipped default output connectors
