"""Print defects: from what the user sees to the likely causes, in order, and one fix at a time.

A defect has a few questions and several possible causes. Each cause looks at the facts (the material, the
filament and process presets that printed it, the printer, the user's measurements, their answers, and what was
already tried for this defect) and says how likely it is, why, and what to do: change settings (within the
material's and the preset's limits), print one of OrcaSlicer's calibration tests, or something no setting fixes
(dry the filament, clean the plate, level the bed). The plan puts the likeliest first.

After a print the user says how it went. "Better" keeps the change (and the next step of the same cause, from the
new values, is offered when there is one); "no change" or "worse" undoes it and that cause drops to the bottom.

Everything here is plain data: the plugin reads OrcaSlicer and writes the presets.
"""

import json
import os
import time

from . import files, habits
from .review import number

DEFECTS = ("stringing", "first_layer", "layer_split", "dimensions", "over_extrusion", "under_extrusion",
           "overhangs", "warping", "seam", "ghosting", "top_holes", "wall_gaps", "curling", "supports", "layer_shift")

# defect -> [(question, (answers...))]; the first answer is the default
QUESTIONS = {
    "stringing": [("dried", ("unknown", "no", "yes")), ("kind", ("hairs", "strings"))],
    "first_layer": [("where", ("everywhere", "area", "edges")), ("cleaned", ("unknown", "no", "yes"))],
    "layer_split": [("where", ("everywhere", "higher"))],
    "dimensions": [],
    "over_extrusion": [("how", ("all_big", "top_rough", "blobs"))],
    "under_extrusion": [("when", ("always", "fast", "later"))],
    "overhangs": [("which", ("both", "bridges", "overhangs"))],
    "warping": [("big", ("no", "yes")), ("cleaned", ("unknown", "no", "yes"))],
    "seam": [("look", ("line", "zits", "gap", "bump"))],
    "ghosting": [("axis", ("both", "one")), ("fast", ("no", "yes"))],
    "top_holes": [("look", ("holes", "lines", "rough"))],
    "wall_gaps": [("where", ("infill", "walls", "top"))],
    "curling": [("where", ("overhangs", "small", "corners"))],
    "supports": [("problem", ("stuck", "underside", "fail"))],
    "layer_shift": [("when", ("random", "same_height")), ("axis", ("one", "both"))],
}
# the OrcaSlicer calibration test a "calibration" cause asks for, and what to read from it
TESTS = ("flow", "pa", "mvs", "temp", "retraction", "input_shaping")
# measurements a defect can take
INPUTS = {"dimensions": ("rows",), "over_extrusion": ("wall",), "under_extrusion": ("wall",)}

ENCLOSED_MATERIALS = ("ABS", "ASA", "PC", "PA")
HYGROSCOPIC = ("PETG", "PA", "TPU", "PC")
# usual first-layer bed (°C), the most the bed should go, a part fan an enclosed material tolerates (%)
BED_TYPICAL = {"PLA": 60, "SILK": 60, "CF": 60, "PETG": 75, "ABS": 100, "ASA": 100, "PC": 110, "PA": 90, "TPU": 50}
BED_MAX = {"PLA": 65, "SILK": 65, "CF": 70, "PETG": 90, "ABS": 110, "ASA": 110, "PC": 120, "PA": 100, "TPU": 60}
FAN_GENTLE = {"ABS": 40, "ASA": 40, "PC": 30, "PA": 40, "PETG": 60}
DRYING = {"PLA": (50, 5), "SILK": (50, 5), "CF": (55, 6), "PETG": (65, 5), "ABS": (80, 4), "ASA": (80, 4),
          "PC": (90, 6), "PA": (85, 10), "TPU": (55, 6)}
PLATE_KEYS = ("hot_plate_temp", "textured_plate_temp", "cool_plate_temp", "eng_plate_temp", "supertack_plate_temp",
              "textured_cool_plate_temp")
LIMIT = -100           # score of a cause that was tried and didn't help


def _fmt(value, digits=3):
    text = ("%." + str(digits) + "f") % value
    return text.rstrip("0").rstrip(".") if "." in text else text


def _first(text):
    """The first value of an OrcaSlicer setting as text ("220,220" -> "220")."""
    if text is None:
        return ""
    if isinstance(text, (list, tuple)):
        text = text[0] if text else ""
    return str(text).split(";")[0].split(",")[0].strip().strip('"')


class Facts:
    """What a cause can look at. ctx: {cls, filament: {key: text}, process: {...}, printer: {...}, enclosed,
    chamber_control, measured: {key: number}, tried: {cause: [outcomes]}}."""

    def __init__(self, ctx):
        self.cls = ctx.get("cls") or "PLA"
        self.fil = ctx.get("filament") or {}
        self.proc = ctx.get("process") or {}
        self.prn = ctx.get("printer") or {}
        self.enclosed = ctx.get("enclosed")
        self.chamber = bool(ctx.get("chamber_control"))
        self.measured = ctx.get("measured") or {}
        self.tried = ctx.get("tried") or {}

    def f(self, key, default=None):
        value = number(self.fil.get(key))
        return default if value is None else value

    def p(self, key, default=None):
        value = number(self.proc.get(key))
        return default if value is None else value

    def temps(self):
        """(nozzle, low, high, position in the range 0..1) or None."""
        temp = self.f("nozzle_temperature")
        if not temp:
            return None
        lo, hi = self.f("nozzle_temperature_range_low"), self.f("nozzle_temperature_range_high")
        if not lo or not hi or hi <= lo:
            lo, hi = temp - 15, temp + 15
        return temp, lo, hi, max(0.0, min(1.0, (temp - lo) / (hi - lo)))

    def retraction(self):
        """(length now, from the filament?, Bowden?)."""
        own = self.f("filament_retraction_length")
        printer = number(self.prn.get("retraction_length")) or 0.8
        bowden = str(self.prn.get("extruder_type") or "").lower().startswith("bowden") or printer >= 2.5
        return (own if own is not None else printer), own is not None, bowden

    def needs_enclosure(self):
        return self.cls in ENCLOSED_MATERIALS and not self.chamber and not self.enclosed


def _change(target, key, before, after):
    return {"target": target, "key": key, "before": before, "after": after}


