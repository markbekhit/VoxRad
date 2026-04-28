"""Standalone impression generation for the public RadSpeed Impressions tool.

Reuses the OpenAI-compatible client and style-preamble plumbing from
`llm.format`, but with a purpose-built system prompt that produces ONLY an
impression, not a full structured report. Designed for the free copy-paste
funnel tool — no template selection, no patient context, no recommendations
loop. Style preferences (spelling, units, laterality, impression style) are
honoured.

When `with_guidelines=True`, the relevant guideline content (Fleischner /
BI-RADS / LI-RADS / PI-RADS / TI-RADS) is loaded from `guidelines/` and
injected verbatim into the system prompt, with the model required to cite
the guideline by name and the rule it applied.
"""
from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

from openai import OpenAI

from config.config import config
from llm.format import _build_style_preamble

logger = logging.getLogger(__name__)


_GUIDELINES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "guidelines",
)


# Each entry: (display name, file basename, regex of trigger keywords).
# Order matters only for de-duplication; all matching guidelines are loaded.
_GUIDELINE_REGISTRY: List[tuple[str, str, re.Pattern]] = [
    (
        "Fleischner Society 2017 (incidental pulmonary nodules)",
        "Fleischner_Society_2017_guidelines.md",
        re.compile(
            r"\b(pulmonary\s+nodule|lung\s+nodule|"
            r"(?:solid|subsolid|part-?solid|ground[-\s]?glass)\s+nodule|"
            r"GGN|GGO|nodule[s]?\s+in\s+the\s+(?:right|left)?\s*(?:upper|middle|lower)\s+lobe|"
            r"spiculated\s+nodule)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ACR BI-RADS (mammography)",
        "BIRADS_MAMMOGRAPHY.md",
        re.compile(
            r"\b(mammogra(?:m|phy|phic)|breast\s+(?:mass|lesion|calcification|"
            r"asymmetry|architectural\s+distortion)|microcalcification)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ACR BI-RADS (ultrasound)",
        "BIRADS_USG.md",
        re.compile(
            r"\b(breast\s+ultrasound|breast\s+US|breast\s+sonograph)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "LI-RADS v2018 (liver, at-risk patients)",
        "LIRADS_(Liver).md",
        re.compile(
            r"\b(LI-?RADS|liver\s+(?:lesion|observation|nodule|mass)|"
            r"hepatic\s+(?:lesion|observation|nodule|mass)|HCC|"
            r"hepatocellular)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "PI-RADS v2.1 (prostate mpMRI)",
        "PIRADS.md",
        re.compile(
            r"\b(PI-?RADS|prostate\s+(?:lesion|nodule|mass)|"
            r"peripheral\s+zone|transition\s+zone|mpMRI|multiparametric\s+MR)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ACR TI-RADS (thyroid nodules on US)",
        "TIRADS.md",
        re.compile(
            r"\b(TI-?RADS|thyroid\s+(?:nodule|lesion))\b",
            re.IGNORECASE,
        ),
    ),
]


_MAX_GUIDELINE_BYTES = 16000  # cap per request to control prompt size


def _load_guideline_file(basename: str) -> Optional[str]:
    path = os.path.join(_GUIDELINES_DIR, basename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        logger.warning("could not read guideline %s: %s", basename, exc)
        return None


def _select_relevant_guidelines(text: str) -> List[tuple[str, str]]:
    """Return [(display_name, file_content), ...] for guidelines whose trigger
    pattern matches the supplied text. Total content is capped at
    _MAX_GUIDELINE_BYTES.
    """
    if not text:
        return []
    out: List[tuple[str, str]] = []
    used = 0
    for name, basename, pattern in _GUIDELINE_REGISTRY:
        if not pattern.search(text):
            continue
        content = _load_guideline_file(basename)
        if not content:
            continue
        if used + len(content) > _MAX_GUIDELINE_BYTES:
            # Truncate the last guideline rather than dropping it entirely
            remaining = _MAX_GUIDELINE_BYTES - used
            if remaining > 500:
                out.append((name, content[:remaining] + "\n... [truncated]"))
            break
        out.append((name, content))
        used += len(content)
    return out


_IMPRESSION_SYSTEM_PROMPT = """\
You are a senior radiologist writing the IMPRESSION section of a radiology
report.

Your job is to produce a CONCISE, SYNTHESISED impression — NOT a re-listing
of the findings. The impression is what the referring clinician reads when
they do not have time for the whole report. It must surface the clinically
actionable conclusions, not summarise every finding.

LENGTH:
- Aim for 2 to 5 short points (or one short paragraph in prose mode).
- The impression should be MUCH shorter than the findings — typically
  20-40% of the findings length, often less.
- If the findings list eight paragraphs of normal anatomy and one disc
  bulge, the impression is one to two lines about the disc bulge.

SYNTHESIS — this is the most important rule:
- GROUP related findings into a single conclusion. Do NOT enumerate.
  Wrong: "L3-4 disc bulge, L4-5 disc bulge, L5-S1 disc bulge."
  Right: "Multilevel degenerative disc disease at L3-S1, most prominent
  at L4-5."
- LEAD with the clinically most significant finding, regardless of
  anatomical order in the findings. If there is ONE significant finding
  amongst otherwise-normal anatomy, often the whole impression is just
  that finding plus a one-line "Otherwise unremarkable [study]" closer.

OMIT BY DEFAULT — do NOT include the following unless they directly
answer the indication:
- Normal organ size, shape, position, or signal/echogenicity.
- Simple cysts (renal, hepatic, ovarian if simple, nabothian, Bartholin).
- Trace pleural / peritoneal / pelvic free fluid.
- Mild atherosclerosis with normal vessel calibre.
- Age-typical degenerative change.
- Pelvic ultrasound: normal uterus, endometrium, contralateral ovary,
  Pouch of Douglas, and kidneys when only one ovary has a finding.
- Spine: normal vertebral bodies, marrow, conus, and discs above the
  abnormal levels.
- Head CT: normal grey-white differentiation, ventricles, sulci, sinuses
  unless the indication is trauma / stroke / mass effect.
- Chest CT: normal mediastinum, pleural spaces, and bones when there's a
  positive lung finding.

ONLY include a normal finding if it directly answers the clinical
question (e.g. "no acute intracranial haemorrhage" on a trauma head CT
is appropriate; "lungs are clear" on a lumbar spine MRI is not).

When the study is normal except for one or two findings, prefer this
shape:
  "<the finding(s) in one or two lines>.
   Otherwise unremarkable <study type>."

ABSOLUTE RULES:
1. Output ONLY the impression text. Do NOT output a heading, do NOT
   output the findings, do NOT prepend "IMPRESSION:" — just the body.
2. Every conclusion must be supported by the findings. Never invent
   findings, measurements, or features the report doesn't state.
3. Honour the spelling, numeral, measurement-unit, separator, decimal,
   laterality, negation, and impression-format style preferences supplied
   in the style preamble exactly. The impression-format preference
   (bulleted, numbered, prose) governs the output structure.
4. If the findings are entirely normal, say so plainly in one or two lines.
5. Never restate the patient's clinical history; this is the impression,
   not a clinical summary.
6. If the findings appear non-radiological, irrelevant, or empty, output
   exactly: "Insufficient findings provided to generate an impression."

WORKED EXAMPLE (multilevel lumbar MRI — synthesis vs enumeration):

Findings: "Vertebral body heights preserved. Marrow signal normal. T11-12,
T12-L1, L1-2 discs normal. L2-3 mild dehydration with small central
protrusion not contacting cord. L3-4 moderate dehydration with broad-based
posterior bulge causing mild bilateral neural foraminal narrowing. L4-5
severe dehydration with 4 mm posterior protrusion contacting the
descending right L5 nerve root. L5-S1 moderate dehydration with mild
bilateral facet arthropathy. Conus and cauda equina normal."

WRONG (verbose, restates each level):
"- T11-12 to L1-2 discs normal.
- L2-3 mild dehydration with small central protrusion.
- L3-4 broad-based bulge with mild bilateral foraminal narrowing.
- L4-5 4 mm posterior protrusion contacting right L5 nerve root.
- L5-S1 mild facet arthropathy.
- Conus and cauda equina normal."

RIGHT (synthesised, action-focused):
"- Multilevel degenerative disc disease, most prominent at L4-5.
- L4-5 4 mm posterior disc protrusion contacting the descending right L5
  nerve root — likely cause of any right L5 radiculopathy.
- No central canal stenosis, cord compression, or cauda equina abnormality."

WORKED EXAMPLE (pelvic ultrasound — one finding, everything else normal):

Findings: "Uterus anteverted, 79 x 40 x 56 mm. Myometrium homogeneous.
Cervix shows simple nabothian cysts, largest 6 mm. Endometrium 3 mm,
well-defined. Right ovary 22 x 27 x 24 mm with a 46 x 30 x 38 mm simple
cyst. Left ovary 13 x 20 x 13 mm, normal. No free fluid in the Pouch of
Douglas. Right kidney 100 mm, normal cortical thickness, no calculi or
hydronephrosis. Left kidney 102 mm, normal."

WRONG (lists every paragraph as its own impression point):
"- Normal uterine size and morphology with a well-defined endometrium.
- Simple nabothian cysts in the cervix, largest 6 mm.
- Right ovary demonstrates a simple cyst measuring 46 x 30 x 38 mm; left
  ovary appears normal.
- No free fluid in the Pouch of Douglas; both kidneys are normal."

RIGHT (one finding, then an "otherwise unremarkable" closer):
"- Right ovarian simple cyst measuring 46 x 30 x 38 mm.
- Otherwise unremarkable pelvic ultrasound."

Note how nabothian cysts, normal uterus, normal contralateral ovary,
absent free fluid, and normal kidneys are ALL omitted — they do not
answer any clinical question and a normal-anatomy phrase like
"otherwise unremarkable" covers them implicitly.
"""


def _build_guideline_block(matched: List[tuple[str, str]]) -> str:
    """Compose the guideline-aware addendum from the matched guideline files."""
    if not matched:
        return (
            "\n\nGUIDELINE-AWARE RECOMMENDATIONS:\n"
            "The user requested guideline-aware recommendations, but the "
            "findings did not match any of the guidelines we provide "
            "verbatim (Fleischner, BI-RADS, LI-RADS, PI-RADS, TI-RADS). "
            "Do NOT invent guideline names or thresholds. If a follow-up "
            "recommendation is appropriate, give general clinical advice "
            "without claiming to apply a specific guideline.\n"
        )

    blocks: List[str] = [
        "\n\nGUIDELINE-AWARE RECOMMENDATIONS — MANDATORY:\n\n",
        "The user has explicitly requested guideline-aware recommendations. ",
        "The following guideline content is supplied verbatim and is the ",
        "ONLY source you may use to derive recommendations.\n\n",

        "ABSOLUTE REQUIREMENT — citation:\n",
        "When a finding matches a rule in the supplied guideline content, ",
        "your impression MUST end with a separate recommendation point that ",
        "begins with the literal phrase \"Per [Guideline Name], \" using the ",
        "guideline name exactly as written in the BEGIN GUIDELINE header ",
        "below. Failing to cite the guideline by name when one is supplied ",
        "is a defect — the user has asked for guideline-aware output and ",
        "expects to see the source attributed.\n\n",

        "REQUIRED FORMAT for each guideline-driven recommendation:\n",
        "  \"Per <Guideline Name as supplied>, <feature that triggered the ",
        "rule, including size>: <management options quoted literally from ",
        "the guideline text, in full>.\"\n\n",

        "WORKED EXAMPLE (for a 14 mm spiculated solid pulmonary nodule):\n",
        "  \"Per Fleischner Society 2017 (incidental pulmonary nodules), ",
        "for a single solid nodule >8 mm: consider options including CT at ",
        "3 months, PET/CT, or tissue sampling, based on the probability of ",
        "malignancy.\"\n\n",

        "Additional rules:\n",
        "1. Apply the rule from the supplied content only — never from memory.\n",
        "2. Quote the management options in FULL. Do not silently drop ",
        "options (e.g. do not say \"PET/CT or tissue sampling\" if the ",
        "guideline lists \"CT at 3 months, PET/CT, or tissue sampling\").\n",
        "3. If risk status (smoker / at-risk for HCC / etc.) is not stated ",
        "AND the guideline gives different recommendations by risk stratum, ",
        "either give the recommendation for both strata or note that risk ",
        "stratum is not stated. If the guideline does not stratify by risk ",
        "for this finding, do not mention risk.\n",
        "4. If a finding is potentially guideline-relevant but the findings ",
        "lack required detail (e.g. nodule mentioned without a size), say ",
        "exactly what extra detail is needed rather than guessing.\n",
    ]

    for name, content in matched:
        blocks.append(f"\n\n--- BEGIN GUIDELINE: {name} ---\n")
        blocks.append(content.strip())
        blocks.append(f"\n--- END GUIDELINE: {name} ---\n")

    return "".join(blocks)


def stream_impression(
    findings: str,
    modality: Optional[str] = None,
    style: Optional[dict] = None,
    with_guidelines: bool = False,
):
    """Yield impression text chunks generated from the supplied findings.

    Caller is responsible for SSE / clipboard / display. Errors are raised so
    the caller can surface a friendly message; this function does not return
    a placeholder string on failure.
    """
    if not findings or not findings.strip():
        return

    client = OpenAI(api_key=config.TEXT_API_KEY, base_url=config.BASE_URL)

    system_content = _IMPRESSION_SYSTEM_PROMPT + _build_style_preamble(style)
    if with_guidelines:
        haystack = f"{modality or ''}\n{findings}"
        matched = _select_relevant_guidelines(haystack)
        if matched:
            logger.info(
                "[impressions] injecting guidelines: %s",
                ", ".join(name for name, _ in matched),
            )
        system_content += _build_guideline_block(matched)

    user_content = (
        f"Modality / study: {modality.strip()}\n\n"
        if modality and modality.strip()
        else ""
    ) + f"FINDINGS:\n{findings.strip()}\n\nWrite the impression now."

    stream = client.chat.completions.create(
        model=config.SELECTED_MODEL,
        stream=True,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        temperature=0.1,
    )

    in_think = False
    think_buf = ""
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta is None:
            continue
        # Strip <think>...</think> blocks from reasoning models on the fly
        if not in_think:
            if "<think>" in delta:
                pre, _, rest = delta.partition("<think>")
                if pre:
                    yield pre
                in_think = True
                think_buf = rest
                continue
            yield delta
        else:
            think_buf += delta
            if "</think>" in think_buf:
                _, _, post = think_buf.partition("</think>")
                in_think = False
                think_buf = ""
                if post:
                    yield post
