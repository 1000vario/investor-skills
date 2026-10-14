"""Replay land-due-diligence-france against the public foncier-mcp demo endpoint.

Usage:
    python tools/replay_demo.py [--endpoint URL] [--asking-price EUR --surface M2] [ADDRESS ...]

For each address the tool posts {"address": ...} to the demo (no key, 15 nearby sales at most,
10 calls per hour per IP, 300 per day), checks that the envelope follows the contract the
fixtures and tests of skills/land-due-diligence-france rely on, runs due_diligence.analyse on
it and prints counts, flag codes and sources only. Nothing is written to disk: the demo returns
real DVF sales and this repository keeps none of them.

Exit codes: 0 every envelope conforms, 1 a contract check failed, 2 the demo did not answer.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "land-due-diligence-france" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import due_diligence as dd  # noqa: E402

DEMO_URL = "https://foncier.guillaume-couillard.com/demo/summary"
DEFAULT_ADDRESSES = ("1 Rue de Metz 31000 Toulouse", "2 Place Bellecour 69002 Lyon")
SECTIONS = ("parcel", "geocoding", "centroid", "sales", "zoning", "energy_diagnostics", "risks")
# Always present in a sales section; "years" is written only when DVF covers the department.
SALES_KEYS = ("sales_on_parcel", "sales_nearby")
MAX_NEARBY = 15
TIMEOUT_S = 90
USER_AGENT = "investor-skills replay_demo (https://github.com/1000vario/investor-skills)"


def check_envelope(env: Any) -> list[str]:
    """Problems found against the summarize_parcel contract, empty when it conforms."""
    if not isinstance(env, dict):
        return ["envelope is not a JSON object"]
    problems: list[str] = []
    if env.get("ok") is not True:
        problems.append("ok is not true")
    if env.get("tool") != "summarize_parcel":
        problems.append(f"tool is {env.get('tool')!r}, expected 'summarize_parcel'")
    for key in ("warnings", "errors"):
        if not isinstance(env.get(key), list):
            problems.append(f"{key} is not a list")
    if not isinstance(env.get("sources"), dict) or not env["sources"]:
        problems.append("sources is empty or not an object")
    if not isinstance(env.get("generated_at"), str):
        problems.append("generated_at missing")
    data = env.get("data")
    if not isinstance(data, dict):
        return [*problems, "data is not an object"]
    for key in SECTIONS:
        if key not in data:
            problems.append(f"data.{key} missing")
    for key in ("parcel", "geocoding", "centroid"):
        if key in data and not dd.is_sourced(data[key]):
            problems.append(f"data.{key} is not a sourced field")
    sales = data.get("sales")
    if dd.section_missing(sales) is None and "sales" in data:
        if not isinstance(sales, dict):
            problems.append("data.sales is neither a section nor a sourced field")
        else:
            for key in SALES_KEYS:
                if not dd.is_sourced(sales.get(key)):
                    problems.append(f"data.sales.{key} is not a sourced field")
            if "years" in sales and not dd.is_sourced(sales["years"]):
                problems.append("data.sales.years is present but not a sourced field")
            nearby = dd.val(sales.get("sales_nearby"))
            if isinstance(nearby, list) and len(nearby) > MAX_NEARBY:
                problems.append(
                    f"data.sales.sales_nearby has {len(nearby)} items, max {MAX_NEARBY}"
                )
    for key in ("zoning", "energy_diagnostics", "risks"):
        if key in data and not isinstance(data[key], dict):
            problems.append(f"data.{key} is neither a section nor a sourced field")
    return problems


def fetch(endpoint: str, address: str) -> dict[str, Any]:
    body = json.dumps({"address": address}).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def section_state(section: Any) -> str:
    reason = dd.section_missing(section)
    if reason is None and isinstance(section, dict):
        # A sales section in a DVF coverage hole keeps its keys as missing fields.
        first = section.get("sales_on_parcel")
        if dd.is_sourced(first) and dd.val(first) is None and first.get("reason"):
            reason = first["reason"]
    return f"unavailable ({reason})" if reason else "available"


def report(address: str, env: dict[str, Any], result: dict[str, Any]) -> None:
    data = env.get("data") or {}
    geocoding = dd.val(data.get("geocoding")) or {}
    sales = data.get("sales")
    nearby = dd.val(sales.get("sales_nearby")) if isinstance(sales, dict) else None
    on_parcel = dd.val(sales.get("sales_on_parcel")) if isinstance(sales, dict) else None
    counts = result["counts"]
    print(f"Address: {address}")
    print(f"  geocoded as: {geocoding.get('label') or 'n/a'}")
    print(f"  parcel: {result['subject']['parcel_id'] or 'n/a'}")
    print(f"  generated_at: {env.get('generated_at')} | duration_ms: {env.get('duration_ms')}")
    print(f"  warnings: {len(env.get('warnings') or [])} | errors: {len(env.get('errors') or [])}")
    for err in env.get("errors") or []:
        print(f"    error {err.get('code')} from {err.get('source') or 'no source'}")
    print(
        "  sales: "
        + section_state(sales)
        + (
            f" | {len(on_parcel) if isinstance(on_parcel, list) else 0} on parcel"
            f" | {len(nearby) if isinstance(nearby, list) else 0} nearby"
            if isinstance(sales, dict) and dd.section_missing(sales) is None
            else ""
        )
    )
    for key in ("zoning", "energy_diagnostics", "risks"):
        print(f"  {key}: {section_state(data.get(key))}")
    comp = result["comparables"].get("stats") or {}
    print(f"  comparables kept: {comp.get('count', 0)}")
    print(f"  flags: red {counts['red']} | orange {counts['orange']} | info {counts['info']}")
    grouped = Counter(
        (f["severity"], f["code"], f.get("source") or "no source") for f in result["flags"]
    )
    for (severity, code, source), n in grouped.items():
        print(f"    [{severity}] {code} | {source}" + (f" x{n}" if n > 1 else ""))
    print("  sources: " + ", ".join(sorted(env.get("sources") or {})))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("addresses", nargs="*", default=list(DEFAULT_ADDRESSES))
    ap.add_argument("--endpoint", default=DEMO_URL)
    ap.add_argument("--asking-price", type=float, default=None)
    ap.add_argument("--surface", type=float, default=None)
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between two calls")
    args = ap.parse_args(argv)
    failed = False
    for i, address in enumerate(args.addresses):
        if i:
            time.sleep(args.pause)
        try:
            env = fetch(args.endpoint, address)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            print(f"Address: {address}\n  HTTP {exc.code}: {detail}")
            return 2
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"Address: {address}\n  no usable answer: {exc}")
            return 2
        problems = check_envelope(env)
        if problems:
            failed = True
            print(f"Address: {address}\n  CONTRACT FAILED")
            for p in problems:
                print(f"    - {p}")
            continue
        try:
            result = dd.analyse(env, asking_price=args.asking_price, surface=args.surface)
        except dd.InvalidInput as exc:
            failed = True
            print(f"Address: {address}\n  analyse refused the envelope: {exc}")
            continue
        report(address, env, result)
        print("  contract: OK")
    print("REPLAY " + ("FAILED" if failed else "OK") + f": {len(args.addresses)} address(es)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
