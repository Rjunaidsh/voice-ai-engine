import asyncio
import json
import logging
import urllib.request
from typing import AsyncGenerator, List

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)


async def stream_claude(
    history: List[dict],
    system_prompt: str,
) -> AsyncGenerator[str, None]:
    """
    Stream Claude responses token by token.
    Yields text chunks as they arrive.
    """
    payload = {
        "model":      CLAUDE_MODEL,
        "max_tokens": 300,
        "stream":     True,
        "system":     system_prompt,
        "messages":   history,
    }

    def _do_request():
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=30)

    loop   = asyncio.get_event_loop()
    resp   = await loop.run_in_executor(None, _do_request)
    buffer = b""

    try:
        while True:
            chunk = await loop.run_in_executor(None, resp.read, 256)
            if not chunk:
                break
            buffer += chunk

            # SSE stream: each event is "data: {...}\n\n"
            while b"\n\n" in buffer:
                line, buffer = buffer.split(b"\n\n", 1)
                line = line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    return
                try:
                    event = json.loads(data_str)
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield delta.get("text", "")
                except json.JSONDecodeError:
                    continue
    finally:
        resp.close()


def split_into_sentences(text: str):
    """Split text into sentences for TTS chunking."""
    import re
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sentences if s.strip()]


async def get_claude_response(
    history: List[dict],
    system_prompt: str,
    on_sentence: callable = None,
) -> str:
    """
    Stream Claude and fire on_sentence callback after each complete sentence.
    This lets TTS start playing before Claude finishes the full response.
    Returns the complete response text.
    """
    full_text    = ""
    sentence_buf = ""

    async for token in stream_claude(history, system_prompt):
        full_text    += token
        sentence_buf += token

        # Check if we have a complete sentence to send to TTS
        if on_sentence and any(c in sentence_buf for c in ".!?,"):
            # Find the last sentence boundary
            last_boundary = max(
                sentence_buf.rfind("."),
                sentence_buf.rfind("!"),
                sentence_buf.rfind("?"),
            )
            if last_boundary > 0:
                sentence = sentence_buf[:last_boundary + 1].strip()
                remainder = sentence_buf[last_boundary + 1:].strip()

                # Don't send very short fragments
                if len(sentence) > 10:
                    await on_sentence(sentence)
                    sentence_buf = remainder

    # Send any remaining text
    if on_sentence and sentence_buf.strip() and len(sentence_buf.strip()) > 3:
        await on_sentence(sentence_buf.strip())

    return full_text
