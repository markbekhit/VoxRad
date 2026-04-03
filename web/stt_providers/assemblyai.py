import json
import logging
from typing import AsyncIterator, List
from urllib.parse import quote

import websockets
import websockets.exceptions

from .base import StreamingSTTProvider, TranscriptEvent

logger = logging.getLogger(__name__)

_MAX_KEYWORDS = 200


class AssemblyAIProvider(StreamingSTTProvider):
    """AssemblyAI Universal-2 real-time streaming STT provider.

    Uses the AssemblyAI WebSocket streaming API.
    Sends raw linear16 PCM audio as binary; receives JSON transcript events.
    """

    def __init__(self):
        self._ws = None
        self._closed = False

    async def connect(self, api_key: str, sample_rate: int, keywords: List[str]) -> None:
        # AssemblyAI Universal-2 streaming v3 endpoint
        url = (
            f"wss://streaming.assemblyai.com/v3/ws"
            f"?sample_rate_hertz={sample_rate}&encoding=pcm_s16le&speech_model=best"
        )
        if keywords:
            word_boost = ",".join(k for k in keywords[:_MAX_KEYWORDS])
            url += f"&word_boost={quote(word_boost)}&boost_param=high"

        logger.info("[assemblyai] connecting to %s", url[:120])
        self._ws = await websockets.connect(
            url,
            additional_headers={"Authorization": api_key},
        )
        # v3 sends a session_begins message before accepting audio
        session_msg = await self._ws.recv()
        data = json.loads(session_msg)
        msg_type = data.get("type") or data.get("message_type", "")
        if msg_type not in ("session_begins", "SessionBegins"):
            raise RuntimeError(f"Unexpected AssemblyAI session message: {data}")
        logger.info("[assemblyai] session began: %s", data.get("session_id"))

    async def send_audio(self, audio_bytes: bytes) -> None:
        if self._ws and not self._closed:
            try:
                await self._ws.send(audio_bytes)
            except websockets.exceptions.ConnectionClosed:
                pass

    async def receive_results(self) -> AsyncIterator[TranscriptEvent]:
        if not self._ws:
            return
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                data = json.loads(raw)
                # v3 uses "type"; v2 used "message_type" — handle both
                msg_type = data.get("type") or data.get("message_type", "")
                if msg_type not in (
                    "partial_transcript", "final_transcript",   # v3
                    "PartialTranscript", "FinalTranscript",     # v2
                ):
                    continue
                # v3 uses "transcript"; v2 used "text"
                text = (data.get("transcript") or data.get("text") or "").strip()
                if not text:
                    continue
                is_final = msg_type in ("final_transcript", "FinalTranscript")
                confidence = float(data.get("confidence", 1.0))
                yield TranscriptEvent(text=text, is_final=is_final, confidence=confidence)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            logger.warning("[assemblyai] receive_results error: %s", exc)

    async def close(self) -> None:
        self._closed = True
        if self._ws:
            try:
                # v3 termination format
                await self._ws.send(json.dumps({"type": "terminate_session"}))
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