def _cause(cause, kind, score, why, vars_=None, changes=None, test=None):
    return {"cause": cause, "kind": kind, "score": score, "why": why, "vars": vars_ or {}, "changes": changes or [],
            "test": test}


# --- the causes ---------------------------------------------------------------------------------------------------
def _temp_step(x, delta, cause, base, why, initial=True):
    """Nozzle temperature by delta within the preset's range; None at its edge."""
    t = x.temps()
    if t is None:
        return None
    temp, lo, hi, pos = t
    new = temp + delta
    if new < lo or new > hi:
        return None
    changes = [_change("filament", "nozzle_temperature", _fmt(temp), _fmt(new))]
    first = x.f("nozzle_temperature_initial_layer")
    if initial and first:
        moved = max(first + delta, lo) if delta < 0 else min(first + delta, hi + 5)
        if (moved < first) if delta < 0 else (moved > first):   # never the other way
            changes.append(_change("filament", "nozzle_temperature_initial_layer", _fmt(first), _fmt(moved)))
    score = base + (30 * pos if delta < 0 else 30 * (1 - pos))
    return _cause(cause, "change", score, why, {"from": _fmt(temp), "to": _fmt(new), "lo": _fmt(lo), "hi": _fmt(hi)},
                  changes)


def _dry(x, base, extra=0):
    temp, hours = DRYING.get(x.cls, (50, 5))
    score = base + extra + (25 if x.cls in HYGROSCOPIC else 0)
    return _cause("dry", "advice", score, "w_dry", {"cls": x.cls, "temp": temp, "hours": hours})


def stringing(x, a):
    out = []
    dried = a.get("dried", "unknown")
    out.append(_dry(x, 35, {"no": 25, "unknown": 10, "yes": -60}[dried] + (10 if a.get("kind") == "strings" else 0)))
    temp = _temp_step(x, -5, "temp_down", 30 + (15 if x.cls in ("PLA", "SILK", "CF") else 0), "w_temp_down")
    if temp:
        out.append(temp)
    length, own, bowden = x.retraction()
    step, cap, usual = (0.5, 6.5, 4.0) if bowden else (0.2, 1.6, 0.6)
    if length + step <= cap:
        score = 30 + (15 if a.get("kind") == "hairs" else 0) + (15 if length < usual else 0)
        out.append(_cause("retraction", "change", score, "w_retraction",
                          {"from": _fmt(length, 2), "to": _fmt(length + step, 2), "bowden": bowden},
                          [_change("filament", "filament_retraction_length", _fmt(length, 2) if own else "nil",
                                   _fmt(length + step, 2))]))
    if "retraction_length" not in x.measured:
        out.append(_cause("retraction_test", "calibration", 30 + (10 if a.get("kind") == "strings" else 0),
                          "w_retraction_test", test="retraction"))
    if "nozzle_temp" not in x.measured:
        out.append(_cause("temp_test", "calibration", 28, "w_temp_test", test="temp"))
    speed = x.f("filament_retraction_speed") or number(x.prn.get("retraction_speed"))
    if speed and speed < 30 and not bowden:
        out.append(_cause("retraction_speed", "change", 25, "w_retraction_speed", {"from": _fmt(speed), "to": "40"},
                          [_change("filament", "filament_retraction_speed", _fmt(speed), "40")]))
    return out


def first_layer(x, a):
    out = []
    where, cleaned = a.get("where", "everywhere"), a.get("cleaned", "unknown")
    if where == "edges":
        out.append(_cause("see_warping", "redirect", 85, "w_see_warping", {"defect": "warping"}))
    out.append(_cause("clean_plate", "advice", 40 + {"no": 35, "unknown": 15, "yes": -60}[cleaned], "w_clean_plate"))
    out.append(_cause("level", "advice", 70 if where == "area" else 40, "w_level_area" if where == "area" else "w_z_offset"))
    fan_off = x.f("close_fan_the_first_x_layers")
    if fan_off is not None and fan_off < 1:
        out.append(_cause("first_fan", "change", 60, "w_first_fan", {"to": 1},
                          [_change("filament", "close_fan_the_first_x_layers", _fmt(fan_off), "1")]))
    bed = _bed_step(x, 5, initial_only=True)
    if bed:
        low = bed["vars"]["from"] < BED_TYPICAL.get(x.cls, 60)
        bed.update(cause="bed_first", score=30 + (25 if low else 0), why="w_bed_first")
        out.append(bed)
    speed = x.p("initial_layer_speed")
    if speed and speed > 25:
        new = max(20.0, round(speed * 0.7))
        out.append(_cause("first_speed", "change", 25 + (20 if speed > 50 else 0), "w_first_speed",
                          {"from": _fmt(speed), "to": _fmt(new)},
                          [_change("process", "initial_layer_speed", _fmt(speed), _fmt(new))]))
    t = x.temps()
    first = x.f("nozzle_temperature_initial_layer")
    if t and first and first + 5 <= t[2] + 5:
        out.append(_cause("nozzle_first", "change", 20, "w_nozzle_first", {"from": _fmt(first), "to": _fmt(first + 5)},
                          [_change("filament", "nozzle_temperature_initial_layer", _fmt(first), _fmt(first + 5))]))
    return out


def _bed_step(x, delta, initial_only=False):
    """Every plate type's bed temperature (first layer, and the others unless initial_only) by delta, within the
    material's maximum; the vars say the hot plate's."""
    top = BED_MAX.get(x.cls, 100)
    changes, shown = [], None
    for plate in PLATE_KEYS:
        keys = (plate + "_initial_layer",) if initial_only else (plate + "_initial_layer", plate)
        for key in keys:
            now = x.f(key)
            if not now:
                continue
            new = min(now + delta, top)
            if new > now:
                changes.append(_change("filament", key, _fmt(now), _fmt(new)))
                if shown is None:
                    shown = (now, new)
    if not changes:
        return None
    return _cause("bed", "change", 0, "", {"from": shown[0], "to": shown[1], "max": top}, changes)


