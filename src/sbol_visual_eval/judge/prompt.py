"""The rubric-conditioned judging prompt.

One prompt evaluates one figure through the full historical cascade:
is the figure appropriate for SBOL Visual at all, and if so, which
compliance and best-practice rules does it satisfy. The compatibility
definition mirrors the retrospective study's scope — diagrams of
engineered nucleic-acid designs — and is the main calibration surface
when evaluator counts drift from the historical counts.
"""

from __future__ import annotations

from collections.abc import Sequence

from .rubric import RubricRule, render_rubric
from .schema import CompatibilityExemplar, CompatibilityExemplarSpec, FigureContext

COMPATIBILITY_PROMPT_PROFILE = "historical_2025_prose_v1"
ERA_COMPATIBILITY_PROMPT_PROFILE = "historical_2025_era_v3"
BORDERLINE_PROMPT_PROFILE = "borderline_vote_v1"
CODEX_THRESHOLD_PROMPT_PROFILE = "codex_panel_threshold_v1"

# These references come only from the calibration side of the paper-level,
# era-stratified split. Each label follows deductively from a saturated paper
# count. Images render from checksum-pinned local corpus PDFs at runtime rather
# than being copied into the source tree.
COMPATIBILITY_EXEMPLAR_PROFILE = "boundary_pages_v2"
COMPATIBILITY_EXEMPLAR_SPECS = (
    CompatibilityExemplarSpec(
        identifier="10.1021/acssynbio.5b00124",
        publication_year=2016,
        figure_number=1,
        caption_text="Figure 1. CIDAR MoClo overview: basic assembly strategy.",
        expected_compatible=True,
        rationale=(
            "The early historical panel included this conventional physical construct and "
            "modular-assembly cartography."
        ),
        pdf_path=("data/papers/2016/10.1021__acssynbio.5b00124/publisher/paper.pdf"),
        pdf_sha256="8d198020b8d8cae0dce05337ed547aa0633499450ddfbfc27508fe38912aaa68",
        page_number=2,
    ),
    CompatibilityExemplarSpec(
        identifier="10.1021/sb300018h",
        publication_year=2012,
        figure_number=1,
        caption_text=(
            "Figure 1. Systematic construction of a transcriptional inverter, repeater, "
            "and circuits composed of modular switch motifs."
        ),
        expected_compatible=False,
        rationale=(
            "The early panel excluded this abstract switch-topology diagram despite its "
            "engineered DNA circuit subject."
        ),
        pdf_path="data/papers/2012/10.1021__sb300018h/publisher/paper.pdf",
        pdf_sha256="04b537127afc4847d370f1e68e151fe4542beeb3800d5c1099320cecda0ddd0d",
        page_number=2,
    ),
    CompatibilityExemplarSpec(
        identifier="10.1021/acssynbio.7b00209",
        publication_year=2018,
        figure_number=1,
        caption_text="Figure 1. Scheme of plasmids used in the IIS-alphoid-tetO-HAC system.",
        expected_compatible=True,
        rationale=(
            "The panel included these concrete engineered carrier and expression plasmid "
            "designs despite their cloning, selection, and recombination annotations."
        ),
        pdf_path="data/papers/2018/10.1021__acssynbio.7b00209/paper.pdf",
        pdf_sha256="cf36654ac6431d84a2b5c15c647b698161afd0d5e410c6a5fde3a7b25dc26a9c",
        page_number=3,
    ),
    CompatibilityExemplarSpec(
        identifier="10.1021/acssynbio.5b00249",
        publication_year=2016,
        figure_number=2,
        caption_text="Figure 2. Genome engineering of target genes using DNA-free CRISPR/Cas9.",
        expected_compatible=False,
        rationale=(
            "The panel excluded these annotated native target-locus maps because they mark "
            "cut sites without specifying an engineered construct's composition."
        ),
        pdf_path="data/papers/2016/10.1021__acssynbio.5b00249/publisher/paper.pdf",
        pdf_sha256="51703c6672dda8cb0091970c0df2e2227dab3a87982d688dc14eb197a767901b",
        page_number=3,
    ),
)

