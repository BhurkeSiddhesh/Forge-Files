; Inno Setup script for the Forge Files Windows installer.
; Build:  iscc /DAppVersion=1.0.0 desktop\installer.iss   (run from public/)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1D2C54-8B7E-4C1A-9E36-5A0B7D3F21C8}
AppName=Forge Files
AppVersion={#AppVersion}
AppPublisher=Forge Files
AppPublisherURL=https://www.forgefiles.org
DefaultDirName={autopf}\Forge Files
DefaultGroupName=Forge Files
; Per-user install: no admin prompt, and the app folder stays writable for updates.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer-out
OutputBaseFilename=ForgeFiles-Setup-{#AppVersion}
SetupIconFile=forgefiles.ico
UninstallDisplayIcon={app}\ForgeFiles.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
CloseApplications=yes

[Files]
Source: "dist\ForgeFiles\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
; Microsoft's WebView2 runtime draws the app window. Windows 11 already has it;
; this installs it only on machines that do not (older Windows 10).
Source: "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2

[Icons]
Name: "{group}\Forge Files"; Filename: "{app}\ForgeFiles.exe"
Name: "{autodesktop}\Forge Files"; Filename: "{app}\ForgeFiles.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{tmp}\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft WebView2 runtime..."; Check: NeedsWebView2; Flags: waituntilterminated
Filename: "{app}\ForgeFiles.exe"; Description: "Launch Forge Files"; Flags: nowait postinstall skipifsilent

[Code]
function HasWebView2(Root: Integer; Key: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0');
end;

function NeedsWebView2: Boolean;
begin
  Result := not (
    HasWebView2(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') or
    HasWebView2(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'));
end;
