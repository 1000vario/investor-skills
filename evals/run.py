#!/usr/bin/env python3
"""Eval runner for investor-skills: the same cases, with and without the skill.

Arms
    dry-run        the skill's script alone on the case inputs, graded against the hand truth.
                   No model, no network, no key. CI runs it on every push.
    with_skill     the model reads SKILL.md and calls the script through one tool, run_script.
    without_skill  the model gets the same question and the same raw data, no skill, no tool.
    bare           the model gets the question only, no data.

Live arms need ANTHROPIC_API_KEY and spend money; CI never runs them. The anthropic SDK is
imported only when a live arm runs, so the dry run works on a bare Python.

Each case holds a hand-written truth with its date and its sources (evals/cases/<skill>/*.json).
Grading per case: numeric accuracy within the case tolerance, absence of invention, presence of
sources and dates, wall time and cost from the price table below. The with/without gap is the
figure of the case study.

Usage
    python evals/run.py --dry-run                              all skills, script arm only
    python evals/run.py --skill rental-yield-france --arm with_skill --arm without_skill
    python evals/run.py --report                               table of evals/results/*.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "evals" / "cases"
RESULTS_DIR = ROOT / "evals" / "results"
SKILLS_DIR = ROOT / "skills"

SKILL_NAMES = ["rental-yield-france", "land-due-diligence-france", "french-company-accounts"]
SCRIPTS = {
    "rental-yield-france": "scripts/rendement.py",
    "land-due-diligence-france": "scripts/due_diligence.py",
    "french-company-accounts": "scripts/comptes.py",
}
RECORDED_DIR = "evals/cases/french-company-accounts/recorded"
LIVE_ARMS = ["with_skill", "without_skill", "bare"]
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000
TOOL_ROUNDS = 6
SCRIPT_TIMEOUT_S = 120
TOOL_OUTPUT_LIMIT = 30000

# USD per million tokens: input, cache write (5 minutes), cache read, output.
# Read on platform.claude.com, pricing page, on 2026-10-11.
PRICES_READ_ON = "2026-10-11"
PRICES_USD_PER_MTOK: dict[str, tuple[float, float, float, float]] = {
    "claude-fable-5-1": (10.0, 12.5, 0.25, 50.0),
    "claude-opus-5-5": (4.0, 5.0, 0.20, 20.0),
    "claude-opus-5": (5.0, 6.25, 0.50, 25.0),
    "claude-sonnet-5-5": (2.0, 2.5, 0.10, 10.0),
    "claude-sonnet-5": (2.0, 2.5, 0.20, 10.0),
    "claude-haiku-5-5": (0.10, 0.125, 0.01, 0.50),
    "claude-haiku-4-5": (1.0, 1.25, 0.10, 5.0),
}

DATE_RE = re.compile(
    r"(20\d\d-\d\d-\d\d|\b\d{1,2}/\d{2}/20\d\d\b|\b\d{1,2}(?:er)? (?:janvier|f[eé]vrier|mars|avril"
    r"|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre) 20\d\d\b)",
    re.I,
)
JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
OFFICIAL_SOURCE_RE = re.compile(
    r"service-public|legifrance|bofip|impots\.gouv|CGI|code g[eé]n[eé]ral des imp[oô]ts"
    r"|data\.economie\.gouv|recherche-entreprises|annuaire|INPI|ratios_inpi_bce"
    r"|dvf|apicarto|gpu|ademe|georisques|geoplateforme|foncier",
    re.I,
)

# Rule values a correct rental answer may quote (evals/cases/rental-yield-france/_rule_values.json).
# The warning rule R6 has no id outside the skill: its signature is the 23 000 EUR ceiling.
WARNING_SIGNATURES = {"R6": r"23 ?000"}

RENTAL_FIGURES = {
    "summary.gross_yield_pct": (
        "gross yield in %: rent over 12 months / purchase price, fees and works excluded"
    ),
    "summary.net_yield_pct": (
        "net yield in %: (rent collected - owner charges - property tax - insurance - management"
        " fees) / (price + fees + works)"
    ),
    "summary.taxable_income_year1": (
        "taxable rental income of year 1 in EUR, after the allowance or the deductions of the"
        " regime"
    ),
    "summary.total_tax_year1": (
        "income tax at the marginal rate plus social contributions on year 1 rental income, in EUR"
        " (a negative value is a saving)"
    ),
    "summary.net_after_tax_yield_year1_pct": (
        "(net income of year 1 - total tax of year 1) / (price + fees + works), in %"
    ),
    "summary.monthly_cash_flow_after_tax_year1": (
        "(net income - loan payments - loan insurance - total tax) of year 1 / 12, in EUR"
    ),
    "summary.monthly_loan_payment": "constant monthly loan payment, principal and interest, in EUR",
    "summary.monthly_loan_insurance": "monthly loan insurance in EUR: amount x annual rate / 12",
    "years[0].rent": "rent collected in year 1 in EUR, vacancy months deducted",
    "years[0].operating_charges": (
        "owner charges + property tax + insurance + management fees of year 1, in EUR"
    ),
    "years[0].loan_interest": "loan interest paid in year 1 in EUR",
    "years[0].deficit_on_global_income": (
        "property deficit set against global income in year 1 in EUR (positive number)"
    ),
    "years[0].tax_saving_global_income": "income tax saved by that deficit in year 1 in EUR",
    "years[0].carry_added": (
        "deficit of year 1 carried forward to later property income, in EUR (positive number)"
    ),
    "years[0].income_tax": "income tax on the rental income of year 1 in EUR",
    "years[0].social_contributions": "social contributions on the rental income of year 1 in EUR",
    "years[1].total_tax": "income tax plus social contributions of year 2 in EUR",
    "years[2].total_tax": "income tax plus social contributions of year 3 in EUR",
    "years[0].depreciation_used": "depreciation deducted in year 1 in EUR (LMNP reel)",
    "years[0].depreciation_carried": (
        "depreciation of year 1 not deductible and carried forward, in EUR"
    ),
    "years[1].depreciation_carried": (
        "cumulative depreciation carried forward at the end of year 2, in EUR"
    ),
}

LAND_FLAG_CODES = {
    "sales_unavailable": ("red", "sales data missing or area not covered"),
    "no_comparable_sale": ("red", "no usable dwelling sale in the radius"),
    "not_constructible": ("red", "zone or sector not constructible"),
    "dpe_f_or_g": ("red", "latest DPE class F or G"),
    "flood_zone": ("red", "flood zoning at the point"),
    "asking_above_median": ("orange", "asking price per m2 well above the median of comparables"),
    "few_comparables": ("orange", "too few comparable sales"),
    "zoning_unavailable": ("orange", "zoning section missing"),
    "zoning_unknown": ("orange", "no zone found at the point"),
    "rnu": ("orange", "commune without a planning document (RNU)"),
    "zone_to_be_urbanised": ("orange", "zone to be urbanised (AU)"),
    "easement": ("orange", "public utility easement (info when not a risk prevention plan)"),
    "dpe_e": ("orange", "latest DPE class E"),
    "risks_unavailable": ("orange", "risks section missing"),
    "point_risks_unavailable": ("orange", "point risks missing"),
    "flood_unknown": ("orange", "flood block missing"),
    "clay_strong": ("orange", "strong clay shrink-swell exposure"),
    "seveso_nearby": ("orange", "Seveso installation nearby"),
    "underground_cavities_500m": ("orange", "underground cavities within 500 m"),
    "ground_movements_500m": ("orange", "ground movements within 500 m"),
    "tool_error": ("orange", "an error entry in the data"),
    "asking_below_median": ("info", "asking price per m2 well below the median"),
    "outlier": ("info", "an outlier among comparables"),
    "parcel_sold_before": ("info", "the parcel itself appears in the sales"),
    "prescriptions": ("info", "planning prescriptions apply"),
    "dpe_unavailable": ("info", "no address match for the DPE lookup"),
    "dpe_none": ("info", "no DPE found"),
    "dpe_several": ("info", "several DPE with different classes"),
    "clay_medium": ("info", "medium clay exposure"),
    "icpe_nearby": ("info", "classified installations nearby, not Seveso"),
    "catnat_orders": ("info", "natural disaster orders in the commune"),
    "radon_high": ("info", "radon potential class 3"),
    "commune_in_tri": ("info", "commune inside a flood risk territory"),
}

RATIO_NAMES = [
    "marge_ebe",
    "resultat_courant_avant_impots_sur_ca",
    "caf_sur_ca",
    "taux_d_endettement",
    "autonomie_financiere",
    "poids_bfr_exploitation_sur_ca",
    "poids_bfr_exploitation_sur_ca_jours",
    "credit_clients_jours",
    "credit_fournisseurs_jours",
    "rotation_des_stocks_jours",
    "ratio_de_liquidite",
    "couverture_des_interets",
    "capacite_de_remboursement",
    "ratio_de_vetuste",
]

HARNESS = (
    "You are answering an evaluation question about French property or company figures, for a "
    "reader who will act on the answer. Answer in French. Tie every figure to its source and to "
    "the date you read it. If a figure cannot be established from the material you have, say so "
    "and put null in the JSON: an invented figure counts against you, a null does not. Give no "
    "personalised advice: describe, the reader decides. End with one fenced JSON block "
    "(```json ... ```) and nothing after it, with exactly this shape:\n\n{schema}"
)

RENTAL_SCHEMA = """{"refused": false, "rule": null,
 "figures": {"<requested key>": number or null, ...},
 "fiscal_rules": [{"name": "...", "value": number, "source": "..."}],
 "warnings": ["..."],
 "sources": ["..."]}

