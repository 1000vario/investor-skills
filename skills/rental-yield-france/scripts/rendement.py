#!/usr/bin/env python3
"""Rental yield calculator for a residential property let in France.

The model judges, this script computes: every figure quoted in a skill answer
must come from this script. The fiscal rules are listed with their source and
reading date in ../references/regles-fiscales.md; the identifiers R1 to R9
below point to that file. Nothing here is personalised advice: the script
calculates and describes, the reader decides.

Usage:
    python rendement.py --input case.json             JSON result on stdout
    python rendement.py --input case.json --markdown  tables for a reader
    python rendement.py --example                     prints an example input
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy

READ_ON = "2026-10-11"

SOCIAL_RATE = 0.172
MICRO_FONCIER_CEILING = 15_000.0
MICRO_FONCIER_ABATEMENT = 0.30
DEFICIT_FONCIER_GLOBAL_CAP = 10_700.0
DEFICIT_FONCIER_ENERGY_CAP = 21_400.0
DEFICIT_CARRY_YEARS = 10
LMNP_STATUS_CEILING = 23_000.0
MICRO_BIC_LONG_TERM = {2025: (77_700.0, 0.50), 2026: (83_600.0, 0.50)}
MICRO_BIC_TOURISM_UNCLASSIFIED = (15_000.0, 0.30)
VALID_TMI = (0.0, 0.11, 0.30, 0.41, 0.45)
REGIMES = ("micro-foncier", "reel-foncier", "lmnp-micro-bic", "lmnp-reel")

RULES = {
    "R1": {
        "text": "Micro-foncier: gross property income of at most 15 000 EUR a year; "
        "taxable income = gross income minus a 30 % allowance.",
        "source": "CGI art. 32 (Legifrance LEGISCTA000006197178, in force since 2026-02-21); "
        "service-public.fr F1991 (checked 2026-04-15)",
    },
    "R2": {
        "text": "Regime reel (revenus fonciers): deductible charges are repair and maintenance, "
        "improvement works on dwellings, insurance premiums, taxes borne by the owner, "
        "management fees and loan interest.",
        "source": "CGI art. 31 I 1 a, a bis, b, c, d, e (Legifrance LEGISCTA000006197178, "
        "in force since 2026-02-21); service-public.fr F1991 (checked 2026-04-15)",
    },
    "R3": {
        "text": "Deficit foncier: the deficit from charges other than loan interest is deducted "
        "from global income up to 10 700 EUR a year (21 400 EUR for energy renovation works "
        "paid until 2027-12-31); the excess and the interest part are deducted from property "
        "income of the following ten years. The dwelling must stay let for three years.",
        "source": "CGI art. 156 I 3 (Legifrance LEGIARTI000006307920, in force since "
        "2026-07-01); decree 2023-297 "
        "(JORFTEXT000047476729); service-public.fr F1991 (checked 2026-04-15)",
    },
    "R4": {
        "text": "Social contributions (prelevements sociaux) of 17.2 % on the taxable property "
        "or furnished-rental income.",
        "source": "service-public.fr F1991 and F32744 (checked 2026-04-15)",
    },
    "R5": {
        "text": "Micro-BIC for furnished lets: 2025 income, receipts of at most 77 700 EUR and a "
        "50 % allowance; 2026 income, 83 600 EUR and 50 %. Unclassified furnished tourist "
        "accommodation: 15 000 EUR and 30 %.",
        "source": "CGI art. 50-0 (Legifrance LEGIARTI000042159220); service-public.fr F32744 "
        "(checked 2026-04-15); BOFiP ACTU-2025-00127 (2026-08-19)",
    },
    "R6": {
        "text": "Non-professional furnished lessor (LMNP) status: receipts of at most "
        "23 000 EUR a year, or at most the household's other earned income.",
        "source": "CGI art. 155 IV (Legifrance LEGISCTA000006197230); service-public.fr "
        "F32744 (checked 2026-04-15)",
    },
    "R7": {
        "text": "Depreciation of a let asset owned by an individual is deductible only up to "
        "the rent earned minus the other charges of the asset; the excess is deducted from "
        "the results of later years.",
        "source": "CGI art. 39 C II 2 and 3 (Legifrance LEGIARTI000029355753, in force since "
        "2015-01-01); BOFiP BOI-BIC-AMT-20-40-10-20 (2017-03-01) para. 40, 80 and 90",
    },
    "R8": {
        "text": "LMNP deficits (excluding the depreciation blocked by R7) are deducted only "
        "from non-professional furnished-rental income of the following ten years.",
        "source": "CGI art. 156 I 1 ter (Legifrance LEGIARTI000006307920, in force since "
        "2026-07-01); BOFiP "
        "BOI-BIC-CHAMP-40-20 (2026-08-19) para. 240 and 250",
    },
    "R9": {
        "text": "Income tax brackets on 2025 income: 0 % up to 11 600 EUR, 11 % to 29 579, "
        "30 % to 84 577, 41 % to 181 917, 45 % above. The marginal rate is an input, not "
        "computed here.",
        "source": "service-public.fr F1419 (checked 2026-04-15), which points to CGI "
        "art. 193 to 199",
    },
}

ASSUMPTIONS = [
    "A1: the loan is a constant-payment loan; loan insurance is charged on the initial capital.",
    "A2: loan insurance premiums are not deducted from taxable income (conservative).",
    "A3: the household's other income is high enough to absorb a deficit deducted from "
    "global income (R3); the tax saving is valued at the marginal rate given.",
    "A4: LMNP reel depreciation is linear: building share of (price + fees) over "
    "building_years, works over works_years, furniture over furniture_years; the land "
    "share is not depreciated. Real practice splits the building into components.",
    "A5: the deductible CSG of a later year and any local taxes other than the property "
    "tax are not modelled.",
    "A6: rent and charges grow at the given yearly rates; vacancy is a fixed share of each year.",
    "A7: micro regimes apply the allowance to the rent given, taken as the receipts.",
    "A8: the chosen regime is kept for the whole horizon; the three-year lock of the "
    "micro-foncier option and the three-year letting condition of R3 are not enforced.",
]

DEFAULT_LMNP = {
    "land_share": 0.15,
    "building_years": 30,
    "furniture_value": 0.0,
    "furniture_years": 7,
    "works_years": 15,
}

EXAMPLE_INPUT = {
    "price": 150000,
    "fees": 12000,
    "works": 0,
    "rent_monthly": 650,
    "charges_annual": 600,
    "property_tax": 700,
    "insurance_annual": 0,
    "management_rate": 0.0,
    "vacancy_months": 0,
    "regime": "micro-foncier",
    "tmi": 0.30,
    "income_year": 2026,
    "tourism_unclassified": False,
    "works_deductible": True,
    "energy_renovation": False,
    "rent_growth": 0.0,
    "charges_growth": 0.0,
    "horizon_years": 10,
    "loan": {"amount": 0, "rate": 0.0, "years": 0, "insurance_rate": 0.0},
    "lmnp": dict(DEFAULT_LMNP),
}


class InputError(ValueError):
    """The input is incomplete or inconsistent; nothing is computed."""


class NotEligibleError(ValueError):
    """The chosen regime does not apply to these inputs; the script refuses to compute."""

    def __init__(self, rule_id: str, message: str) -> None:
        super().__init__(message)
        self.rule_id = rule_id


def _num(value, name: str, minimum: float | None = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{name} must be a number, got {value!r}")
    if minimum is not None and value < minimum:
        raise InputError(f"{name} must be at least {minimum}, got {value}")
    return float(value)


def normalise(raw: dict) -> dict:
    """Validate the input and fill the defaults; returns a new dict."""
    if not isinstance(raw, dict):
        raise InputError("input must be a JSON object")
    inp = deepcopy(raw)
    for key in ("price", "rent_monthly", "regime", "tmi"):
        if key not in inp:
            raise InputError(f"missing required field: {key}")
    cfg = {
        "price": _num(inp["price"], "price", 1.0),
        "fees": _num(inp.get("fees", 0), "fees"),
        "works": _num(inp.get("works", 0), "works"),
        "rent_monthly": _num(inp["rent_monthly"], "rent_monthly"),
        "charges_annual": _num(inp.get("charges_annual", 0), "charges_annual"),
        "property_tax": _num(inp.get("property_tax", 0), "property_tax"),
        "insurance_annual": _num(inp.get("insurance_annual", 0), "insurance_annual"),
        "management_rate": _num(inp.get("management_rate", 0), "management_rate"),
        "vacancy_months": _num(inp.get("vacancy_months", 0), "vacancy_months"),
        "regime": inp["regime"],
        "tmi": _num(inp["tmi"], "tmi"),
        "income_year": int(inp.get("income_year", 2026)),
        "tourism_unclassified": bool(inp.get("tourism_unclassified", False)),
        "works_deductible": bool(inp.get("works_deductible", True)),
        "energy_renovation": bool(inp.get("energy_renovation", False)),
        "rent_growth": _num(inp.get("rent_growth", 0), "rent_growth", None),
        "charges_growth": _num(inp.get("charges_growth", 0), "charges_growth", None),
        "horizon_years": int(inp.get("horizon_years", 10)),
    }
    if cfg["regime"] not in REGIMES:
        raise InputError(f"regime must be one of {REGIMES}, got {cfg['regime']!r}")
    if round(cfg["tmi"], 4) not in VALID_TMI:
        raise InputError(f"tmi must be one of {VALID_TMI} (R9), got {cfg['tmi']}")
    if cfg["management_rate"] >= 1 or cfg["vacancy_months"] > 12:
        raise InputError("management_rate must be below 1 and vacancy_months at most 12")
    if not 1 <= cfg["horizon_years"] <= 40:
        raise InputError("horizon_years must be between 1 and 40")

    loan = inp.get("loan") or {}
    cfg["loan"] = {
        "amount": _num(loan.get("amount", 0), "loan.amount"),
        "rate": _num(loan.get("rate", 0), "loan.rate"),
        "years": int(loan.get("years", 0)),
        "insurance_rate": _num(loan.get("insurance_rate", 0), "loan.insurance_rate"),
    }
    if cfg["loan"]["amount"] > 0 and cfg["loan"]["years"] <= 0:
        raise InputError("loan.years must be positive when loan.amount is positive")

    lmnp = dict(DEFAULT_LMNP)
    lmnp.update(inp.get("lmnp") or {})
    cfg["lmnp"] = {
        "land_share": _num(lmnp["land_share"], "lmnp.land_share"),
        "building_years": int(lmnp["building_years"]),
        "furniture_value": _num(lmnp["furniture_value"], "lmnp.furniture_value"),
        "furniture_years": int(lmnp["furniture_years"]),
        "works_years": int(lmnp["works_years"]),
    }
    if not 0 <= cfg["lmnp"]["land_share"] < 1:
        raise InputError("lmnp.land_share must be between 0 and 1")
    for key in ("building_years", "furniture_years", "works_years"):
        if cfg["lmnp"][key] <= 0:
            raise InputError(f"lmnp.{key} must be positive")
    return cfg


def micro_bic_terms(
    income_year: int, tourism_unclassified: bool
) -> tuple[float, float, str | None]:
    """Ceiling and allowance of the micro-BIC regime for one income year (R5)."""
    if tourism_unclassified:
        ceiling, abatement = MICRO_BIC_TOURISM_UNCLASSIFIED
        return ceiling, abatement, None
    if income_year <= 2025:
        ceiling, abatement = MICRO_BIC_LONG_TERM[2025]
        return ceiling, abatement, None
    ceiling, abatement = MICRO_BIC_LONG_TERM[2026]
    note = None
    if income_year > 2026:
        note = (
            f"Micro-BIC ceiling for income year {income_year} was not published on "
            f"{READ_ON}; the 2026 ceiling of {ceiling:.0f} EUR is used."
        )
    return ceiling, abatement, note


def loan_schedule(loan: dict, horizon: int) -> tuple[list[dict], float, float]:
    """Constant-payment loan: per-year interest, principal, insurance, payments, balance."""
    amount, rate, years = loan["amount"], loan["rate"], loan["years"]
    if amount <= 0:
        rows = [
            {
                "year": y,
                "interest": 0.0,
                "principal": 0.0,
                "insurance": 0.0,
                "payments": 0.0,
                "remaining": 0.0,
            }
            for y in range(1, horizon + 1)
        ]
        return rows, 0.0, 0.0
    n = years * 12
    r = rate / 12
    payment = amount / n if r == 0 else amount * r / (1 - (1 + r) ** (-n))
    insurance_monthly = amount * loan["insurance_rate"] / 12
    remaining = amount
    rows = []
    for year in range(1, horizon + 1):
        interest = principal = 0.0
        months = 0
        for m in range(12):
            if (year - 1) * 12 + m >= n or remaining <= 1e-9:
                break
            i = remaining * r
            p = min(payment - i, remaining)
            interest += i
            principal += p
            remaining -= p
            months += 1
        rows.append(
            {
                "year": year,
                "interest": interest,
                "principal": principal,
                "insurance": insurance_monthly * months,
                "payments": payment * months,
                "remaining": max(remaining, 0.0),
            }
        )
    return rows, payment, insurance_monthly


def _apply_carry(carry: list[dict], amount: float, year: int) -> tuple[float, float]:
    """Use carried-forward deficits (oldest first, ten-year life) against a positive result."""
    used = 0.0
    for item in carry:
        if item["amount"] <= 0 or year - item["year"] > DEFICIT_CARRY_YEARS:
            continue
        take = min(item["amount"], amount - used)
        item["amount"] -= take
        used += take
        if used >= amount:
            break
    carry[:] = [c for c in carry if c["amount"] > 1e-9 and year - c["year"] <= DEFICIT_CARRY_YEARS]
    return amount - used, used


def _depreciation(cfg: dict, year: int) -> float:
    """Linear depreciation available in one year under LMNP reel (A4)."""
    lmnp = cfg["lmnp"]
    base_building = (cfg["price"] + cfg["fees"]) * (1 - lmnp["land_share"])
    total = 0.0
    if year <= lmnp["building_years"]:
        total += base_building / lmnp["building_years"]
    if cfg["works"] > 0 and year <= lmnp["works_years"]:
        total += cfg["works"] / lmnp["works_years"]
    if lmnp["furniture_value"] > 0 and year <= lmnp["furniture_years"]:
        total += lmnp["furniture_value"] / lmnp["furniture_years"]
    return total


def compute(raw: dict) -> dict:
    """Full computation; raises InputError or NotEligibleError instead of guessing."""
    cfg = normalise(raw)
    regime = cfg["regime"]
    tmi = cfg["tmi"]
    horizon = cfg["horizon_years"]
    warnings: list[str] = []
    rules_used: list[str] = ["R4", "R9"]
    total_cost = cfg["price"] + cfg["fees"] + cfg["works"]
    rent_full_y1 = cfg["rent_monthly"] * 12
    loan_rows, payment, insurance_monthly = loan_schedule(cfg["loan"], horizon)

    # Eligibility checks happen on year-1 receipts; the yearly loop re-checks each year.
    if regime == "micro-foncier":
        rules_used += ["R1"]
    elif regime == "reel-foncier":
        rules_used += ["R2", "R3"]
    elif regime == "lmnp-micro-bic":
        rules_used += ["R5", "R6"]
    else:
        rules_used += ["R2", "R6", "R7", "R8"]

    foncier_carry: list[dict] = []
    bic_carry: list[dict] = []
    amort_carry = 0.0
    years: list[dict] = []
    deficit_cap = (
        DEFICIT_FONCIER_ENERGY_CAP if cfg["energy_renovation"] else DEFICIT_FONCIER_GLOBAL_CAP
    )

    for year in range(1, horizon + 1):
        growth = (1 + cfg["rent_growth"]) ** (year - 1)
        cgrowth = (1 + cfg["charges_growth"]) ** (year - 1)
        rent_full = rent_full_y1 * growth
        rent = rent_full * (1 - cfg["vacancy_months"] / 12)
        management = cfg["management_rate"] * rent
        opex = (cfg["charges_annual"] + cfg["property_tax"] + cfg["insurance_annual"]) * cgrowth
        opex += management
        lr = loan_rows[year - 1]
        interest = lr["interest"]
        works_year = cfg["works"] if (year == 1 and cfg["works_deductible"]) else 0.0
        income_year = cfg["income_year"] + year - 1

        taxable = 0.0
        global_deduction = 0.0
        depreciation_used = 0.0
        carry_used = 0.0
        carry_added = 0.0

        if regime == "micro-foncier":
            if rent > MICRO_FONCIER_CEILING:
                raise NotEligibleError(
                    "R1",
                    f"year {year}: gross property income {rent:.2f} EUR exceeds the "
                    f"micro-foncier ceiling of {MICRO_FONCIER_CEILING:.0f} EUR (R1); "
                    "use regime 'reel-foncier'.",
                )
            taxable = rent * (1 - MICRO_FONCIER_ABATEMENT)

        elif regime == "reel-foncier":
            other_charges = opex + works_year
            base = rent - interest
            interest_excess = max(0.0, -base)
            result = base - other_charges if base >= 0 else -other_charges
            if result >= 0:
                taxable, carry_used = _apply_carry(foncier_carry, result, year)
            else:
                deficit_other = -result
                global_deduction = min(deficit_other, deficit_cap)
                carry_added = deficit_other - global_deduction
            carry_added += interest_excess
            if carry_added > 0:
                foncier_carry.append({"year": year, "amount": carry_added})

        elif regime == "lmnp-micro-bic":
            ceiling, abatement, note = micro_bic_terms(income_year, cfg["tourism_unclassified"])
            if note and note not in warnings:
                warnings.append(note)
            if rent > ceiling:
                raise NotEligibleError(
                    "R5",
                    f"year {year}: receipts {rent:.2f} EUR exceed the micro-BIC ceiling of "
                    f"{ceiling:.0f} EUR for income year {income_year} (R5); use 'lmnp-reel'.",
                )
            taxable = rent * (1 - abatement)

        else:  # lmnp-reel
            charges = opex + interest + works_year * 0  # works are depreciated, not expensed
            available = _depreciation(cfg, year) + amort_carry
            cap = max(0.0, rent - charges)
            depreciation_used = min(available, cap)
            amort_carry = available - depreciation_used
            result = rent - charges - depreciation_used
            if result >= 0:
                taxable, carry_used = _apply_carry(bic_carry, result, year)
            else:
                carry_added = -result
                bic_carry.append({"year": year, "amount": carry_added})

        if regime in ("lmnp-micro-bic", "lmnp-reel") and rent > LMNP_STATUS_CEILING:
            msg = (
                f"year {year}: receipts {rent:.2f} EUR exceed 23 000 EUR; the LMNP status then "
                "depends on the household's other earned income (R6), not checked here."
            )
            if msg not in warnings:
                warnings.append(msg)

        income_tax = taxable * tmi
        tax_saving = global_deduction * tmi
        social = taxable * SOCIAL_RATE
        total_tax = income_tax + social - tax_saving
        cash_before_tax = rent - opex - lr["payments"] - lr["insurance"]
        cash_after_tax = cash_before_tax - total_tax
        years.append(
            {
                "year": year,
                "income_year": income_year,
                "rent": rent,
                "operating_charges": opex,
                "works_deducted": works_year,
                "loan_interest": interest,
                "loan_principal": lr["principal"],
                "loan_insurance": lr["insurance"],
                "depreciation_used": depreciation_used,
                "depreciation_carried": amort_carry,
                "carry_used": carry_used,
                "carry_added": carry_added,
                "deficit_on_global_income": global_deduction,
                "taxable_income": taxable,
                "income_tax": income_tax,
                "tax_saving_global_income": tax_saving,
                "social_contributions": social,
                "total_tax": total_tax,
                "cash_flow_before_tax": cash_before_tax,
                "cash_flow_after_tax": cash_after_tax,
                "loan_remaining": lr["remaining"],
            }
        )

    y1 = years[0]
    noi_y1 = y1["rent"] - y1["operating_charges"]
    after_tax_yields = [
        (y["rent"] - y["operating_charges"] - y["total_tax"]) / total_cost for y in years
    ]
    summary = {
        "total_cost": total_cost,
        "annual_rent": rent_full_y1,
        "gross_yield_pct": 100 * rent_full_y1 / cfg["price"],
        "net_yield_pct": 100 * noi_y1 / total_cost,
        "net_after_tax_yield_year1_pct": 100 * after_tax_yields[0],
        "net_after_tax_yield_avg_pct": 100 * sum(after_tax_yields) / len(after_tax_yields),
        "monthly_loan_payment": payment,
        "monthly_loan_insurance": insurance_monthly,
        "monthly_cash_flow_before_tax_year1": y1["cash_flow_before_tax"] / 12,
        "monthly_cash_flow_after_tax_year1": y1["cash_flow_after_tax"] / 12,
        "monthly_cash_flow_after_tax_avg": sum(y["cash_flow_after_tax"] for y in years)
        / (12 * len(years)),
        "total_tax_over_horizon": sum(y["total_tax"] for y in years),
        "taxable_income_year1": y1["taxable_income"],
        "total_tax_year1": y1["total_tax"],
        "equity_invested": max(0.0, total_cost - cfg["loan"]["amount"]),
    }
    return {
        "script": "rendement.py",
        "rules_read_on": READ_ON,
        "inputs": cfg,
        "regime": regime,
        "rules_applied": [{"id": r, **RULES[r]} for r in sorted(set(rules_used))],
        "assumptions": ASSUMPTIONS,
        "warnings": warnings,
        "summary": _round(summary),
        "years": [_round(y) for y in years],
        "disclaimer": "Calculation only, not personalised advice: the reader decides.",
    }


def _round(d: dict) -> dict:
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}


def to_markdown(result: dict) -> str:
    s = result["summary"]
    lines = [
        f"## Rental yield ({result['regime']}, rules read on {result['rules_read_on']})",
        "",
        "| Indicator | Value |",
        "| --- | ---: |",
        f"| Total cost (price + fees + works) | {s['total_cost']:,.2f} EUR |",
        f"| Annual rent | {s['annual_rent']:,.2f} EUR |",
        f"| Gross yield | {s['gross_yield_pct']:.2f} % |",
        f"| Net yield (before tax) | {s['net_yield_pct']:.2f} % |",
        f"| Net yield after tax, year 1 | {s['net_after_tax_yield_year1_pct']:.2f} % |",
        f"| Net yield after tax, average over horizon | {s['net_after_tax_yield_avg_pct']:.2f} % |",
        "| Monthly loan payment (+ insurance) | "
        f"{s['monthly_loan_payment']:,.2f} (+ {s['monthly_loan_insurance']:,.2f}) EUR |",
        "| Monthly cash flow before tax, year 1 | "
        f"{s['monthly_cash_flow_before_tax_year1']:,.2f} EUR |",
        "| Monthly cash flow after tax, year 1 | "
        f"{s['monthly_cash_flow_after_tax_year1']:,.2f} EUR |",
        "| Monthly cash flow after tax, average | "
        f"{s['monthly_cash_flow_after_tax_avg']:,.2f} EUR |",
        f"| Total tax over horizon | {s['total_tax_over_horizon']:,.2f} EUR |",
        "",
        "| Year | Rent | Charges | Interest | Depreciation | Taxable | Total tax "
        "| Cash flow after tax | Loan left |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for y in result["years"]:
        lines.append(
            f"| {y['year']} | {y['rent']:,.0f} | {y['operating_charges']:,.0f} | "
            f"{y['loan_interest']:,.0f} | {y['depreciation_used']:,.0f} | "
            f"{y['taxable_income']:,.0f} | {y['total_tax']:,.0f} | "
            f"{y['cash_flow_after_tax']:,.0f} | {y['loan_remaining']:,.0f} |"
        )
    lines += ["", "Rules applied:"]
    for r in result["rules_applied"]:
        lines.append(f"- {r['id']}: {r['text']} Source: {r['source']}.")
    lines += ["", "Assumptions:"]
    lines += [f"- {a}" for a in result["assumptions"]]
    if result["warnings"]:
        lines += ["", "Warnings:"]
        lines += [f"- {w}" for w in result["warnings"]]
    lines += ["", result["disclaimer"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--input", help="JSON file with the inputs, or - for stdin")
    parser.add_argument("--markdown", action="store_true", help="print tables instead of JSON")
    parser.add_argument("--example", action="store_true", help="print an example input and exit")
    args = parser.parse_args(argv)
    if args.example:
        print(json.dumps(EXAMPLE_INPUT, indent=2))
        return 0
    if not args.input:
        parser.error("--input is required (or --example)")
    raw = (
        json.load(sys.stdin) if args.input == "-" else json.load(open(args.input, encoding="utf-8"))
    )
    try:
        result = compute(raw)
    except NotEligibleError as exc:
        rule = RULES.get(exc.rule_id, {})
        print(
            json.dumps(
                {
                    "error": "not_eligible",
                    "rule": exc.rule_id,
                    "message": str(exc),
                    "rule_text": rule.get("text"),
                    "source": rule.get("source"),
                    "rules_read_on": READ_ON,
                },
                indent=2,
            )
        )
        return 2
    except InputError as exc:
        print(json.dumps({"error": "invalid_input", "message": str(exc)}, indent=2))
        return 3
    print(
        to_markdown(result) if args.markdown else json.dumps(result, indent=2, ensure_ascii=False)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
