# LeadPilot AI Decisions

This document records the main architecture decisions behind the LeadPilot AI Voice Agent and the wider LeadPilot AI agent suite.

## 1. Product Boundary

Decision: keep this repository focused on inbound voice lead qualification.

Why:

- Inbound calls are the highest-urgency workflow for home-service businesses.
- Voice qualification exercises the hardest parts of the stack: telephony, streaming STT, LLM routing, TTS, state management, persistence, and failure handling.
- Other automations belong in separate services so each agent can be deployed, tested, and reasoned about independently.

Tradeoff:

- Some integration patterns will be repeated in later agents. That duplication is acceptable because each service should remain independently runnable.

## 2. Five-Agent Suite

Decision: build a connected suite, not one oversized monolith.

The planned services are:

1. LeadPilot AI Voice Agent - inbound call qualification.
2. Missed Call Text-Back Agent - SMS recovery and lead qualification after missed calls.
3. Outbound Follow-Up Agent - estimate, no-show, re-engagement, and seasonal campaigns.
4. AI Review Request Agent - post-job review or feedback routing.
5. Web Chat Lead Qualifier - embeddable RAG chat widget for contractor websites.

Why:

- Each agent solves a distinct operational gap.
- Separate repos make each service easier to inspect and deploy.
- A shared domain model across all agents shows how the systems fit together without forcing runtime coupling.

Tradeoff:

- Shared code may eventually deserve a small package, but early services should stay self-contained.

## 3. Build the Voice Loop Instead of Using a Voice-Orchestration Platform

Decision: use Twilio Media Streams, Deepgram, LangGraph, LiteLLM, and TTS directly instead of Vapi, Bland, Retell, or OpenAI Realtime.

Why:

- The application owns the orchestration layer.
- Provider fallback, logging, and state transitions are visible in code.
- The call flow can be tuned for the business process instead of fitting a hosted voice-agent abstraction.
- It avoids per-minute orchestration markup for a system that can be self-hosted.

Tradeoff:

- More operational details are now application responsibilities: media-stream reconnects, silence timers, prompt timing, and failure recovery.

## 4. Twilio Is Telephony Only

Decision: Twilio handles phone numbers, PSTN, webhooks, Media Streams, SMS, and call updates. It does not own the AI behavior.

Why:

- Twilio is reliable telephony infrastructure.
- The AI state machine remains inside FastAPI and LangGraph.
- Webhook validation is easy to test with Twilio's RequestValidator.

Tradeoff:

- Twilio trial accounts restrict calls and SMS to verified numbers. Upgrade Twilio for unrestricted public call testing.

## 5. Deepgram for Streaming STT

Decision: use Deepgram live transcription over raw WebSocket with Twilio's mulaw 8 kHz audio.

Why:

- Twilio Media Streams already send mulaw 8 kHz audio.
- Deepgram accepts that format directly.
- Keeping the WebSocket code in the repo gives precise control over final transcripts and turn-taking.

Tradeoff:

- The current implementation uses final utterances only. Barge-in and interim transcript handling can be added later.

## 6. LangGraph for State Management

Decision: represent the call as a LangGraph state machine.

Why:

- The business process is naturally stateful.
- Nodes map cleanly to qualification steps: greeting, service, urgency, location, contact, routing, terminal outcome, summary.
- Emergency keyword interception can run before every turn.
- Tests can exercise routing without live phone calls.

Tradeoff:

- A simple Python state machine would be smaller today. LangGraph is chosen because the workflow is expected to grow and because explicit graph nodes make the call path easier to review.

## 7. LiteLLM Fallback Chain

Decision: all LLM calls go through one LiteLLM client.

Current chain:

1. `groq/llama-3.3-70b-versatile`
2. `mistral/mistral-small-latest`
3. `mistral/ministral-3b-latest`

Why:

- The rest of the app does not depend on one provider SDK.
- Timeouts, provider errors, and rate-limit fallback are centralized.
- The serving provider is logged and stored on the call record.
- Groq usage can be capped before the daily free-tier limit is exhausted.

Tradeoff:

- Fallbacks only help if API keys and model names stay valid. `/health` checks the configured chain.

## 8. TTS Fallback

Decision: ElevenLabs is optional; Twilio `<Say>` is the default budget-safe fallback.

Why:

- ElevenLabs API speech can fail with `402 Payment Required` when account balance is zero.
- A live call should keep moving with a lower-quality voice instead of failing.
- Twilio `<Say>` is already available through the telephony provider.

Implementation detail:

- Twilio `<Say>` requires updating the live call with new TwiML.
- That update reconnects the Media Stream.
- The app tracks expected `<Say>` reconnects so it does not finalize the call, replay the greeting, or fire the silence timer too early.

Tradeoff:

- Twilio `<Say>` sounds less natural and adds reconnect complexity.

## 9. Supabase for Persistence

Decision: store calls, leads, and duplicate markers in Supabase through PostgREST.

Why:

- Supabase is simple to inspect during testing.
- It avoids running a database container for small deployments.
- The same Supabase project can support adjacent services using `business_id`.

Tradeoff:

- The voice agent must tolerate database failures during live calls. The app logs failures but still answers the phone.

## 10. One Worker

Decision: deploy with one Uvicorn worker for now.

Why:

- Active call sessions are in memory.
- The Groq daily usage counter is in memory.
- One worker keeps live call state predictable.

Next step before scaling:

- Move active sessions and usage counters to Redis or Supabase.

## 11. Demo Mode

Decision: keep demo constraints explicit.

Why:

- Twilio trial limitations are vendor restrictions, not application behavior.
- ElevenLabs API balance is optional because Twilio `<Say>` is supported.
- Clear constraints make testing easier and prevent confusion.

Recommended settings for a budget deployment:

```env
USE_ELEVENLABS_TTS=false
SILENCE_PROMPT_SECONDS=10
SILENCE_HANGUP_SECONDS=7
```
