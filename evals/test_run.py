"""Tests of the eval runner's grading, without any model call."""

import copy
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run  # noqa: E402

RULE_VALUES = run.load_rule_values()


def test_dig_reads_dotted_paths_with_indexes():
    data = {"summary": {"a": 1.5}, "years": [{"total_tax": 10}, {"total_tax": 20}]}
    assert run.dig(data, "summary.a") == 1.5
    assert run.dig(data, "years[1].total_tax") == 20
    assert run.dig(data, "years[5].total_tax") is None
    assert run.dig(data, "missing.key") is None


def test_parse_json_block_takes_the_last_valid_block():
    text = 'first ```json\n{"a": 1}\n``` then ```json\n{"b": 2}\n```'
    assert run.parse_json_block(text) == {"b": 2}
    assert run.parse_json_block("no block") is None
    assert run.parse_json_block("```json\n{broken\n```") is None


def test_cost_from_the_price_table():
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    assert run.cost_usd("claude-opus-5-5", usage) == 6.0
    assert run.cost_usd("unknown-model", usage) is None


def test_rental_grade_passes_on_the_truth_and_counts_invented_rule_values():
    case = run.load_cases("rental-yield-france")[0]
    ans = {
        "refused": False,
        "rule": None,
        "figures": {k: v["value"] for k, v in case["expected"].items()},
        "fiscal_rules": [{"name": "micro-foncier allowance", "value": 30, "source": "CGI art. 32"}],
        "warnings": [],
        "sources": ["service-public.fr F1991, read 2026-10-11"],
    }
    text = "Plafond 15 000 EUR, abattement 30 %, lu le 2026-10-11."
    g = run.grade_rental(case, ans, text, "with_skill", RULE_VALUES)
    assert g["ok"], g
    assert g["numeric_pass"] == g["numeric_total"] == len(case["expected"])
    bad = copy.deepcopy(ans)
    bad["fiscal_rules"].append({"name": "invented ceiling", "value": 12345, "source": "memory"})
    g = run.grade_rental(case, bad, text, "with_skill", RULE_VALUES)
    assert not g["ok"]
    assert len(g["inventions"]) == 1


def test_rental_refusal_case_is_graded_on_the_refusal():
    case = next(c for c in run.load_cases("rental-yield-france") if c["expect_refusal"])
    ans = {
        "refused": True,
        "rule": "R1",
        "figures": {},
        "fiscal_rules": [],
        "sources": ["CGI art. 32"],
    }
    g = run.grade_rental(
        case, ans, "Au-dessus de 15 000 EUR, passer au reel. Lu le 2026-10-11.", "bare", RULE_VALUES
    )
    assert g["checks"][0]["name"] == "refused" and g["checks"][0]["ok"]
    g = run.grade_rental(
        case, {"refused": False, "figures": {"summary.gross_yield_pct": 5}}, "", "bare", RULE_VALUES
    )
    assert not g["ok"]


def test_land_grade_flags_a_figure_where_the_truth_has_none():
    case = next(
        c for c in run.load_cases("land-due-diligence-france") if c["expected"]["stats"] is None
    )
    ans = {
        "counts": case["expected"]["counts"],
        "flags": case["expected"]["flag_codes"],
        "figures": {"count": 4, "median_eur_m2": 2500, "gap_to_median_pct": None},
        "sources": [{"id": "dvf_geo", "date": "2026-10-11"}],
    }
    g = run.grade_land(case, ans, "DVF, GPU urbanisme, 2026-10-11", "with_skill")
    assert not g["ok"]
    assert {i["figure"] for i in g["inventions"]} == {"count", "median_eur_m2"}


def test_accounts_grade_counts_a_wrong_figure_as_invented():
    case = run.load_cases("french-company-accounts")[0]
    exp = case["expected"]
    ans = {
        "figures": {
            "latest_closing_date": exp["latest_closing_date"],
            "type_bilan": exp["type_bilan"],
            "turnover_eur": exp["turnover_eur"] * 1.2,
            "net_result_eur": exp["net_result_eur"],
            "net_margin_pct": exp["net_margin_pct"],
            "ratio_name": exp["ratio"]["name"],
            "ratio_value": exp["ratio"]["value"],
        },
        "previous_closings": exp["previous_closings"],
        "anomalies_noted": ["net result published as 0"],
        "sources": [{"url": "https://data.economie.gouv.fr/", "date": "2026-10-11"}],
    }
    g = run.grade_accounts(case, ans, "INPI, 2026-10-11", "without_skill")
    assert not g["ok"]
    assert [i["figure"] for i in g["inventions"]] == ["turnover_eur"]


@pytest.mark.parametrize("skill", run.SKILL_NAMES)
def test_dry_run_passes_on_the_first_case(skill):
    case = run.load_cases(skill, max_cases=1)[0]
    g = run.run_case(skill, case, "dry-run", run.DEFAULT_MODEL, None, RULE_VALUES)
    assert g["ok"], json.dumps(g, indent=1)
    assert g["numeric_total"] > 0
