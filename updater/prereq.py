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
    """The machine's NATIVE architecture: "x64" or "arm64".

    Must not use platform.machine() or %PROCESSOR_ARCHITECTURE%. GreenCraft.exe is
    built x64, so on an ARM64 machine it runs under emulation and both of those report
    AMD64 -- we would install the x64 mod set on ARM and it would crash on world join.
    Git Bash has the same problem, and %PROCESSOR_ARCHITEW6432% is empty there too, so
    the usual WOW64 tell does not save you either.

    IsWow64Process2's nativeMachine is the authoritative answer. Verified against
    Win32_ComputerSystem.SystemType.
    """
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetCurrentProcess.argtypes = []
        k.IsWow64Process2.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.USHORT),
            ctypes.POINTER(wintypes.USHORT),
        ]
        k.IsWow64Process2.restype = wintypes.BOOL
        pm, nm = wintypes.USHORT(), wintypes.USHORT()
        if k.IsWow64Process2(k.GetCurrentProcess(), ctypes.byref(pm), ctypes.byref(nm)):
            if nm.value == 0xAA64:      # IMAGE_FILE_MACHINE_ARM64
                return "arm64"
            if nm.value == 0x8664:      # IMAGE_FILE_MACHINE_AMD64
                return "x64"
    except Exception:
        pass

    # Fallbacks, in decreasing reliability.
    if (os.environ.get("PROCESSOR_ARCHITEW6432") or "").upper() in ("ARM64", "AARCH64"):
        return "arm64"
    m = (platform.machine() or "").upper()
    return "arm64" if m in ("ARM64", "AARCH64") else "x64"


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


def tailscale_auth_url():
    """The sign-in URL the daemon is waiting on, if any."""
    exe = tailscale_exe()
    if not exe:
        return None
    try:
        import json
        r = _run([exe, "status", "--json"], timeout=30)
        d = json.loads(r.stdout or "{}")
        return d.get("AuthURL") or None
    except Exception:
        return None


def set_unattended(log):
    """Keep Tailscale connected after the user logs out of Windows.

    Done as a separate `tailscale set` after login rather than as `up --unattended`,
    so the persistence setting is not entangled with the interactive sign-in.
    """
    exe = tailscale_exe()
    try:
        _run([exe, "set", "--unattended"], timeout=60)
    except Exception:
        pass


def tailscale_up(log, timeout=600):
    """Sign in, surfacing the auth URL and opening it.

    `tailscale up` prints its auth URL to stdout and then **blocks forever** waiting
    for the user to authenticate -- its --timeout defaults to 0s, which means no
    limit. A previous version ran it with captured output and no timeout of its own,
    so the URL went into a pipe nobody read, no browser ever opened, and setup sat on
    a spinning bar indefinitely with nothing in the log. That was a install-blocking
    bug for every first-time user.

    So: start it detached with its output discarded, read the URL from
    `status --json` instead, open it ourselves, and always print it in case the
    browser does not appear. Kill the child on every exit path.
    """
    exe = tailscale_exe()
    if not exe:
        return False

    if tailscale_status() == "running":
        log("  already connected")
        set_unattended(log)
        return True

    log("  starting sign-in...")
    proc = None
    try:
        proc = subprocess.Popen(
            [exe, "up", "--timeout", f"{int(timeout)}s"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )

        # The daemon publishes AuthURL shortly after `up` starts.
        url = None
        for _ in range(30):
            if tailscale_status() == "running":
                break
            url = tailscale_auth_url()
            if url:
                break
            time.sleep(1)

        if url:
            log("")
            log("  Sign in to Tailscale in your browser:")
            log(f"  {url}")
            log("")
            try:
                webbrowser.open(url)
            except Exception:
                log("  (could not open a browser automatically - copy the link above)")

        deadline = time.time() + timeout
        while time.time() < deadline:
            if tailscale_status() == "running":
                log("  connected")
                set_unattended(log)
                return True
            if proc.poll() is not None and tailscale_status() != "running":
                # `up` exited without reaching Running -- give the daemon a moment,
                # then believe it.
                time.sleep(3)
                if tailscale_status() == "running":
                    log("  connected")
                    set_unattended(log)
                    return True
                break
            time.sleep(2)

        log("  sign-in did not complete")
        if url:
            log(f"  Finish signing in at {url} and run GreenCraft again.")
        return False
    finally:
        # Never leave a blocked `tailscale up` behind. Earlier builds leaked one per
        # attempt, outliving the installer that spawned them.
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


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


def seed_prism_config(log):
    """Pre-answer Prism's Quick Setup wizard.

    Without this, launching an instance drops the user into Prism's setup: a 60-entry
    language table, then a theme picker, then accounts -- with nothing on screen saying
    GreenCraft, right after setup promised "Minecraft will open on the title screen".

    Only written when there is no config at all, so an existing Prism install keeps its
    own settings. The account page still appears, which is correct: they do have to
    sign in to Microsoft.
    """
    cfg = Path(os.environ.get("APPDATA", "")) / "PrismLauncher" / "prismlauncher.cfg"
    if cfg.exists():
        return False
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "[General]\n"
        "ConfigVersion=1.3\n"
        "Language=en_US\n"
        "ApplicationTheme=dark\n"
        "IconTheme=pe_colored\n"
        "MaxMemAlloc=4096\n"
        "MinMemAlloc=512\n",
        encoding="utf-8",
    )
    log("  pre-configured Prism so its setup wizard is skipped")
    return True


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
