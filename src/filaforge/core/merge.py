"""From what every source said to one value per setting, with where it came from and how sure it is.

Rules, strongest first: the user's tested value; the maker's declared value (data sheet, then the maker's
database entries); a preset made for this filament; an estimate; the generic preset of the material.
Within a kind the more trusted source wins. Values a source gives as a range (nozzle, bed) are used to
check the others: a preset value inside the maker's range is kept, one outside it is replaced.
"""

from .model import BASELINE, DECLARED, ESTIMATED, KIND_RANK, PRESET, TESTED, Observation
from .text import round_to

TOLERANCE = {"nozzle_temp": 5, "bed_temp": 5, "density": 0.03, "fan_min": 10, "fan_max": 10, "vitrification": 5,
             "bridge_fan": 10, "fan_off_layers": 0, "full_fan_layer": 0, "layer_time": 2, "aux_fan": 10,
             "chamber_temp": 5, "retraction_length": 0.3, "retraction_speed": 10, "flow_ratio": 0.03,
             "pressure_advance": 0.01, "dry_temp": 5, "dry_time": 2, "print_speed_max": 30}
REL_TOLERANCE = {"max_volumetric_speed": 0.2}
# A line of 0.45 mm by 0.2 mm (OrcaSlicer's flow model) is about 0.0814 mm²: max speed x this = mm³/s.
LINE_AREA = 0.2 * (0.45 - 0.2 * (1 - 3.14159265 / 4))


def _ranked(obs):
    return sorted(obs, key=lambda o: (KIND_RANK.get(o.kind, 9), -o.weight))


def _by_trust(obs):
    return sorted(obs, key=lambda o: -o.weight)


def _close(key, a, b):
    if isinstance(a, tuple) or isinstance(b, tuple):
        if not (isinstance(a, tuple) and isinstance(b, tuple)):
            return False
        tol = TOLERANCE.get(key.replace("_range", ""), 5)
        return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return a == b
    if key in REL_TOLERANCE:
        return abs(a - b) <= REL_TOLERANCE[key] * max(abs(a), abs(b), 1e-9)
    return abs(a - b) <= TOLERANCE.get(key, 0)


class Pick:
    """The value chosen for one canonical key."""

    def __init__(self, key, value, kind, source, label="", url="", note="", basis=None):
        self.key, self.value, self.kind, self.source = key, value, kind, source
        self.label, self.url, self.note = label, url, note
        self.basis = basis or []          # the observations it was chosen from (shown as sources)
        self.agree, self.differ = [], []

    @classmethod
    def of(cls, o, note=""):
        return cls(o.key, o.value, o.kind, o.source, o.label, o.url, note or o.note, [o])

    def confidence(self):
        if self.kind == TESTED:
            return "high"
        if self.kind in (ESTIMATED, BASELINE):
            return "low"
        sources = {self.source} | {o.source for o in self.agree}
        return "high" if len(sources) >= 2 and not self.differ else "medium"

    def as_dict(self):
        value = list(self.value) if isinstance(self.value, tuple) else self.value
        return {"key": self.key, "value": value, "kind": self.kind, "source": self.source, "label": self.label,
                "url": self.url, "note": self.note, "confidence": self.confidence(),
                "agree": [o.as_dict() for o in self.agree], "differ": [o.as_dict() for o in self.differ]}


def _cross_check(pick, obs):
    for o in obs:
        if o.kind == BASELINE or o.source == pick.source:
            continue
        (pick.agree if _close(pick.key, pick.value, o.value) else pick.differ).append(o)


def _first(obs, key, kinds=None):
    for o in _ranked([o for o in obs if o.key == key]):
        if kinds is None or o.kind in kinds:
            return o
    return None


def _in(value, rng, slack=0):
    return rng is not None and rng[0] - slack <= value <= rng[1] + slack


