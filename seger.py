"""Seger: offline glaze chemistry calculator.

Reads a plain-text glaze recipe, works out the Seger unity molecular formula
(UMF), oxide weight percentages, loss on ignition, ratio warnings, batch
scaling, cost and recipe-to-recipe differences. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field

# Molecular weights of the oxides that can appear in a glaze.
OXIDE_MW = {
    "Li2O": 29.88, "Na2O": 61.98, "K2O": 94.20,
    "MgO": 40.30, "CaO": 56.08, "SrO": 103.62, "BaO": 153.33,
    "ZnO": 81.38, "PbO": 223.20, "CoO": 74.93, "CuO": 79.55,
    "B2O3": 69.62, "Al2O3": 101.96, "Fe2O3": 159.69,
    "SiO2": 60.08, "TiO2": 79.87, "SnO2": 150.71, "ZrO2": 123.22,
    "MnO2": 86.94,
}

# Seger groups. Fluxes are summed to make the unity; the rest are relative to it.
FLUXES = ("Li2O", "Na2O", "K2O", "MgO", "CaO", "SrO", "BaO", "ZnO", "PbO", "CoO", "CuO")
R2O3 = ("Al2O3", "B2O3", "Fe2O3")
RO2 = ("SiO2", "TiO2", "SnO2", "ZrO2", "MnO2")

# Generic starting points, not a cone-specific standard. Tune them in [limits].
DEFAULT_LIMITS = {
    "al2o3_min": 0.20,
    "al2o3_max": 0.60,
    "ratio_min": 6.0,
    "ratio_max": 12.0,
}

SECTIONS = ("batch", "additions", "prices", "materials", "limits")


class GlazeError(Exception):
    """Raised when a recipe cannot be analysed."""


@dataclass
class Material:
    """A raw material: its molar mass and the oxides one mole of it yields."""

    name: str
    raw_mw: float
    oxides: dict  # oxide -> moles per mole of raw material


# Library values: raw molar mass in g/mol and oxide moles per mole of raw material.
_BUILTIN = [
    ("Silica", 60.08, {"SiO2": 1}),
    ("EPK", 258.16, {"Al2O3": 1, "SiO2": 2}),
    ("Potash feldspar", 278.33, {"K2O": 0.5, "Al2O3": 0.5, "SiO2": 3}),
    ("Soda feldspar", 262.22, {"Na2O": 0.5, "Al2O3": 0.5, "SiO2": 3}),
    ("Whiting", 100.09, {"CaO": 1}),
    ("Dolomite", 184.40, {"CaO": 1, "MgO": 1}),
    ("Magnesium carbonate", 84.31, {"MgO": 1}),
    ("Talc", 379.27, {"MgO": 3, "SiO2": 4}),
    ("Wollastonite", 116.16, {"CaO": 1, "SiO2": 1}),
    ("Zinc oxide", 81.38, {"ZnO": 1}),
    ("Lithium carbonate", 73.89, {"Li2O": 1}),
    ("Barium carbonate", 197.34, {"BaO": 1}),
    ("Strontium carbonate", 147.63, {"SrO": 1}),
    ("Borax", 381.37, {"Na2O": 1, "B2O3": 2}),
    ("Boric acid", 61.83, {"B2O3": 0.5}),
    ("Red iron oxide", 159.69, {"Fe2O3": 1}),
    ("Rutile", 79.87, {"TiO2": 1}),
    ("Tin oxide", 150.71, {"SnO2": 1}),
    ("Zircon", 183.30, {"ZrO2": 1, "SiO2": 1}),
    ("Cobalt oxide", 74.93, {"CoO": 1}),
    ("Copper carbonate", 123.55, {"CuO": 1}),
    ("Manganese dioxide", 86.94, {"MnO2": 1}),
]

LIBRARY = {name.lower(): Material(name, mw, ox) for name, mw, ox in _BUILTIN}


@dataclass
class Recipe:
    """A parsed recipe. Batch and additions hold (line, Material, value) tuples."""

    name: str = "Untitled glaze"
    cone: str = ""
    batch: list = field(default_factory=list)      # grams
    additions: list = field(default_factory=list)  # percent of the base batch
    prices: dict = field(default_factory=dict)     # material key -> price per kg
    materials: dict = field(default_factory=dict)  # custom material key -> Material
    limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))


def lookup(key: str, recipe: Recipe | None = None) -> Material | None:
    """Find a material by name, checking the recipe's custom materials first."""
    k = key.strip().lower()
    if recipe is not None and k in recipe.materials:
        return recipe.materials[k]
    return LIBRARY.get(k)


