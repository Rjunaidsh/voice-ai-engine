import asyncio
import logging
import time
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CallState(Enum):
    IDLE         = "idle"
    LISTENING    = "listening"
    PROCESSING   = "processing"   # Claude is thinking
    SPEAKING     = "speaking"     # TTS is playing
    INTERRUPTED  = "interrupted"


class CallManager:
    """
    Manages state for a single active call.
    One instance per WebSocket connection.
    """

    def __init__(self, call_id: str, system_prompt: str):
        self.call_id        = call_id
        self.state          = CallState.IDLE
        self.history        = []          # Claude conversation history
        self.system_prompt  = system_prompt

        # Audio buffers
        self.audio_out_queue = asyncio.Queue()   # Chunks to send to caller
        self.transcript_buf  = []                # Words accumulating in current turn

        # Interruption control
        self.tts_task: Optional[asyncio.Task] = None
        self.llm_task: Optional[asyncio.Task] = None

        # Timing
        self.last_speech_time = 0.0
        self.call_start_time  = time.time()

        # Booking data collected during call
        self.caller_phone = ""
        self.booking_data = {}

        logger.info("CallManager created for call_id=%s", call_id)

    # ─── State helpers ────────────────────────────────────────────────────────

    def set_state(self, state: CallState):
        logger.info("[%s] State: %s → %s", self.call_id, self.state.value, state.value)
        self.state = state

    def is_speaking(self) -> bool:
        return self.state == CallState.SPEAKING

    def is_processing(self) -> bool:
        return self.state == CallState.PROCESSING

    # ─── Interruption ─────────────────────────────────────────────────────────

    async def interrupt(self):
        """Stop TTS and LLM immediately when caller starts speaking."""
        if self.state in (CallState.SPEAKING, CallState.PROCESSING):
            logger.info("[%s] INTERRUPTED by caller", self.call_id)
            self.set_state(CallState.INTERRUPTED)

            # Cancel ongoing tasks
            if self.tts_task and not self.tts_task.done():
                self.tts_task.cancel()
            if self.llm_task and not self.llm_task.done():
                self.llm_task.cancel()

            # Flush audio queue
            while not self.audio_out_queue.empty():
                try:
                    self.audio_out_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Signal caller audio to stop (send silence)
            await self.audio_out_queue.put(b"STOP")

    # ─── History management ───────────────────────────────────────────────────

    def add_user_message(self, text: str):
        self.history.append({"role": "user", "content": text})
        logger.info("[%s] User: %s", self.call_id, text)

    def add_assistant_message(self, text: str):
        self.history.append({"role": "assistant", "content": text})
        logger.info("[%s] Assistant: %s", self.call_id, text)

    # ─── Transcript buffer ────────────────────────────────────────────────────

    def add_transcript_word(self, word: str):
        self.transcript_buf.append(word)
        self.last_speech_time = time.time()

    def flush_transcript(self) -> str:
        text = " ".join(self.transcript_buf).strip()
        self.transcript_buf = []
        return text

    def has_pending_speech(self) -> bool:
        return len(self.transcript_buf) > 0

    def silence_duration_ms(self) -> float:
        if not self.last_speech_time:
            return 0
        return (time.time() - self.last_speech_time) * 1000
