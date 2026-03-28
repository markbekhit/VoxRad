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

import logging
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import OpenAI
from pydantic import BaseModel

from config.config import config
from llm.fhir_export import save_fhir_report
from llm.format import format_text

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
    # Prefer user templates in the working directory; fall back to bundled ones.
    for d in [
        os.path.join(config.save_directory or "", "templates"),
        _BUNDLED_TEMPLATES_DIR,
    ]:
        if os.path.isdir(d):
            return sorted(f for f in os.listdir(d) if f.endswith((".txt", ".md")))
    return []


def _load_template_content(template_name: str) -> str:
    if not template_name:
        return ""
    for d in [
        os.path.join(config.save_directory or "", "templates"),
        _BUNDLED_TEMPLATES_DIR,
    ]:
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
        },
    )


@app.get("/templates")
def list_templates(username: str = Depends(_verify_auth)):
    return {"templates": _list_templates()}


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

    _prune_sessions()

    tmp_path = None
    try:
        # Save to temp file — suffix preserves format for Whisper
        suffix = ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        # Extract [correct spellings] block from template for ASR prompt
        prompt_spellings = " "
        if template_name:
            content = _load_template_content(template_name)
            match = re.search(
                r"\[correct spellings\](.*?)\[correct spellings\]", content, re.DOTALL
            )
            if match:
                prompt_spellings = match.group(1).strip()

        client = OpenAI(
            api_key=config.TRANSCRIPTION_API_KEY,
            base_url=config.TRANSCRIPTION_BASE_URL,
        )
        with open(tmp_path, "rb") as f:
            result = client.audio.transcriptions.create(
                file=(os.path.basename(tmp_path), f.read()),
                model=config.SELECTED_TRANSCRIPTION_MODEL,
                prompt=prompt_spellings,
                language="en",
                temperature=0.0,
            )

        session_id = _create_session(result.text)
        logger.info("Transcription complete for session %s (%d chars)", session_id, len(result.text))
        return {"transcription": result.text, "session_id": session_id}

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
    patient_id: Optional[str] = None
    accession: Optional[str] = None
    radiologist: Optional[str] = None


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
            report = format_text(req.transcription)
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
