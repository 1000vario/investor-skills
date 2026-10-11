"""Tests of rendement.py against figures computed by hand (see evals/cases)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rendement  # noqa: E402

BASE = {
    "price": 150000,
    "fees": 12000,
    "works": 0,
    "rent_monthly": 650,
    "charges_annual": 600,
    "property_tax": 700,
    "regime": "micro-foncier",
    "tmi": 0.30,
}


def test_micro_foncier_by_hand():
    # Rent 7 800; taxable 70 % = 5 460; IR 30 % = 1 638; PS 17.2 % = 939.12; total 2 577.12.
    r = rendement.compute(BASE)
    s = r["summary"]
    assert s["gross_yield_pct"] == pytest.approx(5.20, abs=0.005)
    assert s["net_yield_pct"] == pytest.approx(100 * 6500 / 162000, abs=0.005)
    assert s["taxable_income_year1"] == 5460.0
    assert s["total_tax_year1"] == pytest.approx(2577.12, abs=0.005)
    assert s["net_after_tax_yield_year1_pct"] == pytest.approx(100 * 3922.88 / 162000, abs=0.005)
    assert s["monthly_cash_flow_after_tax_year1"] == pytest.approx(3922.88 / 12, abs=0.005)
    assert len(r["years"]) == 10


def test_micro_foncier_refused_above_ceiling():
    with pytest.raises(rendement.NotEligibleError) as exc:
        rendement.compute({**BASE, "rent_monthly": 1300})  # 15 600 > 15 000
    assert exc.value.rule_id == "R1"


def test_reel_foncier_deficit_and_carry():
    # Rent 6 000; charges 500 + 600 + works 20 000 = 21 100; deficit 15 100;
    # 10 700 on global income (saving 3 210 at 30 %), 4 400 carried.
    inp = {
        "price": 100000,
        "fees": 8000,
        "works": 20000,
        "rent_monthly": 500,
        "charges_annual": 500,
        "property_tax": 600,
        "regime": "reel-foncier",
        "tmi": 0.30,
    }
    r = rendement.compute(inp)
    y1, y2, y3 = r["years"][:3]
    assert y1["deficit_on_global_income"] == 10700.0
    assert y1["carry_added"] == 4400.0
    assert y1["taxable_income"] == 0.0
    assert y1["total_tax"] == pytest.approx(-3210.0)
    # Year 2: result 4 900, carry 4 400 used, taxable 500: IR 150 + PS 86.
    assert y2["carry_used"] == 4400.0
    assert y2["taxable_income"] == 500.0
    assert y2["total_tax"] == pytest.approx(236.0)
    # Year 3: taxable 4 900: 1 470 + 842.80.
    assert y3["taxable_income"] == 4900.0
    assert y3["total_tax"] == pytest.approx(2312.80)


def test_reel_foncier_interest_excess_carried_not_deducted_globally():
    # No other charges, interest 7 000 on rent 6 000: 1 000 carried, nothing on global income.
    inp = {
        "price": 100000,
        "rent_monthly": 500,
        "regime": "reel-foncier",
        "tmi": 0.30,
        "loan": {"amount": 200000, "rate": 0.035, "years": 25, "insurance_rate": 0},
    }
    r = rendement.compute(inp)
    y1 = r["years"][0]
    assert y1["loan_interest"] > 6000
    assert y1["deficit_on_global_income"] == 0.0
    assert y1["carry_added"] == pytest.approx(y1["loan_interest"] - 6000, abs=0.01)


def test_energy_renovation_cap():
    inp = {
        "price": 100000,
        "works": 30000,
        "rent_monthly": 500,
        "regime": "reel-foncier",
        "tmi": 0.30,
        "energy_renovation": True,
    }
    assert rendement.compute(inp)["years"][0]["deficit_on_global_income"] == 21400.0


def test_micro_bic_thresholds_2025_vs_2026():
    inp = {"price": 900000, "rent_monthly": 6666.67, "regime": "lmnp-micro-bic", "tmi": 0.41}
    with pytest.raises(rendement.NotEligibleError):
        rendement.compute({**inp, "income_year": 2025})  # 80 000 > 77 700
    r = rendement.compute({**inp, "income_year": 2026})  # 80 000 <= 83 600
    assert r["years"][0]["taxable_income"] == pytest.approx(40000.02, abs=0.01)
    assert any("23 000" in w for w in r["warnings"])


def test_micro_bic_tourism_unclassified():
    inp = {
        "price": 100000,
        "rent_monthly": 1000,
        "regime": "lmnp-micro-bic",
        "tmi": 0.11,
        "tourism_unclassified": True,
    }
    r = rendement.compute(inp)
    assert r["years"][0]["taxable_income"] == pytest.approx(8400.0)  # 12 000 minus 30 %
    with pytest.raises(rendement.NotEligibleError):
        rendement.compute({**inp, "rent_monthly": 1300})  # 15 600 > 15 000


def test_lmnp_reel_depreciation_cap():
    # Building base 85 000 / 30 = 2 833.33; cap = 6 000 - 1 000 = 5 000; taxable 2 166.67.
    inp = {
        "price": 100000,
        "rent_monthly": 500,
        "charges_annual": 1000,
        "regime": "lmnp-reel",
        "tmi": 0.30,
    }
    y1 = rendement.compute(inp)["years"][0]
    assert y1["depreciation_used"] == pytest.approx(2833.33, abs=0.01)
    assert y1["taxable_income"] == pytest.approx(2166.67, abs=0.01)
    # Rent 3 000: cap 2 000, 833.33 carried, taxable 0, carried amount used later.
    r = rendement.compute({**inp, "rent_monthly": 250})
    y1, y2 = r["years"][:2]
    assert y1["depreciation_used"] == pytest.approx(2000.0)
    assert y1["depreciation_carried"] == pytest.approx(833.33, abs=0.01)
    assert y1["taxable_income"] == 0.0
    assert y2["depreciation_carried"] == pytest.approx(1666.67, abs=0.01)


def test_lmnp_reel_deficit_carried_ten_years():
    # Charges 7 000 above rent 6 000: deficit 1 000 carried, no depreciation used.
    inp = {
        "price": 100000,
        "rent_monthly": 500,
        "charges_annual": 7000,
        "regime": "lmnp-reel",
        "tmi": 0.30,
        "horizon_years": 12,
    }
    r = rendement.compute({**inp, "charges_growth": 0})
    y1 = r["years"][0]
    assert y1["depreciation_used"] == 0.0
    assert y1["carry_added"] == pytest.approx(1000.0)
    assert y1["total_tax"] == 0.0


def test_loan_payment_formula():
    rows, payment, ins = rendement.loan_schedule(
        {"amount": 100000, "rate": 0.036, "years": 20, "insurance_rate": 0.003}, 20
    )
    assert payment == pytest.approx(585.1, abs=0.2)
    assert ins == pytest.approx(25.0)
    assert sum(r["principal"] for r in rows) == pytest.approx(100000, abs=0.01)
    assert rows[-1]["remaining"] == pytest.approx(0.0, abs=0.01)
    assert rows[0]["interest"] == pytest.approx(3543.0, abs=1)


def test_invalid_inputs():
    with pytest.raises(rendement.InputError):
        rendement.compute({**BASE, "tmi": 0.25})
    with pytest.raises(rendement.InputError):
        rendement.compute({**BASE, "regime": "sci-is"})
    with pytest.raises(rendement.InputError):
        rendement.compute({k: v for k, v in BASE.items() if k != "price"})


def test_cli_json_and_markdown(tmp_path):
    case = tmp_path / "case.json"
    case.write_text(json.dumps(BASE), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "rendement.py"), "--input", str(case)],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(out.stdout)
    assert data["summary"]["taxable_income_year1"] == 5460.0
    md = subprocess.run(
        [sys.executable, str(SCRIPTS / "rendement.py"), "--input", str(case), "--markdown"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "| Gross yield | 5.20 % |" in md
    assert chr(0x2014) not in md


def test_not_eligible_exit_code(tmp_path):
    case = tmp_path / "case.json"
    case.write_text(json.dumps({**BASE, "rent_monthly": 2000}), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "rendement.py"), "--input", str(case)],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 2
    refusal = json.loads(out.stdout)
    assert refusal["rule"] == "R1"
    assert "CGI art. 32" in refusal["source"]
    assert refusal["rules_read_on"] == rendement.READ_ON
