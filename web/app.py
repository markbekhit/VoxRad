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

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.requests import Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import OpenAI
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from config.config import config
from config.settings import save_web_settings
from llm.dicom_sr_export import save_dicom_sr_report
from llm.fhir_export import save_fhir_report
from llm.hl7_export import save_hl7_report
from llm.hl7_import import archive_order, list_inbox
from llm.format import apply_report_feedback, capitalize_after_colon, format_text, stream_format_text
from web.auth_oauth import (
    exchange_google_code,
    exchange_microsoft_code,
    get_or_create_user,
    get_user_style,
    google_auth_url,
    google_enabled,
    init_db,
    microsoft_auth_url,
    microsoft_enabled,
    oauth_enabled,
    require_oauth_user,
    save_user_style,
    SESSION_SECRET_KEY,
    set_session_user,
    clear_session,
)
from web.stt_providers import get_streaming_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="VoxRad Web", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY(), max_age=86400)
# auto_error=False so we can return a redirect (not a 401) when OAuth is active
security = HTTPBasic(auto_error=False)

# Initialise user database on startup (noop if already created)
init_db()

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
# Authentication — OAuth (primary) or HTTP Basic Auth (fallback)
# ---------------------------------------------------------------------------

_DEFAULT_WEB_PASSWORD = "voxrad"


def _verify_auth(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
) -> dict:
    """Unified auth dependency.

    OAuth mode  — reads the session; returns 307 → /login if not signed in.
    Basic Auth mode — validates the shared password; returns 401 if wrong.
    """
    if oauth_enabled():
        return require_oauth_user(request)

    # Basic Auth mode
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
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
    return {"id": None, "email": credentials.username, "name": credentials.username}


def _get_username(user: dict) -> str:
    return user.get("name") or user.get("email") or "user"


def _user_style(user: dict) -> Optional[dict]:
    """Return per-user style dict in OAuth mode; None (→ global config) in Basic Auth mode."""
    if oauth_enabled() and user.get("id") is not None:
        return get_user_style(user["id"])
    return None


def _user_fhir_enabled(user: dict) -> bool:
    """Per-user FHIR export toggle in OAuth mode; global config in Basic Auth mode."""
    if oauth_enabled() and user.get("id") is not None:
        return get_user_style(user["id"]).get("fhir_export_enabled", False)
    return config.fhir_export_enabled


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
    # Pin Plain_Prose.txt to the top so the unstructured option is discoverable
    # without scrolling past the alphabetical list of structured templates.
    ordered = sorted(names)
    for pinned in ("Plain_Prose.txt",):
        if pinned in ordered:
            ordered.remove(pinned)
            ordered.insert(0, pinned)
    return ordered


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


@app.get("/login")
def login_page(request: Request):
    return _jinja.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "google_enabled": google_enabled(),
            "microsoft_enabled": microsoft_enabled(),
            "error": request.query_params.get("error"),
            "static_version": _STATIC_VERSION,
        },
    )


@app.get("/auth/google")
def auth_google(request: Request):
    if not google_enabled():
        raise HTTPException(status_code=404)
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(google_auth_url(state))


@app.get("/auth/google/callback")
def auth_google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/login?error={error}")
    expected = request.session.pop("oauth_state", None)
    if not expected or state != expected:
        return RedirectResponse("/login?error=invalid_state")
    try:
        info = exchange_google_code(code)
    except Exception as exc:
        logger.error("Google OAuth error: %s", exc)
        return RedirectResponse("/login?error=google_auth_failed")
    if not info.get("email"):
        return RedirectResponse("/login?error=no_email")
    db_user = get_or_create_user(info["email"], info["name"], "google")
    set_session_user(request, db_user)
    return RedirectResponse("/")


@app.get("/auth/microsoft")
def auth_microsoft(request: Request):
    if not microsoft_enabled():
        raise HTTPException(status_code=404)
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(microsoft_auth_url(state))


@app.get("/auth/microsoft/callback")
def auth_microsoft_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/login?error={error}")
    expected = request.session.pop("oauth_state", None)
    if not expected or state != expected:
        return RedirectResponse("/login?error=invalid_state")
    try:
        info = exchange_microsoft_code(code)
    except Exception as exc:
        logger.error("Microsoft OAuth error: %s", exc)
        return RedirectResponse("/login?error=microsoft_auth_failed")
    if not info.get("email"):
        return RedirectResponse("/login?error=no_email")
    db_user = get_or_create_user(info["email"], info["name"], "microsoft")
    set_session_user(request, db_user)
    return RedirectResponse("/")


