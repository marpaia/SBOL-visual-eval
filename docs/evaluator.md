# The learned evaluator

The evaluator turns one manuscript PDF into a validation score in the exact
format of the historical study: the four paper-level counts (`figures_total`,
`figures_sbol_visual_compatible`, `figures_sbol_visual_compliant`,
`figures_best_practices`) plus the derived paper flags of
`data/processed/papers.csv`.

```bash
uv run sbol-visual-eval score paper.pdf                      # Claude API judge
uv run sbol-visual-eval score paper.pdf --judge claude-cli   # authenticated Claude Code CLI
uv run sbol-visual-eval score paper.pdf --judge codex-cli    # authenticated Codex/ChatGPT CLI
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
from the environment), `claude-cli` (headless `claude -p` against an
authenticated Claude Code installation), and `codex-cli` (sandboxed,
noninteractive `codex exec` against the local OpenAI/ChatGPT authentication).
The Codex backend attaches page images directly, constrains the final response
with JSON Schema, runs ephemerally with a read-only sandbox, and defaults to
`gpt-5.6-sol`; `--model` selects another locally available model.

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
uv run sbol-visual-eval evaluate --sample 25 --seed 2718 --resume
uv run sbol-visual-eval evaluate --sample 25 --exclude-report data/reports/prior.csv
```

Sweeps pair each evaluated paper with its historical counts and write
`data/reports/evaluator_agreement.csv` (per-paper), `.json` (agreement
summary), and `evaluator_verdicts.jsonl` (complete per-figure, per-rule
verdicts for audit). Papers whose historical counts are internally
inconsistent (`requires_adjudication`, the 2020 invariant violation) are
excluded from scoring, matching `GroundTruthPaper.scoreable`.

Repeatable `--exclude-report` options remove every DOI in prior benchmark CSVs
before seeded sampling. The JSON report records those exclusions with the
sample seed, year filter, and edition policy so a validation cohort can be
shown to be paper-disjoint from every calibration or provider-selection run.

Every judge-backed sweep records its prompt profile, judging mode, backend, and
model in the JSON summary. Cascade summaries also record their sampled paper
count, seed, and VOR-only policy rather than relying on report filenames for
cohort provenance. End-to-end, compatibility, and cascade sweeps write
append-only ignored checkpoints while they run; `--resume` reuses only
successful rows whose paper identity, entailed labels, prompt profile, and
judging mode still match. An error-free report removes its checkpoint; an
error-bearing report retains the checkpoint and can also recover successful
rows from its CSV.

Because the ground truth is one reviewer panel, target agreement bands rather
than exactness everywhere: within-one count agreement and reproduction of the
yearly adoption trend are the primary acceptance criteria; exact all-counts
agreement is the stretch metric. Split by year when tuning — train/calibrate
on early years and hold out recent years — since deployment means scoring
future papers under glyph and style drift.

## Measured judge agreement

Every calibration follows the same loop: run a benchmark on figures whose
labels the counts entail, read the judge's own rationales and failed rules on
the disagreements, encode the panel's operational reading into
`judge/prompt.py`, and validate on data that never influenced the prompt.
Report files for every round live under `data/reports/`.

**Compatibility stage** (compatibility benchmark, ~200 labeled figures per
round):

| | Accuracy | FP rate | Recall | Net count bias /100 |
|---|---|---|---|---|
| Uncalibrated | 88.5% | 12.5% | 93.5% | +10.74 |
| Calibrated, fresh seed | **97.0%** | **0.6%** | 82.8% | **−1.06** |

Two edits got there: encoding the panel's excluded categories (annotated
sequence/motif maps, cloning and vector cartography, constructs decorating
another structure's nodes), then scoping those exclusions to what a depiction
is rather than the figure's overall subject, which recovered recall from an
over-corrected 50%. The net-bias column is the decision metric: false
positives and negatives cancel in a paper's count, so the benchmark projects
both error rates onto the corpus mix (~10:1 negative:positive).

