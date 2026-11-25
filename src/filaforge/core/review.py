"""The preset review: what is wrong or out of date in the user's filament presets, and what changed in OrcaSlicer.

Everything here is plain data in, plain data out: the plugin reads the presets from OrcaSlicer and acts on the
fixes. An issue is {"code", "level": "error"|"warn"|"info", "preset", "kind", "ff", "details", "fixes"}.

What FilaForge wrote into its own filament preset is kept in the bundle state ("written": {orca key: value} of the
values the user can measure). A value that differs from it in OrcaSlicer is a change the user made there by hand
("orca_changed"); FilaForge offers to keep it as their measurement, so the next preset it makes has it too. A
preset the user saved from FilaForge's ("filament_id" FilaForge's, or inheriting it) is compared the same way.
"""

import hashlib
import json
import math

# OrcaSlicer filament keys the user tunes by hand, and the measurement each one is
SYNC_KEYS = {
    "nozzle_temperature": "nozzle_temp", "hot_plate_temp": "bed_temp", "filament_flow_ratio": "flow_ratio",
    "pressure_advance": "pressure_advance", "filament_max_volumetric_speed": "max_volumetric_speed",
    "filament_retraction_length": "retraction_length", "filament_retraction_speed": "retraction_speed",
    "fan_min_speed": "fan_min", "fan_max_speed": "fan_max", "chamber_temperature": "chamber_temp",
}
# a measurement goes into these keys of a preset
APPLY_KEYS = {"nozzle_temp": ("nozzle_temperature",), "bed_temp": ("hot_plate_temp", "hot_plate_temp_initial_layer"),
              "flow_ratio": ("filament_flow_ratio",), "pressure_advance": ("pressure_advance",),
              "max_volumetric_speed": ("filament_max_volumetric_speed",),
              "retraction_length": ("filament_retraction_length",),
              "retraction_speed": ("filament_retraction_speed",), "fan_min": ("fan_min_speed",),
              "fan_max": ("fan_max_speed",), "chamber_temp": ("chamber_temperature",)}
# settings that make two presets the same filament for the same printer
_SAME_SKIP = {"name", "inherits", "filament_settings_id", "filament_id", "setting_id", "version", "from",
              "instantiation", "description", "is_custom_defined", "renamed_from", "filament_notes", "updated_time",
              "user_id", "base_id"}
LEVELS = {"error": 0, "warn": 1, "info": 2}


def number(text):
    """The first number of an OrcaSlicer value ("0.04", "0.04,0.04", "95%") or None."""
    if text is None:
        return None
    if isinstance(text, (list, tuple)):
        text = text[0] if text else ""
    head = str(text).split(";")[0].split(",")[0].strip().strip('"').rstrip("%")
    if head in ("", "nil"):
        return None
    try:
        value = float(head)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _same(a, b):
    return a is not None and b is not None and abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


def tuned(config):
    """{orca key: number} of the hand-tuned values a preset holds."""
    out = {}
    for key in SYNC_KEYS:
        value = number(config.get(key))
        if value is not None:
            out[key] = value
    return out


def changed(before, now):
    """[{orca_key, key, was, now}] of the tuned values that differ."""
    out = []
    for key, measure in SYNC_KEYS.items():
        a, b = before.get(key), now.get(key)
        if a is not None and b is not None and not _same(a, b):
            out.append({"orca_key": key, "key": measure, "was": a, "now": b})
    return out


def config_hash(config):
    """A short hash of a preset's settings, to notice when OrcaSlicer updated a base preset."""
    data = {k: v for k, v in (config or {}).items() if k not in _SAME_SKIP}
    return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _issue(code, level, preset, details=None, fixes=(), ff=False, kind="filament"):
    return {"code": code, "level": level, "preset": preset["name"], "label": preset.get("label") or preset["name"],
            "kind": kind, "ff": ff, "details": details or {}, "fixes": list(fixes)}


def basic(preset, ff=False):
    """The checks any filament preset gets."""
    cfg = preset["config"]
    out = []
    fix = ["research"]
    mvs = number(cfg.get("filament_max_volumetric_speed"))
    if not mvs:
        out.append(_issue("no_mvs", "warn", preset, {}, fix, ff))
    if number(cfg.get("enable_pressure_advance")) == 1 and number(cfg.get("pressure_advance")) == 0:
        out.append(_issue("pa_zero", "warn", preset, {}, fix, ff))
    temp = number(cfg.get("nozzle_temperature"))
    lo, hi = number(cfg.get("nozzle_temperature_range_low")), number(cfg.get("nozzle_temperature_range_high"))
    if temp and lo and hi and lo < hi and not lo <= temp <= hi:
        out.append(_issue("temp_range", "warn", preset, {"temp": temp, "low": lo, "high": hi}, fix, ff))
    fmin, fmax = number(cfg.get("fan_min_speed")), number(cfg.get("fan_max_speed"))
    if fmin is not None and fmax is not None and fmin > fmax:
        out.append(_issue("fan_order", "warn", preset, {"min": fmin, "max": fmax}, fix, ff))
    return out