@app.get("/logout")
def logout(request: Request):
    clear_session(request)
    return RedirectResponse("/login")


@app.get("/")
def index(request: Request, user: dict = Depends(_verify_auth)):
    _username = _get_username(user)
    _style = _user_style(user) or {}
    paste_format = _style.get("paste_format", config.style_paste_format) or "rich"
    return _jinja.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "templates": _list_templates(),
            "username": _username,
            "fhir_enabled": _user_fhir_enabled(user),
            "ws_token": _make_ws_token(_username),
            "static_version": _STATIC_VERSION,
            "oauth_mode": oauth_enabled(),
            "paste_format": paste_format,
        },
    )


@app.get("/settings")
def settings_page(request: Request, user: dict = Depends(_verify_auth)):
    return _jinja.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "username": _get_username(user),
            "static_version": _STATIC_VERSION,
        },
    )


@app.get("/templates")
def list_templates(user: dict = Depends(_verify_auth)):
    user_dir = os.path.join(config.save_directory or "", "templates")
    user_names: set[str] = set()
    if os.path.isdir(user_dir):
        user_names = {f for f in os.listdir(user_dir) if f.endswith((".txt", ".md"))}
    return {
        "templates": [
            {"name": t, "is_custom": t in user_names}
            for t in _list_templates()
        ]
    }


_TEMPLATE_NAME_RE = re.compile(r'^[\w\-. ]+\.(txt|md)$')


@app.get("/api/templates/{name}")
def get_template_content(name: str, user: dict = Depends(_verify_auth)):
    if not _TEMPLATE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid template name")
    user_dir = os.path.join(config.save_directory or "", "templates")
    user_path = os.path.join(user_dir, name)
    bundled_path = os.path.join(_BUNDLED_TEMPLATES_DIR, name)
    if os.path.exists(user_path):
        with open(user_path, "r", encoding="utf-8") as f:
            return {"content": f.read(), "is_custom": True}
    if os.path.exists(bundled_path):
        with open(bundled_path, "r", encoding="utf-8") as f:
            return {"content": f.read(), "is_custom": False}
    raise HTTPException(status_code=404, detail="Template not found")


class _TemplateSaveBody(BaseModel):
    content: str


@app.put("/api/templates/{name}")
def save_template_content(name: str, body: _TemplateSaveBody, user: dict = Depends(_verify_auth)):
    if not _TEMPLATE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid template name")
    user_dir = os.path.join(config.save_directory or "", "templates")
    os.makedirs(user_dir, exist_ok=True)
    with open(os.path.join(user_dir, name), "w", encoding="utf-8") as f:
        f.write(body.content)
    return {"ok": True}


