# Inbound AI Voice Agent for Home Service Businesses

A production-ready, fully **self-hosted** inbound voice agent that answers a
business's phone line, qualifies the caller (service type, urgency, location,
contact), and turns the call into an actionable lead — escalating emergencies
to the owner by SMS in real time.

Built for HVAC, roofing, plumbing, electrical, pest control, and garage door
companies. Multi-tenant from day one.

> **Why no Vapi / Bland / Retell / OpenAI Realtime API?**
> This build intentionally avoids all paid voice-orchestration platforms and
> the OpenAI Realtime API so it can run **fully self-funded on free tiers**
> (Deepgram 100 STT hours/mo, Groq + Mistral free LLM tiers, Supabase free
> tier). Twilio is used strictly as raw telephony (Programmable Voice +
> Media Streams). Every layer — STT, reasoning, TTS, orchestration — is code
> you own, which also means no per-minute platform margin when you deploy it
> for a paying client.

---

## Architecture

```
                        ┌──────────────────────────────────────────────────────┐
                        │                    FastAPI (Uvicorn)                 │
  Caller                │                                                      │
    │  PSTN             │  POST /voice ────────► TwiML <Connect><Stream>       │
    ▼                   │  POST /call-status ──► duration bookkeeping          │
┌─────────┐  webhook    │  GET  /health ───────► LLM x3 / DB / Twilio checks   │
│ Twilio  ├────────────►│                                                      │
│ Voice + │  WebSocket  │  WS /media-stream/{call_sid}                         │
│ Media   │◄───────────►│   │                                                  │
│ Streams │  mulaw 8kHz │   │ audio in            audio out (ulaw_8000)        │
└────┬────┘             │   ▼                        ▲                         │
     │ SMS              │ ┌──────────┐          ┌────┴─────────┐               │
     ▼                  │ │ Deepgram │          │ ElevenLabs   │               │
 Owner's phone          │ │ live STT │          │ Flash TTS    │               │
 (emergencies,          │ └────┬─────┘          │ (or Twilio   │               │
  lead summaries)       │      │ final          │  <Say> $0    │               │
                        │      ▼ utterance      │  fallback)   │               │
                        │ ┌─────────────────┐   └──────────────┘               │
                        │ │ LangGraph FSM   │                                  │
                        │ │  greeting       │   LiteLLM fallback chain:        │
                        │ │  service id     │   1. groq/llama-3.3-70b          │
                        │ │  urgency        ├──►2. mistral/mistral-small       │
                        │ │  location       │   3. mistral/open-ministral-3b   │
                        │ │  contact        │   (3s timeout / 429 / 5xx hop;   │
                        │ │  routing ──┐    │    all fail → voicemail TwiML)   │
                        │ └────────────┼────┘                                  │
                        │   ┌──────────┼──────────────┐                        │
                        │   ▼          ▼              ▼                        │
                        │ emergency  lead creation  polite decline             │
                        │ escalation (dedup 60min)  (referral opt.)            │
                        │   └──────────┴──────────────┘                        │
                        │              ▼                                       │
                        │      call summary (always)                           │
                        └──────────────┬────────────────┬──────────────────────┘
                                       ▼                ▼
                            ┌──────────────────┐  ┌───────────────────────┐
                            │ Supabase (PG +   │  │ FSM: Jobber (GraphQL) │
                            │ pgvector): calls,│  │ Housecall Pro (REST)  │
                            │ leads, dedup     │  │ or Generic (Supabase) │
                            └──────────────────┘  └───────────────────────┘
```

### Conversation state machine (LangGraph)

One graph invocation per caller utterance. `route_turn()` is the entry router:
it checks the **emergency keyword intercept first** (`burst pipe`, `flooding`,
`gas leak`, `no heat`, `furnace out`, `no ac`, `no power`, `electrical fire`,
`sparks`, `smoke`) so an emergency spoken at *any* point jumps straight to
`emergency_escalation_node`, regardless of state.

| # | Node | Purpose |
|---|------|---------|
| 1 | `greeting_node` | Professional greeting, ask the caller's name |
| 2 | `service_identification_node` | Classify into 10 service categories |
| 3 | `urgency_assessment_node` | EMERGENCY / SAME_DAY / SCHEDULED |
| 4 | `location_qualification_node` | Address + ZIP vs. configured service area |
| 5 | `contact_collection_node` | Confirm callback number |
| 6 | `routing_decision_node` | Conditional edge → 7 / 8 / 9 |
| 7 | `emergency_escalation_node` | SMS owner NOW + HIGH_PRIORITY FSM job |
| 8 | `lead_creation_node` | Supabase + FSM lead, SMS caller + owner |
| 9 | `polite_decline_node` | Apologize; optional configured referral |
| 10 | `call_summary_node` | **Always** runs last; logs call + transcript |

---

## Quick start

```bash
# 1. Configure
cp .env.example .env        # fill in your keys

# 2. Create the schema (Supabase SQL editor or psql)
#    paste migrations/001_init.sql

# 3. Run
docker compose up --build
#    or locally:
pip install -r requirements.txt
uvicorn main:app --port 8000

# 4. Expose to Twilio (dev)
ngrok http 8000             # put the https URL in PUBLIC_BASE_URL

# 5. Point your Twilio number's Voice webhook to:
#    https://<your-domain>/voice        (HTTP POST)
#    and its status callback to /call-status
```

