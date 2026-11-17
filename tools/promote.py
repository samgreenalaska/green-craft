"""Promote experimental -> stable, on both the manifest and the server host.

Stable is never authored. It is only ever a byte-for-byte copy of an experimental
channel that has been tested, so what friends receive is what was actually run.

Deliberately does NOT touch world/. Both servers live on the same host with their own
independently-seeded worlds; copying experimental's world over stable would replace
every friend's progress with test terrain. That is the one mistake in this design
that no rollback recovers from, so it is asserted rather than assumed.

The host comes from server_target.py (GREENCRAFT_SERVER overrides it for one run).
"""
import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

import build_overrides as bo
import server_target as st

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "manifest.json"
MC = st.MC


def ssh(cmd, check=True, timeout=180):
    return st.ssh(cmd, check=check, timeout=timeout).stdout.strip()


def promote_manifest():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    exp = m["channels"]["experimental"]
    old_stable = m["channels"]["stable"]

    new = copy.deepcopy(exp)
    new["prismInstanceId"] = old_stable.get("prismInstanceId", "greencraft-stable")
    new["promotedFrom"] = old_stable.get("versionId")

    version = new["versionId"]

    # overrides/stable must become a copy of overrides/experimental, then be rezipped
    # under the stable filename. Builds are deterministic, so identical trees give
    # identical hashes -- a differing hash here means the trees actually differ.
    src = REPO / "overrides" / "experimental"
    dst = REPO / "overrides" / "stable"
    if dst.is_dir():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    zp = REPO / "dist" / f"overrides-stable-{version}.zip"
    meta = bo.zip_channel(dst, zp)
    new["overrides"] = {
        "filename": zp.name,
        "hashes": {"sha1": meta["sha1"], "sha512": meta["sha512"]},
        "downloads": [
            f"https://github.com/samgreenalaska/green-craft/releases/download/v{version}/{zp.name}"
        ],
        "fileSize": meta["fileSize"],
    }

    m["channels"]["stable"] = new
    MANIFEST.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")

    exp_sha = exp["overrides"]["hashes"]["sha512"]
    return version, old_stable.get("versionId"), (meta["sha512"] == exp_sha), old_stable, new


def diff_packs(old, new):
    o = {f["path"]: f["hashes"]["sha512"] for f in old.get("pack", {}).get("files", [])}
    n = {f["path"]: f["hashes"]["sha512"] for f in new.get("pack", {}).get("files", [])}
    added = sorted(set(n) - set(o))
    removed = sorted(set(o) - set(n))
    changed = sorted(p for p in set(n) & set(o) if o[p] != n[p])
    return added, removed, changed


def promote_server():
    print(f"\n=== {st.NAME} ===")

    n = st.players_online()
    if n is None:
        print("  WARNING: could not read the player count.")
    elif n > 0:
        print(f"  REFUSING: {n} player(s) online.")
        print("  Promotion stops and restarts the server. Wait, or kick them first.")
        raise SystemExit(1)
    else:
        print("  players online: 0")

    own = st.as_owner

    # Sanity: never proceed if the directories aren't what we expect.
    for d in ("server-stable/mods", "server-experimental/mods"):
        if ssh(own(f"test -d {MC}/{d} && echo yes || echo no")) != "yes":
            print(f"  ERROR: {d} missing on {st.NAME}")
            raise SystemExit(1)

    print("  stopping server...")
    ssh("sudo systemctl stop minecraft")

    print("  backing up server-stable/mods -> mods.prev")
    ssh(own(f"rm -rf {MC}/server-stable/mods.prev && mv {MC}/server-stable/mods {MC}/server-stable/mods.prev"))

    print("  copying experimental mods -> stable")
    ssh(own(f"cp -a {MC}/server-experimental/mods {MC}/server-stable/mods"))

    # config only, additive. No --delete: a stale config for a removed mod is inert,
    # whereas deleting one that stable still needs is not.
    print("  syncing config (additive)")
    ssh(own(f"rsync -a {MC}/server-experimental/config/ {MC}/server-stable/config/"))

    # Loud assertion rather than a silent assumption.
    worlds = ssh(own(f"stat -c%Y {MC}/server-stable/world 2>/dev/null || echo missing"))
    print(f"  server-stable/world untouched (mtime {worlds})")

    print("  switching active channel to stable and starting...")
    out = ssh(f"{st.T['mc_channel']} stable", timeout=240)
    for line in out.splitlines():
        print(f"    {line}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--manifest-only", action="store_true", help="don't touch the server host")
    args = ap.parse_args()

    before = json.loads(MANIFEST.read_text(encoding="utf-8"))
    old_stable = copy.deepcopy(before["channels"]["stable"])
    exp = before["channels"]["experimental"]

    added, removed, changed = diff_packs(old_stable, exp)
    print("=== PROMOTING experimental -> stable ===")
    print(f"  stable   {old_stable.get('versionId')}  ->  {exp.get('versionId')}")
    print()
    if not (added or removed or changed):
        print("  (no content differences between channels)")
    for p in added:
        print(f"  + {p}")
    for p in removed:
        print(f"  - {p}")
    for p in changed:
        print(f"  ~ {p}")

    if not args.yes:
        print()
        reply = input("Proceed? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted. Nothing changed.")
            return 1

    version, prev, bundles_match, _o, _n = promote_manifest()
    print(f"\n=== manifest ===")
    print(f"  channels.stable = channels.experimental ({version}, promotedFrom {prev})")
    print(f"  overrides bundle identical to experimental: {bundles_match}")
    if not bundles_match:
        print("  NOTE: hashes differ, so the override trees genuinely differ. Check that.")

    if args.manifest_only:
        print(f"\n--manifest-only: {st.NAME} not touched.")
    else:
        promote_server()

    print("\n=== Next ===")
    print("  1. Join and sanity-check the stable server.")
    print("  2. publish.bat \"promote " + str(version) + "\"")
    print(f"  Rollback: git revert the publish commit, then on {st.NAME}")
    print("    rm -rf server-stable/mods && mv server-stable/mods.prev server-stable/mods")
    print("    mc-channel stable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