@app.delete("/api/templates/{name}")
def delete_template_content(name: str, user: dict = Depends(_verify_auth)):
    if not _TEMPLATE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid template name")
    user_dir = os.path.join(config.save_directory or "", "templates")
    path = os.path.join(user_dir, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No custom version found")
    os.remove(path)
    return {"ok": True}


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


# Valid single-word replacements a radiologist may dictate in voice-edit mode.
# We allow these through the voice-edit hallucination filter; the regular
# dictation filter still rejects them (because standalone they're suspicious).
# Laterality ("right"/"left") and severity/presence short words are the
# most common one-word voice edits, so they must pass through even though
# some (e.g. "right") are in _HALLUCINATIONS for the general filter.
_VOICE_EDIT_ALLOWED_SHORT = {
    # Laterality and orientation — critical for anatomy swaps
    "left", "right", "bilateral", "upper", "lower",
    "anterior", "posterior", "medial", "lateral",
    "superior", "inferior", "proximal", "distal",
    # Presence / absence
    "no", "yes", "none", "absent", "present", "unchanged",
    "normal", "abnormal", "positive", "negative",
    # Severity
    "mild", "moderate", "severe", "minimal", "marked",
    "small", "large", "trace",
    # Character
    "acute", "chronic", "focal", "diffuse",
    "intact", "clear", "stable", "benign", "patent", "occluded",
    # Common punctuated variants the ASR may return
    "mild.", "no.", "yes.", "right.", "left.",
}


def _is_voice_edit_hallucination(text: str, asr_prompt: str = "") -> bool:
    """Hallucination filter for voice-edit mode.

    Same as _is_hallucination but permissive of short valid replacements
    (e.g. "normal", "intact", "no") that a radiologist may genuinely say.
    Still rejects known Whisper garbage phrases, prompt-echo, and repetition
    loops — the ones that fire on silent/noisy audio.
    """
    normalised = text.strip().lower().rstrip(".,!?;: ").strip()
    if normalised in _VOICE_EDIT_ALLOWED_SHORT:
        return False
    if normalised in _HALLUCINATIONS:
        return True
    words = normalised.split()
    # Repetition loop
    if len(words) >= 10:
        seen: set[tuple] = set()
        for i in range(len(words) - 4):
            ngram = tuple(words[i:i+5])
            if ngram in seen:
                return True
            seen.add(ngram)
    # Prompt-echo: Whisper repeats the surrounding-text prompt verbatim on
    # silent audio. Decisive signal — keep this check active.
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


# ---------------------------------------------------------------------------
# Per-user vocabulary — "remember my corrections"
# ---------------------------------------------------------------------------
# When the user voice-edits a highlighted word and the replacement sounds
# like a correction of a misheard word (rather than a content change), we
# suggest adding the new spelling to their vocab. Accepted terms are then
# injected into the Whisper ASR prompt on subsequent dictations, so the
# same mistake is less likely to recur.
#
# Phonetic check is intentionally heuristic — the toast is dismissable, the
# user has the final say.

def _vocab_dir() -> str:
    return os.path.join(config.save_directory or "/tmp", "vocab")


def _vocab_path(user: dict) -> str:
    os.makedirs(_vocab_dir(), exist_ok=True)
    uid = user.get("id")
    # OAuth → per-user file; Basic Auth → one shared file.
    key = f"user_{uid}" if uid is not None else "shared"
    return os.path.join(_vocab_dir(), f"{key}.txt")


def _load_user_vocab(user: dict) -> list:
    path = _vocab_path(user)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


def _add_to_user_vocab(user: dict, term: str) -> bool:
    term = term.strip()
    if not term:
        return False
    existing = _load_user_vocab(user)
    if term.lower() in [t.lower() for t in existing]:
        return False
    path = _vocab_path(user)
    with open(path, "a", encoding="utf-8") as f:
        f.write(term + "\n")
    logger.info("Added vocab term %r for %s", term, _get_username(user))
    return True


def _soundalike(word: str) -> str:
    """Crude phonetic reduction. Not full metaphone, zero deps.

    Collapses homophonic digraphs, drops interior vowels, removes doubles —
    enough to make 'supraspinatus'↔'super spin atrice' look close without
    making 'mild'↔'moderate' look close.
    """
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    w = w.replace("ph", "f").replace("ck", "k").replace("qu", "kw")
    w = w.replace("ch", "k").replace("sh", "s")
    head, tail = w[0], re.sub(r"[aeiouy]", "", w[1:])
    collapsed = head + tail
    out = []
    for ch in collapsed:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def _phonetic_similar(old: str, new: str) -> bool:
    """True when `new` looks like an ASR correction of `old`, not a content edit."""
    import difflib
    a = re.sub(r"[^\w\s]", "", old.strip().lower())
    b = re.sub(r"[^\w\s]", "", new.strip().lower())
    if not a or not b or a == b:
        return False
    if difflib.SequenceMatcher(None, a, b).ratio() >= 0.65:
        return True
    sa, sb = _soundalike(a), _soundalike(b)
    if sa and sb and difflib.SequenceMatcher(None, sa, sb).ratio() >= 0.7:
        return True
    return False


def _should_suggest_vocab(user: dict, old: str, new: str) -> Optional[dict]:
    """Return {old, new} if this edit looks like a correction worth remembering.

    Single-word edits only — multi-word diffs are too error-prone to
    auto-suggest. Skips terms already in the user's vocab.
    """
    a = (old or "").strip()
    b = (new or "").strip()
    if not a or not b:
        return None
    if len(a.split()) != 1 or len(b.split()) != 1:
        return None
    a_key = a.lower().rstrip(".,!?;:")
    b_key = b.lower().rstrip(".,!?;:")
    if a_key == b_key:
        return None
    if not _phonetic_similar(a, b):
        return None
    if b_key in {t.lower().rstrip(".,!?;:") for t in _load_user_vocab(user)}:
        return None
    return {"old": a.rstrip(".,!?;:"), "new": b.rstrip(".,!?;:")}


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
    selected_text: Optional[str] = Form(None),
    user: dict = Depends(_verify_auth),
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
        user_vocab = _load_user_vocab(user)
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
            # Append user-learned vocab, truncating to the 896-char Groq limit.
            if user_vocab:
                addition = ", " + ", ".join(user_vocab)
                asr_prompt = (asr_prompt + addition)[:896]

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
        # Hallucination filtering. Voice-edit mode uses a permissive variant
        # that allows short valid replacements ("normal", "intact", "no") but
        # still rejects known Whisper garbage phrases (e.g. "thank you for
        # watching") that fire on silent or noisy audio.
        is_voice_edit = whisper_prompt is not None
        hallucinated = (
            _is_voice_edit_hallucination(text, asr_prompt)
            if is_voice_edit
            else _is_hallucination(text, asr_prompt)
        )
        if hallucinated:
            logger.debug("Discarded hallucination (voice_edit=%s): %r", is_voice_edit, text)
            return {"transcription": "", "session_id": ""}

        _prune_sessions()
        session_id = _create_session(text)
        logger.info("Transcription complete for session %s (%d chars)", session_id, len(text))

        response = {"transcription": text, "session_id": session_id}
        # Voice-edit correction suggestion: if the replacement sounds like
        # a fix for a misheard word (not a mind-change), flag it so the
        # client can offer to remember the spelling.
        if is_voice_edit and selected_text:
            suggestion = _should_suggest_vocab(user, selected_text, text)
            if suggestion:
                response["suggest_vocab"] = suggestion
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Transcription failed: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail=f"Transcription failed: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


class VocabAddRequest(BaseModel):
    term: str


@app.post("/vocab/add")
def vocab_add(req: VocabAddRequest, user: dict = Depends(_verify_auth)):
    """Append a term to the signed-in user's custom vocabulary."""
    term = (req.term or "").strip()
    if not term:
        raise HTTPException(status_code=400, detail="Empty term.")
    if len(term) > 64:
        raise HTTPException(status_code=400, detail="Term too long (max 64 chars).")
    added = _add_to_user_vocab(user, term)
    return {"added": added, "term": term}


@app.get("/vocab")
def vocab_list(user: dict = Depends(_verify_auth)):
    """Return the signed-in user's custom vocabulary."""
    return {"terms": _load_user_vocab(user)}


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


def _hl7_outbox_dir() -> Optional[str]:
    """Resolve the HL7 outbox directory, falling back to {save_directory}/hl7_outbox."""
    if not config.hl7_export_enabled:
        return None
    if config.hl7_outbox_path:
        return config.hl7_outbox_path
    if config.save_directory:
        return os.path.join(config.save_directory, "hl7_outbox")
    return None


def _hl7_inbox_dir() -> Optional[str]:
    """Resolve the HL7 order inbox directory used by the worklist."""
    if config.hl7_inbox_path:
        return config.hl7_inbox_path
    if config.save_directory:
        return os.path.join(config.save_directory, "hl7_inbox")
    return None


def _sr_outbox_dir() -> Optional[str]:
    """Resolve the DICOM SR outbox directory, or None when export is disabled."""
    if not config.dicom_sr_export_enabled:
        return None
    if config.dicom_sr_outbox_path:
        return config.dicom_sr_outbox_path
    if config.save_directory:
        return os.path.join(config.save_directory, "sr_outbox")
    return None


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
def format_report(req: FormatRequest, user: dict = Depends(_verify_auth)):
    """Format a transcription into a structured radiology report."""
    if _MOCK_MODE:
        logger.info("[mock] Returning canned report")
        return {"report": _MOCK_REPORT, "fhir_saved": False}

    if not config.TEXT_API_KEY:
        raise HTTPException(
            status_code=503, detail="Text model API key not loaded on server."
        )

    _style = _user_style(user)
    with _format_lock:
        old_template = config.global_md_text_content
        config.global_md_text_content = (
            _load_template_content(req.template_name) if req.template_name else ""
        )
        try:
            report = format_text(
                req.transcription,
                patient_context=_patient_context(req),
                style=_style,
            )
        finally:
            config.global_md_text_content = old_template

    if report is None:
        raise HTTPException(status_code=503, detail="Report generation failed.")

    fhir_saved = False
    if _user_fhir_enabled(user):
        path = save_fhir_report(
            report_text=report,
            template_name=req.template_name,
            patient_id=req.patient_id or None,
            accession=req.accession or None,
            radiologist=req.radiologist or None,
        )
        fhir_saved = path is not None

    hl7_saved = False
    _hl7_dir = _hl7_outbox_dir()
    if _hl7_dir:
        try:
            path = save_hl7_report(
                report_text=report,
                outbox_path=_hl7_dir,
                patient_context=_patient_context(req),
                template_name=req.template_name,
                sending_facility=config.hl7_sending_facility or "VOXRAD",
                receiving_facility=config.hl7_receiving_facility or "",
            )
            hl7_saved = path is not None
        except Exception as e:
            logger.warning("HL7 save failed: %s", e)

    sr_saved = False
    _sr_dir = _sr_outbox_dir()
    if _sr_dir:
        try:
            path = save_dicom_sr_report(
                report_text=report,
                outbox_path=_sr_dir,
                patient_context=_patient_context(req),
                template_name=req.template_name,
                institution_name=config.dicom_sr_institution_name or "VOXRAD",
            )
            sr_saved = path is not None
        except Exception as e:
            logger.warning("DICOM SR save failed: %s", e)

    logger.info(
        "Report formatted (%d chars), fhir_saved=%s, hl7_saved=%s, sr_saved=%s",
        len(report), fhir_saved, hl7_saved, sr_saved,
    )
    return {
        "report": report,
        "fhir_saved": fhir_saved,
        "hl7_saved": hl7_saved,
        "sr_saved": sr_saved,
    }


@app.post("/format/stream")
def format_report_stream(req: FormatRequest, user: dict = Depends(_verify_auth)):
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
    _style = _user_style(user)

    def _generate():
        with _format_lock:
            old_template = config.global_md_text_content
            config.global_md_text_content = (
                _load_template_content(req.template_name) if req.template_name else ""
            )
            _template_snapshot = config.global_md_text_content
            config.global_md_text_content = old_template

        with _format_lock:
            config.global_md_text_content = _template_snapshot
        try:
            full_report = ""
            for chunk in stream_format_text(req.transcription, patient_context=_ctx, style=_style):
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

        # Apply post-processing (capitalise after colons) to the full report.
        # Send corrected version so the client can replace the streamed text.
        corrected_report = capitalize_after_colon(full_report)

        fhir_saved = False
        if _user_fhir_enabled(user) and corrected_report:
            try:
                path = save_fhir_report(
                    report_text=corrected_report,
                    template_name=req.template_name,
                    patient_id=req.patient_id or None,
                    accession=req.accession or None,
                    radiologist=req.radiologist or None,
                )
                fhir_saved = path is not None
            except Exception as e:
                logger.warning("FHIR save failed during streaming: %s", e)

        hl7_saved = False
        _hl7_dir = _hl7_outbox_dir()
        if _hl7_dir and corrected_report:
            try:
                path = save_hl7_report(
                    report_text=corrected_report,
                    outbox_path=_hl7_dir,
                    patient_context=_ctx,
                    template_name=req.template_name,
                    sending_facility=config.hl7_sending_facility or "VOXRAD",
                    receiving_facility=config.hl7_receiving_facility or "",
                )
                hl7_saved = path is not None
            except Exception as e:
                logger.warning("HL7 save failed during streaming: %s", e)

        sr_saved = False
        _sr_dir = _sr_outbox_dir()
        if _sr_dir and corrected_report:
            try:
                path = save_dicom_sr_report(
                    report_text=corrected_report,
                    outbox_path=_sr_dir,
                    patient_context=_ctx,
                    template_name=req.template_name,
                    institution_name=config.dicom_sr_institution_name or "VOXRAD",
                )
                sr_saved = path is not None
            except Exception as e:
                logger.warning("DICOM SR save failed during streaming: %s", e)

        yield f'data: {json.dumps({"done": True, "fhir_saved": fhir_saved, "hl7_saved": hl7_saved, "sr_saved": sr_saved, "report": corrected_report})}\n\n'

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Voice-feedback report refinement
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    report: str
    feedback: str
    selected_text: str = ""


@app.post("/format/feedback")
def format_feedback(req: FeedbackRequest, user: dict = Depends(_verify_auth)):
    """Apply radiologist verbal feedback to refine an already-generated report.

    Accepts a full report, a feedback transcription, and an optional selected
    passage.  If selected_text is provided, only that passage is revised.
    Returns {"report": "<corrected markdown>"}.
    """
    if _MOCK_MODE:
        tag = f" [targeted: {req.selected_text[:40]}…]" if req.selected_text else ""
        return {"report": req.report + f"\n\n*[Feedback applied{tag}: {req.feedback}]*"}

    if not config.TEXT_API_KEY:
        raise HTTPException(
            status_code=503, detail="Text model API key not loaded on server."
        )

    try:
        corrected = apply_report_feedback(req.report, req.feedback, req.selected_text)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Report refinement failed: {e}")

    return {"report": corrected}


# ---------------------------------------------------------------------------
# FHIR RIS patient lookup
# ---------------------------------------------------------------------------

@app.get("/patient/{accession}")
async def lookup_patient(accession: str, user: dict = Depends(_verify_auth)):
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
    # Reporting style preferences
    style_spelling: Optional[str] = None
    style_numerals: Optional[str] = None
    style_measurement_unit: Optional[str] = None
    style_measurement_separator: Optional[str] = None
    style_decimal_precision: Optional[int] = None
    style_laterality: Optional[str] = None
    style_impression_style: Optional[str] = None
    style_negation_phrasing: Optional[str] = None
    style_date_format: Optional[str] = None
    style_paste_format: Optional[str] = None
    # HL7 v2.4 ORU^R01 export (global, server-admin controlled)
    hl7_export_enabled: Optional[bool] = None
    hl7_outbox_path: Optional[str] = None
    hl7_sending_facility: Optional[str] = None
    hl7_receiving_facility: Optional[str] = None


_STYLE_ALLOWED = {
    "style_spelling": {"american", "british"},
    "style_numerals": {"roman", "arabic"},
    "style_measurement_unit": {"mm", "cm", "auto"},
    "style_measurement_separator": {"x", "times", "by"},
    "style_decimal_precision": {0, 1, 2},
    "style_laterality": {"full", "abbrev"},
    "style_impression_style": {"bulleted", "numbered", "prose"},
    "style_negation_phrasing": {"no_evidence_of", "no_x_identified", "x_absent"},
    "style_date_format": {"dd_mm_yyyy", "mm_dd_yyyy", "yyyy_mm_dd"},
    "style_paste_format": {"rich", "plain", "markdown"},
}


@app.get("/api/capabilities")
def api_capabilities():
    """Return streaming STT availability — no auth required."""
    provider = get_streaming_provider()
    return {
        "streaming_stt": provider is not None,
        "provider": config.STREAMING_STT_PROVIDER,
    }


@app.get("/api/hl7/worklist")
def api_hl7_worklist(user: dict = Depends(_verify_auth)):
    """Return pending radiology orders parsed from the HL7 inbox directory.

    The inbox is configured via VOXRAD_HL7_INBOX or the [HL7] InboxPath
    setting, and defaults to {save_directory}/hl7_inbox. Each order includes
    an order_id so the client can dismiss it after dictating.
    """
    inbox = _hl7_inbox_dir()
    if not inbox:
        return {"enabled": False, "orders": []}
    try:
        orders = list_inbox(inbox)
    except Exception as e:
        logger.warning("HL7 worklist scan failed: %s", e)
        return {"enabled": True, "orders": [], "error": str(e)}
    return {"enabled": True, "orders": orders, "inbox": inbox}


@app.post("/api/hl7/worklist/{order_id}/archive")
def api_hl7_worklist_archive(order_id: str, user: dict = Depends(_verify_auth)):
    """Move a processed order out of the inbox into the 'processed' subfolder."""
    inbox = _hl7_inbox_dir()
    if not inbox:
        raise HTTPException(status_code=404, detail="HL7 inbox not configured")
    # Reject path traversal — order_id must be a simple filename stem.
    if "/" in order_id or "\\" in order_id or order_id.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid order_id")
    ok = archive_order(inbox, order_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Order not found in inbox")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# MWL bridge agent — accepts pushed DICOM Modality Worklist orders
# ─────────────────────────────────────────────────────────────────────────────
# The clinic's on-prem agent runs C-FIND SCU against their PACS/broker and
# POSTs each parsed order here. We write one JSON file per order into the
# HL7 inbox so the existing worklist UI, filter, and archive flow pick it up
# with no further changes. Shared-secret auth only — not a user session.

_MWL_FIELD_WHITELIST = {
    "patient_name", "patient_dob", "patient_id",
    "accession", "modality", "body_part", "procedure",
    "referring_physician", "scheduled_datetime",
}


@app.post("/api/worklist/push")
def api_worklist_push(
    payload: dict = Body(...),
    x_voxrad_agent_token: str | None = Header(None),
):
    """Accept a batch of MWL orders from an on-prem bridge agent.

    Authentication is via the X-VoxRad-Agent-Token header matching the
    VOXRAD_MWL_AGENT_TOKEN env var. When the token is unset, the endpoint
    is disabled. The agent POSTs `{"orders": [{...}, ...]}` — each order
    uses the same field names as the HL7 worklist output.
    """
    expected = (config.mwl_agent_token or "").strip()
    if not expected:
        raise HTTPException(status_code=404, detail="MWL push not configured")
    if not x_voxrad_agent_token or not secrets.compare_digest(
        x_voxrad_agent_token.strip(), expected
    ):
        raise HTTPException(status_code=401, detail="Invalid agent token")

    inbox = _hl7_inbox_dir()
    if not inbox:
        raise HTTPException(status_code=500, detail="HL7 inbox not configured on server")

    orders = payload.get("orders") if isinstance(payload, dict) else None
    if not isinstance(orders, list):
        raise HTTPException(status_code=400, detail="Expected {'orders': [...]}")

    written = 0
    skipped = 0
    for order in orders:
        if not isinstance(order, dict):
            skipped += 1
            continue
        clean = {k: v for k, v in order.items() if k in _MWL_FIELD_WHITELIST and v}
        if not clean.get("accession") and not clean.get("patient_id"):
            # Need at least one stable identifier to build the filename
            skipped += 1
            continue
        clean["source"] = "mwl"
        # Deterministic order_id so re-pushed orders overwrite rather than
        # duplicating — keyed on accession (preferred) or patient+scheduled.
        key = clean.get("accession") or f"{clean.get('patient_id','')}_{clean.get('scheduled_datetime','')}"
        # Sanitise for filesystem
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80] or "order"
        fname = f"mwl_{safe}.json"
        fpath = os.path.join(inbox, fname)
        try:
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False, indent=2)
            written += 1
        except OSError as e:
            logger.warning("Could not write MWL order %s: %s", fpath, e)
            skipped += 1

    return {"ok": True, "written": written, "skipped": skipped, "inbox": inbox}


