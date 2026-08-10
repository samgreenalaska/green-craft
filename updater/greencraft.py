"""GreenCraft updater -- sync a Prism instance to a manifest channel, then launch.

Prism does the heavy lifting: Minecraft, Fabric, Java and assets are its problem. This
only owns the instance skeleton, the content it installs (mods, shaderpacks,
resourcepacks), the seeded overrides, and the server list entry.

The rule that matters: only ever delete files we put there. Everything installed is
recorded in .greencraft/installed.json with its hash. A file whose hash no longer
matches has been edited by the user, so it is left alone and reported.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nbt

DEFAULT_MANIFEST = "https://raw.githubusercontent.com/samgreenalaska/green-craft/main/manifest.json"
UA = "GreenCraft-updater/0.1"

APPDATA = Path(os.environ.get("APPDATA", ""))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", ""))
PRISM_INSTANCES = APPDATA / "PrismLauncher" / "instances"
CACHE = LOCALAPPDATA / "GreenCraft" / "cache"

# Downloads are restricted to these hosts. The manifest is an instruction to execute
# code on someone else's machine; it does not get to name arbitrary origins.
ALLOWED_HOSTS = {
    "cdn.modrinth.com", "api.modrinth.com",
    "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
    "raw.githubusercontent.com",
    "edge.forgecdn.net", "mediafilez.forgecdn.net",
    "launcher.mojang.com", "piston-data.mojang.com", "resources.download.minecraft.net",
    "maven.fabricmc.net",
}


LOG_FILE = LOCALAPPDATA / "GreenCraft" / "logs" / "greencraft.log"
_log_fh = None


def log(msg=""):
    """Print if there is anywhere to print to, and always append to the log file.

    A --noconsole PyInstaller build has sys.stdout set to None, so a bare print()
    raises AttributeError and takes the whole run down. The log file is then the only
    record of what happened, which is exactly when someone needs it.
    """
    global _log_fh
    msg = str(msg)
    if sys.stdout is not None:
        try:
            print(msg, flush=True)
        except Exception:
            pass
    try:
        if _log_fh is None:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            _log_fh = open(LOG_FILE, "a", encoding="utf-8")
            _log_fh.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} "
                          f"{' '.join(sys.argv[1:]) or '(no args)'} ---\n")
        _log_fh.write(msg + "\n")
        _log_fh.flush()
    except Exception:
        pass


def find_prism():
    """Locate prismlauncher.exe.

    Prism's own installer does not put itself on PATH, so shutil.which() alone finds
    nothing on a normal install. Check the bundled copy first -- once GreenCraft ships
    with Prism beside it, that is the one we want, not whatever the user already had.
    """
    here = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    candidates = [
        here / "prism" / "prismlauncher.exe",
        here / "PrismLauncher" / "prismlauncher.exe",
        LOCALAPPDATA / "Programs" / "PrismLauncher" / "prismlauncher.exe",
        Path(os.environ.get("ProgramFiles", "")) / "PrismLauncher" / "prismlauncher.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "PrismLauncher" / "prismlauncher.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("prismlauncher")
    return found


def sha512_file(p):
    h = hashlib.sha512()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def host_of(url):
    from urllib.parse import urlparse
    return (urlparse(url).hostname or "").lower()


def fetch(url, timeout=60):
    if url.split(":", 1)[0] != "https":
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_manifest(src):
    if src.startswith("https://"):
        return json.loads(fetch(src).decode("utf-8"))
    return json.loads(Path(src).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- cache


def cached(sha512, filename, urls, size=None):
    """Return a path to a verified copy, downloading into the cache if needed."""
    slot = CACHE / sha512[:2] / sha512[2:16]
    slot.mkdir(parents=True, exist_ok=True)
    dest = slot / filename

    if dest.exists() and sha512_file(dest) == sha512:
        return dest, False

    last = None
    for url in urls:
        h = host_of(url)
        if h not in ALLOWED_HOSTS:
            last = f"host not allowed: {h}"
            continue
        try:
            data = fetch(url, timeout=300)
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            continue
        got = hashlib.sha512(data).hexdigest()
        if got != sha512:
            last = f"sha512 mismatch from {h}"
            continue
        if size is not None and len(data) != size:
            last = f"size {len(data)} != {size}"
            continue
        # Write to a temp file in the same directory, then rename: a half-written jar
        # must never appear under its final name.
        fd, tmp = tempfile.mkstemp(dir=slot)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        return dest, True

    raise RuntimeError(f"could not obtain {filename}: {last}")


def link_or_copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)      # same volume: no second copy on disk
    except OSError:
        shutil.copy2(src, dst)


# ------------------------------------------------------------------- instance setup


def split_java_args(java_args):
    """Separate memory flags from the rest.

    Prism refuses -Xmx/-Xms/-XX:*Size in JvmArgs and pops a modal warning instead of
    launching -- it has dedicated MinMemAlloc/MaxMemAlloc fields and wants those used.
    Returns (min_mb, max_mb, remaining_args).
    """
    if not java_args:
        return None, None, ""

    def to_mb(v):
        v = v.strip().lower()
        mult = 1
        if v.endswith("g"):
            mult, v = 1024, v[:-1]
        elif v.endswith("m"):
            mult, v = 1, v[:-1]
        elif v.endswith("k"):
            return max(1, int(v[:-1]) // 1024)
        try:
            return int(float(v) * mult)
        except ValueError:
            return None

    lo = hi = None
    rest = []
    for tok in java_args.split():
        t = tok.lower()
        if t.startswith("-xmx"):
            hi = to_mb(tok[4:])
        elif t.startswith("-xms"):
            lo = to_mb(tok[4:])
        elif t.startswith("-xx:maxheapsize="):
            hi = to_mb(tok.split("=", 1)[1])
        elif t.startswith("-xx:initialheapsize="):
            lo = to_mb(tok.split("=", 1)[1])
        elif t.startswith("-xx:permsize=") or t.startswith("-xx:maxpermsize="):
            pass  # dead on modern JVMs; drop rather than trip Prism's warning
        else:
            rest.append(tok)
    return lo, hi, " ".join(rest)


def ensure_instance(inst_root, channel_name, deps, java_args):
    """Create the Prism instance skeleton if it isn't there. Never overwrites an
    existing instance.cfg -- that file holds the user's own memory/Java settings."""
    inst_root.mkdir(parents=True, exist_ok=True)
    (inst_root / "minecraft").mkdir(exist_ok=True)

    pack = inst_root / "mmc-pack.json"
    if not pack.exists():
        mc = deps["minecraft"]
        loader = deps["fabric-loader"]
        pack.write_text(json.dumps({
            "components": [
                {"cachedName": "Minecraft", "important": True,
                 "uid": "net.minecraft", "version": mc},
                {"cachedName": "Intermediary Mappings", "cachedVolatile": True,
                 "dependencyOnly": True,
                 "uid": "net.fabricmc.intermediary", "version": mc},
                {"cachedName": "Fabric Loader",
                 "uid": "net.fabricmc.fabric-loader", "version": loader},
            ],
            "formatVersion": 1,
        }, indent=4) + "\n", encoding="utf-8")
        created_pack = True
    else:
        created_pack = False

    lo, hi, rest = split_java_args(java_args)

    cfg = inst_root / "instance.cfg"
    if not cfg.exists():
        lines = [
            "[General]",
            "ConfigVersion=1.3",
            "InstanceType=OneSix",
            f"name={channel_name}",
            "iconKey=default",
            "OverrideCommands=false",
            "JoinServerOnLaunch=false",
        ]
        if lo or hi:
            lines.append("OverrideMemory=true")
            if lo:
                lines.append(f"MinMemAlloc={lo}")
            if hi:
                lines.append(f"MaxMemAlloc={hi}")
        lines.append(f"OverrideJavaArgs={'true' if rest else 'false'}")
        if rest:
            lines.append(f"JvmArgs={rest}")
        # Prism compares the heap cap against *free* RAM, not installed RAM, so a
        # 4 GB cap on a busy 16 GB machine raises "there might not be enough free RAM
        # ... this may cause slowdowns in your system" before the user has played a
        # second. The cap is deliberate and sized for this pack; the warning is noise.
        lines.append("LowMemWarning=false")
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        created_cfg = True
    else:
        created_cfg = False
        # Repair an instance.cfg we previously wrote with memory flags in JvmArgs.
        # Prism shows a modal warning for those and refuses to launch until they go.
        # Only touches the JvmArgs line, and only if it still contains a memory flag.
        try:
            text = cfg.read_text(encoding="utf-8")
        except OSError:
            text = ""
        out, changed = [], False
        for line in text.splitlines():
            if line.startswith("JvmArgs=") and any(
                k in line.lower() for k in ("-xmx", "-xms", "heapsize", "permsize")
            ):
                l2, h2, r2 = split_java_args(line.split("=", 1)[1])
                changed = True
                if h2 or l2:
                    out.append("OverrideMemory=true")
                    if l2:
                        out.append(f"MinMemAlloc={l2}")
                    if h2:
                        out.append(f"MaxMemAlloc={h2}")
                if r2:
                    out.append(f"JvmArgs={r2}")
                else:
                    out.append("OverrideJavaArgs=false")
                continue
            if changed and line.startswith(("OverrideMemory=", "MinMemAlloc=", "MaxMemAlloc=")):
                continue  # superseded by what we just wrote
            out.append(line)
        if changed:
            cfg.write_text("\n".join(out) + "\n", encoding="utf-8")
            log("  repaired instance.cfg: moved memory flags out of JvmArgs")

    return created_pack or created_cfg


