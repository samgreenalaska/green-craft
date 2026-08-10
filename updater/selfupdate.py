"""Replace GreenCraft.exe with a newer build advertised by the manifest.

Windows will not let you overwrite a running executable, but it *will* let you rename
one. So: rename ourselves aside, write the new bytes to our own path, relaunch, and
delete the leftover on the next start. No helper script, no scheduled task, nothing to
leave behind if the machine loses power halfway.
"""
import os
import subprocess
import sys
from pathlib import Path

import version as _version

OLD_SUFFIX = ".old.exe"


def running_exe():
    """The exe path, or None when running from source (development)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None


def cleanup_old(log=lambda *_: None):
    """Delete the previous build left behind by an update."""
    exe = running_exe()
    if not exe:
        return
    old = exe.with_name(exe.name + OLD_SUFFIX)
    if old.exists():
        try:
            old.unlink()
            log(f"  removed previous version {old.name}")
        except OSError:
            pass  # still locked; next run gets it


def available(manifest, channel):
    """(version, spec) if the manifest advertises a newer launcher, else (None, None)."""
    lc = (manifest.get("channels", {}).get(channel, {}) or {}).get("launcher") or {}
    v = lc.get("version")
    if not v or not lc.get("downloads") or not (lc.get("hashes") or {}).get("sha512"):
        return None, None
    if not _version.is_newer(v):
        return None, None
    return v, lc


def apply(spec, cached, log):
    """Swap in the new build. Returns the new exe path, or None if not applicable."""
    exe = running_exe()
    if not exe:
        log("  (running from source - skipping self-update)")
        return None

    src, _ = cached(spec["hashes"]["sha512"], spec.get("filename", "GreenCraft.exe"),
                    spec["downloads"], spec.get("fileSize"))

    old = exe.with_name(exe.name + OLD_SUFFIX)
    if old.exists():
        try:
            old.unlink()
        except OSError:
            pass

    # Rename-then-write. If anything fails after the rename, put it back -- leaving a
    # machine with no GreenCraft.exe would be worse than not updating.
    os.replace(exe, old)
    try:
        with open(exe, "wb") as f:
            f.write(Path(src).read_bytes())
    except Exception:
        os.replace(old, exe)
        raise
    return exe


def relaunch(exe, argv):
    """Start the new build with our arguments and let this process die."""
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        [str(exe)] + list(argv),
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )


def check_and_apply(manifest, channel, cached, log, argv):
    """Full cycle. Returns True if we replaced ourselves and relaunched."""
    try:
        newer, spec = available(manifest, channel)
        if not newer:
            return False
        log(f"Updating GreenCraft {_version.VERSION} -> {newer}...")
        exe = apply(spec, cached, log)
        if not exe:
            return False
        log("  restarting")
        relaunch(exe, argv)
        return True
    except Exception as e:
        # An update failure must never block playing. Log it and carry on with the
        # build we already have.
        log(f"  update failed ({type(e).__name__}: {e}); continuing on this version")
        return False
