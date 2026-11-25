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

from . import files
from .review import number

DEFECTS = ("stringing", "first_layer", "layer_split", "dimensions", "over_extrusion", "under_extrusion",
           "overhangs", "warping")

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
}
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


CAUSES = {"stringing": stringing, "first_layer": first_layer, "layer_split": layer_split,
          "overhangs": overhangs, "warping": warping}
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
    for c in causes:
        tried = list(x.tried.get(c["cause"]) or [])
        c["tried"] = tried
        if tried and tried[-1] in ("same", "worse"):
            c["score"] = LIMIT + c["score"] / 10
        elif tried and tried[-1] == "better":
            c["score"] += 15
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