### Run the tests

```bash
pytest tests/ -v
```

All external services (Twilio REST, Supabase, LiteLLM) are mocked; the Twilio
signature validation runs for real inside the webhook tests.

---

## Production features

1. **Graceful degradation to voicemail** — if the entire LLM chain
   (Groq → Mistral small → Ministral 3B) fails, the call is redirected to a
   recorded-voicemail TwiML instead of dead air.
2. **Conversation timeout** — one re-prompt after 8 s of silence, graceful
   goodbye + hangup after 5 s more (both configurable).
3. **`ENABLE_RECORDING`** — per-deployment dual-channel call recording toggle.
4. **Multi-tenant** — `business_id` on every table, index, log line, and
   config value. One codebase, N businesses.
5. **`GET /health`** — pings all three LLM tiers, Supabase, and Twilio, and
   reports per-dependency `latency_ms` plus the Groq daily-request count.
6. **Twilio signature validation** — `X-Twilio-Signature` verified on every
   webhook; forged requests get a 403.
7. **Structured JSON logs** — every line carries `call_id`, `node`, `action`,
   `latency_ms`, `llm_provider_used`, `business_id`.
8. **Tested webhooks** — happy-path *and* failure-path integration tests for
   each endpoint with Twilio/Supabase/LiteLLM mocked.
9. **Groq daily-usage counter** — Groq's free tier for
   `llama-3.3-70b-versatile` is **30 requests/minute and 1,000 requests/day**
   (numbers quoted elsewhere refer to a smaller Groq model). The counter warns
   in the logs at 80% (800 requests) and skips the Groq tier entirely once the
   cap is spent, so the Mistral fallback path is exercised for real.

## LLM fallback chain

| Tier | Model | Trigger to move on |
|------|-------|--------------------|
| 1 | `groq/llama-3.3-70b-versatile` | >3 s timeout, HTTP 429/5xx, daily cap |
| 2 | `mistral/mistral-small-latest` | >3 s timeout, HTTP 429/5xx |
| 3 | `mistral/open-ministral-3b` | >3 s timeout, HTTP 429/5xx |
| — | all failed | voicemail degradation |

The provider that actually served each turn is logged and stored per call as
`llm_provider_used`.

---

## How this maps to a paid client deployment

This repo is a reference implementation; here is exactly what changes when a
home-service business pays you to run it:

| Free-tier component | Paid deployment upgrade |
|---------------------|-------------------------|
| Deepgram free 100 h/mo | Deepgram pay-as-you-go (~$0.0059/min) — still ~10× cheaper than platform per-minute fees |
| Groq/Mistral free tiers | Groq paid tier (higher rate limits) or a dedicated Mistral endpoint; the LiteLLM chain is unchanged |
| ElevenLabs starter credits | ElevenLabs Creator/Pro, or keep `USE_ELEVENLABS_TTS=false` and ship with Twilio `<Say>` at $0 |
| Supabase free tier | Supabase Pro ($25/mo) — needed once call volume exceeds free-row limits |
| Single Docker container | The same container on Fly.io/Railway/ECS with `BUSINESS_ID` set per tenant; one deployment per client, or one shared deployment with per-tenant config rows |
| `generic` FSM | Flip `FSM_PROVIDER` to `jobber` or `housecallpro` with the client's API credentials — no code changes |

Typical client pricing for this category is $300–800/mo per business + setup.
Total infrastructure cost at moderate volume (~500 calls/mo) is roughly
$30–60/mo, which is the margin argument for self-hosting instead of building
on Vapi/Bland/Retell (whose per-minute fees typically exceed the *entire*
infra bill here).

### Operational notes

* Run **one Uvicorn worker** per deployment: the live-call registry and the
  Groq usage counter are in-process (move both to Redis/Supabase if you ever
  need horizontal scale).
* `migrations/001_init.sql` enables `pgvector` so transcript embeddings /
  semantic search over past calls can be added without another migration.
* All prompts live in `agent/prompts.py` — tune the voice per client without
  touching graph logic.

## File structure

```
├── main.py                     # FastAPI app, webhooks, media-stream WS loop
├── agent/
│   ├── graph.py                # LangGraph wiring
│   ├── nodes.py                # the 10 nodes + routers
│   ├── state.py                # CallState + emergency keywords
│   └── prompts.py              # every prompt/script in one place
├── integrations/
│   ├── twilio_client.py        # SMS + TwiML (recording toggle, <Say> fallback)
│   ├── deepgram_client.py      # streaming STT (mulaw 8k)
│   ├── elevenlabs_client.py    # Flash TTS (ulaw_8000 out)
│   ├── llm_client.py           # LiteLLM chain + Groq daily counter
│   ├── supabase_client.py      # calls / leads / dedup (PostgREST)
│   ├── jobber_client.py        # GraphQL FSM
│   ├── housecallpro_client.py  # REST FSM
│   └── fsm_service.py          # GenericFSMService + provider factory
├── models/                     # pydantic models (call.py, fsm.py)
├── config.py                   # pydantic-settings, all env config
├── middleware.py               # JSON logging + Twilio signature validation
├── migrations/001_init.sql     # calls, leads, duplicate_check (+ pgvector)
├── tests/                      # webhooks, agent graph, LLM fallback
├── Dockerfile / docker-compose.yml
└── .env.example
```
