"""Groq API calls shared by hearing_bot and ai_commit: speech to text, translation and chat."""

import io
import os
import wave

import requests

API_BASE = "https://api.groq.com/openai/v1"
SPEECH_MODEL = "whisper-large-v3-turbo"  # fast speech to text
TRANSLATE_MODEL = "whisper-large-v3"     # the turbo model can't translate
CHAT_MODEL = "openai/gpt-oss-120b"

_session = requests.Session()
# GPT-OSS often writes narrow no-break spaces (U+202F) and similar; paste plain ones instead.
_PLAIN = str.maketrans({"\u00a0": " ", "\u2007": " ", "\u2009": " ", "\u202f": " ", "\u2011": "-"})


def api_key():
    return os.environ.get("GROQ_API_KEY", "")


def _post(path, **kwargs):
    for attempt in (1, 2):  # retry once in case an idle kept-alive connection was dropped
        try:
            resp = _session.post(f"{API_BASE}/{path}", headers={"Authorization": f"Bearer {api_key()}"},
                                 timeout=60, **kwargs)
            break
        except requests.ConnectionError:
            if attempt == 2:
                raise
    if resp.status_code != 200:
        raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def speech_to_text(pcm, sample_rate, language=None, translate=False):
    """Turn 16-bit mono PCM audio into text. With translate=True the text comes back in English."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    form = {"model": TRANSLATE_MODEL if translate else SPEECH_MODEL, "temperature": "0",
            "response_format": "json"}
    if language and not translate:
        form["language"] = language
    endpoint = "audio/translations" if translate else "audio/transcriptions"
    reply = _post(endpoint, data=form, files={"file": ("speech.wav", buf.getvalue(), "audio/wav")})
    return reply.get("text", "")


def chat(instructions, text, max_tokens=4096):
    """Ask the chat model to apply `instructions` to `text` and return its answer."""
    body = {
        "model": CHAT_MODEL,
        "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": text}],
        "reasoning_effort": "low",
        "include_reasoning": False,
        "max_completion_tokens": max_tokens,
    }
    reply = _post("chat/completions", json=body)
    return strip_code_fence(reply["choices"][0]["message"].get("content") or "").translate(_PLAIN)


def strip_code_fence(text):
    """Remove the ``` fence a model sometimes wraps its whole answer in."""
    text = text.strip()
    if text.startswith("```") and text.endswith("```") and "\n" in text:
        text = text[text.index("\n") + 1:-3]
    return text.strip()
