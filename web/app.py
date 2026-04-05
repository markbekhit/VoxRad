"""VoxRad Web Server — FastAPI application.

Launch via:
    python VoxRad.py --web [--host 0.0.0.0] [--port 8765]

Authentication: HTTP Basic Auth.
Password is read from the VOXRAD_WEB_PASSWORD environment variable
(default: "voxrad" — change before any non-localhost deployment).

WARNING: HTTP Basic Auth sends credentials in cleartext over plain HTTP.
Always run behind an HTTPS reverse proxy (e.g. nginx with TLS) in
production. See docs/web-server-setup.md.
"""

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.requests import Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import OpenAI
from pydantic import BaseModel

from config.config import config
from config.settings import save_web_settings
from llm.fhir_export import save_fhir_report
from llm.format import format_text, stream_format_text
from web.stt_providers import get_streaming_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="VoxRad Web", docs_url=None, redoc_url=None)
security = HTTPBasic()

_BASE_DIR = os.path.dirname(__file__)
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(_BASE_DIR, "static")),
    name="static",
)
_jinja = Jinja2Templates(directory=os.path.join(_BASE_DIR, "templates"))

# Cache-busting: use the current git commit hash (or a timestamp fallback)
# so browsers always load fresh JS/CSS after each deploy.
try:
    import subprocess
    _STATIC_VERSION = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=os.path.dirname(_BASE_DIR),
        stderr=subprocess.DEVNULL,
    ).decode().strip()
except Exception:
    _STATIC_VERSION = str(int(time.time()))

# ---------------------------------------------------------------------------
# Static version for cache-busting
# ---------------------------------------------------------------------------

try:
    import subprocess as _sp
    _STATIC_VERSION = _sp.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=_BASE_DIR,
        stderr=_sp.DEVNULL,
    ).decode().strip()
except Exception:
    _STATIC_VERSION = str(int(time.time()))

# ---------------------------------------------------------------------------
# Mock mode — activated by VOXRAD_MOCK_MODE env var
# ---------------------------------------------------------------------------

_MOCK_MODE = bool(os.environ.get("VOXRAD_MOCK_MODE"))

_MOCK_TRANSCRIPTION = (
    "There is a 1.2 cm nodule in the right upper lobe. "
    "No pleural effusion. Heart size is normal. "
    "Impression: solitary pulmonary nodule, right upper lobe. "
    "Recommend CT chest with contrast for further evaluation."
)

_MOCK_REPORT = """## CHEST X-RAY

**Clinical History:** Routine screening.

### Findings

- **Lungs:** 1.2 cm nodule right upper lobe. No consolidation or pleural effusion.
- **Heart:** Normal size and contour.
- **Mediastinum:** Unremarkable.
- **Bones:** No acute osseous abnormality.

### Impression

1. Solitary pulmonary nodule, right upper lobe (1.2 cm). Recommend CT chest with contrast.
"""

# ---------------------------------------------------------------------------
# Bundled templates directory (shipped with the app)
# ---------------------------------------------------------------------------

_BUNDLED_TEMPLATES_DIR = os.path.join(
    os.path.dirname(_BASE_DIR), "templates"
)

# ---------------------------------------------------------------------------
# RadLex-derived ASR vocabulary prompt for Whisper-compatible APIs
# ---------------------------------------------------------------------------

_RADIOLOGY_PROMPT = (
    "adenopathy, atelectasis, attenuation, calcification, cardiomegaly, "
    "consolidation, costophrenic, diaphragm, effusion, emphysema, hepatomegaly, "
    "hilar, hydronephrosis, hyperechoic, hypoechoic, infiltrate, interstitial, "
    "mediastinum, nodule, opacity, osseous, parenchyma, pericardial, pleural, "
    "pneumothorax, splenomegaly, subsegmental, thoracic, vertebral, BIRADS, "
    "TIRADS, PIRADS, LIRADS, Fleischner, T1, T2, FLAIR, DWI, ADC, SUV, "
    "Hounsfield, coronal, sagittal, axial, bilateral, ipsilateral, contralateral, "
    "anterolisthesis, spondylosis, stenosis, foraminal, ligamentum flavum, "
    "pneumonia, edema, infarct, hemorrhage, aneurysm, dissection, thrombosis"
)