def layer_split(x, a):
    out = []
    higher = a.get("where") == "higher"
    if x.needs_enclosure():
        out.append(_cause("enclosure", "advice", 95, "w_enclosure", {"cls": x.cls}))
    gentle = FAN_GENTLE.get(x.cls)
    fan_max = x.f("fan_max_speed")
    if gentle and fan_max and fan_max > gentle:
        changes = [_change("filament", "fan_max_speed", _fmt(fan_max), _fmt(gentle))]
        fan_min = x.f("fan_min_speed")
        if fan_min and fan_min > gentle:
            changes.append(_change("filament", "fan_min_speed", _fmt(fan_min), _fmt(gentle)))
        out.append(_cause("fan_down", "change", 70 + (10 if higher else 0), "w_fan_down",
                          {"cls": x.cls, "from": _fmt(fan_max), "to": _fmt(gentle)}, changes))
    temp = _temp_step(x, 5, "temp_up", 35 + (10 if higher else 0), "w_temp_up", initial=False)
    if temp:
        out.append(temp)
    out.append(_cause("flow_test", "calibration", 25, "w_flow_test", test="flow"))
    mvs = x.f("filament_max_volumetric_speed")
    if mvs and mvs > 15:
        new = round(mvs * 0.85, 1)
        out.append(_cause("mvs_down", "change", 25, "w_mvs_layers", {"from": _fmt(mvs, 1), "to": _fmt(new, 1)},
                          [_change("filament", "filament_max_volumetric_speed", _fmt(mvs, 1), _fmt(new, 1))]))
    if higher:
        out.append(_cause("draft", "advice", 30, "w_draft"))
    return out


def fit_dimensions(rows):
    """From measured parts to the corrections: {"scale": s (measured = expected * (1 + s) + 2*offset), "offset",
    "hole": per-side hole correction, "foot": per-side elephant foot} for what the rows allow; ValueError(code).

    rows: [{"kind": "outer" | "hole" | "foot", "expected", "measured"}] in mm (a foot row: expected = width higher
    up, measured = width at the bottom). Two outer sizes far enough apart tell a scale (shrinkage) from an offset."""
    outer, holes, feet = [], [], []
    for row in rows or []:
        e, m = number(row.get("expected")), number(row.get("measured"))
        if not e or not m or e <= 0 or m <= 0:
            continue
        if abs(m - e) > max(2.0, e * 0.1):
            raise ValueError("d_odd")
        {"outer": outer, "hole": holes, "foot": feet}.get(row.get("kind"), outer).append((e, m))
    if not (outer or holes or feet):
        raise ValueError("d_missing")
    out = {}
    if outer:
        sizes = [e for e, _ in outer]
        if len(outer) >= 2 and max(sizes) - min(sizes) >= 20:
            n = len(outer)
            mean_e = sum(sizes) / n
            mean_d = sum(m - e for e, m in outer) / n
            var = sum((e - mean_e) ** 2 for e in sizes)
            s = sum((e - mean_e) * ((m - e) - mean_d) for e, m in outer) / var
            out["scale"] = s
            out["offset"] = (mean_d - s * mean_e) / 2
        else:
            out["scale"] = 0.0
            out["offset"] = sum(m - e for e, m in outer) / len(outer) / 2
    if holes:
        scale = out.get("scale", 0.0)
        # a hole shrinks by the scale too; what is left is the hole's own error, per side
        out["hole"] = sum((e * (1 + scale) - m) for e, m in holes) / len(holes) / 2
    if feet:
        out["foot"] = sum(m - e for e, m in feet) / len(feet) / 2
    return out


def dimensions(x, a, inputs):
    out = []
    try:
        fit = fit_dimensions((inputs or {}).get("rows"))
    except ValueError as error:
        return [_cause("measure", "need", 0, str(error))]
    changes = []
    scale, offset, hole = fit.get("scale", 0.0), fit.get("offset", 0.0), fit.get("hole")
    if abs(scale) >= 0.002:
        shrink = x.f("filament_shrink", 100.0)
        new = round(shrink * (1 + scale), 2)
        if 90 <= new <= 110:
            changes.append(_change("filament", "filament_shrink", _fmt(shrink, 2) + "%", _fmt(new, 2) + "%"))
    if "offset" in fit and abs(offset) >= 0.01:
        now = x.p("xy_contour_compensation", 0.0)
        new = round(now - offset, 3)
        if -0.5 <= new <= 0.5:
            changes.append(_change("process", "xy_contour_compensation", _fmt(now), _fmt(new)))
    if hole is not None and abs(hole) >= 0.01:
        now = x.p("xy_hole_compensation", 0.0)
        new = round(now + hole, 3)
        if -0.5 <= new <= 0.5:
            changes.append(_change("process", "xy_hole_compensation", _fmt(now), _fmt(new)))
    foot = fit.get("foot")
    if foot is not None and foot >= 0.01:
        now = x.p("elefant_foot_compensation", 0.0)
        new = round(min(now + foot, 1.0), 3)
        if new > now:
            changes.append(_change("process", "elefant_foot_compensation", _fmt(now), _fmt(new)))
        if foot > 0.15:
            out.append(_cause("squish", "advice", 45, "w_squish", {"mm": _fmt(foot * 2, 2)}))
    # bigger outside and smaller holes by about the same: the flow is high, not the geometry
    if "offset" in fit and hole is not None and offset > 0.03 and hole > 0.03 and "flow_ratio" not in x.measured:
        out.append(_cause("flow_test", "calibration", 80, "w_dim_flow", test="flow"))
    if changes:
        why = "w_dim_scale" if any(c["key"] == "filament_shrink" for c in changes) else "w_dim"
        out.append(_cause("compensate", "change", 70, why, {"scale": _fmt(scale * 100, 2), "offset": _fmt(offset, 3)},
                          changes))
    elif not out:
        out.append(_cause("measure", "need", 0, "d_right"))
    return out