# ------------------------------------------------------------------------ lockfile


def load_lock(mcdir):
    p = mcdir / ".greencraft" / "installed.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"files": {}, "overridesVersion": None, "channel": None}


def save_lock(mcdir, lock):
    p = mcdir / ".greencraft" / "installed.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(lock, indent=1) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------- sync


def wanted_files(channel, platform_name=None):
    """The files that belong on THIS machine.

    A file may carry "platforms": ["x64"] to mean "only install here on x64". Absent
    means every platform. This is how the ARM64 variant is expressed -- as six
    exclusions on the shared list rather than a duplicated channel, so the two can
    never drift apart, and an ARM-only addition is just "platforms": ["arm64"].
    """
    if platform_name is None:
        import prereq
        platform_name = prereq.arch()
    out = {}
    for f in channel["pack"]["files"]:
        plats = f.get("platforms")
        if plats and platform_name not in plats:
            continue
        out[f["path"]] = f
    return out


def excluded_here(channel, platform_name):
    return [
        f["path"] for f in channel["pack"]["files"]
        if f.get("platforms") and platform_name not in f["platforms"]
    ]


def sync(channel, mcdir, dry_run=False):
    lock = load_lock(mcdir)
    owned = lock["files"]
    wanted = wanted_files(channel)

    added, updated, kept, skipped, removed = [], [], [], [], []

    for path, f in sorted(wanted.items()):
        dst = mcdir / path
        want = f["hashes"]["sha512"]

        if dst.exists():
            have = sha512_file(dst)
            if have == want:
                kept.append(path)
                owned[path] = want
                continue
            if path in owned and owned[path] != have:
                # We installed this, and it no longer matches what we wrote: the user
                # edited it. Replacing it silently would throw away their change.
                skipped.append(path)
                continue

        if dry_run:
            (updated if dst.exists() else added).append(path)
            continue

        src, downloaded = cached(want, Path(path).name, f["downloads"], f.get("fileSize"))
        link_or_copy(src, dst)
        (updated if path in owned else added).append(path)
        owned[path] = want

    # Anything we installed that is no longer in the manifest, and is still exactly as
    # we left it, is ours to remove. Anything else stays.
    for path, recorded in list(owned.items()):
        if path in wanted:
            continue
        p = mcdir / path
        if p.exists():
            if sha512_file(p) != recorded:
                skipped.append(path)
                continue
            if not dry_run:
                p.unlink()
        removed.append(path)
        if not dry_run:
            owned.pop(path, None)

    if not dry_run:
        lock["channel"] = channel.get("prismInstanceId")
        save_lock(mcdir, lock)

    return added, updated, kept, skipped, removed


