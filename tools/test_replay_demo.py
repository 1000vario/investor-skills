"""Offline tests of tools/replay_demo.py: the contract check on synthetic fixtures, no network."""

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "skills" / "land-due-diligence-france" / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "tools"))
import replay_demo  # noqa: E402


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_clean_fixture_conforms():
    assert replay_demo.check_envelope(load("L01")) == []


def test_dvf_hole_fixture_conforms():
    assert replay_demo.check_envelope(load("L09")) == []


def test_live_dvf_hole_shape_without_years_conforms():
    # The server omits data.sales.years when DVF does not cover the department (seen live on
    # 2026-10-14); the fixture L09 keeps the key. Both shapes must pass.
    env = load("L09")
    del env["data"]["sales"]["years"]
    assert replay_demo.check_envelope(env) == []


def test_missing_section_and_too_many_nearby_sales_are_reported():
    env = copy.deepcopy(load("L01"))
    del env["data"]["risks"]
    nearby = env["data"]["sales"]["sales_nearby"]["value"]
    env["data"]["sales"]["sales_nearby"]["value"] = nearby * 4
    problems = replay_demo.check_envelope(env)
    assert any(p.startswith("data.risks missing") for p in problems)
    assert any("sales_nearby has 20 items, max 15" in p for p in problems)


def test_unsourced_sales_field_and_wrong_tool_are_reported():
    env = copy.deepcopy(load("L01"))
    env["tool"] = "get_parcel_sales"
    env["data"]["sales"]["sales_on_parcel"] = []
    problems = replay_demo.check_envelope(env)
    assert any("expected 'summarize_parcel'" in p for p in problems)
    assert "data.sales.sales_on_parcel is not a sourced field" in problems


def test_non_object_is_rejected():
    assert replay_demo.check_envelope([1, 2]) == ["envelope is not a JSON object"]
    assert "data is not an object" in replay_demo.check_envelope({"ok": True})


def test_main_reports_unreachable_endpoint(capsys):
    code = replay_demo.main(["--endpoint", "http://127.0.0.1:9/none", "1 Rue Test 00000 Nulle"])
    assert code == 2
    assert "no usable answer" in capsys.readouterr().out