def merge(observations, typical_speed=None):
    """{canonical key: Pick}. typical_speed (mm/s) chooses the band of a data sheet that gives a nozzle
    range per print speed."""
    obs = list(observations)
    by_key = {}
    for o in obs:
        by_key.setdefault(o.key, []).append(o)
    picks = {}

    # ranges first: they check the single values
    for key in ("nozzle_temp_range", "bed_temp_range"):
        o = _first(obs, key)
        if o:
            picks[key] = Pick.of(o)
    nozzle_range = picks["nozzle_temp_range"].value if "nozzle_temp_range" in picks else None
    if nozzle_range and picks["nozzle_temp_range"].kind == BASELINE:
        nozzle_range = None
    bed_range = picks["bed_temp_range"].value if "bed_temp_range" in picks else None

    # nozzle: tested > a declared single value in range > the preset's if in range > from the range
    tested = _first(obs, "nozzle_temp", (TESTED,))
    bands = _first(obs, "nozzle_temp_bands")
    band = None
    if bands and isinstance(bands.value, list) and bands.value:
        ordered = sorted(bands.value, key=lambda b: b[2])
        speed = typical_speed or ordered[-1][2]
        band = next((b for b in ordered if speed <= b[2]), ordered[-1])
    window = (band[0], band[1]) if band else nozzle_range
    if tested:
        picks["nozzle_temp"] = Pick.of(tested)
    else:
        chosen = None
        # a value made for this filament (declared or a preset), the most trusted source first
        for o in _by_trust(by_key.get("nozzle_temp", [])):
            if o.kind in (DECLARED, PRESET) and (window is None or _in(o.value, window)):
                chosen = Pick.of(o)
                break
        if chosen is None and window:
            value = min(max(round_to(window[0] + 0.4 * (window[1] - window[0])), window[0]), window[1])
            src = bands if band else picks.get("nozzle_temp_range")
            note = ("data sheet band %g-%g °C for print speeds up to %g mm/s" % band) if band else \
                "40%% into the maker's range %g-%g °C" % window
            basis = src.basis if isinstance(src, Pick) else [src]
            chosen = Pick("nozzle_temp", float(value), ESTIMATED, basis[0].source, basis[0].label, basis[0].url, note,
                          basis)
        if chosen is None:
            o = _first(obs, "nozzle_temp")
            chosen = Pick.of(o) if o else None
        if chosen:
            picks["nozzle_temp"] = chosen

    # bed: tested > declared single in range > preset in range > middle of the declared range > preset/generic
    tested = _first(obs, "bed_temp", (TESTED,))
    if tested:
        picks["bed_temp"] = Pick.of(tested)
    else:
        chosen = None
        for o in _by_trust(by_key.get("bed_temp", [])):
            if o.kind in (DECLARED, PRESET) and (bed_range is None or _in(o.value, bed_range)):
                chosen = Pick.of(o)
                break
        if chosen is None and bed_range and picks["bed_temp_range"].kind != BASELINE:
            value = round_to((bed_range[0] + bed_range[1]) / 2)
            value = min(max(value, bed_range[0]), bed_range[1])
            r = picks["bed_temp_range"]
            chosen = Pick("bed_temp", float(value), ESTIMATED, r.source, r.label, r.url,
                          "middle of the maker's range %g-%g °C" % bed_range, r.basis)
        if chosen is None:
            o = _first(obs, "bed_temp")
            chosen = Pick.of(o) if o else None
        if chosen:
            picks["bed_temp"] = chosen

    # max volumetric speed: tested/declared/preset, else estimated from the maker's max print speed
    o = _first(obs, "max_volumetric_speed", (TESTED, DECLARED, PRESET))
    speed = _first(obs, "print_speed_max", (DECLARED,))
    if o:
        picks["max_volumetric_speed"] = Pick.of(o)
    elif speed:
        est = round(speed.value * LINE_AREA, 1)
        generic = _first(obs, "max_volumetric_speed", (BASELINE,))
        if generic is None or est >= generic.value * 0.6:
            picks["max_volumetric_speed"] = Pick("max_volumetric_speed", est, ESTIMATED, speed.source, speed.label,
                                                 speed.url, "the maker's max speed %g mm/s x a 0.45 x 0.2 mm line"
                                                 % speed.value, [speed])
    if "max_volumetric_speed" not in picks:
        o = _first(obs, "max_volumetric_speed")
        if o:
            picks["max_volumetric_speed"] = Pick.of(o)

    # everything else: the strongest observation
    for key, items in by_key.items():
        if key in picks or key in ("nozzle_temp", "bed_temp", "nozzle_temp_bands"):
            continue
        picks[key] = Pick.of(_ranked(items)[0])
    if bands:
        picks["nozzle_temp_bands"] = Pick.of(bands)

    for key, pick in picks.items():
        others = [o for o in by_key.get(key, []) if not pick.basis or o is not pick.basis[0]]
        _cross_check(pick, others)
    return picks