def _wall_flow(x, inputs, under):
    """The flow ratio from a measured single wall: new = now x line width / measured wall."""
    wall = number((inputs or {}).get("wall"))
    width = x.p("outer_wall_line_width") or x.p("line_width")
    if not wall or not width:
        return None
    if width > 3:                    # a percentage of the nozzle
        width = width / 100 * (number(x.prn.get("nozzle_diameter")) or 0.4)
    now = x.f("filament_flow_ratio", 1.0)
    new = round(now * width / wall, 3)
    if abs(new - now) < 0.005:
        return _cause("flow_wall", "need", 0, "d_flow_right")
    if not 0.85 * now <= new <= 1.15 * now or (under and new < now) or (not under and new > now):
        return _cause("flow_wall", "need", 0, "d_wall_odd", {"width": _fmt(width, 2)})
    return _cause("flow_wall", "change", 95, "w_flow_wall", {"wall": _fmt(wall, 2), "width": _fmt(width, 2),
                                                             "from": _fmt(now), "to": _fmt(new)},
                  [_change("filament", "filament_flow_ratio", _fmt(now), _fmt(new))])


def _measured_flow(x):
    """The user's own flow ratio, when the preset differs from it."""
    flow, now = x.measured.get("flow_ratio"), x.f("filament_flow_ratio")
    if flow and now and abs(flow - now) >= 0.005:
        return _cause("flow_measured", "change", 85, "w_flow_measured", {"from": _fmt(now), "to": _fmt(flow)},
                      [_change("filament", "filament_flow_ratio", _fmt(now), _fmt(flow))])
    return None


def over_extrusion(x, a, inputs):
    out = [c for c in (_wall_flow(x, inputs, False), _measured_flow(x)) if c]
    how = a.get("how", "all_big")
    measured = "flow_ratio" in x.measured
    out.append(_cause("flow_test", "calibration", 40 if measured else 70, "w_flow_test", test="flow"))
    if how == "blobs":
        out.append(_cause("pa_test", "calibration", 65 if "pressure_advance" not in x.measured else 35, "w_pa_test",
                          test="pa"))
    if how == "top_rough":
        temp = _temp_step(x, -5, "temp_down", 25, "w_temp_top")
        if temp:
            out.append(temp)
    if how == "all_big":
        out.append(_cause("see_dimensions", "redirect", 30, "w_see_dimensions", {"defect": "dimensions"}))
    now = x.f("filament_flow_ratio", 1.0)
    if now > 0.9 and not any(c["kind"] == "change" and c["cause"].startswith("flow") for c in out):
        new = round(now * 0.98, 3)
        out.append(_cause("flow_step", "change", 20, "w_flow_step", {"from": _fmt(now), "to": _fmt(new), "pct": 2},
                          [_change("filament", "filament_flow_ratio", _fmt(now), _fmt(new))]))
    return out


def under_extrusion(x, a, inputs):
    out = []
    when = a.get("when", "always")
    if when == "later":
        if x.cls in ("PLA", "SILK", "CF") and x.enclosed:
            out.append(_cause("heat_creep", "advice", 85, "w_heat_creep"))
        out.append(_cause("spool", "advice", 50, "w_spool"))
        out.append(_cause("clog", "advice", 40, "w_clog"))
    if when == "fast":
        mvs = x.f("filament_max_volumetric_speed")
        if mvs:
            new = round(mvs * 0.85, 1)
            out.append(_cause("mvs_down", "change", 70, "w_mvs_down", {"from": _fmt(mvs, 1), "to": _fmt(new, 1)},
                              [_change("filament", "filament_max_volumetric_speed", _fmt(mvs, 1), _fmt(new, 1))]))
        out.append(_cause("mvs_test", "calibration", 60 if "max_volumetric_speed" not in x.measured else 30,
                          "w_mvs_test", test="mvs"))
        temp = _temp_step(x, 5, "temp_up", 30, "w_temp_flow", initial=False)
        if temp:
            out.append(temp)
    if when == "always":
        out += [c for c in (_wall_flow(x, inputs, True), _measured_flow(x)) if c]
        out.append(_cause("flow_test", "calibration", 60 if "flow_ratio" not in x.measured else 30, "w_flow_test",
                          test="flow"))
        diameter = x.f("filament_diameter")
        if diameter and abs(diameter - 1.75) > 0.05 and abs(diameter - 2.85) > 0.1:
            out.append(_cause("diameter", "change", 75, "w_diameter", {"from": _fmt(diameter, 2)},
                              [_change("filament", "filament_diameter", _fmt(diameter, 2), "1.75")]))
        out.append(_cause("clog", "advice", 30, "w_clog"))
        out.append(_dry(x, 10))
    return out


def overhangs(x, a):
    out = []
    which = a.get("which", "both")
    fan = x.f("overhang_fan_speed")
    enabled = str(_first(x.fil.get("enable_overhang_bridge_fan")) or "1").lower() in ("1", "true")
    target = 100 if x.cls in ("PLA", "SILK", "CF", "TPU") else (80 if x.cls == "PETG" else 60)
    if fan is not None and (fan < target or not enabled):
        new = min(target, fan + 20) if fan < target else fan
        changes = [_change("filament", "overhang_fan_speed", _fmt(fan), _fmt(new))] if new > fan else []
        if not enabled:
            changes.append(_change("filament", "enable_overhang_bridge_fan", "0", "1"))
        out.append(_cause("overhang_fan", "change", 60 + (20 if fan < 60 and x.cls in ("PLA", "SILK") else 0),
                          "w_overhang_fan", {"from": _fmt(fan), "to": _fmt(new), "cls": x.cls,
                                             "enclosed_material": x.cls in ENCLOSED_MATERIALS}, changes))
    if which in ("bridges", "both"):
        flow = x.p("bridge_flow")
        if flow and flow > 0.8:
            new = round(max(0.7, flow - 0.05), 2)
            out.append(_cause("bridge_flow", "change", 50 + (10 if which == "bridges" else 0), "w_bridge_flow",
                              {"from": _fmt(flow), "to": _fmt(new)},
                              [_change("process", "bridge_flow", _fmt(flow), _fmt(new))]))
        speed = x.p("bridge_speed")
        if speed and speed > 30 and x.cls not in ("PLA", "SILK"):
            out.append(_cause("bridge_speed", "change", 35, "w_bridge_speed", {"from": _fmt(speed), "to": "25"},
                              [_change("process", "bridge_speed", _fmt(speed), "25")]))
    if which in ("overhangs", "both"):
        curled = _first(x.proc.get("slowdown_for_curled_perimeters"))
        if curled in ("0", "false"):
            out.append(_cause("curled", "change", 55, "w_curled", {},
                              [_change("process", "slowdown_for_curled_perimeters", "0", "1")]))
        slow = _first(x.proc.get("enable_overhang_speed"))
        if slow in ("0", "false"):
            out.append(_cause("overhang_speed", "change", 55, "w_overhang_speed", {},
                              [_change("process", "enable_overhang_speed", "0", "1")]))
        else:
            for key in ("overhang_3_4_speed", "overhang_4_4_speed"):
                now = x.p(key)
                if now and now > 15:
                    new = max(10.0, round(now * 0.7))
                    out.append(_cause("overhang_slower", "change", 35, "w_overhang_slower",
                                      {"from": _fmt(now), "to": _fmt(new)},
                                      [_change("process", key, _fmt(now), _fmt(new))]))
                    break
    temp = _temp_step(x, -5, "temp_down", 15, "w_temp_overhang")
    if temp:
        out.append(temp)
    if "nozzle_temp" not in x.measured:
        out.append(_cause("temp_test", "calibration", 18, "w_temp_test", test="temp"))
    out.append(_cause("geometry", "advice", 20, "w_geometry"))
    return out


