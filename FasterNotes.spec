# PyInstaller spec — one-folder build of the Faster Notes tray app.
# Build:  pyinstaller FasterNotes.spec
# Output: dist/FasterNotes/FasterNotes.exe
#
# The ML stack ships native libraries that PyInstaller can't discover by import
# analysis alone, so we collect_all() them explicitly. PyAV bundles FFmpeg, so no
# separate ffmpeg install is needed.
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "av",
            "tokenizers", "huggingface_hub", "zeroconf", "yaml"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["pystray._win32", "PIL._tkinter_finder"]

# Bundled read-only assets (resolved at runtime via paths.RESOURCE_DIR / _MEIPASS).
datas += [
    ("static", "static"),
    ("pwa/dist/client", "pwa/dist/client"),
    ("SKILL_token_optimized_v2.md", "."),
    ("projects.json", "."),
    ("skills", "skills"),          # bundled default skills (read-only defaults)
    ("connectors", "connectors"),  # bundled default output connectors (read-only defaults)
]

a = Analysis(
    ["tray.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FasterNotes",
    debug=False,
    strip=False,
    upx=False,
    console=False,        # windowed — no console window
    icon=None,            # drop an icon.ico here later if desired
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="FasterNotes",
)
