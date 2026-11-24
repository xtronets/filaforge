"""FilaForge's core: sources, merge, tested values, processes, bundle and Spoolman. No OrcaSlicer needed.
Run: python3 -m unittest discover -s tests"""

import json
import os
import tempfile
import unittest

import helpers
from filaforge.core import measured, merge, process, research, spoolman, writer
from filaforge.core.model import BASELINE, DECLARED, ESTIMATED, PRESET, TESTED, Identity, Observation
from filaforge.core.net import CannedFetcher, FetchError
from filaforge.core.sources import ofd, orca_presets, spoolmandb, tds
from filaforge.core.text import brand_key, match_score, split_material


class TextTests(unittest.TestCase):
    def test_materials(self):
        self.assertEqual(split_material("PLA+"), ("PLA", "PLA+"))
        self.assertEqual(split_material("PLA+", "Silk"), ("PLA", "PLA+ Silk"))
        self.assertEqual(split_material("PETG"), ("PETG", ""))
        self.assertEqual(split_material("PA6-CF")[0], "PA-CF")
        self.assertEqual(split_material("TPU 95A")[0], "TPU")

    def test_match(self):
        self.assertGreater(match_score("PLA+", "PLA+", "PLA"), 0.9)
        self.assertLess(match_score("PLA+", "Silk PLA+", "PLA"), match_score("PLA+", "PLA+", "PLA"))
        self.assertLess(match_score("PLA+", "PLA+ 2.0", "PLA"), match_score("PLA+", "PLA+", "PLA"))
        self.assertLess(match_score("", "High Speed PLA", "PLA"), match_score("", "PLA", "PLA"))
        self.assertEqual(brand_key("SUNLU"), brand_key("Sunlu 3D"))


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.fetcher = helpers.fetcher()

    def values(self, obs):
        return {o.key: o.value for o in obs}

    def test_ofd(self):
        obs, report, extra = ofd.lookup(self.fetcher, Identity("Sunlu", "PLA", "PLA+"))
        v = self.values(obs)
        self.assertEqual(v["nozzle_temp_range"], (205.0, 245.0))
        self.assertEqual(v["density"], 1.21)
        self.assertTrue(v["data_sheet"].startswith("https://media.sunlu.com/"))
        self.assertEqual(extra["orca_profile"], "SUNLU PLA+")
        self.assertTrue(all(o.kind == DECLARED for o in obs))
        obs, _, _ = ofd.lookup(self.fetcher, Identity("SUNLU", "PLA", "High Speed PLA"))
        self.assertEqual(self.values(obs)["nozzle_temp_range"], (210.0, 230.0))
        obs, report, _ = ofd.lookup(self.fetcher, Identity("Nobody", "PLA"))
        self.assertEqual(obs, [])
        self.assertTrue(report.ok)

    def test_ofd_offline(self):
        obs, report, _ = ofd.lookup(CannedFetcher({}), Identity("Sunlu", "PLA"))
        self.assertEqual(obs, [])
        self.assertFalse(report.ok)

    def test_ofd_products(self):
        names = {p["name"] for p in ofd.products(self.fetcher, "SUNLU")}
        self.assertIn("PLA+", names)
        self.assertIn("High Speed PLA", names)
        self.assertEqual(ofd.products(self.fetcher, "Nobody"), [])

    def test_spoolmandb(self):
        obs, report = spoolmandb.lookup(self.fetcher, Identity("Sunlu", "PLA", "Silk PLA+"))
        v = self.values(obs)
        self.assertEqual(v["nozzle_temp_range"], (210.0, 230.0))
        self.assertEqual(v["nozzle_temp"], 230.0)
        self.assertIn("Silk", report.message)

    def test_tds_sheets(self):
        found = tds.parse(helpers.tds_text("sunlu_plaplus.txt"))
        self.assertEqual(found["nozzle_temp_bands"], [(205.0, 215.0, 100.0), (215.0, 245.0, 200.0)])
        self.assertEqual(found["nozzle_temp_range"], (205.0, 245.0))
        self.assertEqual(found["bed_temp_range"], (50.0, 60.0))
        self.assertEqual(found["density"], 1.21)
        self.assertEqual(found["vitrification"], 54.0)      # Vicat, not the HDT line before it
        self.assertEqual(found["retraction_length"], 1.0)
        self.assertEqual(found["dry_temp"], 50.0)
        found = tds.parse(helpers.tds_text("sunlu_hspla.txt"))
        self.assertEqual(found["nozzle_temp_bands"], [(210.0, 230.0, 250.0)])
        self.assertEqual(found["density"], 1.28)

    def test_tds_other_layouts(self):
        found = tds.parse("Printing Temperature: 190 – 220 °C\nBed temperature 50-60°C\nPrint speed 40-100 mm/s\n"
                          "Density: 1.24 g/cm³\nDrying: 55°C for 4-6 hours\nCooling fan 100%\n")
        self.assertEqual(found["nozzle_temp_range"], (190.0, 220.0))
        self.assertEqual(found["print_speed_max"], 100.0)
        self.assertEqual(found["dry_time"], 6.0)
        found = tds.parse("Nozzle temperature 230±10 °C\nHeated bed: 80 °C\nVicat softening point (ISO 306) 82 °C\n"
                          "Max volumetric speed 18 mm³/s")
        self.assertEqual(found["nozzle_temp_range"], (220.0, 240.0))
        self.assertEqual(found["bed_temp"], 80.0)
        self.assertEqual(found["vitrification"], 82.0)
        self.assertEqual(found["max_volumetric_speed"], 18.0)
        self.assertEqual(tds.parse("nothing here"), {})

    def test_tds_pdf(self):
        """A real PDF goes through the vendored pypdf (the maker sheets are kept out of the repository)."""
        pdf = make_pdf(["Printing temperature 200-220 C", "Bed temperature 60 C"])
        obs, report = tds.from_bytes(pdf, "sheet", "x.pdf")
        v = {o.key: o.value for o in obs}
        self.assertEqual(v.get("nozzle_temp_range"), (200.0, 220.0), report.message)
        self.assertEqual(v.get("bed_temp"), 60.0)
        local = "/mnt/user-data/uploads/Downloads/filaforge_test_sunlu_plaplus.pdf"
        if os.path.exists(local):
            with open(local, "rb") as f:
                obs, _ = tds.from_bytes(f.read(), "sheet", local)
            self.assertEqual({o.key: o.value for o in obs}["density"], 1.21)

    def test_orca_presets(self):
        presets = helpers.orca_presets()
        vendor, score, generic = orca_presets.pick(presets, Identity("Sunlu", "PLA", "PLA+"), "SUNLU PLA+")
        self.assertEqual(vendor["name"], "SUNLU PLA+ @System")
        self.assertEqual(generic["name"], "Generic PLA @K1C-all")       # the printer's own generic
        vendor, _, generic = orca_presets.pick(presets, Identity("Sunlu", "PLA", "High Speed PLA"))
        self.assertIsNone(vendor)
        self.assertEqual(generic["name"], "Generic PLA High Speed @System")
        vendor, _, _ = orca_presets.pick(presets, Identity("Sunlu", "PLA", "Silk PLA+"))
        self.assertEqual(vendor["name"], "SUNLU Silk PLA+ @System")
        incompatible = [dict(p, compatible=False) for p in presets]
        self.assertEqual(orca_presets.pick(incompatible, Identity("Sunlu", "PLA"))[::2], (None, None))


