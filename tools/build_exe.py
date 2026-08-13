"""Build both distributables.

  GreenCraftSetup.exe   onefile, tiny, no tkinter. What a friend downloads. Fetches
                        the payload zip, unpacks it, runs the app. Runs once.
  GreenCraft/           onedir payload -> zipped as GreenCraft-<version>.zip. The app
                        friends actually run every day.

Why the split: a PyInstaller *onefile* build unpacks itself to %TEMP%\\_MEInnnnnn on
every launch and deletes it on exit. Windows Defender scans the freshly extracted
files, the bootloader loses the race, and the user gets

    Failed to remove temporary directory: C:\\Users\\...\\Temp\\_MEI184802

Measured: the directory was locked at exit and free 40 s later. There is no retry in
the bootloader and no flag to control it. A *onedir* build extracts nothing, so the
failure mode does not exist -- and it starts faster and trips antivirus less, which
matters when SmartScreen is already in the way.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UPDATER = REPO / "updater"
DIST = REPO / "dist"
WORK = REPO / "build"

COMMON = [
    "--noconfirm",
    "--icon", str(UPDATER / "icon.ico"),
    "--distpath", str(DIST),
    "--workpath", str(WORK),
    "--specpath", str(WORK),
]


def run(cmd):
    print(" ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd]).returncode


def build_app():
    """The real application, onedir."""
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
        *COMMON,
        UPDATER / "greencraft.py",
    ])
    return rc, out


def build_bootstrap():
    """The downloadable installer, onefile and deliberately minimal."""
    rc = run([
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--name", "GreenCraftSetup", "--noconsole",
        "--paths", UPDATER,
        "--hidden-import", "version",
        # Excluded on purpose: every module left out is one less file for a scanner to
        # touch during the temp extraction this binary still does.
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "email",
        "--exclude-module", "xml",
        *COMMON,
        UPDATER / "bootstrap.py",
    ])
    return rc, DIST / "GreenCraftSetup.exe"


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

    rc, boot = build_bootstrap()
    if rc != 0 or not boot.exists():
        print("bootstrap build failed")
        return rc or 1

    zp = zip_payload(app, _v.VERSION)
    shutil.rmtree(WORK, ignore_errors=True)

    print()
    print(f"  {boot.name:<28} {boot.stat().st_size / 1024 / 1024:6.1f} MB   (download this)")
    print(f"  {zp.name:<28} {zp.stat().st_size / 1024 / 1024:6.1f} MB   (payload)")
    print(f"  dist/GreenCraft/           {sum(f.stat().st_size for f in app.rglob('*') if f.is_file()) / 1024 / 1024:6.1f} MB   (unpacked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
