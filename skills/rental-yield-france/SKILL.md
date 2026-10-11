---
name: rental-yield-france
description: Computes gross, net and after-tax rental yield, monthly cash flow with a loan and a ten-year table for a dwelling let in France under micro-foncier, reel foncier, LMNP micro-BIC or LMNP reel, with a tested script and fiscal rules dated and sourced. Use when a user asks about "rendement locatif", "cash flow", "LMNP", "micro-foncier", "deficit foncier", "rentabilite d'un investissement locatif" or whether a French rental purchase pays. Calculation only, never personalised advice.
license: MIT
metadata:
  author: Guillaume Couillard
  rules-read-on: "2026-10-11"
  language: "answers in the user's language, script and references in English and French"
---

# Rental yield, France

The model judges, the script computes. Every number in the answer comes from
`scripts/rendement.py`; never add, subtract or apply a tax rate yourself.

## When to use

A user gives, or can give, a purchase price and a rent for a dwelling in France and asks
about yield, cash flow, tax under a named regime, or a ten-year projection. Typical French
triggers: rendement brut, rendement net, net d'impot, cash flow, mensualite, LMNP, micro-BIC,
micro-foncier, regime reel, deficit foncier, amortissement.

Do not use for a company (SCI a l'IS), for bare ownership or usufruct (demembrement), for
tax-reduction schemes, for resale gains, or for a property outside France. Say so and stop.

## Workflow

1. Collect the inputs. Required: `price`, `rent_monthly` (excluding recoverable charges),
   `regime` (one of `micro-foncier`, `reel-foncier`, `lmnp-micro-bic`, `lmnp-reel`), `tmi`
   (marginal income-tax rate, one of 0, 0.11, 0.30, 0.41, 0.45). Optional: `fees`, `works`,
   `charges_annual` (owner's share), `property_tax`, `insurance_annual`, `management_rate`,
   `vacancy_months`, `income_year`, `tourism_unclassified`, `works_deductible`,
   `energy_renovation`, `rent_growth`, `charges_growth`, `horizon_years`, `loan`
   (`amount`, `rate`, `years`, `insurance_rate`), `lmnp` (`land_share`, `building_years`,
   `furniture_value`, `furniture_years`, `works_years`). Ask for a missing required value;
   never invent one. State every default you keep.
2. Write the inputs to a JSON file (`python scripts/rendement.py --example` prints a
   template) and run:
   `python scripts/rendement.py --input case.json --markdown` for the tables, or without
   `--markdown` for JSON.
3. If the script answers `not_eligible`, report the rule it names (for example R1, rent above
   15 000 EUR) and propose the regime it suggests. Do not compute under the refused regime.
4. Answer with: the summary table and the ten-year table from the script, then exactly three
   reading sentences (what the gross and net yields say, what the monthly cash flow after
   tax means, what changes the picture most), then the assumptions and the rules applied,
   each with its source and reading date, copied from the script output.
5. End with: "Calculation only, not personalised advice: the reader decides."

## Rules and sources

The fiscal rules (R1 to R9) live in `references/regles-fiscales.md` with the article or
BOFiP page, the URL and the reading date. Cite them as the script prints them. If the user
asks about a rule that is not there, say the script does not cover it; do not recall one
from memory.

## Limits

Linear depreciation with a land share, loan insurance not deducted, deductible CSG not
modelled, other household income assumed sufficient to absorb a global-income deduction.
These are listed as A1 to A8 in the script output; repeat the ones that matter.