def make_pdf(lines):
    """A one-page PDF with these lines of text, with a correct cross-reference table."""
    text = " ".join("(%s) Tj 0 -20 Td" % line for line in lines)
    stream = ("BT /F1 12 Tf 20 150 Td %s ET" % text).encode("latin-1")
    objects = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
               b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
               b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
               b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)
    return out


class MergeTests(unittest.TestCase):
    def obs(self, key, value, kind, source, weight=0.5):
        return Observation(key, value, kind, source, source, "", weight)

    def test_tested_wins(self):
        picks = merge.merge([self.obs("nozzle_temp", 215, DECLARED, "tds", 0.95),
                             self.obs("nozzle_temp", 225, TESTED, "user", 1.0)])
        self.assertEqual(picks["nozzle_temp"].value, 225)
        self.assertEqual(picks["nozzle_temp"].confidence(), "high")

    def test_preset_outside_range_is_replaced(self):
        picks = merge.merge([self.obs("nozzle_temp_range", (230.0, 250.0), DECLARED, "ofd", 0.85),
                             self.obs("nozzle_temp", 220, PRESET, "orca", 0.8)])
        self.assertEqual(picks["nozzle_temp"].kind, ESTIMATED)
        self.assertEqual(picks["nozzle_temp"].value, 240.0)

    def test_band_by_speed(self):
        bands = [(205.0, 215.0, 100.0), (215.0, 245.0, 200.0)]
        obs = [self.obs("nozzle_temp_bands", bands, DECLARED, "tds", 0.95),
               self.obs("nozzle_temp_range", (205.0, 245.0), DECLARED, "tds", 0.95),
               self.obs("nozzle_temp", 210, DECLARED, "spoolmandb", 0.6)]
        self.assertEqual(merge.merge(obs, typical_speed=80)["nozzle_temp"].value, 210)
        fast = merge.merge(obs, typical_speed=300)["nozzle_temp"]
        self.assertEqual(fast.value, 225.0)
        self.assertIn("215-245", fast.note)

    def test_flow_estimate_and_generic(self):
        picks = merge.merge([self.obs("print_speed_max", 250.0, DECLARED, "tds", 0.95),
                             self.obs("max_volumetric_speed", 12.0, BASELINE, "generic", 0.3)])
        self.assertEqual(picks["max_volumetric_speed"].kind, ESTIMATED)
        self.assertAlmostEqual(picks["max_volumetric_speed"].value, 20.4, places=1)

    def test_settings(self):
        picks = merge.merge([self.obs("nozzle_temp_range", (205.0, 245.0), DECLARED, "ofd", 0.85),
                             self.obs("nozzle_temp", 220.0, PRESET, "orca", 0.8),
                             self.obs("flow_ratio", 0.95, BASELINE, "generic", 0.3),
                             self.obs("pressure_advance", 0.04, TESTED, "user", 1.0),
                             self.obs("retraction_length", 1.0, DECLARED, "tds", 0.95)])
        rows = {r["orca_key"]: r for r in merge.orca_settings(picks, {"nozzle_temperature": "220,220"})}
        self.assertEqual(rows["nozzle_temperature"]["value"], "220")
        self.assertFalse(rows["nozzle_temperature"]["apply"])                 # already the base's
        self.assertEqual(rows["nozzle_temperature_initial_layer"]["value"], "225")
        self.assertNotIn("filament_flow_ratio", rows)                         # generic: the base has it
        self.assertEqual(rows["enable_pressure_advance"]["value"], "1")
        self.assertNotIn("filament_retraction_length", rows)                  # declared only: not tuned


