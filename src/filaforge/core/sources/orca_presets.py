"""OrcaSlicer's own filament presets: the one a vendor made for this filament (kind "preset"), and the
generic preset of the material on the selected printer (kind "baseline", the floor everything starts from).

Works on plain data the plugin reads from OrcaSlicer on its UI thread (see plugin.read_presets): a list of
{"name", "vendor", "type", "is_system", "compatible", "filament_id", "config": {key: serialized text}}.
"""

import re

from ..model import BASELINE, PRESET, Observation, SourceReport
from ..text import brand_key, fold, match_score, number, split_material, tokens

VENDOR_WEIGHT = 0.8
GENERIC_WEIGHT = 0.3
MIN_SCORE = 0.5

# OrcaSlicer key -> (canonical key, how to read it)
READ = {
    "nozzle_temperature": ("nozzle_temp", "first"),
    "hot_plate_temp": ("bed_temp", "first"),
    "filament_density": ("density", "first"),
    "filament_max_volumetric_speed": ("max_volumetric_speed", "first"),
    "fan_min_speed": ("fan_min", "first"),
    "fan_max_speed": ("fan_max", "first"),
    "filament_flow_ratio": ("flow_ratio", "first"),
    "temperature_vitrification": ("vitrification", "first"),
    "chamber_temperature": ("chamber_temp", "first"),
    "filament_retraction_length": ("retraction_length", "first"),
    "filament_retraction_speed": ("retraction_speed", "first"),
}


def first(text):
    """The first value of OrcaSlicer's serialized text ("220,220" -> 220.0); None for nil/empty."""
    if text is None:
        return None
    part = str(text).split(",")[0].strip().strip('"')
    if not part or part.lower() == "nil":
        return None
    return number(part)


def bare_name(name):
    """A preset name without its "@printer" part and "_local/..." prefix."""
    name = re.sub(r"^_local/[^/]+/", "", str(name or ""))
    return re.sub(r"\s*@.*$", "", name).strip()


def is_generic(preset):
    return fold(preset.get("vendor")) in ("generic", "") and "generic" in fold(preset.get("name")) or \
        fold(bare_name(preset.get("name"))).startswith("generic ")


def same_material(preset, base):
    return split_material(preset.get("type") or "")[0] == base


def brand_matches(preset, brand):
    want = brand_key(brand)
    if not want:
        return False
    if brand_key(preset.get("vendor")) == want:
        return True
    return fold(bare_name(preset.get("name"))).startswith(want + " ")


def product_score(preset, identity, hint=""):
    """How well a vendor preset's name fits the product (the brand's words left out)."""
    brand_words = set(tokens(identity.brand)) | set(tokens(brand_key(identity.brand)))
    name = " ".join(t for t in tokens(bare_name(preset["name"])) if t not in brand_words)
    query = identity.product or identity.material
    score = match_score(query, name, identity.material)
    if hint and fold(bare_name(preset["name"])) == fold(hint):
        score = max(score, 0.99)
    return score


def pick(presets, identity, hint=""):
    """(vendor preset or None, its score, generic preset or None), only presets usable on the printer."""
    base, _ = split_material(identity.material)
    usable = [p for p in presets if p.get("compatible") and same_material(p, base)]
    vendor, score = None, 0.0
    for p in usable:
        if brand_matches(p, identity.brand) and not is_generic(p):
            s = product_score(p, identity, hint)
            # a system preset beats a user copy of equal fit
            key = (s, 1 if p.get("is_system") else 0)
            if vendor is None or key > (score, 1 if vendor.get("is_system") else 0):
                vendor, score = p, s
    if vendor is not None and score < MIN_SCORE:
        vendor, score = None, 0.0
    generics = [p for p in usable if is_generic(p)]
    query = identity.product or identity.material

    def generic_rank(p):
        words = " ".join(t for t in tokens(bare_name(p["name"])) if t != "generic")
        # the generic of the same kind ("Generic PLA High Speed" for a high speed PLA), then one made for
        # this printer, then a system one
        return (-round(match_score(query, words, identity.material), 2), not p.get("specific"),
                not p.get("is_system"), len(p["name"]))
    generic = min(generics, key=generic_rank, default=None)
    return vendor, score, generic


def observations(preset, kind, weight):
    config = preset.get("config") or {}
    label = "OrcaSlicer preset: %s" % preset["name"]
    out = []
    for orca_key, (key, _) in READ.items():
        value = first(config.get(orca_key))
        if value is None or (value == 0 and key in ("vitrification", "chamber_temp", "bed_temp")):
            continue
        out.append(Observation(key, value, kind, "orca" if kind == PRESET else "generic", label, "", weight))
    lo, hi = first(config.get("nozzle_temperature_range_low")), first(config.get("nozzle_temperature_range_high"))
    if lo and hi and lo < hi:
        out.append(Observation("nozzle_temp_range", (lo, hi), kind, "orca" if kind == PRESET else "generic",
                               label, "", weight))
    pa = first(config.get("pressure_advance"))
    if pa is not None and first(config.get("enable_pressure_advance")):
        out.append(Observation("pressure_advance", pa, kind, "orca" if kind == PRESET else "generic", label, "",
                               weight))
    return out


def lookup(presets, identity, hint=""):
    """(observations, report, chosen) where chosen = {"vendor": preset|None, "generic": preset|None}."""
    vendor, score, generic = pick(presets, identity, hint)
    obs = []
    if vendor:
        obs += observations(vendor, PRESET, VENDOR_WEIGHT)
    if generic:
        obs += observations(generic, BASELINE, GENERIC_WEIGHT)
    if vendor:
        msg = "%s (match %.0f%%)" % (vendor["name"], score * 100)
    elif generic:
        msg = "no preset of this brand for the printer; generic: %s" % generic["name"]
    else:
        msg = "no usable preset of this material for the printer"
    return obs, SourceReport("orca", vendor is not None or generic is not None, len(obs), msg), \
        {"vendor": vendor, "generic": generic}
