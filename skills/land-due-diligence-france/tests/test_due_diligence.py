"""Tests of due_diligence.py on synthetic foncier-mcp envelopes (tests/fixtures/L*.json).

The fixtures follow the foncier-mcp envelope contract read on 2026-10-11. They are invented
for the test and describe no real property. Expected figures were computed by hand, see
evals/cases/land-due-diligence-france/<id>.json, field hand_computation.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SCRIPT = HERE.parent / "scripts" / "due_diligence.py"
sys.path.insert(0, str(SCRIPT.parent))
import due_diligence as dd  # noqa: E402


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def codes(result: dict) -> list[str]:
    return [f["code"] for f in result["flags"]]


def test_clean_parcel_has_only_info_flags():
    r = dd.analyse(load("L01"), asking_price=310_000, surface=100)
    assert r["counts"] == {"red": 0, "orange": 0, "info": 1}
    assert codes(r) == ["catnat_orders"]
    st = r["comparables"]["stats"]
    assert (st["count"], st["median_eur_m2"], st["mean_eur_m2"]) == (5, 3100, 3100)
    assert (st["min_eur_m2"], st["max_eur_m2"]) == (2900, 3300)
    assert r["asking"]["gap_to_median_pct"] == 0.0
    assert r["asking"]["median_value_for_surface_eur"] == 310_000
    assert r["subject"]["fixture"] == "synthetic"
    assert r["subject"]["parcel_id"] == "35238000AB0042"


@pytest.mark.parametrize("name", ["L01", "L03", "L05", "L12", "L15"])
def test_every_flag_carries_its_source(name):
    r = dd.analyse(load(name), property_type="Appartement" if name == "L15" else None)
    for f in r["flags"]:
        assert f["source"], (name, f)
        assert f["message"], (name, f)
        if f["code"] != "tool_error" and not f["code"].endswith("_unavailable"):
            assert f["retrieved_at"], (name, f)


def test_flags_are_sorted_red_first():
    r = dd.analyse(load("L03"))
    severities = [f["severity"] for f in r["flags"]]
    assert severities == sorted(severities, key=dd.SEVERITY_ORDER.get)
    assert severities[0] == "red"


def test_flood_zoning_at_the_point_is_red():
    r = dd.analyse(load("L03"))
    flood = [f for f in r["flags"] if f["code"] == "flood_zone"][0]
    assert flood["severity"] == "red"
    assert "flood zoning within 100 m (2 feature(s))" in flood["message"]
    assert flood["source"] == "georisques"
    assert set(codes(r)) == {"flood_zone", "dpe_e", "catnat_orders"}
    assert "commune_in_tri" not in codes(r)


def test_agricultural_zone_is_not_constructible():
    r = dd.analyse(load("L04"), asking_price=180_000, surface=150)
    red = [f for f in r["flags"] if f["severity"] == "red"]
    assert [f["code"] for f in red] == ["not_constructible"]
    assert "zone A of type A" in red[0]["message"]
    assert red[0]["regulation_url"].endswith("PLU_reglement.pdf")
    assert "few_comparables" in codes(r)
    assert r["asking"]["gap_to_median_pct"] == -25.0
    assert r["comparables"]["stats"]["median_eur_m2"] == 1600


def test_dpe_g_is_red_and_several_dpe_are_noted():
    r = dd.analyse(load("L06"), asking_price=170_000, surface=55)
    assert r["counts"] == {"red": 1, "orange": 0, "info": 2}
    dpe = [f for f in r["flags"] if f["code"] == "dpe_f_or_g"][0]
    assert dpe["energy_class"] == "G"
    assert "issued 2024-01-20" in dpe["message"]
    assert r["energy"]["classes_seen"] == ["E", "G"]
    assert r["asking"]["gap_to_median_pct"] == 1.34
    assert r["comparables"]["stats"]["median_eur_m2"] == 3050


def test_no_sale_in_radius_is_red_without_any_figure():
    r = dd.analyse(load("L08"), asking_price=250_000, surface=100)
    assert codes(r) == ["no_comparable_sale"]
    assert r["comparables"]["stats"] is None
    assert r["comparables"]["mutations_in_radius"] == 0
    assert r["asking"]["available"] is False
    assert r["asking"]["asking_eur_m2"] == 2500
    md = dd.to_markdown(r)
    assert "no comparable sale" in md
    assert "median" not in md.split("### Comparable sales")[1].split("### Zoning")[0]


def test_dvf_not_covered_keeps_the_reason_and_invents_nothing():
    r = dd.analyse(load("L09"), asking_price=220_000, surface=90)
    assert r["comparables"]["available"] is False
    assert "do not cover" in r["comparables"]["reason"]
    assert set(codes(r)) == {"sales_unavailable", "tool_error"}
    err = [f for f in r["flags"] if f["code"] == "tool_error"][0]
    assert err["message"].startswith("not_covered:")
    assert err["source"] == "dvf_geo"
    md = dd.to_markdown(r)
    assert "Not available: DVF files do not cover" in md


def test_rnu_commune_and_missing_dpe_address():
    r = dd.analyse(load("L10"))
    assert set(codes(r)) == {"rnu", "clay_strong", "dpe_unavailable"}
    assert r["zoning"]["zones"]["value"] is None
    assert "RNU" in r["zoning"]["zones"]["reason"]
    assert r["energy"]["available"] is False
    assert r["comparables"]["stats"]["mean_eur_m2"] == 1016.67


def test_carte_communale_non_constructible_sector():
    r = dd.analyse(load("L11"))
    red = [f for f in r["flags"] if f["severity"] == "red"]
    assert [f["code"] for f in red] == ["not_constructible"]
    assert "carte communale sector 03" in red[0]["message"]


def test_outlier_and_skipped_mutations():
    r = dd.analyse(load("L14"), asking_price=150_000, surface=80)
    comp = r["comparables"]
    assert comp["skipped"] == {
        "not_a_sale": 1,
        "no_dwelling_area": 1,
        "no_price": 0,
        "other_type": 0,
    }
    assert comp["mutations_in_radius"] == 7
    assert comp["stats"]["count"] == 5
    assert comp["stats"]["median_eur_m2"] == 2300
    assert comp["stats"]["mean_eur_m2"] == 3800
    assert set(codes(r)) == {"outlier", "asking_below_median"}
    assert r["asking"]["gap_to_median_pct"] == -18.48


def test_four_envelopes_property_type_filter_and_failed_risks_tool():
    r = dd.analyse(load("L15"), asking_price=160_000, surface=60, property_type="Appartement")
    comp = r["comparables"]
    assert comp["stats"]["count"] == 3
    assert comp["stats"]["median_eur_m2"] == 2500
    assert comp["skipped"]["other_type"] == 2
    assert r["counts"] == {"red": 0, "orange": 3, "info": 0}
    assert set(codes(r)) == {"risks_unavailable", "easement", "tool_error"}
    assert r["risks"]["available"] is False
    assert r["asking"]["gap_to_median_pct"] == 6.67
    assert r["subject"]["parcel_id"] == "86194000DL0580"


def test_industrial_and_ground_risks():
    r = dd.analyse(load("L12"))
    assert r["counts"] == {"red": 0, "orange": 3, "info": 2}
    seveso = [f for f in r["flags"] if f["code"] == "seveso_nearby"][0]
    assert "Depot chimique de la plaine (Seveso seuil haut)" in seveso["message"]
    assert r["comparables"]["stats"]["multi_dwelling_sales"] == 1


def test_markdown_sheet_has_sources_dates_and_disclaimer():
    md = dd.to_markdown(dd.analyse(load("L03")))
    assert md.startswith("## Due diligence sheet, parcel 37261000BX0077")
    assert "Data: synthetic fixture, not a real parcel." in md
    assert "[RED]" in md
    assert "[source: georisques, retrieved 2026-10-11T14:05:00+00:00]" in md
    assert "median 2 000.00 EUR/m2" in md
    assert "not personalised advice" in md
    assert chr(0x2014) not in md


def test_asking_price_requires_positive_numbers():
    with pytest.raises(dd.InvalidInput):
        dd.analyse(load("L01"), asking_price=-1, surface=10)
    with pytest.raises(dd.InvalidInput):
        dd.analyse(load("L01"), asking_price=100_000, surface=0)


def test_input_that_is_not_an_envelope_is_refused():
    with pytest.raises(dd.InvalidInput):
        dd.analyse({"foo": 1})
    with pytest.raises(dd.InvalidInput):
        dd.analyse({"envelopes": [{"tool": "unknown_tool"}]})


def test_cli_json_and_markdown(tmp_path):
    cmd = [sys.executable, "-I", str(SCRIPT), "--input", str(FIXTURES / "L01.json")]
    out = subprocess.run(
        cmd + ["--asking-price", "310000", "--surface", "100"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["asking"]["gap_to_median_pct"] == 0.0
    out = subprocess.run(cmd + ["--markdown"], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert "median 3 100.00 EUR/m2" in out.stdout
    bad = tmp_path / "bad.json"
    bad.write_text('{"foo": 1}', encoding="utf-8")
    out = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--input", str(bad)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert out.returncode == 3
    assert json.loads(out.stdout)["error"] == "invalid_input"