# Historical interpretations recovered from count-saturated papers: papers whose
# compatible and compliant counts match label every compatible figure compliant,
# so figure styles common in those papers cannot violate a compliance rule under
# the panel's reading. Wrong-role glyph forms appear only in papers scored
# non-compliant. These notes bind the judge to the panel's operational reading
# rather than a literal one.
HISTORICAL_INTERPRETATIONS = {
    "compliance:5.2.6": (
        "The panel failed this rule when a glyph asserts a role the feature does not"
        " have (for example, a promoter drawn with the arrow form of a CDS)."
        " Features drawn as generic shapes — plain rectangles, bars, or boxes —"
        " whose role is conveyed by an adjacent text label were accepted as"
        " compliant; generic-shape glyph choice is penalized only under"
        " best-practice 5.2.6."
    ),
    "compliance:5.3.1": (
        "The panel accepted molecular species drawn as generic labeled shapes;"
        " this rule failed only when a glyph asserts an incorrect species type."
    ),
    "compliance:5.2.1": (
        "The panel required feature glyphs to be anchored on the backbone line;"
        " minor rendering gaps in an otherwise backbone-anchored layout were not"
        " failed."
    ),
    # Best-practice leniency measured on papers whose counts prove every
    # compliant figure also followed best practices.
    "best_practice:5.1.1": (
        "This rule is conditional: it applies only to a diagram that shows both"
        " single- and double-stranded constructs. Answer not_applicable when the"
        " diagram shows only one strandedness, which is the usual case; a"
        " single-line backbone is not a failure on its own."
    ),
    "best_practice:5.1.3": (
        "The panel accepted linear backbones for constructs presented as linear"
        " designs. Fail only when a figure asserts a plasmid or genomic-locus"
        " context and draws it without the corresponding glyph shape, or shows"
        " truncated context with no omitted-detail indication."
    ),
    "best_practice:5.2.6": (
        "The reviewers were explicitly flexible on glyph version and accepted"
        " stylistic variants, so a figure whose features use recognizable"
        " SBOL-style glyph vocabulary satisfies this rule even when the shapes or"
        " versions differ from the current RECOMMENDED set. Fail only when"
        " features are drawn as undifferentiated generic shapes carrying no glyph"
        " vocabulary at all."
    ),
    "best_practice:5.2.1": (
        "The panel accepted ordinary backbone alignment; fail only when glyphs sit"
        " clearly off the backbone in a way that obscures reading."
    ),
    "best_practice:5.3.1": (
        "As with nucleic-acid glyphs, degenerate and stylistic molecular-species"
        " forms were accepted; fail only when a species is drawn with no"
        " type-bearing glyph vocabulary."
    ),
    "best_practice:5.1.4": (
        "Answer not_applicable for assembly diagrams, where a bare backbone is expected."
    ),
}

SYSTEM_PROMPT = """\
You are an expert reviewer for the SBOL Visual diagram standard, reproducing the
review methodology of the decade-long ACS Synthetic Biology retrospective study.
You judge one manuscript figure at a time from a rendered page image and the
figure's caption, and you apply the study's historical reviewer checklist
exactly as written, including its documented exceptions."""

PAPER_SYSTEM_PROMPT = """\
You are an expert reviewer for the SBOL Visual diagram standard, reproducing the
review methodology of the decade-long ACS Synthetic Biology retrospective study.
You review every manuscript figure under one consistent paper-level standard,
return a separate verdict for each figure, and apply the study's historical
reviewer checklist exactly as written, including its documented exceptions."""