def apply_overrides(channel, mcdir, lock, force=False):
    """Unpack the seeded bundle. First install only -- existing files are never
    overwritten, so tuned Sodium/Xaero settings survive every later update."""
    ov = channel.get("overrides")
    if not ov:
        return 0, 0
    if lock.get("overridesVersion") == channel["versionId"] and not force:
        return 0, 0

    src, _ = cached(ov["hashes"]["sha512"], ov["filename"], ov["downloads"], ov.get("fileSize"))
    written = preserved = 0
    with zipfile.ZipFile(src) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            dst = mcdir / name
            if dst.exists() and not force:
                preserved += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(z.read(name))
            written += 1

    lock["overridesVersion"] = channel["versionId"]
    save_lock(mcdir, lock)
    return written, preserved


# ---------------------------------------------------------------------------- main


def do_install(opts, log, manifest_src=DEFAULT_MANIFEST):
    """First-run setup, driven by the GUI. Runs on a worker thread; log() is the only
    way it may talk to the user."""
    import install as inst
    import prereq

    # ARM64 Windows cannot run this pack. Measured on a Snapdragon X / Adreno X1-85,
    # 2026-08-10. Startup is NOT the problem -- the game reaches the title screen and
    # the server shows in Multiplayer. It breaks on joining a world, twice over:
    #
    #   1. voxy bundles RocksDB, whose JNI jar ships x86-64 natives only (verified by
    #      listing META-INF/jars inside voxy-0.2.18-beta.jar: linux64 .so and win64 .dll,
    #      no aarch64). Joining any world throws UnsatisfiedLinkError, "Can't load
    #      AMD 64-bit .dll on a ARM 64-bit platform".
    #   2. Even with voxy disabled, the Windows-on-ARM OpenGL stack cannot sustain a
    #      world. Microsoft OpenGLOn12, Mesa Zink and Mesa D3D12 all fault identically
    #      in glBufferSubData; with Sodium's KHR_no_error context disabled it hangs on
    #      a GPU fence instead. Three independent implementations, same fault --
    #      upstream and unfixed (microsoft/OpenCLOn12#68, MCRcortex/voxy#538 closed as
    #      not planned).
    #
    # Shaders are separately impossible: the Adreno exposes 32 KB of compute shared
    # memory and the bundled pack wants ~36 KB, so Iris disables them.
    #
    # An earlier version of this comment blamed a Fabric preLaunch ClassCastException.
    # That was a test-harness artifact, not ARM64 -- see PLAN.md 8.5.
    if prereq.arch() == "arm64":
        log("ARM processor detected - using the ARM mod set.")
        log("Sodium, Iris (shaders), Nvidium, voxy (distant terrain) and Xaero's")
        log("World Map are left out: the Windows-on-ARM graphics driver crashes on")
        log("them. The minimap and everything else work normally.")
        log()

    log("Fetching the mod list...")
    m = load_manifest(manifest_src)
    prereqs = m.get("prerequisites", {})

    log()
    log("Setting up Tailscale...")
    if not prereq.install_tailscale(log):
        raise RuntimeError(
            "Tailscale was not installed. Install it from https://tailscale.com/download "
            "and run GreenCraft again."
        )
    if not prereq.tailscale_up(log):
        raise RuntimeError("Tailscale sign-in did not complete. Run GreenCraft again to retry.")

    # The share has to be accepted before the server is reachable. Being signed in to
    # your own tailnet is not enough.
    prereq.open_invite(opts.get("invite"), log)
    srv = m["channels"]["stable"]["server"]
    if not prereq.wait_for_server(srv["address"], int(srv["port"]), log, timeout=600):
        raise RuntimeError(
            f"Could not reach {srv['address']}. Make sure you opened the invite link "
            "and accepted the shared machine, then run GreenCraft again."
        )

    log()
    log("Setting up Prism Launcher...")
    prism = find_prism()
    if prism:
        log(f"  already installed: {prism}")
    else:
        prereq.install_prism(prereqs.get("prism", {}), cached, log)
        prism = find_prism()
        if not prism:
            raise RuntimeError(
                "Prism Launcher did not install. Get it from https://prismlauncher.org "
                "and run GreenCraft again."
            )
        log(f"  installed: {prism}")
    prereq.seed_prism_config(log)

    channels = ["stable"] + (["experimental"] if opts.get("experimental") else [])
    instances = []
    for name in channels:
        ch = m["channels"][name]
        inst_id = ch["prismInstanceId"]
        instances.append(inst_id)
        root = PRISM_INSTANCES / inst_id
        mcdir = root / "minecraft"
        log()
        log(f"Setting up the {name} channel...")
        ensure_instance(root, inst_id, ch["pack"]["dependencies"], ch.get("javaArgs"))
        added, updated, kept, skipped, removed = sync(ch, mcdir, False)
        log(f"  {len(added) + len(updated)} files installed, {len(kept)} already present")
        lock = load_lock(mcdir)
        w, _p = apply_overrides(ch, mcdir, lock)
        if w:
            log(f"  {w} settings files written")
        addr = ch["server"]["address"]
        if ch["server"].get("port") and int(ch["server"]["port"]) != 25565:
            addr = f"{addr}:{ch['server']['port']}"
        nbt.upsert_server(mcdir / "servers.dat", ch["server"]["name"], addr)
        log(f"  added '{ch['server']['name']}' to the server list")

    log()
    log("Creating shortcuts...")
    # icon=None means Windows uses the target's own icon, which is GreenCraft's -- it
    # is embedded in the exe at build time, so there is no separate file to ship.
    made = inst.create_shortcuts(
        inst.exe_path(), opts.get("experimental", False),
        on_desktop=opts.get("desktop", True),
        in_start_menu=opts.get("start_menu", True),
    )
    for p in made:
        log(f"  {Path(p).name}")

    log("Registering with Apps & Features...")
    size_kb = 0
    for i in instances:
        d = PRISM_INSTANCES / i
        if d.is_dir():
            size_kb += sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) // 1024
    inst.register_uninstall(inst.exe_path(), inst.INSTALL_DIR, size_kb)

    inst.save_state({
        "installed": True,
        "version": inst.APP_VERSION,
        "channels": channels,
        "instances": instances,
        "experimental": bool(opts.get("experimental")),
    })

    log()
    log("Done. GreenCraft is ready.")
    log()
    # Measured on a clean machine: Prism then downloads Minecraft itself, several
    # hundred MB across a few thousand files, taking minutes with no obvious progress.
    # Without warning, a first-timer concludes it has hung and kills it.
    log("The first launch downloads Minecraft itself and can take several minutes.")
    log("Later launches are quick.")
    log("Sign in with your Microsoft account when Prism asks, then pick GreenCraft")
    log("from Multiplayer once the title screen appears.")
    return {"instances": instances}


