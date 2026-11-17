"""Where the GreenCraft server runs, for the tools that SSH to it.

promote.py and update_manifest.py both read this, so moving the server is one edit
here (DEFAULT), not a hunt through every tool. GREENCRAFT_SERVER=<name> overrides
the default for one run, which is how the old host stays reachable for a rollback.
"""
import os
import shlex
import subprocess

TARGETS = {
    # The old home: files owned by the SSH user, player count from its dashboard.
    "wheatley": {
        "ssh": "wheatley",
        "mc": "~/Desktop/Minecraft",
        "as_owner": "",
        "players_cmd": "curl -sf --max-time 5 http://100.92.154.56:8080/api/current"
                       " | python3 -c 'import json,sys;"
                       " p=(json.load(sys.stdin).get(\"mc\") or {}).get(\"players\");"
                       " print(p if isinstance(p,int) else \"?\")' || echo '?'",
        "mc_channel": "~/.local/bin/mc-channel",
    },
    # Cavern: SSH as ubuntu (full sudo), files owned by the minecraft system user.
    "cavern": {
        "ssh": "cavern",
        "mc": "/srv/minecraft",
        "as_owner": "sudo -u minecraft ",
        "players_cmd": "mc-players",
        "mc_channel": "mc-channel",
    },
}

# Flip to "cavern" at cutover (cavern-migration-plan.md, phase 3).
DEFAULT = "wheatley"

NAME = os.environ.get("GREENCRAFT_SERVER", DEFAULT)
if NAME not in TARGETS:
    raise SystemExit(f"GREENCRAFT_SERVER={NAME!r} is not one of {sorted(TARGETS)}")
T = TARGETS[NAME]
MC = T["mc"]


def ssh(cmd, check=True, timeout=180):
    """Run cmd on the server host. On failure with check=True, print and exit."""
    r = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", T["ssh"], cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    if check and r.returncode != 0:
        print(f"  ssh {NAME} failed: {cmd}")
        print(f"  {r.stderr.strip()[:400]}")
        raise SystemExit(1)
    return r


def as_owner(cmd):
    """Prefix a shell command so it runs as the user that owns the server files."""
    if not T["as_owner"]:
        return cmd
    return T["as_owner"] + "sh -c " + shlex.quote(cmd)


def players_online():
    """Player count as an int, or None when it cannot be read. None means unknown,
    never zero: callers must not stop a server they cannot see into."""
    out = ssh(T["players_cmd"], check=False, timeout=30).stdout.strip()
    return int(out) if out.isdigit() else None
