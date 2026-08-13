"""Replace the onedir payload with a newer build advertised by the manifest.

With a onefile build this was a rename-and-write of one exe. A onedir build is a
directory whose DLLs are mapped into the running process, so nothing inside it can be
replaced while we are alive -- and Windows will not rename a directory containing an
open image either.

So: stage the new payload alongside as `app.new`, then hand the swap to a detached
shell that waits for us to exit before moving anything. The swap itself is two
renames, so an interrupted update leaves either the old app or the new one, never a
half-written mixture.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import version as _version


def app_dir():
    """The directory the payload lives in, or None when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return None


def cleanup_old(log=lambda *_: None):
    """Remove the previous payload left behind by an update."""
    d = app_dir()
    if not d:
        return
    for stale in (d.with_name(d.name + ".old"), d.with_name(d.name + ".new")):
        if stale.exists():
            try:
                shutil.rmtree(stale, ignore_errors=True)
                if not stale.exists():
                    log(f"  cleaned up {stale.name}")
            except OSError:
                pass


def available(manifest, channel):
    """(version, payload_spec) if a newer payload is advertised, else (None, None)."""
    lc = (manifest.get("channels", {}).get(channel, {}) or {}).get("launcher") or {}
    v = lc.get("version")
    payload = lc.get("payload") or {}
    if not v or not payload.get("downloads") or not (payload.get("hashes") or {}).get("sha512"):
        return None, None
    if not _version.is_newer(v):
        return None, None
    return v, payload


def stage(spec, cached, log):
    """Unpack the new payload next to the current one. Returns the staging path."""
    d = app_dir()
    if not d:
        log("  (running from source - skipping self-update)")
        return None

    src, _ = cached(spec["hashes"]["sha512"], spec.get("filename", "GreenCraft.zip"),
                    spec["downloads"], spec.get("fileSize"))

    staging = d.with_name(d.name + ".new")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    with zipfile.ZipFile(src) as z:
        for name in z.namelist():
            target = (staging / name).resolve()
            if not str(target).startswith(str(staging.resolve())):
                raise ValueError(f"unsafe path in archive: {name}")
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))

    if not (staging / "GreenCraft.exe").exists():
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError("downloaded payload has no GreenCraft.exe")
    return staging


def swap_and_relaunch(staging, argv, log):
    """Hand the directory swap to a process that will outlive us."""
    import procenv

    d = app_dir()
    old = d.with_name(d.name + ".old")
    exe = d / "GreenCraft.exe"

    # Waits for our exe to become writable (i.e. we have exited), swaps, relaunches,
    # then removes the old payload. `ping` is the portable sleep on stock Windows.
    script = (
        f'@echo off\r\n'
        f'ping 127.0.0.1 -n 4 >nul\r\n'
        f'rmdir /s /q "{old}" 2>nul\r\n'
        f'move "{d}" "{old}" >nul 2>&1\r\n'
        f'move "{staging}" "{d}" >nul 2>&1\r\n'
        f'if not exist "{exe}" move "{old}" "{d}" >nul 2>&1\r\n'
        f'start "" "{exe}" {" ".join(argv)}\r\n'
        f'ping 127.0.0.1 -n 3 >nul\r\n'
        f'rmdir /s /q "{old}" 2>nul\r\n'
        f'del "%~f0"\r\n'
    )
    fd, path = tempfile.mkstemp(suffix=".cmd")
    with os.fdopen(fd, "w", encoding="ascii", errors="replace") as f:
        f.write(script)

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["cmd", "/c", path],
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        env=procenv.child_env(),
        cwd=os.environ.get("SystemRoot", "C:\\"),
    )
    log("  restarting to finish the update")


def check_and_apply(manifest, channel, cached, log, argv):
    """Returns True if an update was staged and this process should exit now."""
    try:
        newer, spec = available(manifest, channel)
        if not newer:
            return False
        log(f"Updating GreenCraft {_version.VERSION} -> {newer}...")
        staging = stage(spec, cached, log)
        if not staging:
            return False
        swap_and_relaunch(staging, argv, log)
        return True
    except Exception as e:
        # Never let a failed update stop someone playing.
        log(f"  update failed ({type(e).__name__}: {e}); continuing on this version")
        return False