def warping(x, a):
    out = []
    big = a.get("big") == "yes"
    if x.needs_enclosure():
        out.append(_cause("enclosure", "advice", 95, "w_enclosure", {"cls": x.cls}))
    out.append(_cause("clean_plate", "advice", 35 + {"no": 35, "unknown": 10, "yes": -60}[a.get("cleaned", "unknown")],
                      "w_clean_plate"))
    fan_off = x.f("close_fan_the_first_x_layers")
    want = 3 if x.cls in ENCLOSED_MATERIALS else 1
    if fan_off is not None and fan_off < want:
        out.append(_cause("first_fan", "change", 55 + (15 if want == 3 else 0), "w_first_fan", {"to": want},
                          [_change("filament", "close_fan_the_first_x_layers", _fmt(fan_off), str(want))]))
    gentle = FAN_GENTLE.get(x.cls)
    fan_max = x.f("fan_max_speed")
    if gentle and fan_max and fan_max > gentle and x.cls != "PETG":
        out.append(_cause("fan_down", "change", 60, "w_fan_down", {"cls": x.cls, "from": _fmt(fan_max),
                                                                   "to": _fmt(gentle)},
                          [_change("filament", "fan_max_speed", _fmt(fan_max), _fmt(gentle))]))
    bed = _bed_step(x, 5)
    if bed:
        low = bed["vars"]["from"] < BED_TYPICAL.get(x.cls, 60)
        bed.update(cause="bed_up", score=35 + (20 if low else 0), why="w_bed_up")
        out.append(bed)
    brim = _first(x.proc.get("brim_type")) or "auto_brim"
    width = x.p("brim_width", 0.0)
    if brim in ("no_brim", "auto_brim", "inner_only", "painted", "brim_ears") or width < 3:
        out.append(_cause("brim", "change", 45 + (20 if big else 0), "w_brim", {"to": "5"},
                          [_change("process", "brim_type", brim, "outer_only"),
                           _change("process", "brim_width", _fmt(width), "5")]))
    elif width + 3 <= 15:
        out.append(_cause("brim", "change", 30 + (15 if big else 0), "w_brim_wider",
                          {"from": _fmt(width), "to": _fmt(width + 3)},
                          [_change("process", "brim_width", _fmt(width), _fmt(width + 3))]))
    if x.cls in ENCLOSED_MATERIALS:
        out.append(_cause("draft", "advice", 25, "w_draft"))
    return out


# --- surfaces, mechanics, supports -------------------------------------------------------------------------------
def _pct_value(text):
    """("10%" -> (10.0, True), "0.4" -> (0.4, False)), None when it isn't a number."""
    raw = _first(text)
    value = number(raw)
    return None if value is None else (value, raw.endswith("%"))


def _on(text):
    return _first(text).lower() in ("1", "true")


def _overhang_fan(x, base, why="w_overhang_fan"):
    """The bridge and overhang fan up to what the material takes, or None."""
    fan = x.f("overhang_fan_speed")
    target = 100 if x.cls in ("PLA", "SILK", "CF", "TPU") else (80 if x.cls == "PETG" else 60)
    if fan is None or fan >= target:
        return None
    new = min(target, fan + 20)
    return _cause("overhang_fan", "change", base, why, {"from": _fmt(fan), "to": _fmt(new), "cls": x.cls,
                                                        "enclosed_material": x.cls in ENCLOSED_MATERIALS},
                  [_change("filament", "overhang_fan_speed", _fmt(fan), _fmt(new))])


def _accel_down(x, key, cause, score, why, factor=0.6, floor=1000.0):
    """A process acceleration (or, when it is 0 = default, the default one) lowered by factor."""
    now = x.p(key) or x.p("default_acceleration")
    if not now or now <= floor:
        return None
    new = max(floor, round(now * factor / 100) * 100)
    return _cause(cause, "change", score, why, {"from": _fmt(now), "to": _fmt(new)},
                  [_change("process", key, _fmt(x.p(key) or 0), _fmt(new))])


