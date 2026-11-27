"""Cooling for the kind of print: OrcaSlicer keeps every fan setting in the filament preset, so a print that wants
other cooling needs its own preset of the filament ("Sunlu PLA+ FF strength").

standard: the values as they are. detail: more fan for overhangs, bridges and small parts, a longer shortest layer
time. strength: less fan, so the layers stay warm and bond (functional parts). Materials that warp keep a low
ceiling either way: more fan on ABS cracks the layers apart.
"""

MODES = ("standard", "detail", "strength")
# materials that warp: the most fan "detail" gives them
WARPING = ("ABS", "ASA", "PC", "PA")
WARPING_CAP = 50

FAN_KEYS = ("fan_min_speed", "fan_max_speed", "overhang_fan_speed", "additional_cooling_fan_speed")


def _number(text):
    try:
        value = float(str(text).split(",")[0].strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    return value if value == value else None


def _pct(value):
    return str(int(max(0, min(100, round(value)))))


def apply(values, mode, cls, base=None):
    """{key: text} with the cooling of mode over values; a key values lacks is read from base (the parent preset,
    {key: OrcaSlicer text}). Returns the changed keys with their new text; values itself is not changed."""
    if mode not in MODES or mode == "standard":
        return {}
    base = base or {}

    def now(key):
        return _number(values[key]) if key in values else _number(base.get(key))
    out = {}
    cap = WARPING_CAP if cls in WARPING else 100
    fan_min, fan_max = now("fan_min_speed"), now("fan_max_speed")
    bridge, aux, layer = now("overhang_fan_speed"), now("additional_cooling_fan_speed"), now("slow_down_layer_time")
    if mode == "detail":
        if fan_max is not None:
            new_max = max(fan_max, min(cap, fan_max + 20))
            out["fan_max_speed"] = _pct(new_max)
            if fan_min is not None and fan_min > new_max:
                out["fan_min_speed"] = _pct(new_max)
        if bridge is not None:
            out["overhang_fan_speed"] = _pct(max(bridge, min(cap if cls in WARPING else 100, bridge + 20)))
        if aux is not None and aux > 0 and cls not in WARPING:
            out["additional_cooling_fan_speed"] = _pct(min(100, aux + 20))
        if layer is not None:
            out["slow_down_layer_time"] = str(int(round(layer + 4)))
    else:
        if fan_min is not None:
            out["fan_min_speed"] = _pct(fan_min * 0.6)
        if fan_max is not None:
            out["fan_max_speed"] = _pct(fan_max * 0.6)
        if aux is not None:
            out["additional_cooling_fan_speed"] = _pct(aux * 0.5)
        if layer is not None and layer > 4:
            out["slow_down_layer_time"] = str(int(round(max(4, layer - 2))))
    return {k: v for k, v in out.items() if _number(values.get(k, base.get(k))) != _number(v)}