def _finite_number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _split_pair(line: str) -> tuple[str, str] | None:
    key, sep, value = line.partition("=")
    if not sep or not key.strip() or not value.strip():
        return None
    return key.strip(), value.strip()


def parse_recipe(text: str, source: str = "recipe") -> tuple[Recipe, list[str]]:
    """Parse recipe text. Returns (recipe, errors); errors are 'source:line: message'."""
    errors: list[str] = []
    header: dict[str, str] = {}
    sections: dict[str, list[tuple[int, str]]] = {s: [] for s in SECTIONS}
    current: str | None = None  # None = header area, "?" = unknown section (skipped)

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().lower()
            if name in sections:
                current = name
            else:
                errors.append(f"{source}:{lineno}: unknown section [{name}]")
                current = "?"
            continue
        if current == "?":
            continue
        if current is None:
            pair = _split_pair(line)
            if pair and pair[0].lower() in ("name", "cone"):
                header[pair[0].lower()] = pair[1]
            else:
                errors.append(f"{source}:{lineno}: expected a [section] header before '{line}'")
            continue
        sections[current].append((lineno, line))

    recipe = Recipe(name=header.get("name", "Untitled glaze"), cone=header.get("cone", ""))

    # Custom materials go first so that batch lines can refer to them whatever the order.
    for lineno, line in sections["materials"]:
        _parse_material(line, lineno, source, recipe, errors)

    for lineno, line in sections["batch"]:
        _parse_amount(line, lineno, source, recipe, errors, recipe.batch, "grams")
    for lineno, line in sections["additions"]:
        _parse_amount(line, lineno, source, recipe, errors, recipe.additions, "percent")
    for lineno, line in sections["prices"]:
        pair = _split_pair(line)
        mat = lookup(pair[0], recipe) if pair else None
        value = _finite_number(pair[1]) if pair else None
        if pair is None or value is None or value < 0:
            errors.append(f"{source}:{lineno}: expected 'Material = price per kg' with a number >= 0")
        elif mat is None:
            errors.append(f"{source}:{lineno}: unknown material '{pair[0]}'")
        else:
            recipe.prices[mat.name.lower()] = value
    for lineno, line in sections["limits"]:
        pair = _split_pair(line)
        value = _finite_number(pair[1]) if pair else None
        if pair is None or value is None:
            errors.append(f"{source}:{lineno}: expected 'limit = number'")
        elif pair[0].lower() not in DEFAULT_LIMITS:
            errors.append(f"{source}:{lineno}: unknown limit '{pair[0]}'")
        else:
            recipe.limits[pair[0].lower()] = value

    if not sections["batch"]:
        errors.append(f"{source}: [batch] has no materials")
    if recipe.batch and sum(g for _, _, g in recipe.batch) <= 0:
        errors.append(f"{source}: [batch] total must be above zero")
    if recipe.limits["al2o3_min"] > recipe.limits["al2o3_max"]:
        errors.append(f"{source}: al2o3_min is above al2o3_max")
    if recipe.limits["ratio_min"] > recipe.limits["ratio_max"]:
        errors.append(f"{source}: ratio_min is above ratio_max")
    return recipe, errors


