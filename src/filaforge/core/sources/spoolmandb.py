"""SpoolmanDB (https://github.com/Donkie/SpoolmanDB, MIT licence): the community list behind Spoolman's
"external" filaments: temperatures and density per maker and product, read from its source files.
"""

import re

from ..model import DECLARED, Observation, SourceReport
from ..net import FetchError
from ..text import brand_key, match_score, split_material

LISTING = "https://api.github.com/repos/Donkie/SpoolmanDB/contents/filaments"
RAW = "https://raw.githubusercontent.com/Donkie/SpoolmanDB/main/filaments/"
LICENCE = "SpoolmanDB, (c) Donkie, MIT licence"
WEIGHT = 0.6
WEEK = 7 * 24 * 3600
MIN_SCORE = 0.45


def _flat(text):
    return re.sub(r"[^a-z0-9]", "", brand_key(text))


def brand_file(names, brand):
    """The source file of the brand among the listed file names, or None."""
    want = _flat(brand)
    for name in names:
        if _flat(name[:-5]) == want:
            return name
    return next((n for n in names if want and _flat(n[:-5]).startswith(want)), None)


def product_name(entry):
    """The product words of an entry: its name without the colour placeholder, plus its material."""
    name = re.sub(r"\{color_name\}", " ", str(entry.get("name") or ""))
    return re.sub(r"\s+", " ", "%s %s" % (name.replace(" - ", " "), entry.get("material") or "")).strip()


def best_entry(entries, identity):
    base, _ = split_material(identity.material)
    query = identity.product or identity.material
    scored = []
    for e in entries:
        if split_material(e.get("material"))[0] != base:
            continue
        scored.append((match_score(query, product_name(e), base), e))
    scored.sort(key=lambda pair: -pair[0])
    if not scored or scored[0][0] < MIN_SCORE:
        return None, 0.0
    return scored[0][1], scored[0][0]


def observations(entry, label, url):
    out = []

    def add(key, value):
        out.append(Observation(key, value, DECLARED, "spoolmandb", label, url, WEIGHT))

    rng = entry.get("extruder_temp_range")
    if isinstance(rng, list) and len(rng) == 2 and all(isinstance(v, (int, float)) for v in rng):
        add("nozzle_temp_range", (float(rng[0]), float(rng[1])))
    if isinstance(entry.get("extruder_temp"), (int, float)):
        add("nozzle_temp", float(entry["extruder_temp"]))
    rng = entry.get("bed_temp_range")
    if isinstance(rng, list) and len(rng) == 2 and all(isinstance(v, (int, float)) for v in rng):
        add("bed_temp_range", (float(rng[0]), float(rng[1])))
    if isinstance(entry.get("bed_temp"), (int, float)):
        add("bed_temp", float(entry["bed_temp"]))
    if isinstance(entry.get("density"), (int, float)) and entry["density"] > 0:
        add("density", float(entry["density"]))
    return out


def lookup(fetcher, identity):
    try:
        listing = fetcher.json(LISTING, ttl=WEEK)
        names = [i["name"] for i in listing if isinstance(i, dict) and str(i.get("name", "")).endswith(".json")]
        name = brand_file(names, identity.brand)
        if name is None:
            return [], SourceReport("spoolmandb", True, 0, "brand not in SpoolmanDB")
        doc = fetcher.json(RAW + name, ttl=WEEK)
    except (FetchError, KeyError, TypeError) as error:
        return [], SourceReport("spoolmandb", False, 0, str(error))
    entry, score = best_entry(doc.get("filaments") or [], identity)
    if entry is None:
        return [], SourceReport("spoolmandb", True, 0, "no matching product")
    page = "https://github.com/Donkie/SpoolmanDB/blob/main/filaments/" + name
    label = "SpoolmanDB: %s %s" % (doc.get("manufacturer") or identity.brand, product_name(entry))
    obs = observations(entry, label, page)
    return obs, SourceReport("spoolmandb", True, len(obs), "%s (match %.0f%%)" % (product_name(entry), score * 100),
                             page)
