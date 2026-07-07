# LeadPilot AI Decisions

## Root path serves humans

- `GET /` returns a branded landing page so a visitor opening the domain in a browser sees a real demo page instead of raw JSON.
- Twilio traffic stays on its own paths: `/voice`, `/call-status`, and `/media-stream/{call_sid}`.

## Single deployment, one domain

- The project stays on one FastAPI app so the browser page and the voice agent share the same public URL.
- This keeps the DigitalOcean setup simple and avoids maintaining a separate marketing site.

## Static assets stay local

- The landing page is checked into the repo as plain HTML plus an SVG architecture diagram.
- This keeps the page easy to review, portable in Docker, and safe to deploy without a build step.

## One worker only

- The in-memory call registry and Groq usage counter assume a single process.
- If horizontal scaling becomes necessary later, move those shared states into Redis or another external store first.

## DigitalOcean target

- The recommended deployment shape is a single container service with HTTPS and a custom domain.
- The backend should keep using the same webhook URLs after deployment so the Twilio configuration does not change between local and production.