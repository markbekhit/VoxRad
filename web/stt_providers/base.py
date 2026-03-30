from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, List


@dataclass
class TranscriptEvent:
    text: str
    is_final: bool
    confidence: float = 1.0


class StreamingSTTProvider(ABC):
    """Abstract base class for real-time streaming STT providers."""

    @abstractmethod
    async def connect(self, api_key: str, sample_rate: int, keywords: List[str]) -> None:
        """Open a WebSocket connection to the provider."""

    @abstractmethod
    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send a chunk of raw PCM audio bytes to the provider."""

    @abstractmethod
    async def receive_results(self) -> AsyncIterator[TranscriptEvent]:
        """Yield TranscriptEvent objects as the provider produces them."""

    @abstractmethod
    async def close(self) -> None:
        """Signal end-of-stream and close the provider connection."""
