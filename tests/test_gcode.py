import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from filaforge.core import gcode, preflight  # noqa: E402

AREA = math.pi * (1.75 / 2) ** 2


def fake_gcode(layers=5, speed=200.0, width=0.45, height=0.2, retract_per_layer=2, config=None, used_g=12.3,
               infill_speed=None, first_layer_fill=0.0):
    """OrcaSlicer-like G-code: each layer an outer wall square and an infill line, in relative E (M83)."""
    out = ["; HEADER_BLOCK_START", "; total layer number: %d" % layers, "; HEADER_BLOCK_END",
           "G90", "M83", "G1 Z0.4 F600", ";start gcode moves are not a layer", "G1 X0 Y0 E5 F300"]
    z = 0.0
    for n in range(layers):
        z += height
        out += [";LAYER_CHANGE", ";Z:%.2f" % z, ";HEIGHT:%.2f" % height, "G1 Z%.2f F600" % z]
        side = 20.0
        if n == 0 and first_layer_fill:          # a solid first layer: first_layer_fill mm of lines
            out += [";TYPE:Bottom surface", "G1 X0 Y100 F6000",
                    "G1 X%.1f Y100 E%.5f F6000" % (first_layer_fill, first_layer_fill * width * height / AREA)]
        out += [";TYPE:Outer wall", ";WIDTH:%.2f" % width, "G1 X10 Y10 F9000"]
        e = side * width * height / AREA
        for x, y in ((10 + side, 10), (10 + side, 10 + side), (10, 10 + side), (10, 10)):
            out.append("G1 X%.3f Y%.3f E%.5f F%d" % (x, y, e, speed * 60))
        for _ in range(retract_per_layer):
            out += ["G1 E-0.8 F2400", "G1 X50 Y50 F12000", "G1 E0.8 F2400"]
        out += [";TYPE:Sparse infill", ";WIDTH:0.45"]
        v = infill_speed or speed
        out.append("G1 X%.3f Y50 E%.5f F%d" % (50 + 30, 30 * 0.45 * height / AREA, v * 60))
    out += ["; filament used [mm] = 4100.5", "; filament used [g] = %.2f" % used_g,
            "; estimated printing time (normal mode) = 1h 2m 3s", "; CONFIG_BLOCK_START"]
    cfg = {"filament_diameter": "1.75", "filament_max_volumetric_speed": "24", "brim_type": "no_brim",
           "reduce_crossing_wall": "0"}
    cfg.update(config or {})
    out += ["; %s = %s" % kv for kv in cfg.items()]
    out.append("; CONFIG_BLOCK_END")
    return out


class GcodeTests(unittest.TestCase):
    def test_flow_per_feature(self):
        r = gcode.parse(fake_gcode(speed=200.0, infill_speed=300.0))
        self.assertEqual(len(r.layers), 5)
        wall = r.role("outer_wall")
        self.assertAlmostEqual(wall.max_flow, 0.45 * 0.2 * 200, places=1)       # 18 mm³/s
        self.assertAlmostEqual(r.role("sparse_infill").max_flow, 0.45 * 0.2 * 300, places=1)
        self.assertEqual(r.retractions, 10)
        self.assertEqual(r.layers[0].retractions, 2)
        self.assertEqual(r.total_time(), 3723.0)
        self.assertEqual(r.filament_used(), (4100.5, 12.3))
        self.assertEqual(r.setting("filament_max_volumetric_speed"), "24")
        self.assertAlmostEqual(r.layers[2].z, 0.6, places=2)

    def test_known_diameter_and_absolute_e(self):
        lines = ["G90", "M82", "G92 E0", ";LAYER_CHANGE", ";Z:0.2", ";TYPE:Inner wall",
                 "G1 X0 Y0 F6000", "G1 X10 Y0 E%.5f F6000" % (10 * 0.45 * 0.2 / AREA),
                 "G1 X20 Y0 E%.5f" % (20 * 0.45 * 0.2 / AREA)]
        r = gcode.parse(lines, filament_diameter=1.75)
        self.assertAlmostEqual(r.role("inner_wall").max_flow, 0.45 * 0.2 * 100, places=2)
        self.assertAlmostEqual(r.role("inner_wall").length, 20.0, places=3)

    def test_arc(self):
        self.assertAlmostEqual(gcode._arc_length(10, 0, -10, 0, -10, 0, False), math.pi * 10, places=6)
        self.assertAlmostEqual(gcode._arc_length(10, 0, 0, 10, -10, 0, False), math.pi * 5, places=6)
        self.assertAlmostEqual(gcode._arc_length(10, 0, 0, 10, -10, 0, True), math.pi * 15, places=6)

    def test_duration(self):
        self.assertEqual(gcode.duration("1d 2h 3m 4s"), 93784.0)
        self.assertEqual(gcode.duration("45m 2s"), 2702.0)
        self.assertIsNone(gcode.duration("soon"))


class PreflightTests(unittest.TestCase):
    def codes(self, report):
        return {f["code"]: f for f in report["findings"]}

    def test_flow_over_measured(self):
        r = gcode.parse(fake_gcode(speed=200.0, infill_speed=300.0))      # 18 and 27 mm³/s
        found = self.codes(preflight.check(r, {"cls": "PLA", "measured_mvs": 20.0}))
        f = found["flow_over_measured"]
        self.assertEqual(f["vars"]["role"], "sparse_infill")
        self.assertEqual(f["changes"][0]["after"], "20")
        self.assertEqual(f["level"], "warn")
        self.assertNotIn("flow_over_measured", self.codes(preflight.check(r, {"cls": "PLA", "measured_mvs": 30})))

    def test_flow_at_limit_without_measurement(self):
        r = gcode.parse(fake_gcode(speed=250.0))                            # 22.5 of 24
        found = self.codes(preflight.check(r, {"cls": "PLA"}))
        self.assertEqual(found["flow_at_limit"]["test"], "mvs")

    def test_spool(self):
        r = gcode.parse(fake_gcode(used_g=120))
        self.assertEqual(self.codes(preflight.check(r, {"cls": "PLA", "spool_left_g": 100}))["spool_short"]["level"],
                         "stop")
        self.assertIn("spool_tight", self.codes(preflight.check(r, {"cls": "PLA", "spool_left_g": 125})))
        self.assertEqual(self.codes(preflight.check(r, {"cls": "PLA", "spool_left_g": 500})), {})

    def test_retractions_and_brim(self):
        r = gcode.parse(fake_gcode(layers=10, retract_per_layer=45))
        found = self.codes(preflight.check(r, {"cls": "PETG"}))
        self.assertEqual(found["retractions"]["changes"][0]["key"], "reduce_crossing_wall")
        self.assertNotIn("retractions", self.codes(preflight.check(r, {"cls": "PLA"})))
        big = gcode.parse(fake_gcode(first_layer_fill=6000.0, speed=100))
        self.assertEqual(self.codes(preflight.check(big, {"cls": "ABS"}))["warp_brim"]["changes"][0]["after"],
                         "outer_only")
        self.assertNotIn("warp_brim", self.codes(preflight.check(big, {"cls": "PLA"})))
        braced = gcode.parse(fake_gcode(first_layer_fill=6000.0, speed=100, config={"brim_type": "outer_only"}))
        self.assertNotIn("warp_brim", self.codes(preflight.check(braced, {"cls": "ABS"})))


if __name__ == "__main__":
    unittest.main()