Requested keys for this case and their definitions:
{definitions}

"refused": true, with "rule" naming the ceiling or rule that forbids it, when the regime asked
for is not allowed; figures may then be null.
"fiscal_rules": every ceiling, allowance, rate or duration you used, its value as a number (EUR,
percent or years) and its source."""

LAND_SCHEMA = """{"counts": {"red": n, "orange": n, "info": n},
 "flags": ["<code>", ...],
 "figures": {"count": n, "median_eur_m2": x, "mean_eur_m2": x, "min_eur_m2": x, "max_eur_m2": x,
             "gap_to_median_pct": x},
 "sources": [{"id": "<source id as written in the data>", "date": "YYYY-MM-DD"}]}

"flags": codes from this vocabulary only, each with its severity:
{codes}
"figures": over the comparable sales of dwellings in the radius (nature Vente, with a built area
of Maison or Appartement), price per m2 of built area; gap_to_median_pct = (asking price /
surface) / median - 1, in percent; null without an asking price or without comparables."""

ACCOUNTS_SCHEMA = """{"figures": {"latest_closing_date": "YYYY-MM-DD", "type_bilan": "C, S or K",
             "turnover_eur": n, "net_result_eur": n, "net_margin_pct": x,
             "ratio_name": "<name>", "ratio_value": x},
 "previous_closings": [{"closing_date": "YYYY-MM-DD", "type_bilan": "C, S or K",
                        "turnover_eur": n, "net_result_eur": n}],
 "anomalies_noted": ["..."],
 "sources": [{"url": "...", "date": "YYYY-MM-DD"}],
 "reading": ["five short sentences"]}

