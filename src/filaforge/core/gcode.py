"""Reads the G-code OrcaSlicer wrote: what each feature really extrudes, layer by layer.

The plan the settings describe is not what the printer does: OrcaSlicer caps speeds at the filament's max flow,
slows short layers down, and a wide line pushes more plastic than a narrow one at the same speed. This module
measures the result instead, from the file itself:

- every extruding move: its feature (;TYPE:), length, time at its feed rate, volume and so its flow (mm³/s);
- every layer: its height, its time at the feed rates (without acceleration: a lower bound), its retractions;
- the header and the config block OrcaSlicer appends: filament used, estimated time, the settings it sliced with.

Only the standard library and one pass over the file: given the filament diameter, a 100 MB file needs no more
memory than a small one (without it the moves wait for the config block at the end).
"""

import math
import re

# OrcaSlicer's feature names (;TYPE:) grouped into what the checks talk about
ROLES = {
    "outer wall": "outer_wall", "external perimeter": "outer_wall", "overhang wall": "overhang",
    "inner wall": "inner_wall", "perimeter": "inner_wall",
    "sparse infill": "sparse_infill", "internal infill": "sparse_infill",
    "internal solid infill": "solid_infill", "solid infill": "solid_infill",
    "top surface": "top_surface", "top solid infill": "top_surface",
    "bottom surface": "bottom_surface", "bridge": "bridge", "bridge infill": "bridge",
    "internal bridge": "internal_bridge", "gap infill": "gap_infill", "gap fill": "gap_infill",
    "support": "support", "support material": "support", "support interface": "support_interface",
    "support material interface": "support_interface", "support transition": "support",
    "skirt": "skirt", "brim": "brim", "skirt/brim": "brim", "ironing": "ironing",
    "prime tower": "prime_tower", "wipe tower": "prime_tower", "custom": "custom",
}
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)"
_WORD = re.compile(r"([A-Za-z])(" + _NUM + r")")
_TIME = re.compile(r"(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?")


def duration(text):
    """Seconds from OrcaSlicer's "1d 2h 3m 4s" (any part may be missing), or None."""
    match = _TIME.fullmatch(str(text or "").strip())
    if not match or not any(match.groups()):
        return None
    d, h, m, s = (int(x or 0) for x in match.groups())
    return float(((d * 24 + h) * 60 + m) * 60 + s)


class RoleStats:
    """Totals of one feature: time and volume, and the flows its moves ran at (weighted by time)."""

    __slots__ = ("time", "volume", "length", "max_flow", "hist")

    def __init__(self):
        self.time = 0.0
        self.volume = 0.0
        self.length = 0.0
        self.max_flow = 0.0
        self.hist = {}              # flow rounded to 0.5 mm³/s -> seconds

    def add(self, length, seconds, volume):
        self.time += seconds
        self.volume += volume
        self.length += length
        if seconds > 0:
            flow = volume / seconds
            self.max_flow = max(self.max_flow, flow)
            key = round(flow * 2) / 2
            self.hist[key] = self.hist.get(key, 0.0) + seconds

    def time_above(self, flow):
        """Seconds this feature ran above flow (mm³/s)."""
        return sum(t for f, t in self.hist.items() if f > flow)

    def percentile(self, share):
        """The flow this feature stays under for share (0..1) of its time."""
        if not self.time:
            return 0.0
        left = self.time * share
        for f in sorted(self.hist):
            left -= self.hist[f]
            if left <= 1e-9:
                return f
        return max(self.hist)

    def as_dict(self):
        return {"time": round(self.time, 1), "volume": round(self.volume, 1), "length": round(self.length, 1),
                "max_flow": round(self.max_flow, 2), "p95_flow": self.percentile(0.95),
                "p99_flow": self.percentile(0.99),
                "mean_flow": round(self.volume / self.time, 2) if self.time else 0.0}


class Layer:
    __slots__ = ("index", "z", "height", "time_feed", "retractions", "volume", "roles")

    def __init__(self, index, z=None, height=None):
        self.index = index
        self.z = z
        self.height = height
        self.time_feed = 0.0
        self.retractions = 0
        self.volume = 0.0
        self.roles = {}             # role -> seconds

    def as_dict(self):
        return {"index": self.index, "z": self.z, "height": self.height, "time_feed": round(self.time_feed, 2),
                "retractions": self.retractions,
                "volume": round(self.volume, 2)}


