import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime
from typing import Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from config import (
    BANDWIDTH_USERNAME, BANDWIDTH_PASSWORD, BANDWIDTH_APP_ID,
    YOUR_PHONE_NUMBER, PUBLIC_URL, SILENCE_THRESHOLD_MS, MIN_SPEECH_MS
)
from call_manager  import CallManager, CallState
from deepgram_stt  import DeepgramSTT
from claude_llm    import get_claude_response
from cartesia_tts  import CartesiaTTS
from webhook_handler import parse_booking_signal, process_booking

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Voice AI Engine")

# Active calls: call_id → CallManager
active_calls: Dict[str, CallManager] = {}

# ─── System prompt ────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    return f"""You are an AI voice receptionist for The Barbershop, powered by Claude.
You speak exactly like a warm, efficient human receptionist on the phone.

SERVICES: Haircut $25, Beard Trim $15, Shave $20, Hair Color $60
HOURS: Monday to Saturday 9am to 5pm
TODAY: {datetime.now().strftime('%A, %B %d, %Y')}

YOUR GOAL: Get service, date, time, name and email. Then book immediately.

ONE-SHOT: If caller gives multiple details at once, only ask for what's missing.
Example: "haircut tomorrow 3pm" → ask name → ask email → book.

EMAIL: Ask them to spell letter by letter. "r j u n a i d at gmail" = rjunaid@gmail.com
Always confirm the email back before booking: "Is that rjunaid@gmail.com?"

STYLE:
- One or two short sentences max
- Sound human: "Perfect!", "Got it!", "Sounds good!"
- Never use bullet points or lists
- Keep responses SHORT — this is a phone call

BOOKING — when you have all 5 pieces (service, date, time, name, email):
Say confirmation naturally then output:
BOOK_APPOINTMENT:{{"service":"haircut","date":"2026-03-07","time":"15:00","name":"Muhammad","email":"muhammad@gmail.com"}}
END_CALL

TRANSFER: If caller wants human say: TRANSFER_TO_AGENT"""


# ─── Bandwidth webhook — incoming call ────────────────────────────────────────

@app.post("/bandwidth/incoming")
async def bandwidth_incoming(request: Request):
    """
    Bandwidth calls this when a new inbound call arrives.
    We respond with BXML to connect the call to our media stream.
    """
    body     = await request.json()
    call_id  = body.get("callId", str(uuid.uuid4()))
    caller   = body.get("from", "unknown")

    logger.info("Inbound call: call_id=%s from=%s", call_id, caller)

    # Create call manager
    manager              = CallManager(call_id, build_system_prompt())
    manager.caller_phone = caller
    active_calls[call_id] = manager

    # BXML response — connects audio to our WebSocket
    ws_url = f"{PUBLIC_URL.replace('https', 'wss').replace('http', 'ws')}/media/{call_id}"

    bxml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <StartStream name="audio_stream" destination="{ws_url}" mode="duplex" />
    <Pause duration="3600" />
</Response>"""

    return PlainTextResponse(content=bxml, media_type="application/xml")


@app.post("/bandwidth/status")
async def bandwidth_status(request: Request):
    """Handle Bandwidth call status callbacks."""
    body    = await request.json()
    call_id = body.get("callId", "")
    status  = body.get("eventType", "")
    logger.info("Call status: call_id=%s status=%s", call_id, status)

    if status in ("disconnect", "hangup") and call_id in active_calls:
        del active_calls[call_id]

    return JSONResponse({"status": "ok"})


# ─── Media WebSocket ──────────────────────────────────────────────────────────