# --- to OrcaSlicer settings ----------------------------------------------------------------------------------
# (OrcaSlicer key, canonical key, applied by default)
ORCA_MAP = (
    ("nozzle_temperature", "nozzle_temp", True),
    ("nozzle_temperature_initial_layer", "nozzle_temp", True),
    ("nozzle_temperature_range_low", "nozzle_temp_range", True),
    ("nozzle_temperature_range_high", "nozzle_temp_range", True),
    ("hot_plate_temp", "bed_temp", True),
    ("hot_plate_temp_initial_layer", "bed_temp", True),
    ("textured_plate_temp", "bed_temp", True),
    ("textured_plate_temp_initial_layer", "bed_temp", True),
    ("filament_density", "density", True),
    ("filament_max_volumetric_speed", "max_volumetric_speed", True),
    ("fan_min_speed", "fan_min", True),
    ("fan_max_speed", "fan_max", True),
    ("temperature_vitrification", "vitrification", True),
    ("chamber_temperature", "chamber_temp", True),
    ("activate_chamber_temp_control", "chamber_temp", True),
    ("overhang_fan_speed", "bridge_fan", True),
    ("close_fan_the_first_x_layers", "fan_off_layers", True),
    ("full_fan_speed_layer", "full_fan_layer", True),
    ("slow_down_layer_time", "layer_time", True),
    ("additional_cooling_fan_speed", "aux_fan", True),
    ("filament_flow_ratio", "flow_ratio", True),
    ("pressure_advance", "pressure_advance", True),
    ("enable_pressure_advance", "pressure_advance", True),
    ("filament_retraction_length", "retraction_length", False),
    ("filament_retraction_speed", "retraction_speed", False),
)
# keys only a tested value or a preset made for this filament may set: a generic or estimated one says nothing
TUNED_ONLY = {"flow_ratio", "pressure_advance", "retraction_length", "retraction_speed"}


def _fmt(value):
    value = float(value)
    return str(int(value)) if value.is_integer() else ("%.4f" % value).rstrip("0").rstrip(".")


def orca_settings(picks, base_config=None, chamber_control=False):
    """[{orca_key, value, from, kind, apply, note}] of the OrcaSlicer filament settings the picks give.
    base_config ({key: text}) is the preset they go on; a value equal to it is still listed (apply off).
    chamber_control: the printer heats its chamber, so a chamber temperature also switches its control on."""
    base_config = base_config or {}
    out = []
    nozzle = picks.get("nozzle_temp")
    nozzle_range = picks.get("nozzle_temp_range")
    for orca_key, key, default in ORCA_MAP:
        pick = picks.get(key)
        if pick is None:
            continue
        if key in TUNED_ONLY and pick.kind not in (TESTED, PRESET):
            continue
        if pick.kind == BASELINE:
            continue   # the base preset already holds the generic value
        note = pick.note
        value = pick.value
        if orca_key == "nozzle_temperature_initial_layer":
            hi = nozzle_range.value[1] if nozzle_range and nozzle_range.kind != BASELINE else None
            value = nozzle.value + 5 if pick.kind != TESTED else nozzle.value
            if hi is not None:
                value = min(value, hi)
            note = "first layer: nozzle +5 °C, within the maker's range" if pick.kind != TESTED else "as tested"
        elif orca_key.endswith("_range_low"):
            value = pick.value[0]
        elif orca_key.endswith("_range_high"):
            value = pick.value[1]
        elif orca_key == "enable_pressure_advance":
            value = 1
        elif orca_key == "activate_chamber_temp_control":
            if not chamber_control or not pick.value:
                continue
            value = 1
            note = "the printer heats its chamber"
        text = _fmt(value)
        base_text = str(base_config.get(orca_key, "")).split(",")[0].strip().strip('"')
        try:
            same = abs(float(base_text) - float(text)) < 1e-6
        except ValueError:
            same = base_text == text
        out.append({"orca_key": orca_key, "value": text, "from": key, "kind": pick.kind,
                    "apply": default and not same, "same_as_base": same, "note": note,
                    "confidence": pick.confidence()})
    notes = []
    for key, fmt in (("dry_temp", "Drying: %g °C"), ("dry_time", "%g h")):
        if key in picks:
            notes.append(fmt % picks[key].value)
    if notes:
        out.append({"orca_key": "filament_notes", "value": ", ".join(notes), "from": "dry_temp",
                    "kind": picks["dry_temp"].kind if "dry_temp" in picks else DECLARED, "apply": True,
                    "same_as_base": False, "note": "", "confidence": "medium"})
    return out


def measured_observations(values, label="Your measurement"):
    """Observations of the user's tested values ({canonical key: number})."""
    return [Observation(k, float(v), TESTED, "user", label, "", 1.0) for k, v in (values or {}).items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)]
