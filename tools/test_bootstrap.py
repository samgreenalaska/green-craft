"""Compile and run tools/test_bootstrap.c against the live bootstrap.txt.

    python tools/test_bootstrap.py

Run after touching cfg_get in updater/bootstrap.c.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_exe as b

REPO = b.REPO
OUT = b.WORK / "test_bootstrap.exe"


def main():
    vcvars = b.find_vcvars()
    if not vcvars:
        print("MSVC not found -- install the Visual Studio Build Tools.")
        return 1

    b.WORK.mkdir(parents=True, exist_ok=True)
    bat = b.WORK / "cc_test.bat"
    bat.write_text(f'''\
@echo off
call "{vcvars}" >nul
if errorlevel 1 exit /b 1
cl.exe /nologo /Od /MT /W3 /D_CRT_SECURE_NO_WARNINGS /wd4505 ^
  "{REPO / 'tools' / 'test_bootstrap.c'}" ^
  /Fe:"{OUT}" /Fo:"{b.WORK}\\\\" /link /SUBSYSTEM:CONSOLE /INCREMENTAL:NO
exit /b %errorlevel%
''', encoding="utf-8")

    if subprocess.run(["cmd", "/c", str(bat)]).returncode != 0:
        print("compile failed")
        return 1
    return subprocess.run([str(OUT), str(REPO / "bootstrap.txt")]).returncode


if __name__ == "__main__":
    sys.exit(main())
