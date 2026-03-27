"""Minimal mock OpenAI-compatible server for local VoxRad testing.

Implements just enough of the OpenAI API to exercise the full VoxRad web UI
flow without any real API keys:

  - POST /v1/audio/transcriptions  → returns a canned transcription
  - POST /v1/chat/completions      → returns a canned radiology report

Usage:
    python tests/mock_openai_server.py          # starts on port 11434 by default
    python tests/mock_openai_server.py --port 9999

Then in .env (or export before starting VoxRad):
    VOXRAD_TRANSCRIPTION_API_KEY=mock
    VOXRAD_TRANSCRIPTION_BASE_URL=http://localhost:11434/v1
    VOXRAD_TEXT_API_KEY=mock
    VOXRAD_TEXT_BASE_URL=http://localhost:11434/v1

    # In Settings → Transcription Model, set model to: whisper-mock
    # In Settings → Text Model, set model to: gpt-mock
"""

import argparse
import json
import time
import uuid

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock OpenAI Server")

# ---------------------------------------------------------------------------
# Models endpoint — lets VoxRad "discover" available models
# ---------------------------------------------------------------------------

MODELS = [
    {"id": "gpt-mock", "object": "model", "created": 1700000000, "owned_by": "mock"},
    {"id": "whisper-mock", "object": "model", "created": 1700000000, "owned_by": "mock"},
]


@app.get("/v1/models")
def list_models():
    return {"object": "list", "data": MODELS}


# ---------------------------------------------------------------------------
# Transcription endpoint
# ---------------------------------------------------------------------------

CANNED_TRANSCRIPTION = (
    "CT chest with contrast. "
    "The lungs are clear. No focal consolidation, pleural effusion, or pneumothorax. "
    "The heart size is normal. The mediastinum is unremarkable. "
    "No axillary, mediastinal, or hilar lymphadenopathy. "
    "Impression: No acute cardiopulmonary abnormality."
)


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form("whisper-mock"),
    prompt: str = Form(""),
    language: str = Form("en"),
    temperature: float = Form(0.0),
):
    _ = await file.read()  # consume the upload
    return {"text": CANNED_TRANSCRIPTION}


# ---------------------------------------------------------------------------
# Chat completions endpoint (supports function/tool calling)
# ---------------------------------------------------------------------------

# Minimal template selector response — matches what format.py expects from step 1
TEMPLATE_SELECTION = {
    "template": "CT_Chest.txt",
    "guideline": None,
}

# Canned formatted report — returned for step 2 and step 3
CANNED_REPORT = """\
CT CHEST WITH CONTRAST

TECHNIQUE: Axial CT images of the chest were obtained with IV contrast.

CLINICAL DETAILS: Routine evaluation.

COMPARISON: None available.

FINDINGS:

Lungs: The lungs are clear bilaterally. No focal consolidation, mass, or nodule.
       No pleural effusion. No pneumothorax.

Heart: Normal in size. No pericardial effusion.

Mediastinum: The mediastinum is of normal width. No lymphadenopathy.

Chest wall: Unremarkable.

IMPRESSION:
1. No acute cardiopulmonary abnormality.
"""


def _make_message(content: str | None = None, tool_calls: list | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _wrap_response(message: dict) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-mock",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "stop" if not message.get("tool_calls") else "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    tools = body.get("tools", [])
    messages = body.get("messages", [])

    # If tools are provided, this is step 1 (template/guideline selection).
    # Return a tool call with canned template selection.
    if tools:
        tool = tools[0]
        tool_name = tool.get("function", {}).get("name", "select_template_and_guideline")
        tool_call = {
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(TEMPLATE_SELECTION),
            },
        }
        return JSONResponse(_wrap_response(_make_message(tool_calls=[tool_call])))

    # Step 2 or 3 — return the canned report as plain text
    return JSONResponse(_wrap_response(_make_message(content=CANNED_REPORT)))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock OpenAI server for VoxRad testing")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11434)
    args = parser.parse_args()

    print(f"Mock OpenAI server running at http://{args.host}:{args.port}/v1")
    print()
    print("Set these env vars before starting VoxRad:")
    print(f"  VOXRAD_TRANSCRIPTION_API_KEY=mock")
    print(f"  VOXRAD_TRANSCRIPTION_BASE_URL=http://{args.host}:{args.port}/v1")
    print(f"  VOXRAD_TEXT_API_KEY=mock")
    print(f"  VOXRAD_TEXT_BASE_URL=http://{args.host}:{args.port}/v1")
    print()
    print("In VoxRad Settings:")
    print("  Transcription model: whisper-mock")
    print("  Text model:          gpt-mock")
    print()

    uvicorn.run(app, host=args.host, port=args.port)
