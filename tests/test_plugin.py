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
        self.assertIn('"version": "0.7.8"', html)
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

    def make(self, result, settings=None):
        settings = settings if settings is not None else [x for x in result["settings"] if x["apply"]]
        return self.send(cmd="make_preset", identity=result["identity"], settings=settings,
                         base=result["base"]["name"], name=result["preset_name"])

    def test_fractional_whole_numbers(self):
        # "212.5" in a whole-number key made OrcaSlicer refuse the bundle
        result = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+"))
        settings = [dict(x) for x in result["settings"] if x["apply"]]
        settings += [{"orca_key": "nozzle_temperature", "value": "212.5"},
                     {"orca_key": "overhang_fan_speed", "value": "85.4"},
                     {"orca_key": "enable_pressure_advance", "value": "true"}]
        saved = self.last("saved", self.make(result, settings))
        with open(saved["path"], encoding="utf-8") as f:
            preset = json.load(f)
        self.assertEqual(preset["nozzle_temperature"][0], "213")
        self.assertEqual(preset["overhang_fan_speed"][0], "85")
        self.assertEqual(saved["left_out"], [])

    def test_refused_value_left_out(self):
        result = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+"))
        settings = [dict(x) for x in result["settings"] if x["apply"]]
        self.orca.refuse = "1.21"                              # the density OrcaSlicer "refuses"
        saved = self.last("saved", self.make(result, settings))
        self.assertEqual(saved["left_out"], ["filament_density"])
        with open(saved["path"], encoding="utf-8") as f:
            preset = json.load(f)
        self.assertNotEqual(preset["filament_density"], ["1.21"])
        self.assertIn("filament_flow_ratio" if "filament_flow_ratio" in preset else "nozzle_temperature", preset)

    def test_cooling_mode(self):
        result = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+"))
        settings = [dict(x) for x in result["settings"] if x["apply"]]
        saved = self.last("saved", self.send(cmd="make_preset", identity=result["identity"], settings=settings,
                                             base=result["base"]["name"], name="Sunlu PLA+ strength",
                                             cooling="strength"))
        with open(saved["path"], encoding="utf-8") as f:
            preset = json.load(f)
        self.assertTrue(saved["cooling"])
        self.assertEqual(preset["fan_max_speed"][0], saved["cooling"]["fan_max_speed"])
        self.assertLess(float(preset["fan_max_speed"][0]), 100)

    def test_broken_process_set_aside(self):
        # a process whose parent OrcaSlicer no longer has blocked every later save
        result = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+"))
        proc = self.last("saved", self.send(cmd="process_save", base="0.20mm Standard @Creality K1C",
                                            name="Gone FF", changes=[]))
        with open(proc["path"], encoding="utf-8") as f:
            data = json.load(f)
        data["inherits"] = "0.20mm Renamed @Creality K1C"
        with open(proc["path"], "w", encoding="utf-8") as f:
            json.dump(data, f)
        saved = self.last("saved", self.make(result))
        self.assertTrue(os.path.exists(saved["path"]))
        self.assertEqual(saved["set_aside"], [{"name": "Gone FF", "how": "fixed"}])
        with open(proc["path"], encoding="utf-8") as f:
            self.assertEqual(json.load(f)["inherits"], "")
        hist = self.last("history", self.send(cmd="history"))
        self.assertIn("Gone FF", json.dumps(hist))

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

    # -- review, sync, history ---------------------------------------------------------------------------------------
    def make_sunlu(self):
        self.send(cmd="init")
        result = self.last("result", self.send(cmd="research", brand="Sunlu", material="PLA+"))
        settings = [x for x in result["settings"] if x["apply"]]
        saved = self.last("saved", self.send(cmd="make_preset", identity=result["identity"], settings=settings,
                                             base=result["base"]["name"], name=result["preset_name"],
                                             spoolman_id=7))
        return result, saved

    def loaded(self, name):
        return self.orca.host.preset_bundle().filaments.find_preset("_local/filaforge/" + name)

    def issues(self, **filters):
        found = self.last("review", self.send(cmd="review"))["issues"]
        return [i for i in found if all(i.get(k) == v for k, v in filters.items())]

    def test_review_sync_from_orca(self):
        result, saved = self.make_sunlu()
        name = saved["name"]
        self.assertEqual(self.issues(preset=name, code="orca_changed"), [])
        info = next(x for x in saved["saved"] if x["name"] == name)
        self.assertEqual((info["base"], info["spoolman_id"]), ("SUNLU PLA+ @System", 7))
        self.assertEqual(info["written"]["filament_max_volumetric_speed"], 12.0)
        # the user tunes max flow in OrcaSlicer, in FilaForge's preset itself
        self.loaded(name)._config["filament_max_volumetric_speed"] = "14,14"
        issue = self.issues(preset=name, code="orca_changed")[0]
        self.assertEqual([(c["orca_key"], c["now"]) for c in issue["details"]["changes"]], [("filament_max_volumetric_speed", 14.0)])
        self.assertEqual(issue["identity"]["brand"], "Sunlu")
        done = self.last("review", self.send(cmd="review_fix", fix="to_measured", code="orca_changed", preset=name,
                                             source=name))
        self.assertEqual(done["note"], "measured")
        self.assertFalse([i for i in done["issues"] if i["code"] == "orca_changed"])
        shelf = self.plugin.store()
        ident = self.plugin.research.identity_from(result["identity"])
        self.assertEqual(shelf.get(ident, "Creality K1C", 0.4)["max_volumetric_speed"], 14.0)
        self.assertNotIn("max_volumetric_speed", shelf.get(ident, "Creality K1C", 0.6))     # kept for the 0.4 mm nozzle
        # ...or in a preset they saved from it (same filament_id): "leave it" stops asking
        fid = self.loaded(name).config_value("filament_id")
        child = fake_orca.Preset("Sunlu PLA+ mine", dict(self.loaded(name)._config, nozzle_temperature="225",
                                                         filament_id=fid), system=False)
        self.orca.host.preset_bundle().filaments.presets.append(child)
        issue = self.issues(code="orca_changed", preset=name)[0]
        self.assertEqual(issue["details"]["source"], "Sunlu PLA+ mine")
        self.send(cmd="review_fix", fix="keep", code="orca_changed", preset=name, source="Sunlu PLA+ mine")
        self.assertEqual(self.issues(code="orca_changed"), [])

    def test_review_measured_and_history(self):
        result, saved = self.make_sunlu()
        name = saved["name"]
        self.send(cmd="save_measured", identity=result["identity"], section="Creality K1C",
                  values={"flow_ratio": "0.93"})
        issue = self.issues(preset=name, code="measured_newer")[0]
        self.assertEqual(issue["details"]["changes"][0]["orca_key"], "filament_flow_ratio")
        self.send(cmd="review_fix", fix="apply_measured", code="measured_newer", preset=name, source=name)
        with open(saved["path"], encoding="utf-8") as f:
            self.assertEqual(json.load(f)["filament_flow_ratio"], ["0.93"])
        self.assertEqual(self.issues(preset=name, code="measured_newer"), [])
        # the version before is in the history: see what differs, restore it, and the restore is undoable
        entries = self.last("history", self.send(cmd="history"))["entries"]
        self.assertEqual([(e["name"], e["reason"], e["current"]) for e in entries], [(name, "measured", True)])
        diff = self.last("history_diff", self.send(cmd="history_diff", id=entries[0]["id"]))
        self.assertIn("filament_flow_ratio", [c["key"] for c in diff["changes"]])
        back = self.last("saved", self.send(cmd="history_restore", id=entries[0]["id"]))
        self.assertEqual(back["kind"], "restored")
        with open(saved["path"], encoding="utf-8") as f:
            self.assertNotEqual(json.load(f)["filament_flow_ratio"], ["0.93"])
        reasons = [e["reason"] for e in self.last("history", self.send(cmd="history"))["entries"]]
        self.assertEqual(reasons, ["restored", "measured"])
        # a removed preset comes back from the history
        self.send(cmd="remove_preset", name=name)
        entries = self.last("history", self.send(cmd="history"))["entries"]
        self.assertEqual((entries[0]["reason"], entries[0]["current"]), ("removed", False))
        again = self.last("saved", self.send(cmd="history_restore", id=entries[0]["id"]))
        self.assertIn(name, [x["name"] for x in again["saved"]])
        self.assertEqual(next(x for x in again["saved"] if x["name"] == name)["spoolman_id"], 7)
        self.assertIsNotNone(self.loaded(name))
        self.assertEqual(self.last("error", self.send(cmd="history_restore", id="../../etc"))["message"], "history_gone")

    def test_review_base_and_own_presets(self):
        result, saved = self.make_sunlu()
        name = saved["name"]
        base = self.orca.host.preset_bundle().filaments.find_preset("SUNLU PLA+ @System")
        base._config["fan_min_speed"] = "35"          # OrcaSlicer updated the maker's preset
        issue = self.issues(preset=name, code="base_updated")[0]
        self.assertEqual(issue["fixes"], ["rebase"])
        self.send(cmd="review_fix", fix="rebase", code="base_updated", preset=name, source=name)
        with open(saved["path"], encoding="utf-8") as f:
            preset = json.load(f)
        self.assertEqual(preset["fan_min_speed"], "35")
        self.assertEqual(preset["filament_density"], ["1.21"])          # FilaForge's value kept
        self.assertEqual(self.issues(preset=name, code="base_updated"), [])
        # the user's own presets: checked, never changed
        own = fake_orca.Preset("My PETG", {"filament_type": "PETG", "filament_vendor": "Generic",
                                           "filament_max_volumetric_speed": "0", "fan_min_speed": "60",
                                           "fan_max_speed": "40", "nozzle_temperature": "260",
                                           "nozzle_temperature_range_low": "220", "nozzle_temperature_range_high": "250",
                                           "inherits": "Gone PETG"}, system=False)
        self.orca.host.preset_bundle().filaments.presets.append(own)
        codes = {i["code"]: i for i in self.issues(preset="My PETG")}
        self.assertEqual(set(codes), {"no_mvs", "fan_order", "temp_range", "parent_missing"})
        self.assertEqual(codes["no_mvs"]["fixes"], ["research"])
        self.assertFalse(codes["no_mvs"]["ff"])
        self.assertEqual(self.last("error", self.send(cmd="review_fix", fix="remove", code="no_mvs", preset="My PETG",
                                                      source="My PETG"))["message"], "review_gone")
        # FilaForge's preset whose file was deleted by hand
        os.unlink(saved["path"])
        issue = self.issues(preset=name, code="file_missing")[0]
        self.send(cmd="review_fix", fix="forget", code="file_missing", preset=name, source=name)
        self.assertEqual(self.issues(preset=name), [])

    # -- print defects -----------------------------------------------------------------------------------------------
    def test_defects_loop(self):
        self.send(cmd="init")
        ctx = self.last("defect_context", self.send(cmd="defect_context"))
        self.assertIn("stringing", ctx["list"])
        self.assertEqual(ctx["questions"]["stringing"][0][0], "dried")
        fil = ctx["info"]["filament"]
        self.assertFalse(ctx["info"]["filament_ff"])
        plan = self.last("defect_plan", self.send(cmd="defect_plan", defect="stringing",
                                                  answers={"dried": "yes", "kind": "hairs"}))
        first = plan["causes"][0]
        self.assertEqual(first["kind"], "change")
        temp = first["changes"][0]          # PETG with fine hairs, dried: a longer retraction comes first
        # the user's (system) preset stays; a FilaForge copy gets the fix
        done = self.last("defect_plan", self.send(cmd="defect_apply", defect="stringing", cause=first["cause"],
                                                  answers={"dried": "yes", "kind": "hairs"}))
        made = done["applied"]["created"][0]
        self.assertEqual(made["kind"], "filament")
        self.assertTrue(made["name"].endswith("FF"))
        path = next(x["file"] for x in done["saved"] if x["name"] == made["name"])
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)[temp["key"]][0], temp["after"])
        self.assertEqual(len(done["pending"]), 1)
        self.assertIsNotNone(self.orca.host.preset_bundle().filaments.find_preset(fil))
        # no change: undone, and that cause goes to the bottom
        after = self.last("defect_plan", self.send(cmd="defect_outcome", id=done["applied"]["id"], outcome="same"))
        self.assertEqual(after["note"], "undone")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)[temp["key"]][0], temp["before"])
        self.assertEqual(after["pending"], [])
        self.assertEqual(after["causes"][-1]["cause"], first["cause"])
        self.assertEqual(after["causes"][-1]["tried"], ["same"])
        self.assertEqual(self.last("error", self.send(cmd="defect_outcome", id=done["applied"]["id"],
                                                      outcome="better"))["message"], "defect_gone")
        # the next cause helps: kept, and kept as the user's measurement
        nxt = next(c for c in after["causes"] if c["kind"] == "change")
        done = self.last("defect_plan", self.send(cmd="defect_apply", defect="stringing", cause=nxt["cause"],
                                                  answers={"dried": "yes", "kind": "hairs"}))
        kept = self.last("defect_plan", self.send(cmd="defect_outcome", id=done["applied"]["id"], outcome="better"))
        self.assertEqual(kept["note"], "kept_measured")
        ident = self.plugin.research.identity_from(done["info"]["identity"])
        key = self.plugin.review.SYNC_KEYS[nxt["changes"][0]["key"]]
        self.assertEqual(self.plugin.store().get(ident, "Creality K1C", 0.4)[key],
                         float(nxt["changes"][0]["after"]))

    def test_defect_history(self):
        self.send(cmd="init")
        self.last("defect_context", self.send(cmd="defect_context"))
        self.send(cmd="defect_apply", defect="stringing", cause="dry", answers={"dried": "no"})
        hist = self.last("defect_history", self.send(cmd="defect_history"))
        self.assertEqual([(h["defect"], h["cause"]) for h in hist["items"]], [("stringing", "dry")])
        self.assertFalse(hist["all"])
        every = self.last("defect_history", self.send(cmd="defect_history", all=True))
        self.assertTrue(every["all"])
        for defect in ("seam", "ghosting", "top_holes", "wall_gaps", "curling", "supports", "layer_shift"):
            plan = self.last("defect_plan", self.send(cmd="defect_plan", defect=defect))
            self.assertTrue(plan["causes"], defect)

    def test_defect_dimensions_to_process(self):
        self.send(cmd="init")
        rows = [{"kind": "hole", "expected": 5, "measured": 4.8}]
        plan = self.last("defect_plan", self.send(cmd="defect_plan", defect="dimensions", inputs={"rows": rows}))
        fix = next(c for c in plan["causes"] if c["cause"] == "compensate")
        self.assertEqual([(c["target"], c["key"], c["after"]) for c in fix["changes"]],
                         [("process", "xy_hole_compensation", "0.1")])
        done = self.last("defect_plan", self.send(cmd="defect_apply", defect="dimensions", cause="compensate",
                                                  inputs={"rows": rows}))
        made = done["applied"]["created"][0]
        self.assertEqual(made["kind"], "process")
        path = next(x["file"] for x in done["saved"] if x["name"] == made["name"])
        with open(path, encoding="utf-8") as f:
            proc = json.load(f)
        self.assertEqual((proc["xy_hole_compensation"], proc["inherits"]), ("0.1", "0.20mm Standard @Creality K1C"))
        missing = self.last("defect_plan", self.send(cmd="defect_plan", defect="dimensions", inputs={"rows": []}))
        self.assertEqual((missing["causes"][0]["kind"], missing["causes"][0]["why"]), ("need", "d_missing"))

    def test_no_nameless_open(self):
        """OrcaSlicer asks the user about an "open" audit event it can't name (a file opened by descriptor).
        Python writes .pyc files that way, so loading FilaForge and reading a PDF data sheet must not write them."""
        import subprocess
        import textwrap
        here = os.path.dirname(os.path.abspath(__file__))
        fresh = os.path.join(self.data, "fresh")
        shutil.copytree(os.path.join(here, "..", "src", "filaforge"), os.path.join(fresh, "filaforge"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        script = textwrap.dedent("""
            import sys
            sys.path[:0] = [%r, %r]
            import fake_orca
            nameless = []

            def hook(ev, args):
                if ev == "open" and not isinstance(args[0], str):
                    f = sys._getframe(1)
                    while f is not None and "path" not in f.f_locals:
                        f = f.f_back
                    path = str(f.f_locals.get("path")) if f is not None else "?"
                    # the package's own __init__ is compiled by OrcaSlicer's loader before any FilaForge code runs
                    tag = "".join(str(n) for n in sys.version_info[:2])
                    if not path.replace(chr(92), "/").endswith("/filaforge/__pycache__/__init__.cpython-" + tag + ".pyc"):
                        nameless.append(path)
            sys.addaudithook(hook)
            fake_orca.install([])
            import filaforge
            from filaforge.core.sources import tds
            sys.dont_write_bytecode = True                     # the test's own helper module, not FilaForge
            from test_core import make_pdf
            assert "Nozzle" in tds.pdf_text(make_pdf(["Nozzle temperature 200-220"]))
            print(len(nameless), nameless)
        """) % (fresh, here)
        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertEqual(out.stdout.strip().splitlines()[-1], "0 []")

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
