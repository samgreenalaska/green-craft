"""Pre-publish checks. Exits non-zero (and says why) if the repo is not safe to publish.

Run by publish.bat before it commits anything. The point is to make it impossible to
push a manifest that advertises hashes the release assets do not actually have --
friends' updaters verify sha512 before installing, so a mismatch is a hard failure
for every one of them at once.
"""
import hashlib, json, os, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO, "manifest.json")

errors = []
warnings = []

# 1. manifest parses
try:
    with open(MANIFEST, encoding="utf-8") as f:
        m = json.load(f)
except Exception as e:
    print(f"FAIL  manifest.json does not parse: {e}")
    sys.exit(1)
print("ok    manifest.json parses")

version = None
for ch, c in m.get("channels", {}).items():
    # 2. every channel agrees on the version
    if version is None:
        version = c.get("versionId")
    elif c.get("versionId") != version:
        warnings.append(
            f"channels disagree on versionId: {version} vs {c.get('versionId')} ({ch}) "
            "-- expected while experimental is ahead of stable"
        )

    # 3. the overrides asset exists in dist/ and matches the advertised hash
    ov = c.get("overrides")
    if not ov:
        errors.append(f"{ch}: no overrides block")
        continue
    path = os.path.join(REPO, "dist", ov["filename"])
    if not os.path.exists(path):
        errors.append(f"{ch}: dist/{ov['filename']} missing -- run tools/build_overrides.py")
        continue
    data = open(path, "rb").read()
    if len(data) != ov["fileSize"]:
        errors.append(f"{ch}: {ov['filename']} size {len(data)} != manifest {ov['fileSize']}")
    actual = hashlib.sha512(data).hexdigest()
    if actual != ov["hashes"]["sha512"]:
        errors.append(
            f"{ch}: {ov['filename']} sha512 mismatch\n"
            f"        on disk:   {actual}\n"
            f"        manifest:  {ov['hashes']['sha512']}"
        )
    else:
        print(f"ok    {ch}: dist/{ov['filename']} matches manifest sha512")

    # 4. the release tag implied by the download URL matches the versionId
    url = ov["downloads"][0] if ov.get("downloads") else ""
    expect = f"/releases/download/v{c.get('versionId')}/"
    if expect not in url:
        errors.append(f"{ch}: overrides URL does not contain {expect}\n        {url}")

    # 5. launcher block sanity -- either fully null, or fully populated
    lc = c.get("launcher") or {}
    if lc.get("version") is None:
        if lc.get("payload") or lc.get("setup"):
            errors.append(f"{ch}: launcher.version is null but payload/setup present")
        else:
            print(f"ok    {ch}: launcher not yet released (version null)")
    else:
        for part in ("payload", "setup"):
            spec = lc.get(part) or {}
            if not spec.get("downloads") or not (spec.get("hashes") or {}).get("sha512"):
                errors.append(f"{ch}: launcher.{part} incomplete")
                continue
            # The advertised artifact must match what is about to be uploaded, or every
            # client self-updates to something that fails its own hash check.
            f = os.path.join(REPO, "dist", spec["filename"])
            if not os.path.exists(f):
                errors.append(f"{ch}: launcher.{part} names dist/{spec['filename']}, which is missing")
                continue
            d = open(f, "rb").read()
            if hashlib.sha512(d).hexdigest() != spec["hashes"]["sha512"]:
                errors.append(f"{ch}: dist/{spec['filename']} does not match launcher.{part}.sha512")
            elif len(d) != spec.get("fileSize"):
                errors.append(f"{ch}: dist/{spec['filename']} size != launcher.{part}.fileSize")
            elif f"/v{c.get('versionId')}/" not in spec["downloads"][0]:
                errors.append(f"{ch}: launcher.{part} URL tag does not match versionId")
            else:
                print(f"ok    {ch}: launcher.{part} matches dist/{spec['filename']}")

# 5b. bootstrap.txt agrees with the stable launcher payload. GreenCraftSetup.exe reads
#     this file and nothing else, so if it drifts from the manifest every new friend
#     installs the wrong version -- or a version whose hash check fails.
BOOTSTRAP = os.path.join(REPO, "bootstrap.txt")
stable = m.get("channels", {}).get("stable", {})
spec = (stable.get("launcher") or {}).get("payload") or {}
if not os.path.exists(BOOTSTRAP):
    errors.append("bootstrap.txt is missing -- run tools/set_version.py")
elif spec:
    cfg = {}
    for line in open(BOOTSTRAP, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip()
    if cfg.get("sha512") != spec["hashes"]["sha512"]:
        errors.append("bootstrap.txt sha512 does not match launcher.payload")
    elif cfg.get("url") != spec["downloads"][0]:
        errors.append("bootstrap.txt url does not match launcher.payload")
    elif cfg.get("version") != stable.get("versionId"):
        errors.append("bootstrap.txt version does not match stable versionId")
    else:
        print("ok    bootstrap.txt matches launcher.payload")

# 6. every pack file has a hash and a download
for ch, c in m.get("channels", {}).items():
    for f in c.get("pack", {}).get("files", []):
        if not f.get("downloads"):
            errors.append(f"{ch}: {f['path']} has no downloads")
        if not f.get("hashes", {}).get("sha512"):
            errors.append(f"{ch}: {f['path']} has no sha512")

print()
for w in warnings:
    print(f"warn  {w}")
if errors:
    print()
    for e in errors:
        print(f"FAIL  {e}")
    print(f"\n{len(errors)} problem(s). Nothing was committed.")
    sys.exit(1)

print(f"All checks passed. Release version: v{version}")
sys.exit(0)
