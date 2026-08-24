!include "MUI2.nsh"
!include "LogicLib.nsh"

Name "EchoServe"
OutFile "EchoServe-0.1.2-Setup.exe"
InstallDir "$PROGRAMFILES64\EchoServe"
RequestExecutionLevel admin

!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Section "Main" SEC01
    SetOutPath "$INSTDIR"
    File /r "dist\*"
    File /r "docker\*"
    File /r "scripts\*"
    File "README.md"
    File "LICENSE.txt"

    CreateShortCut "$DESKTOP\EchoServe.lnk" "$INSTDIR\start_echoseve.bat" ""
    CreateDirectory "$SMPROGRAMS\EchoServe"
    CreateShortCut "$SMPROGRAMS\EchoServe\Start.lnk" "$INSTDIR\start_echoseve.bat" ""
    CreateShortCut "$SMPROGRAMS\EchoServe\Stop.lnk" "$INSTDIR\stop_echoseve.bat" ""
    CreateShortCut "$SMPROGRAMS\EchoServe\Uninstall.lnk" "$INSTDIR\uninstall.exe" ""

    WriteUninstaller "$INSTDIR\uninstall.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\EchoServe" "DisplayName" "EchoServe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\EchoServe" "UninstallString" "$INSTDIR\uninstall.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\EchoServe" "DisplayVersion" "0.1.2"
SectionEnd

Section "Uninstall"
    Delete "$DESKTOP\EchoServe.lnk"
    RMDir /r "$SMPROGRAMS\EchoServe"
    RMDir /r "$INSTDIR"
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\EchoServe"
SectionEnd