@app.get("/api/settings")
def api_get_settings(user: dict = Depends(_verify_auth)):
    """Return current (non-sensitive) configuration state.

    In OAuth mode, style settings are per-user; in Basic Auth mode they come
    from the global config / settings.ini.
    """
    style = _user_style(user)
    if style is None:
        # Basic Auth mode — read from global config
        style = {
            "spelling":              config.style_spelling,
            "numerals":              config.style_numerals,
            "measurement_unit":      config.style_measurement_unit,
            "measurement_separator": config.style_measurement_separator,
            "decimal_precision":     config.style_decimal_precision,
            "laterality":            config.style_laterality,
            "impression_style":      config.style_impression_style,
            "negation_phrasing":     config.style_negation_phrasing,
            "date_format":           config.style_date_format,
            "paste_format":          config.style_paste_format,
            "fhir_export_enabled":   config.fhir_export_enabled,
        }
    return {
        "streaming_stt_provider": config.STREAMING_STT_PROVIDER or "",
        "transcription_base_url": config.TRANSCRIPTION_BASE_URL or "",
        "transcription_model":    config.SELECTED_TRANSCRIPTION_MODEL or "",
        "text_base_url":          config.BASE_URL or "",
        "text_model":             config.SELECTED_MODEL or "",
        "fhir_export_enabled":    style.get("fhir_export_enabled", config.fhir_export_enabled),
        "style":                  {k: v for k, v in style.items() if k != "fhir_export_enabled"},
        "oauth_mode":             oauth_enabled(),
        "hl7": {
            "export_enabled":     config.hl7_export_enabled,
            "outbox_path":        config.hl7_outbox_path or "",
            "sending_facility":   config.hl7_sending_facility or "VOXRAD",
            "receiving_facility": config.hl7_receiving_facility or "",
        },
        "keys": {
            "transcription": bool(config.TRANSCRIPTION_API_KEY),
            "text":          bool(config.TEXT_API_KEY),
            "deepgram":      bool(config.DEEPGRAM_API_KEY),
            "assemblyai":    bool(config.ASSEMBLYAI_API_KEY),
        },
    }


