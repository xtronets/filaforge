"""What a source tells about a filament, and the catalogue of the values FilaForge works with.

Every source turns what it found into Observations: one value for one canonical key (see KEYS), with
its kind (declared by the maker, a preset someone tuned, an estimate, or tested by the user), where it
came from and how much that source is trusted. merge.py picks the value for each key from them.
"""

from dataclasses import asdict, dataclass, field

# Kinds, strongest first. "tested" is the user's own measurement and always wins.
TESTED = "tested"
DECLARED = "declared"      # the maker says so: data sheet, the maker's own database entry
PRESET = "preset"          # a slicer preset made for this filament (the maker's or a vendor's)
ESTIMATED = "estimated"    # worked out from other values (e.g. max flow from the data sheet's max speed)
BASELINE = "baseline"      # a generic preset for the material: only when nothing better is known
KINDS = (TESTED, DECLARED, PRESET, ESTIMATED, BASELINE)
KIND_RANK = {kind: rank for rank, kind in enumerate(KINDS)}


@dataclass
class Observation:
    key: str                    # canonical key, one of KEYS
    value: object               # a number, or (low, high) for a *_range key, or text
    kind: str                   # one of KINDS
    source: str                 # source id: "ofd", "tds", "spoolmandb", "orca", "generic", "user", ...
    label: str = ""             # what the user sees: "Open Filament Database: SUNLU PLA+"
    url: str = ""
    weight: float = 0.5         # trust in the source, 0..1, within its kind
    note: str = ""

    def as_dict(self):
        out = asdict(self)
        if isinstance(self.value, tuple):
            out["value"] = list(self.value)
        return out


@dataclass
class Identity:
    """Which filament: brand, material (as OrcaSlicer names it: PLA, PETG, ...) and the product words
    that tell it from the brand's other ones of that material ("PLA+", "Silk", "High Speed")."""
    brand: str
    material: str
    product: str = ""
    colour: str = ""            # #RRGGBB, for the preset only

    def label(self):
        words = [self.brand]
        if self.product and self.product.lower() != self.material.lower():
            words.append(self.product)
        else:
            words.append(self.material)
        return " ".join(w for w in words if w).strip()


@dataclass
class SourceReport:
    source: str
    ok: bool
    found: int = 0
    message: str = ""
    url: str = ""
    details: dict = field(default_factory=dict)


# Canonical keys: {key: (unit, what it is)}. A *_range value is (low, high).
KEYS = {
    "nozzle_temp_range": ("°C", "nozzle temperature range"),
    "nozzle_temp": ("°C", "nozzle temperature"),
    "nozzle_temp_bands": ("", "nozzle temperature per print speed: [(low, high, max speed)]"),
    "bed_temp_range": ("°C", "bed temperature range"),
    "bed_temp": ("°C", "bed temperature"),
    "density": ("g/cm³", "density"),
    "max_volumetric_speed": ("mm³/s", "max volumetric speed"),
    "print_speed_max": ("mm/s", "max print speed the maker states"),
    "fan_min": ("%", "minimum part fan speed"),
    "fan_max": ("%", "maximum part fan speed"),
    "chamber_temp": ("°C", "chamber temperature"),
    "bridge_fan": ("%", "part fan on bridges and overhangs"),
    "fan_off_layers": ("", "first layers without the part fan"),
    "vitrification": ("°C", "softening temperature (Vicat / HDT)"),
    "retraction_length": ("mm", "retraction length"),
    "retraction_speed": ("mm/s", "retraction speed"),
    "flow_ratio": ("", "flow ratio"),
    "pressure_advance": ("", "pressure advance"),
    "dry_temp": ("°C", "drying temperature"),
    "dry_time": ("h", "drying time"),
    "data_sheet": ("", "data sheet URL"),
}

# How OrcaSlicer filament settings come from the canonical keys (merge.py fills them).
BED_PLATES_HOT = ("hot_plate_temp", "textured_plate_temp")   # smooth and textured PEI: the data sheet's bed
