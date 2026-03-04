import asyncio
import json
import logging
import audioop
import websockets
from typing import Callable, Awaitable

from config import DEEPGRAM_API_KEY, DEEPGRAM_SAMPLE_RATE

logger = logging.getLogger(__name__)

DEEPGRAM_URL = (
    f"wss://api.deepgram.com/v1/listen"
    f"?model=nova-2-phonecall"          # nova-2-phonecall optimised for 8kHz phone audio
    f"&encoding=linear16"
    f"&sample_rate={DEEPGRAM_SAMPLE_RATE}"
    f"&channels=1"
    f"&punctuate=true"
    f"&interim_results=true"            # Get words as they come in
    f"&endpointing=500"                 # 500ms silence = end of utterance
    f"&utterance_end_ms=1000"
    f"&smart_format=true"              # Formats numbers, emails etc
    f"&no_delay=true"
)


class DeepgramSTT:
    """
    Streams audio to Deepgram and fires callbacks on transcription events.
    """

    def __init__(
        self,
        on_interim: Callable[[str], Awaitable[None]],    # Words streaming in
        on_final:   Callable[[str], Awaitable[None]],    # Complete utterance
    ):
        self.on_interim   = on_interim
        self.on_final     = on_final
        self.ws           = None
        self.audio_queue  = asyncio.Queue()
        self._running     = False
        self._send_task   = None
        self._recv_task   = None

    async def start(self):
        logger.info("Connecting to Deepgram...")
        self.ws = await websockets.connect(
            DEEPGRAM_URL,
            extra_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
            ping_interval=10,
            ping_timeout=20,
        )
        self._running   = True
        self._send_task = asyncio.create_task(self._send_audio())
        self._recv_task = asyncio.create_task(self._recv_transcripts())
        logger.info("Deepgram connected")

    async def stop(self):
        self._running = False
        if self.ws:
            try:
                await self.ws.send(json.dumps({"type": "CloseStream"}))
                await self.ws.close()
            except Exception:
                pass
        for task in (self._send_task, self._recv_task):
            if task and not task.done():
                task.cancel()

    async def send_audio(self, mulaw_chunk: bytes):
        """
        Accept mulaw 8kHz audio from Bandwidth and convert to PCM 16kHz for Deepgram.
        """
        try:
            # mulaw → PCM 16-bit
            pcm_8k = audioop.ulaw2lin(mulaw_chunk, 2)
            # Upsample 8kHz → 16kHz
            pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
            await self.audio_queue.put(pcm_16k)
        except Exception as e:
            logger.warning("Audio conversion error: %s", e)

    async def _send_audio(self):
        while self._running:
            try:
                chunk = await asyncio.wait_for(self.audio_queue.get(), timeout=1.0)
                if self.ws and self.ws.open:
                    await self.ws.send(chunk)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("Deepgram send error: %s", e)
                break

    async def _recv_transcripts(self):
        while self._running:
            try:
                msg  = await asyncio.wait_for(self.ws.recv(), timeout=5.0)
                data = json.loads(msg)
                await self._handle_message(data)
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Deepgram connection closed")
                break
            except Exception as e:
                logger.error("Deepgram recv error: %s", e)
                break

    async def _handle_message(self, data: dict):
        msg_type = data.get("type")

        if msg_type == "Results":
            channel     = data.get("channel", {})
            alternatives = channel.get("alternatives", [{}])
            transcript  = alternatives[0].get("transcript", "").strip()
            is_final    = data.get("is_final", False)
            speech_final = data.get("speech_final", False)

            if not transcript:
                return

            if speech_final or is_final:
                # Complete utterance — fire final callback
                logger.info("Deepgram FINAL: '%s'", transcript)
                await self.on_final(transcript)
            else:
                # Streaming words — fire interim callback
                await self.on_interim(transcript)

        elif msg_type == "UtteranceEnd":
            logger.debug("Deepgram UtteranceEnd")

        elif msg_type == "Metadata":
            logger.debug("Deepgram metadata: %s", data)

        elif msg_type == "Error":
            logger.error("Deepgram error: %s", data)
