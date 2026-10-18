"""Tests for seger. Run with: python3 -m pytest -q tests  (or python3 -m unittest discover -s tests)."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import seger  # noqa: E402

EXAMPLE = ROOT / "examples" / "celadon.glaze"


def _analyze_text(text):
    recipe, errors = seger.parse_recipe(text, source="t.glaze")
    assert not errors, errors
    return seger.analyze(recipe)


class ParseTests(unittest.TestCase):
    def test_example_recipe_parses_cleanly(self):
        recipe, errors = seger.parse_recipe(EXAMPLE.read_text(encoding="utf-8"), source=str(EXAMPLE))
        self.assertEqual(errors, [])
        self.assertEqual(recipe.name, "Celadon Test")
        self.assertEqual(recipe.cone, "6")
        self.assertEqual(len(recipe.batch), 7)
        self.assertEqual(len(recipe.additions), 1)

    def test_unknown_material_reports_line_number(self):
        _, errors = seger.parse_recipe("[batch]\nSilcia = 10\n", source="x.glaze")
        self.assertEqual(len(errors), 1)
        self.assertIn("x.glaze:2: unknown material 'Silcia'", errors[0])

    def test_duplicate_material_is_an_error(self):
        _, errors = seger.parse_recipe("[batch]\nSilica = 10\nsilica = 5\n", source="x.glaze")
        self.assertTrue(any("x.glaze:3: duplicate material" in e for e in errors))

    def test_negative_and_non_finite_amounts_are_rejected(self):
        _, errors = seger.parse_recipe("[batch]\nSilica = -4\nWhiting = nan\n", source="x.glaze")
        self.assertTrue(any("x.glaze:2:" in e for e in errors))
        self.assertTrue(any("x.glaze:3:" in e for e in errors))

    def test_line_outside_a_section_is_an_error(self):
        _, errors = seger.parse_recipe("Silica = 10\n[batch]\nSilica = 10\n", source="x.glaze")
        self.assertTrue(errors[0].startswith("x.glaze:1: expected a [section] header"))

    def test_empty_batch_is_an_error(self):
        _, errors = seger.parse_recipe("name = Empty\n", source="x.glaze")
        self.assertTrue(any("[batch] has no materials" in e for e in errors))

    def test_unknown_section_and_limit_are_reported(self):
        _, errors = seger.parse_recipe("[batch]\nSilica = 1\n[oxides]\n[limits]\nfoo = 2\n", source="x.glaze")
        self.assertTrue(any("x.glaze:3: unknown section [oxides]" in e for e in errors))
        self.assertTrue(any("x.glaze:5: unknown limit 'foo'" in e for e in errors))

    def test_custom_material_can_be_defined_after_use(self):
        text = (
            "[batch]\nMy frit = 100\nSilica = 10\n"
            "[materials]\nMy frit = 230 | SiO2:1.5, B2O3:0.5\n"
        )
        recipe, errors = seger.parse_recipe(text, source="x.glaze")
        self.assertEqual(errors, [])
        self.assertEqual(recipe.batch[0][1].name, "My frit")

    def test_custom_material_heavier_than_its_oxides_is_rejected(self):
        _, errors = seger.parse_recipe("[batch]\nSilica = 1\n[materials]\nHeavy = 10 | SiO2:1\n", source="x.glaze")
        self.assertTrue(any("weigh more than the raw material" in e for e in errors))


class ChemistryTests(unittest.TestCase):
    def test_potash_feldspar_alone_gives_known_unity_formula(self):
        # KAlSi3O8 gives K2O:Al2O3:SiO2 = 1:1:6 once K2O is the only flux.
        result = _analyze_text("[batch]\nPotash feldspar = 100\n")
        self.assertAlmostEqual(result["umf"]["K2O"], 1.0, places=3)
        self.assertAlmostEqual(result["umf"]["Al2O3"], 1.0, places=3)
        self.assertAlmostEqual(result["umf"]["SiO2"], 6.0, places=3)
        self.assertAlmostEqual(result["si_al_ratio"], 6.0, places=3)

    def test_flux_group_normalises_to_one(self):
        result = _analyze_text(EXAMPLE.read_text(encoding="utf-8"))
        fluxes = sum(result["umf"].get(ox, 0.0) for ox in seger.FLUXES)
        self.assertAlmostEqual(fluxes, 1.0, places=3)
        self.assertEqual(result["groups"]["RO_R2O"], 1.0)

    def test_whiting_loss_on_ignition_is_about_44_percent(self):
        # CaCO3 loses CO2 on firing: 100 g gives 56.08 g of CaO.
        result = _analyze_text("[batch]\nWhiting = 100\n")
        self.assertAlmostEqual(result["loi_percent"], 43.97, delta=0.05)

    def test_oxide_percentages_sum_to_one_hundred(self):
        result = _analyze_text(EXAMPLE.read_text(encoding="utf-8"))
        self.assertAlmostEqual(sum(result["oxide_percent"].values()), 100.0, delta=0.01)

    def test_additions_are_percent_of_base_batch(self):
        result = _analyze_text(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(result["batch_grams"], 100.0)
        self.assertEqual(result["total_grams"], 101.0)

    def test_no_flux_cannot_produce_a_unity_formula(self):
        with self.assertRaises(seger.GlazeError):
            _analyze_text("[batch]\nSilica = 100\n")


class WarningTests(unittest.TestCase):
    def test_no_alumina_warns_and_ratio_is_undefined(self):
        result = _analyze_text("[batch]\nSilica = 50\nWhiting = 100\n")
        self.assertIsNone(result["si_al_ratio"])
        self.assertTrue(any("below 0.20" in w for w in result["warnings"]))
        self.assertTrue(any("no alumina" in w for w in result["warnings"]))

    def test_barium_and_lead_are_flagged(self):
        result = _analyze_text("[batch]\nBarium carbonate = 20\nPotash feldspar = 80\n")
        self.assertTrue(any("barium" in w for w in result["warnings"]))
        text = "[batch]\nLead = 100\n[materials]\nLead = 223.2 | PbO:1\n"
        result = _analyze_text(text)
        self.assertTrue(any("lead" in w for w in result["warnings"]))

    def test_limits_section_changes_thresholds(self):
        text = "[batch]\nSilica = 50\nWhiting = 100\n[limits]\nal2o3_min = 0\n"
        result = _analyze_text(text)
        self.assertFalse(any("below" in w for w in result["warnings"]))

    def test_min_above_max_limit_is_an_error(self):
        _, errors = seger.parse_recipe("[batch]\nSilica = 1\n[limits]\nratio_min = 9\nratio_max = 5\n", source="x")
        self.assertTrue(any("ratio_min is above ratio_max" in e for e in errors))


class ToolTests(unittest.TestCase):
    def test_scale_hits_the_target_total(self):
        recipe, _ = seger.parse_recipe(EXAMPLE.read_text(encoding="utf-8"), source="x")
        rows = dict(seger.scale_batch(recipe, 500))
        self.assertEqual(rows["Potash feldspar"], 200.0)
        self.assertEqual(rows["Red iron oxide (addition)"], 5.0)

    def test_cost_and_unpriced_materials(self):
        text = "[batch]\nPotash feldspar = 50\nSilica = 50\nWhiting = 0\n[prices]\nSilica = 0.40\n"
        cost = _analyze_text(text)["cost"]
        self.assertEqual(cost["total"], 0.02)
        self.assertAlmostEqual(cost["per_kg"], 0.4, places=4)
        self.assertEqual(cost["unpriced"], ["Potash feldspar", "Whiting"])

    def test_diff_reports_new_oxide_as_positive_change(self):
        a = _analyze_text("[batch]\nPotash feldspar = 100\n")
        b = _analyze_text("[batch]\nPotash feldspar = 100\nWhiting = 10\n")
        rows = {r["oxide"]: r for r in seger.diff_analyses(a, b)}
        self.assertEqual(rows["CaO"]["a"], 0.0)
        self.assertGreater(rows["CaO"]["delta"], 0)

    def test_json_output_has_the_expected_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "g.glaze")
            Path(path).write_text("[batch]\nPotash feldspar = 100\n", encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                code = seger.main(["analyze", "--json", path])
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertIn("umf", data)
        self.assertIn("warnings", data)


class CliTests(unittest.TestCase):
    def _run(self, argv, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "g.glaze")
            Path(path).write_text(text, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = seger.main(argv + [path])
        return code, out.getvalue(), err.getvalue()

    def test_lint_error_exits_one_with_line_number(self):
        code, _, err = self._run(["lint"], "[batch]\nSilcia = 10\n")
        self.assertEqual(code, 1)
        self.assertIn("unknown material 'Silcia'", err)

    def test_lint_clean_file_exits_zero(self):
        code, out, _ = self._run(["lint"], EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertIn(": ok", out)

    def test_lint_warnings_exit_two_only_with_strict(self):
        text = "[batch]\nSilica = 50\nWhiting = 100\n"
        self.assertEqual(self._run(["lint"], text)[0], 0)
        self.assertEqual(self._run(["lint", "--strict"], text)[0], 2)

    def test_missing_file_exits_one(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = seger.main(["analyze", "/nonexistent/none.glaze"])
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err.getvalue())

    def test_materials_lists_the_library(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = seger.main(["materials"])
        self.assertEqual(code, 0)
        self.assertIn("Whiting", out.getvalue())


if __name__ == "__main__":
    unittest.main()
