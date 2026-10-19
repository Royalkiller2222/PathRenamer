; Inno Setup script for PathRenamer.
; Reconstructed from the 1.2.0 installer: same AppId, so a new version
; installs over (upgrades) an existing installation.
; Build: iscc /DAppVersion=1.3.0 installer\PathRenamer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8A3F2E1D-4B5C-6D7E-8F9A-0B1C2D3E4F5A}
AppName=PathRenamer
AppVersion={#AppVersion}
AppVerName=PathRenamer {#AppVersion}
AppPublisher=PathRenamer
AppPublisherURL=https://github.com/Royalkiller2222/PathRenamer
AppSupportURL=https://github.com/Royalkiller2222/PathRenamer
AppUpdatesURL=https://github.com/Royalkiller2222/PathRenamer/releases
DefaultDirName={autopf}\PathRenamer
DefaultGroupName=PathRenamer
UninstallDisplayIcon={app}\PathRenamer.exe
SetupIconFile=..\src\newicon.ico
OutputDir=..\dist
OutputBaseFilename=PathRenamer-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\PathRenamer.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\PathRenamer"; Filename: "{app}\PathRenamer.exe"
Name: "{group}\Uninstall PathRenamer"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PathRenamer"; Filename: "{app}\PathRenamer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PathRenamer.exe"; Description: "{cm:LaunchProgram,PathRenamer}"; Flags: nowait postinstall skipifsilent