class ResearchTests(unittest.TestCase):
    def test_full(self):
        sheet = "https://media.sunlu.com/prod/20260618/65301701781747847057.pdf?filename=TDS"
        text = helpers.tds_text("sunlu_plaplus.txt").encode("utf-8")
        fetcher = helpers.fetcher({sheet: b"<html><body><pre>" + text + b"</pre></body></html>"})
        ident = research.identity_from({"vendor": "Sunlu", "material": "PLA+", "color_hex": "ffffff"})
        result = research.run(ident, helpers.orca_presets(), fetcher, {"inner_wall_speed": "300"},
                              merge.measured_observations({"flow_ratio": 0.98}))
        rows = {r["orca_key"]: r for r in result["settings"]}
        self.assertEqual(result["base"], {"name": "SUNLU PLA+ @System", "kind": "vendor"})
        self.assertEqual(rows["filament_flow_ratio"]["value"], "0.98")
        self.assertEqual(rows["filament_flow_ratio"]["kind"], TESTED)
        self.assertEqual(rows["filament_density"]["value"], "1.21")
        self.assertEqual(rows["temperature_vitrification"]["value"], "54")
        self.assertEqual(result["identity"]["colour"], "#ffffff")
        self.assertEqual([r["source"] for r in result["reports"]], ["ofd", "spoolmandb", "orca", "tds", "tested"])
        json.dumps(result)   # the page gets it as JSON

    def test_offline(self):
        result = research.run(Identity("Sunlu", "PLA", "PLA+"), helpers.orca_presets(), CannedFetcher({}),
                              use_online=False)
        self.assertEqual(result["base"]["name"], "SUNLU PLA+ @System")
        self.assertEqual([r["source"] for r in result["reports"]], ["orca"])

    def test_own_preset(self):
        sunlu = next(p for p in helpers.orca_presets() if p["name"] == "SUNLU PLA+ @System")
        config = dict(sunlu["config"], filament_flow_ratio="0.95", nozzle_temperature="215",
                      default_filament_colour='"#1E4FD8"')
        own = {"name": "_local/spooler/Sunlu PLA+ Niebieski @Creality K1C", "vendor": "Sunlu", "type": "PLA",
               "is_system": False, "compatible": True, "config": config}
        ident = research.identity_from(orca_presets.identity_of(own))
        self.assertEqual((ident.brand, ident.material, ident.product, ident.colour), ("Sunlu", "PLA", "PLA+", "#1E4FD8"))
        result = research.run(ident, helpers.orca_presets(), CannedFetcher({}), use_online=False, own=own)
        self.assertEqual(result["base"], {"name": own["name"], "kind": "own"})
        self.assertEqual([r["source"] for r in result["reports"]], ["orca", "own"])
        rows = {r["orca_key"]: r for r in result["settings"]}
        # the user's own value beats the maker's preset, and equals the base, so nothing to change
        self.assertEqual(rows["filament_flow_ratio"]["value"], "0.95")
        self.assertEqual(result["picks"]["flow_ratio"]["source"], "own")

    def test_identity_of_presets(self):
        cases = [
            ("Elegoo Rapid PETG Black", "Elegoo", "PETG", ("Elegoo", "PETG", "Rapid PETG")),
            ("eSUN PLA+ Biały", "", "PLA", ("eSUN", "PLA", "PLA+")),
            ("PLA Silk złoty", "Generic", "PLA", ("Generic", "PLA", "PLA Silk")),
            ("Generic PETG @Creality K1C", "Generic", "PETG", ("Generic", "PETG", "")),
            ("Bambu PLA Basic @BBL X1C", "Bambu Lab", "PLA", ("Bambu Lab", "PLA", "PLA Basic")),
        ]
        for name, vendor, kind, want in cases:
            got = orca_presets.identity_of({"name": name, "vendor": vendor, "type": kind, "config": {}})
            self.assertEqual((got["brand"], got["material"], got["product"]), want, name)


