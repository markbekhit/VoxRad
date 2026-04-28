"""NLP QA layer — laterality / gender / unit / modality / anatomy checks.

Phase 2 of the post-PACS roadmap. PowerScribe One ships its own QA pass, so
this is parity rather than a moat — but it's table stakes for users on Dragon,
M-Modal, browser-only setups, or anyone who wants belt-and-braces checking.

Design goals:

- **Deterministic first.** Regex + word lists catch the bulk of errors with
  zero LLM cost and zero false-positive risk for clinical content. The LLM
  pass is reserved for things regex can't see (e.g. "the lesion in the right
  lobe extends to the left lobe" — both sides legitimately appear).
- **Flag, never rewrite.** The radiologist always decides.
- **Scoped to the report only.** No per-user state, no DB. The audit log
  records that a check happened (and what was dismissed) but the QA module
  itself is a pure function.
"""
from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Anatomy / gender word lists
# ---------------------------------------------------------------------------

_FEMALE_ONLY_TERMS = (
    "uterus", "uterine", "endometrium", "endometrial",
    "ovary", "ovaries", "ovarian", "fallopian", "adnexa", "adnexal",
    "cervix", "cervical canal", "vaginal", "vagina",
)

_MALE_ONLY_TERMS = (
    "prostate", "prostatic", "seminal vesicle", "seminal vesicles",
    "testis", "testes", "testicle", "testicles", "testicular",
    "epididymis", "scrotum", "scrotal", "vas deferens",
)

# Approximate body-region groupings for modality/anatomy mismatch.
# Each entry is (region, terms that should NOT appear if region is something else).
_REGION_ANATOMY = {
    "knee": (
        "meniscus", "menisci", "medial collateral", "lateral collateral",
        "anterior cruciate", "posterior cruciate", "patella", "patellar",
        "tibial plateau", "femoral condyle",
    ),
    "shoulder": (
        "rotator cuff", "supraspinatus", "infraspinatus", "subscapularis",
        "labrum", "labral", "glenohumeral", "acromioclavicular",
    ),
    "spine": (
        "disc", "discal", "facet joint", "vertebral body", "spinal canal",
        "neural foramen", "neural foramina", "ligamentum flavum",
    ),
    "chest": (
        "lung", "pulmonary", "pleural", "mediastinum", "mediastinal",
        "bronchi", "bronchial", "trachea", "tracheal",
    ),
    "abdomen": (
        "liver", "hepatic", "kidney", "renal", "spleen", "splenic",
        "pancreas", "pancreatic", "bowel", "appendix", "colon",
    ),
    "pelvis": (
        "uterus", "ovary", "ovaries", "prostate", "bladder",
        "rectum", "rectal",
    ),
    "head": (
        "brain", "ventricles", "ventricular", "cerebral", "cerebellar",
        "intracranial", "extra-axial", "intra-axial",
    ),
    "neck": (
        "thyroid", "parotid", "submandibular", "lymph node",
    ),
    "cardiac": (
        "ventricle", "ventricular", "atrium", "atrial", "cardiac chamber",
        "myocardium", "myocardial", "pericardium", "pericardial",
        "valve", "ejection fraction",
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _word_re(term: str) -> re.Pattern:
    """Word-boundary regex for a term, case-insensitive."""
    return re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)


def _find_first(text: str, term: str) -> Optional[int]:
    m = _word_re(term).search(text)
    return m.start() if m else None


def _line_for_offset(text: str, offset: int) -> str:
    """Return the trimmed line of text at the given character offset."""
    if offset is None or offset < 0 or offset >= len(text):
        return ""
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def _flag(
    *,
    type_: str,
    severity: str,
    message: str,
    location: Optional[str] = None,
    suggestion: Optional[str] = None,
) -> dict:
    f = {
        "type": type_,
        "severity": severity,
        "message": message,
    }
    if location:
        f["location"] = location
    if suggestion:
        f["suggestion"] = suggestion
    return f


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_laterality(report_text: str, ordered_side: Optional[str]) -> list[dict]:
    """Flag if the report mentions only the opposite side of what was ordered.

    ordered_side: "left", "right", or "bilateral" (case-insensitive).
    Returns a list of flags (typically 0 or 1 flag).
    """
    if not ordered_side:
        return []
    side = ordered_side.strip().lower()
    if side not in ("left", "right", "bilateral"):
        return []

    has_left = bool(_word_re("left").search(report_text))
    has_right = bool(_word_re("right").search(report_text))

    flags: list[dict] = []
    if side == "left" and has_right and not has_left:
        flags.append(_flag(
            type_="laterality",
            severity="warning",
            message=(
                "Order is for the LEFT side, but the report mentions only the "
                "RIGHT. Confirm laterality."
            ),
        ))
    elif side == "right" and has_left and not has_right:
        flags.append(_flag(
            type_="laterality",
            severity="warning",
            message=(
                "Order is for the RIGHT side, but the report mentions only the "
                "LEFT. Confirm laterality."
            ),
        ))
    elif side == "bilateral" and (has_left ^ has_right):
        flags.append(_flag(
            type_="laterality",
            severity="info",
            message=(
                "Order is BILATERAL, but the report only mentions one side. "
                "Confirm both sides were assessed."
            ),
        ))
    return flags


def check_gender_anatomy(report_text: str, patient_gender: Optional[str]) -> list[dict]:
    """Flag female-only anatomy in male patients (and vice versa)."""
    if not patient_gender:
        return []
    g = patient_gender.strip().lower()[:1]
    if g not in ("m", "f"):
        return []

    flags: list[dict] = []
    if g == "m":
        for term in _FEMALE_ONLY_TERMS:
            offset = _find_first(report_text, term)
            if offset is not None:
                flags.append(_flag(
                    type_="gender_anatomy",
                    severity="error",
                    message=f"'{term}' appears in a MALE patient's report.",
                    location=_line_for_offset(report_text, offset),
                ))
    elif g == "f":
        for term in _MALE_ONLY_TERMS:
            offset = _find_first(report_text, term)
            if offset is not None:
                flags.append(_flag(
                    type_="gender_anatomy",
                    severity="error",
                    message=f"'{term}' appears in a FEMALE patient's report.",
                    location=_line_for_offset(report_text, offset),
                ))
    return flags


def check_modality_anatomy(
    report_text: str, body_part: Optional[str]
) -> list[dict]:
    """Flag anatomy from a different region than the ordered body part.

    Conservative: only flags if the report mentions anatomy from a *different*
    region while NOT mentioning anatomy from the ordered region. This avoids
    false positives when a report legitimately notes incidental adjacent
    findings.
    """
    if not body_part:
        return []
    bp = body_part.strip().lower()
    # Map body_part text → region key
    region: Optional[str] = None
    for key in _REGION_ANATOMY:
        if key in bp:
            region = key
            break
    # Common synonyms
    if region is None:
        if any(t in bp for t in ("ct head", "mr head", "brain", "cerebral", "ct brain", "mri brain")):
            region = "head"
        elif "thorax" in bp or "lungs" in bp:
            region = "chest"
        elif "lumbar" in bp or "thoracic spine" in bp or "cervical spine" in bp or "spine" in bp:
            region = "spine"

    if region is None:
        return []

    own_terms = _REGION_ANATOMY[region]
    has_own = any(_word_re(t).search(report_text) for t in own_terms)
    if has_own:
        return []

    flags: list[dict] = []
    for other_region, other_terms in _REGION_ANATOMY.items():
        if other_region == region:
            continue
        for term in other_terms:
            offset = _find_first(report_text, term)
            if offset is not None:
                flags.append(_flag(
                    type_="modality_anatomy",
                    severity="warning",
                    message=(
                        f"Order body-part is '{body_part}' but the report "
                        f"mentions '{term}' (typical of {other_region})."
                    ),
                    location=_line_for_offset(report_text, offset),
                ))
                # One mismatch per region is enough — keep the panel readable.
                break
    return flags


# Patterns like "3 cm × 12 mm" or "3 cm by 12 mm" — looking for mixed units
# inside what looks like a single measurement triplet.
_MIXED_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(cm|mm)\b[\s×x]*"
    r"(\d+(?:\.\d+)?)\s*(cm|mm)\b"
    r"(?:[\s×x]*(\d+(?:\.\d+)?)\s*(cm|mm)\b)?",
    re.IGNORECASE,
)


