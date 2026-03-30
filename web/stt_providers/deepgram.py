import json
import logging
from typing import AsyncIterator, List

import websockets
import websockets.exceptions

from .base import StreamingSTTProvider, TranscriptEvent

logger = logging.getLogger(__name__)

# Max keywords Deepgram accepts in the URL
_MAX_KEYWORDS = 100


class DeepgramProvider(StreamingSTTProvider):
    """Deepgram Nova-2 real-time streaming STT provider.

    Uses the Deepgram WebSocket live transcription API.
    Sends raw linear16 PCM audio; receives JSON transcript events.
    """

    def __init__(self):
        self._ws = None
        self._closed = False

    async def connect(self, api_key: str, sample_rate: int, keywords: List[str]) -> None:
        params = (
            f"encoding=linear16&sample_rate={sample_rate}&channels=1"
            f"&punctuate=true&interim_results=true"
            f"&endpointing=800&model=nova-2-medical&smart_format=true"
        )
        # Keyword boosting — each term submitted as ?keywords=term:boost
        if keywords:
            kw_str = "&".join(
                f"keywords={k.replace(' ', '%20')}:2"
                for k in keywords[:_MAX_KEYWORDS]
            )
            params += "&" + kw_str

        url = f"wss://api.deepgram.com/v1/listen?{params}"
        logger.info("[deepgram] connecting to %s", url[:80])
        self._ws = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Token {api_key}"},
        )

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
                if data.get("type") != "Results":
                    continue
                alts = data.get("channel", {}).get("alternatives", [])
                if not alts:
                    continue
                text = alts[0].get("transcript", "").strip()
                if not text:
                    continue
                is_final = bool(data.get("is_final", False))
                confidence = float(alts[0].get("confidence", 1.0))
                yield TranscriptEvent(text=text, is_final=is_final, confidence=confidence)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            logger.warning("[deepgram] receive_results error: %s", exc)

    async def close(self) -> None:
        self._closed = True
        if self._ws:
            try:
                # Signal graceful close to Deepgram
                await self._ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
