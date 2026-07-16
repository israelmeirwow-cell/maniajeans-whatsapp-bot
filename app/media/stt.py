"""Speech-to-Text — transcribe a customer's voice note (Hebrew).

Backends (first available wins):
  1. OpenAI Whisper API — if OPENAI_API_KEY set + `openai` installed (fast, accurate).
  2. Local Whisper       — the open-source `whisper` model on CPU (no API key).

WhatsApp voice notes are OGG/Opus; ffmpeg (required by whisper) handles the decoding.
"""
import os
from typing import Optional

_local_model = None
_WHISPER_SIZE = os.environ.get("WHISPER_MODEL", "small")   # base|small|medium — small≈good Hebrew


def transcribe(audio_path: str, language: str = "he") -> str:
    # 1) OpenAI API if configured
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            client = OpenAI()
            with open(audio_path, "rb") as f:
                r = client.audio.transcriptions.create(model="whisper-1", file=f, language=language)
            return (r.text or "").strip()
        except Exception as e:
            print(f"[stt] OpenAI failed, trying local: {type(e).__name__}")

    # 2) local whisper
    global _local_model
    import whisper
    if _local_model is None:
        _local_model = whisper.load_model(_WHISPER_SIZE)
    result = _local_model.transcribe(audio_path, language=language, fp16=False)
    return (result.get("text") or "").strip()


def available() -> bool:
    if os.environ.get("OPENAI_API_KEY"):
        return True
    try:
        import whisper  # noqa: F401
        return True
    except Exception:
        return False
