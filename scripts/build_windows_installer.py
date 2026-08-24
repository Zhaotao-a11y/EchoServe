"""
EchoServe P2 - Windows installer builder

Generates Windows MSI installer build files:
  - Inno Setup (.iss) script
  - Windows batch scripts (.bat)
  - Windows service wrapper (Python)
  - Windows-specific Docker Compose
  - Environment check script

All template content is loaded from files in scripts/templates/
to avoid Python parser issues with special characters.

Usage:
  python scripts/build_windows_installer.py --output dist/
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger("echoseve.build")

# ═════════════════════════════════════════
#  Inno Setup script template (loaded from file)
# ═════════════════════════════════════════

INNO_TEMPLATE_PATH = "scripts/templates/EchoServe.iss"


# ═════════════════════════════════════════
#  Main builder class
# ═════════════════════════════════════════

class WindowsInstallerBuilder:
    """Windows installer builder"""

    def __init__(
        self,
        version: str = "0.1.0",
        output_dir: str = "./output",
        app_id: str = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890",
    ):
        self.version = version
        self.output_dir = Path(output_dir)
        self.app_id = app_id
        self.build_dir = Path(f"./build/windows_{version}")
        self.templates_dir = Path("./scripts/templates")
        self.iss_template = self._load_template(INNO_TEMPLATE_PATH)

    def _load_template(self, rel_path: str) -> str:
        """Load template file with UTF-8 encoding.
        Searches in templates_dir first, then falls back to CWD."""
        path = self.templates_dir / rel_path
        if path.exists():
            return path.read_text(encoding="utf-8")
        # Fallback to CWD-relative
        path = Path(rel_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
        logger.warning(f"Template not found: {rel_path}")
        return ""

    def build_all(self) -> Dict[str, Any]:
        """Generate all Windows installer files"""
        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        results = {}

        # 1. Inno Setup script
        if self.iss_template:
            inno_content = self.iss_template.format(
                version=self.version, app_id=self.app_id
            )
            results["inno_script"] = self._write_file("EchoServe.iss", inno_content)
        else:
            results["inno_script"] = self._create_inno_from_scratch()

        # 2. NSIS script
        nsis_content = self._generate_nsis()
        results["nsis_script"] = self._write_file("EchoServe.nsi", nsis_content)

        # 3. Copy batch scripts from templates
        for bat_file in [
            "start_echoseve.bat",
            "stop_echoseve.bat",
            "install_docker.bat",
            "check_environment.bat",
        ]:
            results[bat_file] = self._copy_template(bat_file)

        # 4. Copy Docker Compose
        results["docker_compose"] = self._copy_template(
            "docker-compose.windows.yml", subdir="docker"
        )

        # 5. Windows service wrapper
        results["win_service"] = self._write_file(
            "win_service.py", self._generate_win_service()
        )

        # 6. README
        readme = self._load_template("README-Windows.txt")
        if readme:
            readme = readme.replace("{version}", self.version)
        else:
            readme = self._generate_readme()
        results["readme"] = self._write_file("README-Windows.txt", readme)

        # 7. License
        results["license"] = self._write_file(
            "LICENSE.txt", self._generate_license()
        )

        logger.info(f"[WindowsInstaller] Build complete: {self.build_dir}")
        for k, v in results.items():
            logger.info(f"  {k}: {v}")

        return {
            "status": "success",
            "build_dir": str(self.build_dir),
            "files": results,
            "next_steps": [
                f"1. Download Inno Setup: https://jrsoftware.org/isdl.php",
                f"2. Open build\\windows_{self.version}\\EchoServe.iss",
                f"3. Compile to generate MSI/EXE installer",
                f"4. Or use NSIS to compile .nsi script",
            ],
        }

    def _create_inno_from_scratch(self) -> str:
        """Create Inno Setup script if template missing"""
        content = '''#define MyAppName "EchoServe"
#define MyAppVersion "{VERSION}"
#define MyAppPublisher "EchoServe"
#define MyAppURL "https://echoseve.local"
#define MyAppExeName "EchoServe.exe"

[Setup]
AppId={{{APP_ID}}}
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppPublisher={{#MyAppPublisher}}
AppPublisherURL={{#MyAppURL}}
DefaultDirName={{autopf}}\\{{#MyAppName}}
OutputDir=..\\output
OutputBaseFilename=EchoServe-{{#MyAppVersion}}-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin

[Files]
Source: "..\\dist\\*" ; DestDir: "{{app}}" ; Flags: ignoreversion recursesubdirs
Source: "..\\docker\\*" ; DestDir: "{{app}}\\docker" ; Flags: ignoreversion recursesubdirs
Source: "..\\scripts\\*" ; DestDir: "{{app}}" ; Flags: ignoreversion recursesubdirs
Source: "..\\README.md" ; DestDir: "{{app}}" ; Flags: ignoreversion
Source: "..\\LICENSE.txt" ; DestDir: "{{app}}" ; Flags: ignoreversion

[Icons]
Name: "{{autoprograms}}\\{{#MyAppName}}" ; Filename: "{{app}}\\start_echoseve.bat"
Name: "{{autodesktop}}\\{{#MyAppName}}" ; Filename: "{{app}}\\start_echoseve.bat"

[Run]
Filename: "{{cmd}}" ; Parameters: "/c \\"{{app}}\\start_echoseve.bat\\"" ; Description: "Start EchoServe" ; Flags: nowait postinstall skipifsilent
'''
        content = content.replace("{VERSION}", self.version)
        content = content.replace("{APP_ID}", self.app_id)
        return self._write_file("EchoServe.iss", content)

    def _generate_nsis(self) -> str:
        """Generate NSIS script"""
        template = '''!include "MUI2.nsh"
!include "LogicLib.nsh"

Name "EchoServe"
OutFile "EchoServe-{VERSION}-Setup.exe"
InstallDir "$PROGRAMFILES64\\EchoServe"
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
    File /r "dist\\*"
    File /r "docker\\*"
    File /r "scripts\\*"
    File "README.md"
    File "LICENSE.txt"

    CreateShortCut "$DESKTOP\\EchoServe.lnk" "$INSTDIR\\start_echoseve.bat" ""
    CreateDirectory "$SMPROGRAMS\\EchoServe"
    CreateShortCut "$SMPROGRAMS\\EchoServe\\Start.lnk" "$INSTDIR\\start_echoseve.bat" ""
    CreateShortCut "$SMPROGRAMS\\EchoServe\\Stop.lnk" "$INSTDIR\\stop_echoseve.bat" ""
    CreateShortCut "$SMPROGRAMS\\EchoServe\\Uninstall.lnk" "$INSTDIR\\uninstall.exe" ""

    WriteUninstaller "$INSTDIR\\uninstall.exe"
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\EchoServe" "DisplayName" "EchoServe"
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\EchoServe" "UninstallString" "$INSTDIR\\uninstall.exe"
    WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\EchoServe" "DisplayVersion" "{VERSION}"
SectionEnd

Section "Uninstall"
    Delete "$DESKTOP\\EchoServe.lnk"
    RMDir /r "$SMPROGRAMS\\EchoServe"
    RMDir /r "$INSTDIR"
    DeleteRegKey HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\EchoServe"
SectionEnd
'''
        return template.replace("{VERSION}", self.version)

    def _generate_win_service(self) -> str:
        """Generate Windows service wrapper Python script"""
        return '''"""