The full-pool prose baseline (`compatibility_full_pool_baseline.*`) attempts all
2,948 count-entailed VOR figures with the `claude-cli` Opus judge. Opus returns
verdicts for 2,942 figures; its safeguards refuse six pages from one
botulinum-neurotoxin paper. Metrics exclude those six provider errors:

| Publication era | Judged / attempted | Accuracy | FP rate | Recall | Net count bias /100 | Paper-count exact |
|---|---:|---:|---:|---:|---:|---:|
| 2012–2013 | 318 / 318 | 90.3% | 5.3% | 55.6% | +0.69 | 79.0% |
| 2014–2016 | 993 / 993 | 92.4% | 5.6% | 67.1% | +1.99 | 80.2% |
| 2017–2023 | 1,631 / 1,637 | 94.4% | 3.5% | 75.3% | +0.92 | 85.1% |
| **All eras** | **2,942 / 2,948** | **93.2%** | **4.4%** | **70.4%** | **+1.26** | **82.8%** |

Across all 592 fully judged saturated papers, compatible-count agreement is
82.8% exact, 92.4% within one, and 0.336 MAE. The disagreements establish a
real era effect, but not a one-direction leniency rule. Early and middle
positives include conventional cloning/vector cartography, concrete construct
insets beside data, and some protein/domain designs, while negatives include
abstract circuit topology and strand/domain mechanisms that appear equally
translatable under a modern definition. Recent disagreements are smaller and
include cross-paper inconsistencies around vector cartography and protein-only
designs. The generated adjudication layer contains all 199 scored
evaluator-versus-history disagreements and leaves the historical layer intact.

Era conditioning is measured as an opt-in profile. Seed 101 is the burned
calibration cohort; seed 20260905 selects 30 papers (126 figures) from the
paper-disjoint holdout and is opened only after the era-v3 wording is frozen:

| Profile and cohort | Accuracy | FP rate | Recall | Net count bias /100 | Paper-count exact |
|---|---:|---:|---:|---:|---:|
| Paired prose, calibration | 81.2% | 5.0% | 60.0% | +0.82 | 62.5% |
| Era v2, calibration | 83.0% | 1.0% | 58.5% | −2.95 | 72.5% |
| Era v3, calibration | 95.8% | 4.0% | 95.4% | +3.20 | 85.0% |
| Paired prose, fresh holdout | 82.5% | 9.5% | 66.7% | +5.54 | 66.7% |
| Era v3, fresh holdout | 92.1% | 10.7% | 97.6% | +9.50 | 80.0% |

Era v3 fixes 15 and regresses three figure calls on the fresh cohort; exact
paper counts rise by six and regress by two. Its recall and exact-count gains
validate, but its false-positive count bias does not. The profile therefore
remains explicitly selectable with `--era-conditioned`; the default evaluator
continues to use the lower-bias prose profile.

Image-backed few-shot references also remain opt-in. On the burned 40-paper /
165-figure calibration cohort, the first reference set reaches 89.7% accuracy, 78.5%
recall, 3.0% FP rate, +0.72 projected bias per 100, and 72.5% exact paper
counts. The boundary-v2 set replaces over-broad recent examples with an early
abstract-topology negative, a recent concrete-plasmid positive, and a native
target-locus negative. It reaches 93.9% accuracy, 87.7% recall, 2.0% FP rate,
+0.67 projected bias, and 80.0% exact paper counts, fixing nine v1 calls and
regressing two. Against era v3 without images, however, v2 fixes three calls
and regresses six while exact paper counts fall from 85.0% to 80.0%. The
examples improve their predecessor and reduce bias, but do not beat the prose
era profile, so they are not adopted by default.

Whole-paper and selective-voting modes are rejected by their paired calibration
benchmarks and remain opt-in. Whole-paper era-v3 judging on 40 papers / 165
figures lowers accuracy from 95.8% to 92.1%, exact paper counts from 85.0% to
77.5%, and worsens projected bias from +3.20 to +4.44 per 100. Selective k=3
voting on 20 papers / 78 figures leaves the 98.7% figure accuracy and 95.0%
paper-count exact rate unchanged: five initially borderline figures consume ten
extra verdicts (+12.8% calls) without changing a call.
The Codex voting profile is also rejected on the paired 20-paper / 78-figure
calibration cohort. One initially borderline figure consumes two extra
verdicts, while the profile fixes no baseline calls, regresses two, lowers
accuracy from 97.4% to 94.9%, and lowers exact paper counts from 90.0% to 80.0%.

