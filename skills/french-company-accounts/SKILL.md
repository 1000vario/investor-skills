---
name: french-company-accounts
description: Reads the filed accounts of a French company from a SIREN or a name through two public APIs without any key (annuaire des entreprises on data.gouv.fr, Banque de France ratios computed from INPI filings), with a tested script that extracts key items, usual ratios and the change over three closing dates. Use when a user asks to "lire un bilan", "analyser les comptes", check "chiffre d'affaires", "resultat net", "endettement", "delai clients" or the health of a French SME, supplier, customer or competitor. Description only, never personalised advice.
license: MIT
metadata:
  author: Guillaume Couillard
  sources-read-on: "2026-10-11"
  api-key: "none"
---

# French company accounts

The model judges, the script computes. Every figure in the answer comes from
`scripts/comptes.py`, which reads the two public APIs; never quote a turnover, a result or
a ratio from memory, and never compute a ratio yourself.

## When to use

A user names a French company or gives a SIREN and asks what its accounts say: size,
margin, debt, cash, customer delay, trend. Typical French triggers: bilan, comptes annuels,
liasse, chiffre d'affaires, resultat net, EBE, endettement, tresorerie, delai de paiement,
sante financiere, fournisseur, concurrent.

Do not use for companies outside France, for listed-group analysis beyond the filed
accounts, or for any rating or recommendation. Groups whose accounts are confidential at
INPI appear with items missing; say so, do not fill them.

## Workflow

1. Identify the company. With a SIREN: `python scripts/comptes.py --siren 552032534`.
   With a name: `python scripts/comptes.py --name "Danone"`; the output lists the other
   candidates, show them and ask the user if the first one is not clearly the right one.
2. Prefer `--markdown` for the tables. Add `--social` to read the company's own accounts
   (C or S) instead of consolidated accounts (K) when both exist; say which type you show.
3. Answer with the identity line, the items table, the ratios table and the cross-check
   table from the script, then a five-point reading: size and trend of turnover, margin
   (net and EBE), debt and financial autonomy, cash and customer or supplier delays, data
   quality (anomalies, confidentiality, alternatives not shown). Every figure names its
   closing date and its type of accounts.
4. Copy the two source lines (name, licence, URL, fetch date) from the script output.
5. End with: "Figures as filed and published; description only, not personalised advice."

## What the script reads and computes

Items from the Banque de France dataset: turnover, gross margin, EBE, EBIT, net result.
Published ratios: EBE margin, current result before tax over turnover, CAF over turnover,
debt ratio, financial autonomy, working-capital weight, customer, supplier and stock days,
liquidity, interest cover, repayment capacity, asset age. Computed by the script: net margin,
EBE margin check, year-on-year change, change first to last closing date, cross-check of
turnover and net result against the annuaire. Field meanings and limits are in
`references/sources-api.md`.

## Limits

Only filed accounts appear; a company that filed late or under confidentiality shows gaps.
Ratios are reproduced as published, with the unit the dataset implies; the script flags
items published as zero or identical from one closing date to the next, it does not
correct them. The INPI RNE API with full balance sheets needs an account and is not used.
