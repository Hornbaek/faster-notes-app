"""Dev launcher — runs both servers in-process (no tray).

  Dashboard  http://localhost:8765   (loopback only)
  Phone      https://<LAN-IP>:8766    (TLS; for the PWA + bridge)

For the installed app use tray.py instead. Data + cert live under
%LOCALAPPDATA%\\FasterNotes (override with the FASTER_NOTES_DATA env var).

Usage:  python start.py
"""
import runner

if __name__ == "__main__":
    runner.run()