def _parse_material(line: str, lineno: int, source: str, recipe: Recipe, errors: list[str]) -> None:
    pair = _split_pair(line)
    if pair is None or "|" not in pair[1]:
        errors.append(f"{source}:{lineno}: expected 'Name = raw_mw | Oxide:moles, ...'")
        return
    name, spec = pair
    key = name.lower()
    if key in LIBRARY or key in recipe.materials:
        errors.append(f"{source}:{lineno}: material '{name}' is already defined")
        return
    mw_text, _, oxide_text = spec.partition("|")
    raw_mw = _finite_number(mw_text.strip())
    if raw_mw is None or raw_mw <= 0:
        errors.append(f"{source}:{lineno}: raw molar mass must be a number above zero")
        return
    oxides: dict[str, float] = {}
    for part in oxide_text.split(","):
        ox, sep, moles_text = part.partition(":")
        ox = ox.strip()
        moles = _finite_number(moles_text.strip()) if sep else None
        if ox not in OXIDE_MW or moles is None or moles <= 0:
            errors.append(f"{source}:{lineno}: expected 'Oxide:moles' with a known oxide, got '{part.strip()}'")
            return
        oxides[ox] = moles
    mass = sum(moles * OXIDE_MW[ox] for ox, moles in oxides.items())
    if mass > raw_mw:
        errors.append(f"{source}:{lineno}: oxides weigh more than the raw material ({mass:.2f} > {raw_mw:.2f})")
        return
    recipe.materials[key] = Material(name, raw_mw, oxides)


def _parse_amount(line, lineno, source, recipe, errors, target, unit) -> None:
    pair = _split_pair(line)
    if pair is None:
        errors.append(f"{source}:{lineno}: expected 'Material = {unit}'")
        return
    name, text = pair
    mat = lookup(name, recipe)
    value = _finite_number(text)
    if mat is None:
        errors.append(f"{source}:{lineno}: unknown material '{name}' (check spelling or define it under [materials])")
        return
    if value is None or value < 0:
        errors.append(f"{source}:{lineno}: amount for '{name}' must be a number >= 0")
        return
    if any(existing.name.lower() == mat.name.lower() for _, existing, _ in target):
        errors.append(f"{source}:{lineno}: duplicate material '{name}'")
        return
    target.append((lineno, mat, value))


def _material_totals(mat: Material, grams: float) -> tuple[dict, dict, float]:
    """Oxide moles, oxide grams and loss on ignition for a given mass of material."""
    moles = grams / mat.raw_mw
    ox_moles = {ox: moles * n for ox, n in mat.oxides.items()}
    ox_grams = {ox: m * OXIDE_MW[ox] for ox, m in ox_moles.items()}
    loi = grams - sum(ox_grams.values())
    return ox_moles, ox_grams, loi


def analyze(recipe: Recipe) -> dict:
    """Work out the unity formula, oxide percentages, LOI, warnings and cost."""
    base = sum(g for _, _, g in recipe.batch)
    if base <= 0:
        raise GlazeError("batch total must be above zero")

    rows = []
    for _, mat, grams in recipe.batch:
        rows.append((mat, "base", grams))
    for _, mat, pct in recipe.additions:
        rows.append((mat, "addition", base * pct / 100))

    ox_moles: dict[str, float] = {}
    ox_grams: dict[str, float] = {}
    loi_total = 0.0
    total_grams = 0.0
    row_out = []
    for mat, kind, grams in rows:
        moles, mass, loi = _material_totals(mat, grams)
        for ox, value in moles.items():
            ox_moles[ox] = ox_moles.get(ox, 0.0) + value
        for ox, value in mass.items():
            ox_grams[ox] = ox_grams.get(ox, 0.0) + value
        loi_total += loi
        total_grams += grams
        row_out.append({"material": mat.name, "kind": kind,
                        "grams": round(grams, 4), "loi_grams": round(loi, 4)})

    flux = sum(ox_moles.get(ox, 0.0) for ox in FLUXES)
    if flux <= 0:
        raise GlazeError("the glaze has no flux (Li2O, Na2O, K2O, MgO, CaO, ZnO, BaO, ...); "
                         "the unity formula cannot be computed")

    umf = {ox: ox_moles[ox] / flux for ox in OXIDE_MW if ox_moles.get(ox)}
    groups = {
        "RO_R2O": 1.0,
        "R2O3": sum(umf.get(ox, 0.0) for ox in R2O3),
        "RO2": sum(umf.get(ox, 0.0) for ox in RO2),
    }
    alumina = umf.get("Al2O3", 0.0)
    ratio = umf.get("SiO2", 0.0) / alumina if alumina > 0 else None

    fired_mass = sum(ox_grams.values())
    oxide_percent = {ox: round(ox_grams[ox] / fired_mass * 100, 4) for ox in OXIDE_MW if ox_grams.get(ox)}

    priced = [(mat, grams) for mat, _, grams in rows if mat.name.lower() in recipe.prices]
    unpriced = sorted({mat.name for mat, _, _ in rows if mat.name.lower() not in recipe.prices})
    cost = None
    if priced:
        cost_total = sum(grams / 1000 * recipe.prices[mat.name.lower()] for mat, grams in priced)
        priced_grams = sum(grams for _, grams in priced)
        per_kg = cost_total / priced_grams * 1000 if priced_grams > 0 else 0.0
        cost = {"total": round(cost_total, 4),
                "per_kg": round(per_kg, 4),
                "unpriced": unpriced}

    return {
        "name": recipe.name,
        "cone": recipe.cone,
        "batch_grams": round(base, 4),
        "total_grams": round(total_grams, 4),
        "rows": row_out,
        "loi_percent": round(loi_total / total_grams * 100, 4),
        "oxide_percent": oxide_percent,
        "umf": {ox: round(v, 4) for ox, v in umf.items()},
        "groups": {k: round(v, 4) for k, v in groups.items()},
        "si_al_ratio": round(ratio, 4) if ratio is not None else None,
        "warnings": _warnings(umf, ratio, ox_grams, recipe.limits),
        "cost": cost,
    }


