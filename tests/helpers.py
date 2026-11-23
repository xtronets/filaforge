"""Test data: the fixtures served the way the live sources answer."""

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, os.path.join(ROOT, "src"))

from filaforge.core.net import CannedFetcher  # noqa: E402
from filaforge.core.sources import ofd, spoolmandb  # noqa: E402


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ofd_answers():
    """{url: answer} of the OFD API for the brands in fixtures/ofd."""
    base = os.path.join(FIX, "ofd")
    answers, brands = {}, []
    for brand_dir in sorted(glob.glob(os.path.join(base, "*"))):
        slug = os.path.basename(brand_dir)
        brand = _load(os.path.join(brand_dir, "brand.json"))
        brands.append({"name": brand.get("name", slug), "slug": slug, "path": slug + "/index.json"})
        materials = []
        for mat_dir in sorted(glob.glob(os.path.join(brand_dir, "*", "material.json"))):
            mslug = os.path.basename(os.path.dirname(mat_dir))
            mat = _load(mat_dir)
            materials.append({"material": mat["material"], "slug": mslug, "path": "materials/%s/index.json" % mslug})
            filaments = []
            for fpath in sorted(glob.glob(os.path.join(os.path.dirname(mat_dir), "*", "filament.json"))):
                fslug = os.path.basename(os.path.dirname(fpath))
                data = _load(fpath)
                filaments.append({"name": data["name"], "slug": fslug, "path": "filaments/%s/index.json" % fslug})
                answers[ofd.API + "brands/%s/materials/%s/filaments/%s/index.json" % (slug, mslug, fslug)] = \
                    dict(data, slug=fslug, material=mat["material"])
            answers[ofd.API + "brands/%s/materials/%s/index.json" % (slug, mslug)] = \
                {"material": mat["material"], "slug": mslug, "filaments": filaments}
        answers[ofd.API + "brands/%s/index.json" % slug] = {"name": brand.get("name"), "slug": slug,
                                                             "materials": materials}
    answers[ofd.API + "brands/index.json"] = {"brands": brands + [{"name": "Polymaker", "slug": "polymaker"}]}
    return answers


def spoolmandb_answers():
    base = os.path.join(FIX, "spoolmandb")
    with open(os.path.join(base, "listing.txt")) as f:
        names = [line.strip() for line in f if line.strip()]
    answers = {spoolmandb.LISTING: [{"name": n, "type": "file"} for n in names]}
    for path in glob.glob(os.path.join(base, "*.json")):
        with open(path, "rb") as f:
            answers[spoolmandb.RAW + os.path.basename(path)] = f.read()
    return answers


def fetcher(extra=None):
    answers = {}
    answers.update(ofd_answers())
    answers.update(spoolmandb_answers())
    answers.update(extra or {})
    return CannedFetcher(answers)


def _resolve(name, own, lib, seen=()):
    data = own.get(name) or lib.get(name)
    if data is None or name in seen:
        return {}
    parent = data.get("inherits")
    merged = dict(_resolve(parent, own, lib, seen + (name,))) if parent else {}
    merged.update(data)
    return merged


def orca_presets(printer="Creality K1C 0.4 nozzle"):
    """The filament presets of the fixtures as the plugin reads them from OrcaSlicer, for one printer."""
    lib = {d["name"]: d for d in map(_load, glob.glob(os.path.join(FIX, "orca", "lib", "*.json")))}
    own = {d["name"]: d for d in map(_load, glob.glob(os.path.join(FIX, "orca", "creality", "*.json")))}
    out = []
    for group in (lib, own):
        for name, data in group.items():
            if str(data.get("instantiation")) != "true":
                continue
            full = _resolve(name, own if group is own else {}, lib)
            printers = full.get("compatible_printers") or []
            config = {k: ",".join(map(str, v)) if isinstance(v, list) else str(v) for k, v in full.items()}
            out.append({"name": name, "vendor": (full.get("filament_vendor") or [""])[0],
                        "type": (full.get("filament_type") or [""])[0], "is_system": True,
                        "compatible": not printers or printer in printers, "specific": bool(printers),
                        "filament_id": full.get("filament_id", ""), "config": config})
    return out


def tds_text(name):
    with open(os.path.join(FIX, "tds", name), encoding="utf-8") as f:
        return f.read()
