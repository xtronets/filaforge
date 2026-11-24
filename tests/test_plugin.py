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
        self.assertIn(os.path.join(self.data, "user"), perms["fs_readwrite"])    # where the bundle is written
        html = self.page.get_ui()
        self.assertIn('"version": "0.5.0"', html)
        self.assertNotIn("/*BOOT*/null", html)
        self.assertNotIn("{{img:", html)
        self.assertIn('src="data:image/webp;base64,', html)
        self.assertIn('src="data:image/png;base64,', html)
        self.assertTrue(os.path.exists(self.page.get_icon()))

    def test_flow(self):
        init = self.last("init", self.send(cmd="init"))
        self.assertEqual(init["printer"]["name"], "Creality K1C 0.4 nozzle")
        self.assertEqual(init["printer"]["model"], "Creality K1C")
        self.assertIn("0.20mm Standard @Creality K1C", init["processes"])

        result = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+", colour="#ffffff"))
        self.assertEqual(result["base"]["name"], "SUNLU PLA+ @System")
        self.assertEqual(result["preset_name"], "Sunlu PLA+ FF")
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
        self.assertEqual([s["name"] for s in saved["saved"]], ["Sunlu PLA+ FF"])
        # a name typed without the mark gets it
        again = self.last("saved", self.send(cmd="make_preset", identity=result["identity"], settings=settings,
                                             base=result["base"]["name"], name="Moja Sunlu"))
        self.assertEqual(again["name"], "Moja Sunlu FF")
        self.send(cmd="remove_preset", name=again["name"])

        measured = self.last("measured", self.send(cmd="save_measured", identity=result["identity"],
                                                   section="Creality K1C", values={"flow_ratio": "0.97"}))
        self.assertEqual(measured["sections"]["Creality K1C"]["flow_ratio"], 0.97)
        # max flow for a 0.6 mm nozzle picked on the page: kept for that nozzle, not used on this 0.4 mm printer
        six = self.last("measured", self.send(cmd="save_measured", identity=result["identity"], section="Creality K1C",
                                              values={"flow_ratio": "0.97", "max_volumetric_speed": "30"}, nozzle="0.6"))
        self.assertEqual(six["sections"]["Creality K1C"]["_nozzle"], {"0.6": {"max_volumetric_speed": 30.0}})
        self.assertEqual(six["nozzle"], "0.6")
        again = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+"))
        row = next(s for s in again["settings"] if s["orca_key"] == "filament_flow_ratio")
        self.assertEqual((row["value"], row["kind"]), ("0.97", "tested"))

        plan = self.last("process_plan", self.send(cmd="process_plan", process="0.20mm Standard @Creality K1C",
                                                   max_volumetric_speed=12, identity=result["identity"]))
        self.assertTrue(plan["changes"])
        self.assertEqual(plan["name"], "0.20mm Standard Sunlu PLA+ FF")
        self.assertIn("bridge_acceleration", [c["key"] for c in plan["changes"]])
        small = self.last("process_plan", self.send(cmd="process_plan", process="0.20mm Standard @Creality K1C",
                                                    max_volumetric_speed=12, identity=result["identity"],
                                                    variant="plate"))
        self.assertEqual((small["variant"], small["sizes"]), ("small", [[25, 30], [18, 18]]))
        self.assertEqual(small["name"], "0.20mm Standard Sunlu PLA+ small parts FF")
        saved = self.last("saved", self.send(cmd="process_save", base=plan["base"], name=plan["name"],
                                             changes=plan["changes"]))
        with open(saved["path"], encoding="utf-8") as f:
            proc = json.load(f)
        self.assertEqual(proc["inherits"], "0.20mm Standard @Creality K1C")
        self.assertEqual(len(saved["saved"]), 2)

        removed = self.last("saved", self.send(cmd="remove_preset", name=plan["name"]))
        self.assertEqual(len(removed["saved"]), 1)

    def test_same_name_on_another_printer(self):
        result = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+"))
        args = dict(cmd="make_preset", identity=result["identity"], base=result["base"]["name"],
                    settings=[s for s in result["settings"] if s["apply"]], name=result["preset_name"])
        first = self.last("saved", self.send(**args))
        self.assertEqual((first["name"], first["renamed"]), ("Sunlu PLA+ FF", False))
        again = self.last("saved", self.send(**args))           # same printer: the preset is replaced
        self.assertEqual((again["name"], again["renamed"]), ("Sunlu PLA+ FF", False))
        bundle = self.orca.host.preset_bundle()
        bundle.printers.presets[0].name = "Snapmaker U1 (0.4 nozzle)"
        bundle._full["printer_model"] = '"Snapmaker U1"'
        other = self.last("saved", self.send(**args))
        self.assertEqual((other["name"], other["renamed"]), ("Sunlu PLA+ FF (Snapmaker U1)", True))
        self.assertEqual(len(other["saved"]), 2)

    def test_from_orca(self):
        bundle = self.orca.host.preset_bundle()
        sunlu = bundle.filaments.find_preset("SUNLU PLA+ @System")
        config = dict(sunlu._config, filament_flow_ratio="0.95", default_filament_colour="#1E4FD8")
        bundle.filaments.presets.append(fake_orca.Preset("Sunlu PLA+ Niebieski", config, system=False))
        bundle.filaments.presets.append(fake_orca.Preset("_local/filaforge/Sunlu PLA+ FF", config, system=False))
        items = self.last("orca_filaments", self.send(cmd="orca_filaments"))["items"]
        self.assertEqual([i["label"] for i in items], ["Sunlu PLA+ Niebieski"])   # system and FilaForge's left out
        item = items[0]
        self.assertEqual((item["identity"]["brand"], item["identity"]["product"], item["colour"]),
                         ("SUNLU", "PLA+", "#1E4FD8"))
        result = self.last("result", self.send(cmd="research", orca_preset=item["name"], **item["identity"]))
        self.assertEqual(result["base"], {"name": "Sunlu PLA+ Niebieski", "kind": "own"})
        self.assertEqual(result["preset_name"], "Sunlu PLA+ Niebieski FF")
        saved = self.last("saved", self.send(cmd="make_preset", identity=result["identity"], base=result["base"]["name"],
                                             settings=[s for s in result["settings"] if s["apply"]],
                                             name=result["preset_name"]))
        with open(saved["path"], encoding="utf-8") as f:
            preset = json.load(f)
        self.assertIn(preset["filament_flow_ratio"], ("0.95", ["0.95"]))   # kept from the user's preset
        self.assertEqual(preset["inherits"], "")

    def test_selftest(self):
        # the self-test the real-OrcaSlicer CI job runs, here against the fake host
        selftest = importlib.import_module("filaforge.selftest")
        report = os.path.join(self.data, "selftest.json")
        os.environ[selftest.ENV] = report
        try:
            selftest.WAIT_REPLY = 5
            selftest._run()
        finally:
            del os.environ[selftest.ENV]
        with open(report, encoding="utf-8") as f:
            data = json.load(f)
        failed = [s for s in data["steps"] if not s["ok"]]
        self.assertFalse(failed, json.dumps(failed, indent=1))
        self.assertGreaterEqual(len(data["steps"]), 10)

    def test_process_over_own_process(self):
        # OrcaSlicer loads plugin bundles before user presets: a process over the user's own one must not
        # inherit it, or it is gone after a restart
        result = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+"))
        plan = self.last("process_plan", self.send(cmd="process_plan", process="My process", max_volumetric_speed=12,
                                                   identity=result["identity"]))
        saved = self.last("saved", self.send(cmd="process_save", base=plan["base"], name=plan["name"],
                                             changes=plan["changes"]))
        with open(saved["path"], encoding="utf-8") as f:
            proc = json.load(f)
        self.assertEqual(proc["inherits"], "")
        self.assertEqual(proc["layer_height"], "0.2")          # the user's settings carried over

    def test_repair_old_process(self):
        result = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+"))
        saved = self.last("saved", self.send(cmd="process_save", base="0.20mm Standard @Creality K1C",
                                             name="Old FF", changes=[]))
        with open(saved["path"], encoding="utf-8") as f:
            old = json.load(f)
        old["inherits"] = "My process"                         # as 0.4.2 wrote it
        old["outer_wall_speed"] = "120"
        with open(saved["path"], "w", encoding="utf-8") as f:
            json.dump(old, f)
        self.plugin.Tab._repaired = False
        self.send(cmd="init")
        with open(saved["path"], encoding="utf-8") as f:
            new = json.load(f)
        self.assertEqual((new["inherits"], new["layer_height"], new["outer_wall_speed"]), ("", "0.2", "120"))

    def test_no_descriptor_opens(self):
        # OrcaSlicer's sandbox can't name a file opened by descriptor and asks the user: open by path only
        import ast, glob
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "filaforge")
        for path in glob.glob(os.path.join(src, "**", "*.py"), recursive=True):
            if "_vendor" in path:
                continue
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and n.func.attr in ("mkstemp", "fdopen", "NamedTemporaryFile", "TemporaryFile")]
            self.assertFalse(calls, "%s: %s" % (path, calls))

    def test_products(self):
        reply = self.last("products", self.send(cmd="products", brand="Sunlu"))
        self.assertIn("PLA+", [p["name"] for p in reply["products"]])

    def test_errors_reach_the_page(self):
        err = self.last("error", self.send(cmd="research", brand="", material="PLA"))
        self.assertEqual(err["message"], "brand_missing")
        err = self.last("error", self.send(cmd="settings", spoolman_url="ftp://x"))
        self.assertEqual(err["where"], "settings")

    def test_settings(self):
        reply = self.last("settings", self.send(cmd="settings", spoolman_url="192.168.1.2:7912", language="pl"))
        self.assertEqual(reply["settings"]["spoolman_url"], "192.168.1.2:7912")
        self.assertEqual(self.plugin.read_settings()["language"], "pl")
        reply = self.last("settings", self.send(cmd="settings", habits={"PETG": {"outer": 0.5, "bogus": 1}},
                                                enclosed={"printer": "Creality K1C 0.4 nozzle", "on": True}))
        self.assertEqual(reply["settings"]["habits"], {"PETG": {"outer": 0.5}})
        self.assertEqual(reply["settings"]["enclosed_printers"], ["Creality K1C 0.4 nozzle"])
        reply = self.last("settings", self.send(cmd="settings", enclosed={"printer": "Creality K1C 0.4 nozzle", "on": False}))
        self.assertEqual(reply["settings"]["enclosed_printers"], [])

    def test_habits_reach_the_process(self):
        init = self.last("init", self.send(cmd="init"))
        self.assertIn("PETG", init["habits"]["classes"])
        self.assertEqual(init["habits"]["defaults"]["PETG"]["bridge"], 0.8)
        ident = {"brand": "Sunlu", "material": "PETG"}
        plan = self.last("process_plan", self.send(cmd="process_plan", max_volumetric_speed=30, identity=ident))
        keys = {c["key"]: c["new"] for c in plan["changes"]}
        self.assertEqual((keys["outer_wall_speed"], keys["bridge_speed"]), (140, 20))
        self.send(cmd="settings", habits={"PETG": {"outer": 0.5}})
        plan = self.last("process_plan", self.send(cmd="process_plan", max_volumetric_speed=30, identity=ident))
        self.assertEqual({c["key"]: c["new"] for c in plan["changes"]}["outer_wall_speed"], 100)
        plan = self.last("process_plan", self.send(cmd="process_plan", max_volumetric_speed=30,
                                                   identity={"brand": "Sunlu", "material": "ASA"}))
        self.assertEqual(plan["warnings"][0]["code"], "enclosure")


if __name__ == "__main__":
    unittest.main()