def _warnings(umf: dict, ratio, ox_grams: dict, limits: dict) -> list[str]:
    out = []
    alumina = umf.get("Al2O3", 0.0)
    if alumina < limits["al2o3_min"]:
        out.append(f"Al2O3 {alumina:.3f} is below {limits['al2o3_min']:.2f}: glaze may run or flow off")
    elif alumina > limits["al2o3_max"]:
        out.append(f"Al2O3 {alumina:.3f} is above {limits['al2o3_max']:.2f}: glaze may come out dry or matte")
    if ratio is None:
        out.append("no alumina in the recipe, so the SiO2:Al2O3 ratio is undefined")
    elif not limits["ratio_min"] <= ratio <= limits["ratio_max"]:
        out.append(f"SiO2:Al2O3 ratio {ratio:.2f} is outside {limits['ratio_min']:.1f} to {limits['ratio_max']:.1f}")
    if ox_grams.get("PbO"):
        out.append("lead (PbO) present: not food safe")
    if ox_grams.get("BaO"):
        out.append("barium (BaO) present: check food safety before using on food ware")
    return out


def scale_batch(recipe: Recipe, total: float) -> list[tuple[str, float]]:
    """Scale the base batch to a target total in grams; additions stay a percentage of it."""
    if not math.isfinite(total) or total <= 0:
        raise GlazeError("target total must be a number above zero")
    base = sum(g for _, _, g in recipe.batch)
    factor = total / base
    out = [(mat.name, round(grams * factor, 1)) for _, mat, grams in recipe.batch]
    out += [(f"{mat.name} (addition)", round(total * pct / 100, 1)) for _, mat, pct in recipe.additions]
    return out


def diff_analyses(a: dict, b: dict) -> list[dict]:
    """Per-oxide comparison of two analysed recipes' unity formulas."""
    rows = []
    for ox in OXIDE_MW:
        va, vb = a["umf"].get(ox, 0.0), b["umf"].get(ox, 0.0)
        if va or vb:
            rows.append({"oxide": ox, "a": va, "b": vb, "delta": round(vb - va, 4)})
    return rows


