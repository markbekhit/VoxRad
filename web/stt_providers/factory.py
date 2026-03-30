import logging
from typing import Optional

from config.config import config
from .base import StreamingSTTProvider

logger = logging.getLogger(__name__)


def get_streaming_provider() -> Optional[StreamingSTTProvider]:
    """Return the configured streaming STT provider instance, or None.

    Returns None when:
    - No provider is selected (STREAMING_STT_PROVIDER is empty/None)
    - The required API key for the selected provider is not configured
    """
    provider = (config.STREAMING_STT_PROVIDER or "").strip().lower()

    if provider == "deepgram":
        if not config.DEEPGRAM_API_KEY:
            logger.warning("[factory] deepgram selected but DEEPGRAM_API_KEY not set")
            return None
        from .deepgram import DeepgramProvider
        return DeepgramProvider()

    if provider == "assemblyai":
        if not config.ASSEMBLYAI_API_KEY:
            logger.warning("[factory] assemblyai selected but ASSEMBLYAI_API_KEY not set")
            return None
        from .assemblyai import AssemblyAIProvider
        return AssemblyAIProvider()

    return None  # No streaming provider → use segment-based Groq Whisper fallback
