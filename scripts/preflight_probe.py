"""Slices a test model with a real OrcaSlicer (command line) and runs FilaForge's G-code reading and checks on it.

    python scripts/preflight_probe.py <squashfs-root of the AppImage> <out folder>

The printer, process and filament come from OrcaSlicer's own system profiles, flattened (their "inherits" chain
merged) so the command line needs no data folder. The model: a 40 × 40 × 10 mm block (solid layers, infill) with
a 6 × 6 × 30 mm tower on it (short layers at the top). Prints the summary and the findings as JSON and writes
them to the job summary when there is one.
"""

import glob
import json
import os
import struct
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from filaforge.core import gcode, preflight  # noqa: E402

PRINTER = "Creality K1C 0.4 nozzle"
PROCESS = "0.20mm Standard @Creality K1C 0.4 nozzle"
FILAMENT = "Generic PLA @K1C-all"


def index(root):
    """{(kind, name): path} of every system profile."""
    out = {}
    for path in glob.glob(os.path.join(root, "*", "*", "*.json")) + glob.glob(os.path.join(root, "*", "*.json")):
        kind = os.path.basename(os.path.dirname(path))
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("name"):
            out.setdefault((kind, data["name"]), path)
    return out


def flatten(profiles, kind, name):
    with open(profiles[(kind, name)], encoding="utf-8") as f:
        data = json.load(f)
    parent = data.get("inherits")
    if parent:
        base = flatten(profiles, kind, parent)
        base.update({k: v for k, v in data.items()})
        data = base
    data.pop("inherits", None)
    return data


def box(x0, y0, z0, x1, y1, z1):
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7),
             (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return [(v[a], v[b], v[c]) for a, b, c in faces]


def write_stl(path, triangles):
    with open(path, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(triangles)))
        for tri in triangles:
            f.write(struct.pack("<3f", 0, 0, 0))
            for p in tri:
                f.write(struct.pack("<3f", *p))
            f.write(b"\0\0")


def main(app, out):
    os.makedirs(out, exist_ok=True)
    found = [p for p in glob.glob(os.path.join(app, "**", "profiles", "Creality"), recursive=True) if os.path.isdir(p)]
    if not found:
        print("no OrcaSlicer profiles under", app)
        return 1
    root = os.path.dirname(found[0])
    print("profiles:", root)
    profiles = index(root)
    paths = {}
    for kind, name, key in (("machine", PRINTER, "printer"), ("process", PROCESS, "process"),
                            ("filament", FILAMENT, "filament")):
        data = flatten(profiles, kind, name)
        if kind == "process":
            data["compatible_printers"] = [PRINTER]
        if kind == "filament":
            data["compatible_printers"] = [PRINTER]
        paths[key] = os.path.join(out, key + ".json")
        with open(paths[key], "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    model = os.path.join(out, "probe.stl")
    write_stl(model, box(0, 0, 0, 40, 40, 10) + box(17, 17, 10, 23, 23, 40))
    cmd = [os.path.join(app, "AppRun"), "--slice", "0", "--load-settings", paths["printer"] + ";" + paths["process"],
           "--load-filaments", paths["filament"], "--outputdir", out, model]
    print(" ".join(cmd), flush=True)
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    print(run.stdout[-3000:], run.stderr[-3000:], sep="\n")
    files = glob.glob(os.path.join(out, "*.gcode"))
    if not files:
        print("no G-code written")
        return 1
    result = gcode.read(files[0])
    measured = (result.number("filament_max_volumetric_speed") or 20.0) * 0.6
    report = preflight.check(result, {"cls": "PLA", "measured_mvs": measured, "spool_left_g": 5.0,
                                      "spool_label": "probe"})
    summary = result.summary()
    text = json.dumps({"file": os.path.basename(files[0]), "summary": summary, "report": report,
                       "layers": [lay.as_dict() for lay in result.layers[:3] + result.layers[-3:]]}, indent=1)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Pre-flight probe (%s)\n\n```json\n%s\n```\n" % (os.path.basename(files[0]), text))
    ok = summary["layers"] > 100 and summary["roles"] and any(f["code"] == "spool_short" for f in report["findings"])
    print("probe ok" if ok else "probe FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
