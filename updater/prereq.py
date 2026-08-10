"""Getting Tailscale and Prism Launcher onto a machine that has neither.

Two different strategies on purpose:

  Prism    -- pinned in the manifest with a sha512 we verify. We control which version
              friends get, and a bad download is caught before it is executed.
  Tailscale -- installed through winget rather than a pinned download. It is the piece
              that touches the network stack, so it should track upstream security
              fixes on its own rather than being frozen at whatever version we last
              wrote into a manifest.
"""
import os
import platform
import shutil
import socket
import subprocess
import time
import urllib.request
import webbrowser
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
UA = "GreenCraft-updater/0.1"


def _run(cmd, timeout=900, shell=False):
    return subprocess.run(
        cmd, shell=shell, capture_output=True, text=True,
        timeout=timeout, creationflags=CREATE_NO_WINDOW,
    )


def arch():
    m = (platform.machine() or "").upper()
    if m in ("ARM64", "AARCH64"):
        return "arm64"
    return "x64"


# ------------------------------------------------------------------- Tailscale


def tailscale_exe():
    for p in (
        Path(os.environ.get("ProgramFiles", "")) / "Tailscale" / "tailscale.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Tailscale" / "tailscale.exe",
    ):
        if p.is_file():
            return str(p)
    return shutil.which("tailscale")


def tailscale_installed():
    return tailscale_exe() is not None


def tailscale_status():
    """Returns 'running', 'stopped', 'logged-out', or None if not installed."""
    exe = tailscale_exe()
    if not exe:
        return None
    try:
        r = _run([exe, "status", "--json"], timeout=30)
        import json
        d = json.loads(r.stdout or "{}")
        state = (d.get("BackendState") or "").lower()
        if state == "running":
            return "running"
        if state in ("stopped",):
            return "stopped"
        return "logged-out"
    except Exception:
        return "logged-out"


def install_tailscale(log):
    if tailscale_installed():
        log("  already installed")
        return True

    if shutil.which("winget"):
        log("  installing via winget (Windows will ask for permission)...")
        r = _run([
            "winget", "install", "--id", "tailscale.tailscale",
            "--silent", "--accept-package-agreements", "--accept-source-agreements",
        ], timeout=1800)
        if tailscale_installed():
            log("  installed")
            return True
        log(f"  winget did not complete (exit {r.returncode})")
    else:
        log("  winget is not available on this PC")

    # Rather than shipping a pinned installer for the one component that sits on the
    # network stack, send them to the source and wait.
    log("  opening https://tailscale.com/download - install it, then come back")
    webbrowser.open("https://tailscale.com/download")
    for _ in range(120):          # ~10 minutes
        time.sleep(5)
        if tailscale_installed():
            log("  detected - thanks")
            return True
    return False


def tailscale_up(log):
    """Bring the node up. Opens a browser for sign-in if it is not logged in yet."""
    exe = tailscale_exe()
    if not exe:
        return False
    state = tailscale_status()
    if state == "running":
        log("  already connected")
        return True
    log("  connecting (a browser window will open for sign-in)...")
    # --unattended keeps it connected when the user logs out of Windows; without it
    # the machine silently drops off the tailnet.
    try:
        _run([exe, "up", "--unattended"], timeout=600)
    except subprocess.TimeoutExpired:
        log("  sign-in timed out")
        return False
    return tailscale_status() == "running"


def open_invite(url, log):
    if not url:
        return
    log("  opening your invite link...")
    webbrowser.open(url)


def wait_for_server(host, port, log, timeout=600):
    """Poll until the shared machine answers. This is what actually proves the share
    was accepted -- the client can be connected to its own tailnet and still not see
    wheatley until the invite is taken."""
    log(f"  waiting for {host} to become reachable...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5):
                log("  reachable")
                return True
        except OSError:
            time.sleep(4)
    return False


# ----------------------------------------------------------------------- Prism


def install_prism(spec, cached, log):
    """spec is manifest.prerequisites.prism[<arch>]: url, sha512, fileSize, filename."""
    a = arch()
    if a not in spec:
        raise RuntimeError(f"no Prism Launcher build listed for {a}")
    s = spec[a]
    log(f"  downloading Prism Launcher {spec.get('version', '')} ({a})...")
    path, _ = cached(s["sha512"], s["filename"], s["downloads"], s.get("fileSize"))
    log("  running the installer...")
    # NSIS: /S is silent, and Prism installs per-user by default, so no admin prompt.
    r = _run([str(path), "/S"], timeout=1800)
    if r.returncode not in (0, None):
        log(f"  installer exited {r.returncode}")
    return r.returncode == 0
