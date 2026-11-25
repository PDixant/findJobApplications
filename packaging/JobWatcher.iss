; Windows installer for Pathfinder (Inno Setup 6). Built after PyInstaller, from the main folder:
;     iscc /DAppVersion=0.4.0 packaging\JobWatcher.iss
; Installs for the current user only (no admin prompt), adds Start menu and optional Desktop
; shortcuts, and can add the jobwatcher command to PATH. Uninstalling keeps your data in
; %APPDATA%\JobWatcher.
#ifndef AppVersion
  #define AppVersion "0.4.0"
#endif

[Setup]
AppId={{B5C3E5A8-7D2F-4E7A-9C61-2F0D7A1B9E44}
AppName=Pathfinder
AppVersion={#AppVersion}
AppPublisher=Dixant Patel
AppPublisherURL=https://github.com/PDixant/findJobApplications
DefaultDirName={localappdata}\Programs\Pathfinder
DefaultGroupName=Pathfinder
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=PathfinderSetup
SetupIconFile=..\jobwatcher\assets\icon.ico
UninstallDisplayIcon={app}\Pathfinder.exe
Compression=lzma2
SolidCompression=yes
ChangesEnvironment=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Put Pathfinder on the Desktop"; GroupDescription: "Shortcuts:"
Name: "addtopath"; Description: "Add the jobwatcher command for terminals"; GroupDescription: "Command line:"

[Files]
Source: "..\dist\JobWatcher\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; Pathfinder was called Job Watcher: an upgrade removes the program and shortcuts under the old name
Type: files; Name: "{app}\Job Watcher.exe"
Type: files; Name: "{group}\Job Watcher.lnk"
Type: files; Name: "{autodesktop}\Job Watcher.lnk"

[Icons]
Name: "{group}\Pathfinder"; Filename: "{app}\Pathfinder.exe"
Name: "{autodesktop}\Pathfinder"; Filename: "{app}\Pathfinder.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
  Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\Pathfinder.exe"; Description: "Open Pathfinder"; Flags: nowait postinstall skipifsilent

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
