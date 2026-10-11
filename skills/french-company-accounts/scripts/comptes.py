#!/usr/bin/env python3
"""Reads the filed accounts of a French company from two public APIs, no key needed.

Sources, read on 2026-10-11 (details in ../references/sources-api.md):
- recherche-entreprises.api.gouv.fr: identity, and the yearly turnover and net result
  shown by the annuaire des entreprises (data.gouv.fr).
- data.economie.gouv.fr, dataset ratios_inpi_bce: key items and ratios computed by the
  Banque de France from the accounts filed at INPI, one row per closing date and type of
  accounts (C complete, S simplified, K consolidated).

The model judges, this script computes: every figure quoted in an answer must come from
this output, tied to its closing date, its type of accounts and its source URL. The script
reproduces the published values and computes margins and changes; it does not rate the
company. Not personalised advice.

Usage:
    python comptes.py --siren 552032534 [--markdown] [--social] [--exercices 3]
    python comptes.py --name "Danone"
    python comptes.py --siren 552032534 --recorded DIR    offline, from recorded responses
    python comptes.py --siren 552032534 --record FILE     live, and save the raw responses
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

READ_ON = "2026-10-11"
RECHERCHE_BASE = "https://recherche-entreprises.api.gouv.fr/search"
RATIOS_BASE = (
    "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/ratios_inpi_bce/records"
)
USER_AGENT = (
    "investor-skills/french-company-accounts (https://github.com/1000vario/investor-skills)"
)
TYPE_BILAN = {
    "C": "comptes annuels complets",
    "S": "comptes annuels simplifies",
    "K": "comptes consolides",
}
PRIORITY_DEFAULT = ("K", "C", "S")
PRIORITY_SOCIAL = ("C", "S", "K")
ITEMS = ("chiffre_d_affaires", "marge_brute", "ebe", "ebit", "resultat_net")
RATIOS = {
    "marge_ebe": "%",
    "resultat_courant_avant_impots_sur_ca": "%",
    "caf_sur_ca": "%",
    "taux_d_endettement": "%",
    "autonomie_financiere": "%",
    "poids_bfr_exploitation_sur_ca": "%",
    "poids_bfr_exploitation_sur_ca_jours": "days",
    "credit_clients_jours": "days",
    "credit_fournisseurs_jours": "days",
    "rotation_des_stocks_jours": "days",
    "ratio_de_liquidite": "as published",
    "couverture_des_interets": "as published",
    "capacite_de_remboursement": "as published",
    "ratio_de_vetuste": "as published",
}
SOURCES = {
    "recherche_entreprises": {
        "name": "API Recherche d'entreprises (annuaire des entreprises, data.gouv.fr)",
        "licence": "Licence Ouverte 2.0 (MIT for the API code)",
        "rate_limit": "7 requests per second per IP, no key",
    },
    "ratios_inpi_bce": {
        "name": "Ratios financiers (BCE / INPI), data.economie.gouv.fr",
        "licence": "Licence Ouverte 2.0",
        "rate_limit": "public Opendatasoft explore API, no key",
    },
}


class ScriptError(Exception):
    code = "internal_error"
    exit_code = 1


class InvalidInput(ScriptError):
    code = "invalid_input"
    exit_code = 3


class NotFound(ScriptError):
    code = "not_found"
    exit_code = 4


class SourceUnavailable(ScriptError):
    code = "source_unavailable"
    exit_code = 5


def siren_is_valid(siren: str) -> bool:
    """Nine digits with a valid Luhn key (La Poste's 356000000 is the known exception)."""
    if not re.fullmatch(r"\d{9}", siren):
        return False
    if siren == "356000000":
        return True
    total = 0
    for i, ch in enumerate(reversed(siren)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SourceUnavailable(f"{url} answered HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SourceUnavailable(f"{url} unreachable or not JSON: {exc}") from exc


def search_url(query: str, per_page: int) -> str:
    return (
        RECHERCHE_BASE
        + "?"
        + urllib.parse.urlencode(
            {
                "q": query,
                "minimal": "true",
                "include": "finances,siege",
                "page": 1,
                "per_page": per_page,
            }
        )
    )


def ratios_url(siren: str, limit: int = 30) -> str:
    return (
        RATIOS_BASE
        + "?"
        + urllib.parse.urlencode(
            {"where": f'siren="{siren}"', "order_by": "date_cloture_exercice desc", "limit": limit}
        )
    )


def load_recorded(directory: Path, siren: str) -> dict:
    path = directory / f"{siren}.json"
    if not path.exists():
        raise NotFound(f"no recorded response for SIREN {siren} in {directory}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {
        "recherche_entreprises": doc["recherche_entreprises"],
        "ratios_inpi_bce": doc["ratios_inpi_bce"],
        "fetched_on": doc.get("recorded_on"),
        "mode": "recorded",
    }


def fetch_live(siren: str | None, name: str | None) -> dict:
    query = siren if siren else name
    u1 = search_url(query, 1 if siren else 5)
    r1 = fetch_json(u1)
    results = r1.get("results") or []
    if not results:
        raise NotFound(f"no company found for {query!r} in the annuaire des entreprises")
    found_siren = results[0]["siren"]
    u2 = ratios_url(found_siren)
    r2 = fetch_json(u2)
    return {
        "recherche_entreprises": {"url": u1, "response": r1},
        "ratios_inpi_bce": {"url": u2, "response": r2},
        "fetched_on": dt.date.today().isoformat(),
        "mode": "live",
    }


def pick_rows(rows: list[dict], priority: tuple[str, ...], n: int) -> tuple[list[dict], dict]:
    """One row per closing date, chosen by type priority; also the alternatives left out."""
    by_date: dict[str, list[dict]] = {}
    for row in rows:
        by_date.setdefault(row["date_cloture_exercice"], []).append(row)
    chosen, alternatives = [], {}
    for date in sorted(by_date, reverse=True)[:n]:
        candidates = by_date[date]
        # A row whose turnover is empty or zero loses to a filled row of the same date,
        # whatever its type: some consolidated rows are published entirely at zero.
        filled = [r for r in candidates if r.get("chiffre_d_affaires")]
        pool = filled or candidates
        ranked = sorted(
            pool,
            key=lambda r: (
                priority.index(r.get("type_bilan")) if r.get("type_bilan") in priority else 99
            ),
        )
        chosen.append(ranked[0])
        others = [r.get("type_bilan") for r in ranked[1:]]
        others += [f"{r.get('type_bilan')} (empty)" for r in candidates if r not in pool]
        if others:
            alternatives[date] = others
    return chosen, alternatives


def pct(num, den):
    if num is None or den in (None, 0):
        return None
    return 100.0 * num / den


def change_pct(new, old):
    if new is None or old in (None, 0):
        return None
    return 100.0 * (new - old) / abs(old)


def analyse(data: dict, *, social: bool = False, exercices: int = 3) -> dict:
    r1 = data["recherche_entreprises"]["response"]
    results = r1.get("results") or []
    if not results:
        raise NotFound("no company in the annuaire response")
    company = results[0]
    siege = company.get("siege") or {}
    identity = {
        "siren": company.get("siren"),
        "name": company.get("nom_complet"),
        "legal_form_code": company.get("nature_juridique"),
        "main_activity_naf": company.get("activite_principale"),
        "company_category": company.get("categorie_entreprise"),
        "created_on": company.get("date_creation"),
        "administrative_state": company.get("etat_administratif"),
        "headcount_band_code": company.get("tranche_effectif_salarie"),
        "headcount_band_year": company.get("annee_tranche_effectif_salarie"),
        "head_office": siege.get("adresse"),
        "updated_on": company.get("date_mise_a_jour"),
    }
    candidates = [{"siren": c.get("siren"), "name": c.get("nom_complet")} for c in results[1:]]
    annuaire_finances = company.get("finances") or {}

    rows = (data["ratios_inpi_bce"]["response"] or {}).get("results") or []
    rows = [r for r in rows if r.get("date_cloture_exercice")]
    priority = PRIORITY_SOCIAL if social else PRIORITY_DEFAULT
    chosen, alternatives = pick_rows(rows, priority, exercices)
    anomalies: list[str] = []
    exercice_out = []
    previous = None
    for row in sorted(chosen, key=lambda r: r["date_cloture_exercice"]):
        date = row["date_cloture_exercice"]
        year = date[:4]
        items = {k: row.get(k) for k in ITEMS}
        ca, rn, ebe = items["chiffre_d_affaires"], items["resultat_net"], items["ebe"]
        computed = {
            "net_margin_pct": pct(rn, ca),
            "ebe_margin_pct": pct(ebe, ca),
            "turnover_change_pct": change_pct(ca, previous["chiffre_d_affaires"])
            if previous
            else None,
            "net_result_change_pct": change_pct(rn, previous["resultat_net"]) if previous else None,
        }
        published_ebe_margin = row.get("marge_ebe")
        if computed["ebe_margin_pct"] is not None and published_ebe_margin is not None:
            computed["ebe_margin_check_delta_pt"] = (
                computed["ebe_margin_pct"] - published_ebe_margin
            )
        # The annuaire keys its figures by year; a fiscal year closing in March 2025 is
        # listed under 2024 there, so the previous year is tried when the closing year is absent.
        annuaire_year = year if year in annuaire_finances else str(int(year) - 1)
        annuaire = annuaire_finances.get(annuaire_year) or {}
        cross = {
            "annuaire_year": annuaire_year if annuaire else None,
            "annuaire_turnover": annuaire.get("ca"),
            "annuaire_net_result": annuaire.get("resultat_net"),
            "turnover_matches": _close(ca, annuaire.get("ca")),
            "net_result_matches": _close(rn, annuaire.get("resultat_net")),
        }
        label = f"{date} ({row.get('type_bilan')})"
        if ca and rn == 0:
            anomalies.append(f"{label}: net result published as 0 with turnover {ca:,.0f}")
        if ca and items["marge_brute"] == ca:
            anomalies.append(f"{label}: gross margin equals turnover, item probably not filled")
        if ca and ebe is not None and ebe >= ca:
            anomalies.append(f"{label}: EBE equal to or above turnover, item probably not filled")
        if previous and all(items[k] == previous.get(k) for k in ITEMS) and ca:
            anomalies.append(f"{label}: items identical to the previous closing date")
        if previous and row.get("type_bilan") != previous.get("type_bilan"):
            anomalies.append(
                f"{label}: type of accounts differs from the previous closing date "
                f"({previous.get('type_bilan')}), changes are not comparable"
            )
        prev_ca = previous.get("chiffre_d_affaires") if previous else None
        if ca and prev_ca and (ca / prev_ca > 50 or prev_ca / ca > 50):
            anomalies.append(
                f"{label}: turnover differs from the previous closing date by a factor above 50, "
                "probably a unit mismatch in the filing"
            )
        exercice_out.append(
            {
                "closing_date": date,
                "type_bilan": row.get("type_bilan"),
                "type_bilan_label": TYPE_BILAN.get(row.get("type_bilan"), "unknown"),
                "confidentiality": row.get("confidentiality"),
                "alternatives_not_shown": alternatives.get(date, []),
                "items_eur": items,
                "ratios_published": {k: row.get(k) for k in RATIOS},
                "computed": computed,
                "cross_check_annuaire": cross,
            }
        )
        previous = row
    if not exercice_out:
        anomalies.append("no filed accounts in ratios_inpi_bce for this SIREN")

    first, last = (exercice_out[0], exercice_out[-1]) if exercice_out else (None, None)
    evolution = {
        "exercices_shown": len(exercice_out),
        "from": first["closing_date"] if first else None,
        "to": last["closing_date"] if last else None,
        "turnover_change_pct": change_pct(
            last["items_eur"]["chiffre_d_affaires"], first["items_eur"]["chiffre_d_affaires"]
        )
        if first and last and first is not last
        else None,
        "net_result_change_pct": change_pct(
            last["items_eur"]["resultat_net"], first["items_eur"]["resultat_net"]
        )
        if first and last and first is not last
        else None,
    }
    return {
        "script": "comptes.py",
        "sources_read_on": READ_ON,
        "fetched_on": data.get("fetched_on"),
        "mode": data.get("mode"),
        "identity": identity,
        "other_candidates": candidates,
        "annuaire_finances": annuaire_finances,
        "exercices": [_round_tree(e) for e in exercice_out],
        "evolution": _round_tree(evolution),
        "ratio_units": RATIOS,
        "anomalies": anomalies,
        "sources": [
            {
                "id": "recherche_entreprises",
                **SOURCES["recherche_entreprises"],
                "url": data["recherche_entreprises"]["url"],
                "fetched_on": data.get("fetched_on"),
            },
            {
                "id": "ratios_inpi_bce",
                **SOURCES["ratios_inpi_bce"],
                "url": data["ratios_inpi_bce"]["url"],
                "fetched_on": data.get("fetched_on"),
            },
        ],
        "disclaimer": "Figures as filed and published; computation only, not personalised advice.",
    }


def _close(a, b, rel: float = 0.005) -> bool | None:
    if a is None or b is None:
        return None
    if a == b:
        return True
    return abs(a - b) <= rel * max(abs(a), abs(b))


def _round_tree(obj):
    if isinstance(obj, float):
        return round(obj, 2)
    if isinstance(obj, dict):
        return {k: _round_tree(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_tree(v) for v in obj]
    return obj


def _fmt(v, unit=""):
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:,.2f}{unit}"
    return f"{v:,}{unit}"


def to_markdown(result: dict) -> str:
    idt = result["identity"]
    lines = [
        f"## {idt['name']} (SIREN {idt['siren']})",
        "",
        f"Legal form code {idt['legal_form_code']}, NAF {idt['main_activity_naf']}, category "
        f"{idt['company_category']}, created {idt['created_on']}, "
        f"state {idt['administrative_state']}, "
        f"head office {idt['head_office']}. Annuaire updated {idt['updated_on']}; data fetched "
        f"{result['fetched_on']} ({result['mode']}).",
        "",
        "| Closing date | Type | Turnover | Gross margin | EBE | EBIT | Net result | Net margin |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for e in result["exercices"]:
        it, c = e["items_eur"], e["computed"]
        lines.append(
            f"| {e['closing_date']} | {e['type_bilan']} | {_fmt(it['chiffre_d_affaires'])} | "
            f"{_fmt(it['marge_brute'])} | {_fmt(it['ebe'])} | {_fmt(it['ebit'])} | "
            f"{_fmt(it['resultat_net'])} | {_fmt(c['net_margin_pct'], ' %')} |"
        )
    if result["exercices"]:
        lines += [
            "",
            "| Ratio (as published) | "
            + " | ".join(e["closing_date"] for e in result["exercices"])
            + " |",
            "| --- | " + " | ".join("---:" for _ in result["exercices"]) + " |",
        ]
        for ratio, unit in RATIOS.items():
            cells = " | ".join(_fmt(e["ratios_published"].get(ratio)) for e in result["exercices"])
            lines.append(f"| {ratio} ({unit}) | {cells} |")
        lines += [
            "",
            "| Cross-check with the annuaire | "
            + " | ".join(e["closing_date"] for e in result["exercices"])
            + " |",
            "| --- | " + " | ".join("---:" for _ in result["exercices"]) + " |",
        ]
        for key in (
            "annuaire_turnover",
            "annuaire_net_result",
            "turnover_matches",
            "net_result_matches",
        ):
            cells = " | ".join(
                _fmt(e["cross_check_annuaire"].get(key)) for e in result["exercices"]
            )
            lines.append(f"| {key} | {cells} |")
    ev = result["evolution"]
    lines += [
        "",
        f"Evolution {ev['from']} to {ev['to']}: turnover {_fmt(ev['turnover_change_pct'], ' %')}, "
        f"net result {_fmt(ev['net_result_change_pct'], ' %')} "
        f"({ev['exercices_shown']} closing dates shown).",
    ]
    if result["anomalies"]:
        lines += ["", "Anomalies in the published data:"] + [f"- {a}" for a in result["anomalies"]]
    if result["other_candidates"]:
        lines += ["", "Other companies matching the name:"] + [
            f"- {c['name']} (SIREN {c['siren']})" for c in result["other_candidates"]
        ]
    lines += ["", "Sources:"]
    for s in result["sources"]:
        lines.append(f"- {s['name']}, {s['licence']}, fetched {s['fetched_on']}: {s['url']}")
    lines += ["", result["disclaimer"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--siren", help="nine-digit SIREN")
    group.add_argument("--name", help="company name to search in the annuaire (live only)")
    parser.add_argument("--recorded", help="directory of recorded responses (offline)")
    parser.add_argument("--record", help="save the raw live responses to this JSON file")
    parser.add_argument(
        "--social", action="store_true", help="prefer company accounts (C, S) over consolidated (K)"
    )
    parser.add_argument(
        "--exercices", type=int, default=3, help="closing dates to show (default 3)"
    )
    parser.add_argument("--markdown", action="store_true", help="print tables instead of JSON")
    args = parser.parse_args(argv)
    try:
        if args.siren is not None and not siren_is_valid(args.siren):
            raise InvalidInput(f"{args.siren!r} is not a valid SIREN (nine digits, Luhn key)")
        if args.recorded:
            if not args.siren:
                raise InvalidInput("--recorded needs --siren")
            data = load_recorded(Path(args.recorded), args.siren)
        else:
            data = fetch_live(args.siren, args.name)
            if args.record:
                doc = {
                    "siren": data["recherche_entreprises"]["response"]["results"][0]["siren"],
                    "recorded_on": data["fetched_on"],
                    "recherche_entreprises": data["recherche_entreprises"],
                    "ratios_inpi_bce": data["ratios_inpi_bce"],
                }
                Path(args.record).write_text(
                    json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        result = analyse(data, social=args.social, exercices=max(1, args.exercices))
    except ScriptError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, indent=2))
        return exc.exit_code
    print(
        to_markdown(result) if args.markdown else json.dumps(result, indent=2, ensure_ascii=False)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