COMPATIBILITY_DEFINITION = """\
A figure is COMPATIBLE with SBOL Visual when it (or any of its panels) is a
diagram that communicates the design of an engineered nucleic-acid construct or
system: sequence features such as promoters, RBSs, CDSs, and terminators laid
out on a backbone; composition of genetic circuits or plasmids; or functional
interactions between such constructs and molecular species. Such content could
be drawn with SBOL Visual glyphs whether or not the authors used them.

A figure is NOT compatible when it contains only: data plots or charts,
micrographs or photographs, gel images, protocol or workflow schematics,
mathematical models, protein-only structures or pathways with no nucleic-acid
design content, or purely conceptual illustrations. The historical panel
counted a figure only when communicating a construct's design is a primary
purpose of the figure or one of its panels; mechanism, workflow, or
base-pairing illustrations that merely include incidental construct sketches
were not counted.

The panel also did not count these, even though each shows sequence features
positioned along a line or circle. Judge them NOT compatible:

- Annotated sequence or motif maps: a native, variant, or target sequence
  marked up with the positions of binding sites, operators, -35/-10 boxes,
  TATA boxes, CpG islands, transcription start sites, or target sites. These
  report where features occur in a sequence rather than specifying a design
  to build.
- Cloning and vector cartography: conventional plasmid, vector, genome, or
  locus maps whose annotations are cloning apparatus — selection markers,
  origins, homology arms, primer-binding sites, restriction sites, insertion
  points, cleavage sites.
- Figures whose organizing structure is something else — a model-composition
  graph, a signaling or metabolic pathway, a host-strain engineering
  overview — with construct depictions attached to its nodes.

The distinction the panel drew is design specification versus sequence
annotation or laboratory cartography: count a figure when it specifies the
composition and arrangement of an engineered construct as a design, not when
it documents where features sit in a sequence or how a cloning product was
assembled.

Apply those three exclusions to what a depiction is, not to the figure's
overall subject. A panel that lays out an engineered construct's composition
as a design still counts when the rest of the figure is data plots, a
workflow, or a mechanism — judge the construct depiction on its own terms.
Decide by asking what the construct depiction does: specify a design to
build (compatible), or annotate a sequence, document a cloning product, or
decorate another structure's nodes (not compatible)."""


def era_compatibility_guidance(publication_year: int | None) -> str:
    """Render the historical compatibility boundary for one publication era."""
    if publication_year is None:
        return ""
    if publication_year <= 2013:
        policy = """\
For 2012-2013 papers, reproduce the early panel's visually conventional
boundary rather than asking only whether a modern SBOL rendering is possible:

- Count a conventional plasmid, vector, genome, locus, cloning, mutagenesis,
  or assembly diagram when that physical cartography is a central subject of
  the figure, even when its labels are restriction sites, primers, markers,
  origins, homology arms, or insertion points.
- Also count a concrete depiction that identifies the specific engineered
  construct, protein/domain variant, or assembly measured in an experiment,
  even when it is a small schematic beside a plot or inside a mechanism panel.
  These concrete experimental designs remain countable without a
  primary-purpose or nucleic-acid-only requirement.
- Do not count purely abstract gene-circuit topology, generic logic or model
  diagrams, or strand/domain interaction mechanisms whose nodes do not depict
  the concrete physical design of the experimental material."""
        era = "2012-2013"
    elif publication_year <= 2016:
        policy = """\
For 2014-2016 papers, reproduce the panel's visually conventional boundary:

- Count a conventional plasmid, vector, cloning, genome-editing, or assembly
  diagram when that physical cartography or construction process is a central
  subject of the figure, even when it emphasizes restriction sites, primers,
  markers, origins, homology arms, or insertion points.
- Also count a concrete construct or reporter-cassette schematic that
  identifies the specific engineered DNA measured in a plot, workflow, or
  signaling mechanism, even when the depiction is small. The historical
  boundary treats those experimental-design identifiers more leniently than
  abstract models.
- Do not count abstract gene-circuit or model topology, DNA
  strand-displacement domain diagrams, or an annotated native sequence or
  locus that shows target positions but no composition of the engineered
  construct."""
        era = "2014-2016"
    elif publication_year <= 2023:
        policy = """\
For 2017-2023 papers, apply the design-specification boundary above directly.
Do not import the earlier panel's exception for conventional cloning and
physical cartography."""
        era = "2017-2023"
    else:
        policy = "Apply the design-specification boundary above directly."
        era = f"post-2023 ({publication_year})"
    return f"""\
PUBLICATION ERA: {publication_year} ({era}).

{policy}

This publication-era policy overrides the general compatibility definition
where the two conflict."""


HISTORICAL_FAILURE_THRESHOLD_REINFORCEMENT = """\
FINAL HISTORICAL THRESHOLD CHECK: This review is deliberately not an exhaustive
specification audit. Before returning findings, change a proposed failure to
pass or not_applicable unless the violation is visually dominant,
unambiguous, changes how the biological design is read, and would be noticed
without zooming or searching for it. Ordinary labeled or color-coded generic
shapes, simple linear or circular backbones, generic interaction arrows, and
conventional boundary geometry are understandable historical notation and do
not fail merely because a more specific RECOMMENDED glyph exists. Do not
accumulate independent formal nitpicks: multiple failures are appropriate only
when each one separately crosses this conspicuous-violation bar."""


