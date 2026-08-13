"""Collect seeded files from a Prism instance into overrides/<channel>/, then zip to dist/.

Importable: update_manifest.py reuses collect() and zip_channel().
Run directly (as publish.bat does) to rebuild both channels from the default instance.
"""
import hashlib
import os
import shutil
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTANCES = Path(os.environ.get("APPDATA", "")) / "PrismLauncher" / "instances"
DEFAULT_INSTANCE = "tectonic"

# Files seeded on first install only. Deliberately narrow: mod configuration and
# client settings, nothing personal or machine-specific.
#
# Excluded on purpose:
#   config/sodium-fingerprint.json  -- a hardware/driver fingerprint for THIS GPU. Shipping
#                                      it would let Sodium skip driver-compat checks on a
#                                      friend's different card. Sodium regenerates it.
#   config/xaeropatreon.txt         -- licence/patron key file.
#   xaero/, journeymap/, saves/     -- waypoints, explored map data, worlds. Personal, large,
#                                      and not wanted on someone else's install.
#   servers.dat                     -- the updater writes this from the manifest.
EXCLUDE_NAMES = {"sodium-fingerprint.json", "xaeropatreon.txt"}

SEEDED_DIRS = ["config"]
SEEDED_FILES = ["options.txt", "shaderpacks/photon_v1.3b.zip.txt"]


def instance_dir(name):
    d = INSTANCES / name / "minecraft"
    if not d.is_dir():
        avail = sorted(p.name for p in INSTANCES.iterdir() if p.is_dir()) if INSTANCES.is_dir() else []
        raise SystemExit(
            f"Prism instance '{name}' not found at {d}\n"
            f"Available instances: {', '.join(avail) or '(none)'}"
        )
    return d


def collect(inst, dest):
    """Copy the seeded subset of `inst` into `dest`, replacing whatever was there."""
    dest = Path(dest)
    if dest.is_dir():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    for sub in SEEDED_DIRS:
        root = Path(inst) / sub
        if not root.is_dir():
            continue
        for src in root.rglob("*"):
            if src.is_dir() or src.name in EXCLUDE_NAMES:
                continue
            out = dest / src.relative_to(inst)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)

    for rel in SEEDED_FILES:
        src = Path(inst) / rel
        if src.exists():
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)

    return sum(1 for _ in Path(dest).rglob("*") if _.is_file())


def zip_channel(srcdir, zippath):
    """Zip srcdir deterministically and return size + hashes."""
    zippath = Path(zippath)
    zippath.parent.mkdir(parents=True, exist_ok=True)
    if zippath.exists():
        zippath.unlink()
    files = sorted(p for p in Path(srcdir).rglob("*") if p.is_file())
    with zipfile.ZipFile(zippath, "w", zipfile.ZIP_DEFLATED) as z:
        for full in files:
            # Fixed timestamp so an unchanged tree always yields an identical zip.
            info = zipfile.ZipInfo(
                str(full.relative_to(srcdir)).replace("\\", "/"),
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, full.read_bytes())
    data = zippath.read_bytes()
    return {
        "fileSize": len(data),
        "sha1": hashlib.sha1(data).hexdigest(),
        "sha512": hashlib.sha512(data).hexdigest(),
    }


def build(channel, instance_name, version):
    inst = instance_dir(instance_name)
    tree = REPO / "overrides" / channel
    n = collect(inst, tree)
    zp = REPO / "dist" / f"overrides-{channel}-{version}.zip"
    meta = zip_channel(tree, zp)
    return n, zp, meta


def main():
    """Re-zip both channels' override trees from what is already in overrides/.

    Does NOT re-collect from a Prism instance. overrides/ is the source of truth: the
    experimental tree is authored by update_manifest.py from the experimental instance,
    and promote.py makes the stable tree a copy of it. Scanning DEFAULT_INSTANCE here
    would rmtree both and refill them from a third instance, which publish.bat would
    then ship.
    """
    import json
    manifest = json.loads((REPO / "manifest.json").read_text(encoding="utf-8"))
    for ch in ("stable", "experimental"):
        version = manifest["channels"][ch]["versionId"]
        tree = REPO / "overrides" / ch
        zp = REPO / "dist" / f"overrides-{ch}-{version}.zip"
        meta = zip_channel(tree, zp)
        n = sum(1 for p in tree.rglob("*") if p.is_file())
        print(f"{ch}: {n} files -> {zp.name} ({meta['fileSize']} bytes)")
        print(f"  sha512: {meta['sha512']}")


if __name__ == "__main__":
    sys.exit(main())
