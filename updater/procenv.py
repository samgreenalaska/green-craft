"""Environment scrubbing for child processes.

A PyInstaller onefile build unpacks itself to %TEMP%\\_MEInnnnnn and prepends that
directory to PATH so its own bundled DLLs are found. Every child process we spawn
inherits that PATH.

prismlauncher.exe is a native MSVC build that needs VCRUNTIME140.dll. Started with our
PATH, it finds *our* copy first and loads it out of the temp directory -- and holds it
open for as long as Prism and Minecraft run. When GreenCraft exits, the bootloader
cannot delete its own temp directory and shows:

    Failed to remove temporary directory: C:\\Users\\...\\Temp\\_MEI149762

Confirmed by hand: the stranded directory refused to delete with
"Access to the path 'VCRUNTIME140.dll' is denied", while every other stale one
deleted cleanly.

So: strip our bundle directory from PATH before spawning anything, and drop the
PyInstaller bookkeeping variables while we are at it so children do not inherit a
confused idea of where they were launched from.
"""
import os
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000


def hidden():
    """Keyword arguments that stop a console child flashing a window on screen.

    CREATE_NO_WINDOW on its own is not enough on Windows 11. When Windows Terminal is
    the default console host, the console handoff can still paint a window for a few
    frames, which is exactly the black flash a friend sees during setup and reads as
    something sketchy. Passing a hidden STARTUPINFO as well closes that gap.

    Spread into any subprocess call that runs a console program:

        subprocess.run(cmd, **procenv.hidden(), env=procenv.child_env())
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": si}


def bundle_dir():
    """The onefile extraction directory, or None when not frozen."""
    return getattr(sys, "_MEIPASS", None)


def child_env(extra=None):
    """A copy of os.environ safe to hand to a child process."""
    env = dict(os.environ)
    mei = bundle_dir()
    if mei:
        target = os.path.normcase(os.path.abspath(mei))
        parts = []
        for p in env.get("PATH", "").split(os.pathsep):
            if not p:
                continue
            try:
                if os.path.normcase(os.path.abspath(p)) == target:
                    continue
            except OSError:
                pass
            parts.append(p)
        env["PATH"] = os.pathsep.join(parts)

    # PyInstaller's own breadcrumbs. Harmless to keep, but a child that is itself a
    # PyInstaller app would misread them.
    for k in ("_MEIPASS2", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE",
              "_PYI_PARENT_PROCESS_LEVEL"):
        env.pop(k, None)

    if extra:
        env.update(extra)
    return env
