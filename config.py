import os

# ─── Telephony ───────────────────────────────────────────────────────────────
BANDWIDTH_ACCOUNT_ID  = os.environ.get("BANDWIDTH_ACCOUNT_ID", "")
BANDWIDTH_USERNAME    = os.environ.get("BANDWIDTH_USERNAME", "")
BANDWIDTH_PASSWORD    = os.environ.get("BANDWIDTH_PASSWORD", "")
BANDWIDTH_APP_ID      = os.environ.get("BANDWIDTH_APP_ID", "")
YOUR_PHONE_NUMBER     = os.environ.get("YOUR_PHONE_NUMBER", "")   # e.g. +12345678900

# ─── STT ─────────────────────────────────────────────────────────────────────
DEEPGRAM_API_KEY      = os.environ.get("DEEPGRAM_API_KEY", "")

# ─── LLM ─────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL          = "claude-haiku-4-5-20251001"

# ─── TTS ─────────────────────────────────────────────────────────────────────
CARTESIA_API_KEY      = os.environ.get("CARTESIA_API_KEY", "")
CARTESIA_VOICE_ID     = os.environ.get("CARTESIA_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091")  # Default: Barbershop receptionist voice
CARTESIA_MODEL        = "sonic-2"

# ─── Server ───────────────────────────────────────────────────────────────────
SERVER_HOST           = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT           = int(os.environ.get("SERVER_PORT", "8000"))
PUBLIC_URL            = os.environ.get("PUBLIC_URL", "")          # e.g. https://your-lightsail-ip

# ─── Audio ────────────────────────────────────────────────────────────────────
# Bandwidth streams mulaw 8kHz — we convert to PCM 16kHz for Deepgram
INBOUND_SAMPLE_RATE   = 8000
INBOUND_ENCODING      = "mulaw"
DEEPGRAM_SAMPLE_RATE  = 16000
CARTESIA_SAMPLE_RATE  = 8000   # Send back at 8kHz mulaw for Bandwidth

# ─── Tuning ───────────────────────────────────────────────────────────────────
SILENCE_THRESHOLD_MS  = 700    # How long silence before we assume caller stopped
MIN_SPEECH_MS         = 200    # Ignore blips shorter than this
INTERRUPT_THRESHOLD   = 0.3    # Seconds of caller speech to trigger interruption
