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

# These references come only from the calibration side of the paper-level,
# era-stratified split. Each label follows deductively from a saturated paper
# count. Images render from checksum-pinned local corpus PDFs at runtime rather
# than being copied into the source tree.
COMPATIBILITY_EXEMPLAR_PROFILE = "boundary_pages_v1"
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
        identifier="10.1021/acssynbio.6b00009",
        publication_year=2016,
        figure_number=1,
        caption_text=("Figure 1. Example of a buffered four-domain DNA strand-displacement gate."),
        expected_compatible=False,
        rationale=(
            "The panel excluded this abstract domain-level strand-displacement mechanism "
            "despite its DNA circuit-design language."
        ),
        pdf_path=("data/papers/2016/10.1021__acssynbio.6b00009/publisher/paper.pdf"),
        pdf_sha256="d71f1b3d5bf85fe65165afb392b59b423d30947aa0ab3433b46c33d0f4e76fcf",
        page_number=3,
    ),
    CompatibilityExemplarSpec(
        identifier="10.1021/acssynbio.3c00375",
        publication_year=2023,
        figure_number=2,
        caption_text=(
            "Figure 2. Model suggests that differential affinities of scRNA and sgRNA are "
            "a problem."
        ),
        expected_compatible=True,
        rationale=(
            "The panel included the visible promoter-reporter circuit design embedded in "
            "this modeling-and-data figure."
        ),
        pdf_path="data/papers/2023/10.1021__acssynbio.3c00375/pmc/paper.pdf",
        pdf_sha256="4abb64c13e5988475d07371121e6373d9d4424404f41868c144f2229f9d8259d",
        page_number=3,
    ),
    CompatibilityExemplarSpec(
        identifier="10.1021/acssynbio.3c00124",
        publication_year=2023,
        figure_number=1,
        caption_text="Figure 1. Genome-Integration Module Workflow.",
        expected_compatible=False,
        rationale=(
            "The panel excluded this cloning and genome-integration workflow even though "
            "it depicts plasmid backbones and transcription-unit cassettes."
        ),
        pdf_path="data/papers/2023/10.1021__acssynbio.3c00124/pmc/paper.pdf",
        pdf_sha256="72cc92e8795f6777636b445f7744793410d26ebb30f3ad50902028ba2272ba6d",
        page_number=2,
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


def render_compatibility_exemplar(exemplar: CompatibilityExemplar) -> str:
    """Render the historical label paired with one reference image."""
    expected = "COMPATIBLE" if exemplar.expected_compatible else "NOT compatible"
    return (
        f"Historical reference {exemplar.identifier}, published {exemplar.publication_year}, "
        f"Figure {exemplar.figure_number}. Its caption begins: "
        f'"{exemplar.caption_text[:600]}"\n'
        f"The historical verdict is {expected}. {exemplar.rationale}"
    )


def build_compatibility_prompt(figure_number: int, caption_text: str) -> str:
    """A compatibility-only prompt for cheap, large-scale boundary calibration."""
    return f"""\
The attached image is the manuscript page containing Figure {figure_number}.
Its caption begins: "{caption_text[:600]}"

Evaluate Figure {figure_number} only, considering every panel that belongs to it.

{COMPATIBILITY_DEFINITION}

Respond with a single JSON object and nothing else:
{{
  "figure_number": {figure_number},
  "compatible": true or false,
  "rationale": "one or two sentences"
}}"""


def _paper_figure_list(contexts: Sequence[FigureContext]) -> str:
    lines = []
    for context in contexts:
        page = f", manuscript page {context.page_number}" if context.page_number is not None else ""
        lines.append(
            f'- Figure {context.figure_number}{page}; caption begins: "{context.caption_text[:600]}"'
        )
    return "\n".join(lines)


def build_compatibility_paper_prompt(contexts: Sequence[FigureContext]) -> str:
    """A compatibility-only prompt that applies one standard across a paper."""
    figures = _paper_figure_list(contexts)
    return f"""\
The attached images are the manuscript pages containing these figures:
{figures}

Evaluate every listed figure, considering every panel that belongs to it. Apply
one consistent historical-review standard across the paper, while returning a
separate verdict and rationale for each figure.

{COMPATIBILITY_DEFINITION}

Respond with a single JSON object and nothing else:
{{
  "figures": [
    {{
      "figure_number": integer,
      "compatible": true or false,
      "rationale": "one or two sentences"
    }}
    ... one entry for every listed figure, in listed order ...
  ]
}}"""


def build_user_prompt(figure_number: int, caption_text: str, rules: list[RubricRule]) -> str:
    return f"""\
The attached image is the manuscript page containing Figure {figure_number}.
Its caption begins: "{caption_text[:600]}"

Evaluate Figure {figure_number} only, considering every panel that belongs to it.

{COMPATIBILITY_DEFINITION}

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

Respond with a single JSON object and nothing else:
{{
  "figure_number": {figure_number},
  "compatible": true or false,
  "rationale": "one or two sentences",
  "findings": [
    {{"rule_key": "compliance:5.1.0", "verdict": "pass" | "fail" | "not_applicable",
      "evidence": "short justification"}},
    ... one entry for every rule listed above (omit all when not compatible) ...
  ]
}}"""


def build_paper_user_prompt(contexts: Sequence[FigureContext], rules: list[RubricRule]) -> str:
    """A full-cascade prompt that applies one standard across a paper."""
    figures = _paper_figure_list(contexts)
    return f"""\
The attached images are the manuscript pages containing these figures:
{figures}

Evaluate every listed figure, considering every panel that belongs to it. Apply
one consistent historical-review standard across the paper, while returning a
separate verdict and rationale for each figure.

{COMPATIBILITY_DEFINITION}

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

Respond with a single JSON object and nothing else:
{{
  "figures": [
    {{
      "figure_number": integer,
      "compatible": true or false,
      "rationale": "one or two sentences",
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