**Codex/ChatGPT compatibility stage.** The authenticated `codex-cli` backend
is evaluated with its default `gpt-5.6-sol` model against the same image and
prompt contracts. Its paired calibration and paper-disjoint holdout reports are
provider measurements, not replacements for the lower-bias default:

| Profile and cohort | Accuracy | FP rate | Recall | Net count bias /100 | Paper-count exact |
|---|---:|---:|---:|---:|---:|
| Claude era v3, paired calibration | 98.7% | 2.2% | 100.0% | +1.97 | 95.0% |
| Codex prose, calibration | 80.8% | 10.9% | 68.8% | +6.95 | 70.0% |
| Codex era v3, calibration | 97.4% | 4.4% | 100.0% | +3.94 | 90.0% |
| Codex era v3 + boundary-v2 images, calibration | 92.3% | 6.5% | 90.6% | +5.04 | 80.0% |
| Codex era v3, disjoint holdout | 90.8% | 7.4% | 71.4% | +4.04 | 80.0% |

The 30-paper / 163-figure Codex holdout contains only 14 positives, all in
2017–2023, after excluding the earlier holdout report. It therefore supports a
provider fallback but not a claim that era conditioning generalizes across all
three eras. Whole-paper Codex calls return exactly the same 163 verdicts as
per-figure calls on this cohort and take longer, so per-figure judging remains
the operational mode. The image exemplars also regress the paired Codex
calibration result and remain opt-in.

A model sweep on the burned cohorts rejects `gpt-6-astra` for this task. It
reaches 94.9% compatibility accuracy, 93.8% recall, and 85.0% exact paper
counts on the paired calibration cohort, below `gpt-5.6-sol` at 97.4%, 100.0%,
and 90.0%. With the full-rubric threshold, both models reach 93.3%
compatibility and compliance accuracy on the 15-figure cascade cohort, while
`gpt-6-astra` drops best-practice accuracy from 93.3% to 60.0%.

**Compliance and best-practice stages** (cascade benchmark):

- Compliance needed only the original interpretation notes: on held-out
  cascade papers, compliant recall given a correct compatible verdict is
  46/47 (98%).
- Best practice had two distinct failure modes. Per-rule misreadings (5.2.6
  ignoring its own "flexible on version" note, conditional 5.1.1 applied
  unconditionally, 5.1.3 demanding plasmid glyphs for linear constructs) were
  fixed with interpretation notes. The remainder was a diffuse
  one-nitpick-per-figure pattern — most misses failed exactly one of the 18
  SHOULD rules, a different rule each time — fixed by calibrating the global
  failure threshold to the panel's conspicuous-violation bar. Together:
  best-practice accuracy 46% → 62% on the calibration sample, **61% on
  held-out papers** (baseline 46%), with errors now balanced in both
  directions.

Codex full-rubric calls need a provider-specific restatement of the same global
conspicuous-violation threshold. On the burned 15-figure cascade calibration
cohort, this leaves compatibility and compliance accuracy at 93.3% while
raising best-practice accuracy from 73.3% to 93.3%; six diffuse findings across
five blocking SHOULD rules disappear. On a 34-figure cross-provider transfer
cohort, before that restatement, Codex reaches 94.1% compatible, 79.4%
compliant, and 73.5% best-practice accuracy, versus 82.4%, 73.5%, and 82.4%
for the earlier Claude verdicts. All 39 cascade papers have previously informed
calibration, so this transfer comparison is diagnostic rather than fresh
validation. Whole-paper Codex judging on the 15-figure calibration cohort
changes the per-figure 93.3% / 93.3% / 93.3% result to 93.3% / 73.3% / 40.0%:
the calls become more internally uniform but less historically accurate.
Two full-cascade image references selected from different exact-label papers
also fail to transfer to this cohort: compatibility and compliance remain at
93.3%, while best-practice accuracy falls to 66.7%. They remain available only
through the experimental `--cascade-few-shot` switch. A staged pipeline that
runs the dedicated compatibility prompt before a compatibility-preclassified
rubric call produces the same 93.3% / 93.3% / 66.7% result. It neither recovers
the remaining compatibility miss nor preserves best-practice accuracy, so
`--staged` also remains experimental.

