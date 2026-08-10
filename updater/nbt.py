"""Just enough NBT to write servers.dat.

servers.dat is UNCOMPRESSED big-endian NBT. This is the one file people get wrong --
level.dat and most other .dat files are gzipped, servers.dat is not. Writing it gzipped
makes the client log

    Couldn't load server list
    java.io.IOException: Invalid tag id: 31

where 31 is 0x1f, the first byte of the gzip magic. The client then discards the whole
list, taking any servers the user had added with it. Reading still accepts gzip, since
that costs nothing and some tools do compress it.

Structure:

    TAG_Compound ""
      TAG_List "servers" of TAG_Compound
        TAG_String "name"
        TAG_String "ip"
        TAG_Byte   "hidden"          (optional)
        TAG_String "icon"            (optional)

Reading matters as much as writing: the file belongs to the user, who may have added
their own servers. Replacing the whole file would delete them, so we parse what is
there, update our own entry in place, and write it back.
"""
import gzip
import os
import struct

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


class Reader:
    def __init__(self, data):
        self.d = data
        self.i = 0

    def take(self, n):
        b = self.d[self.i:self.i + n]
        if len(b) != n:
            raise ValueError("truncated NBT")
        self.i += n
        return b

    def u1(self):
        return self.take(1)[0]

    def u2(self):
        return struct.unpack(">H", self.take(2))[0]

    def i4(self):
        return struct.unpack(">i", self.take(4))[0]

    def string(self):
        return self.take(self.u2()).decode("utf-8", "replace")

    def payload(self, tag):
        if tag == TAG_BYTE:
            return self.u1()
        if tag == TAG_SHORT:
            return struct.unpack(">h", self.take(2))[0]
        if tag == TAG_INT:
            return self.i4()
        if tag == TAG_LONG:
            return struct.unpack(">q", self.take(8))[0]
        if tag == TAG_FLOAT:
            return struct.unpack(">f", self.take(4))[0]
        if tag == TAG_DOUBLE:
            return struct.unpack(">d", self.take(8))[0]
        if tag == TAG_BYTE_ARRAY:
            return self.take(self.i4())
        if tag == TAG_STRING:
            return self.string()
        if tag == TAG_LIST:
            item = self.u1()
            n = self.i4()
            return {"__list__": item, "items": [self.payload(item) for _ in range(max(0, n))]}
        if tag == TAG_COMPOUND:
            out = {}
            while True:
                t = self.u1()
                if t == TAG_END:
                    return out
                name = self.string()
                out[name] = {"__tag__": t, "value": self.payload(t)}
        if tag == TAG_INT_ARRAY:
            return [self.i4() for _ in range(self.i4())]
        if tag == TAG_LONG_ARRAY:
            n = self.i4()
            return [struct.unpack(">q", self.take(8))[0] for _ in range(n)]
        raise ValueError(f"unsupported NBT tag {tag}")


def _w_string(s):
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _w_payload(tag, v):
    if tag == TAG_BYTE:
        return struct.pack(">B", v & 0xFF)
    if tag == TAG_SHORT:
        return struct.pack(">h", v)
    if tag == TAG_INT:
        return struct.pack(">i", v)
    if tag == TAG_LONG:
        return struct.pack(">q", v)
    if tag == TAG_FLOAT:
        return struct.pack(">f", v)
    if tag == TAG_DOUBLE:
        return struct.pack(">d", v)
    if tag == TAG_BYTE_ARRAY:
        return struct.pack(">i", len(v)) + bytes(v)
    if tag == TAG_STRING:
        return _w_string(v)
    if tag == TAG_LIST:
        item = v["__list__"]
        items = v["items"]
        out = struct.pack(">Bi", item, len(items))
        for it in items:
            out += _w_payload(item, it)
        return out
    if tag == TAG_COMPOUND:
        out = b""
        for name, field in v.items():
            out += struct.pack(">B", field["__tag__"]) + _w_string(name)
            out += _w_payload(field["__tag__"], field["value"])
        return out + b"\x00"
    if tag == TAG_INT_ARRAY:
        return struct.pack(">i", len(v)) + b"".join(struct.pack(">i", x) for x in v)
    if tag == TAG_LONG_ARRAY:
        return struct.pack(">i", len(v)) + b"".join(struct.pack(">q", x) for x in v)
    raise ValueError(f"unsupported NBT tag {tag}")


