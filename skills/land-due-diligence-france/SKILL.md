---
name: land-due-diligence-france
description: One-page due diligence sheet for a land parcel or address in France, computed from foncier-mcp data (DVF comparable sales in EUR/m2, Geoportail de l'urbanisme zoning, ADEME energy diagnostics, Georisques risks). Use when the user gives a French parcel id or address and asks whether a plot or house is constructible, exposed to flood, clay or industrial risks, restricted for letting by its DPE class, or priced in line with nearby sales. The script computes every figure and every flag; the model only reads and cites.
license: MIT
---

# Land due diligence, France

Facts come from the foncier-mcp server (open data from IGN, DGFiP, ADEME, BRGM). Figures and
flags come from `scripts/due_diligence.py`. The model writes the sheet from the script output and
never adds a number, a zone, a class or a risk of its own.

This skill computes and describes. It gives no personalised advice: the reader decides.

## When to use

- A French cadastral parcel id (for example `35238000AB0042`) or a postal address.
- Questions such as: is it constructible, is it in a flood zone, can it still be let with this
  DPE, is the asking price in line with nearby sales, what should I check before an offer.

Do not use for a valuation, a legal opinion, or a property outside France.

## Requirements

- The foncier-mcp MCP server, https://github.com/1000vario/foncier-mcp, with its tools
  `summarize_parcel`, `get_parcel_sales`, `get_zoning`, `get_energy_diagnostics`, `get_risks`.
  As of 2026-10-11 the server is being published; without it, the skill runs on a saved
  envelope only (see `tests/fixtures/` for the shape).
- Python 3.11 or later, standard library only.

## Steps

1. Fetch the data: call `summarize_parcel` with the parcel id or address (radius 500 m by
   default), or the four tools above. Save the JSON envelope(s) to a file, unchanged. For four
   envelopes, wrap them as `{"envelopes": [...]}`.
2. Run the script, with the asking price and surface when the user gave them:

   ```bash
   python scripts/due_diligence.py --input envelope.json --asking-price 310000 --surface 100 --markdown
   ```

   Add `--property-type Maison` or `Appartement` to keep one dwelling type among comparables.
   Without `--markdown` the script prints the full JSON (flags, comparables, zoning, energy,
   risks, sources).
3. Write the sheet from the output, in this order: red flags, orange flags, info flags, then
   comparable sales (count, median EUR/m2, range, period, asking gap), zoning, energy
   diagnostics, risks. Copy the `[source: ..., retrieved ...]` stamp of every fact.
4. When a section is unavailable, say so with the reason the script gives (for example DVF does
   not cover Alsace-Moselle). Never fill the gap with a figure from memory.
5. End with: "Facts as published by the sources named above, computation only, not personalised
   advice: the reader decides and checks the originals."

## Reading rules

- The median EUR/m2 of comparables is a market observation on the mutations found, not an
  appraisal of this property. Say how many sales it rests on.
- A zone type is read at the parcel centroid; link the regulation file the script gives.
- A DPE class F or G, or E, raises a flag because of the decent dwelling calendar in
  `references/sources-foncier.md`, section 4. State the class and its issue date, not a legal
  conclusion on a given lease.
- Exit code 3 with `{"error": "invalid_input"}` means the file is not a foncier-mcp envelope:
  report it, do not improvise.

## References

- `references/sources-foncier.md`: data sources and licences, envelope contract, every flag with
  its threshold, the DPE letting calendar with its legal texts and reading dates.
- `tests/fixtures/L01.json` to `L15.json`: synthetic envelopes covering the flags.