class MeasuredTests(unittest.TestCase):
    def test_store(self):
        with tempfile.TemporaryDirectory() as folder:
            store = measured.Store(folder)
            ident = Identity("SUNLU", "PLA", "PLA+")
            store.set(ident, "*", {"flow_ratio": "0,98", "pressure_advance": ""})
            store.set(ident, "Creality K1C", {"pressure_advance": 0.04})
            self.assertEqual(store.get(Identity("Sunlu", "PLA", "PLA+"), "Creality K1C 0.4 nozzle"),
                             {"flow_ratio": 0.98, "pressure_advance": 0.04})
            self.assertEqual(store.get(ident, "Snapmaker U1 (0.4 nozzle)"), {"flow_ratio": 0.98})
            with self.assertRaises(ValueError):
                store.set(ident, "*", {"flow_ratio": 3})
            store.set(ident, "*", {})
            self.assertEqual(store.get(ident, "Snapmaker U1"), {})

    def test_per_nozzle(self):
        with tempfile.TemporaryDirectory() as folder:
            store = measured.Store(folder)
            ident = Identity("SUNLU", "PLA", "PLA+")
            # saved before 0.3.1: max flow at the top of the section, measured on a 0.4 mm nozzle
            store.set(ident, "Creality K1C", {"max_volumetric_speed": 20, "flow_ratio": 0.97})
            self.assertEqual(store.get(ident, "Creality K1C", 0.4), {"max_volumetric_speed": 20, "flow_ratio": 0.97})
            self.assertEqual(store.get(ident, "Creality K1C", 0.6), {"flow_ratio": 0.97})
            # a 0.6 mm nozzle gets its own; the 0.4 mm value stays
            store.set(ident, "Creality K1C", {"max_volumetric_speed": 28, "pressure_advance": 0.02,
                                              "flow_ratio": 0.97}, nozzle=0.6)
            self.assertEqual(store.get(ident, "Creality K1C", 0.6),
                             {"max_volumetric_speed": 28, "pressure_advance": 0.02, "flow_ratio": 0.97})
            self.assertEqual(store.get(ident, "Creality K1C", "0.40"), {"max_volumetric_speed": 20, "flow_ratio": 0.97})
            # a 0.4 mm save moves the old value under its nozzle
            store.set(ident, "Creality K1C", {"max_volumetric_speed": 21, "flow_ratio": 0.97}, nozzle=0.4)
            section = store.sections(ident)["Creality K1C"]
            self.assertEqual((section["_nozzle"]["0.4"], "max_volumetric_speed" in section),
                             ({"max_volumetric_speed": 21}, False))
            self.assertEqual(store.get(ident, "Creality K1C", 0.6)["max_volumetric_speed"], 28)
            self.assertEqual(measured.nozzle_tag("0,6"), "0.6")


