"""Single source of truth for the running build's version.

Stamped by tools/set_version.py so the exe, the manifest, the release tag and the
Apps & Features entry can never disagree. Self-update compares this against the
manifest's launcher.version, so if it is wrong the launcher either never updates or
updates in a loop.
"""

VERSION = "0.1.9"


def parse(v):
    """"1.2.3" -> (1, 2, 3), tolerant of junk so a bad manifest cannot crash startup."""
    out = []
    for part in str(v or "0").split(".")[:3]:
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def is_newer(candidate, current=VERSION):
    return parse(candidate) > parse(current)
