"""Build both distributables.

  GreenCraftSetup.exe   native, tiny, no interpreter. What a friend downloads. Fetches
                        the payload zip, unpacks it, runs the app. Runs once.
  GreenCraft/           onedir payload -> zipped as GreenCraft-<version>.zip. The app
                        friends actually run every day.

Neither one extracts anything at startup, and that is the whole design.

A PyInstaller *onefile* build unpacks itself to %TEMP%\\_MEInnnnnn on every launch and
deletes it on exit. Windows Defender scans the freshly extracted files, the bootloader
loses the race, and the user gets

    Failed to remove temporary directory: C:\\Users\\...\\Temp\\_MEI184802

Measured: the directory was locked at exit and free 40 s later. There is no retry in
the bootloader and no flag to control it.

The app avoids this by being *onedir* -- it extracts nothing, starts faster, and trips
antivirus less, which matters when SmartScreen is already in the way. The installer
could not be onedir, because a friend downloads one file, so it stayed onefile and kept
paying: an 8.4 MB download that wrote 17.2 MB across 12 files into %TEMP% -- an
interpreter, OpenSSL, and a pile of .pyd files -- every one of them scanned on write
and again on load, to run 150 lines of glue.

It is C now. One file, ~0.2 MB, nothing written to %TEMP%, and the temp-directory race
is gone rather than narrowed. See updater/bootstrap.c.
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UPDATER = REPO / "updater"
DIST = REPO / "dist"
WORK = REPO / "build"


def run(cmd):
    print(" ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd]).returncode


def build_app():
    """The real application, onedir, still PyInstaller."""
    out = DIST / "GreenCraft"
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    rc = run([
        sys.executable, "-m", "PyInstaller",
        "--onedir", "--name", "GreenCraft", "--noconsole",
        "--paths", UPDATER,
        # Reached via sys.path insert or deferred import; PyInstaller's static
        # analysis does not follow either.
        "--hidden-import", "nbt",
        "--hidden-import", "gui",
        "--hidden-import", "install",
        "--hidden-import", "prereq",
        "--hidden-import", "selfupdate",
        "--hidden-import", "version",
        "--hidden-import", "procenv",
        "--add-data", f"{UPDATER / 'icon.ico'};.",
        "--noconfirm",
        "--icon", str(UPDATER / "icon.ico"),
        "--distpath", str(DIST),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        UPDATER / "greencraft.py",
    ])
    return rc, out


def find_vcvars():
    """Locate vcvars64.bat through vswhere, the only supported way to find MSVC."""
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(pf86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.exists():
        return None
    out = subprocess.run(
        [str(vswhere), "-latest", "-products", "*",
         "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
         "-property", "installationPath"],
        capture_output=True, text=True).stdout.strip()
    if not out:
        return None
    bat = Path(out.splitlines()[0]) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    return bat if bat.exists() else None


def write_rc(version):
    """Icon and version resource.

    Deliberately nothing more. An earlier attempt added an embedded application
    manifest, Control Flow Guard and fuller version strings to look less like a
    dropper to Defender's ML; measured against VirusTotal it moved Microsoft not at
    all and gained a Kaspersky heuristic hit, 2/71 -> 3/71. The detections are about
    what this program does -- unsigned, downloads an archive, runs what it unpacks --
    not about how its PE headers are dressed. See PLAN.md for the measurements.
    """
    major, minor, patch = (list(map(int, version.split("."))) + [0, 0, 0])[:3]
    icon = str(UPDATER / "icon.ico").replace("\\", "\\\\")
    (WORK / "setup.rc").write_text(f'''\
#include <windows.h>

1 ICON "{icon}"

VS_VERSION_INFO VERSIONINFO
FILEVERSION    {major},{minor},{patch},0
PRODUCTVERSION {major},{minor},{patch},0
FILEOS         VOS_NT_WINDOWS32
FILETYPE       VFT_APP
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904B0"
    BEGIN
      VALUE "CompanyName",      "GreenCraft"
      VALUE "FileDescription",  "GreenCraft Setup"
      VALUE "FileVersion",      "{version}.0"
      VALUE "InternalName",     "GreenCraftSetup"
      VALUE "OriginalFilename", "GreenCraftSetup.exe"
      VALUE "ProductName",      "GreenCraft"
      VALUE "ProductVersion",   "{version}.0"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
''', encoding="utf-8")


def build_bootstrap(version):
    """The downloadable installer.

    MSVC rather than the MinGW also on this box: its output is the most unremarkable
    PE Windows can be handed, which is the entire point of the exercise. /MT links the
    CRT statically, so a friend needs no redistributable.
    """
    out = DIST / "GreenCraftSetup.exe"
    vcvars = find_vcvars()
    if not vcvars:
        print("MSVC not found. Install the Visual Studio Build Tools with the\n"
              '"Desktop development with C++" workload.')
        return 1, out

    WORK.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    write_rc(version)

    # cl and rc only exist inside the environment vcvars sets up, so the compile has
    # to run as a batch file rather than as a direct exec.
    bat = WORK / "cc.bat"
    bat.write_text(f'''\
@echo off
call "{vcvars}" >nul
if errorlevel 1 exit /b 1
rc.exe /nologo /fo "{WORK / 'setup.res'}" "{WORK / 'setup.rc'}"
if errorlevel 1 exit /b 1
cl.exe /nologo /O1 /MT /GS /guard:cf /W3 /DNDEBUG /D_CRT_SECURE_NO_WARNINGS ^
  "{UPDATER / 'bootstrap.c'}" "{WORK / 'setup.res'}" ^
  /Fe:"{out}" /Fo:"{WORK}\\\\" ^
  /link /SUBSYSTEM:WINDOWS /INCREMENTAL:NO /RELEASE /DYNAMICBASE /NXCOMPAT
exit /b %errorlevel%
''', encoding="utf-8")

    if out.exists():
        out.unlink()
    return run(["cmd", "/c", str(bat)]), out


def zip_payload(app_dir, version):
    """Zip the onedir tree with paths relative to its root, deterministically."""
    zp = DIST / f"GreenCraft-{version}.zip"
    if zp.exists():
        zp.unlink()
    files = sorted(p for p in app_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            info = zipfile.ZipInfo(str(f.relative_to(app_dir)).replace("\\", "/"),
                                   date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, f.read_bytes())
    return zp


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:  python -m pip install pyinstaller")
        return 1

    sys.path.insert(0, str(UPDATER))
    import version as _v

    rc, app = build_app()
    if rc != 0 or not (app / "GreenCraft.exe").exists():
        print("app build failed")
        return rc or 1

    rc, boot = build_bootstrap(_v.VERSION)
    if rc != 0 or not boot.exists():
        print("bootstrap build failed")
        return rc or 1

    zp = zip_payload(app, _v.VERSION)
    shutil.rmtree(WORK, ignore_errors=True)

    print()
    print(f"  {boot.name:<28} {boot.stat().st_size / 1024:6.0f} KB   (download this)")
    print(f"  {zp.name:<28} {zp.stat().st_size / 1024 / 1024:6.1f} MB   (payload)")
    print(f"  dist/GreenCraft/           {sum(f.stat().st_size for f in app.rglob('*') if f.is_file()) / 1024 / 1024:6.1f} MB   (unpacked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