class SpoolmanTests(unittest.TestCase):
    FILAMENT = {"id": 46, "name": "Biały", "material": "PLA+", "vendor": {"name": "Sunlu"},
                "extra": {"orca_settings": json.dumps(json.dumps({"*": {"nozzle_temperature_range_low": 190},
                                                                  "K1C": {"filament_flow_ratio": 0.98,
                                                                          "pressure_advance": 0.04}})),
                          "orca_max_volumetric_speed": "18"}}

    def test_read(self):
        data = spoolman.settings_of(self.FILAMENT)
        self.assertEqual(data["K1C"]["pressure_advance"], 0.04)
        tested = {o.key: o.value for o in spoolman.tested(self.FILAMENT, "Creality K1C 0.4 nozzle")}
        self.assertEqual(tested, {"flow_ratio": 0.98, "pressure_advance": 0.04, "max_volumetric_speed": 18.0})
        self.assertEqual({o.key for o in spoolman.tested(self.FILAMENT, "Snapmaker U1")}, {"max_volumetric_speed"})
        self.assertIsNone(spoolman.settings_of({"extra": {"orca_settings": '"[1]"'}}))

    def test_add_missing(self):
        data = spoolman.settings_of(self.FILAMENT)
        merged, added, kept = spoolman.add_missing(data, "Creality K1C", {"filament_flow_ratio": "1",
                                                                         "nozzle_temperature": "220",
                                                                         "nozzle_temperature_range_low": "205"})
        self.assertEqual(added, ["filament_flow_ratio", "nozzle_temperature"])
        self.assertEqual(kept, ["nozzle_temperature_range_low"])
        self.assertEqual(merged["K1C"], {"filament_flow_ratio": 0.98, "pressure_advance": 0.04})  # untouched
        self.assertEqual(merged["Creality K1C"]["filament_flow_ratio"], "1")    # its own section, the K1C one
        self.assertNotIn("Creality K1C", data)                                  # the input is not changed

    def test_write(self):
        calls = []
        fresh = json.loads(json.dumps(self.FILAMENT))

        class Response:
            def __init__(self, body):
                self.body = body

            def read(self):
                return json.dumps(self.body).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(request, timeout):
            calls.append((request.get_method(), request.full_url, request.data))
            if request.full_url.endswith("field/filament"):
                return Response([{"key": "orca_settings"}])
            return Response(fresh if request.get_method() == "GET" else {})

        client = spoolman.Client("192.168.1.2:7912", opener)
        result = client.write_settings(46, "*", {"filament_max_volumetric_speed": "18", "filament_density": "1.21",
                                                 "nozzle_temperature_range_low": "5"})
        self.assertEqual((result["added"], result["differs"]), (["filament_max_volumetric_speed"],
                                                                ["nozzle_temperature_range_low"]))
        method, url, body = calls[-1]
        self.assertEqual((method, url), ("PATCH", "http://192.168.1.2:7912/api/v1/filament/46"))
        extra = json.loads(body)["extra"]
        self.assertEqual(extra["orca_max_volumetric_speed"], "18")              # every other field goes back
        written = json.loads(json.loads(extra["orca_settings"]))
        self.assertEqual(written["*"], {"nozzle_temperature_range_low": 190, "filament_max_volumetric_speed": "18"})
        # asked to overwrite: the differing value is replaced, a same one isn't written again
        fresh["extra"]["orca_settings"] = extra["orca_settings"]
        result = client.write_settings(46, "*", {"nozzle_temperature_range_low": "200",
                                                 "filament_max_volumetric_speed": "18.0"},
                                       overwrite=["nozzle_temperature_range_low", "filament_max_volumetric_speed"])
        self.assertEqual((result["replaced"], result["same"]), (["nozzle_temperature_range_low"],
                                                               ["filament_max_volumetric_speed"]))
        written = json.loads(json.loads(json.loads(calls[-1][2])["extra"]["orca_settings"]))
        self.assertEqual(written["*"]["nozzle_temperature_range_low"], "200")
        # nothing new and nothing to replace: no PATCH
        before = len(calls)
        fresh["extra"]["orca_settings"] = json.loads(calls[-1][2])["extra"]["orca_settings"]
        result = client.write_settings(46, "*", {"nozzle_temperature_range_low": "200"})
        self.assertEqual(result["same"], ["nozzle_temperature_range_low"])
        self.assertFalse([c for c in calls[before:] if c[0] == "PATCH"])

    def test_merge_model_section_wins(self):
        data = spoolman.settings_of(self.FILAMENT)
        r = spoolman.merge_settings(data, "K1C", {"filament_flow_ratio": "0.98", "pressure_advance": "0.05",
                                                  "nozzle_temperature_range_low": "200"},
                                    overwrite=["pressure_advance", "nozzle_temperature_range_low"])
        self.assertEqual((r["same"], r["replaced"]), (["filament_flow_ratio"],
                                                      ["pressure_advance", "nozzle_temperature_range_low"]))
        self.assertEqual(r["data"]["K1C"]["nozzle_temperature_range_low"], "200")   # in the model's section
        self.assertEqual(r["data"]["*"]["nozzle_temperature_range_low"], 190)       # "*" left for other printers


