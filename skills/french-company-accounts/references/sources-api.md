# Sources read by comptes.py

Both sources were read on 2026-10-11. Neither needs an API key. If a field or a URL below
no longer matches what the API returns, the API prevails and this file is to be corrected.

## 1. API Recherche d'entreprises (annuaire des entreprises)

- Base URL: `https://recherche-entreprises.api.gouv.fr/search`
- Operator: DINUM, data published on data.gouv.fr. Documentation:
  https://recherche-entreprises.api.gouv.fr/docs/ (read 2026-10-11).
- Licence: Licence Ouverte 2.0 for the data; the API code is under MIT.
- Rate limit: 7 requests per second per IP; no authentication.
- Query used: `?q=<siren or name>&minimal=true&include=finances,siege&page=1&per_page=1`
  (`per_page=5` for a name search, so the other candidates can be shown).
- Fields used from each result: `siren`, `nom_complet`, `nature_juridique` (legal form
  code), `activite_principale` (NAF code), `categorie_entreprise` (PME, ETI, GE),
  `date_creation`, `etat_administratif` (A active, C closed), `tranche_effectif_salarie`
  (headcount band code), `annee_tranche_effectif_salarie`, `date_mise_a_jour`,
  `finances` (a map year to `ca` and `resultat_net`), `siege.adresse`.
- Not requested on purpose: `dirigeants` and other personal data, not needed to read
  accounts.
- What `finances` holds: the turnover and net result the annuaire displays for the latest
  years it knows. For a group it carries the consolidated figures (checked on Danone,
  2025: 27 376 000 000 and 2 100 000 000, equal to the K row of source 2). For a fiscal
  year closing in March, the key is the year the fiscal year started (checked on Ubisoft,
  closing 2025-03-31 listed under 2024). The script uses it as a cross-check only.

## 2. Ratios financiers (BCE / INPI), data.economie.gouv.fr

- Dataset: `ratios_inpi_bce`, Opendatasoft explore API v2.1.
- URL used: `https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/ratios_inpi_bce/records?where=siren="<siren>"&order_by=date_cloture_exercice desc&limit=30`
- Publisher: ministere de l'Economie, ratios computed by the Banque de France from the
  annual accounts filed at INPI (registre national des entreprises). Licence Ouverte 2.0.
- Rate limit: public explore API, no key; keep calls sparse.
- One row per closing date and per type of accounts. Field `type_bilan`: `C` complete
  annual accounts, `S` simplified annual accounts, `K` consolidated accounts.
  `confidentiality` says whether the filing is public.
- Items in euros: `chiffre_d_affaires`, `marge_brute`, `ebe`, `ebit`, `resultat_net`.
- Ratios as published, units implied by their names: `marge_ebe`,
  `resultat_courant_avant_impots_sur_ca`, `caf_sur_ca`, `taux_d_endettement`,
  `autonomie_financiere`, `poids_bfr_exploitation_sur_ca` (all in percent of turnover or
  of the balance sheet), `poids_bfr_exploitation_sur_ca_jours`, `credit_clients_jours`,
  `credit_fournisseurs_jours`, `rotation_des_stocks_jours` (days), `ratio_de_liquidite`,
  `couverture_des_interets`, `capacite_de_remboursement`, `ratio_de_vetuste` (as published).
- The metadata endpoint of the dataset did not return JSON on 2026-10-11; the field list
  above comes from the records themselves.

## How the script chooses rows

- By default, for each closing date: consolidated (K), then complete (C), then simplified
  (S). With `--social`: C, then S, then K. A row whose turnover is empty or zero loses to
  a filled row of the same date, whatever its type.
- The chosen type is always shown next to the figure, and the types left out are listed
  under `alternatives_not_shown`.
- The script never corrects a published value. It flags: net result published as 0 with
  a turnover; gross margin or EBE equal to the turnover (item not filled); items identical
  to the previous closing date; a change of type of accounts between closing dates shown;
  a turnover jump by a factor above 50 (unit mismatch in the filing).

## Known data quirks seen on 2026-10-11

- Danone: the consolidated rows of 2023 and 2024 carry a net result of 0; the simplified
  rows of 2024 and 2025 are identical.
- Ubisoft: the consolidated row of 2024-03-31 is entirely at zero.
- Societe Bic and TotalEnergies SE: the latest closing date has company accounts only, the
  previous ones consolidated accounts; the changes across types are not comparable.
- Michelin (MFPM): the 2022 row seems filed in thousands of euros.
- Holdings (Legrand, Vinci, Hermes, TotalEnergies SE): company accounts show a tiny
  turnover and a net result made of dividends; the net margin computed on them is not
  meaningful and the answer must say so.

## Optional, not used: INPI registre national des entreprises API

The INPI API (`https://registre-national-entreprises.inpi.fr/api`, documentation "API
Comptes annuels v4" read on 2026-10-11) returns the full filed accounts (balance sheet,
income statement) but needs a free account and a login call (`/api/sso/login`) to obtain
a token, and caps a query at 10 000 results. The skill does not use it, so that it works
for a stranger with no key; the two open sources above give the key items and the ratios. A user who wants the full balance sheet can download the filing from
https://annuaire-entreprises.data.gouv.fr/ (page of the company, tab "Documents").
