# LeadPilot AI Voice Agent

Inbound AI voice agent for home-service lead qualification.

LeadPilot AI answers a business phone line, qualifies the caller, detects emergencies, creates or routes the lead, and logs the call outcome. It is built as a self-hosted FastAPI service using Twilio Media Streams, Deepgram streaming transcription, LangGraph, LiteLLM, Supabase, and optional FSM integrations.

## Live Demo

- Live page: `https://leadpilotai.sohaib.systems/`
- Health check: `https://leadpilotai.sohaib.systems/health`
- Repository: `https://github.com/HafizMuhammadSohaibUmar/LeadPilotAI`

Budget testing note: if the Twilio account is on trial, only verified caller numbers can complete live calls. The application still runs the real Twilio webhook and media-stream flow; unrestricted public calling requires upgrading Twilio.

## LeadPilot AI Agent Suite

This repository is the first service in a connected home-service AI automation suite.

| # | Agent | Purpose | Status | Link |
| --- | --- | --- | --- | --- |
| 1 | LeadPilot AI Voice Agent | Answers inbound calls, qualifies leads, escalates emergencies, and logs outcomes. | Live | [Repo](https://github.com/HafizMuhammadSohaibUmar/LeadPilotAI) |
| 2 | Missed Call Text-Back Agent | Sends fast SMS replies after missed calls and qualifies the conversation into a lead. | Planned | Repo to be published |
| 3 | Outbound Follow-Up Agent | Runs estimate, no-show, re-engagement, and seasonal follow-up campaigns. | Planned | Repo to be published |
| 4 | AI Review Request Agent | Sends review or feedback requests after completed jobs based on sentiment routing. | Planned | Repo to be published |
| 5 | Web Chat Lead Qualifier | Embeddable RAG chat widget for contractor websites. | Planned | Repo to be published |

Each agent is designed to be independently runnable, with its own README, `DECISIONS.md`, tests, Docker deployment, and demo path. Shared ideas are reused, but each service stays deployable on its own.

## What This Agent Does

- Answers an inbound Twilio call.
- Opens a bidirectional Media Stream to FastAPI.
- Sends raw mulaw 8 kHz audio to Deepgram.
- Receives final caller utterances.
- Runs one LangGraph turn per utterance.
- Extracts caller name, service type, urgency, address/ZIP, and callback number.
- Detects emergency keywords from any state.
- Creates a lead, escalates an emergency, skips duplicates, or declines out-of-area callers.
- Persists call summary and transcript to Supabase.
- Sends SMS notifications through Twilio.
- Uses ElevenLabs TTS when enabled, otherwise falls back to Twilio `<Say>`.

## Architecture

```text
Caller
  |
  v
Twilio Voice
  |
  | POST /voice
  v
FastAPI returns TwiML <Connect><Stream>
  |
  | WS /media-stream/{call_sid}
  v
Deepgram streaming STT
  |
  v
LangGraph qualification flow
  |
  +--> LiteLLM: Groq primary, Mistral fallbacks
  +--> Supabase: calls, leads, duplicate checks
  +--> Twilio SMS: owner and caller notifications
  +--> FSM: Jobber, Housecall Pro, or generic local lead
  |
  v
ElevenLabs TTS or Twilio <Say> fallback
```

## Conversation Flow

1. Greeting and caller name
2. Service identification
3. Urgency assessment
4. Location qualification
5. Callback number confirmation
6. Routing decision
7. Lead creation, emergency escalation, duplicate handling, or out-of-area decline
8. Call summary persistence

Emergency keywords are checked before every graph turn. If the caller says something like "gas leak," "burst pipe," "sparks," or "smoke," the graph jumps directly to emergency escalation.

## API Surface

| Route | Purpose |
| --- | --- |
| `GET /` | Human-facing demo page |
| `POST /voice` | Twilio inbound voice webhook |
| `WS /media-stream/{call_sid}` | Bidirectional Twilio audio stream |
| `POST /call-status` | Twilio call status callback |
| `GET /health` | LLM, Supabase, Twilio, and usage health |
| `GET /DECISIONS.md` | Architecture decision record |

## Tech Stack

- FastAPI and Uvicorn
- Twilio Voice, Media Streams, SMS, and RequestValidator
- Deepgram streaming STT
- LangGraph
- LiteLLM with Groq and Mistral fallback
- ElevenLabs TTS with Twilio `<Say>` fallback
- Supabase PostgREST
- Pydantic Settings
- Pytest and pytest-asyncio
- Docker and Docker Compose

## Production Features

- Twilio webhook signature validation
- Structured JSON logs with call ID, graph node, action, latency, provider, and business ID
- LLM fallback chain with daily Groq usage guard
- Voicemail degradation if all LLM providers fail
- TTS fallback that survives Twilio `<Say>` reconnects
- Silence prompt and graceful hangup timers
- Duplicate lead suppression by phone number
- Multi-tenant `business_id` across config, rows, and logs
- Health check across Twilio, Supabase, and all LLM tiers
- Single-worker deployment boundary documented clearly

## Local Setup

```bash
cp .env.example .env
pip install -r requirements.txt
uvicorn main:app --port 8000
```

Expose the app for local Twilio testing:

```bash
ngrok http 8000
```

Then set:

```env
PUBLIC_BASE_URL=https://your-ngrok-domain
```

Configure Twilio:

- Voice webhook: `https://your-domain/voice`
- Status callback: `https://your-domain/call-status`

## Database Setup

Run the migration in Supabase SQL Editor:

```text
migrations/001_init.sql
```

It creates:

- `calls`
- `leads`
- `duplicate_check`

The migration also enables `pgvector` so transcript search can be added later without another extension migration.

## Important Environment Variables

```env
PUBLIC_BASE_URL=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
OWNER_PHONE_NUMBER=
DEEPGRAM_API_KEY=
GROQ_API_KEY=
MISTRAL_API_KEY=
SUPABASE_URL=
SUPABASE_KEY=
USE_ELEVENLABS_TTS=false
SILENCE_PROMPT_SECONDS=10
SILENCE_HANGUP_SECONDS=7
```

ElevenLabs API TTS can return `402 Payment Required` when the account has no API balance. For budget deployments, keep `USE_ELEVENLABS_TTS=false` and use Twilio `<Say>`.

## Tests

```bash
pytest tests/ -v
```

The tests mock external APIs while exercising:

- LangGraph routing and emergency intercepts
- Twilio webhook signature validation
- LLM fallback behavior
- Health endpoint behavior
- Duplicate and out-of-area paths

## Deployment

Run as one container and one Uvicorn worker:

```bash
docker compose up --build -d
```

One-worker note: active call sessions and the Groq usage counter are currently in memory. Move those to Redis or Supabase before horizontal scaling.

## Current Demo Limitations

- Twilio trial accounts only allow calls and SMS with verified numbers.
- ElevenLabs API speech requires available account balance; Twilio `<Say>` is the budget fallback.
- The generic FSM provider stores leads in Supabase and sends owner SMS instead of creating a real Jobber or Housecall Pro job.
- Horizontal scaling needs external shared state for active calls.
