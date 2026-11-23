"""Names: brands, materials and product words, normalised so sources that spell them differently meet."""

import re
import unicodedata

# Spellings of a material to the base material OrcaSlicer's presets use (filament_type), plus the words
# they add to the product ("PLA+" is PLA, product "PLA+").
MATERIAL_BASE = {
    "PLA+": "PLA", "PLAPLUS": "PLA", "PLA PLUS": "PLA", "PLA PRO": "PLA", "PLAPRO": "PLA", "HS PLA": "PLA",
    "HSPLA": "PLA", "PLA HS": "PLA", "SILK PLA": "PLA", "PLA SILK": "PLA", "MATTE PLA": "PLA", "PLA MATTE": "PLA",
    "PLA-CF": "PLA-CF", "PLA CF": "PLA-CF", "PETG+": "PETG", "HS PETG": "PETG", "PETG HF": "PETG", "PETG-HF": "PETG",
    "PETG-CF": "PETG-CF", "PETG CF": "PETG-CF", "ABS+": "ABS", "NYLON": "PA", "PA6": "PA", "PA12": "PA",
    "PA-CF": "PA-CF", "PA6-CF": "PA-CF", "PA12-CF": "PA-CF", "TPU 95A": "TPU", "TPU95A": "TPU", "TPE": "TPU",
}
KNOWN_MATERIALS = ("PLA-CF", "PETG-CF", "PA-CF", "PLA", "PETG", "PCTG", "ABS", "ASA", "TPU", "PC", "PA", "PET",
                   "PVA", "HIPS", "PP", "PVB", "PEEK", "PEI")
# Product words that make a different filament of the same material, so a match must not ignore them.
DISTINCT_WORDS = {"silk", "matte", "high", "speed", "hs", "hf", "cf", "gf", "wood", "glow", "marble", "galaxy",
                  "rainbow", "dual", "tri", "metal", "lite", "translucent", "transparent", "clear", "carbon",
                  "fiber", "fibre", "2.0", "plus", "+", "pro", "flex", "95a", "85a", "esd", "eco", "tough",
                  "uv", "luminous", "twinkling", "sparkle", "basic", "meta", "upgrade", "fluorescent", "temp",
                  "color", "change", "aero", "foam", "lw"}
BRAND_ALIASES = {"bambu": "bambu lab", "bambulab": "bambu lab", "esun": "esun", "e-sun": "esun",
                 "polymaker": "polymaker", "prusament": "prusament", "prusa": "prusament", "sunlu": "sunlu",
                 "elegoo": "elegoo", "creality": "creality", "kingroon": "kingroon", "overture": "overture",
                 "jayo": "jayo", "geeetech": "geeetech", "eryone": "eryone", "anycubic": "anycubic",
                 "snapmaker": "snapmaker", "fiberlogy": "fiberlogy", "spectrum": "spectrum",
                 "devil design": "devil design", "rosa3d": "rosa3d", "rosa 3d": "rosa3d"}


def fold(text):
    """Lower case, no accents, single spaces."""
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text.lower()).strip()


def brand_key(brand):
    key = fold(brand).replace("_", " ").strip()
    key = re.sub(r"\b(filaments?|3d|inc|ltd|gmbh|co)\b", "", key).strip(" .-")
    return BRAND_ALIASES.get(key, BRAND_ALIASES.get(key.replace(" ", ""), key))


def tokens(text):
    """The words of a product name; "PLA+" keeps its "+", "2.0" stays one word."""
    text = fold(text).replace("_", " ")
    text = re.sub(r"(?<=[a-z])\+", " +", text)
    return [t for t in re.split(r"[^a-z0-9.+]+", text) if t and t != "."]


def split_material(material, product=""):
    """(base material, product words) of a Spoolman/user material such as "PLA+" or "Silk PLA"."""
    raw = re.sub(r"\s+", " ", str(material or "").strip().upper())
    base = MATERIAL_BASE.get(raw)
    if base is None:
        base = next((m for m in KNOWN_MATERIALS if re.search(r"(^|[^A-Z])" + re.escape(m) + r"($|[^A-Z])", raw)),
                    raw or "PLA")
    words = product.strip() if product and product.strip() else ""
    if raw and raw != base and fold(raw) not in fold(words):
        words = (raw + " " + words).strip()
    return base, words


def match_score(query, name, material=""):
    """How well a source's product name fits what the user asked for, 0..1.

    The material word itself counts for nothing (every PLA is PLA); a distinct word on one side only
    (Silk, High Speed, 2.0, +) costs a lot, any other unmatched word a little."""
    mat = set(tokens(material))
    q = [t for t in tokens(query) if t not in mat]
    n = [t for t in tokens(name) if t not in mat]
    if not q and not n:
        return 1.0
    qs, ns = set(q), set(n)
    common = qs & ns
    only = (qs - ns) | (ns - qs)
    penalty = sum(0.45 if t in DISTINCT_WORDS else 0.15 for t in only)
    base = (2 * len(common)) / (len(qs) + len(ns)) if (qs or ns) else 1.0
    if not q:   # nothing asked beyond the material: the plain product is the best fit
        base = 1.0
    return max(0.0, min(1.0, base - penalty + (0.1 if common else 0)))


def round_to(value, step=5):
    return int(round(float(value) / step) * step)


def number(text):
    """A float from "1.24", "1,24", "220 °C"; None when there is none."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    found = re.search(r"-?\d+(?:[.,]\d+)?", str(text or ""))
    return float(found.group(0).replace(",", ".")) if found else None
