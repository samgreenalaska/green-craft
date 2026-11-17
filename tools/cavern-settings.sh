#!/usr/bin/env bash
# cavern-settings -- apply the settings that differ between Cavern and wheatley.
#
# Syncing the server directories from wheatley (the cutover, or a rollback that comes
# back) overwrites these with wheatley's values. Run this after any such sync, before
# starting the server. Idempotent: running it twice changes nothing the second time.
#
#   server.properties   listen on all interfaces, whitelist enforced, player names
#                       hidden from server-list scanners (the server is public here)
#   c2me.toml           OpenCL acceleration off. The mod stays installed: it is the only
#                       jar that ships LWJGL's core classes, which voxymap needs. Cavern
#                       has no OpenCL at all.
#   voxyserver.json     2 LOD worker threads, not 4 (Cavern has 2 cores)
#
# The whitelist itself is not written here: entries need UUIDs, and "whitelist add" in
# the console looks them up. See cavern-migration-plan.md.
set -euo pipefail

MC=/srv/minecraft
if [[ "$(id -un)" != minecraft ]]; then
  exec sudo -u minecraft "$0" "$@"
fi

changed=0

setprop() {  # setprop FILE KEY VALUE
  local f=$1 k=$2 v=$3
  if grep -q "^${k}=" "$f"; then
    if ! grep -qx "${k}=${v}" "$f"; then
      sed -i "s|^${k}=.*|${k}=${v}|" "$f"
      echo "  $k=$v"
      changed=1
    fi
  else
    echo "${k}=${v}" >>"$f"
    echo "  $k=$v (added)"
    changed=1
  fi
}

for ch in stable experimental; do
  d="$MC/server-$ch"
  [[ -d "$d" ]] || { echo "skip $ch: $d missing"; continue; }
  echo "== $ch"

  p="$d/server.properties"
  setprop "$p" server-ip ""
  setprop "$p" online-mode true
  setprop "$p" white-list true
  setprop "$p" enforce-whitelist true
  setprop "$p" hide-online-players true

  t="$d/config/c2me.toml"
  if [[ -f "$t" ]]; then
    if grep -q '^\[openclAccel\]' "$t"; then
      if ! awk '/^\[openclAccel\]/{s=1;next} /^\[/{s=0} s && /^[[:space:]]*enabled = false/{f=1} END{exit !f}' "$t"; then
        sed -i '/^\[openclAccel\]/,/^\[/ s/^\([[:space:]]*\)enabled = .*/\1enabled = false/' "$t"
        echo "  c2me openclAccel.enabled = false"
        changed=1
      fi
    else
      printf '\n[openclAccel]\n\tenabled = false\n\tallowCPUDevices = false\n\tallowIncompatibilityFallback = true\n' >>"$t"
      echo "  c2me openclAccel section added, enabled = false"
      changed=1
    fi
  fi

  j="$d/config/voxyserver.json"
  if [[ -f "$j" ]]; then
    if python3 - "$j" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
if d.get("workerThreads") == 2:
    sys.exit(1)
d["workerThreads"] = 2
with open(p, "w") as f:
    json.dump(d, f, indent=2)
PY
    then
      echo "  voxyserver workerThreads = 2"
      changed=1
    fi
  fi
done

if [[ $changed == 0 ]]; then
  echo "already applied, nothing changed"
fi