def lint_file(path: str) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a recipe file."""
    text = _read(path)
    recipe, errors = parse_recipe(text, source=path)
    if errors:
        return errors, []
    return [], analyze(recipe)["warnings"]


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def format_analysis(result: dict) -> str:
    lines = [f"{result['name']}" + (f" (cone {result['cone']})" if result["cone"] else ""),
             f"Batch {result['batch_grams']:.1f} g, total with additions {result['total_grams']:.1f} g",
             "", "Batch", f"  {'Material':<24}{'grams':>10}{'LOI g':>10}"]
    for row in result["rows"]:
        label = row["material"] + (" (add)" if row["kind"] == "addition" else "")
        lines.append(f"  {label:<24}{row['grams']:>10.2f}{row['loi_grams']:>10.2f}")
    lines += ["", f"Loss on ignition: {result['loi_percent']:.2f} %", "", "Unity molecular formula"]
    for ox, value in result["umf"].items():
        lines.append(f"  {ox:<8}{value:>8.3f}")
    lines += ["", "Oxide weight %"]
    for ox, value in result["oxide_percent"].items():
        lines.append(f"  {ox:<8}{value:>8.2f}")
    ratio = result["si_al_ratio"]
    lines += ["", f"SiO2:Al2O3 ratio: {ratio:.2f}" if ratio is not None else "SiO2:Al2O3 ratio: n/a"]
    if result["cost"]:
        c = result["cost"]
        lines.append(f"Cost: {c['total']:.2f} for the batch, {c['per_kg']:.2f} per kg")
        if c["unpriced"]:
            lines.append("Unpriced: " + ", ".join(c["unpriced"]))
    lines.append("")
    if result["warnings"]:
        lines += [f"WARNING: {w}" for w in result["warnings"]]
    else:
        lines.append("No warnings.")
    return "\n".join(lines)


def format_diff(a_name: str, b_name: str, rows: list[dict]) -> str:
    lines = [f"{'Oxide':<8}{a_name[:12]:>14}{b_name[:12]:>14}{'change':>10}"]
    for r in rows:
        lines.append(f"{r['oxide']:<8}{r['a']:>14.3f}{r['b']:>14.3f}{r['delta']:>+10.3f}")
    return "\n".join(lines)


def _load(path: str) -> dict:
    """Parse and analyse a file. Raises GlazeError with every problem found."""
    recipe, errors = parse_recipe(_read(path), source=path)
    if errors:
        raise GlazeError("\n".join(errors))
    return analyze(recipe)


def _cmd_analyze(args) -> int:
    result = _load(args.file)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(format_analysis(result))
    return 0


def _cmd_scale(args) -> int:
    recipe, errors = parse_recipe(_read(args.file), source=args.file)
    if errors:
        raise GlazeError("\n".join(errors))
    print(f"Scaled to {args.total:g} g of base batch")
    for name, grams in scale_batch(recipe, args.total):
        print(f"  {name:<28}{grams:>10.1f} g")
    return 0


def _cmd_diff(args) -> int:
    a, b = _load(args.a), _load(args.b)
    print(format_diff(a["name"], b["name"], diff_analyses(a, b)))
    return 0


def _cmd_lint(args) -> int:
    errors, warnings = lint_file(args.file)
    for e in errors:
        print(e, file=sys.stderr)
    if errors:
        return 1
    for w in warnings:
        print(f"{args.file}: warning: {w}")
    if not warnings:
        print(f"{args.file}: ok")
    return 2 if (warnings and args.strict) else 0


def _cmd_materials(args) -> int:
    print(f"{'Material':<22}{'raw g/mol':>11}  Oxides (moles per mole)")
    for mat in sorted(LIBRARY.values(), key=lambda m: m.name.lower()):
        parts = ", ".join(f"{ox}:{n:g}" for ox, n in mat.oxides.items())
        print(f"{mat.name:<22}{mat.raw_mw:>11.2f}  {parts}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seger", description="Offline glaze chemistry calculator.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="unity formula, oxide %, LOI, warnings and cost")
    p.add_argument("file")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p.set_defaults(func=_cmd_analyze)

    p = sub.add_parser("scale", help="scale the base batch to a total in grams")
    p.add_argument("file")
    p.add_argument("--total", type=float, required=True)
    p.set_defaults(func=_cmd_scale)

    p = sub.add_parser("diff", help="compare the unity formulas of two recipes")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=_cmd_diff)

    p = sub.add_parser("lint", help="check a recipe file; exit 1 on errors, 2 on warnings with --strict")
    p.add_argument("file")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures (exit 2)")
    p.set_defaults(func=_cmd_lint)

    p = sub.add_parser("materials", help="list the built-in raw material library")
    p.set_defaults(func=_cmd_materials)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        print(f"seger: cannot read {exc.filename}: {exc.strerror}", file=sys.stderr)
        return 1
    except GlazeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
