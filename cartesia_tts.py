import asyncio
import json
import logging
import base64
import websockets
from typing import AsyncGenerator

from config import CARTESIA_API_KEY, CARTESIA_VOICE_ID, CARTESIA_MODEL

logger = logging.getLogger(__name__)

CARTESIA_WS_URL = "wss://api.cartesia.ai/tts/websocket?cartesia_version=2024-06-10&api_key=" + CARTESIA_API_KEY


class CartesiaTTS:
    """
    Streams text to Cartesia and yields mulaw audio chunks back.
    Maintains a persistent WebSocket connection per call for low latency.
    """

    def __init__(self):
        self.ws       = None
        self._running = False

    async def connect(self):
        logger.info("Connecting to Cartesia...")
        self.ws = await websockets.connect(
            CARTESIA_WS_URL,
            ping_interval=10,
        )
        self._running = True
        logger.info("Cartesia connected")

    async def disconnect(self):
        self._running = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass

    async def synthesize(self, text: str, context_id: str) -> AsyncGenerator[bytes, None]:
        """
        Send text to Cartesia, yield raw mulaw audio chunks.
        Uses context_id to allow interruption of ongoing synthesis.
        """
        if not self.ws or not self.ws.open:
            await self.connect()

        # Strip any system signals from text before speaking
        clean_text = text
        for signal in ["BOOK_APPOINTMENT:", "END_CALL", "TRANSFER_TO_AGENT"]:
            if signal in clean_text:
                clean_text = clean_text.split(signal)[0].strip()

        if not clean_text:
            return

        # Send TTS request
        request = {
            "context_id": context_id,
            "model_id":   CARTESIA_MODEL,
            "voice": {
                "mode": "id",
                "id":   CARTESIA_VOICE_ID,
            },
            "output_format": {
                "container":   "raw",
                "encoding":    "pcm_mulaw",
                "sample_rate": 8000,          # Bandwidth expects 8kHz mulaw
            },
            "transcript": clean_text,
            "continue":   False,
        }

        try:
            await self.ws.send(json.dumps(request))

            # Receive audio chunks
            while True:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=10.0)

                if isinstance(msg, bytes):
                    yield msg
                    continue

                # JSON control message
                data = json.loads(msg)

                if data.get("type") == "chunk":
                    audio_b64 = data.get("data", "")
                    if audio_b64:
                        yield base64.b64decode(audio_b64)

                elif data.get("type") == "done":
                    break

                elif data.get("type") == "error":
                    logger.error("Cartesia error: %s", data)
                    break

        except asyncio.TimeoutError:
            logger.warning("Cartesia timeout for context_id=%s", context_id)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Cartesia connection closed mid-stream")
            self.ws = None
        except asyncio.CancelledError:
            # Interrupted — cancel this synthesis context
            logger.info("Cartesia synthesis cancelled for context_id=%s", context_id)
            await self._cancel_context(context_id)
            raise

    async def _cancel_context(self, context_id: str):
        """Tell Cartesia to stop generating for this context."""
        if self.ws and self.ws.open:
            try:
                await self.ws.send(json.dumps({
                    "context_id": context_id,
                    "cancel":     True,
                }))
            except Exception:
                pass