# ---------------------------------------------------------------------------
# Hallucination filter — common Whisper ghost outputs
# ---------------------------------------------------------------------------

_HALLUCINATIONS: set[str] = {
    "thank you",
    "thank you for watching",
    "thanks for watching",
    "you",
    ".",
    "please subscribe",
    "transcribed by",
    "subtitles by",
    "www.",
    "http",
}


def _is_hallucination(text: str, asr_prompt: str = "") -> bool:
    """Return True if *text* looks like an ASR hallucination to be discarded."""
    t = text.strip().lower()
    if not t or len(t) < 3:
        return True
    for h in _HALLUCINATIONS:
        if t == h or t.startswith(h):
            return True
    # If text is identical to the ASR prompt seed, discard
    if asr_prompt and t == asr_prompt.strip().lower():
        return True
    return False


# ---------------------------------------------------------------------------
# ASR post-correction via text LLM
# ---------------------------------------------------------------------------

_CORRECTION_SYSTEM = (
    "You are a radiology transcription editor. "
    "Correct only obvious speech-to-text errors (wrong homophones, "
    "mis-spelled medical terms, punctuation). "
    "Do NOT add, remove, or reorder clinical content. "
    "Return ONLY the corrected text with no preamble."
)


def _correct_asr_text(raw: str) -> str:
    """Post-correct raw ASR output using the configured text LLM.

    Returns the corrected text, or the original *raw* string on any error.
    """
    if not raw or not config.TEXT_API_KEY:
        return raw
    try:
        client = OpenAI(api_key=config.TEXT_API_KEY, base_url=config.BASE_URL)
        resp = client.chat.completions.create(
            model=config.SELECTED_MODEL,
            messages=[
                {"role": "system", "content": _CORRECTION_SYSTEM},
                {"role": "user", "content": raw},
            ],
            temperature=0.0,
            max_tokens=2048,
        )
        corrected = (resp.choices[0].message.content or "").strip()
        return corrected if corrected else raw
    except Exception as e:
        logger.warning("ASR correction failed, using raw text: %s", e)
        return raw


# ---------------------------------------------------------------------------
# HTTP Basic Auth
# ---------------------------------------------------------------------------

_DEFAULT_WEB_PASSWORD = "voxrad"


