"""Rebuild channels.experimental in manifest.json from a local Prism instance.

Scans mods/, shaderpacks/, resourcepacks/ and datapacks/, hashes everything, resolves
each file against Modrinth by sha512, falls back to tools/sources.json for anything not
published there, rebuilds the overrides bundle, and writes the result.

Only the experimental channel is touched. Stable is produced by promotion, never by this.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import build_overrides as bo

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "manifest.json"
SOURCES = REPO / "tools" / "sources.json"
UA = "green-craft-manifest-builder/0.2"

# Where content lives inside the instance, and where it lands in the pack.
CONTENT_DIRS = {
    "mods": (".jar",),
    "shaderpacks": (".zip",),
    "resourcepacks": (".zip",),
    "datapacks": (".zip",),
}


def jar_meta(path):
    try:
        with zipfile.ZipFile(path) as z:
            with z.open("fabric.mod.json") as f:
                d = json.loads(f.read().decode("utf-8", "replace"), strict=False)
        return d.get("id"), d.get("version"), d.get("environment", "*")
    except Exception:
        return None, None, None


def scan(inst):
    out = []
    for sub, exts in CONTENT_DIRS.items():
        d = Path(inst) / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            data = p.read_bytes()
            mod_id, version, env = (jar_meta(p) if p.suffix.lower() == ".jar" else (None, None, None))
            out.append({
                "path": f"{sub}/{p.name}",
                "file": p.name,
                "size": len(data),
                "sha1": hashlib.sha1(data).hexdigest(),
                "sha512": hashlib.sha512(data).hexdigest(),
                "id": mod_id,
                "version": version,
                "declared_env": env,
            })
    return out


def modrinth_bulk(entries):
    if not entries:
        return {}
    payload = json.dumps({
        "hashes": [e["sha512"] for e in entries],
        "algorithm": "sha512",
    }).encode()
    req = urllib.request.Request(
        "https://api.modrinth.com/v2/version_files",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except Exception as e:
        print(f"  WARNING: Modrinth lookup failed ({e}); falling back to sources.json only")
        return {}


def head_ok(url, expect_size=None):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status != 200:
                return False, f"HTTP {r.status}"
            clen = r.headers.get("Content-Length")
            if expect_size is not None and clen is not None and int(clen) != expect_size:
                return False, f"size {clen} != {expect_size}"
            return True, "ok"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


def server_mod_files(no_ssh):
    """Filenames in wheatley's server-experimental/mods, for env.server markers."""
    if no_ssh:
        return None
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "wheatley",
             "ls ~/Desktop/Minecraft/server-experimental/mods/"],
            capture_output=True, text=True, timeout=45,
        )
        if r.returncode != 0:
            print(f"  WARNING: could not reach wheatley ({r.stderr.strip()[:80]})")
            return None
        return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    except Exception as e:
        print(f"  WARNING: could not reach wheatley ({e})")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", help="Prism instance name (default: channel's prismInstanceId)")
    ap.add_argument("--version", help="new versionId for the experimental channel")
    ap.add_argument("--no-ssh", action="store_true", help="skip the wheatley server-side check")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ch = manifest["channels"]["experimental"]
    prev_files = {f["path"]: f for f in ch.get("pack", {}).get("files", [])}

    inst_name = args.instance or ch.get("prismInstanceId") or bo.DEFAULT_INSTANCE
    inst = bo.instance_dir(inst_name)
    version = args.version or ch["versionId"]
    print(f"Instance : {inst}")
    print(f"Version  : {version}\n")

    print("Scanning content...")
    entries = scan(inst)
    by_dir = {}
    for e in entries:
        by_dir[e["path"].split("/")[0]] = by_dir.get(e["path"].split("/")[0], 0) + 1
    for k, v in sorted(by_dir.items()):
        print(f"  {k}: {v}")
    if "datapacks" not in by_dir:
        print("  datapacks: none (server datapacks live on wheatley, not in the client pack)")

    print("\nResolving against Modrinth...")
    found = modrinth_bulk(entries)
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))

    print("\nChecking wheatley for server-side membership...")
    server_files = server_mod_files(args.no_ssh)
    if server_files is None:
        print("  carrying forward existing env markers where possible")

    files, unresolved = [], []
    for e in entries:
        v = found.get(e["sha512"])
        if v:
            f = next((x for x in v["files"] if x["hashes"]["sha512"] == e["sha512"]), v["files"][0])
            url = f["url"]
        else:
            key = e["id"] or e["file"]
            src = sources.get(key)
            if not src or not isinstance(src, dict) or "url" not in src:
                unresolved.append(e)
                continue
            url = src["url"].format(version=e["version"] or "", filename=e["file"])
            ok, why = head_ok(url, e["size"])
            if not ok:
                unresolved.append({**e, "why": f"sources.json URL unusable: {why}\n      {url}"})
                continue
            print(f"  sources.json -> {e['file']} ({why})")

        # env.server: prefer ground truth from wheatley; else keep what the manifest said.
        if e["path"].startswith("mods/") and server_files is not None:
            srv = "required" if e["file"] in server_files else "unsupported"
        elif e["path"] in prev_files:
            srv = prev_files[e["path"]]["env"]["server"]
        else:
            srv = "unsupported"

        files.append({
            "path": e["path"],
            "hashes": {"sha1": e["sha1"], "sha512": e["sha512"]},
            "env": {"client": "required", "server": srv},
            "downloads": [url],
            "fileSize": e["size"],
        })

    if unresolved:
        print("\nCANNOT RESOLVE -- no Modrinth match and no tools/sources.json entry:")
        for e in unresolved:
            print(f"  {e['path']}  (id={e['id']} v={e['version']})")
            if "why" in e:
                print(f"      {e['why']}")
        print("\nAdd an entry to tools/sources.json keyed by the mod id (or filename), e.g.")
        print('  "somemod": { "url": "https://.../releases/download/v{version}/{filename}" }')
        print("\nManifest NOT written.")
        return 1

    files.sort(key=lambda f: f["path"])

    print("\nRebuilding overrides bundle...")
    n, zp, meta = bo.build("experimental", inst_name, version)
    print(f"  {n} files -> {zp.name} ({meta['fileSize']} bytes)")

    # ---- diff against what was there before ----
    new_paths = {f["path"] for f in files}
    old_paths = set(prev_files)
    added = sorted(new_paths - old_paths)
    removed = sorted(old_paths - new_paths)
    changed = sorted(
        p for p in (new_paths & old_paths)
        if prev_files[p]["hashes"]["sha512"]
        != next(f for f in files if f["path"] == p)["hashes"]["sha512"]
    )

    print("\n=== CHANGES TO channels.experimental ===")
    if not (added or removed or changed):
        print("  (no content changes)")
    for p in added:
        print(f"  + {p}")
    for p in removed:
        print(f"  - {p}")
    for p in changed:
        print(f"  ~ {p}")

    client_only = [f["path"] for f in files if f["env"]["server"] == "unsupported"]
    total = sum(f["fileSize"] for f in files)
    print(f"\n  {len(files)} files, {total / 1024 / 1024:.1f} MB")
    print(f"  client-only: {len(client_only)}")

    ch["versionId"] = version
    ch["pack"]["files"] = files
    ch["overrides"] = {
        "filename": zp.name,
        "hashes": {"sha1": meta["sha1"], "sha512": meta["sha512"]},
        "downloads": [
            f"https://github.com/samgreenalaska/green-craft/releases/download/v{version}/{zp.name}"
        ],
        "fileSize": meta["fileSize"],
    }

    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {MANIFEST}")
    print("Stable channel untouched -- run promote when experimental has been tested.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
