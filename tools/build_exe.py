"""Package the updater as a single self-contained GreenCraft.exe.

PyInstaller bundles the Python interpreter, so friends install nothing -- the exe is
the whole runtime. Output lands in dist/ alongside the overrides bundles, ready to be
attached to a release.
"""
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UPDATER = REPO / "updater"
DIST = REPO / "dist"
WORK = REPO / "build"


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:  python -m pip install pyinstaller")
        return 1

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "GreenCraft",
        "--icon", str(UPDATER / "icon.ico"),
        "--distpath", str(DIST),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        # Windowed, not console. A command prompt appearing when a friend clicks the
        # shortcut is exactly what the GUI exists to avoid, and it makes ordinary
        # progress output look like an error. Everything is written to
        # %LOCALAPPDATA%\GreenCraft\logs\greencraft.log instead, and --no-gui still
        # gives plain output when run from an existing terminal.
        "--noconsole",
        # greencraft.py reaches nbt via a sys.path insert, which PyInstaller's static
        # analysis does not follow. Say so explicitly or the exe dies on `import nbt`.
        "--paths", str(UPDATER),
        # These are reached through a sys.path insert or a deferred import, neither of
        # which PyInstaller's static analysis follows. Without them the exe builds and
        # then dies at runtime on the first import.
        "--hidden-import", "nbt",
        "--hidden-import", "gui",
        "--hidden-import", "install",
        "--hidden-import", "prereq",
        # Needed at runtime for the window icon; --icon only sets the exe's own icon.
        "--add-data", f"{UPDATER / 'icon.ico'};.",
        "--noconfirm",
        str(UPDATER / "greencraft.py"),
    ]
    print(" ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        return r.returncode

    exe = DIST / "GreenCraft.exe"
    if not exe.exists():
        print("build reported success but GreenCraft.exe is missing")
        return 1

    shutil.rmtree(WORK, ignore_errors=True)
    print(f"\nBuilt {exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
