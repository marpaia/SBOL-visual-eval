# The learned evaluator

The evaluator turns one manuscript PDF into a validation score in the exact
format of the historical study: the four paper-level counts (`figures_total`,
`figures_sbol_visual_compatible`, `figures_sbol_visual_compliant`,
`figures_best_practices`) plus the derived paper flags of
`data/processed/papers.csv`.

```bash
uv run sbol-visual-eval score paper.pdf                      # Claude API judge
uv run sbol-visual-eval score paper.pdf --judge claude-cli   # authenticated Claude Code CLI
```

## Architecture: a cascade mirroring the historical review

1. **Figure census** (`figures/`) — the scoped main-manuscript pages are
   scanned for `Figure N` caption anchors using column-aware line grouping;
   the census count is the prediction of `figures_total`. Provenance
   manifests with `artifact_scope: main_manuscript_with_appended_supporting_information`
   restrict the scan to the declared page range so Supporting Information
   figures never leak into the count.
2. **Per-figure judgment** (`judge/`) — each figure's page is rendered to PNG
   and judged in one rubric-conditioned prompt: is the figure appropriate for
   SBOL Visual at all, and if so, a verdict for every rule of
   `historical_2025_rubric_v1`, reviewer exceptions included. The evaluator
   targets the historical checklist, not the strict SBOL Visual 3.0
   specification.
3. **Aggregation** (`evaluator/`) — verdicts collapse under the historical
   cascade: compliant means compatible with no failed MUST rule; best-practice
   means compliant with no failed SHOULD rule. Compliant and best-practice
   outcomes are always derived from per-rule findings, never taken from a
   model's own summary judgment.

Judge backends: `anthropic` (Claude API through the Anthropic SDK; credentials
from the environment) and `claude-cli` (headless `claude -p` against an
authenticated Claude Code installation).

## Supervision strategy

The released ground truth is paper-level count supervision (see the
[ground-truth limitation](corpus.md#ground-truth-limitation)). The evaluator
treats it three ways:

- **Direct count agreement.** Every predicted score is comparable
  paper-by-paper with the historical counts; `evaluation/metrics.py` reports
  exact, within-one, and MAE agreement per count, precision/recall on the
  derived flags, and the yearly totals whose trend is the study's headline
  result.
- **Saturated counts as exact figure labels.** `evaluation/groundtruth.py`
  derives the figure-level labels the counts entail: a paper with zero
  compatible figures labels every figure a certain compatible-negative, and
  matching compatible/compliant counts pin the compliant label of every
  compatible figure. Across the corpus this yields roughly 5,900 certain
  negative and 600 certain positive compatible-stage figure labels, and a
  fully determined compliant stage for about 1,500 of the 1,659 papers with
  compatible figures — calibration fuel that requires no figure identities.
- **Never invented figure identities.** `MIXED` stages are handled with
  count-level supervision only; figure-level pseudo-labels are never recorded
  as historical ground truth.

## Measured extraction quality

`uv run sbol-visual-eval census` reconciles the census against every
annotated paper with a local preferred PDF (`data/reports/figure_census.*`).
Current agreement with `figures_total`: **98.4% exact on 1,257
Version-of-Record PDFs** (MAE 0.02). Non-VOR editions agree only 57% —
preprints and accepted manuscripts genuinely differ in figure count — so
evaluation sweeps default to Version-of-Record PDFs and treat other editions
as version-noise, with `source_version` retained per artifact.

## Scoring the evaluator

```bash
uv run sbol-visual-eval evaluate --sample 25 --judge claude-cli
uv run sbol-visual-eval evaluate --years 2023            # temporal holdout
```

Sweeps pair each evaluated paper with its historical counts and write
`data/reports/evaluator_agreement.csv` (per-paper), `.json` (agreement
summary), and `evaluator_verdicts.jsonl` (complete per-figure, per-rule
verdicts for audit). Papers whose historical counts are internally
inconsistent (`requires_adjudication`, the 2020 invariant violation) are
excluded from scoring, matching `GroundTruthPaper.scoreable`.

Because the ground truth is one reviewer panel, target agreement bands rather
than exactness everywhere: within-one count agreement and reproduction of the
yearly adoption trend are the primary acceptance criteria; exact all-counts
agreement is the stretch metric. Split by year when tuning — train/calibrate
on early years and hold out recent years — since deployment means scoring
future papers under glyph and style drift.

## Measured judge agreement

Ten-paper seeded sweeps with the `claude-cli` judge
(`data/reports/evaluator_agreement_pilot*.{csv,json}` and
`evaluator_agreement_holdout.*`):

- **Calibration sample (seed 7):** the uncalibrated judge agreed exactly on
  all four counts for 50% of papers, with compliance systematically stricter
  than the historical panel — rule 5.2.6 read literally fails generic labeled
  shapes that count-saturated papers prove the panel accepted. Encoding the
  recovered interpretations (`judge/prompt.py`,
  `HISTORICAL_INTERPRETATIONS`) raised same-sample agreement to 70%.
- **Held-out sample (seed 21, after calibration):** `figures_total` 10/10
  exact; compatible 6/10 exact (8/10 within one); compliant 5/10 exact (7/10
  within one); all-counts exact 50%. The residual disagreement concentrates
  on the compatibility boundary — the judge counts construct schematics
  inside mechanism/workflow figures that the panel did not count — and on
  best-practice strictness.

## Figure-level benchmarks from saturated counts

Two benchmarks convert count supervision into exact per-figure labels, so the
stages can be tuned without any figure-level annotation:

```bash
uv run sbol-visual-eval compatibility --sample 40   # compatibility boundary
uv run sbol-visual-eval cascade --sample 20         # compliance + best practice
```

`compatibility` (`evaluation/compatibility.py`) draws on papers whose
compatible stage is saturated — zero compatible figures, or every figure
compatible. After excluding papers whose census disagrees with
`figures_total` (their figure set is not the set the reviewers scored),
**599 Version-of-Record papers yield 2,948 exactly labeled figures: 2,674
certain negatives and 274 certain positives.** It judges with a
compatibility-only prompt, so no rule evaluation is paid for, and reports
accuracy, recall, false-positive rate, and precision.

`cascade` (`evaluation/cascade_bench.py`) uses the stronger shapes: papers
where all four counts are equal label every figure positive through the whole
cascade (58 papers, 191 figures), and papers compliant everywhere with zero
best-practice figures label every figure a best-practice negative. It reports
per-stage accuracy plus the rules that block figures the panel judged
positive — the direct evidence for the next round of interpretation notes.

## Calibration levers, in order of expected value

1. **The compatibility definition** in `judge/prompt.py` — the dominant source
   of compatible-count disagreement; tune against the saturated-label subsets
   before touching anything else.
2. **Per-rule strictness** — rules whose failure verdicts drive
   compliant-count disagreement can be individually audited from
   `evaluator_verdicts.jsonl` against papers with fully determined compliant
   stages.
3. **Judge model and prompt variants** — sweep with `--model` and compare
   agreement reports; the report stem flag keeps runs side by side.
4. **Disagreement adjudication** — papers where predicted and historical
   counts disagree are exactly where expert review is worth buying; adjudicated
   figures become a new, separately layered figure-level ground truth.