def _compatibility_definition(publication_year: int | None, *, era_conditioned: bool) -> str:
    if not era_conditioned:
        return COMPATIBILITY_DEFINITION
    guidance = era_compatibility_guidance(publication_year)
    return f"{COMPATIBILITY_DEFINITION}\n\n{guidance}" if guidance else COMPATIBILITY_DEFINITION


def _borderline_instruction(request_borderline: bool) -> str:
    if not request_borderline:
        return ""
    return """\
Set "borderline" to true only when two reasonable applications of the
historical boundary or conspicuous-violation threshold could reverse the
compatible verdict or a rule finding. Set it to false for confident calls.

"""


def _borderline_property(request_borderline: bool, *, indent: int) -> str:
    if not request_borderline:
        return ""
    return f'{" " * indent}"borderline": true or false,\n'


def render_compatibility_exemplar(exemplar: CompatibilityExemplar) -> str:
    """Render the historical label paired with one reference image."""
    expected = "COMPATIBLE" if exemplar.expected_compatible else "NOT compatible"
    return (
        f"Historical reference {exemplar.identifier}, published {exemplar.publication_year}, "
        f"Figure {exemplar.figure_number}. Its caption begins: "
        f'"{exemplar.caption_text[:600]}"\n'
        f"The historical verdict is {expected}. {exemplar.rationale}"
    )


def build_compatibility_prompt(
    figure_number: int,
    caption_text: str,
    *,
    publication_year: int | None = None,
    era_conditioned: bool = False,
    request_borderline: bool = False,
) -> str:
    """A compatibility-only prompt for cheap, large-scale boundary calibration."""
    compatibility_definition = _compatibility_definition(
        publication_year, era_conditioned=era_conditioned
    )
    borderline_instruction = _borderline_instruction(request_borderline)
    borderline_property = _borderline_property(request_borderline, indent=2)
    return f"""\
The attached image is the manuscript page containing Figure {figure_number}.
Its caption begins: "{caption_text[:600]}"

Evaluate Figure {figure_number} only, considering every panel that belongs to it.

{compatibility_definition}

{borderline_instruction}Respond with a single JSON object and nothing else:
{{
  "figure_number": {figure_number},
  "compatible": true or false,
{borderline_property}  "rationale": "one or two sentences"
}}"""


def _paper_figure_list(contexts: Sequence[FigureContext]) -> str:
    lines = []
    for context in contexts:
        page = f", manuscript page {context.page_number}" if context.page_number is not None else ""
        lines.append(
            f'- Figure {context.figure_number}{page}; caption begins: "{context.caption_text[:600]}"'
        )
    return "\n".join(lines)


def build_compatibility_paper_prompt(
    contexts: Sequence[FigureContext],
    *,
    era_conditioned: bool = False,
    request_borderline: bool = False,
) -> str:
    """A compatibility-only prompt that applies one standard across a paper."""
    figures = _paper_figure_list(contexts)
    publication_year = contexts[0].publication_year if contexts else None
    compatibility_definition = _compatibility_definition(
        publication_year, era_conditioned=era_conditioned
    )
    borderline_instruction = _borderline_instruction(request_borderline)
    borderline_property = _borderline_property(request_borderline, indent=6)
    return f"""\
The attached images are the manuscript pages containing these figures:
{figures}

Evaluate every listed figure, considering every panel that belongs to it. Apply
one consistent historical-review standard across the paper, while returning a
separate verdict and rationale for each figure.

{compatibility_definition}

{borderline_instruction}Respond with a single JSON object and nothing else:
{{
  "figures": [
    {{
      "figure_number": integer,
      "compatible": true or false,
{borderline_property}      "rationale": "one or two sentences"
    }}
    ... one entry for every listed figure, in listed order ...
  ]
}}"""