class Result:
    def __init__(self):
        self.header = {}            # "; key = value" lines outside the config block, and the header block
        self.config = {}            # the config block OrcaSlicer appends
        self.layers = []
        self.roles = {}
        self.travel_length = 0.0
        self.travel_time = 0.0
        self.retractions = 0
        self.moves = 0
        self.tools = set()          # T commands seen

    def role(self, name):
        return self.roles.get(name) or RoleStats()

    def values(self, key):
        """A setting from the config block as a list, one per extruder: preset names are written quoted and
        separated by ";" ("a";"b"), numbers by "," (0.4,0.4)."""
        value = self.config.get(key)
        if value is None:
            return []
        text = value.strip()
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', text) if text.startswith('"') else \
            [p for p in re.split(r"[;,]", text)]
        return [p.strip() for p in parts]

    def setting(self, key, index=0):
        """A setting from the config block; for a per-extruder list the value of extruder index."""
        values = self.values(key)
        if not values:
            return None
        return values[index] if index < len(values) else values[0]

    def used_extruders(self):
        """The extruders the print uses: from the grams used per extruder, else the T commands, else [0]."""
        grams = [x.strip() for x in str(self.header.get("filament used [g]", "")).split(",")]
        used = []
        for i, g in enumerate(grams):
            try:
                if float(g) > 0:
                    used.append(i)
            except ValueError:
                pass
        return used or sorted(self.tools) or [0]

    def number(self, key, index=0):
        try:
            return float(str(self.setting(key, index)).rstrip("%"))
        except (TypeError, ValueError):
            return None

    def total_time(self):
        for key in ("estimated printing time (normal mode)", "model printing time", "total estimated time"):
            seconds = duration(self.header.get(key))
            if seconds is not None:
                return seconds
        return sum(layer.time_feed for layer in self.layers) + 0.0

    def filament_used(self, index=None):
        """(mm, g) of filament from OrcaSlicer's summary lines: of extruder index, or of all of them; None where
        missing."""
        def amount(key):
            out = []
            for x in str(self.header.get(key, "")).split(","):
                try:
                    out.append(float(x))
                except ValueError:
                    pass
            if not out:
                return None
            if index is None:
                return round(sum(out), 2)
            return out[index] if index < len(out) else None
        return amount("filament used [mm]"), amount("filament used [g]")

    def summary(self):
        mm, g = self.filament_used()
        return {"layers": len(self.layers), "time": self.total_time(), "filament_mm": mm, "filament_g": g,
                "retractions": self.retractions, "travel": round(self.travel_length, 1),
                "roles": {k: v.as_dict() for k, v in sorted(self.roles.items())}}


def _arc_length(x0, y0, x1, y1, i, j, clockwise):
    cx, cy = x0 + i, y0 + j
    r = math.hypot(i, j)
    if r <= 0:
        return math.hypot(x1 - x0, y1 - y0)
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    sweep = (a0 - a1) if clockwise else (a1 - a0)
    if sweep <= 0:
        sweep += 2 * math.pi
    return r * sweep