def duplicates(presets):
    """Issues for presets with exactly the same settings for the same printers (one issue per group)."""
    groups = {}
    for p in presets:
        groups.setdefault(config_hash(p["config"]), []).append(p)
    out = []
    for group in groups.values():
        if len(group) < 2:
            continue
        names = [p["name"] for p in group]
        for p in group:
            others = [n for n in names if n != p["name"]]
            fixes = ["remove"] if p.get("ff") else []
            out.append(_issue("duplicate", "info", p, {"others": others}, fixes, bool(p.get("ff"))))
    return out


def run(ff_items, user_presets):
    """All issues, most serious first.

    ff_items: FilaForge's presets, each {name, label, kind, exists, loaded, config (as OrcaSlicer has it, {} when not
    loaded), meta (the bundle state entry), base_hash_now (the base preset's hash now, None when it is gone,
    "" when unknown), measured ({orca key: number} the user's measurement says for its printer and nozzle),
    derived ([{name, config}] the user's presets saved from it)}.
    user_presets: the user's other filament presets, {name, label, config, parent_missing}."""
    issues = []
    for item in ff_items:
        meta = item.get("meta") or {}
        kind = item.get("kind", "filament")
        if not item.get("exists", True):
            issues.append(_issue("file_missing", "error", item, {}, ["forget"], True, kind))
            continue
        if not item.get("loaded", True):
            fixes = ["repair", "remove"] if kind == "process" else ["remove"]
            issues.append(_issue("not_loaded", "error", item, {}, fixes, True, kind))
            continue
        if kind != "filament":
            continue
        cfg = item.get("config") or {}
        now = tuned(cfg)
        written = meta.get("written")
        pending = set()
        if isinstance(written, dict):
            diff = changed(written, now)
            if diff:
                pending = {d["orca_key"] for d in diff}
                issues.append(_issue("orca_changed", "warn", item, {"changes": diff, "source": item["name"]},
                                     ["to_measured", "keep"], True))
            for child in item.get("derived") or []:
                seen = (meta.get("ack") or {}).get(child["name"]) or written
                diff = changed(seen, tuned(child["config"]))
                if diff:
                    issues.append(_issue("orca_changed", "warn", item, {"changes": diff, "source": child["name"]},
                                         ["to_measured", "keep"], True))
        measured = item.get("measured") or {}
        stale = [{"orca_key": k, "key": SYNC_KEYS[k], "was": now[k], "now": v} for k, v in measured.items()
                 if k in now and k not in pending and not _same(now[k], v)]
        if stale:
            issues.append(_issue("measured_newer", "warn", item, {"changes": stale}, ["apply_measured"], True))
        base_now = item.get("base_hash_now")
        if meta.get("base") and meta.get("base_hash"):
            if base_now is None:
                issues.append(_issue("base_missing", "info", item, {"base": meta["base"]}, [], True))
            elif base_now and base_now != meta["base_hash"] and meta.get("set_keys"):
                issues.append(_issue("base_updated", "info", item, {"base": meta["base"]}, ["rebase"], True))
        issues += basic(dict(item, config=cfg), ff=True)
    for p in user_presets:
        if p.get("parent_missing"):
            issues.append(_issue("parent_missing", "error", p, {"parent": p.get("parent", "")}, ["research"]))
        issues += basic(p)
    loaded_ff = [dict(i, ff=True) for i in ff_items if i.get("kind", "filament") == "filament" and i.get("config")]
    issues += duplicates(loaded_ff + [dict(p, ff=False) for p in user_presets])
    issues.sort(key=lambda i: (LEVELS.get(i["level"], 3), i["label"].lower(), i["code"]))
    return issues


def apply_values(preset, updates):
    """The preset (dict as written) with {orca key: number} set, each list keeping its length."""
    out = dict(preset)
    for key, value in updates.items():
        text = ("%g" % value) if isinstance(value, (int, float)) else str(value)
        old = out.get(key)
        out[key] = [text] * len(old) if isinstance(old, list) and old else [text]
        if key == "pressure_advance":
            old = out.get("enable_pressure_advance")
            out["enable_pressure_advance"] = ["1"] * (len(old) if isinstance(old, list) and old else 1)
    return out


def measured_updates(measured):
    """{orca key: number} a measurement ({key: number}) sets in a preset."""
    out = {}
    for key, value in (measured or {}).items():
        for orca_key in APPLY_KEYS.get(key, ()):
            out[orca_key] = value
    return out