def check_unit_drift(report_text: str) -> list[dict]:
    """Flag a single measurement that mixes mm and cm."""
    flags: list[dict] = []
    seen: set[str] = set()
    for m in _MIXED_UNIT_RE.finditer(report_text):
        units = {m.group(2).lower(), m.group(4).lower()}
        if m.group(6):
            units.add(m.group(6).lower())
        if len(units) > 1:
            phrase = m.group(0)
            if phrase in seen:
                continue
            seen.add(phrase)
            flags.append(_flag(
                type_="unit_drift",
                severity="warning",
                message=(
                    f"Measurement mixes units: '{phrase}'. Standardise on mm or cm."
                ),
                location=phrase,
            ))
    return flags


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_qa_checks(
    *,
    report_text: str,
    patient_gender: Optional[str] = None,
    ordered_side: Optional[str] = None,
    body_part: Optional[str] = None,
) -> list[dict]:
    """Run all deterministic QA checks and return a flat list of flags.

    The list is ordered by descending severity (error → warning → info) and
    deduplicated by (type, location).
    """
    if not report_text or not report_text.strip():
        return []

    flags: list[dict] = []
    flags.extend(check_laterality(report_text, ordered_side))
    flags.extend(check_gender_anatomy(report_text, patient_gender))
    flags.extend(check_modality_anatomy(report_text, body_part))
    flags.extend(check_unit_drift(report_text))

    severity_order = {"error": 0, "warning": 1, "info": 2}
    flags.sort(key=lambda f: severity_order.get(f.get("severity", "info"), 3))

    seen = set()
    deduped = []
    for f in flags:
        key = (f.get("type"), f.get("location") or "", f.get("message"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    return deduped
