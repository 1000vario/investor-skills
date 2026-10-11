# Evals: the skill against the model alone

Every skill ships with fifteen cases whose truth was computed by hand, dated and sourced. The
runner plays each case through several arms and grades every arm with the same grader. The gap
between the arms is the figure that justifies the skill.

## Arms

| Arm | What answers | Needs | Who runs it |
| --- | --- | --- | --- |
| `dry-run` | the skill's script alone, on the case inputs | Python only | CI, on every push |
| `with_skill` | the model, with `SKILL.md` in its system prompt and one tool, `run_script`, that executes the skill's script | an Anthropic API key | you, by hand |
| `without_skill` | the model, with the same question and the same raw data (inputs JSON, foncier-mcp envelope or recorded API responses), no skill, no tool | an Anthropic API key | you, by hand |
| `bare` | the model, with the question only | an Anthropic API key | you, by hand |

The live arms call the model through the Anthropic SDK (`pip install anthropic`), with adaptive
thinking and streaming, and cost money. They never run in CI. The model arms receive the answer
format they must end with (one fenced JSON block); the `with_skill` arm also gets a `read_file`
tool restricted to the skill folder. The harness refuses script arguments that point outside the
repository and, for `french-company-accounts`, forces the recorded responses so that no arm
touches the network.

## Cases

`cases/<skill>/<id>.json`: the question in French as a user would ask it, the inputs, the
expected figures with their tolerance, the strings or patterns the answer must mention, the hand
computation, the truth date and the sources read. `cases/rental-yield-france/_rule_values.json`
lists every fiscal value a correct answer may quote; a rental answer that quotes another value
counts as an invention.

- `rental-yield-france`: C01 to C15, four regimes, loan or not, two refusal cases (rent above
  the micro-foncier ceiling, receipts above the micro-BIC ceiling) and one warning case
  (receipts above 23 000 EUR). All properties are invented.
- `land-due-diligence-france`: L01 to L15 on the synthetic envelopes of
  `skills/land-due-diligence-france/tests/fixtures/`, covering every flag, the DVF coverage
  hole, a commune under the RNU and a failed risks tool. No real parcel.
- `french-company-accounts`: A01 to A15 on fifteen large French companies whose filings are
  public, with the API responses recorded on 2026-10-11 in `cases/french-company-accounts/recorded/`.

## Grading

Per case and per arm: numeric checks within the case tolerance (figures, counts, flag codes,
closing dates), the refusal or warning the case expects, the absence of invention (a stated
figure outside tolerance, a flag code outside the vocabulary, a fiscal value outside the list),
the presence of an official source and of a date in the answer, the `must_mention` patterns for
the model arms, wall time, tokens and cost from the price table in `run.py` (prices read on
2026-10-11). A case is fully correct when every check passes with no invention and, for the model
arms, with sources and dates.

## Run

```bash
python evals/run.py --dry-run                                     # all skills, script arm
python evals/run.py --skill rental-yield-france --dry-run         # one skill
export ANTHROPIC_API_KEY=...                                      # live arms, your account
python evals/run.py --arm with_skill --arm without_skill --arm bare --max-cases 3
python evals/run.py --report                                      # table of results/*.json
```

Results land in `results/<date>-<arm>-<skill>.json` (summary and per-case grades, kept in the
repository) and the live transcripts in `results/raw/` (ignored by git).

## Results

| date | skill | arm | model | cases fully correct | numeric accuracy | inventions | sources and dates | mean s | cost USD |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-11 | french-company-accounts | dry-run | script only | 15/15 | 100.0 % (86/86) | 0 | 15/15 | 0.08 | 0.0 |
| 2026-10-11 | land-due-diligence-france | dry-run | script only | 15/15 | 100.0 % (82/82) | 0 | 15/15 | 0.07 | 0.0 |
| 2026-10-11 | rental-yield-france | dry-run | script only | 15/15 | 100.0 % (87/87) | 0 | 15/15 | 0.05 | 0.0 |

The live arms (`with_skill`, `without_skill`, `bare`) had not been run on 2026-10-11: they need an
API key and a spend approval that the build did not have. The runner is ready; the first run will
add its dated lines here and in the root README.
