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

You will be given the FINDINGS portion of a study (which may be a freeform
paragraph, dictated text, or a structured findings list). Your job is to
produce a concise, clinically useful IMPRESSION that summarises the key
diagnostic conclusions.

RULES:
1. Output ONLY the impression text. Do NOT output a heading, do NOT output
   the findings, do NOT prepend "IMPRESSION:" — just the impression body.
2. Lead with the most clinically significant finding. Sequence subsequent
   points by clinical importance, not by anatomical order.
3. Each impression point must be supported by the findings. Do NOT invent
   findings, do NOT hallucinate measurements, do NOT extrapolate beyond
   what the findings state.
4. Keep each point tight: one clear clinical statement, no padding.
5. If the findings are entirely normal, say so plainly in one or two lines.
6. Honour the spelling, numeral, measurement-unit, separator, decimal,
   laterality, negation, and impression-format style preferences supplied in
   the style preamble exactly. The impression-format preference (bulleted,
   numbered, prose) governs the output structure.
7. Never restate the patient's clinical history; this is the impression, not
   a summary.
8. If the findings appear non-radiological, irrelevant, or empty, output
   exactly: "Insufficient findings provided to generate an impression."
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
