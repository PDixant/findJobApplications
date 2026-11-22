; Windows installer for Job Watcher (Inno Setup 6). Built after PyInstaller, from the main folder:
;     iscc /DAppVersion=0.4.0 packaging\JobWatcher.iss
; Installs for the current user only (no admin prompt), adds Start menu and optional Desktop
; shortcuts, and can add the jobwatcher command to PATH. Uninstalling keeps your data in
; %APPDATA%\JobWatcher.
#ifndef AppVersion
  #define AppVersion "0.4.0"
#endif

[Setup]
AppId={{B5C3E5A8-7D2F-4E7A-9C61-2F0D7A1B9E44}
AppName=Job Watcher
AppVersion={#AppVersion}
AppPublisher=Job Watcher
DefaultDirName={localappdata}\Programs\Job Watcher
DefaultGroupName=Job Watcher
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=JobWatcherSetup
SetupIconFile=..\jobwatcher\assets\icon.ico
UninstallDisplayIcon={app}\Job Watcher.exe
Compression=lzma2
SolidCompression=yes
ChangesEnvironment=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Put Job Watcher on the Desktop"; GroupDescription: "Shortcuts:"
Name: "addtopath"; Description: "Add the jobwatcher command for terminals"; GroupDescription: "Command line:"

[Files]
Source: "..\dist\JobWatcher\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Job Watcher"; Filename: "{app}\Job Watcher.exe"
Name: "{autodesktop}\Job Watcher"; Filename: "{app}\Job Watcher.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
  Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\Job Watcher.exe"; Description: "Open Job Watcher"; Flags: nowait postinstall skipifsilent

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
