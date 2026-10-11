# investor-skills

[![ci](https://github.com/1000vario/investor-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/1000vario/investor-skills/actions/workflows/ci.yml)

Three [Agent Skills](https://agentskills.io) for French real-estate and SME due diligence,
installable in Claude Code, Claude Desktop and Codex. One principle: **the model judges, the
script computes.** Every figure in an answer comes from a tested Python script; the model reads,
cites and describes, and never adds up anything itself.

| Skill | What it computes | Data | Key needed |
| --- | --- | --- | --- |
| [`rental-yield-france`](skills/rental-yield-france/SKILL.md) | gross, net and after-tax rental yield, monthly cash flow with a loan, ten-year table under micro-foncier, reel foncier, LMNP micro-BIC or LMNP reel | the user's figures; fiscal rules dated and sourced in `references/` | none |
| [`land-due-diligence-france`](skills/land-due-diligence-france/SKILL.md) | one-page sheet for a parcel or address: red, orange and info flags, comparable sales in EUR/m2, zoning, DPE, natural and industrial risks | the [foncier-mcp](https://github.com/1000vario/foncier-mcp) server (DVF, Geoportail de l'urbanisme, ADEME, Georisques) | none, but the MCP server |
| [`french-company-accounts`](skills/french-company-accounts/SKILL.md) | key items, published ratios and the change over three closing dates of a French company's filed accounts | two public APIs read live: annuaire des entreprises, Banque de France ratios from INPI filings | none |

Each skill is a folder with a `SKILL.md`, a script, its tests and a `references/` file that
gives every rule and every source with the date it was read. The scripts need Python 3.11 or
later and the standard library only.

The skills compute and describe. They give no personalised advice: the reader decides. No
property, loan or company of the author appears in the examples or the evals; the company cases
use large listed groups whose filings are public.

## Install in under ten minutes

Clone the repository, or download it as a ZIP, then copy the skill folder(s) you want.

```bash
git clone https://github.com/1000vario/investor-skills.git
```

**Claude Code.** Copy a skill folder into `~/.claude/skills/` for every project, or into
`.claude/skills/` of one project. Claude Code lists the skill at the next start; ask it, for
example, "rendement net d'un appartement a 180 000 EUR loue 750 EUR par mois en LMNP reel" and
check that it runs `scripts/rendement.py` instead of computing by itself.

```bash
cp -r investor-skills/skills/rental-yield-france ~/.claude/skills/
cp -r investor-skills/skills/french-company-accounts ~/.claude/skills/
cp -r investor-skills/skills/land-due-diligence-france ~/.claude/skills/
```

**Claude Desktop.** Zip one skill folder (the folder itself, with `SKILL.md` at its root), then
open Settings, Capabilities, Code execution, Customize, Skills, and upload the ZIP. The scripts
run in the code execution sandbox; `french-company-accounts` needs network access to the two
public APIs, so use its `--recorded` mode when the sandbox is offline.

**Codex.** Copy a skill folder into `$HOME/.agents/skills/` for every project, or into
`.agents/skills/` of one repository. Codex reads `SKILL.md` and runs the script from there.

**`land-due-diligence-france`** needs the foncier-mcp server configured in the same client; its
`SKILL.md` says which tools it calls. As of 2026-10-11 the server is being published; until then
the skill runs on a saved envelope (see `tests/fixtures/`).

## Run a script by hand

```bash
python skills/rental-yield-france/scripts/rendement.py --example > case.json
python skills/rental-yield-france/scripts/rendement.py --input case.json --markdown
python skills/french-company-accounts/scripts/comptes.py --siren 552032534 --markdown
python skills/land-due-diligence-france/scripts/due_diligence.py --input envelope.json --asking-price 310000 --surface 100 --markdown
```

Every output names the rules applied, their sources and the date they were read, and ends with
the same disclaimer as the skill.

## Tests, checks and evals

```bash
pip install ruff pytest
ruff check . && ruff format --check .
python tools/check_skills.py       # frontmatter, size limits, scripts, tests, no em dash or en dash
python -m pytest -q                # 54 tests
python evals/run.py --dry-run      # 45 cases, script arm, no model, no network
```

CI runs the same five commands on Python 3.11 and 3.13 on every push.

### Eval results

Fifteen cases per skill, each with a truth computed by hand, dated and sourced
([details](evals/README.md)). The runner plays each case with the skill (the model plus the
script), without the skill (the model with the same raw data) and bare (the question only), and
grades every arm the same way: figures within tolerance, no invented figure or rule, sources and
dates present, time and cost.

| date | skill | arm | model | cases fully correct | numeric accuracy | inventions | sources and dates | mean s | cost USD |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-11 | french-company-accounts | dry-run | script only | 15/15 | 100.0 % (86/86) | 0 | 15/15 | 0.08 | 0.0 |
| 2026-10-11 | land-due-diligence-france | dry-run | script only | 15/15 | 100.0 % (82/82) | 0 | 15/15 | 0.07 | 0.0 |
| 2026-10-11 | rental-yield-france | dry-run | script only | 15/15 | 100.0 % (87/87) | 0 | 15/15 | 0.05 | 0.0 |

The model arms had not been run on 2026-10-11: they need an Anthropic API key and cost money,
and the build had neither the key nor the approval to spend. Run them yourself with
`ANTHROPIC_API_KEY` set and `python evals/run.py --arm with_skill --arm without_skill --arm bare`;
the dated lines of `python evals/run.py --report` belong in this table.

## Sources and limits

- `rental-yield-france`: CGI articles 31, 32, 39 C, 50-0, 155 and 156, decree 2023-297, BOFiP
  pages and service-public.fr sheets, read on 2026-10-11 and listed in
  `skills/rental-yield-france/references/regles-fiscales.md`. Not covered: SCI subject to
  corporate tax, bare ownership and usufruct, tax-reduction schemes, resale gains.
- `land-due-diligence-france`: sources, flag thresholds and the DPE letting calendar (loi 89-462
  art. 6, CCH L. 173-1-1) in `skills/land-due-diligence-france/references/sources-foncier.md`.
  DVF does not cover Alsace-Moselle nor Mayotte; the script says so and invents no price.
- `french-company-accounts`: the INPI data as republished by the Banque de France carries unit
  and filling quirks; the script flags them as anomalies and corrects nothing. Companies with
  confidential filings appear with items missing.

Fiscal rules change every year. Each reference file carries the date it was read; check it
before relying on a figure.

## Service

The three skills are free, public and stay so. Issues and pull requests are welcome, in French
or in English.

## Licence

MIT, see [LICENSE](LICENSE).
