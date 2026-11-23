"""The plugin in a fake OrcaSlicer: the tab's messages from init to a preset in the bundle.
Run: python3 -m unittest discover -s tests"""

import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

import fake_orca
import helpers


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data, True)
        user_file = os.path.join(self.data, "user", "4242", "process", "My process.json")
        self.orca = fake_orca.install(helpers.orca_presets(), user_file)
        for name in [m for m in sys.modules if m == "filaforge" or m.startswith("filaforge.")]:
            if not name.startswith("filaforge.core") and not name.startswith("filaforge._vendor"):
                del sys.modules[name]
        self.pkg = importlib.import_module("filaforge")
        self.plugin = sys.modules["filaforge.plugin"]
        self.plugin.data_dir = lambda: self.data
        self.plugin.own_dir = lambda: os.path.join(self.data, "filaforge")
        canned = helpers.fetcher()
        self.plugin.fetcher = lambda: canned
        # the work threads run inline here
        self.plugin.spawn = lambda fn, *a: fn(*a)
        plugin_cls = next(c for c in self.orca.registered if getattr(c, "__name__", "") == "FilaForge")
        plugin_cls().register_capabilities()
        page_cls = next(c for c in self.orca.registered if getattr(c, "__name__", "") == "FilaForgePage")
        self.page = page_cls()

    def tearDown(self):
        sys.modules.pop("orca", None)
        sys.modules.pop("orca.pages", None)

    def send(self, **msg):
        before = len(self.page.posted)
        self.page.on_message(msg)
        return self.page.posted[before:]

    def last(self, cmd, posted):
        found = [m for m in posted if m.get("cmd") == cmd]
        errors = [m for m in posted if m.get("cmd") == "error"]
        self.assertTrue(found, "no %s; errors: %s" % (cmd, errors))
        return found[-1]

    def test_registers_and_page(self):
        self.assertIn("FilaForgePage", [c.__name__ for c in self.orca.registered])
        perms = self.orca.permissions[-1]
        self.assertTrue(perms["threading"])
        self.assertIn("https://api.openfilamentdatabase.org", perms["network_http"])
        html = self.page.get_ui()
        self.assertIn('"version": "0.1.0"', html)
        self.assertNotIn("/*BOOT*/null", html)
        self.assertTrue(os.path.exists(self.page.get_icon()))

    def test_flow(self):
        init = self.last("init", self.send(cmd="init"))
        self.assertEqual(init["printer"]["name"], "Creality K1C 0.4 nozzle")
        self.assertEqual(init["printer"]["model"], "Creality K1C")
        self.assertIn("0.20mm Standard @Creality K1C", init["processes"])

        result = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+", colour="#ffffff"))
        self.assertEqual(result["base"]["name"], "SUNLU PLA+ @System")
        self.assertEqual(result["preset_name"], "Sunlu PLA+ FF @Creality K1C 0.4 nozzle")
        settings = [s for s in result["settings"] if s["apply"]]
        self.assertTrue(settings)
        json.dumps(result)

        saved = self.last("saved", self.send(cmd="make_preset", identity=result["identity"], settings=settings,
                                             base=result["base"]["name"], name=result["preset_name"]))
        self.assertEqual(self.orca.reloads, ["filaforge"])
        path = saved["path"]
        self.assertIn(os.path.join("user", "4242", "_local", "filaforge", "filament"), path)
        with open(path, encoding="utf-8") as f:
            preset = json.load(f)
        self.assertEqual(preset["inherits"], "")
        self.assertEqual(preset["compatible_printers"], ["Creality K1C 0.4 nozzle"])
        self.assertEqual(preset["filament_density"], ["1.21"])
        self.assertEqual(preset["filament_type"], ["PLA"])
        self.assertEqual([s["name"] for s in saved["saved"]], ["Sunlu PLA+ FF @Creality K1C 0.4 nozzle"])
        # a name typed without the mark gets it
        again = self.last("saved", self.send(cmd="make_preset", identity=result["identity"], settings=settings,
                                             base=result["base"]["name"], name="Moja Sunlu @Creality K1C 0.4 nozzle"))
        self.assertEqual(again["name"], "Moja Sunlu FF @Creality K1C 0.4 nozzle")
        self.send(cmd="remove_preset", name=again["name"])

        measured = self.last("measured", self.send(cmd="save_measured", identity=result["identity"],
                                                   section="Creality K1C", values={"flow_ratio": "0.97"}))
        self.assertEqual(measured["sections"]["Creality K1C"]["flow_ratio"], 0.97)
        again = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+"))
        row = next(s for s in again["settings"] if s["orca_key"] == "filament_flow_ratio")
        self.assertEqual((row["value"], row["kind"]), ("0.97", "tested"))

        plan = self.last("process_plan", self.send(cmd="process_plan", process="0.20mm Standard @Creality K1C",
                                                   max_volumetric_speed=12, identity=result["identity"]))
        self.assertTrue(plan["changes"])
        self.assertEqual(plan["name"], "0.20mm Standard Sunlu PLA+ FF @Creality K1C")
        saved = self.last("saved", self.send(cmd="process_save", base=plan["base"], name=plan["name"],
                                             changes=plan["changes"]))
        with open(saved["path"], encoding="utf-8") as f:
            proc = json.load(f)
        self.assertEqual(proc["inherits"], "0.20mm Standard @Creality K1C")
        self.assertEqual(len(saved["saved"]), 2)

        removed = self.last("saved", self.send(cmd="remove_preset", name=plan["name"]))
        self.assertEqual(len(removed["saved"]), 1)

    def test_errors_reach_the_page(self):
        err = self.last("error", self.send(cmd="research", brand="", material="PLA"))
        self.assertEqual(err["message"], "brand_missing")
        err = self.last("error", self.send(cmd="settings", spoolman_url="ftp://x"))
        self.assertEqual(err["where"], "settings")

    def test_settings(self):
        reply = self.last("settings", self.send(cmd="settings", spoolman_url="192.168.1.2:7912", language="pl"))
        self.assertEqual(reply["settings"]["spoolman_url"], "192.168.1.2:7912")
        self.assertEqual(self.plugin.read_settings()["language"], "pl")


if __name__ == "__main__":
    unittest.main()