def _sync_and_prepare(args, log):
    """Everything the routine path does except actually starting Prism.

    Returns (prism_path_or_None, instance_id). Raising is how failure is reported --
    the progress window catches it and shows the log.
    """
    m = load_manifest(args.manifest)
    if args.channel not in m.get("channels", {}):
        raise RuntimeError(f"no such channel '{args.channel}' in the manifest")
    ch = m["channels"][args.channel]

    inst_id = args.instance or ch["prismInstanceId"]
    root = Path(args.instances_dir) if args.instances_dir else PRISM_INSTANCES
    mcdir = root / inst_id / "minecraft"

    log(f"Channel {args.channel}, version {ch['versionId']}")
    ensure_instance(root / inst_id, ch["prismInstanceId"],
                    ch["pack"]["dependencies"], ch.get("javaArgs"))

    log("Checking mods...")
    added, updated, kept, skipped, removed = sync(ch, mcdir, False)
    changed = len(added) + len(updated) + len(removed)
    log(f"{changed} change(s), {len(kept)} already up to date"
        if changed else "Everything is up to date")
    for p in skipped:
        log(f"left alone (edited locally): {p}")

    lock = load_lock(mcdir)
    apply_overrides(ch, mcdir, lock)

    addr = ch["server"]["address"]
    if ch["server"].get("port") and int(ch["server"]["port"]) != 25565:
        addr = f"{addr}:{ch['server']['port']}"
    nbt.upsert_server(mcdir / "servers.dat", ch["server"]["name"], addr)

    prism = args.prism or find_prism()
    if not prism:
        raise RuntimeError(
            "Prism Launcher was not found. Install it from https://prismlauncher.org "
            "and run GreenCraft again."
        )
    log("Starting Minecraft...")
    return prism, inst_id