@app.websocket("/media/{call_id}")
async def media_websocket(websocket: WebSocket, call_id: str):
    """
    Main WebSocket handler for real-time audio streaming.
    Bandwidth sends audio here, we process and send back TTS audio.
    """
    await websocket.accept()
    logger.info("Media WebSocket connected: call_id=%s", call_id)

    # Get or create call manager
    if call_id not in active_calls:
        active_calls[call_id] = CallManager(call_id, build_system_prompt())
    manager = active_calls[call_id]
    manager.set_state(CallState.LISTENING)

    # Initialize TTS
    tts = CartesiaTTS()
    await tts.connect()

    # Silence detection task
    silence_task = None

    async def on_interim_transcript(text: str):
        """Fires as words stream in — use for interruption detection."""
        if manager.is_speaking() or manager.is_processing():
            logger.info("Caller interrupted with: '%s'", text)
            await manager.interrupt()

    async def on_final_transcript(text: str):
        """Fires when Deepgram detects end of utterance."""
        nonlocal silence_task

        if not text or len(text) < 2:
            return

        logger.info("Final transcript: '%s'", text)
        manager.set_state(CallState.PROCESSING)

        # Add to history and get Claude response
        manager.add_user_message(text)

        sentence_queue = asyncio.Queue()

        async def on_sentence(sentence: str):
            await sentence_queue.put(sentence)

        async def run_llm():
            full_response = await get_claude_response(
                manager.history,
                manager.system_prompt,
                on_sentence=on_sentence,
            )
            await sentence_queue.put(None)  # Signal done
            manager.add_assistant_message(full_response)
            return full_response

        async def run_tts():
            context_id = str(uuid.uuid4())
            manager.set_state(CallState.SPEAKING)

            while True:
                sentence = await sentence_queue.get()
                if sentence is None:
                    break

                # Stream TTS sentence to caller
                try:
                    async for audio_chunk in tts.synthesize(sentence, context_id):
                        if audio_chunk == b"STOP":
                            return
                        # Send audio back to Bandwidth
                        await websocket.send_bytes(audio_chunk)
                except asyncio.CancelledError:
                    break

            manager.set_state(CallState.LISTENING)

        # Run LLM and TTS concurrently
        manager.llm_task = asyncio.create_task(run_llm())
        manager.tts_task = asyncio.create_task(run_tts())

        try:
            full_response = await manager.llm_task
            await manager.tts_task

            # Check for booking signal
            if "BOOK_APPOINTMENT:" in full_response:
                booking_data = parse_booking_signal(full_response)
                if booking_data:
                    confirmation = await process_booking(booking_data, manager.caller_phone)
                    manager.add_assistant_message(confirmation)
                    # Speak the confirmation
                    context_id = str(uuid.uuid4())
                    async for audio_chunk in tts.synthesize(confirmation, context_id):
                        await websocket.send_bytes(audio_chunk)

            # End call if signalled
            if "END_CALL" in full_response:
                logger.info("Ending call: %s", call_id)
                await asyncio.sleep(2)
                await websocket.close()

            # Transfer if requested
            if "TRANSFER_TO_AGENT" in full_response:
                logger.info("Transfer requested for call: %s", call_id)
                # TODO: Implement transfer via Bandwidth API

        except asyncio.CancelledError:
            logger.info("LLM/TTS cancelled due to interruption")
            manager.set_state(CallState.LISTENING)

    # Initialize Deepgram STT
    stt = DeepgramSTT(
        on_interim=on_interim_transcript,
        on_final=on_final_transcript,
    )
    await stt.start()

    # Send greeting
    async def send_greeting():
        greeting = "Thank you for calling The Barbershop! How can I help you today?"
        manager.add_assistant_message(greeting)
        context_id = str(uuid.uuid4())
        async for audio_chunk in tts.synthesize(greeting, context_id):
            await websocket.send_bytes(audio_chunk)
        manager.set_state(CallState.LISTENING)

    asyncio.create_task(send_greeting())

    # Main audio receive loop
    try:
        async for message in websocket.iter_bytes():
            # Bandwidth sends mulaw audio — forward to Deepgram
            await stt.send_audio(message)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: call_id=%s", call_id)
    except Exception as e:
        logger.error("WebSocket error: %s", e)
    finally:
        await stt.stop()
        await tts.disconnect()
        if call_id in active_calls:
            del active_calls[call_id]
        logger.info("Call cleaned up: call_id=%s", call_id)


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "active_calls": len(active_calls)}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    from config import SERVER_HOST, SERVER_PORT
    uvicorn.run("main:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)
