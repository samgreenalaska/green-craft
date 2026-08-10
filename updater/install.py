"""Installation state: shortcuts, Apps & Features registration, uninstall.

Everything lives under HKEY_CURRENT_USER and %LOCALAPPDATA%, so nothing here needs
administrator rights. The only step in the whole product that needs elevation is
installing Tailscale, and that is its own installer's prompt.
"""
import json
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

APP_NAME = "GreenCraft"
APP_VERSION = "0.0.0"
PUBLISHER = "crazysam"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\GreenCraft"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", ""))
APPDATA = Path(os.environ.get("APPDATA", ""))
INSTALL_DIR = LOCALAPPDATA / "GreenCraft"
STATE_FILE = INSTALL_DIR / "install.json"
CACHE_DIR = INSTALL_DIR / "cache"

CREATE_NO_WINDOW = 0x08000000


def desktop():
    return Path(os.path.expanduser("~")) / "Desktop"


def start_menu():
    return APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs"


# ------------------------------------------------------------------ install state


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state):
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")


def is_installed():
    st = load_state()
    return bool(st.get("installed"))


def exe_path():
    """Where GreenCraft.exe actually is. Frozen: the exe. Source: this checkout."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(__file__).resolve().parent / "greencraft.py"


# --------------------------------------------------------------------- shortcuts


def make_shortcut(path, target, args, description, icon=None, workdir=None):
    """Create a .lnk.

    Done through PowerShell's WScript.Shell rather than a Python COM binding so the
    packaged exe needs no extra dependency. CREATE_NO_WINDOW keeps a console from
    flashing up during a GUI install.
    """
    ps = [
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(%s)" % _q(str(path)),
        "$s.TargetPath = %s" % _q(str(target)),
        "$s.Arguments = %s" % _q(args or ""),
        "$s.WorkingDirectory = %s" % _q(str(workdir or Path(target).parent)),
        "$s.Description = %s" % _q(description),
    ]
    if icon:
        ps.append("$s.IconLocation = %s" % _q(icon))
    ps.append("$s.Save()")
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", "; ".join(ps)],
        capture_output=True, creationflags=CREATE_NO_WINDOW, check=False,
    )
    return path.exists()


def _q(s):
    return "'" + str(s).replace("'", "''") + "'"


def shortcut_targets(experimental):
    """(filename, channel) pairs. Names have no parentheses -- they read badly in the
    Start Menu and are awkward to type."""
    out = [("GreenCraft.lnk", "stable")]
    if experimental:
        out.append(("GreenCraft Experimental.lnk", "experimental"))
    return out


def create_shortcuts(exe, experimental, on_desktop=True, in_start_menu=True, icon=None):
    made = []
    for fname, channel in shortcut_targets(experimental):
        desc = f"{APP_NAME} - {'experimental test' if channel == 'experimental' else 'play'} channel"
        for folder, want in ((desktop(), on_desktop), (start_menu(), in_start_menu)):
            if not want:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            p = folder / fname
            if make_shortcut(p, exe, f"--channel {channel}", desc, icon):
                made.append(str(p))
    return made


def remove_shortcuts():
    removed = []
    for folder in (desktop(), start_menu()):
        for fname in ("GreenCraft.lnk", "GreenCraft Experimental.lnk",
                      "GreenCraft (Experimental).lnk"):  # legacy name
            p = folder / fname
            if p.exists():
                try:
                    p.unlink()
                    removed.append(str(p))
                except OSError:
                    pass
    return removed


# ------------------------------------------------------- Apps & Features listing


def register_uninstall(exe, install_dir, size_kb=0):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0,
                            winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, f'"{exe}" --uninstall')
        winreg.SetValueEx(k, "QuietUninstallString", 0, winreg.REG_SZ,
                          f'"{exe}" --uninstall --quiet')
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "URLInfoAbout", 0, winreg.REG_SZ,
                          "https://github.com/samgreenalaska/green-craft")
        if size_kb:
            winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, int(size_kb))


def unregister_uninstall():
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        return True
    except FileNotFoundError:
        return False


def is_registered():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY):
            return True
    except FileNotFoundError:
        return False


# ------------------------------------------------------------------- uninstall


def instance_dirs(state):
    root = APPDATA / "PrismLauncher" / "instances"
    return [root / i for i in state.get("instances", []) if (root / i).is_dir()]


def world_count(state):
    """How many saved worlds are inside the instances we created.

    Surfaced before anything is deleted: these are the only irreplaceable thing the
    uninstaller can touch, and on a multiplayer-only install there are usually none.
    """
    n = 0
    for d in instance_dirs(state):
        saves = d / "minecraft" / "saves"
        if saves.is_dir():
            n += sum(1 for p in saves.iterdir() if p.is_dir())
    return n


def find_installed(pattern):
    """Find Apps & Features entries whose DisplayName contains `pattern`.

    Checks HKLM (both registry views) and HKCU, because per-machine and per-user
    installers land in different places and 32/64-bit ones differ again.
    """
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, 0),
    ]
    base = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    seen, out = set(), []
    for hive, view in roots:
        try:
            with winreg.OpenKey(hive, base, 0, winreg.KEY_READ | view) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        sub = winreg.EnumKey(k, i)
                        with winreg.OpenKey(k, sub, 0, winreg.KEY_READ | view) as s:
                            def val(n):
                                try:
                                    return winreg.QueryValueEx(s, n)[0]
                                except FileNotFoundError:
                                    return ""
                            name = val("DisplayName")
                            if not name or pattern.lower() not in name.lower():
                                continue
                            quiet = val("QuietUninstallString")
                            normal = val("UninstallString")
                            if not (quiet or normal):
                                continue
                            key = (name, val("DisplayVersion"), quiet or normal)
                            if key in seen:
                                continue
                            seen.add(key)
                            out.append({
                                "name": name,
                                "version": val("DisplayVersion"),
                                "quiet": quiet,
                                "uninstall": normal,
                            })
                    except OSError:
                        continue
        except FileNotFoundError:
            continue
    return out


def uninstall_program(pattern, log=print, quiet=True):
    """Run a third-party uninstaller.

    Prefers an entry that has a QuietUninstallString: Tailscale registers twice (the
    MSI and the bundle that wraps it), and only the bundle uninstalls cleanly -- going
    at the MSI directly leaves the bundle's entry behind.
    """
    entries = find_installed(pattern)
    if not entries:
        log(f"  {pattern}: not installed")
        return False
    entries.sort(key=lambda e: 0 if e["quiet"] else 1)
    e = entries[0]
    cmd = (e["quiet"] if quiet and e["quiet"] else e["uninstall"])
    log(f"  {e['name']} {e['version']}: running uninstaller")
    if "tailscale" in pattern.lower():
        log("    (Windows will ask for permission -- Tailscale runs a system service)")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            log(f"    removed")
            return True
        log(f"    uninstaller exited {r.returncode}")
        return False
    except subprocess.TimeoutExpired:
        log("    timed out -- finish it from Apps & Features")
        return False
    except Exception as ex:
        log(f"    failed: {ex}")
        return False


def uninstall(remove_game_data=False, log=print):
    state = load_state()
    log("Removing shortcuts...")
    for s in remove_shortcuts():
        log(f"  removed {Path(s).name}")

    log("Removing Apps & Features entry...")
    log("  removed" if unregister_uninstall() else "  (was not registered)")

    if remove_game_data:
        for d in instance_dirs(state):
            log(f"Removing instance {d.name} (including any worlds)...")
            shutil.rmtree(d, ignore_errors=True)
    else:
        kept = [d.name for d in instance_dirs(state)]
        if kept:
            log(f"Keeping game data: {', '.join(kept)}")

    if CACHE_DIR.is_dir():
        log("Removing download cache...")
        shutil.rmtree(CACHE_DIR, ignore_errors=True)

    state["installed"] = False
    save_state(state)
    log()
    log("GreenCraft has been uninstalled.")


def prism_data_dir():
    return APPDATA / "PrismLauncher"


def prism_leftovers(state):
    """What removing Prism's program would leave behind.

    Prism's uninstaller removes ~70 MB of program files; instances, worlds, Minecraft
    itself and the bundled Java live in %APPDATA%\\PrismLauncher and are untouched --
    which is why reinstalling keeps your instances. Returns (bytes, other_instances)
    where other_instances are ones GreenCraft did not create.
    """
    d = prism_data_dir()
    if not d.is_dir():
        return 0, []
    ours = set(state.get("instances", []))
    inst_root = d / "instances"
    others = sorted(
        p.name for p in inst_root.iterdir()
        if p.is_dir() and p.name not in ours
    ) if inst_root.is_dir() else []
    size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
    return size, others


def uninstall_selected(opts, log=print):
    """Uninstall whichever components the user ticked.

    Order matters: GreenCraft's own shortcuts and registry entry go first, so that if
    a third-party uninstaller stalls or is cancelled at its UAC prompt, GreenCraft is
    not left half-registered pointing at things that are gone.
    """
    state = load_state()

    if opts.get("greencraft"):
        log("Removing GreenCraft...")
        uninstall(remove_game_data=False, log=lambda m="": log("  " + str(m) if m else ""))

        # The instance is a GreenCraft artifact -- nothing else will ever clean it up,
        # since Prism's uninstaller does not touch %APPDATA%\PrismLauncher.
        dirs = instance_dirs(state)
        if dirs:
            if opts.get("keep_worlds"):
                saved = _rescue_worlds(dirs, log)
                if saved:
                    log(f"  saved worlds to {saved}")
            log("  removing Minecraft instance...")
            for d in dirs:
                log(f"    {d.name}")
                shutil.rmtree(d, ignore_errors=True)

    if opts.get("prism"):
        log("Removing Prism Launcher...")
        uninstall_program("Prism Launcher", log)

        size, others = prism_leftovers(state)
        if others:
            # Someone else's instances live here. Refuse rather than take 12 GB of
            # unrelated worlds with us.
            log(f"  keeping {prism_data_dir()} ({size / 1024 ** 3:.1f} GB)")
            log(f"  it still holds {len(others)} other instance(s): {', '.join(others)}")
        elif size:
            log(f"  removing leftover game data ({size / 1024 ** 3:.1f} GB)...")
            shutil.rmtree(prism_data_dir(), ignore_errors=True)

    if opts.get("tailscale"):
        log("Removing Tailscale...")
        uninstall_program("Tailscale", log)

    log()
    log("Done.")


def _rescue_worlds(dirs, log):
    """Move saved worlds somewhere safe before deleting an instance."""
    dest = Path(os.path.expanduser("~")) / "Documents" / "GreenCraft worlds"
    moved = 0
    for d in dirs:
        saves = d / "minecraft" / "saves"
        if not saves.is_dir():
            continue
        for w in saves.iterdir():
            if not w.is_dir():
                continue
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / w.name
            n = 1
            while target.exists():
                target = dest / f"{w.name} ({n})"
                n += 1
            try:
                shutil.move(str(w), str(target))
                moved += 1
            except OSError as e:
                log(f"    could not move {w.name}: {e}")
    return dest if moved else None
