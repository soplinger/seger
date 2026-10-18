# Seger

**Offline glaze chemistry calculator.** Write a glaze as a plain-text recipe, and Seger works out its unity molecular formula, oxide weight percentages, loss on ignition, ratio warnings, scaled batches, cost and differences between recipes.

Seger is one standard-library Python file. No servers, no accounts, no network calls.

## Why

Glaze chemists check a recipe by reading its unity formula (the Seger formula): every flux is normalised to a sum of 1, so the alumina and silica ratios can be compared between glazes. Doing this by hand from raw-material batches is slow and error-prone. Seger does the arithmetic from molar masses, and it reports errors with the file and line they came from.

## Features

- **Plain-text recipe format** with `[batch]`, `[additions]`, `[prices]`, `[materials]` and `[limits]` sections, plus `name` and `cone` headers.
- **Built-in raw material library** of 22 common glaze materials (feldspars, silica, EPK, whiting, dolomite, talc, zinc oxide, borax, colorants and more), with molar masses and oxide yields. Run `seger materials` to list them.
- **Custom materials** defined in the recipe itself, with their molar mass and oxide content. Seger rejects a material whose oxides weigh more than the raw material.
- **Unity (Seger) molecular formula** with the RO/R2O, R2O3 and RO2 groups, the fluxes normalised to 1.
- **Oxide weight percentages** of the fired glaze, plus **loss on ignition** per material and for the whole batch.
- **SiO2:Al2O3 ratio** and alumina checks against configurable limits, with plain-language warnings.
- **Food-safety flags** for lead (PbO) and barium (BaO).
- **Additions** such as colorants, entered as a percentage of the base batch.
- **Batch scaling** to any total in grams, with additions kept in proportion.
- **Cost** per batch and per kilogram from a price list, with unpriced materials listed by name.
- **Recipe diff** that puts two unity formulas side by side with the change for each oxide.
- **Line-numbered errors** for unknown materials, duplicates, bad or negative numbers, unknown sections, unknown limits and malformed custom materials.
- **Lint command** with CI exit codes: `0` clean, `1` errors, `2` warnings when `--strict` is set.
- **JSON output** for `analyze`, for scripts and spreadsheets.

## Quick start

The repository includes `examples/celadon.glaze`, a small cone 6 test glaze:

```text
name = Celadon Test
cone = 6

[batch]
Potash feldspar = 40
Silica = 25
Whiting = 15
EPK = 10
Dolomite = 5
Zinc oxide = 3
Talc = 2

[additions]
Red iron oxide = 1
```

Then run:

```sh
python3 seger.py analyze examples/celadon.glaze      # unity formula, oxide %, LOI, cost, warnings
python3 seger.py scale examples/celadon.glaze --total 500
python3 seger.py lint examples/celadon.glaze --strict
python3 seger.py analyze --json examples/celadon.glaze
python3 seger.py materials
```

## How to run

Requires Python 3 and nothing else to install.

```sh
python3 seger.py --help
python3 seger.py analyze FILE [--json]
python3 seger.py scale FILE --total GRAMS
python3 seger.py diff A B
python3 seger.py lint FILE [--strict]
python3 seger.py materials
```

Exit codes: `0` success, `1` errors (also for an unreadable file), `2` warnings when `lint --strict` is used.

### Recipe format

- Lines starting with `#` and blank lines are ignored.
- `name = ...` and `cone = ...` go before the first section.
- Under `[batch]`, write `Material = grams`. Under `[additions]`, write `Material = percent of base batch`.
- Under `[prices]`, write `Material = price per kg`.
- Under `[materials]`, define a custom material as `Name = raw_molar_mass | Oxide:moles, Oxide:moles`.
- Under `[limits]`, override `al2o3_min`, `al2o3_max`, `ratio_min` or `ratio_max`. The defaults are generic starting points, not a cone-specific standard.

## How to test

```sh
python3 -m pytest -q tests
```

Without pytest, the same tests run with the standard library:

```sh
python3 -m unittest discover -s tests
```

The tests cover parsing and line-numbered errors, the unity formula against a hand-checked feldspar case, loss on ignition, oxide percentage totals, additions, warnings and limit overrides, scaling, cost, diffs, JSON output and the lint exit codes.

## Notes and limits

- Seger models the raw materials as ideal oxide sources. It does not model melting, volatilisation, or the real chemistry of frits and gerstley borate. Test every glaze before firing.
- Alumina and silica limits are rules of thumb. Adjust them in `[limits]` for your clay body and cone.
- Fe2O3 is counted in the R2O3 group, the same as Al2O3 and B2O3. Colorant amounts therefore affect the alumina figure a little.