def _verify_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Verify HTTP Basic Auth password against VOXRAD_WEB_PASSWORD env var."""
    expected = os.environ.get("VOXRAD_WEB_PASSWORD", _DEFAULT_WEB_PASSWORD)
    ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        expected.encode("utf-8"),
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ---------------------------------------------------------------------------
# Session cache  {session_id: {transcription, expires_at}}
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}
_SESSION_TTL = 1800  # 30 minutes


def _create_session(transcription: str) -> str:
    sid = str(uuid.uuid4())
    _sessions[sid] = {
        "transcription": transcription,
        "expires_at": time.time() + _SESSION_TTL,
    }
    return sid


def _get_session(sid: str) -> Optional[str]:
    s = _sessions.get(sid)
    if s and s["expires_at"] > time.time():
        return s["transcription"]
    _sessions.pop(sid, None)
    return None


def _prune_sessions() -> None:
    """Remove expired sessions (called lazily on each transcription)."""
    now = time.time()
    expired = [k for k, v in _sessions.items() if v["expires_at"] <= now]
    for k in expired:
        del _sessions[k]


# ---------------------------------------------------------------------------
# Thread lock for format_text() — protects config.global_md_text_content
# mutation from concurrent requests.
# ---------------------------------------------------------------------------

_format_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helper: list available templates
# ---------------------------------------------------------------------------

_BUNDLED_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")


def _list_templates() -> list[str]:
    template_dir = os.path.join(config.save_directory or "", "templates")
    names: set[str] = set()
    for d in (template_dir, _BUNDLED_TEMPLATES_DIR):
        if os.path.isdir(d):
            names.update(f for f in os.listdir(d) if f.endswith((".txt", ".md")))
    return sorted(names)


def _load_template_content(template_name: str) -> str:
    if not template_name:
        return ""
    for d in (
        os.path.join(config.save_directory or "", "templates"),
        _BUNDLED_TEMPLATES_DIR,
    ):
        path = os.path.join(d, template_name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    return ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# WebSocket auth helpers
# ---------------------------------------------------------------------------

def _make_ws_token(username: str) -> str:
    """Return a base64-encoded token embedding username:password for WS auth."""
    password = os.environ.get("VOXRAD_WEB_PASSWORD", _DEFAULT_WEB_PASSWORD)
    return base64.b64encode(f"{username}:{password}".encode()).decode()


def _verify_ws_token(token: str) -> bool:
    """Verify a WS auth token produced by _make_ws_token()."""
    try:
        decoded = base64.b64decode(token).decode()
        _, pw = decoded.split(":", 1)
        expected = os.environ.get("VOXRAD_WEB_PASSWORD", _DEFAULT_WEB_PASSWORD)
        return secrets.compare_digest(pw.encode(), expected.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Keyword list builder — used for provider-side vocabulary boosting
# ---------------------------------------------------------------------------

def _build_keyword_list(template_name: Optional[str]) -> list[str]:
    """Return a list of medical terms for STT keyword boosting.

    Prefers the [correct spellings] block from the selected template;
    falls back to extracting terms from _RADIOLOGY_PROMPT.
    """
    text = _RADIOLOGY_PROMPT
    if template_name:
        content = _load_template_content(template_name)
        match = re.search(
            r"\[correct spellings\](.*?)\[correct spellings\]", content, re.DOTALL
        )
        if match:
            text = match.group(1).strip()
    parts = re.split(r"[,\n.]+", text)
    keywords = [p.strip() for p in parts if p.strip() and len(p.strip()) > 2]
    return keywords[:200]


# Mount mock OpenAI-compatible routes when running without real API keys.
if os.environ.get("VOXRAD_MOCK_MODE"):
    from web.mock_routes import router as _mock_router
    app.include_router(_mock_router)
    logger.info("[mock] Mock API routes mounted at /mock/v1/...")


@app.get("/")
def index(request: Request, username: str = Depends(_verify_auth)):
    return _jinja.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "templates": _list_templates(),
            "username": username,
            "fhir_enabled": config.fhir_export_enabled,
            "ws_token": _make_ws_token(username),
            "static_version": _STATIC_VERSION,
        },
    )


@app.get("/settings")
def settings_page(request: Request, username: str = Depends(_verify_auth)):
    return _jinja.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "username": username,
            "static_version": _STATIC_VERSION,
        },
    )


@app.get("/templates")
def list_templates(username: str = Depends(_verify_auth)):
    return {"templates": _list_templates()}


# ---------------------------------------------------------------------------
# Whisper hallucination filter
# ---------------------------------------------------------------------------

# Whisper reliably hallucinates these phrases on silent/noisy audio.
# Normalise to lowercase, strip trailing punctuation before comparing.
_HALLUCINATIONS: set[str] = {
    "", "thank you", "thanks", "thank you so much", "thank you very much",
    "thank you for watching", "thanks for watching", "thank you for listening",
    "thanks for listening", "please like and subscribe", "don't forget to subscribe",
    "okay", "ok", "alright", "right", "sure", "yes", "no", "yep", "nope",
    "bye", "goodbye", "see you", "see you next time", "take care",
    "hello", "hi", "hey", "welcome", "welcome back",
    "you", "hmm", "mm", "mm-hmm", "um", "uh", "ah", "oh",
    "of course", "absolutely", "indeed", "certainly",
    "subtitles by", "subtitles", "captions by", "captions",
    "transcribed by", "translated by",
    "john", "omar", "james", "michael", "david",  # common single-name hallucinations
}


def _is_hallucination(text: str, asr_prompt: str = "") -> bool:
    normalised = text.strip().lower().rstrip(".,!?;: ").strip()
    if normalised in _HALLUCINATIONS:
        return True
    # Single short non-medical word
    words = normalised.split()
    if len(words) == 1 and len(normalised) <= 6 and normalised.isalpha():
        return True
    # Repetition loop: any 5-word ngram appearing more than once
    if len(words) >= 10:
        seen: set[tuple] = set()
        for i in range(len(words) - 4):
            ngram = tuple(words[i:i+5])
            if ngram in seen:
                return True
            seen.add(ngram)
    # Prompt-echo detection: discard if the transcription matches any sentence
    # from the ASR prompt (Whisper completes/repeats prompt on silent audio)
    if asr_prompt:
        text_norm = text.strip().lower().rstrip(".,!?;: ")
        for sentence in re.split(r"[.!?]", asr_prompt):
            s = sentence.strip().lower().rstrip(".,!?;: ")
            if len(s) > 15 and (text_norm == s or text_norm in s or s in text_norm):
                return True
    return False


# ---------------------------------------------------------------------------
# Radiology vocabulary prompt for Whisper
# Two-part design per OpenAI Whisper docs:
#   1. A short context sentence (tells Whisper this is medical dictation)
#   2. A curated vocabulary list of RadLex-derived terms Whisper commonly
#      confuses — comma-separated so Whisper learns their spelling/casing.
# Template-specific [correct spellings] blocks override this entirely.
# ---------------------------------------------------------------------------
_RADIOLOGY_PROMPT = (
    # Context sentence + RadLex-derived vocabulary list (≤ 896 chars for Groq)
    "Radiology dictation. "
    "oedema, meniscus, menisci, supraspinatus, infraspinatus, subscapularis, "
    "chondromalacia, chondral, subchondral, osteochondral, trabecular, "
    "Baker's cyst, Hoffa's fat pad, trochanteric, ACL, PCL, MCL, LCL, MPFL, "
    "Bankart, Hill-Sachs, SLAP, rotator cuff, "
    "effusion, synovitis, tenosynovitis, enthesopathy, bursitis, "
    "consolidation, atelectasis, bronchiectasis, pneumothorax, "
    "pleural effusion, mediastinum, hilar, parenchyma, "
    "hepatomegaly, splenomegaly, cholelithiasis, nephrolithiasis, "
    "lymphadenopathy, herniation, spondylosis, spondylolisthesis, stenosis, "
    "cauda equina, ligamentum flavum, intraosseous, cortical, cancellous, "
    "T1-weighted, T2-weighted, STIR, gradient echo, Hounsfield, "
    "coronal, sagittal, axial."
)

# ---------------------------------------------------------------------------
# LLM post-correction for raw Whisper output
# A fast, tight prompt that fixes medical ASR errors without rephrasing.
# ---------------------------------------------------------------------------
_CORRECTION_SYSTEM = """\
You are a medical transcription corrector for radiology dictation.
Fix ONLY obvious speech recognition errors: misspelled medical terms, \
mangled anatomy, and garbled drug or procedure names.
Do NOT rephrase, reorder, summarise, or add any words not present in the input.
Return the corrected text only — no explanation, no prefix, no punctuation changes \
beyond fixing the erroneous word itself.\
"""


def _correct_asr_text(raw: str) -> str:
    """Pass raw Whisper output through a fast LLM to fix medical terminology errors.

    Uses the text LLM (GPT-4o-mini / configured model). Skips correction if
    the text LLM is not configured or if the text is very short (≤ 3 words).
    """
    if not config.TEXT_API_KEY:
        return raw
    words = raw.split()
    if len(words) <= 3:
        return raw  # too short to bother; unlikely to have complex errors
    try:
        client = OpenAI(api_key=config.TEXT_API_KEY, base_url=config.BASE_URL)
        resp = client.chat.completions.create(
            model=config.SELECTED_MODEL,
            messages=[
                {"role": "system", "content": _CORRECTION_SYSTEM},
                {"role": "user", "content": raw},
            ],
            temperature=0.0,
            max_tokens=len(words) + 30,  # corrected text won't be longer
        )
        corrected = resp.choices[0].message.content.strip()
        # Safety: if the LLM somehow returns nothing or drastically expands the
        # text, fall back to the original Whisper output.
        if not corrected or len(corrected) > len(raw) * 2:
            return raw
        return corrected
    except Exception as exc:
        logger.warning("ASR correction skipped: %s", exc)
        return raw


_MOCK_TRANSCRIPTION = (
    "CT chest with contrast. "
    "The lungs are clear. No focal consolidation, pleural effusion, or pneumothorax. "
    "The heart size is normal. The mediastinum is unremarkable. "
    "No axillary, mediastinal, or hilar lymphadenopathy. "
    "Impression: No acute cardiopulmonary abnormality."
)

_MOCK_REPORT = """\
CT CHEST WITH CONTRAST

