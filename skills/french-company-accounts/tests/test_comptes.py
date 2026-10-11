"""Tests of comptes.py on a recorded response (Danone, SIREN 552032534, recorded 2026-10-11)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import comptes  # noqa: E402


@pytest.fixture
def data():
    return comptes.load_recorded(FIXTURES, "552032534")


def test_siren_luhn():
    for siren in ("552032534", "632012100", "855200507", "542051180", "356000000"):
        assert comptes.siren_is_valid(siren)
    assert not comptes.siren_is_valid("552032535")
    assert not comptes.siren_is_valid("55203253")
    assert not comptes.siren_is_valid("abc")


def test_identity_and_sources(data):
    r = comptes.analyse(data)
    assert r["identity"]["siren"] == "552032534"
    assert r["identity"]["name"] == "DANONE"
    assert r["mode"] == "recorded"
    assert r["fetched_on"] == "2026-10-11"
    assert {s["id"] for s in r["sources"]} == {"recherche_entreprises", "ratios_inpi_bce"}
    assert all(s["url"].startswith("https://") and s["fetched_on"] for s in r["sources"])


def test_consolidated_first_then_social(data):
    r = comptes.analyse(data)
    last = r["exercices"][-1]
    assert last["closing_date"] == "2025-12-31"
    assert last["type_bilan"] == "K"
    assert "S" in last["alternatives_not_shown"]
    assert last["items_eur"]["chiffre_d_affaires"] == 27_376_000_000
    assert last["items_eur"]["resultat_net"] == 2_100_000_000
    # Net margin by hand: 2 100 / 27 376 = 7.67 %.
    assert last["computed"]["net_margin_pct"] == pytest.approx(7.67, abs=0.01)
    assert last["computed"]["ebe_margin_pct"] == pytest.approx(13.37, abs=0.01)
    assert abs(last["computed"]["ebe_margin_check_delta_pt"]) < 0.01
    social = comptes.analyse(data, social=True)
    assert social["exercices"][-1]["type_bilan"] == "S"
    assert social["exercices"][-1]["items_eur"]["chiffre_d_affaires"] == 1_030_000_000


def test_three_exercices_sorted_and_cross_checked(data):
    r = comptes.analyse(data)
    dates = [e["closing_date"] for e in r["exercices"]]
    assert dates == sorted(dates) and len(dates) == 3
    last = r["exercices"][-1]
    assert last["cross_check_annuaire"]["turnover_matches"] is True
    assert last["cross_check_annuaire"]["net_result_matches"] is True
    assert r["evolution"]["from"] == dates[0] and r["evolution"]["to"] == dates[-1]
    assert r["exercices"][1]["computed"]["turnover_change_pct"] is not None


def test_anomalies_detected(data):
    r = comptes.analyse(data)
    assert any("net result published as 0" in a for a in r["anomalies"])


def test_empty_consolidated_row_loses_to_filled_row():
    # Ubisoft, closing 2024-03-31: the K row is published entirely at zero, the C row is filled.
    r = comptes.analyse(comptes.load_recorded(FIXTURES, "335186094"))
    middle = [e for e in r["exercices"] if e["closing_date"] == "2024-03-31"][0]
    assert middle["type_bilan"] == "C"
    assert middle["items_eur"]["chiffre_d_affaires"] == 2_643_600_000
    assert "K (empty)" in middle["alternatives_not_shown"]
    # A fiscal year closing in March 2025 is listed under 2024 in the annuaire.
    last = r["exercices"][-1]
    assert last["closing_date"] == "2025-03-31"
    assert last["cross_check_annuaire"]["annuaire_year"] == "2024"
    assert last["cross_check_annuaire"]["turnover_matches"] is True
    assert any("type of accounts differs" in a for a in r["anomalies"])


def test_type_change_flagged_between_closing_dates():
    # Societe Bic: 2025 has company accounts only, 2024 and 2023 consolidated ones.
    r = comptes.analyse(comptes.load_recorded(FIXTURES, "552008443"))
    types = [e["type_bilan"] for e in r["exercices"]]
    assert types == ["K", "K", "C"]
    assert any("type of accounts differs" in a for a in r["anomalies"])
    assert r["exercices"][-1]["items_eur"]["chiffre_d_affaires"] == 666_788_000
    assert r["exercices"][-1]["computed"]["net_margin_pct"] == pytest.approx(13.77, abs=0.01)


def test_exercices_limit(data):
    assert len(comptes.analyse(data, exercices=2)["exercices"]) == 2


def test_cli_recorded_markdown_and_json():
    base = [
        sys.executable,
        str(SCRIPTS / "comptes.py"),
        "--siren",
        "552032534",
        "--recorded",
        str(FIXTURES),
    ]
    out = subprocess.run(base, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout)["identity"]["name"] == "DANONE"
    md = subprocess.run(base + ["--markdown"], capture_output=True, text=True, check=True).stdout
    assert "## DANONE (SIREN 552032534)" in md
    assert "| 2025-12-31 | K |" in md
    assert chr(0x2014) not in md


def test_cli_errors():
    bad = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "comptes.py"),
            "--siren",
            "123",
            "--recorded",
            str(FIXTURES),
        ],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 3 and json.loads(bad.stdout)["error"] == "invalid_input"
    missing = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "comptes.py"),
            "--siren",
            "632012100",
            "--recorded",
            str(FIXTURES),
        ],
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 4 and json.loads(missing.stdout)["error"] == "not_found"
