"""A stand-in for OrcaSlicer's `orca` module: just what FilaForge uses, with presets from the fixtures."""

import sys
import json
import os
import types


INT_KEYS = ("nozzle_temperature", "hot_plate_temp", "overhang_fan_speed", "chamber_temperature")


class Preset:
    def __init__(self, name, config, system=True, compatible=True, file="", visible=True):
        self.name = name
        self._config = dict(config)
        self.is_system = system
        self.is_compatible = compatible
        self.is_visible = visible
        self.file = file

    def is_user(self):
        return not self.is_system

    def config_keys(self):
        return list(self._config)

    def config_value(self, key):
        return self._config.get(key)


class Collection:
    def __init__(self, presets, selected=0):
        self.presets = presets
        self.selected = selected

    def size(self):
        return len(self.presets)

    def preset(self, i):
        return self.presets[i]

    def get_selected_preset(self):
        return self.presets[self.selected] if self.presets else None

    def find_preset(self, name):
        return next((p for p in self.presets if p.name == name), None)


class Bundle:
    def __init__(self, filaments, prints, printers, full):
        self.filaments, self.prints, self.printers = filaments, prints, printers
        self._full = full

    def full_config_value(self, key):
        return self._full.get(key)


def install(filament_presets, user_file=""):
    """Put a fake `orca` into sys.modules; returns it (orca.reloads lists reload_local calls)."""
    orca = types.ModuleType("orca")
    pages = types.ModuleType("orca.pages")

    class PagesPluginCapabilityBase:
        def __init__(self):
            self.posted = []

        def post_message(self, data):
            self.posted.append(data)

    pages.PagesPluginCapabilityBase = PagesPluginCapabilityBase
    orca.pages = pages
    orca.PluginType = types.SimpleNamespace(Pages="pages")
    orca.LifecycleEvent = types.SimpleNamespace(GCodeExportStarted="GCodeExportStarted",
                                                GCodeExportFinished="GCodeExportFinished")
    orca.LifecycleEvtCode = types.SimpleNamespace(Ok="Ok", Error="Error", Warn="Warn")
    orca.registered = []
    orca.plugin = lambda cls: orca.registered.append(cls) or cls

    class Base:
        pass
    orca.base = Base
    orca.register_capability = lambda cls: orca.registered.append(cls)
    orca.permissions = []
    orca.request_permissions = lambda **kw: orca.permissions.append(kw)
    orca.reloads = []
    orca.refuse = "\x00never"

    def reload_local(bundle_id):
        # like OrcaSlicer: the bundle's presets as "_local/<bundle>/<name>", read again from its folder
        orca.reloads.append(bundle_id)
        if not user_file:
            return
        root = os.path.join(os.path.dirname(os.path.dirname(user_file)), "_local", bundle_id)
        prefix = "_local/%s/" % bundle_id
        loaded = []
        for kind, col in (("filament", bundle.filaments), ("process", bundle.prints)):
            folder = os.path.join(root, kind)
            for f in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
                if f.endswith(".json"):
                    with open(os.path.join(folder, f), encoding="utf-8") as fh:
                        data = json.load(fh)
                    config = {k: ",".join(v) if isinstance(v, list) else str(v) for k, v in data.items()}
                    parent = data.get("inherits") or ""
                    # like OrcaSlicer: a whole number with a fraction, a refused value or a parent it does not
                    # have makes it refuse the whole bundle (and keep the presets it had)
                    if any("." in str(config.get(k, "")) for k in INT_KEYS) or \
                            any(str(v) == orca.refuse for v in config.values()) or \
                            (parent and col.find_preset(parent) is None):
                        raise RuntimeError("Local bundle contains an invalid preset")
                    loaded.append((col, Preset(prefix + data.get("name", f[:-5]), config, system=False,
                                               file=os.path.join(folder, f))))
        for col in (bundle.filaments, bundle.prints):
            col.presets = [p for p in col.presets if not p.name.startswith(prefix)]
        for col, preset in loaded:
            col.presets.append(preset)
    filaments = Collection([Preset(p["name"], dict(p["config"], filament_vendor='"%s"' % p["vendor"],
                                                      filament_type='"%s"' % p["type"]),
                                   compatible=p["compatible"]) for p in filament_presets])
    process = Preset("0.20mm Standard @Creality K1C", {
        "layer_height": "0.2", "initial_layer_print_height": "0.2", "line_width": "0.42", "outer_wall_line_width": "0.42",
        "inner_wall_line_width": "0.45", "sparse_infill_line_width": "0.45", "outer_wall_speed": "200",
        "inner_wall_speed": "300", "sparse_infill_speed": "270", "top_surface_speed": "200", "bridge_speed": "25",
        "internal_bridge_speed": "150%", "outer_wall_acceleration": "3000", "bridge_acceleration": "50%",
        "default_acceleration": "10000"})
    user = Preset("My process", {"layer_height": "0.2"}, system=False, file=user_file)
    printers = Collection([Preset("Creality K1C 0.4 nozzle", {"printer_model": "Creality K1C"})])
    bundle = Bundle(filaments, Collection([process, user]), printers,
                    {"nozzle_diameter": "0.4", "printer_model": '"Creality K1C"',
                     "machine_max_acceleration_extruding": "20000,20000", "machine_max_acceleration_travel": "20000,20000",
                     "machine_max_speed_x": "600,600", "machine_max_speed_y": "600,600"})
    box = lambda x, y: types.SimpleNamespace(bounding_box=lambda: types.SimpleNamespace(defined=True, size=[x, y, 10]))
    orca.plate = [box(25, 30), box(18, 18)]
    model = types.SimpleNamespace(objects=lambda: list(orca.plate))
    orca.host = types.SimpleNamespace(preset_bundle=lambda: bundle, model=lambda: model,
                                      preset=types.SimpleNamespace(bundles=types.SimpleNamespace(
                                          reload_local=reload_local, list_ids=lambda: ["filaforge"])))
    sys.modules["orca"] = orca
    sys.modules["orca.pages"] = pages
    return orca
