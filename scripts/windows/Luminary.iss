; ─────────────────────────────────────────────────────────────────────────
;  Luminary — Windows installer (Inno Setup)
; ─────────────────────────────────────────────────────────────────────────
;
; Build the app first:  build-windows.bat   (run from the project root)
; Then compile this script from the project root, e.g.:
;   iscc scripts\windows\Luminary.iss
; (The relative MyDistDir path below assumes that working directory.)
;
; USER DATA SAFETY
; ─────────────────
; This installer only ever writes to {app} (the install directory). User
; data — the SQLite DB, config, logs, thumbnail cache — lives entirely
; outside {app}, in %LOCALAPPDATA%\Luminary, managed by app_paths.py. That
; means:
;   - Every [Files] entry below uses "ignoreversion", i.e. always overwrite
;     — which is safe now, because {app} only ever contains replaceable
;     binaries/resources, never user data.
;   - Upgrading (running this installer again over an existing install)
;     replaces {app} wholesale and never touches %LOCALAPPDATA%\Luminary.
;   - Uninstalling removes {app} but leaves %LOCALAPPDATA%\Luminary alone,
;     matching standard Windows convention (user data survives uninstall
;     unless the user removes it themselves).
; If the previous installed version predates this refactor (i.e. it stored
; data inside _internal\), app_paths.py's migrate_legacy_internal_data()
; moves it out to %LOCALAPPDATA%\Luminary the first time the new build runs
; — nothing needs to happen here in the installer for that.

#define MyAppName "Luminary"
#define MyAppVersion "1.0.0"
  ; ^ Bump this for every release. Keep it in sync with whatever
  ;   versioning scheme the rest of the project uses.
#define MyAppPublisher "Luminary"
#define MyAppExeName "Luminary.exe"
#define MyAppIcon "..\..\app\src\frontend\images\luminary.ico"
  ; ^ Inno Setup (and Windows shortcuts generally) need an .ico, not a .png —
  ;   convert your PNG once, e.g. via ImageMagick:
  ;     magick icon.png -define icon:auto-resize=256,128,64,48,32,16 icon.ico
  ;   or any online PNG→ICO converter (include multiple sizes for a crisp
  ;   look at every zoom level: taskbar, Start menu, desktop, Explorer).
  ;   Place the resulting icon.ico next to this .iss file (scripts\windows\).
#define MyDistDir "..\..\app\build\windows\portable\Luminary"
  ; ^ Must match DIST_DIR\Luminary in build-windows.bat.

[Setup]
; This AppId is what lets Windows/Inno recognize a new installer as an
; UPGRADE of the same product rather than a separate install. Generate your
; own once (Tools > Generate GUID in the Inno Setup IDE, or `python3 -c
; "import uuid; print(uuid.uuid4())"`) and then NEVER change it between
; releases — changing it would orphan every existing install.
AppId={{8B5A41FA-1578-4300-9044-C7FDF1FAD649}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\app\build\windows\installer
OutputBaseFilename=Luminary-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#MyAppIcon}
UninstallDisplayIcon={app}\{#MyAppIcon}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Recurse the entire built app — Luminary.exe, _internal\ (PyInstaller
; runtime + libraries), resources\ (frontend), ffmpeg.exe/ffprobe.exe if
; bundled. "ignoreversion" always overwrites on upgrade, which is safe here
; since none of this is user data (see the note at the top of this file).
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyAppIcon}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIcon}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppIcon}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only clean up build/runtime scratch files that might land under {app}
; itself (e.g. a stray __pycache__). Deliberately does NOT list anything
; under {localappdata}\Luminary — that's user data and should survive
; uninstall by default, same as any well-behaved Windows app.
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
// Best-effort: close a running instance before installing/upgrading so its
// files aren't locked. Failure here (e.g. it wasn't running) is ignored —
// this mirrors what a real deployment would want but keep in mind
// taskkill's exit code isn't checked, by design.
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    Exec('taskkill.exe', '/F /IM {#MyAppExeName}', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode);
  end;
end;