def build_user_prompt(
    figure_number: int,
    caption_text: str,
    rules: list[RubricRule],
    *,
    publication_year: int | None = None,
    era_conditioned: bool = False,
    request_borderline: bool = False,
    reinforce_historical_threshold: bool = False,
) -> str:
    compatibility_definition = _compatibility_definition(
        publication_year, era_conditioned=era_conditioned
    )
    borderline_instruction = _borderline_instruction(request_borderline)
    borderline_property = _borderline_property(request_borderline, indent=2)
    threshold_reinforcement = (
        f"{HISTORICAL_FAILURE_THRESHOLD_REINFORCEMENT}\n\n"
        if reinforce_historical_threshold
        else ""
    )
    return f"""\
The attached image is the manuscript page containing Figure {figure_number}.
Its caption begins: "{caption_text[:600]}"

Evaluate Figure {figure_number} only, considering every panel that belongs to it.

{compatibility_definition}

If and only if the figure is compatible, evaluate every rule below against the
figure's genetic-design content. Mark a rule "not_applicable" when the figure
contains no element the rule governs, "pass" when the governed elements satisfy
it, and "fail" when any governed element violates it. Honor every reviewer
exception noted under a rule.

Calibrate your failure threshold to the historical panel's. The panel failed a
rule only on a clear violation that an experienced reviewer would flag on a
first reading of the figure; borderline observations, judgment calls, and
details only visible under close scrutiny were recorded as pass or
not_applicable. This matters most for the SHOULD rules: about half of all
compliant figures met the panel's best-practice bar, and the figures that
missed it violated a rule conspicuously — do not deny best practice over a
single subtle imperfection hunted out of an otherwise well-drawn diagram.

{render_rubric(rules, interpretations=HISTORICAL_INTERPRETATIONS)}

{threshold_reinforcement}{borderline_instruction}Respond with a single JSON object and nothing else:
{{
  "figure_number": {figure_number},
  "compatible": true or false,
{borderline_property}  "rationale": "one or two sentences",
  "findings": [
    {{"rule_key": "compliance:5.1.0", "verdict": "pass" | "fail" | "not_applicable",
      "evidence": "short justification"}},
    ... one entry for every rule listed above (omit all when not compatible) ...
  ]
}}"""


def build_paper_user_prompt(
    contexts: Sequence[FigureContext],
    rules: list[RubricRule],
    *,
    era_conditioned: bool = False,
    request_borderline: bool = False,
    reinforce_historical_threshold: bool = False,
) -> str:
    """A full-cascade prompt that applies one standard across a paper."""
    figures = _paper_figure_list(contexts)
    publication_year = contexts[0].publication_year if contexts else None
    compatibility_definition = _compatibility_definition(
        publication_year, era_conditioned=era_conditioned
    )
    borderline_instruction = _borderline_instruction(request_borderline)
    borderline_property = _borderline_property(request_borderline, indent=6)
    threshold_reinforcement = (
        f"{HISTORICAL_FAILURE_THRESHOLD_REINFORCEMENT}\n\n"
        if reinforce_historical_threshold
        else ""
    )
    return f"""\
The attached images are the manuscript pages containing these figures:
{figures}

Evaluate every listed figure, considering every panel that belongs to it. Apply
one consistent historical-review standard across the paper, while returning a
separate verdict and rationale for each figure.

{compatibility_definition}

If and only if a figure is compatible, evaluate every rule below against that
figure's genetic-design content. Mark a rule "not_applicable" when the figure
contains no element the rule governs, "pass" when the governed elements satisfy
it, and "fail" when any governed element violates it. Honor every reviewer
exception noted under a rule.

Calibrate your failure threshold to the historical panel's. The panel failed a
rule only on a clear violation that an experienced reviewer would flag on a
first reading of the figure; borderline observations, judgment calls, and
details only visible under close scrutiny were recorded as pass or
not_applicable. This matters most for the SHOULD rules: about half of all
compliant figures met the panel's best-practice bar, and the figures that missed
it violated a rule conspicuously — do not deny best practice over a single
subtle imperfection hunted out of an otherwise well-drawn diagram.

{render_rubric(rules, interpretations=HISTORICAL_INTERPRETATIONS)}

{threshold_reinforcement}{borderline_instruction}Respond with a single JSON object and nothing else:
{{
  "figures": [
    {{
      "figure_number": integer,
      "compatible": true or false,
{borderline_property}      "rationale": "one or two sentences",
      "findings": [
        {{"rule_key": "compliance:5.1.0",
          "verdict": "pass" | "fail" | "not_applicable",
          "evidence": "short justification"}}
        ... one entry for every rule listed above (omit all when not compatible) ...
      ]
    }}
    ... one entry for every listed figure, in listed order ...
  ]
}}"""