def seam(x, a):
    out = []
    look = a.get("look", "line")
    slope = _first(x.proc.get("seam_slope_type"))
    if slope in ("none", "") and look in ("line", "gap", "bump"):
        out.append(_cause("scarf", "change", 60 + (10 if look == "line" else 0), "w_scarf", {},
                          [_change("process", "seam_slope_type", slope or "none", "external")]))
    position = _first(x.proc.get("seam_position"))
    if position == "random" and look == "zits":
        out.append(_cause("seam_aligned", "change", 70, "w_seam_random", {},
                          [_change("process", "seam_position", "random", "aligned")]))
    elif position in ("nearest", "aligned") and look == "line":
        out.append(_cause("seam_back", "change", 40, "w_seam_back", {},
                          [_change("process", "seam_position", position, "back")]))
    gap = _pct_value(x.proc.get("seam_gap"))
    if gap and look in ("gap", "bump"):
        value, pct = gap
        step = 5.0 if pct else 0.02
        new = value - step if look == "gap" else value + step
        lo, hi = (0.0, 20.0) if pct else (0.0, 0.1)
        if lo <= new <= hi:
            unit = "%" if pct else ""
            out.append(_cause("seam_gap", "change", 65, "w_seam_gap_" + look,
                              {"from": _fmt(value) + unit, "to": _fmt(new) + unit},
                              [_change("process", "seam_gap", _fmt(value) + unit, _fmt(new) + unit)]))
    if look in ("zits", "bump"):
        out.append(_cause("pa_test", "calibration", 55 if "pressure_advance" not in x.measured else 25, "w_pa_seam",
                          test="pa"))
        wipe = _first(x.fil.get("filament_wipe"))
        if wipe not in ("1", "true") and not _on(x.prn.get("wipe")):
            out.append(_cause("wipe", "change", 45, "w_wipe", {},
                              [_change("filament", "filament_wipe", wipe or "nil", "1")]))
    out.append(_cause("seam_paint", "advice", 20, "w_seam_paint"))
    if look == "zits":
        temp = _temp_step(x, -5, "temp_down", 25, "w_temp_ooze")
        if temp:
            out.append(temp)
        out.append(_dry(x, 15))
    return out


def ghosting(x, a):
    out = []
    one, fast = a.get("axis") == "one", a.get("fast") == "yes"
    out.append(_cause("input_shaping", "calibration", 60 + (5 if not one else 0), "w_input_shaping",
                      test="input_shaping"))
    if one:
        out.append(_cause("belts", "advice", 65, "w_belts"))
    accel = _accel_down(x, "outer_wall_acceleration", "outer_accel", 55 + (10 if fast else 0), "w_outer_accel")
    if accel:
        out.append(accel)
    speed = x.p("outer_wall_speed")
    if speed and speed > 80:
        new = max(60.0, round(speed * 0.75))
        out.append(_cause("outer_speed", "change", 35 + (15 if fast else 0), "w_outer_speed",
                          {"from": _fmt(speed), "to": _fmt(new)},
                          [_change("process", "outer_wall_speed", _fmt(speed), _fmt(new))]))
    out.append(_cause("frame", "advice", 25, "w_frame"))
    return out


def top_holes(x, a):
    out = []
    look = a.get("look", "holes")
    layers = x.p("top_shell_layers")
    if layers is not None and layers < 8 and look in ("holes", "lines"):
        new = min(8, int(layers) + 2)
        changes = [_change("process", "top_shell_layers", _fmt(layers), str(new))]
        thick = x.p("top_shell_thickness")
        height = x.p("layer_height") or 0.2
        if thick and thick < new * height:
            changes.append(_change("process", "top_shell_thickness", _fmt(thick), _fmt(round(new * height, 2))))
        out.append(_cause("top_layers", "change", 60 + (10 if layers < 4 else 0), "w_top_layers",
                          {"from": _fmt(layers), "to": str(new)}, changes))
    density = _pct_value(x.proc.get("sparse_infill_density"))
    if density and density[0] < 15 and look in ("holes", "lines"):
        out.append(_cause("infill_density", "change", 45, "w_infill_density", {"from": _fmt(density[0]), "to": "15"},
                          [_change("process", "sparse_infill_density", _fmt(density[0]) + "%", "15%")]))
    fan_max = x.f("fan_max_speed")
    if look == "holes" and x.cls in ("PLA", "SILK", "TPU") and fan_max is not None and fan_max < 90:
        out.append(_cause("top_fan", "change", 50, "w_top_fan", {"from": _fmt(fan_max), "to": "100"},
                          [_change("filament", "fan_max_speed", _fmt(fan_max), "100")]))
    speed = x.p("top_surface_speed")
    if speed and speed > 120 and look in ("holes", "rough"):
        new = max(80.0, round(speed * 0.7))
        out.append(_cause("top_speed", "change", 35, "w_top_speed", {"from": _fmt(speed), "to": _fmt(new)},
                          [_change("process", "top_surface_speed", _fmt(speed), _fmt(new))]))
    if look == "lines":
        pattern = _first(x.proc.get("top_surface_pattern"))
        if pattern and pattern not in ("monotonic", "monotonicline"):
            out.append(_cause("top_pattern", "change", 45, "w_top_pattern", {"from": pattern},
                              [_change("process", "top_surface_pattern", pattern, "monotonic")]))
    if look == "rough":
        out.append(_cause("see_over", "redirect", 55, "w_see_over", {"defect": "over_extrusion"}))
        ironing = _first(x.proc.get("ironing_type"))
        if ironing in ("no ironing", ""):
            out.append(_cause("ironing", "change", 40, "w_ironing", {},
                              [_change("process", "ironing_type", ironing or "no ironing", "top")]))
    out.append(_cause("flow_test", "calibration", 30 if "flow_ratio" not in x.measured else 10, "w_flow_test",
                      test="flow"))
    return out


def wall_gaps(x, a):
    out = []
    where = a.get("where", "infill")
    overlap = _pct_value(x.proc.get("infill_wall_overlap"))
    if overlap and where == "infill":
        value, pct = overlap
        if pct and value < 40:
            new = min(40.0, value + 10)
            out.append(_cause("infill_overlap", "change", 65, "w_infill_overlap",
                              {"from": _fmt(value) + "%", "to": _fmt(new) + "%"},
                              [_change("process", "infill_wall_overlap", _fmt(value) + "%", _fmt(new) + "%")]))
    top = _pct_value(x.proc.get("top_bottom_infill_wall_overlap"))
    if top and where == "top":
        value, pct = top
        if pct and value < 50:
            new = min(50.0, value + 10)
            out.append(_cause("top_overlap", "change", 65, "w_top_overlap",
                              {"from": _fmt(value) + "%", "to": _fmt(new) + "%"},
                              [_change("process", "top_bottom_infill_wall_overlap", _fmt(value) + "%",
                                       _fmt(new) + "%")]))
    if where == "walls":
        out.append(_cause("see_under", "redirect", 55, "w_see_under", {"defect": "under_extrusion"}))
        out.append(_cause("pa_test", "calibration", 45 if "pressure_advance" not in x.measured else 20,
                          "w_pa_walls", test="pa"))
    out.append(_cause("flow_test", "calibration", 50 if "flow_ratio" not in x.measured else 15, "w_flow_test",
                      test="flow"))
    measured = _measured_flow(x)
    if measured:
        out.append(measured)
    speed = x.p("sparse_infill_speed")
    if speed and speed > 200 and where == "infill":
        new = round(speed * 0.8)
        out.append(_cause("infill_speed", "change", 35, "w_infill_speed", {"from": _fmt(speed), "to": _fmt(new)},
                          [_change("process", "sparse_infill_speed", _fmt(speed), _fmt(new))]))
    return out