**Claude end-to-end paper agreement** (20 papers, fresh seed, full pipeline,
`evaluator_agreement_final.*`):

| Count | Exact | Within one | MAE | Totals (predicted / historical) |
|---|---|---|---|---|
| `figures_total` | 19/20 | 20/20 | 0.05 | 86 / 87 |
| `figures_sbol_visual_compatible` | 15/20 | 20/20 | 0.25 | 23 / 28 |
| `figures_sbol_visual_compliant` | 15/20 | 18/20 | 0.50 | 21 / 17 |
| `figures_best_practices` | 15/20 | 19/20 | 0.30 | 12 / 12 |

All four counts exact simultaneously on 10/20 papers. The remaining
disagreements are scattered ±1 errors in both directions rather than a
systematic bias — the aggregate best-practice total matches exactly and the
other totals sit within a few figures — which is the profile of a system near
the corpus's own consistency floor. Under the within-one acceptance band the
stages sit at 100% / 100% / 90% / 95%.

**Codex end-to-end paper agreement** uses 25 VOR papers excluded from every
earlier evaluator, full-pool compatibility, cascade, and unreinforced Codex
report (`evaluator_agreement_codex_threshold_v1_validation_seed20260911.*`):

| Count | Exact | Within one | MAE | Totals (predicted / historical) |
|---|---|---|---|---|
| `figures_total` | 24/25 | 24/25 | 0.08 | 143 / 141 |
| `figures_sbol_visual_compatible` | 18/25 | 24/25 | 0.32 | 37 / 43 |
| `figures_sbol_visual_compliant` | 15/25 | 24/25 | 0.44 | 33 / 34 |
| `figures_best_practices` | 9/25 | 21/25 | 0.80 | 26 / 18 |

All four counts are exact on 7/25 papers (28%). The full-rubric threshold
transfers beyond its cascade calibration, but it over-awards eight
best-practice figures while missing six compatible figures across 141
historical figures. That directional error fails the near-zero-bias acceptance
criterion, so `codex-cli` is a functional local substitute and experiment
backend, not the measured default for headline scoring.

**Known agreement ceiling.** The panel scored near-identical content
differently across papers (an annotated vector map counted in one paper,
excluded in another). Reviewers were consistent within papers — which is why
saturation exists — but not perfectly across them, so exact agreement has a
ceiling short of 100% and residual best-practice fuzziness is expected.

## Figure-level benchmarks from saturated counts

Two benchmarks convert count supervision into exact per-figure labels, so the
stages can be tuned without any figure-level annotation:

```bash
uv run sbol-visual-eval compatibility --workers 6 --resume
uv run sbol-visual-eval compatibility --partition calibration --sample 40
uv run sbol-visual-eval compatibility --partition holdout
uv run sbol-visual-eval cascade --sample 20 --resume
uv run sbol-visual-eval cascade --sample 20 --whole-paper
```

`compatibility` (`evaluation/compatibility.py`) draws on papers whose
compatible stage is saturated — zero compatible figures, or every figure
compatible. After excluding papers whose census disagrees with
`figures_total` (their figure set is not the set the reviewers scored),
**599 Version-of-Record papers yield 2,948 exactly labeled figures: 2,674
certain negatives and 274 certain positives.** It judges with a
compatibility-only prompt, so no rule evaluation is paid for, and reports
accuracy, recall, false-positive rate, precision, count bias, and exact/within-one
agreement after reaggregating predictions by saturated paper. The default
paper-level split is fixed by `--split-seed 20260904`, stratified within
2012–2013, 2014–2016, and 2017–2023 by entailed label, and never divides a
paper between calibration and holdout. `--sample` runs after partitioning and
balances papers across the available era × label strata. Repeatable
`--exclude-report` arguments remove every paper present in earlier
compatibility CSVs before sampling; the JSON summary records the partition,
split seed, sample seed and size, holdout fraction, and exclusion reports.

