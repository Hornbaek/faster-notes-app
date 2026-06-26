; Inno Setup script for Faster Notes.
; Build the app first (see PACKAGING.md), then compile this with Inno Setup
; (iscc installer.iss) to produce dist/FasterNotesSetup.exe.

#define AppName "Faster Notes - Server"
#define AppVersion "1.0.0"
#define AppExe "FasterNotes.exe"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Sebastian Hornbaek
DefaultDirName={autopf}\FasterNotes
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=dist
OutputBaseFilename=FasterNotesSetup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
; Admin needed once so the firewall rule for the phone port can be added.
PrivilegesRequired=admin

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startup"; Description: "Start {#AppName} automatically when I log in"; GroupDescription: "Startup:"

[Files]
Source: "dist\FasterNotes\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Autostart at login (per-user).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "FasterNotes"; ValueData: """{app}\{#AppExe}"""; \
  Flags: uninsdeletevalue; Tasks: startup

[Run]
; Allow inbound on the phone port (8766). The dashboard (8765) is loopback-only,
; so it needs no firewall rule.
Filename: "{sys}\netsh.exe"; \
  Parameters: "advfirewall firewall add rule name=""Faster Notes (phone 8766)"" dir=in action=allow protocol=TCP localport=8766"; \
  Flags: runhidden
; Offer to launch right after install. runascurrentuser is REQUIRED: without it the
; postinstall launch inherits Setup's elevated/admin token, which resolves a DIFFERENT
; %LOCALAPPDATA% (empty data dir) than the normal user session — the app then mints a
; throwaway api_key and never sees the user's config (Cloudflare tunnel, etc.).
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; \
  Flags: nowait postinstall skipifsilent runascurrentuser

[UninstallRun]
Filename: "{sys}\netsh.exe"; \
  Parameters: "advfirewall firewall delete rule name=""Faster Notes (phone 8766)"""; \
  Flags: runhidden
