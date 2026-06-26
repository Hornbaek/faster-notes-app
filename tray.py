"""System-tray entry point for the installed app.

Owns the main thread with a tray icon; runs both servers on a background thread.
Menu: Open dashboard / Pair a phone / status / Quit.
"""
import logging
import os
import sys
import threading
import webbrowser

import paths
import runner
import app as appmod

log = logging.getLogger("faster_notes")

DASHBOARD_URL = f"http://localhost:{appmod.HTTP_PORT}"


def _make_icon_image():
    """Generate the tray icon at runtime (no binary asset needed)."""
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([3, 3, size - 3, size - 3], radius=16, fill=(123, 94, 248, 255))
    # simple white microphone glyph
    d.rounded_rectangle([26, 15, 38, 38], radius=6, fill=(255, 255, 255, 255))
    d.arc([21, 26, 43, 46], start=0, end=180, fill=(255, 255, 255, 255), width=3)
    d.line([32, 46, 32, 52], fill=(255, 255, 255, 255), width=3)
    d.line([26, 52, 38, 52], fill=(255, 255, 255, 255), width=3)
    return img


def _status_text(_item=None) -> str:
    return "Whisper: ready" if appmod.whisper_model is not None else "Whisper: loading…"


def _quit(icon, _item=None) -> None:
    # Stop the Cloudflare connector so it isn't orphaned (Windows doesn't kill
    # child processes when the parent exits), then drop the tray icon.
    try:
        runner.stop_cloudflared()
    except Exception:
        pass
    icon.stop()


def main() -> None:
    runner.setup_logging()
    # Windowed builds have no stdout/stderr — guard any stray print()/library writes.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    import pystray
    from pystray import MenuItem as Item

    def _serve():
        try:
            runner.run()
        except Exception:
            log.exception("Server failed to start")

    threading.Thread(target=_serve, daemon=True).start()

    icon = pystray.Icon(
        "FasterNotes", _make_icon_image(), "Faster Notes",
        menu=pystray.Menu(
            Item("Open dashboard", lambda i, it: webbrowser.open(DASHBOARD_URL), default=True),
            Item("Pair a phone", lambda i, it: webbrowser.open(DASHBOARD_URL + "/?pair=1")),
            pystray.Menu.SEPARATOR,
            Item(_status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            Item("Quit", _quit),
        ),
    )
    icon.run()


if __name__ == "__main__":
    # `FasterNotes.exe --server-only` runs the servers without the tray icon
    # (useful for headless debugging / running as a plain background process).
    if "--server-only" in sys.argv:
        runner.setup_logging()
        runner.run()
    else:
        main()
