; Windows installer for Pathfinder (Inno Setup 6.3 or newer). Built after PyInstaller, from the main folder:
;     iscc /DAppVersion=1.0.0 packaging\pathfinder.iss
; The release workflow does this for every tag. Installs for the current user only (no admin prompt),
; adds Start menu and optional Desktop shortcuts, and can add the jobwatcher command to PATH.
; Uninstalling keeps your data in %APPDATA%\JobWatcher.
;
; Updating from inside the app runs this installer with /SILENT once its SHA-256 matches the release's
; SHA256SUMS.txt; it closes Pathfinder, installs the new version and opens it again.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
; The AppId stays the same forever, so new versions install over old ones (including Job Watcher's)
AppId={{B5C3E5A8-7D2F-4E7A-9C61-2F0D7A1B9E44}
AppName=Pathfinder
AppVersion={#AppVersion}
AppVerName=Pathfinder {#AppVersion}
AppPublisher=Dixant Patel
AppPublisherURL=https://github.com/PDixant/findJobApplications
AppSupportURL=https://github.com/PDixant/findJobApplications/issues
AppUpdatesURL=https://github.com/PDixant/findJobApplications/releases
AppCopyright=Copyright (c) 2026 Dixant Patel
VersionInfoVersion={#AppVersion}
VersionInfoCompany=Dixant Patel
VersionInfoDescription=Pathfinder installer
VersionInfoProductName=Pathfinder
DefaultDirName={localappdata}\Programs\Pathfinder
DefaultGroupName=Pathfinder
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=PathfinderSetup
SetupIconFile=..\jobwatcher\assets\icon.ico
UninstallDisplayIcon={app}\Pathfinder.exe
UninstallDisplayName=Pathfinder
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
ChangesEnvironment=yes
CloseApplications=yes
RestartApplications=no
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Put Pathfinder on the Desktop"; GroupDescription: "Shortcuts:"
Name: "addtopath"; Description: "Add the jobwatcher command for terminals"; GroupDescription: "Command line:"

[Files]
Source: "..\dist\Pathfinder\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; Pathfinder was called Job Watcher: an upgrade removes the program and shortcuts under the old name
Type: files; Name: "{app}\Job Watcher.exe"
Type: files; Name: "{group}\Job Watcher.lnk"
Type: files; Name: "{autodesktop}\Job Watcher.lnk"

[Icons]
Name: "{group}\Pathfinder"; Filename: "{app}\Pathfinder.exe"; AppUserModelID: "PDixant.Pathfinder"
Name: "{autodesktop}\Pathfinder"; Filename: "{app}\Pathfinder.exe"; AppUserModelID: "PDixant.Pathfinder"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
  Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
; Installed by hand: offer to open it. Installed by the in-app update (silent): open it again.
Filename: "{app}\Pathfinder.exe"; Description: "Open Pathfinder"; Flags: nowait postinstall skipifsilent
Filename: "{app}\Pathfinder.exe"; Flags: nowait; Check: WizardSilent

[Code]
function NeedsAddPath(Dir: string): Boolean;
var
  Path: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Path) + ';') = 0;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Path, Dir: string;
  P: Integer;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path) then
    exit;
  Dir := ExpandConstant('{app}');
  P := Pos(';' + Uppercase(Dir), Uppercase(Path));
  if P > 0 then
  begin
    Delete(Path, P, Length(Dir) + 1);
    RegWriteExpandStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Path);
  end;
end;
