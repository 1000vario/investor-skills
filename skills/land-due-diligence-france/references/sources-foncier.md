# Sources, contract and rules of land-due-diligence-france

Everything the script reads comes from a foncier-mcp envelope. Everything the script decides is
listed here with its threshold. Dates are the day the rule or the page was read.

## 1. Data sources (as declared by foncier-mcp, read 2026-10-11)

foncier-mcp stamps every field with a source id, the retrieval time and, when the field is
empty, a reason. The script copies these stamps on every flag and every line of the sheet.

| Source id | Producer and dataset | URL | Licence |
| --- | --- | --- | --- |
| `geoplateforme_geocodage` | IGN Geoplateforme geocoding service (Base Adresse Nationale index) | https://data.geopf.fr/geocodage | Licence Ouverte / Open Licence 2.0 (Etalab) |
| `apicarto_cadastre` | IGN API Carto, cadastre module (Parcellaire Express PCI) | https://apicarto.ign.fr/api/cadastre | Licence Ouverte 2.0 |
| `apicarto_gpu` | IGN API Carto, urbanisme module (Geoportail de l'urbanisme) | https://apicarto.ign.fr/api/gpu | Licence Ouverte 2.0 |
| `dvf_geo` | Demandes de valeurs foncieres geolocalisees (DGFiP data, Etalab files) | https://files.data.gouv.fr/geo-dvf/latest/csv/ | Licence Ouverte 2.0 |
| `ademe_dpe` | ADEME, diagnostics de performance energetique (existing and new dwellings) | https://data.ademe.fr/datasets/dpe03existant | Licence Ouverte 2.0 |
| `georisques` | Georisques API v1 (BRGM and Ministere de la Transition ecologique) | https://georisques.gouv.fr/api/v1 | Licence Ouverte 2.0, per the site footer |
| `foncier_mcp` | Values computed by foncier-mcp from the sources above (geometry, distances, grouping) | https://github.com/1000vario/foncier-mcp | MIT |

Known coverage limits, as reported by the sources:

- DVF does not cover the departments 57, 67 and 68 (Alsace-Moselle land registry) nor Mayotte
  (976). foncier-mcp answers with a `not_covered` error and the script raises a red flag
  `sales_unavailable` instead of inventing a price.
- DVF files hold the last five years of mutations. The years used are in the `years` field.
- The DPE lookup needs a house-number address within reach of the parcel. Without one, the
  diagnostics field is missing and the flag is `dpe_unavailable`, which is informational.
- Zoning is read at the parcel centroid. A parcel crossing two zones shows the zone of its
  centroid only.

## 2. Envelope contract (foncier-mcp, models.py and tools.py read 2026-10-11)

```json
{
  "ok": true, "tool": "summarize_parcel", "input": {"parcel_id": "...", "radius_m": 500},
  "data": {
    "parcel": {"value": {"...": "..."}, "source": "apicarto_cadastre", "retrieved_at": "...", "reason": null},
    "geocoding": "sourced field", "centroid": "sourced field",
    "sales": {"files": "...", "years": "...", "sales_on_parcel": "...", "sales_on_parcel_count": "...",
              "sales_nearby": "...", "sales_nearby_radius_m": "..."},
    "zoning": {"municipality": "...", "planning_document": "...", "zones": "...", "prescriptions": "...",
               "public_utility_easements": "...", "lookup_point": "..."},
    "energy_diagnostics": {"address": "...", "diagnostics": "...", "counts": "..."},
    "risks": {"insee": "...", "commune": {"...": "..."}, "point": {"...": "..."}}
  },
  "warnings": [], "errors": [], "sources": {"<id>": {"name": "", "url": "", "licence": ""}},
  "generated_at": "...", "duration_ms": 0
}
```

Each leaf is a sourced field `{"value", "source", "retrieved_at", "reason"}`. A missing field has
`value: null` and a `reason`. A whole section that failed is itself a missing field. The script
also accepts `{"envelopes": [...]}` with the four envelopes of `get_parcel_sales`, `get_zoning`,
`get_energy_diagnostics` and `get_risks`; an envelope with `data: null` becomes a missing section
whose reason is the error message.

## 3. Computation rules

Comparable sales (`comparables`):

- Keep the mutations of `sales_nearby` whose `nature` is `Vente` and whose price is positive.
- The surface is the sum of `built_area_m2` of the buildings of type `Maison` or `Appartement`
  (both, or one type when `--property-type` is given). Mutations without such a surface are
  skipped and counted (`skipped`), never estimated.
- Price per square metre of a mutation = `price_eur / surface`, rounded to 2 decimals.
- Statistics: count, median, mean, min, max, quartiles (inclusive method, from 2 rows). The
  median is the figure to quote; the mean is reported but is sensitive to outliers.
- Asking gap = `(asking_price / surface) / median - 1`, in percent, 2 decimals. Without an asking
  price or without comparables, no gap is computed.

Flags, from most to least severe. Every flag carries the source and retrieval time of the field
it was computed from.

| Code | Severity | Condition |
| --- | --- | --- |
| `sales_unavailable` | red | the sales section or the radius search is missing |
| `no_comparable_sale` | red | no usable mutation in the radius |
| `not_constructible` | red | zone type A, N, Ah, Nh, Nd or AUs, or carte communale sector other than `01` |
| `dpe_f_or_g` | red | latest DPE class F or G, see section 4 |
| `flood_zone` | red | `flood_zoning_100m.count` > 0 at the point |
| `asking_above_median` | orange | gap > +15 % |
| `few_comparables` | orange | fewer than 3 comparable sales |
| `zoning_unavailable`, `zoning_unknown`, `rnu` | orange | zoning missing, no zone at the point, commune under the RNU |
| `zone_to_be_urbanised` | orange | zone type AU or AUc |
| `easement` | orange if category starts with `PM` (risk prevention plan), else info | a public utility easement applies |
| `dpe_e` | orange | latest DPE class E, see section 4 |
| `risks_unavailable`, `point_risks_unavailable`, `flood_unknown` | orange | the risks section, the point block or the flood block is missing |
| `clay_strong` | orange | clay shrink-swell exposure code 3 |
| `seveso_nearby` | orange | a classified installation within 1 km with a Seveso status |
| `underground_cavities_500m`, `ground_movements_500m` | orange | count > 0 within 500 m |
| `tool_error` | orange | an error entry in the envelope, message copied |
| `asking_below_median` | info | gap < -15 % |
| `outlier` | info | max > 3 x median or min < median / 3 |
| `parcel_sold_before` | info | the parcel itself appears in DVF |
| `prescriptions` | info | planning prescriptions apply |
| `dpe_unavailable`, `dpe_none`, `dpe_several` | info | no address match, no DPE, several DPE with different classes |
| `clay_medium`, `icpe_nearby`, `catnat_orders`, `radon_high`, `commune_in_tri` | info | medium clay exposure, non-Seveso installations, natural disaster orders, radon class 3, commune inside a flood risk territory without zoning at the point |

Zone types follow the GPU vocabulary: U urban, AU and AUc to be urbanised, AUs strict to be
urbanised, A agricultural, N natural, Ah and Nh limited-size sectors (STECAL), Nd specific natural.
Carte communale sectors: `01` constructible, `02` reserved for activities, `03` non constructible.

## 4. DPE and letting: the legal calendar (read 2026-10-11)

The flags `dpe_f_or_g` and `dpe_e` rest on the decent dwelling rule, not on a market opinion.

- Loi n° 89-462 du 6 juillet 1989, article 6, consolidated version in force since 2025-01-01
  (modified by loi n° 2021-1104 du 22 août 2021, art. 160), read on Légifrance on 2026-10-11:
  https://www.legifrance.gouv.fr/loda/id/JORFTEXT000000509310. The landlord must deliver a
  decent dwelling meeting a minimum performance level within the meaning of article L. 173-1-1 of
  the code de la construction et de l'habitation. The article states the calendar: from
  2025-01-01 classes A to F are allowed (G excluded), from 2028-01-01 classes A to E (F excluded),
  from 2034-01-01 classes A to D (E excluded). In Guadeloupe, Martinique, Guyane, La Réunion and
  Mayotte: A to F from 2028-01-01, A to E from 2031-01-01.
- Code de la construction et de l'habitation, article L. 173-1-1, version in force since
  2021-08-25, created by loi n° 2021-1104 du 22 août 2021, art. 148, read on Légifrance on
  2026-10-11: https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000043966496. Defines the
  seven classes A to G of existing dwellings by primary energy and greenhouse gas emissions.
- Ministère de la Transition écologique, page "Diagnostic de performance énergétique (DPE)",
  updated 2025-12-19, read 2026-10-11:
  https://www.ecologie.gouv.fr/politiques-publiques/diagnostic-performance-energetique-dpe.
  Confirms the national calendar (G from 2025, F from 2028, E from 2034), the 450 kWh final
  energy threshold since 2023-01-01, and the ten-year validity of a DPE with the exceptions for
  DPE issued before 2021-07-01.
- Décret n° 2023-796 du 18 août 2023 sets the thresholds in the decent dwelling decree. It was
  not read on Légifrance on 2026-10-11 (the search did not return its page); the dates above are
  taken from the law itself, which carries them.

The rule applies to new leases, renewals and tacit renewals; a running lease is not ended by the
calendar. The script states the class and the issue date and points here; whether a given lease
is caught is a legal question the reader checks.

## 5. What the script does not do

- No valuation: the median of comparable sales is a market observation, not an appraisal.
- No legal opinion on constructibility: the zone type is read, the regulation file is linked.
- No forecast and no personalised advice: the reader decides.