def launch_detached(prism, inst_id):
    """Start Prism without keeping a handle on it.

    A PyInstaller onefile build unpacks itself to %TEMP%\\_MEInnnnnn and deletes that
    on exit. A child process that inherits handles keeps the directory busy, and the
    bootloader then pops "Failed to remove temporary directory". DETACHED_PROCESS plus
    close_fds means Prism holds nothing of ours and outlives us cleanly.
    """
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        [prism, "--launch", inst_id],
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )


def run_setup_gui():
    import gui
    import install as inst

    state = inst.load_state()
    win = gui.SetupWindow(
        lambda opts, log: do_install(opts, log),
        uninstall_work=lambda opts, log: inst.uninstall_selected(opts, log),
        world_count=inst.world_count(state),
        installed=inst.is_installed(),
    )
    result, launch = win.run()
    if result and launch:
        prism = find_prism()
        if prism:
            launch_detached(prism, result["instances"][0])
    return 0 if result else 1


def run_uninstall_gui(quiet=False):
    import install as inst

    state = inst.load_state()
    if quiet:
        inst.uninstall(remove_game_data=False, log=lambda m="": None)
        return 0
    import gui
    gui.UninstallWindow(inst.world_count(state), inst.uninstall).run()
    return 0


def main():
    ap = argparse.ArgumentParser(description="Sync a Prism instance to a GreenCraft channel.")
    ap.add_argument("--setup", action="store_true", help="force the first-run wizard")
    ap.add_argument("--install", action="store_true",
                    help="run setup headlessly, no window (for scripted testing)")
    ap.add_argument("--experimental", action="store_true",
                    help="with --install: also set up the experimental channel")
    ap.add_argument("--no-desktop", action="store_true",
                    help="with --install: skip the desktop shortcut")
    ap.add_argument("--start-menu", action="store_true",
                    help="with --install: add Start Menu shortcuts")
    ap.add_argument("--invite", default="",
                    help="with --install: Tailscale invite URL to open")
    ap.add_argument("--allow-unsupported", action="store_true",
                    help="with --install: proceed on unsupported hardware (ARM64), "
                         "for diagnostics only")
    ap.add_argument("--uninstall", action="store_true", help="remove GreenCraft")
    ap.add_argument("--components", default="",
                    help="with --uninstall --quiet: comma-separated list of "
                         "greencraft,prism,tailscale,all")
    ap.add_argument("--keep-worlds", action="store_true",
                    help="with --uninstall: move saved worlds to Documents first")
    ap.add_argument("--quiet", action="store_true", help="with --uninstall: no window")
    ap.add_argument("--channel", default="stable", choices=["stable", "experimental"])
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST, help="URL or local path")
    ap.add_argument("--instance", help="override the Prism instance id")
    ap.add_argument("--instances-dir", help="override the Prism instances directory")
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    ap.add_argument("--no-launch", action="store_true", help="sync only")
    ap.add_argument("--no-gui", action="store_true",
                    help="plain console output, no progress window")
    ap.add_argument("--prism", help="path to prismlauncher.exe (implies launch)")
    args = ap.parse_args()

    import install as inst

    if args.uninstall:
        if args.quiet or args.components:
            names = [c.strip().lower() for c in args.components.split(",") if c.strip()]
            everything = "all" in names
            opts = {
                "greencraft": everything or not names or "greencraft" in names,
                "prism": everything or "prism" in names,
                "tailscale": everything or "tailscale" in names,
                "keep_worlds": args.keep_worlds,
            }
            log(f"Uninstalling: {', '.join(k for k, v in opts.items() if v and k != 'keep_worlds')}")
            inst.uninstall_selected(opts, log)
            return 0
        return run_uninstall_gui(False)

    if args.install:
        # Headless equivalent of the wizard. Exists so a scripted session can exercise
        # the whole first-run path without anyone clicking buttons.
        opts = {
            "desktop": not args.no_desktop,
            "start_menu": args.start_menu,
            "experimental": args.experimental,
            "invite": args.invite,
            "allow_unsupported": args.allow_unsupported,
        }
        try:
            do_install(opts, log, args.manifest)
        except Exception as e:
            log(f"\nINSTALL FAILED: {type(e).__name__}: {e}")
            return 1
        return 0

    # No arguments means someone ran the exe directly rather than via a shortcut, so
    # they want to set something up or take it away -- not to start the game. Playing
    # is what the shortcuts are for, and they always pass --channel.
    bare = len(sys.argv) == 1
    if args.setup or bare:
        return run_setup_gui()

    # Routine path from a shortcut: show a small progress window rather than nothing,
    # since a windowed build has no console. --no-gui keeps the plain behaviour for
    # scripting and for debugging this program itself.
    if not args.no_gui and not args.dry_run:
        import gui as _gui

        def _work(window_log):
            # Tee to the log file as well. With no console, that file is the only
            # record once the window has closed itself.
            def tee(msg=""):
                window_log(msg)
                log(msg)
            return _sync_and_prepare(args, tee)

        _res, failed = _gui.ProgressWindow(_work).run()
        if failed or not _res:
            return 1
        prism, inst_id = _res
        if args.no_launch or not prism:
            return 0
        launch_detached(prism, inst_id)
        return 0

    log(f"Manifest: {args.manifest}")
    m = load_manifest(args.manifest)
    if args.channel not in m.get("channels", {}):
        log(f"ERROR: no such channel '{args.channel}'")
        return 1
    ch = m["channels"][args.channel]

    inst_id = args.instance or ch["prismInstanceId"]
    root = Path(args.instances_dir) if args.instances_dir else PRISM_INSTANCES
    inst_root = root / inst_id
    mcdir = inst_root / "minecraft"

    log(f"Channel : {args.channel}  v{ch['versionId']}")
    log(f"Instance: {inst_root}")
    log(f"Server  : {ch['server']['name']} @ {ch['server']['address']}:{ch['server']['port']}")
    if args.dry_run:
        log("MODE    : dry run, nothing will be written")
    log()

    if not args.dry_run:
        created = ensure_instance(
            inst_root, ch["prismInstanceId"], ch["pack"]["dependencies"], ch.get("javaArgs")
        )
        log(f"Instance skeleton: {'created' if created else 'already present'}")

    log("Syncing content...")
    added, updated, kept, skipped, removed = sync(ch, mcdir, args.dry_run)
    for p in added:
        log(f"  + {p}")
    for p in updated:
        log(f"  ~ {p}")
    for p in removed:
        log(f"  - {p}")
    for p in skipped:
        log(f"  ! {p}  (edited locally -- left alone)")
    log(f"  {len(kept)} already correct, {len(added)} added, {len(updated)} updated, "
        f"{len(removed)} removed, {len(skipped)} skipped")

    if not args.dry_run:
        lock = load_lock(mcdir)
        w, p = apply_overrides(ch, mcdir, lock)
        if w or p:
            log(f"Overrides: {w} written, {p} already present (left as-is)")
        else:
            log("Overrides: already at this version")

        sd = mcdir / "servers.dat"
        addr = ch["server"]["address"]
        if ch["server"].get("port") and int(ch["server"]["port"]) != 25565:
            addr = f"{addr}:{ch['server']['port']}"
        what = nbt.upsert_server(sd, ch["server"]["name"], addr)
        log(f"Server list: {what} '{ch['server']['name']}' -> {addr}")

    if args.dry_run or args.no_launch:
        log("\nDone (not launching).")
        return 0

    prism = args.prism or find_prism()
    if not prism:
        log("\nSynced, but prismlauncher.exe was not found.")
        log("Looked in the GreenCraft folder, %LOCALAPPDATA%\\Programs\\PrismLauncher,")
        log("Program Files, and PATH. Pass --prism <path> to launch automatically.")
        return 0

    # Deliberately NOT passing --server. Minecraft takes minutes to reach the title
    # screen, and someone who walks away during that would come back already spawned
    # in the world and possibly under attack. The server is in the multiplayer list;
    # joining stays a decision the player makes when they are actually at the keyboard.
    cmd = [prism, "--launch", inst_id]
    log(f"\nLaunching: {' '.join(cmd)}")
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