def curling(x, a):
    out = []
    where = a.get("where", "overhangs")
    if x.needs_enclosure() and where == "corners":
        out.append(_cause("enclosure", "advice", 85, "w_enclosure", {"cls": x.cls}))
    if x.cls in ENCLOSED_MATERIALS:
        if where == "corners":
            out.append(_cause("see_warping", "redirect", 60, "w_see_warping", {"defect": "warping"}))
    else:
        fan = _overhang_fan(x, 55 + (10 if where == "overhangs" else 0), "w_curl_fan")
        if fan:
            out.append(fan)
        fan_max = x.f("fan_max_speed")
        if x.cls in ("PLA", "SILK") and fan_max is not None and fan_max < 100:
            out.append(_cause("fan_up", "change", 50, "w_fan_up", {"from": _fmt(fan_max), "to": "100"},
                              [_change("filament", "fan_max_speed", _fmt(fan_max), "100")]))
    layer = x.f("slow_down_layer_time")
    if layer is not None and layer < 20 and where in ("small", "corners"):
        new = min(20, int(layer) + 4)
        out.append(_cause("layer_time", "change", 60 if where == "small" else 40, "w_layer_time",
                          {"from": _fmt(layer), "to": str(new)},
                          [_change("filament", "slow_down_layer_time", _fmt(layer), str(new))]))
    if where == "overhangs" and _first(x.proc.get("slowdown_for_curled_perimeters")) in ("0", "false"):
        out.append(_cause("curled", "change", 55, "w_curled", {},
                          [_change("process", "slowdown_for_curled_perimeters", "0", "1")]))
    temp = _temp_step(x, -5, "temp_down", 25 + (10 if where == "small" else 0), "w_temp_curl")
    if temp:
        out.append(temp)
    if where == "small":
        out.append(_cause("several", "advice", 30, "w_several"))
    out.append(_cause("geometry", "advice", 15, "w_geometry"))
    return out


def supports(x, a):
    out = []
    problem = a.get("problem", "stuck")
    gap = x.p("support_top_z_distance")
    spacing = x.p("support_interface_spacing")
    if problem == "stuck":
        top = 0.35 if x.cls == "PETG" else 0.3
        if gap is not None and gap + 0.05 <= top:
            step = 0.1 if x.cls == "PETG" and gap < 0.2 else 0.05
            new = round(min(top, gap + step), 2)
            out.append(_cause("support_gap", "change", 65 + (10 if x.cls == "PETG" else 0), "w_support_gap_up",
                              {"from": _fmt(gap, 2), "to": _fmt(new, 2)},
                              [_change("process", "support_top_z_distance", _fmt(gap, 2), _fmt(new, 2))]))
        if spacing is not None and spacing < 0.5:
            new = round(spacing + 0.2, 2)
            out.append(_cause("interface_spacing", "change", 40, "w_interface_spacing_up",
                              {"from": _fmt(spacing, 2), "to": _fmt(new, 2)},
                              [_change("process", "support_interface_spacing", _fmt(spacing, 2), _fmt(new, 2))]))
        if x.cls == "PETG":
            out.append(_cause("support_material", "advice", 45, "w_support_material"))
    elif problem == "underside":
        if gap is not None and gap - 0.05 >= 0.1:
            new = round(gap - 0.05, 2)
            out.append(_cause("support_gap", "change", 60, "w_support_gap_down",
                              {"from": _fmt(gap, 2), "to": _fmt(new, 2)},
                              [_change("process", "support_top_z_distance", _fmt(gap, 2), _fmt(new, 2))]))
        layers = x.p("support_interface_top_layers")
        if layers is not None and layers < 3:
            out.append(_cause("interface_layers", "change", 50, "w_interface_layers",
                              {"from": _fmt(layers), "to": "3"},
                              [_change("process", "support_interface_top_layers", _fmt(layers), "3")]))
        if spacing is not None and spacing > 0.2:
            new = round(max(0.1, spacing - 0.2), 2)
            out.append(_cause("interface_spacing", "change", 45, "w_interface_spacing_down",
                              {"from": _fmt(spacing, 2), "to": _fmt(new, 2)},
                              [_change("process", "support_interface_spacing", _fmt(spacing, 2), _fmt(new, 2))]))
        fan = x.f("support_material_interface_fan_speed")
        if x.cls in ("PLA", "SILK") and fan is not None and fan < 100:
            out.append(_cause("interface_fan", "change", 35, "w_interface_fan", {"to": "100"},
                              [_change("filament", "support_material_interface_fan_speed", _fmt(fan), "100")]))
    else:
        kind = _first(x.proc.get("support_type"))
        if kind.startswith("normal"):
            new = kind.replace("normal", "tree")
            out.append(_cause("tree", "change", 55, "w_tree", {},
                              [_change("process", "support_type", kind, new)]))
        speed = x.p("support_speed")
        if speed and speed > 80:
            new = max(50.0, round(speed * 0.7))
            out.append(_cause("support_speed", "change", 35, "w_support_speed", {"from": _fmt(speed), "to": _fmt(new)},
                              [_change("process", "support_speed", _fmt(speed), _fmt(new))]))
        out.append(_cause("see_first", "redirect", 40, "w_see_first", {"defect": "first_layer"}))
    out.append(_cause("support_settings", "advice", 15, "w_support_settings_" + problem))
    return out


