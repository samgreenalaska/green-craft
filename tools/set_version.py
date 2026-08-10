"""Stamp a new version across the manifest, overrides bundles and launcher block.

    python tools/set_version.py 0.1.1

Run after tools/build_exe.py, before publish.bat. Keeps four things that must agree
in lockstep: both channels' versionId, the overrides zip filenames and hashes, the
launcher hash, and the release tag embedded in every download URL.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
M = REPO / "manifest.json"
REPO_URL = "https://github.com/samgreenalaska/green-craft"

sys.path.insert(0, str(REPO / "tools"))
import build_overrides as bo


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        print("usage: set_version.py X.Y.Z")
        return 2
    version = sys.argv[1]

    # Stamp the source FIRST, then require a rebuild, so the exe's own VERSION and the
    # manifest's launcher.version can never disagree. If they do, self-update either
    # never fires or fires in a loop.
    vf = REPO / "updater" / "version.py"
    text = vf.read_text(encoding="utf-8")
    new = re.sub(r'^VERSION = ".*"$', f'VERSION = "{version}"', text, flags=re.M)
    if new != text:
        vf.write_text(new, encoding="utf-8")
        print(f"stamped updater/version.py -> {version}")
        print("\nversion.py changed - rebuild and re-run:")
        print("    python tools/build_exe.py")
        print(f"    python tools/set_version.py {version}")
        return 1

    exe = REPO / "dist" / "GreenCraft.exe"
    if not exe.exists():
        print("dist/GreenCraft.exe missing - run tools/build_exe.py first")
        return 1

    m = json.loads(M.read_text(encoding="utf-8"))
    old = m["channels"]["stable"]["versionId"]

    data = exe.read_bytes()
    launcher = {
        "version": version,
        "filename": "GreenCraft.exe",
        "hashes": {"sha1": hashlib.sha1(data).hexdigest(),
                   "sha512": hashlib.sha512(data).hexdigest()},
        "downloads": [f"{REPO_URL}/releases/download/v{version}/GreenCraft.exe"],
        "fileSize": len(data),
    }

    for ch in ("stable", "experimental"):
        c = m["channels"][ch]
        if ch == "stable":
            c["promotedFrom"] = old
        c["versionId"] = version
        n, zp, meta = bo.build(ch, bo.DEFAULT_INSTANCE, version)
        c["overrides"] = {
            "filename": zp.name,
            "hashes": {"sha1": meta["sha1"], "sha512": meta["sha512"]},
            "downloads": [f"{REPO_URL}/releases/download/v{version}/{zp.name}"],
            "fileSize": meta["fileSize"],
        }
        c["launcher"] = dict(launcher)
        print(f"{ch}: {n} override files -> {zp.name}")

    # Stale zips would otherwise be picked up by publish.bat's dist\*.zip glob and
    # uploaded alongside the current ones.
    for old_zip in (REPO / "dist").glob("overrides-*.zip"):
        if version not in old_zip.name:
            old_zip.unlink()
            print(f"removed stale {old_zip.name}")

    M.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
    print(f"\n{old} -> {version}")
    print(f"launcher: {len(data):,} bytes, sha512 {launcher['hashes']['sha512'][:32]}...")
    print("\nNext: python tools/verify_release.py, then publish.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
