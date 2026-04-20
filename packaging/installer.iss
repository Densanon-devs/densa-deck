; Inno Setup script for MTG Deck Engine
;
; Build order:
;   1. `python packaging/build_installer.ps1` or run the two steps manually:
;      a. `pyinstaller mtg-engine.spec --clean --noconfirm`
;         -> produces `dist/mtg-engine/` (folder-mode bundle)
;      b. `ISCC packaging/installer.iss`
;         -> produces `dist/MTG-Deck-Engine-Setup-<version>.exe`
;
; Inno Setup is free: https://jrsoftware.org/isinfo.php
; The ISCC compiler gets added to PATH on install.
;
; Code signing is NOT wired here (Windows SmartScreen will show an "unknown
; publisher" warning). To sign: get a code-signing cert (~$200-400/yr),
; use `SignTool sign /f ... /tr http://timestamp.digicert.com "<ExeFile>"`
; as a post-build step. Add [Setup] SignTool=mytool to sign the installer.

#define AppName "MTG Deck Engine"
#define AppId "{{MTG-DECK-ENGINE-6F7C2A4B}}"
#define AppVersion "0.1.0"
#define AppPublisher "Densanon LLC"
#define AppURL "https://toolkit.densanon.com/mtg-engine.html"
#define AppExeName "mtg-engine.exe"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
; Current-user install by default — avoids the UAC prompt and lets the app
; write to %USERPROFILE% for the card DB without permission gymnastics.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
OutputDir=..\dist
OutputBaseFilename=MTG-Deck-Engine-Setup-{#AppVersion}
SetupIconFile=
; DisableDirPage=yes  ; uncomment if you want a one-click install
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "registerprotocol"; Description: "Register the mtg-engine:// URL scheme (enables one-click license activation from the Stripe success page)"; GroupDescription: "Integrations:"

[Files]
; Ship the entire PyInstaller folder. `\*` plus `recursesubdirs` picks up
; the _internal/ dir, bundled Python DLLs, and the analyst/static assets.
Source: "..\dist\mtg-engine\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "app"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "app"; Tasks: desktopicon

[Registry]
; URI scheme registration — mirrors what `mtg-engine register-protocol` writes
; at runtime, but done once at install time so the user doesn't have to
; execute a separate command. Using {app} instead of the Python interpreter
; means the packaged .exe handles the deep link directly.
Root: HKCU; Subkey: "Software\Classes\mtg-engine"; ValueType: string; ValueName: ""; ValueData: "URL:MTG Deck Engine Protocol"; Flags: uninsdeletekey; Tasks: registerprotocol
Root: HKCU; Subkey: "Software\Classes\mtg-engine"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Flags: uninsdeletekey; Tasks: registerprotocol
Root: HKCU; Subkey: "Software\Classes\mtg-engine\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" app ""%1"""; Flags: uninsdeletekey; Tasks: registerprotocol

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "app"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