def parse(lines, filament_diameter=None):
    """Result of a G-code file read line by line (an open file or any iterable of str)."""
    out = Result()
    config_lines = []
    in_config = False
    x = y = z = e = 0.0
    feed = 0.0                  # mm/min
    relative_e = False
    relative_xyz = False
    role = "custom"
    layer = Layer(0)
    out.layers.append(layer)
    started = False             # the first ;LAYER_CHANGE: moves before it (start G-code) are not a layer
    pending_z = None
    retracted = False
    moves = []                  # extruding moves kept until the filament diameter is known (from the config block)
    area = None
    if filament_diameter:
        area = math.pi * (filament_diameter / 2) ** 2

    def flush_moves():
        for (lay, rl, length, seconds, filament) in moves:
            _record(out, lay, rl, length, seconds, filament * area)
        moves.clear()

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line[0] == ";":
            body = line[1:].strip()
            if body.startswith("CONFIG_BLOCK_START"):
                in_config = True
                continue
            if body.startswith("CONFIG_BLOCK_END"):
                in_config = False
                continue
            if in_config:
                config_lines.append(body)
                continue
            upper = body.upper()
            if upper.startswith("TYPE:"):
                role = ROLES.get(body[5:].strip().lower(), body[5:].strip().lower().replace(" ", "_"))
            elif upper == "LAYER_CHANGE":
                if started:
                    layer = Layer(len(out.layers))
                    out.layers.append(layer)
                else:
                    started = True
                    layer.index = 0
                if pending_z is not None:
                    layer.z = pending_z
            elif upper.startswith("Z:"):
                try:
                    layer.z = pending_z = float(body[2:])
                except ValueError:
                    pass
            elif upper.startswith("HEIGHT:"):
                try:
                    if layer.height is None:
                        layer.height = float(body[7:])
                except ValueError:
                    pass
            elif " = " in body:
                key, _, value = body.partition(" = ")
                out.header.setdefault(key.strip(), value.strip())
            elif ": " in body:
                key, _, value = body.partition(": ")
                out.header.setdefault(key.strip().lower(), value.strip())
            continue
        code = line.split(";", 1)[0].strip()
        if not code:
            continue
        head = code.split(None, 1)[0].upper()
        if head in ("G0", "G1", "G2", "G3"):
            words = {k.upper(): float(v) for k, v in _WORD.findall(code[len(head):])}
            if "F" in words and words["F"] > 0:
                feed = words["F"]
            nx = (x + words["X"] if relative_xyz else words["X"]) if "X" in words else x
            ny = (y + words["Y"] if relative_xyz else words["Y"]) if "Y" in words else y
            nz = (z + words["Z"] if relative_xyz else words["Z"]) if "Z" in words else z
            de = 0.0
            if "E" in words:
                de = words["E"] if relative_e else words["E"] - e
                e = e + words["E"] if relative_e else words["E"]
            if head in ("G2", "G3") and ("I" in words or "J" in words):
                length = _arc_length(x, y, nx, ny, words.get("I", 0.0), words.get("J", 0.0), head == "G2")
                length = math.hypot(length, nz - z)
            else:
                length = math.sqrt((nx - x) ** 2 + (ny - y) ** 2 + (nz - z) ** 2)
            speed = feed / 60.0
            seconds = length / speed if speed > 0 and length > 0 else 0.0
            if length <= 1e-9 and de != 0:
                # extruder only: a retraction or an unretraction
                if de < 0:
                    if not retracted:
                        out.retractions += 1
                        if started:
                            layer.retractions += 1
                    retracted = True
                else:
                    retracted = False
                seconds = abs(de) / speed if speed > 0 else 0.0
                layer.time_feed += seconds if started else 0.0
            elif de > 0 and length > 0:
                retracted = False
                out.moves += 1
                if started:
                    layer.time_feed += seconds
                    if area is None:
                        moves.append((layer, role, length, seconds, de))
                    else:
                        _record(out, layer, role, length, seconds, de * area)
            elif length > 0:
                if de < 0 and not retracted:       # a wipe: retracting while moving
                    out.retractions += 1
                    if started:
                        layer.retractions += 1
                    retracted = True
                out.travel_length += length
                out.travel_time += seconds
                if started:
                    layer.time_feed += seconds
            x, y, z = nx, ny, nz
        elif head == "G92":
            words = {k.upper(): float(v) for k, v in _WORD.findall(code[3:])}
            if "E" in words:
                e = words["E"]
            x, y, z = words.get("X", x), words.get("Y", y), words.get("Z", z)
        elif head == "M82":
            relative_e = False
        elif head == "M83":
            relative_e = True
        elif len(head) > 1 and head[0] == "T" and head[1:].isdigit():
            out.tools.add(int(head[1:]))
        elif head == "G90":                     # as Marlin: absolute E too, until an M83 after it
            relative_xyz = False
            relative_e = False
        elif head == "G91":
            relative_xyz = True
    out.config = _config(config_lines)
    if area is None:
        diameter = out.number("filament_diameter") or 1.75
        area = math.pi * (diameter / 2) ** 2
        flush_moves()
    if not started:
        out.layers = []
    return out


def _record(out, layer, role, length, seconds, volume):
    stats = out.roles.get(role)
    if stats is None:
        stats = out.roles[role] = RoleStats()
    stats.add(length, seconds, volume)
    layer.volume += volume
    layer.roles[role] = layer.roles.get(role, 0.0) + seconds


def _config(lines):
    out = {}
    for body in lines:
        key, sep, value = body.partition(" = ")
        if sep:
            out[key.strip()] = value.strip()
    return out


def read(path, filament_diameter=None):
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse(f, filament_diameter)