class ProcessTests(unittest.TestCase):
    # OrcaSlicer's "0.20mm Standard @Creality K1C", as it resolves
    CONFIG = {"layer_height": "0.2", "initial_layer_print_height": "0.2", "line_width": "0.42",
              "outer_wall_line_width": "0.42", "inner_wall_line_width": "0.45", "sparse_infill_line_width": "0.45",
              "outer_wall_speed": "200", "inner_wall_speed": "300", "sparse_infill_speed": "270",
              "top_surface_speed": "200", "initial_layer_speed": "50", "initial_layer_line_width": "0.5",
              "gap_infill_speed": "250", "bridge_speed": "25", "internal_bridge_speed": "150%",
              "scarf_joint_speed": "100%", "small_perimeter_speed": "50%", "thick_bridges": "0", "bridge_flow": "0.9",
              "default_acceleration": "10000", "outer_wall_acceleration": "3000", "inner_wall_acceleration": "3000",
              "top_surface_acceleration": "2000", "sparse_infill_acceleration": "100%", "bridge_acceleration": "50%",
              "travel_acceleration": "10000", "initial_layer_acceleration": "1000"}

    def changes(self, *a, **k):
        return {c["key"]: c for c in process.fit(self.CONFIG, 0.4, *a, **k)}

    def test_flow_limit(self):
        changes = self.changes(12, "PLA")
        # 12 mm³/s through 0.45 x 0.2 mm (0.0814 mm²) is 147 mm/s
        self.assertEqual(changes["inner_wall_speed"]["new"], 147)
        self.assertEqual(changes["inner_wall_speed"]["reasons"][0]["code"], "flow")
        self.assertEqual(changes["gap_infill_speed"]["new"], 159)   # solid infill width, here the 0.42 default
        self.assertNotIn("initial_layer_speed", changes)
        self.assertNotIn("small_perimeter_speed", changes)        # 50% of the outer wall follows it by itself
        self.assertEqual(changes["scarf_joint_speed"]["base"], 200)  # 100% of the outer wall, written in mm/s

    def test_relative_values(self):
        self.assertEqual(process.resolved(self.CONFIG, "internal_bridge_speed"), 37.5)
        self.assertEqual(process.resolved(self.CONFIG, "bridge_acceleration"), 1500)
        self.assertEqual(process.resolved(self.CONFIG, "sparse_infill_acceleration"), 10000)

    def test_accelerations(self):
        changes = self.changes(30, "PLA")
        # 25 mm/s reached in 25 ms: 1000 mm/s², under the 1500 (50% of 3000) the process had
        self.assertEqual(changes["bridge_acceleration"]["new"], 1000)
        self.assertEqual(changes["bridge_acceleration"]["group"], "accel")
        capped = self.changes(30, "PLA", machine={"accel": 5000, "travel_accel": 8000, "speed": 250})
        self.assertEqual(capped["default_acceleration"]["new"], 5000)
        self.assertEqual(capped["travel_acceleration"]["new"], 8000)
        self.assertEqual(capped["inner_wall_speed"]["new"], 250)
        tpu = self.changes(3.6, "TPU")
        self.assertEqual(tpu["outer_wall_acceleration"]["new"], 1500)
        self.assertLessEqual(tpu["inner_wall_speed"]["new"], 60)

    def test_materials_and_variants(self):
        petg = self.changes(30, "PETG")
        self.assertEqual(petg["outer_wall_speed"]["new"], 140)
        small = self.changes(30, "PLA", variant="small")
        self.assertEqual(small["outer_wall_speed"]["new"], 140)
        self.assertEqual(small["outer_wall_acceleration"]["new"], 1800)
        self.assertEqual(process.material_class("PLA+", "Silk"), "SILK")
        self.assertEqual(process.width_mm("110%", 0.4), 0.44)
        self.assertEqual(process.variant_for([(20, 30), (35, 10)]), "small")
        self.assertEqual(process.variant_for([(20, 30), (120, 10)]), "standard")
        self.assertEqual(process.variant_for([]), "standard")
        self.assertIn("bridge speed", process.describe({"code": "bridge_ramp", "speed": 25, "seconds": 0.025}))

    def test_preset(self):
        changes = process.fit(self.CONFIG, 0.4, 12, "PLA")
        name = process.process_name("0.20mm Standard @Creality K1C", "Sunlu PLA+")
        self.assertEqual(name, "0.20mm Standard Sunlu PLA+")
        p = process.preset("0.20mm Standard @Creality K1C", name, ["Creality K1C 0.4 nozzle"], changes)
        self.assertEqual(p["inherits"], "0.20mm Standard @Creality K1C")
        self.assertEqual(p["inner_wall_speed"], "147")
        self.assertEqual(p["bridge_acceleration"], "1000")
        self.assertEqual(writer.problem(p), "")


