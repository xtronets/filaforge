"""Open Filament Database (https://openfilamentdatabase.org, data under the MIT licence,
(c) OpenFilamentCollective): what makers declare for each filament, often with the data sheet's address.

Read through its static JSON API: brands -> the brand's materials -> the material's filaments -> one filament.
"""

from ..model import DECLARED, Observation, SourceReport
from ..net import FetchError
from ..text import brand_key, fold, match_score, split_material

API = "https://api.openfilamentdatabase.org/api/v1/"
LICENCE = "Open Filament Database, (c) OpenFilamentCollective, MIT licence"
WEIGHT = 0.85
WEEK = 7 * 24 * 3600
MIN_SCORE = 0.45


def find_brand(brands, brand):
    want = brand_key(brand)
    best = None
    for b in brands:
        for name in (b.get("name"), b.get("slug")):
            key = brand_key(name)
            if key == want:
                return b
            if key and want and (key.startswith(want + " ") or want.startswith(key + " ")):
                best = best or b
    return best


def find_filament(filaments, identity):
    """(filament entry, score) of the best match for the identity's product, or (None, 0)."""
    query = identity.product or identity.material
    scored = sorted(((match_score(query, f.get("name") or f.get("slug") or "", identity.material), f)
                     for f in filaments), key=lambda pair: -pair[0])
    if not scored or scored[0][0] < MIN_SCORE:
        return None, 0.0
    return scored[0][1], scored[0][0]


def observations(data, label, url):
    """The values of one OFD filament entry as Observations."""
    out = []

    def add(key, value, note=""):
        out.append(Observation(key, value, DECLARED, "ofd", label, url, WEIGHT, note))

    def num(name):
        v = data.get(name)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    lo, hi = num("min_print_temperature"), num("max_print_temperature")
    if lo and hi and lo <= hi:
        add("nozzle_temp_range", (lo, hi))
    lo, hi = num("min_bed_temperature"), num("max_bed_temperature")
    if lo is not None and hi is not None and lo <= hi:
        add("bed_temp_range", (lo, hi))
    for key, name in (("density", "density"), ("max_volumetric_speed", "max_volumetric_speed"),
                      ("print_speed_max", "max_print_speed"), ("fan_min", "min_fan_speed"),
                      ("fan_max", "max_fan_speed"), ("dry_time", "dry_time")):
        v = num(name)
        if v is not None and v > 0:
            add(key, v)
    chamber = num("chamber_temperature") or num("min_chamber_temperature")
    if chamber:
        add("chamber_temp", chamber)
    dry = num("dry_temperature") or num("max_dry_temperature")
    if dry:
        add("dry_temp", dry)
    sheet = data.get("data_sheet_url")
    if isinstance(sheet, str) and sheet.startswith("https://"):
        add("data_sheet", sheet)
    return out


def lookup(fetcher, identity):
    """(observations, report, extra) for the identity; extra carries the Orca preset name OFD links
    ("orca_profile") and the data sheet address."""
    extra = {}
    try:
        brands = fetcher.json(API + "brands/index.json").get("brands") or []
        brand = find_brand(brands, identity.brand)
        if brand is None:
            return [], SourceReport("ofd", True, 0, "brand not in the database"), extra
        slug = brand["slug"]
        brand_doc = fetcher.json(API + "brands/%s/index.json" % slug)
        base, _ = split_material(identity.material)
        materials = [m for m in brand_doc.get("materials") or []
                     if split_material(m.get("material") or m.get("slug"))[0] == base]
        candidates = []
        for m in materials:
            doc = fetcher.json(API + "brands/%s/materials/%s/index.json" % (slug, m["slug"]))
            candidates += [dict(f, _material=m["slug"]) for f in doc.get("filaments") or []]
        chosen, score = find_filament(candidates, identity)
        if chosen is None:
            names = ", ".join(sorted({c.get("name", "") for c in candidates})[:12])
            return [], SourceReport("ofd", True, 0, "no matching product (has: %s)" % names), extra
        url = API + "brands/%s/materials/%s/filaments/%s/index.json" % (slug, chosen["_material"], chosen["slug"])
        data = fetcher.json(url)
    except (FetchError, KeyError, AttributeError, TypeError) as error:
        return [], SourceReport("ofd", False, 0, str(error)), extra
    label = "Open Filament Database: %s %s" % (brand.get("name") or identity.brand, data.get("name") or chosen["name"])
    page = "https://openfilamentdatabase.org/brands/%s/materials/%s/filaments/%s" % (slug, chosen["_material"],
                                                                                    chosen["slug"])
    obs = observations(data, label, page)
    orca = ((data.get("slicer_settings") or {}).get("orcaslicer") or {})
    if isinstance(orca.get("profile_name"), str):
        extra["orca_profile"] = orca["profile_name"]
    if isinstance(orca.get("id"), str):
        extra["orca_filament_id"] = orca["id"]
    extra["match"] = {"name": data.get("name") or chosen.get("name"), "score": round(score, 2)}
    return obs, SourceReport("ofd", True, len(obs), "%s (match %.0f%%)" % (fold(data.get("name")), score * 100),
                             page, extra), extra


def products(fetcher, brand):
    """[{"name", "material"}] of every filament OFD lists for the brand ([] when it doesn't know the brand)."""
    brands = fetcher.json(API + "brands/index.json").get("brands") or []
    found = find_brand(brands, brand)
    if found is None:
        return []
    doc = fetcher.json(API + "brands/%s/index.json" % found["slug"])
    out = []
    for m in doc.get("materials") or []:
        try:
            mdoc = fetcher.json(API + "brands/%s/materials/%s/index.json" % (found["slug"], m["slug"]))
        except FetchError:
            continue
        out += [{"name": f.get("name") or f.get("slug"), "material": m.get("material") or m["slug"]}
                for f in mdoc.get("filaments") or []]
    return out
