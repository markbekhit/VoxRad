"""Standalone impression generation for the public RadSpeed Impressions tool.

Reuses the OpenAI-compatible client and style-preamble plumbing from
`llm.format`, but with a purpose-built system prompt that produces ONLY an
impression, not a full structured report. Designed for the free copy-paste
funnel tool — no template selection, no patient context, no recommendations
loop. Style preferences (spelling, units, laterality, impression style) are
honoured.
"""
from __future__ import annotations

import logging
from typing import Optional

from openai import OpenAI

from config.config import config
from llm.format import _build_style_preamble

logger = logging.getLogger(__name__)


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
5. If the findings explicitly mention a guideline-relevant feature (e.g.
   pulmonary nodule with size, BIRADS-relevant breast lesion, LIRADS-relevant
   liver lesion, PIRADS-relevant prostate lesion, TIRADS-relevant thyroid
   nodule), include the appropriate category / recommendation only when the
   findings provide enough detail to support it. If detail is insufficient,
   say so explicitly rather than guess.
6. If the findings are entirely normal, say so plainly in one or two lines.
7. Honour the spelling, numeral, measurement-unit, separator, decimal,
   laterality, negation, and impression-format style preferences supplied in
   the style preamble exactly. The impression-format preference (bulleted,
   numbered, prose) governs the output structure.
8. Never restate the patient's clinical history; this is the impression, not
   a summary.
9. If the findings appear non-radiological, irrelevant, or empty, output
   exactly: "Insufficient findings provided to generate an impression."
"""


_GUIDELINE_ADDENDUM = """\

GUIDELINE-AWARE RECOMMENDATIONS:
The user has requested guideline-aware follow-up recommendations. When the
findings clearly support a guideline application, append a short follow-up
recommendation in plain prose at the end of the impression. Use the
guideline only when the findings supply enough detail to apply it correctly.

- Pulmonary nodules: Fleischner Society 2017 guidelines.
- Breast lesions: BI-RADS (mammography or ultrasound as appropriate).
- Liver observations in at-risk patient: LI-RADS (only if at-risk status is
  stated; do not assume it).
- Prostate lesions on multiparametric MR: PI-RADS v2.1.
- Thyroid nodules on ultrasound: ACR TI-RADS.

Do NOT apply a guideline if the findings lack the required detail (e.g. no
size given, no margin description). Instead, recommend the specific extra
detail needed.
"""


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
        system_content += _GUIDELINE_ADDENDUM

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
