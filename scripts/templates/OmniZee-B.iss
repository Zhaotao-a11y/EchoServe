#define MyAppName "EchoServe"
#define MyAppVersion "0.1.2"
#define MyAppPublisher "EchoServe"
#define MyAppURL "https://echoseve.local"
#define MyAppExeName "EchoServe.exe"

[Setup]
AppId={{TEST-APP-ID-1234}}
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppPublisher={{#MyAppPublisher}}
AppPublisherURL={{#MyAppURL}}
DefaultDirName={{autopf}}\{{#MyAppName}}
OutputDir=..\output
OutputBaseFilename=EchoServe-{{#MyAppVersion}}-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin

[Files]
Source: "..\dist\*" ; DestDir: "{{app}}" ; Flags: ignoreversion recursesubdirs
Source: "..\docker\*" ; DestDir: "{{app}}\docker" ; Flags: ignoreversion recursesubdirs
Source: "..\scripts\*" ; DestDir: "{{app}}" ; Flags: ignoreversion recursesubdirs
Source: "..\README.md" ; DestDir: "{{app}}" ; Flags: ignoreversion
Source: "..\LICENSE.txt" ; DestDir: "{{app}}" ; Flags: ignoreversion

[Icons]
Name: "{{autoprograms}}\{{#MyAppName}}" ; Filename: "{{app}}\start_echoseve.bat"
Name: "{{autodesktop}}\{{#MyAppName}}" ; Filename: "{{app}}\start_echoseve.bat"

[Run]
Filename: "{{cmd}}" ; Parameters: "/c \"{{app}}\start_echoseve.bat\"" ; Description: "Start EchoServe" ; Flags: nowait postinstall skipifsilent
