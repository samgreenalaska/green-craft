"""GreenCraftSetup.exe -- fetch the payload, unpack it, hand over.

Deliberately tiny and dependency-free. It is the only onefile binary left, so it is
the only thing that still unpacks to %TEMP%; keeping it small keeps that window short.
Everything interesting lives in the payload.

Flow:
    read manifest -> download GreenCraft-<version>.zip -> verify sha512
    -> unpack to %LOCALAPPDATA%\\GreenCraft\\app -> run app\\GreenCraft.exe --setup
"""
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

MANIFEST_URL = "https://raw.githubusercontent.com/samgreenalaska/green-craft/main/manifest.json"
UA = "GreenCraftSetup/1"

INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "GreenCraft"
APP_DIR = INSTALL_DIR / "app"
APP_EXE = APP_DIR / "GreenCraft.exe"

ALLOWED_HOSTS = {
    "github.com", "objects.githubusercontent.com",
    "release-assets.githubusercontent.com", "raw.githubusercontent.com",
}


def message(text, title="GreenCraft Setup", flags=0x40):
    """No console and no tkinter here, so failures go through the Win32 message box."""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:
        pass


def fetch(url, timeout=300):
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"refusing download from {host}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def unpack(zip_bytes, dest):
    """Replace dest with the archive contents.

    Staged in a sibling directory and swapped, so an interrupted download cannot leave
    a half-written app folder behind.
    """
    dest = Path(dest)
    staging = dest.with_name(dest.name + ".new")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tf:
        tf.write(zip_bytes)
        tmp = tf.name
    try:
        with zipfile.ZipFile(tmp) as z:
            for name in z.namelist():
                # Reject absolute paths and traversal before writing anything.
                target = (staging / name).resolve()
                if not str(target).startswith(str(staging.resolve())):
                    raise ValueError(f"unsafe path in archive: {name}")
                if name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(name))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    old = dest.with_name(dest.name + ".old")
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        os.replace(dest, old)
    os.replace(staging, dest)
    shutil.rmtree(old, ignore_errors=True)


def main():
    argv = sys.argv[1:]
    try:
        manifest = json.loads(fetch(MANIFEST_URL, timeout=60).decode("utf-8"))
    except Exception as e:
        message(f"Could not reach GitHub to download GreenCraft.\n\n{e}\n\n"
                "Check your internet connection and try again.", flags=0x10)
        return 1

    ch = (manifest.get("channels") or {}).get("stable") or {}
    lc = ch.get("launcher") or {}
    payload = lc.get("payload") or {}
    urls = payload.get("downloads") or []
    want = (payload.get("hashes") or {}).get("sha512")
    if not urls or not want:
        message("This version of the installer is out of date and the download "
                "information is missing.\n\nAsk Sam for a newer installer.", flags=0x10)
        return 1

    data = None
    last = None
    for url in urls:
        try:
            data = fetch(url)
            if hashlib.sha512(data).hexdigest() != want:
                last = "the download did not match its checksum"
                data = None
                continue
            break
        except Exception as e:
            last = str(e)
    if data is None:
        message(f"Could not download GreenCraft.\n\n{last}", flags=0x10)
        return 1

    try:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        unpack(data, APP_DIR)
    except Exception as e:
        message(f"Could not install GreenCraft.\n\n{e}", flags=0x10)
        return 1

    if not APP_EXE.exists():
        message("The download unpacked but GreenCraft.exe is missing.", flags=0x10)
        return 1

    try:
        subprocess.Popen([str(APP_EXE), *(argv or ["--setup"])],
                         cwd=str(APP_DIR), close_fds=True)
    except Exception as e:
        message(f"Installed, but could not start GreenCraft.\n\n{e}\n\n"
                f"Try running:\n{APP_EXE}", flags=0x10)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
