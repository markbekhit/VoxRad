"""Mock OpenAI-compatible routes — mounted when VOXRAD_MOCK_MODE=1.

These routes are added to the main VoxRad FastAPI app at /mock/v1/... so the
app can call itself without any external API keys.  They return canned
responses that exercise the full transcribe → format pipeline.
"""

import json
import time
import uuid

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/mock/v1")

# ---------------------------------------------------------------------------
# /mock/v1/models
# ---------------------------------------------------------------------------

_MODELS = [
    {"id": "gpt-mock", "object": "model", "created": 1700000000, "owned_by": "mock"},
    {"id": "whisper-mock", "object": "model", "created": 1700000000, "owned_by": "mock"},
]


@router.get("/models")
def mock_models():
    return {"object": "list", "data": _MODELS}


# ---------------------------------------------------------------------------
# /mock/v1/audio/transcriptions
# ---------------------------------------------------------------------------

_CANNED_TRANSCRIPTION = (
    "CT chest with contrast. "
    "The lungs are clear. No focal consolidation, pleural effusion, or pneumothorax. "
    "The heart size is normal. The mediastinum is unremarkable. "
    "No axillary, mediastinal, or hilar lymphadenopathy. "
    "Impression: No acute cardiopulmonary abnormality."
)


@router.post("/audio/transcriptions")
async def mock_transcribe(
    file: UploadFile = File(...),
    model: str = Form("whisper-mock"),
    prompt: str = Form(""),
    language: str = Form("en"),
    temperature: float = Form(0.0),
):
    _ = await file.read()
    return {"text": _CANNED_TRANSCRIPTION}


# ---------------------------------------------------------------------------
# /mock/v1/chat/completions
# ---------------------------------------------------------------------------

_CANNED_REPORT = """\
CT CHEST WITH CONTRAST

TECHNIQUE: Axial CT images of the chest were obtained with IV contrast.

CLINICAL DETAILS: Routine evaluation.

COMPARISON: None available.

FINDINGS:

Lungs: Clear bilaterally. No focal consolidation, mass, nodule, or pleural effusion.
       No pneumothorax.

Heart: Normal in size. No pericardial effusion.

Mediastinum: Normal width. No lymphadenopathy.

Chest wall: Unremarkable.

IMPRESSION:
1. No acute cardiopulmonary abnormality.
"""

_TEMPLATE_SELECTION = {"template": "CT_Chest.txt", "guideline": None}


def _wrap(message: dict) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-mock",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


@router.post("/chat/completions")
async def mock_chat(request: Request):
    body = await request.json()
    tools = body.get("tools", [])

    if tools:
        # Step 1: template/guideline selection via tool call
        tool_name = tools[0].get("function", {}).get("name", "select_template_and_guideline")
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(_TEMPLATE_SELECTION),
                    },
                }
            ],
        }
    else:
        # Step 2/3: formatted report
        message = {"role": "assistant", "content": _CANNED_REPORT}

    return JSONResponse(_wrap(message))