class WriterTests(unittest.TestCase):
    def test_filament_preset(self):
        ident = Identity("Sunlu", "PLA", "PLA+", "#ffffff")
        base = {"nozzle_temperature": "220,220", "filament_type": "\"PLA\"", "inherits": "x", "filament_id": "OF1",
                "compatible_printers": "\"a\""}
        p = writer.filament_preset("Sunlu PLA+ @Creality K1C 0.4 nozzle", base, {"nozzle_temperature": "225",
                                                                               "filament_notes": "a, b"},
                                   "Creality K1C 0.4 nozzle", ident, measured.identity_key(ident))
        self.assertEqual(p["nozzle_temperature"], ["225", "225"])        # one per nozzle variant of the base
        self.assertEqual(p["filament_notes"], ["a, b"])
        self.assertEqual(p["inherits"], "")
        self.assertTrue(p["filament_id"].startswith("FFG"))
        self.assertEqual(p["compatible_printers"], ["Creality K1C 0.4 nozzle"])
        self.assertEqual(p["filament_colour"], ["#FFFFFF"])
        self.assertEqual(writer.problem(p), "")
        bad = dict(p, nozzle_temperature=["hot"])
        self.assertIn("not a number", writer.problem(bad))

    def test_bundle_save_and_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = writer.Bundle(folder)
            reloads = []
            p = {"type": "process", "name": "A/B: test", "from": "User", "inherits": "base", "x": "1"}
            path = bundle.save(p, reloads.append)
            self.assertTrue(path.endswith("A-B- test.json"))
            self.assertEqual(reloads, ["filaforge"])
            self.assertEqual([i["name"] for i in bundle.listing()], ["A/B: test"])

            def refuse(_):
                raise RuntimeError("refused")
            with self.assertRaises(RuntimeError):
                bundle.save(dict(p, x="2"), refuse)
            with open(path) as f:
                self.assertEqual(json.load(f)["x"], "1")                  # the previous file is back
            self.assertTrue(bundle.remove("A/B: test", reloads.append))
            self.assertFalse(os.path.exists(path))

    def test_marker(self):
        self.assertEqual(writer.with_marker("Sunlu PLA+ @Creality K1C 0.4 nozzle"), "Sunlu PLA+ FF @Creality K1C 0.4 nozzle")
        self.assertEqual(writer.with_marker("Sunlu PLA+ FF @K1C"), "Sunlu PLA+ FF @K1C")
        self.assertEqual(writer.with_marker("Sunlu PLA+"), "Sunlu PLA+ FF")
        self.assertEqual(writer.with_marker("FFG PLA"), "FFG PLA FF")

    def test_account_folders(self):
        with tempfile.TemporaryDirectory() as data:
            os.makedirs(os.path.join(data, "user", "123"))
            os.makedirs(os.path.join(data, "user", "default"))
            found = writer.account_folders(data, [os.path.join(data, "user", "999", "filament", "x.json")])
            self.assertEqual(found, ["999", "123", "default"])


class NetTests(unittest.TestCase):
    def test_cache_and_stale(self):
        with tempfile.TemporaryDirectory() as folder:
            hits = []

            class Response:
                def read(self, n):
                    return b'{"a": 1}'

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            def opener(request, timeout):
                hits.append(request.full_url)
                if len(hits) > 1:
                    raise OSError("offline")
                return Response()
            from filaforge.core.net import Fetcher
            f = Fetcher(folder, ttl=0, opener=opener)
            self.assertEqual(f.json("https://x.test/a.json"), {"a": 1})
            self.assertEqual(f.json("https://x.test/a.json"), {"a": 1})     # stale copy when offline
            with self.assertRaises(FetchError):
                f.get("http://x.test/plain")


if __name__ == "__main__":
    unittest.main()