def layer_shift(x, a):
    out = []
    same, one = a.get("when") == "same_height", a.get("axis") == "one"
    if same:
        out.append(_cause("see_curling", "redirect", 55, "w_see_curling", {"defect": "curling"}))
        hop = x.f("filament_z_hop")
        printer_hop = number(x.prn.get("z_hop")) or 0.0
        now = hop if hop is not None else printer_hop
        if now < 0.4:
            out.append(_cause("z_hop", "change", 55, "w_z_hop", {"from": _fmt(now, 2), "to": "0.4"},
                              [_change("filament", "filament_z_hop", _fmt(hop, 2) if hop is not None else "nil",
                                       "0.4")]))
        if _first(x.proc.get("reduce_crossing_wall")) in ("0", "false"):
            out.append(_cause("avoid_walls", "change", 40, "w_avoid_walls", {},
                              [_change("process", "reduce_crossing_wall", "0", "1")]))
    out.append(_cause("belts", "advice", 60 + (10 if one else 0) + (0 if same else 10), "w_shift_belts"))
    accel = _accel_down(x, "default_acceleration", "accel_down", 45 + (0 if same else 10), "w_accel_shift", 0.7)
    if accel:
        out.append(accel)
    travel = _accel_down(x, "travel_acceleration", "travel_accel", 40 + (0 if same else 10), "w_travel_accel", 0.7)
    if travel:
        out.append(travel)
    speed = x.p("travel_speed")
    if speed and speed > 300:
        out.append(_cause("travel_speed", "change", 35, "w_travel_speed", {"from": _fmt(speed), "to": "250"},
                          [_change("process", "travel_speed", _fmt(speed), "250")]))
    if not same:
        out.append(_cause("drivers", "advice", 25, "w_drivers"))
    return out


CAUSES = {"stringing": stringing, "first_layer": first_layer, "layer_split": layer_split,
          "overhangs": overhangs, "warping": warping, "seam": seam, "ghosting": ghosting, "top_holes": top_holes,
          "wall_gaps": wall_gaps, "curling": curling, "supports": supports, "layer_shift": layer_shift}
WITH_INPUTS = {"dimensions": dimensions, "over_extrusion": over_extrusion, "under_extrusion": under_extrusion}


def plan(defect, ctx, answers=None, inputs=None):
    """The causes of a defect, likeliest first: [{cause, kind ("change" | "calibration" | "advice" | "redirect" |
    "need"), score, why, vars, changes: [{target, key, before, after}], test, tried: [outcomes]}].

    A cause already tried without help ("same", "worse") goes to the bottom; one that helped ("better") stays up,
    its next step computed from the values it left."""
    if defect not in DEFECTS:
        raise ValueError("defect_unknown")
    x = Facts(ctx)
    answers = {k: v for k, v in (answers or {}).items()
               if any(q == k and v in opts for q, opts in QUESTIONS[defect])}
    if defect in WITH_INPUTS:
        causes = WITH_INPUTS[defect](x, answers, inputs)
    else:
        causes = CAUSES[defect](x, answers)
    elsewhere = ctx.get("elsewhere") or {}
    for c in causes:
        tried = list(x.tried.get(c["cause"]) or [])
        c["tried"] = tried
        c["elsewhere"] = list(elsewhere.get(c["cause"]) or [])[-3:]
        if tried and tried[-1] in ("same", "worse"):
            c["score"] = LIMIT + c["score"] / 10
        elif tried and tried[-1] == "better":
            c["score"] += 15
        elif c["elsewhere"]:
            # what this fix did on similar filaments (same kind of material, same printer)
            good = sum(1 for e in c["elsewhere"] if e["outcome"] in ("fixed", "better"))
            c["score"] += 12 * good - 6 * (len(c["elsewhere"]) - good)
        c["changes"] = [ch for ch in c["changes"] if ch["before"] != ch["after"]]
        if c["kind"] == "change" and not c["changes"]:
            c["kind"] = "need"
            c["why"] = c["why"] or "d_at_limit"
        c["score"] = round(c["score"], 1)
    causes.sort(key=lambda c: (c["kind"] == "need", -c["score"]))
    return causes


# --- what was tried --------------------------------------------------------------------------------------------
class Attempts:
    """The fixes the user tried, in <FilaForge folder>/defects.json: [{id, at, defect, cause, kind, key, filament,
    process, created, changes, answers, inputs, outcome}], the newest last; outcome None until they say."""

    FILE = "defects.json"
    KEEP = 300

    def __init__(self, folder):
        self.path = os.path.join(folder, self.FILE)

    def all(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        items = data.get("attempts") if isinstance(data, dict) else None
        return [a for a in items if isinstance(a, dict) and a.get("id")] if isinstance(items, list) else []

    def _write(self, items):
        files.write_json(self.path, {"version": 1, "attempts": items[-self.KEEP:]}, ensure_ascii=False, indent=1)

    def add(self, record):
        items = self.all()
        record = dict(record, id="%d-%d" % (int(time.time() * 1000), len(items)), at=int(time.time()), outcome=None)
        items.append(record)
        self._write(items)
        return record

    def get(self, attempt_id):
        return next((a for a in self.all() if a["id"] == attempt_id), None)

    def update(self, attempt_id, **changes):
        items = self.all()
        for a in items:
            if a["id"] == attempt_id:
                a.update(changes)
                self._write(items)
                return a
        return None

    def tried(self, defect, key):
        """{cause: [outcomes, oldest first]} of a defect on one filament (and printer)."""
        out = {}
        for a in self.all():
            if a.get("defect") == defect and a.get("key") == key and a.get("outcome"):
                out.setdefault(a["cause"], []).append(a["outcome"])
        return out

    def pending(self):
        return [a for a in self.all() if not a.get("outcome")]

    def history(self, key=None, limit=100):
        """The attempts, newest first; with key only those of one filament (and printer)."""
        items = [a for a in self.all() if key is None or a.get("key") == key]
        return list(reversed(items))[:limit]

    def elsewhere(self, defect, cls, model, key):
        """{cause: [{label, outcome}]} of a defect on other filaments of the same kind of material on the same
        printer model, oldest first: what helped there is likelier to help here."""
        out = {}
        for a in self.all():
            if a.get("defect") != defect or a.get("key") == key or not a.get("outcome") or a.get("model") != model:
                continue
            ident = a.get("identity") or {}
            if habits.material_class(ident.get("material") or "", ident.get("product") or "") != cls:
                continue
            out.setdefault(a["cause"], []).append({"label": a.get("label") or "", "outcome": a["outcome"]})
        return out