_STYLE_FIELD_MAP = {
    "style_spelling":             "spelling",
    "style_numerals":             "numerals",
    "style_measurement_unit":     "measurement_unit",
    "style_measurement_separator":"measurement_separator",
    "style_decimal_precision":    "decimal_precision",
    "style_laterality":           "laterality",
    "style_impression_style":     "impression_style",
    "style_negation_phrasing":    "negation_phrasing",
    "style_date_format":          "date_format",
    "style_paste_format":         "paste_format",
}


@app.post("/api/settings")
def api_save_settings(req: SettingsRequest, user: dict = Depends(_verify_auth)):
    """Persist non-sensitive settings.

    Global settings (model URLs, streaming provider) → settings.ini.
    Style preferences → per-user SQLite row (OAuth mode) or settings.ini (Basic Auth mode).
    """
    config.STREAMING_STT_PROVIDER = req.streaming_stt_provider or None
    if req.transcription_base_url:
        config.TRANSCRIPTION_BASE_URL = req.transcription_base_url
    if req.transcription_model:
        config.SELECTED_TRANSCRIPTION_MODEL = req.transcription_model
    if req.text_base_url:
        config.BASE_URL = req.text_base_url
    if req.text_model:
        config.SELECTED_MODEL = req.text_model

    # Validate style fields
    style_update: dict = {}
    for req_field, style_key in _STYLE_FIELD_MAP.items():
        val = getattr(req, req_field)
        if val is None:
            continue
        allowed = _STYLE_ALLOWED[req_field]
        if val not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid {req_field}: {val!r} (allowed: {sorted(allowed)})",
            )
        style_update[style_key] = val

    # HL7 settings are always global (server-admin controlled).
    hl7_touched = False
    if req.hl7_export_enabled is not None:
        config.hl7_export_enabled = bool(req.hl7_export_enabled)
        hl7_touched = True
    if req.hl7_outbox_path is not None:
        config.hl7_outbox_path = req.hl7_outbox_path.strip()
        hl7_touched = True
    if req.hl7_sending_facility is not None:
        config.hl7_sending_facility = req.hl7_sending_facility.strip() or "VOXRAD"
        hl7_touched = True
    if req.hl7_receiving_facility is not None:
        config.hl7_receiving_facility = req.hl7_receiving_facility.strip()
        hl7_touched = True

    if oauth_enabled() and user.get("id") is not None:
        # Per-user: merge with existing preferences and save to SQLite
        existing = get_user_style(user["id"])
        existing.update(style_update)
        existing["fhir_export_enabled"] = req.fhir_export_enabled
        save_user_style(user["id"], existing)
        if hl7_touched:
            # Persist the global HL7 change to settings.ini.
            save_web_settings()
    else:
        # Basic Auth / global mode: write to config + settings.ini
        config.fhir_export_enabled = req.fhir_export_enabled
        for style_key, val in style_update.items():
            setattr(config, f"style_{style_key}", val)
        save_web_settings()

    logger.info("Settings saved by %s", _get_username(user))
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

        try:
            await asyncio.wait_for(provider.close(), timeout=4.0)
        except asyncio.TimeoutError:
            logger.warning("provider.close() timed out — forcing teardown")
        provider = None

        full_text = " ".join(finals).strip()
        # Run synchronous LLM call in a thread so it doesn't block the event loop.
        # Cap at 8 s — if the LLM is slow/unavailable, send the raw Deepgram text
        # (which is already high quality from Nova-2 medical) rather than hanging.
        if full_text:
            try:
                corrected = await asyncio.wait_for(
                    asyncio.to_thread(_correct_asr_text, full_text),
                    timeout=8.0,
                )
            except asyncio.TimeoutError:
                logger.warning("_correct_asr_text timed out — using raw text")
                corrected = full_text
        else:
            corrected = ""

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
