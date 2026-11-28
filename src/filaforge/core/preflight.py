"""Checks a sliced print against what you measured for the filament, before it is printed.

Every finding carries the numbers it rests on: what the G-code does (gcode.Result) and the limit it is held to
(your measurement, the preset, the spool). A finding with a fix names the change in the same shape the defects
plan uses, so the page applies it the same way (one change, into an FF preset, kept in the history).

Levels: "stop" (the print will fail or run out), "warn" (likely to show), "info" (worth knowing).
"""

WARPING = ("ABS", "ASA", "PC", "PA")
# features whose flow the hotend has to keep up with (skirt, ironing and the prime tower don't count)
FLOW_ROLES = ("outer_wall", "inner_wall", "overhang", "sparse_infill", "solid_infill", "top_surface",
              "bottom_surface", "bridge", "internal_bridge", "gap_infill", "support", "support_interface", "brim")
# share of print time above a limit that is worth a finding, and that makes it a warning
SHARE_INFO, SHARE_WARN = 0.01, 0.05
NEAR_LIMIT = 0.9              # flow within 10% of the preset's max: the hotend runs at its edge
RETRACTIONS_PER_LAYER = {"PETG": 40, "TPU": 25, "PA": 40}
BRIM_AREA_MM2 = 2500.0        # a first layer larger than 5 × 5 cm of a warping material


def _f(value, digits=1):
    text = ("%." + str(digits) + "f") % value
    return text.rstrip("0").rstrip(".") if "." in text else text


def _finding(code, level, vars=None, changes=None, test=None):
    out = {"code": code, "level": level, "vars": vars or {}}
    if changes:
        out["changes"] = changes
    if test:
        out["test"] = test
    return out


def _flow_time_above(result, limit):
    """{role: seconds above limit}, the roles that count, and the total."""
    per = {}
    for role in FLOW_ROLES:
        seconds = result.role(role).time_above(limit)
        if seconds > 0:
            per[role] = seconds
    return per, sum(per.values())


def check(result, ctx):
    """Findings for a gcode.Result. ctx: {cls, measured_mvs (mm³/s, for this nozzle) or None, extruder (the one
    the checked filament prints from, 0 by default), spool_left_g or None, spool_label}. Flow peaks are the 99th
    percentile: a single short segment's rounding is no peak."""
    out = []
    cls = ctx.get("cls") or ""
    total = result.total_time() or 0.0
    printing = sum(result.role(r).time for r in FLOW_ROLES) or 0.0
    extruder = int(ctx.get("extruder") or 0)
    preset_mvs = result.number("filament_max_volumetric_speed", extruder)
    measured = ctx.get("measured_mvs")
    peak = max((result.role(r).percentile(0.99) for r in FLOW_ROLES), default=0.0)

    # 1. the hotend asked for more than this filament melts (your max flow test)
    if measured and printing:
        per, above = _flow_time_above(result, measured)
        share = above / printing
        if share >= SHARE_INFO:
            worst = max(per, key=per.get)
            changes = []
            if preset_mvs and preset_mvs > measured:
                changes = [{"target": "filament", "key": "filament_max_volumetric_speed", "before": _f(preset_mvs),
                            "after": _f(measured)}]
            out.append(_finding("flow_over_measured", "warn" if share >= SHARE_WARN else "info",
                                {"share": _f(share * 100), "measured": _f(measured), "peak": _f(peak),
                                 "role": worst, "preset": _f(preset_mvs) if preset_mvs else ""}, changes))
    # 2. no measurement and the print runs at the preset's limit: a max flow test is worth it
    elif preset_mvs and printing:
        per, above = _flow_time_above(result, preset_mvs * NEAR_LIMIT)
        share = above / printing
        if share >= SHARE_WARN:
            out.append(_finding("flow_at_limit", "info", {"share": _f(share * 100), "preset": _f(preset_mvs),
                                                          "peak": _f(peak)}, test="mvs"))

    # 3. the spool has less left than the print needs
    mm, grams = result.filament_used()
    _, own_grams = result.filament_used(extruder)
    left = ctx.get("spool_left_g")
    total_grams = grams
    grams = own_grams if own_grams is not None else grams
    if grams and left is not None:
        if grams > left:
            out.append(_finding("spool_short", "stop", {"need": _f(grams), "left": _f(left),
                                                        "spool": ctx.get("spool_label") or ""}))
        elif grams > left * 0.9:
            out.append(_finding("spool_tight", "warn", {"need": _f(grams), "left": _f(left),
                                                        "spool": ctx.get("spool_label") or ""}))

    # 4. many retractions on a material that strings or grinds
    limit = RETRACTIONS_PER_LAYER.get(cls)
    if limit and result.layers:
        busy = [lay for lay in result.layers if lay.retractions > limit]
        if len(busy) >= max(3, len(result.layers) * 0.1):
            worst = max(busy, key=lambda lay: lay.retractions)
            changes = []
            if (result.setting("reduce_crossing_wall") or "0") in ("0", "false"):
                changes = [{"target": "process", "key": "reduce_crossing_wall", "before": "0", "after": "1"}]
            out.append(_finding("retractions", "warn", {"layers": str(len(busy)), "max": str(worst.retractions),
                                                        "z": _f(worst.z or 0, 2), "cls": cls, "limit": str(limit)},
                                changes))

    # 5. a large first layer of a warping material with no brim
    if cls in WARPING and result.layers:
        first = result.layers[0]
        height = first.height or result.number("initial_layer_print_height") or 0.2
        area = first.volume / height if height else 0.0
        brim = (result.setting("brim_type") or "").lower()
        if area >= BRIM_AREA_MM2 and brim in ("no_brim", "none", ""):
            out.append(_finding("warp_brim", "warn", {"area": _f(area / 100), "cls": cls},
                                [{"target": "process", "key": "brim_type", "before": brim or "no_brim",
                                  "after": "outer_only"}]))

    order = {"stop": 0, "warn": 1, "info": 2}
    out.sort(key=lambda f: order[f["level"]])
    return {"findings": out, "time": total, "filament_g": total_grams, "filament_mm": mm, "peak_flow": round(peak, 2),
            "preset_mvs": preset_mvs, "measured_mvs": measured, "layers": len(result.layers),
            "roles": {r: result.role(r).as_dict() for r in FLOW_ROLES if result.role(r).time > 0}}