TECHNIQUE: Axial CT images of the chest were obtained with IV contrast.

FINDINGS:

Lungs: Clear bilaterally. No focal consolidation, mass, nodule, or pleural effusion.
       No pneumothorax.

Heart: Normal in size. No pericardial effusion.

Mediastinum: Normal width. No lymphadenopathy.

IMPRESSION:
1. No acute cardiopulmonary abnormality.
"""

_MOCK_MODE = bool(os.environ.get("VOXRAD_MOCK_MODE"))


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    template_name: Optional[str] = Form(None),
    whisper_prompt: Optional[str] = Form(None),
    username: str = Depends(_verify_auth),
):
    """Accept a WebM audio blob, transcribe via Whisper-compatible API."""
    if _MOCK_MODE:
        _ = await audio.read()
        _prune_sessions()
        session_id = _create_session(_MOCK_TRANSCRIPTION)
        logger.info("[mock] Returning canned transcription for session %s", session_id)
        return {"transcription": _MOCK_TRANSCRIPTION, "session_id": session_id}

    if not config.TRANSCRIPTION_API_KEY:
        raise HTTPException(
            status_code=503, detail="Transcription API key not loaded on server."
        )

    audio_bytes = await audio.read()
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(audio_bytes) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio exceeds 100 MB limit.")


    tmp_path = None
    try:
        # Save to temp file — suffix preserves format for Whisper
        suffix = ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        # Build ASR prompt.
        # whisper_prompt (from voice-edit mode) overrides everything — it is the
        # surrounding transcript text, which is what Whisper's prompt parameter
        # is actually designed for.  Using the vocabulary list as the prompt for
        # short voice-edit clips causes Whisper to hallucinate completions of it.
        if whisper_prompt is not None:
            asr_prompt = whisper_prompt[:896]
        else:
            asr_prompt = _RADIOLOGY_PROMPT
            if template_name:
                content = _load_template_content(template_name)
                match = re.search(
                    r"\[correct spellings\](.*?)\[correct spellings\]", content, re.DOTALL
                )
                if match:
                    asr_prompt = match.group(1).strip()[:896]  # Groq hard limit

        client = OpenAI(
            api_key=config.TRANSCRIPTION_API_KEY,
            base_url=config.TRANSCRIPTION_BASE_URL,
        )
        with open(tmp_path, "rb") as f:
            result = client.audio.transcriptions.create(
                file=(os.path.basename(tmp_path), f.read()),
                model=config.SELECTED_TRANSCRIPTION_MODEL,
                prompt=asr_prompt,
                language="en",
                temperature=0.0,
            )

        text = result.text.strip()
        if _is_hallucination(text, asr_prompt):
            logger.debug("Discarded hallucination: %r", text)
            return {"transcription": "", "session_id": ""}

        # LLM post-correction: fix medical ASR errors (e.g. "bake assist" →
        # "Baker's cyst") using the text LLM before returning to the client.
        text = _correct_asr_text(text)

        _prune_sessions()
        session_id = _create_session(text)
        logger.info("Transcription complete for session %s (%d chars)", session_id, len(text))
        return {"transcription": text, "session_id": session_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Transcription failed: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail=f"Transcription failed: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


class FormatRequest(BaseModel):
    transcription: str
    template_name: Optional[str] = None
    session_id: Optional[str] = None
    # Patient context fields
    patient_name: Optional[str] = None
    patient_dob: Optional[str] = None
    patient_id: Optional[str] = None
    accession: Optional[str] = None
    modality: Optional[str] = None
    body_part: Optional[str] = None
    referring_physician: Optional[str] = None
    radiologist: Optional[str] = None


def _patient_context(req: "FormatRequest") -> Optional[dict]:
    """Build a patient context dict from a FormatRequest, returning None if all fields empty."""
    ctx = {k: v for k, v in {
        "patient_name": req.patient_name,
        "patient_dob": req.patient_dob,
        "patient_id": req.patient_id,
        "accession": req.accession,
        "modality": req.modality,
        "body_part": req.body_part,
        "referring_physician": req.referring_physician,
        "radiologist": req.radiologist,
    }.items() if v}
    return ctx or None


@app.post("/format")
def format_report(req: FormatRequest, username: str = Depends(_verify_auth)):
    """Format a transcription into a structured radiology report."""
    if _MOCK_MODE:
        logger.info("[mock] Returning canned report")
        return {"report": _MOCK_REPORT, "fhir_saved": False}

    if not config.TEXT_API_KEY:
        raise HTTPException(
            status_code=503, detail="Text model API key not loaded on server."
        )

    # Acquire lock to safely mutate config.global_md_text_content
    with _format_lock:
        old_template = config.global_md_text_content
        config.global_md_text_content = (
            _load_template_content(req.template_name) if req.template_name else ""
        )
        try:
            report = format_text(req.transcription, patient_context=_patient_context(req))
        finally:
            config.global_md_text_content = old_template

    if report is None:
        raise HTTPException(status_code=503, detail="Report generation failed.")

    fhir_saved = False
    if config.fhir_export_enabled:
        path = save_fhir_report(
            report_text=report,
            template_name=req.template_name,
            patient_id=req.patient_id or None,
            accession=req.accession or None,
            radiologist=req.radiologist or None,
        )
        fhir_saved = path is not None

    logger.info("Report formatted (%d chars), fhir_saved=%s", len(report), fhir_saved)
    return {"report": report, "fhir_saved": fhir_saved}


@app.post("/format/stream")
def format_report_stream(req: FormatRequest, username: str = Depends(_verify_auth)):
    """Stream a structured radiology report as Server-Sent Events.

    Each SSE event is one of:
      data: {"token": "..."}       — a text chunk from the LLM
      data: {"done": true, "fhir_saved": bool}  — signals completion
      data: {"error": "..."}       — an error occurred
    """
    if _MOCK_MODE:
        def _mock_stream():
            import time
            for word in _MOCK_REPORT.split(" "):
                yield f'data: {{"token": {json.dumps(word + " ")}}}\n\n'
                time.sleep(0.03)
            yield 'data: {"done": true, "fhir_saved": false}\n\n'
        return StreamingResponse(_mock_stream(), media_type="text/event-stream")

    if not config.TEXT_API_KEY:
        def _err():
            yield 'data: {"error": "Text model API key not loaded on server."}\n\n'
        return StreamingResponse(_err(), media_type="text/event-stream")

    _ctx = _patient_context(req)

    def _generate():
        # We hold the config lock only while pulling template content, then release
        # it so the generator can stream without blocking other requests.
        with _format_lock:
            old_template = config.global_md_text_content
            config.global_md_text_content = (
                _load_template_content(req.template_name) if req.template_name else ""
            )
            # Snapshot the template content so we can release the lock
            _template_snapshot = config.global_md_text_content
            config.global_md_text_content = old_template

        # Temporarily set the template for this call using a thread-local approach:
        # inject directly then restore after iteration completes.
        with _format_lock:
            config.global_md_text_content = _template_snapshot
        try:
            full_report = ""
            for chunk in stream_format_text(req.transcription, patient_context=_ctx):
                if chunk:
                    full_report += chunk
                    yield f'data: {json.dumps({"token": chunk})}\n\n'
        except Exception as e:
            logger.error("Streaming format error: %s", e, exc_info=True)
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
            return
        finally:
            with _format_lock:
                config.global_md_text_content = old_template

        fhir_saved = False
        if config.fhir_export_enabled and full_report:
            try:
                path = save_fhir_report(
                    report_text=full_report,
                    template_name=req.template_name,
                    patient_id=req.patient_id or None,
                    accession=req.accession or None,
                    radiologist=req.radiologist or None,
                )
                fhir_saved = path is not None
            except Exception as e:
                logger.warning("FHIR save failed during streaming: %s", e)

        yield f'data: {json.dumps({"done": True, "fhir_saved": fhir_saved})}\n\n'

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# FHIR RIS patient lookup
# ---------------------------------------------------------------------------

@app.get("/patient/{accession}")
async def lookup_patient(accession: str, username: str = Depends(_verify_auth)):
    """Look up patient data from a configured FHIR R4 server by accession number.

    Requires FHIR_BASE_URL env var (e.g. https://fhir.hospital.org/r4).
    Optional FHIR_BEARER_TOKEN env var for Bearer auth.

    Returns a JSON object with any of:
      patient_name, patient_dob, patient_id, accession,
      modality, body_part, referring_physician
    """
    import httpx

    fhir_base = os.environ.get("FHIR_BASE_URL", "").rstrip("/")
    if not fhir_base:
        raise HTTPException(
            status_code=503,
            detail="FHIR_BASE_URL is not configured on this server.",
        )

    headers: dict = {"Accept": "application/fhir+json"}
    token = os.environ.get("FHIR_BEARER_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Search ImagingStudy by identifier, requesting inline Patient
            resp = await client.get(
                f"{fhir_base}/ImagingStudy",
                params={"identifier": accession, "_include": "ImagingStudy:patient"},
                headers=headers,
            )
            resp.raise_for_status()
            bundle = resp.json()

        result: dict = {"accession": accession}
        patient_ref: Optional[str] = None

        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            rt = resource.get("resourceType", "")

            if rt == "ImagingStudy":
                series = resource.get("series", [])
                if series:
                    s = series[0]
                    mod = s.get("modality", {})
                    if mod:
                        result["modality"] = mod.get("code", "")
                    site = s.get("bodySite", {})
                    if site:
                        result["body_part"] = site.get("display", "")
                subject = resource.get("subject", {})
                patient_ref = subject.get("reference") or patient_ref

            elif rt == "Patient":
                _extract_patient_fields(resource, result)

        # If patient wasn't bundled via _include, fetch separately
        if patient_ref and "patient_name" not in result:
            url = patient_ref if patient_ref.startswith("http") else f"{fhir_base}/{patient_ref}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                p_resp = await client.get(url, headers=headers)
            if p_resp.status_code == 200:
                _extract_patient_fields(p_resp.json(), result)

        if len(result) <= 1:  # only "accession" key — nothing found
            raise HTTPException(
                status_code=404,
                detail=f"No imaging study found for accession: {accession}",
            )

        return result

    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Accession not found: {accession}")
        raise HTTPException(status_code=503, detail=f"FHIR server error: {exc.response.status_code}")
    except Exception as exc:
        logger.error("FHIR lookup failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"FHIR lookup failed: {exc}")


# ---------------------------------------------------------------------------
# Settings API
# ---------------------------------------------------------------------------

class SettingsRequest(BaseModel):
    streaming_stt_provider: Optional[str] = None
    transcription_base_url: Optional[str] = None
    transcription_model: Optional[str] = None
    text_base_url: Optional[str] = None
    text_model: Optional[str] = None
    fhir_export_enabled: bool = False


@app.get("/api/capabilities")
def api_capabilities():
    """Return streaming STT availability — no auth required."""
    provider = get_streaming_provider()
    return {
        "streaming_stt": provider is not None,
        "provider": config.STREAMING_STT_PROVIDER,
    }


@app.get("/api/settings")
def api_get_settings(username: str = Depends(_verify_auth)):
    """Return current (non-sensitive) configuration state."""
    return {
        "streaming_stt_provider": config.STREAMING_STT_PROVIDER or "",
        "transcription_base_url": config.TRANSCRIPTION_BASE_URL or "",
        "transcription_model": config.SELECTED_TRANSCRIPTION_MODEL or "",
        "text_base_url": config.BASE_URL or "",
        "text_model": config.SELECTED_MODEL or "",
        "fhir_export_enabled": config.fhir_export_enabled,
        "keys": {
            "transcription": bool(config.TRANSCRIPTION_API_KEY),
            "text": bool(config.TEXT_API_KEY),
            "deepgram": bool(config.DEEPGRAM_API_KEY),
            "assemblyai": bool(config.ASSEMBLYAI_API_KEY),
        },
    }


@app.post("/api/settings")
def api_save_settings(req: SettingsRequest, username: str = Depends(_verify_auth)):
    """Persist non-sensitive settings to settings.ini."""
    config.STREAMING_STT_PROVIDER = req.streaming_stt_provider or None
    if req.transcription_base_url:
        config.TRANSCRIPTION_BASE_URL = req.transcription_base_url
    if req.transcription_model:
        config.SELECTED_TRANSCRIPTION_MODEL = req.transcription_model
    if req.text_base_url:
        config.BASE_URL = req.text_base_url
    if req.text_model:
        config.SELECTED_MODEL = req.text_model
    config.fhir_export_enabled = req.fhir_export_enabled
    save_web_settings()
    logger.info("Settings saved by %s", username)
    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket: real-time streaming STT proxy
# ---------------------------------------------------------------------------

@app.websocket("/ws/transcribe")
async def ws_transcribe(websocket: WebSocket, token: str = ""):
    """Proxy PCM audio from browser to Deepgram/AssemblyAI; stream transcripts back.

    Protocol:
      1. Client connects with ?token=<base64(user:pass)>
      2. Client sends JSON: {"template_name": "..."}
      3. Client sends binary frames: raw PCM 16kHz mono int16
      4. Server sends JSON: {"type":"interim","text":"..."} or {"type":"final","text":"..."}
      5. Client sends JSON: {"type":"stop"} when done recording
      6. Server sends JSON: {"type":"session_complete","transcription":"...","session_id":"..."}
    """
    if not _verify_ws_token(token):
        await websocket.close(code=4001)
        return

    await websocket.accept()
    provider = None
    try:
        # Step 1: receive config message
        config_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
        template_name = config_msg.get("template_name")
        keywords = _build_keyword_list(template_name)

        provider = get_streaming_provider()
        if not provider:
            await websocket.send_json({
                "type": "error",
                "message": "No streaming STT provider configured. Configure one in Settings.",
            })
            return

        provider_name = (config.STREAMING_STT_PROVIDER or "").lower()
        if provider_name == "deepgram":
            api_key = config.DEEPGRAM_API_KEY or ""
        elif provider_name == "assemblyai":
            api_key = config.ASSEMBLYAI_API_KEY or ""
        else:
            api_key = ""

        await provider.connect(api_key, 16000, keywords)

        finals: list[str] = []
        stop_event = asyncio.Event()

        async def receive_loop():
            """Read from browser: binary frames are audio, text frames are control."""
            try:
                while not stop_event.is_set():
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        stop_event.set()
                        break
                    raw_bytes = msg.get("bytes")
                    if raw_bytes:
                        await provider.send_audio(raw_bytes)
                    raw_text = msg.get("text")
                    if raw_text:
                        try:
                            ctrl = json.loads(raw_text)
                            if ctrl.get("type") == "stop":
                                stop_event.set()
                        except (json.JSONDecodeError, KeyError):
                            pass
            except Exception:
                stop_event.set()

        async def results_loop():
            """Forward transcript events from provider to browser."""
            try:
                async for event in provider.receive_results():
                    if stop_event.is_set():
                        break
                    if not _is_hallucination(event.text):
                        # Strip em dash artifacts (STT inserts — during pauses)
                        clean = re.sub(r'\s*—\s*', ' ', event.text).strip()
                        if not clean:
                            continue
                        if event.is_final:
                            finals.append(clean)
                            await websocket.send_json({"type": "final", "text": clean})
                        else:
                            await websocket.send_json({"type": "interim", "text": clean})
            except Exception as e:
                logger.warning("Results loop error: %s", e)
            finally:
                stop_event.set()

        receive_task = asyncio.create_task(receive_loop())
        results_task = asyncio.create_task(results_loop())

        await stop_event.wait()

        receive_task.cancel()
        results_task.cancel()
        await asyncio.gather(receive_task, results_task, return_exceptions=True)

        await provider.close()
        provider = None

        full_text = " ".join(finals).strip()
        corrected = _correct_asr_text(full_text) if full_text else ""

        _prune_sessions()
        session_id = _create_session(corrected) if corrected else ""

        await websocket.send_json({
            "type": "session_complete",
            "transcription": corrected,
            "session_id": session_id,
        })

    except WebSocketDisconnect:
        logger.info("WS client disconnected during streaming")
    except Exception as e:
        logger.error("WS transcribe error: %s", e, exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if provider:
            try:
                await provider.close()
            except Exception:
                pass


def _extract_patient_fields(resource: dict, result: dict) -> None:
    """Pull name, DOB, and MRN from a FHIR Patient resource into result dict."""
    names = resource.get("name", [])
    if names:
        n = names[0]
        given = " ".join(n.get("given", []))
        family = n.get("family", "")
        full = f"{given} {family}".strip()
        if full:
            result["patient_name"] = full
    dob = resource.get("birthDate", "")
    if dob:
        result["patient_dob"] = dob
    for ident in resource.get("identifier", []):
        code = (ident.get("type", {}).get("coding") or [{}])[0].get("code", "")
        if code == "MR":
            result["patient_id"] = ident.get("value", "")
            break