def read_servers_dat(path):
    """Return the list of server compounds, or [] if the file is absent/unreadable."""
    try:
        raw = open(path, "rb").read()
    except FileNotFoundError:
        return []
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    r = Reader(raw)
    tag = r.u1()
    if tag != TAG_COMPOUND:
        raise ValueError("servers.dat is not an NBT compound")
    r.string()  # root name, conventionally empty
    root = r.payload(TAG_COMPOUND)
    servers = root.get("servers")
    if not servers:
        return []
    return servers["value"]["items"]


def write_servers_dat(path, servers):
    """servers is a list of compound dicts as produced by read_servers_dat."""
    root = {
        "servers": {
            "__tag__": TAG_LIST,
            "value": {"__list__": TAG_COMPOUND, "items": servers},
        }
    }
    body = struct.pack(">B", TAG_COMPOUND) + _w_string("") + _w_payload(TAG_COMPOUND, root)
    # Uncompressed -- see the module docstring. Written via a temp file and renamed so
    # a crash mid-write cannot leave the user with a truncated server list.
    tmp = str(path) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, path)


def server_entry(name, ip):
    # hidden=0 is load-bearing. When the client joins via --server (quick play) it
    # records the server with hidden=1 so it does not clutter the list, and an entry
    # left that way parses perfectly and is simply never drawn -- the multiplayer
    # screen looks empty with a valid entry sitting in the file.
    return {
        "name": {"__tag__": TAG_STRING, "value": name},
        "ip": {"__tag__": TAG_STRING, "value": ip},
        "hidden": {"__tag__": TAG_BYTE, "value": 0},
    }


def normalise_address(ip):
    """Compare addresses the way Minecraft treats them: :25565 is the default.

    The client writes the port it connected on, so a quick-join lands as
    "host:25565" while we would write plain "host". Without normalising, the next
    sync adds a second entry for the same server.
    """
    ip = (ip or "").strip()
    if ip.lower().endswith(":25565"):
        ip = ip[: -len(":25565")]
    return ip.lower()


def upsert_server(path, name, ip):
    """Add or update our entry without disturbing servers the user added themselves.

    Matches on address rather than name: the address identifies the server, and the
    user is free to rename it in their own list.
    """
    servers = read_servers_dat(path)
    target = normalise_address(ip)

    mine = [s for s in servers if normalise_address(s.get("ip", {}).get("value")) == target]
    others = [s for s in servers if normalise_address(s.get("ip", {}).get("value")) != target]

    if mine:
        # The client adds its own quick-play entry each time it connects, so several
        # can accumulate for one address. Collapse them into one.
        entry = mine[0]
        was = (
            entry.get("name", {}).get("value"),
            entry.get("ip", {}).get("value"),
            entry.get("hidden", {}).get("value"),
        )
        entry["name"] = {"__tag__": TAG_STRING, "value": name}
        entry["ip"] = {"__tag__": TAG_STRING, "value": ip}
        entry["hidden"] = {"__tag__": TAG_BYTE, "value": 0}
        if len(mine) > 1:
            result = f"updated (removed {len(mine) - 1} duplicate)"
        elif was == (name, ip, 0) and len(servers) == len(others) + 1:
            # Report honestly on a no-op run. Saying "updated" when nothing changed
            # makes an idempotent sync look like it wrote something.
            result = "unchanged"
        else:
            result = "updated"
    else:
        entry = server_entry(name, ip)
        result = "added"

    write_servers_dat(path, [entry] + others)
    return result