`cascade` (`evaluation/cascade_bench.py`) uses the stronger shapes: papers
where all four counts are equal label every figure positive through the whole
cascade (58 papers, 191 figures), and papers compliant everywhere with zero
best-practice figures label every figure a best-practice negative. It reports
per-stage accuracy plus the rules that block figures the panel judged
positive — the direct evidence for the next round of interpretation notes.

## Judge experiment modes

The default evaluator retains the prose-only, per-figure profile. Explicit
switches select independently measurable alternatives:

- `--era-conditioned` supplies the paper's publication year and applies the
  measured historical boundary for its review era.
- `--few-shot` renders four checksum-pinned, count-entailed calibration pages
  from local corpus PDFs and supplies their positive and negative labels as
  image-backed references. The images are not copied into the repository.
- `--cascade-few-shot` adds two checksum-pinned figures whose saturated counts
  entail their compatible, compliant, and best-practice labels. It supplies no
  invented rule-level failure label and remains disabled by default because
  the transfer benchmark regresses.
- `--staged` runs the compatibility-only prompt first, skips the rubric call
  for negatives, and treats positives as preclassified when applying all 31
  rules. The cascade transfer benchmark regresses, so the single-call cascade
  remains the default.
- `--whole-paper` sends every unique page image and caption from one paper in
  one request and requires one verdict per censused figure.
- `--self-consistency 3` asks for an explicit borderline flag and draws two
  additional samples only for an initially borderline figure. Whole-paper
  mode resamples the paper call but replaces only its initially borderline
  verdicts.

All judge backends implement the same image-reference and whole-paper
contracts. Compatibility report checkpoints include the prompt profile,
judging mode, and requested self-consistency sample count, so incompatible
variants cannot be mixed by `--resume`.

Paired candidate reports are compared only when their figure identities,
entailed labels, and full paper membership match:

```bash
uv run sbol-visual-eval compare-compatibility \
  data/reports/compatibility_full_pool_baseline.csv \
  data/reports/compatibility_era_holdout.csv
```

The comparison JSON reports fixed and regressed figure calls, saturated-paper
exact transitions, MAE, and net count bias for the shared paper set.

## Expert adjudication layer

`adjudicate` builds a local HTML gallery for exact-label compatibility
disagreements without changing the historical corpus:

```bash
uv run sbol-visual-eval adjudicate \
  data/reports/compatibility_full_pool_baseline.csv
```

Each row contains the rendered figure page, historical entailed verdict,
evaluator verdict, and rationale, plus blank expert decision and notes fields.
Expert decisions live under `data/adjudicated/`; regeneration preserves
reviewed rows and refuses to drop them. Rendered page images and HTML remain
gitignored because source-paper licenses vary.

## Remaining calibration levers, in order of expected value

1. **Compatible-stage recall on positive-heavy papers** — the compatibility
   boundary is balanced corpus-wide, but fully-saturated papers still lose
   ~20% of their figures at the compatible stage, which caps every downstream
   stage; the 274 certain-positive figures are the tuning set.
2. **Best-practice residual** — errors are now balanced but sizable in both
   directions; further gains likely need larger benchmark samples or
   disagreement adjudication rather than more prompt text.
3. **Judge model and prompt variants** — sweep with `--model` and compare
   agreement reports; the report stem flag keeps runs side by side.
4. **Disagreement adjudication** — papers where predicted and historical
   counts disagree are exactly where expert review is worth buying; adjudicated
   figures become a new, separately layered figure-level ground truth. This is
   also the only way past the cross-paper consistency ceiling.

Calibration seeds are burned once used: paper-level seed 7, compatibility
seeds 101 and 777, and cascade seed 55 have all influenced prompt text.
Headline numbers must come from seeds that never did.