EchoServe Windows Service Wrapper

Install: python win_service.py install
Start:   python win_service.py start
Stop:    python win_service.py stop
Remove:  python win_service.py remove

Requires: pip install pywin32
"""
import sys
import os
import subprocess

try:
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager
except ImportError:
    print("pywin32 required: pip install pywin32")
    sys.exit(1)

SERVICE_NAME = "EchoServeB"
SERVICE_DISPLAY_NAME = "EchoServe Knowledge Base Service"


class EchoServeBService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENT_LOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, "")
        )
        install_dir = os.path.dirname(os.path.abspath(__file__))
        compose_file = os.path.join(install_dir, "docker", "docker-compose.windows.yml")
        try:
            self.process = subprocess.Popen(
                ["docker", "compose", "-f", compose_file, "up"],
                cwd=install_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
        except Exception as e:
            servicemanager.LogErrorMsg(f"EchoServe service error: {e}")


def main():
    if len(sys.argv) == 1:
        servicemanager.Initialize(SERVICE_NAME, SERVICE_NAME)
        servicemanager.PrepareToHostSingle(EchoServeBService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(EchoServeBService)


if __name__ == "__main__":
    main()
'''

    def _generate_readme(self) -> str:
        """Generate README if template missing"""
        return f"""# EchoServe V{self.version} Windows Installer

## Requirements
- Windows 10/11 (64-bit)
- Docker Desktop for Windows (WSL2)
- NVIDIA GPU recommended (RTX 4090 24GB+)
- 16GB+ RAM, 500GB+ SSD

## Build with Inno Setup
1. Install Inno Setup: https://jrsoftware.org/isdl.php
2. Open EchoServe.iss
3. Build -> Compile
4. Output: output/EchoServe-{self.version}-Setup.exe

## Build with NSIS
1. Install NSIS: https://nsis.sourceforge.io/
2. Right-click EchoServe.nsi -> Compile
3. Output: output/EchoServe-{self.version}-Setup.exe

## Usage
- Double-click desktop icon or run start_echoseve.bat
- Wait for Docker startup (2-5 min first time)
- Browser opens https://localhost
- Default admin: admin / Admin@2026!
"""

    def _generate_license(self) -> str:
        """Generate LICENSE file"""
        return f"""EchoServe Enterprise Local Knowledge Base Q&A System
Copyright (c) 2026 EchoServe

License: Commercial

This software is licensed for authorized users only.
No reproduction, modification, distribution, or commercial use
without written permission.
"""

    def _copy_template(self, filename: str, subdir: str = "scripts") -> str:
        """Copy template file to build dir"""
        src = self.templates_dir / filename
        dst_dir = self.build_dir / subdir
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / filename

        if src.exists():
            content = src.read_text(encoding="utf-8")
            content = content.replace("{version}", self.version)
            dst.write_text(content, encoding="utf-8", newline="\r\n")
            return str(dst.relative_to(self.build_dir))

        logger.warning(f"  Template missing: {src}")
        return f"MISSING: {filename}"

    def _write_file(self, relative_path: str, content: str) -> str:
        """Write file with Windows line endings"""
        path = self.build_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(content)
        return str(path.relative_to(self.build_dir))


# ═════════════════════════════════════════
#  CLI entry point
# ═════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EchoServe Windows Installer Builder")
    parser.add_argument("--version", default="0.1.0", help="Version number")
    parser.add_argument("--output", default="./build", help="Output directory")
    parser.add_argument(
        "--app-id",
        default="A1B2C3D4-E5F6-7890-ABCD-EF1234567890",
        help="Windows App GUID",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    builder = WindowsInstallerBuilder(
        version=args.version,
        output_dir=args.output,
        app_id=args.app_id,
    )

    result = builder.build_all()
    print(json.dumps(result, indent=2, ensure_ascii=False))
