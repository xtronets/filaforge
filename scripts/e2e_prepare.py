"""Prepare an OrcaSlicer data folder for the real-OrcaSlicer test: a finished first-run setup with a Creality K1C,
and FilaForge installed, enabled and allowed everything (no permission dialog).

    python scripts/e2e_prepare.py DATA_DIR dist/filaforge_any.whl ORCA_VERSION
"""

import json
import os
import shutil
import sys


def main(data_dir, wheel, orca_version):
    os.makedirs(data_dir, exist_ok=True)
    conf = {
        "header": "OrcaSlicer %s" % orca_version,
        "app": {
            "language": "en_US", "version": orca_version, "region": "Europe", "privacy_version": "00.00.00.00",
            "show_splash_screen": False, "show_daily_tips": False, "show_home_page": False, "show_hints": False,
            "preset_bundle_auto_update": False, "check_stable_update_only": True, "sync_user_preset": False,
            "sync_system_preset": False, "installed_networking": False, "hide_plugin_permission_tip": "1",
            "single_instance": False, "user_mode": "expert", "units": "0", "use_inches": "0",
        },
        "firstguide": {"finish": True},
        "models": [{"vendor": "Creality", "model": "Creality K1C", "nozzle_diameter": "0.4"}],
        "filaments": ["Generic PLA @System"],
        "presets": {"machine": "Creality K1C 0.4 nozzle", "print": "0.20mm Standard @Creality K1C",
                    "filament": "Generic PLA @System"},
    }
    with open(os.path.join(data_dir, "OrcaSlicer.conf"), "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=1)

    plugin = os.path.join(data_dir, "orca_plugins", "filaforge_any.whl")
    os.makedirs(plugin, exist_ok=True)
    shutil.copy(wheel, os.path.join(plugin, "filaforge_any.whl"))
    version = _version(wheel)
    state = {"capabilities": [{"FilaForge": True}], "enabled": True, "installed_from": "local",
             "installed_version": version, "plugin_name": "FilaForge",
             "permissions": {"events": [], "fs_read": [], "fs_readwrite": [], "full_access": True,
                             "network_http": [], "network_socket": [], "process": [], "threading": True}}
    with open(os.path.join(plugin, ".install_state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    print("prepared %s: FilaForge %s, OrcaSlicer %s" % (data_dir, version, orca_version))


def _version(wheel):
    import zipfile
    with zipfile.ZipFile(wheel) as z:
        meta = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        for line in z.read(meta).decode("utf-8").splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    return "0"


if __name__ == "__main__":
    main(*sys.argv[1:4])