type_bilan: C complete annual accounts, S simplified, K consolidated, as published in the INPI
ratios dataset. net_margin_pct = net result / turnover x 100, two decimals, on the latest
closing. ratio_name: one of {ratios}, with its published value."""


# ----------------------------------------------------------------------------- helpers


def isnum(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


def dig(data: Any, path: str) -> Any:
    """dig(d, "years[0].total_tax") reads d["years"][0]["total_tax"]; None when absent."""
    cur = data
    for token in path.split("."):
        m = re.fullmatch(r"([a-zA-Z_][a-zA-Z0-9_]*)(?:\[(\d+)\])?", token)
        if not m or not isinstance(cur, dict) or m.group(1) not in cur:
            return None
        cur = cur[m.group(1)]
        if m.group(2) is not None:
            idx = int(m.group(2))
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
    return cur


def check(name: str, expected: Any, got: Any, ok: bool | None = None) -> dict[str, Any]:
    if ok is None:
        ok = got == expected
    return {"name": name, "expected": expected, "got": got, "ok": bool(ok)}


def within(got: Any, expected: Any, tol: float) -> bool:
    return isnum(got) and isnum(expected) and abs(got - expected) <= tol


def within_rel(got: Any, expected: Any, rel: float) -> bool:
    if not (isnum(got) and isnum(expected)):
        return False
    if expected == 0:
        return abs(got) <= 0.5
    return abs(got - expected) / abs(expected) <= rel


def parse_json_block(text: str) -> dict[str, Any] | None:
    """Last fenced JSON block of a model answer, or None."""
    for block in reversed(JSON_BLOCK_RE.findall(text or "")):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def cost_usd(model: str, usage: dict[str, int]) -> float | None:
    prices = PRICES_USD_PER_MTOK.get(model)
    if prices is None:
        return None
    p_in, p_cw, p_cr, p_out = prices
    return round(
        (
            usage.get("input_tokens", 0) * p_in
            + usage.get("cache_creation_input_tokens", 0) * p_cw
            + usage.get("cache_read_input_tokens", 0) * p_cr
            + usage.get("output_tokens", 0) * p_out
        )
        / 1_000_000,
        4,
    )


def load_cases(skill: str, max_cases: int | None = None) -> list[dict[str, Any]]:
    files = sorted(p for p in (CASES_DIR / skill).glob("*.json") if not p.name.startswith("_"))
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    return cases[:max_cases] if max_cases else cases


def load_rule_values() -> list[float]:
    path = CASES_DIR / "rental-yield-france" / "_rule_values.json"
    values = json.loads(path.read_text(encoding="utf-8"))["values"]
    flat: list[float] = []
    for v in values.values():
        flat.extend(v if isinstance(v, list) else [v])
    return [float(x) for x in flat]


def known_rule_value(v: Any, rule_values: list[float]) -> bool:
    if not isnum(v):
        return True
    return any(
        abs(cand - x) <= 0.011
        for x in rule_values
        for cand in (v, v * 100, v / 100)
        if x is not None
    )


# ----------------------------------------------------------------------------- scripts


def script_args(skill: str, case: dict[str, Any]) -> tuple[list[str], str | None]:
    inp = case["inputs"]
    if skill == "rental-yield-france":
        return ["--input", "-"], json.dumps(inp)
    if skill == "land-due-diligence-france":
        args = ["--input", inp["fixture"]]
        if inp.get("asking_price") is not None:
            args += ["--asking-price", str(inp["asking_price"])]
        if inp.get("surface") is not None:
            args += ["--surface", str(inp["surface"])]
        if inp.get("property_type"):
            args += ["--property-type", inp["property_type"]]
        return args, None
    args = ["--siren", inp["siren"], "--recorded", RECORDED_DIR]
    if inp.get("social"):
        args.append("--social")
    return args, None


def run_script(
    skill: str, args: list[str], stdin: str | None = None
) -> tuple[int, str, str, float]:
    cmd = [sys.executable, "-I", str(SKILLS_DIR / skill / SCRIPTS[skill]), *args]
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=SCRIPT_TIMEOUT_S,
    )
    return proc.returncode, proc.stdout, proc.stderr, time.perf_counter() - t0


def answer_from_script(skill: str, case: dict[str, Any], rc: int, out: str) -> dict[str, Any]:
    """The script output rewritten in the answer schema, so one grader serves every arm."""
    data = json.loads(out)
    if skill == "rental-yield-france":
        if rc == 2:
            return {
                "refused": True,
                "rule": data.get("rule"),
                "figures": {},
                "fiscal_rules": [],
                "warnings": [data.get("message", "")],
                "sources": [data.get("source")] if data.get("source") else [],
                "_dates": [data.get("rules_read_on")],
            }
        return {
            "refused": False,
            "rule": None,
            "figures": {k: dig(data, k) for k in case["expected"]},
            "fiscal_rules": [],
            "warnings": list(data.get("warnings") or []),
            "sources": [r.get("source", "") for r in data.get("rules_applied") or []],
            "_dates": [data.get("rules_read_on")],
        }
    if skill == "land-due-diligence-france":
        st = data["comparables"].get("stats") or {}
        srcs = data.get("sources") or {}
        if isinstance(srcs, dict):
            sources = [{"id": k, "date": None} for k in srcs]
        else:
            sources = [{"id": s.get("id"), "date": s.get("retrieved_at")} for s in srcs]
        return {
            "counts": data["counts"],
            "flags": [f["code"] for f in data["flags"]],
            "figures": {
                "count": st.get("count"),
                "median_eur_m2": st.get("median_eur_m2"),
                "mean_eur_m2": st.get("mean_eur_m2"),
                "min_eur_m2": st.get("min_eur_m2"),
                "max_eur_m2": st.get("max_eur_m2"),
                "gap_to_median_pct": data["asking"].get("gap_to_median_pct"),
            },
            "sources": sources,
            "_messages": [f["message"] for f in data["flags"]],
            "_dates": [f.get("retrieved_at") for f in data["flags"]] + [data.get("generated_at")],
        }
    exercices = data.get("exercices") or []
    last = exercices[-1] if exercices else {}
    items = last.get("items_eur") or {}
    turnover = items.get("chiffre_d_affaires")
    net = items.get("resultat_net")
    margin = last.get("net_margin_pct")
    if margin is None and isnum(turnover) and isnum(net) and turnover:
        margin = round(net / turnover * 100, 2)
    ratio_name = (case["expected"].get("ratio") or {}).get("name")
    ratio_value = (last.get("ratios_published") or {}).get(ratio_name) if ratio_name else None
    return {
        "figures": {
            "latest_closing_date": last.get("closing_date"),
            "type_bilan": last.get("type_bilan"),
            "turnover_eur": turnover,
            "net_result_eur": net,
            "net_margin_pct": margin,
            "ratio_name": ratio_name,
            "ratio_value": ratio_value,
        },
        "previous_closings": [
            {
                "closing_date": e.get("closing_date"),
                "type_bilan": e.get("type_bilan"),
                "turnover_eur": (e.get("items_eur") or {}).get("chiffre_d_affaires"),
                "net_result_eur": (e.get("items_eur") or {}).get("resultat_net"),
            }
            for e in exercices[:-1]
        ],
        "anomalies_noted": list(data.get("anomalies") or []),
        "sources": [
            {"url": s.get("url"), "date": s.get("fetched_on")} for s in data.get("sources") or []
        ],
        "reading": [],
        "_dates": [data.get("fetched_on")],
    }


# ----------------------------------------------------------------------------- grading


def new_grade(case: dict[str, Any], arm: str) -> dict[str, Any]:
    return {
        "id": case["id"],
        "title": case.get("title"),
        "arm": arm,
        "ok": False,
        "checks": [],
        "numeric_pass": 0,
        "numeric_total": 0,
        "inventions": [],
        "must_mention_missing": None,
        "sources_ok": False,
        "dates_ok": False,
        "seconds": 0.0,
        "cost_usd": 0.0,
        "usage": None,
        "error": None,
    }


def finish_grade(grade: dict[str, Any], ans: dict[str, Any], text: str, model_arm: bool) -> None:
    numeric = [c for c in grade["checks"] if c.get("numeric")]
    grade["numeric_pass"] = sum(1 for c in numeric if c["ok"])
    grade["numeric_total"] = len(numeric)
    sources = ans.get("sources") or []
    joined = " ".join(json.dumps(s, ensure_ascii=False) for s in sources)
    grade["sources_ok"] = bool(sources) and bool(OFFICIAL_SOURCE_RE.search(joined + " " + text))
    dates = " ".join(str(d) for d in ans.get("_dates") or [] if d) + " " + joined + " " + text
    grade["dates_ok"] = bool(DATE_RE.search(dates))
    missing = grade["must_mention_missing"]
    grade["ok"] = (
        all(c["ok"] for c in grade["checks"])
        and not grade["inventions"]
        and not missing
        and (not model_arm or (grade["sources_ok"] and grade["dates_ok"]))
    )


def must_mention_missing(case: dict[str, Any], text: str) -> list[str]:
    return [rx for rx in case.get("must_mention") or [] if not re.search(rx, text, re.I)]


def grade_rental(
    case: dict[str, Any],
    ans: dict[str, Any],
    text: str,
    arm: str,
    rule_values: list[float],
) -> dict[str, Any]:
    grade = new_grade(case, arm)
    model_arm = arm != "dry-run"
    figures = ans.get("figures") or {}
    if case.get("expect_refusal"):
        c = check("refused", True, bool(ans.get("refused")))
        c["numeric"] = True
        grade["checks"].append(c)
    else:
        for key, spec in case["expected"].items():
            got = figures.get(key)
            c = check(key, spec["value"], got, within(got, spec["value"], spec["tolerance"]))
            c["numeric"] = True
            c["tolerance"] = spec["tolerance"]
            grade["checks"].append(c)
        if ans.get("refused"):
            grade["checks"].append(check("not refused", False, True, False))
    rule = case.get("expect_warning")
    if rule:
        hay = " ".join(str(w) for w in ans.get("warnings") or []) + " " + text
        sig = WARNING_SIGNATURES.get(rule, rule)
        ok = bool(re.search(rf"\b{rule}\b", hay)) or bool(re.search(sig, hay, re.I))
        grade["checks"].append(check(f"warning {rule}", True, ok, ok))
    for fr in ans.get("fiscal_rules") or []:
        v = fr.get("value") if isinstance(fr, dict) else None
        if not known_rule_value(v, rule_values):
            grade["inventions"].append({"rule_value": fr})
    if model_arm:
        grade["must_mention_missing"] = must_mention_missing(case, text)
    finish_grade(grade, ans, text, model_arm)
    return grade


def grade_land(case: dict[str, Any], ans: dict[str, Any], text: str, arm: str) -> dict[str, Any]:
    grade = new_grade(case, arm)
    model_arm = arm != "dry-run"
    exp = case["expected"]
    tol = case["tolerance"]
    figures = ans.get("figures") or {}
    grade["checks"].append(check("counts", exp["counts"], ans.get("counts")))
    got_codes = sorted(str(c) for c in ans.get("flags") or [])
    grade["checks"].append(check("flag_codes", exp["flag_codes"], got_codes))
    for code in got_codes:
        if code not in LAND_FLAG_CODES:
            grade["inventions"].append({"flag_code": code})
    if exp["stats"] is None:
        stated = [k for k in ("count", "median_eur_m2", "mean_eur_m2") if figures.get(k)]
        c = check("stats null", None, {k: figures.get(k) for k in stated}, not stated)
        c["numeric"] = True
        grade["checks"].append(c)
        for k in stated:
            grade["inventions"].append({"figure": k, "value": figures.get(k)})
    else:
        c = check("count", exp["stats"]["count"], figures.get("count"))
        c["numeric"] = True
        grade["checks"].append(c)
        for k in ("median_eur_m2", "mean_eur_m2", "min_eur_m2", "max_eur_m2"):
            c = check(
                k,
                exp["stats"][k],
                figures.get(k),
                within(figures.get(k), exp["stats"][k], tol["eur_m2"]),
            )
            c["numeric"] = True
            grade["checks"].append(c)
    gap = figures.get("gap_to_median_pct")
    if exp["gap_to_median_pct"] is None:
        c = check("gap_to_median_pct", None, gap, gap is None)
        if gap is not None:
            grade["inventions"].append({"figure": "gap_to_median_pct", "value": gap})
    else:
        c = check(
            "gap_to_median_pct",
            exp["gap_to_median_pct"],
            gap,
            within(gap, exp["gap_to_median_pct"], tol["pct_points"]),
        )
    c["numeric"] = True
    grade["checks"].append(c)
    hay = text if model_arm else " ".join(ans.get("_messages") or [])
    for sev in ("red", "orange"):
        for rx in exp.get(f"{sev}_flag_keywords") or []:
            ok = bool(re.search(rx, hay, re.I))
            grade["checks"].append(check(f"{sev} keyword {rx}", True, ok, ok))
    if model_arm:
        grade["must_mention_missing"] = must_mention_missing(case, text)
    finish_grade(grade, ans, text, model_arm)
    return grade


def grade_accounts(
    case: dict[str, Any],
    ans: dict[str, Any],
    text: str,
    arm: str,
    published_ratios: dict[str, Any] | None = None,
) -> dict[str, Any]:
    grade = new_grade(case, arm)
    model_arm = arm != "dry-run"
    exp = case["expected"]
    tol = case["tolerance"]
    f = ans.get("figures") or {}

    def numeric_check(name: str, expected: Any, got: Any, ok: bool) -> None:
        c = check(name, expected, got, ok)
        c["numeric"] = True
        grade["checks"].append(c)
        if got is not None and not ok:
            grade["inventions"].append({"figure": name, "value": got, "expected": expected})

    grade["checks"].append(
        check("latest_closing_date", exp["latest_closing_date"], f.get("latest_closing_date"))
    )
    grade["checks"].append(check("type_bilan", exp["type_bilan"], f.get("type_bilan")))
    numeric_check(
        "turnover_eur",
        exp["turnover_eur"],
        f.get("turnover_eur"),
        within_rel(f.get("turnover_eur"), exp["turnover_eur"], tol["eur_relative"]),
    )
    numeric_check(
        "net_result_eur",
        exp["net_result_eur"],
        f.get("net_result_eur"),
        within_rel(f.get("net_result_eur"), exp["net_result_eur"], tol["eur_relative"]),
    )
    numeric_check(
        "net_margin_pct",
        exp["net_margin_pct"],
        f.get("net_margin_pct"),
        within(f.get("net_margin_pct"), exp["net_margin_pct"], tol["pct_points"]),
    )
    if exp.get("ratio"):
        name, value = f.get("ratio_name"), f.get("ratio_value")
        if name == exp["ratio"]["name"]:
            expected = exp["ratio"]["value"]
        elif published_ratios and name in published_ratios and isnum(published_ratios[name]):
            expected = published_ratios[name]
        else:
            expected = exp["ratio"]["value"]
            name = exp["ratio"]["name"]
        numeric_check(f"ratio {name}", expected, value, within(value, expected, tol["pct_points"]))
    got_prev = {
        p.get("closing_date"): p for p in ans.get("previous_closings") or [] if isinstance(p, dict)
    }
    for prev in exp.get("previous_closings") or []:
        got = got_prev.get(prev["closing_date"]) or {}
        ok = within_rel(
            got.get("turnover_eur"), prev["turnover_eur"], tol["eur_relative"]
        ) and within_rel(got.get("net_result_eur"), prev["net_result_eur"], tol["eur_relative"])
        numeric_check(
            f"closing {prev['closing_date']}",
            {"turnover_eur": prev["turnover_eur"], "net_result_eur": prev["net_result_eur"]},
            {"turnover_eur": got.get("turnover_eur"), "net_result_eur": got.get("net_result_eur")}
            if got
            else None,
            ok,
        )
    hay = " ".join(str(a) for a in ans.get("anomalies_noted") or []) + " " + text
    for needle in case.get("must_flag") or []:
        ok = needle.lower() in hay.lower()
        grade["checks"].append(check(f"flag {needle}", True, ok, ok))
    if model_arm:
        grade["must_mention_missing"] = must_mention_missing(case, text)
    finish_grade(grade, ans, text, model_arm)
    return grade


def grade(
    skill: str,
    case: dict[str, Any],
    ans: dict[str, Any],
    text: str,
    arm: str,
    rule_values: list[float],
    truth: dict[str, Any] | None,
) -> dict[str, Any]:
    if skill == "rental-yield-france":
        return grade_rental(case, ans, text, arm, rule_values)
    if skill == "land-due-diligence-france":
        return grade_land(case, ans, text, arm)
    published = None
    if truth and truth.get("exercices"):
        published = truth["exercices"][-1].get("ratios_published")
    return grade_accounts(case, ans, text, arm, published)


# ----------------------------------------------------------------------------- model arms


def skill_markdown(skill: str) -> str:
    return (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")


def schema_text(skill: str, case: dict[str, Any]) -> str:
    if skill == "rental-yield-france":
        keys = list(case["expected"])
        defs = "\n".join(f"- {k}: {RENTAL_FIGURES.get(k, k)}" for k in keys) or (
            "- none: check first whether the regime asked for is allowed"
        )
        return RENTAL_SCHEMA.replace("{definitions}", defs)
    if skill == "land-due-diligence-france":
        codes = "\n".join(
            f"- {code} ({sev}): {meaning}" for code, (sev, meaning) in LAND_FLAG_CODES.items()
        )
        return LAND_SCHEMA.replace("{codes}", codes)
    return ACCOUNTS_SCHEMA.replace("{ratios}", ", ".join(RATIO_NAMES))


def build_system(skill: str, arm: str, case: dict[str, Any]) -> str:
    system = HARNESS.replace("{schema}", schema_text(skill, case))
    if arm == "with_skill":
        system += (
            f"\n\n# Skill installed: {skill}\n\n{skill_markdown(skill)}\n\n"
            f"The skill folder is skills/{skill}/ at the repository root. The tool run_script runs"
            f" `python -I skills/{skill}/{SCRIPTS[skill]}` with your arguments (paths relative to"
            " the repository root; `--input -` reads the stdin you pass). The tool read_file reads"
            " a file of the skill folder (references, fixtures)."
        )
    return system


def raw_data(skill: str, case: dict[str, Any]) -> str:
    inp = case["inputs"]
    if skill == "rental-yield-france":
        return json.dumps(inp, ensure_ascii=False, indent=1)
    if skill == "land-due-diligence-france":
        return (ROOT / inp["fixture"]).read_text(encoding="utf-8")
    return (CASES_DIR / skill / inp["recorded"]).read_text(encoding="utf-8")


def build_user(skill: str, arm: str, case: dict[str, Any]) -> str:
    question = case["question"]
    inp = case["inputs"]
    if arm == "bare":
        return question
    if arm == "without_skill":
        label = {
            "rental-yield-france": "Structured inputs of the case",
            "land-due-diligence-france": "foncier-mcp response, unchanged",
            "french-company-accounts": (
                "Raw responses of the two public APIs, recorded on 2026-10-11"
            ),
        }[skill]
        return f"{question}\n\n{label}:\n\n```json\n{raw_data(skill, case)}\n```"
    if skill == "rental-yield-france":
        return (
            f"{question}\n\nStructured inputs of the case:\n\n```json\n{raw_data(skill, case)}\n```"
        )
    if skill == "land-due-diligence-france":
        extra = ""
        if inp.get("asking_price") is not None:
            extra = f" Asking price {inp['asking_price']} EUR for {inp['surface']} m2."
        if inp.get("property_type"):
            extra += f" Keep only the type {inp['property_type']} among comparables."
        return f"{question}\n\nThe foncier-mcp response is saved at {inp['fixture']}.{extra}"
    return (
        f"{question}\n\nThe network is unavailable: run the script with"
        f" `--recorded {RECORDED_DIR}` (responses recorded on 2026-10-11)."
    )


def build_tools(skill: str) -> list[dict[str, Any]]:
    return [
        {
            "name": "run_script",
            "description": (
                f"Runs the skill script skills/{skill}/{SCRIPTS[skill]} with the given command line"
                " arguments and returns its stdout and stderr. Paths are relative to the repository"
                " root. Optional stdin for `--input -`."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "arguments": {"type": "array", "items": {"type": "string"}},
                    "stdin": {"type": "string"},
                },
                "required": ["arguments"],
            },
        },
        {
            "name": "read_file",
            "description": (
                f"Reads a text file of the skill folder skills/{skill}/"
                " (path relative to the repository root)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    ]


def safe_path(skill: str, raw: str, within_skill: bool) -> Path | None:
    base = SKILLS_DIR / skill if within_skill else ROOT
    try:
        path = (ROOT / raw).resolve()
        path.relative_to(base.resolve())
    except (ValueError, OSError):
        return None
    return path


def execute_tool(skill: str, name: str, tool_input: dict[str, Any]) -> str:
    if name == "read_file":
        path = safe_path(skill, str(tool_input.get("path", "")), within_skill=True)
        if path is None or not path.is_file():
            return "error: path must name a file inside the skill folder"
        return path.read_text(encoding="utf-8")[:TOOL_OUTPUT_LIMIT]
    if name != "run_script":
        return f"error: unknown tool {name}"
    args = [str(a) for a in tool_input.get("arguments") or []]
    for a in args:
        looks_like_path = ("/" in a or "\\" in a or a.endswith(".json")) and a != "-"
        if looks_like_path and safe_path(skill, a, within_skill=False) is None:
            return f"error: path outside the repository refused: {a}"
    if skill == "french-company-accounts" and "--recorded" not in args:
        args += ["--recorded", RECORDED_DIR]
    try:
        rc, out, err, _ = run_script(skill, args, tool_input.get("stdin"))
    except subprocess.TimeoutExpired:
        return "error: script timed out"
    text = out
    if err.strip():
        text += "\n[stderr]\n" + err
    return f"[exit code {rc}]\n{text}"[:TOOL_OUTPUT_LIMIT]


def block_dict(block: Any) -> Any:
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return block


def live_case(
    client: Any, model: str, skill: str, case: dict[str, Any], arm: str
) -> dict[str, Any]:
    system = build_system(skill, arm, case)
    messages: list[dict[str, Any]] = [{"role": "user", "content": build_user(skill, arm, case)}]
    tools = build_tools(skill) if arm == "with_skill" else None
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    transcript: list[dict[str, Any]] = [{"role": "system", "content": system}, dict(messages[0])]
    t0 = time.perf_counter()
    text = ""
    rounds = 0
    for _ in range(TOOL_ROUNDS):
        rounds += 1
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": messages,
            "thinking": {"type": "adaptive"},
        }
        if tools:
            kwargs["tools"] = tools
        with client.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()
        for key in usage:
            usage[key] += int(getattr(msg.usage, key, 0) or 0)
        messages.append({"role": "assistant", "content": msg.content})
        transcript.append({"role": "assistant", "content": [block_dict(b) for b in msg.content]})
        text = "".join(
            getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text"
        )
        if msg.stop_reason != "tool_use":
            break
        results = []
        for block in msg.content:
            if getattr(block, "type", "") == "tool_use":
                output = execute_tool(skill, block.name, dict(block.input or {}))
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})
        transcript.append({"role": "user", "content": results})
    return {
        "text": text,
        "usage": usage,
        "seconds": round(time.perf_counter() - t0, 2),
        "rounds": rounds,
        "stop_reason": getattr(msg, "stop_reason", None),
        "transcript": transcript,
    }


# ----------------------------------------------------------------------------- cases and runs


def run_case(
    skill: str,
    case: dict[str, Any],
    arm: str,
    model: str,
    client: Any,
    rule_values: list[float],
    raw_dir: Path | None = None,
) -> dict[str, Any]:
    args, stdin = script_args(skill, case)
    try:
        rc, out, err, secs = run_script(skill, args, stdin)
    except subprocess.TimeoutExpired:
        g = new_grade(case, arm)
        g["error"] = "script timed out"
        return g
    truth: dict[str, Any] | None = None
    try:
        truth = json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        truth = None
    if arm == "dry-run":
        if rc not in (0, 2) or truth is None:
            g = new_grade(case, arm)
            g["error"] = f"script exit {rc}: {err.strip()[:500] or out[:500]}"
            return g
        ans = answer_from_script(skill, case, rc, out)
        g = grade(skill, case, ans, out, arm, rule_values, truth)
        g["seconds"] = round(secs, 3)
        return g
    live = live_case(client, model, skill, case, arm)
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{case['id']}-{arm}.json").write_text(
            json.dumps(live, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
    ans = parse_json_block(live["text"])
    if ans is None:
        g = new_grade(case, arm)
        g["error"] = "no JSON block in the answer"
        g["must_mention_missing"] = must_mention_missing(case, live["text"])
    else:
        g = grade(skill, case, ans, live["text"], arm, rule_values, truth)
    g["seconds"] = live["seconds"]
    g["usage"] = live["usage"]
    g["cost_usd"] = cost_usd(model, live["usage"])
    g["rounds"] = live["rounds"]
    g["stop_reason"] = live["stop_reason"]
    return g


def summarise(
    skill: str, arm: str, model: str | None, grades: list[dict[str, Any]]
) -> dict[str, Any]:
    n = len(grades)
    num_pass = sum(g["numeric_pass"] for g in grades)
    num_total = sum(g["numeric_total"] for g in grades)
    costs = [g["cost_usd"] for g in grades if isnum(g.get("cost_usd"))]
    return {
        "skill": skill,
        "arm": arm,
        "model": model,
        "cases": n,
        "fully_correct": sum(1 for g in grades if g["ok"]),
        "numeric_pass": num_pass,
        "numeric_total": num_total,
        "numeric_accuracy_pct": round(100 * num_pass / num_total, 1) if num_total else None,
        "inventions": sum(len(g["inventions"]) for g in grades),
        "cases_with_invention": sum(1 for g in grades if g["inventions"]),
        "sources_and_dates": sum(1 for g in grades if g["sources_ok"] and g["dates_ok"]),
        "errors": sum(1 for g in grades if g["error"]),
        "mean_seconds": round(sum(g["seconds"] for g in grades) / n, 2) if n else None,
        "total_cost_usd": round(sum(costs), 4) if costs else None,
    }


def case_line(skill: str, g: dict[str, Any]) -> str:
    status = "ok  " if g["ok"] else "FAIL"
    cost = f" {g['cost_usd']:.4f} USD" if isnum(g.get("cost_usd")) and g["arm"] != "dry-run" else ""
    err = f" error: {g['error']}" if g["error"] else ""
    failed = [c["name"] for c in g["checks"] if not c["ok"]]
    detail = f" failed: {', '.join(failed)}" if failed else ""
    inv = f" inventions: {len(g['inventions'])}" if g["inventions"] else ""
    miss = f" missing: {g['must_mention_missing']}" if g["must_mention_missing"] else ""
    return (
        f"{skill} {g['arm']} {g['id']} {status} {g['numeric_pass']}/{g['numeric_total']}"
        f" {g['seconds']:.1f}s{cost}{err}{detail}{inv}{miss}"
    )


def summary_line(s: dict[str, Any]) -> str:
    acc = f"{s['numeric_accuracy_pct']} %" if s["numeric_accuracy_pct"] is not None else "n/a"
    cost = f"{s['total_cost_usd']} USD" if s["total_cost_usd"] is not None else "0 USD"
    return (
        f"== {s['skill']} {s['arm']} ({s['model'] or 'script only'}):"
        f" {s['fully_correct']}/{s['cases']}"
        f" cases fully correct, numeric accuracy {acc} ({s['numeric_pass']}/{s['numeric_total']}),"
        f" inventions {s['inventions']} in {s['cases_with_invention']} case(s), sources and dates"
        f" {s['sources_and_dates']}/{s['cases']}, errors {s['errors']}, mean {s['mean_seconds']} s,"
        f" cost {cost}"
    )


def report(results_dir: Path) -> str:
    rows = []
    for path in sorted(results_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        s = data.get("summary")
        if not s:
            continue
        rows.append((data.get("date"), s))
    if not rows:
        return "no results in " + str(results_dir)
    lines = [
        "| date | skill | arm | model | cases fully correct | numeric accuracy | inventions"
        " | sources and dates | mean s | cost USD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for date, s in rows:
        acc = f"{s['numeric_accuracy_pct']} %" if s["numeric_accuracy_pct"] is not None else "n/a"
        cost = s["total_cost_usd"] if s["total_cost_usd"] is not None else 0
        lines.append(
            f"| {date} | {s['skill']} | {s['arm']} | {s['model'] or 'script only'}"
            f" | {s['fully_correct']}/{s['cases']}"
            f" | {acc} ({s['numeric_pass']}/{s['numeric_total']})"
            f" | {s['inventions']} | {s['sources_and_dates']}/{s['cases']} | {s['mean_seconds']}"
            f" | {cost} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--skill", choices=["all", *SKILL_NAMES], default="all")
    ap.add_argument("--arm", action="append", choices=LIVE_ARMS, help="live arm, repeatable")
    ap.add_argument(
        "--dry-run", action="store_true", help="script arm: no model, no network, no key"
    )
    ap.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"model id for live arms (default {DEFAULT_MODEL})"
    )
    ap.add_argument("--max-cases", type=int, help="first N cases of each skill")
    ap.add_argument("--out", default=str(RESULTS_DIR), help="results directory")
    ap.add_argument("--report", action="store_true", help="print a table of the results directory")
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    if args.report:
        print(report(out_dir))
        return 0
    arms: list[str] = []
    if args.dry_run or not args.arm:
        arms.append("dry-run")
    arms += args.arm or []
    client = None
    if any(a != "dry-run" for a in arms):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "ANTHROPIC_API_KEY is not set: live arms call the model and cost money."
                " Set the key yourself, or run --dry-run.",
                file=sys.stderr,
            )
            return 2
        import anthropic  # imported here so the dry run needs no SDK

        client = anthropic.Anthropic()
    skills = SKILL_NAMES if args.skill == "all" else [args.skill]
    rule_values = load_rule_values()
    date = datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out_dir.mkdir(parents=True, exist_ok=True)
    dry_failures = 0
    for skill in skills:
        cases = load_cases(skill, args.max_cases)
        for arm in arms:
            model = None if arm == "dry-run" else args.model
            raw_dir = None if arm == "dry-run" else out_dir / "raw" / f"{stamp}-{skill}"
            grades = []
            for case in cases:
                g = run_case(skill, case, arm, args.model, client, rule_values, raw_dir)
                grades.append(g)
                print(case_line(skill, g), flush=True)
            summary = summarise(skill, arm, model, grades)
            print(summary_line(summary), flush=True)
            result = {
                "date": date,
                "skill": skill,
                "arm": arm,
                "model": model,
                "prices_read_on": PRICES_READ_ON if model else None,
                "summary": summary,
                "cases": grades,
            }
            path = out_dir / f"{date}-{arm}-{skill}.json"
            path.write_text(
                json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
            )
            if arm == "dry-run":
                dry_failures += summary["cases"] - summary["fully_correct"]
    return 1 if dry_failures else 0


if __name__ == "__main__":
    sys.exit(main())
