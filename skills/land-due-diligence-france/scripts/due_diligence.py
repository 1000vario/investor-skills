#!/usr/bin/env python3
"""Due diligence sheet for a parcel in France, computed from foncier-mcp tool outputs.

The model judges, the script computes. This script reads the JSON returned by foncier-mcp
(the `summarize_parcel` envelope, or the four envelopes of `get_parcel_sales`, `get_zoning`,
`get_energy_diagnostics` and `get_risks`) and produces:

- red flags first, each with its source id and retrieval time copied from the envelope;
- comparable sales within the radius: price per square metre, median, mean, range, dates;
- the gap between an asking price and the median price per square metre;
- the facts behind each flag (zone, flood zoning, DPE class, clay exposure, installations).

It never fetches anything and never fills a missing value: a field whose value is null in the
envelope stays "not available" with the reason the tool gave.

Usage:
  python due_diligence.py --input summary.json [--asking-price 250000] [--surface 80]
                          [--property-type Maison|Appartement] [--markdown]

Exit codes: 0 ok, 3 invalid input (the JSON is not a foncier-mcp envelope).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

DWELLING_TYPES = ("Maison", "Appartement")
NOT_CONSTRUCTIBLE_ZONE_TYPES = {
    "A": "agricultural zone, building limited to what the farm needs",
    "N": "natural zone, building limited to what the zone regulation allows",
    "Ah": "agricultural zone with a limited-size sector (STECAL), check the regulation",
    "Nh": "natural zone with a limited-size sector (STECAL), check the regulation",
    "Nd": "natural zone with a specific sector, check the regulation",
    "AUs": "zone to be urbanised, strict: closed until the planning document changes",
}
RADON_HIGH = {"3"}
CLAY_STRONG = {"3", 3}
CLAY_MEDIUM = {"2", 2}
GAP_ORANGE_PCT = 15.0
SEVERITY_ORDER = {"red": 0, "orange": 1, "info": 2}
DISCLAIMER = (
    "Facts as published by the sources named above, computation only, not personalised "
    "advice: the reader decides and checks the originals."
)


class InvalidInput(ValueError):
    pass


# ----------------------------------------------------------------------------- envelope access


def load_envelopes(raw: Any) -> dict[str, Any]:
    """Return a dict of sections: sales, zoning, energy_diagnostics, risks, plus header fields."""
    if isinstance(raw, dict) and raw.get("tool") == "summarize_parcel":
        data = raw.get("data") or {}
        if not isinstance(data, dict):
            raise InvalidInput("summarize_parcel envelope has no data")
        return {
            "parcel": data.get("parcel"),
            "geocoding": data.get("geocoding"),
            "centroid": data.get("centroid"),
            "sales": data.get("sales"),
            "zoning": data.get("zoning"),
            "energy_diagnostics": data.get("energy_diagnostics"),
            "risks": data.get("risks"),
            "warnings": list(raw.get("warnings") or []),
            "errors": list(raw.get("errors") or []),
            "sources": raw.get("sources") or {},
            "generated_at": raw.get("generated_at"),
            "input": raw.get("input") or {},
        }
    envelopes = raw.get("envelopes") if isinstance(raw, dict) else None
    if isinstance(envelopes, list):
        names = {
            "get_parcel_sales": "sales",
            "get_zoning": "zoning",
            "get_energy_diagnostics": "energy_diagnostics",
            "get_risks": "risks",
        }
        out: dict[str, Any] = {
            "parcel": None,
            "geocoding": None,
            "centroid": None,
            "sales": None,
            "zoning": None,
            "energy_diagnostics": None,
            "risks": None,
            "warnings": [],
            "errors": [],
            "sources": {},
            "generated_at": None,
            "input": {},
        }
        for env in envelopes:
            if not isinstance(env, dict) or env.get("tool") not in names:
                raise InvalidInput("each envelope needs a tool name among " + ", ".join(names))
            section = names[env["tool"]]
            data = env.get("data")
            if data is None:
                out[section] = {
                    "value": None,
                    "source": next(
                        (e.get("source") for e in env.get("errors") or [] if e.get("source")),
                        None,
                    ),
                    "retrieved_at": None,
                    "reason": "; ".join(
                        e.get("message", e.get("code", "error")) for e in env.get("errors") or []
                    )
                    or "tool returned no data",
                }
            else:
                out[section] = data
                if out["parcel"] is None and isinstance(data, dict) and data.get("parcel"):
                    out["parcel"] = data["parcel"]
            out["warnings"] += list(env.get("warnings") or [])
            out["errors"] += list(env.get("errors") or [])
            out["sources"].update(env.get("sources") or {})
            out["generated_at"] = out["generated_at"] or env.get("generated_at")
            out["input"].update(env.get("input") or {})
        return out
    raise InvalidInput(
        'input must be a summarize_parcel envelope or {"envelopes": [tool envelopes]}'
    )


def is_sourced(field: Any) -> bool:
    return isinstance(field, dict) and {"value", "source", "retrieved_at"} <= set(field)


def val(field: Any) -> Any:
    """Value of a sourced field, or None."""
    return field.get("value") if is_sourced(field) else None


def meta(field: Any) -> dict[str, Any]:
    """Source, retrieval date and reason of a sourced field or of a computed block."""
    if isinstance(field, dict) and "source" in field:
        return {
            "source": field.get("source"),
            "retrieved_at": field.get("retrieved_at"),
            "reason": field.get("reason"),
        }
    return {"source": None, "retrieved_at": None, "reason": "field absent"}


def section_missing(section: Any) -> str | None:
    """Reason when a whole section is unavailable, else None."""
    if section is None:
        return "section absent from the input"
    if is_sourced(section) and section.get("value") is None:
        return section.get("reason") or "section unavailable"
    return None


# ----------------------------------------------------------------------------- comparables


def dwelling_area(mutation: dict[str, Any], property_type: str | None) -> tuple[int, int]:
    """Total built area of the dwellings of a mutation and their count."""
    area, count = 0, 0
    for b in mutation.get("buildings") or []:
        btype = b.get("type")
        if btype not in DWELLING_TYPES:
            continue
        if property_type and btype != property_type:
            continue
        a = b.get("built_area_m2") or 0
        if a > 0:
            area += a
            count += 1
    return area, count


def comparables(sales: Any, property_type: str | None) -> dict[str, Any]:
    reason = section_missing(sales)
    if reason:
        return {"available": False, "reason": reason, **meta(sales)}
    nearby_field = sales.get("sales_nearby")
    radius = val(sales.get("sales_nearby_radius_m"))
    if not is_sourced(nearby_field) or nearby_field.get("value") is None:
        return {
            "available": False,
            "reason": meta(nearby_field).get("reason") or "no radius search was requested",
            **meta(nearby_field),
        }
    rows = []
    skipped = {"not_a_sale": 0, "no_dwelling_area": 0, "no_price": 0, "other_type": 0}
    for m in nearby_field["value"]:
        if (m.get("nature") or "") != "Vente":
            skipped["not_a_sale"] += 1
            continue
        price = m.get("price_eur") or 0
        if price <= 0:
            skipped["no_price"] += 1
            continue
        area, count = dwelling_area(m, property_type)
        if area <= 0:
            if property_type and dwelling_area(m, None)[0] > 0:
                skipped["other_type"] += 1
            else:
                skipped["no_dwelling_area"] += 1
            continue
        rows.append(
            {
                "mutation_id": m.get("mutation_id"),
                "date": m.get("date"),
                "address": m.get("address"),
                "commune": m.get("commune"),
                "types": sorted({b.get("type") for b in m.get("buildings") or [] if b.get("type")}),
                "dwellings": count,
                "built_area_m2": area,
                "price_eur": price,
                "price_per_m2": round(price / area, 2),
                "distance_m": m.get("distance_m"),
            }
        )
    rows.sort(key=lambda r: (r["distance_m"] if r["distance_m"] is not None else 1e9, r["date"]))
    ppm = [r["price_per_m2"] for r in rows]
    stats = None
    if ppm:
        q = statistics.quantiles(ppm, n=4, method="inclusive") if len(ppm) >= 2 else [None] * 3
        stats = {
            "count": len(ppm),
            "median_eur_m2": round(statistics.median(ppm), 2),
            "mean_eur_m2": round(statistics.fmean(ppm), 2),
            "min_eur_m2": min(ppm),
            "max_eur_m2": max(ppm),
            "p25_eur_m2": round(q[0], 2) if q[0] is not None else None,
            "p75_eur_m2": round(q[2], 2) if q[2] is not None else None,
            "from": min(r["date"] for r in rows if r["date"])
            if any(r["date"] for r in rows)
            else None,
            "to": max(r["date"] for r in rows if r["date"])
            if any(r["date"] for r in rows)
            else None,
            "multi_dwelling_sales": sum(1 for r in rows if r["dwellings"] > 1),
        }
    return {
        "available": True,
        "radius_m": radius,
        "property_type_filter": property_type,
        "years": val(sales.get("years")),
        "mutations_in_radius": len(nearby_field["value"]),
        "skipped": skipped,
        "rows": rows,
        "stats": stats,
        "reason": None if stats else "no sale of a dwelling with a built area within the radius",
        **meta(nearby_field),
    }


def sales_on_parcel(sales: Any) -> dict[str, Any]:
    if section_missing(sales):
        return {"available": False, "reason": section_missing(sales), **meta(sales)}
    field = sales.get("sales_on_parcel")
    if not is_sourced(field) or field.get("value") is None:
        return {"available": False, **meta(field)}
    rows = [
        {
            "date": m.get("date"),
            "nature": m.get("nature"),
            "price_eur": m.get("price_eur"),
            "built_area_m2": dwelling_area(m, None)[0] or None,
            "land_m2": sum((lnd.get("area_m2") or 0) for lnd in m.get("land") or []) or None,
        }
        for m in field["value"]
    ]
    return {"available": True, "rows": rows, **meta(field)}


def asking_gap(stats: dict[str, Any] | None, asking_price: float | None, surface: float | None):
    if asking_price is None or surface is None:
        return {"available": False, "reason": "asking price or surface not given"}
    if surface <= 0 or asking_price <= 0:
        raise InvalidInput("asking price and surface must be positive")
    asking_m2 = round(asking_price / surface, 2)
    if not stats:
        return {"available": False, "asking_eur_m2": asking_m2, "reason": "no comparable sale"}
    median = stats["median_eur_m2"]
    gap = round((asking_m2 / median - 1) * 100, 2)
    return {
        "available": True,
        "asking_price_eur": asking_price,
        "surface_m2": surface,
        "asking_eur_m2": asking_m2,
        "median_eur_m2": median,
        "gap_to_median_pct": gap,
        "median_value_for_surface_eur": round(median * surface, 0),
    }


# ----------------------------------------------------------------------------- flags


def flag(severity: str, code: str, message: str, field: Any = None, **extra) -> dict[str, Any]:
    out = {"severity": severity, "code": code, "message": message, **meta(field)}
    out.pop("reason", None)
    out.update(extra)
    return out


def zoning_flags(zoning: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    facts: dict[str, Any] = {}
    reason = section_missing(zoning)
    if reason:
        flags.append(
            flag("orange", "zoning_unavailable", f"zoning not available: {reason}", zoning)
        )
        return flags, {"available": False, "reason": reason}
    muni = val(zoning.get("municipality")) or {}
    facts["municipality"] = {**muni, **meta(zoning.get("municipality"))}
    if muni.get("is_rnu"):
        flags.append(
            flag(
                "orange",
                "rnu",
                "commune under the RNU, no local planning document: building is only possible "
                "in the already urbanised parts, check with the commune",
                zoning.get("municipality"),
            )
        )
    zones_field = zoning.get("zones")
    zones = val(zones_field)
    facts["zones"] = {"value": zones, **meta(zones_field)}
    if zones is None and not muni.get("is_rnu"):
        sectors_field = zoning.get("carte_communale_sectors")
        sectors = val(sectors_field)
        if sectors:
            facts["carte_communale_sectors"] = {"value": sectors, **meta(sectors_field)}
            for s in sectors:
                if str(s.get("type_code")) != "01":
                    flags.append(
                        flag(
                            "red",
                            "not_constructible",
                            "carte communale sector "
                            f"{s.get('type_code')} ({s.get('meaning') or 'meaning unknown'}): "
                            "not the constructible sector",
                            sectors_field,
                        )
                    )
        else:
            flags.append(
                flag(
                    "orange",
                    "zoning_unknown",
                    "no zone found at this point: " + (meta(zones_field).get("reason") or ""),
                    zones_field,
                )
            )
    for z in zones or []:
        ztype = z.get("type")
        if ztype in NOT_CONSTRUCTIBLE_ZONE_TYPES:
            flags.append(
                flag(
                    "red",
                    "not_constructible",
                    f"zone {z.get('label')} of type {ztype}: {NOT_CONSTRUCTIBLE_ZONE_TYPES[ztype]}"
                    + (
                        f" (document validated on {z.get('validated_on')})"
                        if z.get("validated_on")
                        else ""
                    ),
                    zones_field,
                    zone=z.get("label"),
                    regulation_url=z.get("regulation_url"),
                )
            )
        elif ztype in ("AU", "AUc"):
            flags.append(
                flag(
                    "orange",
                    "zone_to_be_urbanised",
                    f"zone {z.get('label')} of type {ztype}: constructible only under the "
                    "conditions of the planning document (networks, operation)",
                    zones_field,
                    zone=z.get("label"),
                )
            )
    presc_field = zoning.get("prescriptions")
    presc = val(presc_field) or []
    facts["prescriptions"] = {"value": presc, **meta(presc_field)}
    if presc:
        flags.append(
            flag(
                "info",
                "prescriptions",
                f"{len(presc)} planning prescription(s) apply: "
                + "; ".join(str(p.get("label")) for p in presc[:5]),
                presc_field,
            )
        )
    ease_field = zoning.get("public_utility_easements")
    easements = val(ease_field) or []
    facts["public_utility_easements"] = {"value": easements, **meta(ease_field)}
    for e in easements:
        cat = str(e.get("category") or "")
        sev = "orange" if cat.startswith("PM") else "info"
        flags.append(
            flag(
                sev,
                "easement",
                f"public utility easement {cat} {e.get('name') or ''}".strip()
                + (" (risk prevention plan)" if cat.startswith("PM") else ""),
                ease_field,
            )
        )
    facts["available"] = True
    return flags, facts


def dpe_flags(energy: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    reason = section_missing(energy)
    if reason:
        flags.append(
            flag("info", "dpe_unavailable", f"energy diagnostics not available: {reason}", energy)
        )
        return flags, {"available": False, "reason": reason}
    diag_field = energy.get("diagnostics")
    records = val(diag_field)
    if records is None:
        why = meta(diag_field).get("reason") or "no diagnostic attached"
        flags.append(
            flag("info", "dpe_unavailable", f"energy diagnostics not available: {why}", diag_field)
        )
        return flags, {"available": False, "reason": why, **meta(diag_field)}
    if not records:
        flags.append(flag("info", "dpe_none", "no DPE recorded at this address", diag_field))
        return flags, {"available": True, "latest": None, "count": 0, **meta(diag_field)}
    latest = records[0]
    cls = latest.get("energy_class")
    facts = {
        "available": True,
        "count": len(records),
        "latest": latest,
        "classes_seen": sorted({r.get("energy_class") for r in records if r.get("energy_class")}),
        **meta(diag_field),
    }
    if cls in ("F", "G"):
        flags.append(
            flag(
                "red",
                "dpe_f_or_g",
                f"latest DPE class {cls} (issued {latest.get('issued_on')}, valid until "
                f"{latest.get('valid_until')}): energy-inefficient dwelling, letting restrictions "
                "apply by date of class, see references/sources-foncier.md",
                diag_field,
                energy_class=cls,
            )
        )
    elif cls == "E":
        flags.append(
            flag(
                "orange",
                "dpe_e",
                f"latest DPE class E (issued {latest.get('issued_on')}): next class to be "
                "restricted, see references/sources-foncier.md",
                diag_field,
                energy_class=cls,
            )
        )
    if len(records) > 1 and len(facts["classes_seen"]) > 1:
        flags.append(
            flag(
                "info",
                "dpe_several",
                f"{len(records)} DPE at this address with classes "
                + ", ".join(facts["classes_seen"])
                + ": several dwellings or a renovated one, check which applies",
                diag_field,
            )
        )
    return flags, facts


def risk_flags(risks: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    reason = section_missing(risks)
    if reason:
        flags.append(flag("orange", "risks_unavailable", f"risks not available: {reason}", risks))
        return flags, {"available": False, "reason": reason}
    facts: dict[str, Any] = {"available": True}
    commune = risks.get("commune") or {}
    point = risks.get("point")

    inv_field = commune.get("risks_inventory")
    inventory = val(inv_field) or []
    labels = [str(r.get("label")) for r in inventory]
    facts["commune_risks"] = {"value": labels, **meta(inv_field)}

    if is_sourced(point) and point.get("value") is None:
        flags.append(
            flag("orange", "point_risks_unavailable", meta(point).get("reason") or "", point)
        )
        point = None
    if point:
        fz_field = point.get("flood_zoning_100m")
        fz = val(fz_field)
        facts["flood_zoning_100m"] = {"value": fz, **meta(fz_field)}
        if fz and (fz.get("count") or 0) > 0:
            items = fz.get("items") or []
            desc = "; ".join(
                f"{i.get('tri_label') or i.get('tri_code')} {i.get('flood_type') or ''} "
                f"{i.get('scenario') or ''}".strip()
                for i in items[:3]
            )
            flags.append(
                flag(
                    "red",
                    "flood_zone",
                    f"flood zoning within 100 m ({fz.get('count')} feature(s)): {desc}",
                    fz_field,
                )
            )
        elif fz is None and is_sourced(fz_field):
            flags.append(
                flag(
                    "orange",
                    "flood_unknown",
                    "flood zoning not available: " + (meta(fz_field).get("reason") or ""),
                    fz_field,
                )
            )

        clay_field = point.get("clay_shrink_swell")
        clay = val(clay_field)
        facts["clay_shrink_swell"] = {"value": clay, **meta(clay_field)}
        if clay:
            code = clay.get("exposure_code")
            if code in CLAY_STRONG:
                flags.append(
                    flag(
                        "orange",
                        "clay_strong",
                        f"clay shrink-swell exposure: {clay.get('label')} (strong)",
                        clay_field,
                    )
                )
            elif code in CLAY_MEDIUM:
                flags.append(
                    flag(
                        "info",
                        "clay_medium",
                        f"clay shrink-swell exposure: {clay.get('label')} (medium)",
                        clay_field,
                    )
                )

        icpe_field = point.get("classified_installations_1km")
        icpe = val(icpe_field)
        facts["classified_installations_1km"] = {"value": icpe, **meta(icpe_field)}
        if icpe:
            seveso = [
                i
                for i in icpe.get("items") or []
                if i.get("seveso_status")
                and str(i.get("seveso_status")).lower() not in ("non seveso", "none", "null")
            ]
            if seveso:
                flags.append(
                    flag(
                        "orange",
                        "seveso_nearby",
                        f"{len(seveso)} Seveso installation(s) within 1 km: "
                        + "; ".join(
                            f"{i.get('name')} ({i.get('seveso_status')})" for i in seveso[:3]
                        ),
                        icpe_field,
                    )
                )
            elif (icpe.get("count") or 0) > 0:
                flags.append(
                    flag(
                        "info",
                        "icpe_nearby",
                        f"{icpe.get('count')} classified installation(s) within 1 km, none Seveso",
                        icpe_field,
                    )
                )

        for key, label in (
            ("underground_cavities_500m", "underground cavity"),
            ("ground_movements_500m", "ground movement"),
        ):
            f = point.get(key)
            v = val(f)
            facts[key] = {"value": v, **meta(f)}
            if v and (v.get("count") or 0) > 0:
                flags.append(
                    flag("orange", key, f"{v.get('count')} {label}(s) recorded within 500 m", f)
                )

    catnat_field = commune.get("natural_disaster_orders")
    catnat = val(catnat_field)
    facts["natural_disaster_orders"] = {"value": catnat, **meta(catnat_field)}
    if catnat and (catnat.get("count") or 0) > 0:
        orders = catnat.get("orders") or []
        hazards = sorted({str(o.get("hazard")) for o in orders if o.get("hazard")})
        flags.append(
            flag(
                "info",
                "catnat_orders",
                f"{catnat.get('count')} natural disaster order(s) in the commune: "
                + ", ".join(hazards[:4]),
                catnat_field,
            )
        )
    for key in ("seismic_zone", "radon_potential"):
        f = commune.get(key)
        v = val(f)
        facts[key] = {"value": v, **meta(f)}
    radon = facts["radon_potential"]["value"]
    if radon and str(radon.get("class")) in RADON_HIGH:
        flags.append(
            flag(
                "info",
                "radon_high",
                f"radon potential class {radon.get('class')}: {radon.get('meaning') or ''}",
                commune.get("radon_potential"),
            )
        )
    tri_field = commune.get("flood_risk_territories")
    tri = val(tri_field)
    facts["flood_risk_territories"] = {"value": tri, **meta(tri_field)}
    if tri and not any(fl["code"] == "flood_zone" for fl in flags):
        flags.append(
            flag(
                "info",
                "commune_in_tri",
                f"commune inside {len(tri)} flood risk territory(ies), "
                "no flood zoning at the point",
                tri_field,
            )
        )
    return flags, facts


def sales_flags(
    comp: dict[str, Any], parcel_sales: dict[str, Any], gap: dict[str, Any]
) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    if not comp.get("available"):
        flags.append(
            flag("red", "sales_unavailable", f"sales not available: {comp.get('reason')}", comp)
        )
    elif not comp.get("stats"):
        flags.append(
            flag(
                "red",
                "no_comparable_sale",
                f"no sale of a dwelling with a built area within {comp.get('radius_m')} m over "
                f"{', '.join(comp.get('years') or []) or 'the requested years'} "
                f"({comp.get('mutations_in_radius')} mutation(s) seen, none usable)",
                comp,
            )
        )
    else:
        s = comp["stats"]
        if s["count"] < 3:
            flags.append(
                flag(
                    "orange",
                    "few_comparables",
                    f"only {s['count']} comparable sale(s), the median is fragile",
                    comp,
                )
            )
        if s["max_eur_m2"] > 3 * s["median_eur_m2"] or s["min_eur_m2"] < s["median_eur_m2"] / 3:
            flags.append(
                flag(
                    "info",
                    "outlier",
                    "a comparable is more than three times away from the median; "
                    "the median is kept, the mean is not reliable",
                    comp,
                )
            )
    if gap.get("available"):
        g = gap["gap_to_median_pct"]
        if g > GAP_ORANGE_PCT:
            flags.append(
                flag(
                    "orange",
                    "asking_above_median",
                    f"asking price {g:+.1f} % against the median price per square metre "
                    "of comparables",
                    comp,
                )
            )
        elif g < -GAP_ORANGE_PCT:
            flags.append(
                flag(
                    "info",
                    "asking_below_median",
                    f"asking price {g:+.1f} % against the median: check the reason "
                    "(condition, surface, legal situation)",
                    comp,
                )
            )
    if parcel_sales.get("available") and parcel_sales.get("rows"):
        last = parcel_sales["rows"][0]
        flags.append(
            flag(
                "info",
                "parcel_sold_before",
                f"the parcel itself was sold on {last.get('date')} for {last.get('price_eur')} EUR",
                parcel_sales,
            )
        )
    return flags


# ----------------------------------------------------------------------------- compose


def analyse(
    raw: Any,
    *,
    asking_price: float | None = None,
    surface: float | None = None,
    property_type: str | None = None,
) -> dict[str, Any]:
    env = load_envelopes(raw)
    parcel = val(env["parcel"]) or {}
    comp = comparables(env["sales"], property_type)
    on_parcel = sales_on_parcel(env["sales"])
    gap = asking_gap(comp.get("stats"), asking_price, surface)
    flags = sales_flags(comp, on_parcel, gap)
    zflags, zfacts = zoning_flags(env["zoning"])
    dflags, dfacts = dpe_flags(env["energy_diagnostics"])
    rflags, rfacts = risk_flags(env["risks"])
    flags += zflags + dflags + rflags
    for e in env["errors"]:
        flags.append(
            {
                "severity": "orange",
                "code": "tool_error",
                "message": f"{e.get('code')}: {e.get('message')}",
                "source": e.get("source"),
                "retrieved_at": None,
            }
        )
    flags.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))
    return {
        "subject": {
            "parcel_id": parcel.get("id"),
            "code_insee": parcel.get("code_insee"),
            "commune": (zfacts.get("municipality") or {}).get("name")
            if zfacts.get("available")
            else None,
            "centroid": val(env["centroid"]) or parcel.get("centroid"),
            "address": (val(env["geocoding"]) or {}).get("label") if env["geocoding"] else None,
            "fixture": env["input"].get("fixture"),
        },
        "flags": flags,
        "counts": {s: sum(1 for f in flags if f["severity"] == s) for s in SEVERITY_ORDER},
        "asking": gap,
        "comparables": comp,
        "sales_on_parcel": on_parcel,
        "zoning": zfacts,
        "energy": dfacts,
        "risks": rfacts,
        "warnings": env["warnings"],
        "sources": env["sources"],
        "generated_at": env["generated_at"],
        "disclaimer": DISCLAIMER,
    }


def _fmt(v: Any, unit: str = "") -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:,.2f}{unit}".replace(",", " ")
    if isinstance(v, int):
        return f"{v:,}{unit}".replace(",", " ")
    return str(v)


def _src(m: dict[str, Any]) -> str:
    """Short source tag for a dict that carries source and retrieved_at."""
    when = f", retrieved {m.get('retrieved_at')}" if m.get("retrieved_at") else ""
    return f"[source: {m.get('source') or 'n/a'}{when}]"


def to_markdown(r: dict[str, Any]) -> str:
    s = r["subject"]
    c = r["counts"]
    lines = [f"## Due diligence sheet, parcel {s.get('parcel_id') or 'unknown'}"]
    where = [x for x in (s.get("address"), s.get("commune") and f"({s['commune']})") if x]
    if where:
        lines.append(" ".join(where))
    if s.get("fixture"):
        lines.append(f"Data: {s['fixture']} fixture, not a real parcel.")
    lines += ["", f"Flags: {c['red']} red, {c['orange']} orange, {c['info']} info", ""]
    for f in r["flags"]:
        lines.append(f"- [{f['severity'].upper()}] {f['message']} {_src(f)}")
    comp = r["comparables"]
    lines += ["", "### Comparable sales"]
    if comp.get("available") and comp.get("stats"):
        st = comp["stats"]
        lines.append(
            f"{st['count']} sale(s) of dwellings within {comp.get('radius_m')} m, "
            f"{st['from']} to {st['to']}: median {_fmt(st['median_eur_m2'], ' EUR/m2')}, "
            f"mean {_fmt(st['mean_eur_m2'], ' EUR/m2')}, range {_fmt(st['min_eur_m2'])} to "
            f"{_fmt(st['max_eur_m2'])}, p25 {_fmt(st['p25_eur_m2'])}, p75 {_fmt(st['p75_eur_m2'])}."
        )
        lines += [
            "",
            "| date | address | type | m2 | price EUR | EUR/m2 | distance m |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in comp["rows"][:15]:
            lines.append(
                f"| {row['date']} | {row.get('address') or ''} | {'/'.join(row['types'])} | "
                f"{row['built_area_m2']} | {_fmt(row['price_eur'])} | "
                f"{_fmt(row['price_per_m2'])} | {_fmt(row['distance_m'])} |"
            )
        lines.append(f"{_src(comp)} skipped: {comp['skipped']}.")
    else:
        lines.append(f"Not available: {comp.get('reason')}.")
    a = r["asking"]
    if a.get("available"):
        lines += [
            "",
            f"Asking price {_fmt(a['asking_price_eur'])} EUR for {_fmt(a['surface_m2'])} m2 "
            f"= {_fmt(a['asking_eur_m2'])} EUR/m2, {a['gap_to_median_pct']:+.1f} % against "
            f"the median ({_fmt(a['median_value_for_surface_eur'])} EUR at the median for "
            "this surface).",
        ]
    elif a.get("asking_eur_m2"):
        lines += ["", f"Asking price {_fmt(a['asking_eur_m2'])} EUR/m2, no comparable sale."]
    z = r["zoning"]
    lines += ["", "### Zoning"]
    if z.get("available"):
        zf = z.get("zones") or {}
        zones = zf.get("value") or []
        if zones:
            text = "; ".join(
                f"{x.get('label')} ({x.get('type')}, {x.get('type_meaning') or ''}, "
                f"validated {x.get('validated_on')})"
                for x in zones
            )
        else:
            text = f"none ({zf.get('reason') or 'RNU'})"
        lines.append(f"Zones: {text} {_src(zf)}")
    else:
        lines.append(f"Not available: {z.get('reason')}.")
    e = r["energy"]
    lines += ["", "### Energy diagnostics"]
    if e.get("available") and e.get("latest"):
        lt = e["latest"]
        lines.append(
            f"Latest DPE: class {lt.get('energy_class')} (GHG {lt.get('ghg_class')}), "
            f"issued {lt.get('issued_on')}, valid until {lt.get('valid_until')}, "
            f"{lt.get('living_area_m2')} m2, {lt.get('building_type')} {_src(e)}"
        )
    else:
        lines.append(f"Not available: {e.get('reason') or 'no DPE recorded'}.")
    rk = r["risks"]
    lines += ["", "### Risks"]
    if rk.get("available"):
        cr = rk.get("commune_risks", {})
        inventory = ", ".join(cr.get("value") or []) or "none"
        lines.append(f"Commune inventory: {inventory} {_src(cr)}")
        for key in (
            "flood_zoning_100m",
            "clay_shrink_swell",
            "classified_installations_1km",
            "natural_disaster_orders",
        ):
            v = (rk.get(key) or {}).get("value")
            if v is None:
                continue
            short = v.get("count") if "count" in v else v.get("label")
            lines.append(f"{key}: {short} {_src(rk[key])}")
    else:
        lines.append(f"Not available: {rk.get('reason')}.")
    if r["warnings"]:
        lines += ["", "Warnings from the tools:"] + [f"- {w}" for w in r["warnings"]]
    lines += ["", "Sources:"]
    for sid, m in (r["sources"] or {}).items():
        lines.append(f"- {sid}: {m.get('name')} ({m.get('url')}, {m.get('licence')})")
    lines += ["", r["disclaimer"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--input", required=True, help="JSON file: summarize_parcel envelope or {envelopes: [...]}"
    )
    ap.add_argument("--asking-price", type=float, default=None, help="asking price in EUR")
    ap.add_argument(
        "--surface", type=float, default=None, help="built surface of the property for sale, m2"
    )
    ap.add_argument(
        "--property-type",
        choices=DWELLING_TYPES,
        default=None,
        help="restrict comparables to this type",
    )
    ap.add_argument(
        "--markdown", action="store_true", help="print the one-page sheet instead of JSON"
    )
    args = ap.parse_args(argv)
    try:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = analyse(
            raw,
            asking_price=args.asking_price,
            surface=args.surface,
            property_type=args.property_type,
        )
    except (InvalidInput, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"error": "invalid_input", "message": str(exc)}, indent=2))
        return 3
    if args.markdown:
        sys.stdout.write(to_markdown(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
