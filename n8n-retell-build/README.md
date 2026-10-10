# LeadPilot: Retell + n8n post-call build

This is a **separate implementation** from the live, custom-coded LeadPilot in the parent repository. Retell handles the conversation; n8n handles post-call routing. Neither workflow in this folder is deployed or active. Importing these files does not replace or modify the custom-coded agent.

## Files

- `LeadPilot AI - Retell + n8n v0.json`: importable Retell conversation-flow agent. It is unpublished and points only `call_analyzed` events at the n8n production webhook path.
- `leadpilot-post-call.json`: webhook, regex extraction, duplicate check, routing, Jobber requests, Twilio SMS, Supabase call logs.
- `leadpilot-error-handler.json`: separate n8n Error Trigger workflow for Supabase error logs and Slack alerts.
- `schema.sql`: dedicated `retell_call_logs` and `retell_call_errors` tables; does not alter the custom-coded agent's tables.
- `build-workflows.js`: source for the two generated JSON files; run `node build-workflows.js` after editing it.

JSON does not support comments. Each workflow starts with a Sticky Note describing credentials and cost.

## Import the Retell voice agent

1. In Retell AI, import `LeadPilot AI - Retell + n8n v0.json` as a new agent/version. Keep it unpublished while reviewing the canvas.
2. Replace all demo defaults before any external test: business name, services, service-area ZIP codes, hours, and callback expectation. The included values are explicitly demo values inherited from the custom-coded LeadPilot example configuration.
3. Confirm the agent webhook is `https://n8n.sohaib.systems/webhook/retell-call-end` and the only selected event is `call_analyzed`. Retell sends the full `call_analysis` on that event.
4. Test every Retell branch in its simulator first: routine qualified lead, same-day request, emergency, out-of-area ZIP, missing ZIP, general question, human callback, and caller correction.
5. Do not publish or place a real call yet. The n8n workflow is inactive and its credential nodes are intentionally unconfigured. The webhook must also have Retell signature verification before public activation.

The Retell agent conducts the live voice conversation. The n8n workflow starts only after Retell finishes post-call analysis; it does not speak to the caller or control the live dialogue.

## Before import or activation

1. Create the dedicated tables from `schema.sql` in a **separate/test Supabase project** first. Only move to a client's project after approval. Protect the service-role key in n8n credentials; never expose it in browser code.
2. Import the error workflow, configure its Supabase and Slack credentials, select the destination Slack channel, save it, and test an error. Then import the main workflow and choose the saved error workflow under **Workflow Settings > Error workflow**. The main workflow is intentionally inactive.
3. Configure the main workflow's Supabase and Twilio credentials, both Jobber HTTP Request nodes with a client-authorized OAuth2 credential, current Jobber API version, `REPLACE_WITH_OWNER_E164`, and `REPLACE_WITH_TWILIO_E164`. Review the client's service area, consent rules, number formatting, and Retell prompt before enabling caller SMS. Jobber access tokens expire; use an OAuth2 credential that refreshes them.
4. The public webhook **must authenticate Retell** before activation. Retell signs the raw request body in `x-retell-signature`; this JSON does not verify it. Put a raw-body signature-verifying ingress in front of n8n, or implement and test equivalent verification in n8n. Do not expose an unsigned webhook that can send SMS or create Jobber records.
5. In Retell, subscribe the agent to `call_analyzed` and send it to the production webhook URL only after the security gate and test pass. Real Retell payload is `{ "event": "call_analyzed", "call": { ... } }`. The parser also accepts the earlier flat sample payload for isolated tests. It ignores `call_ended`, which normally lacks `call_analysis`.
6. Run controlled tests with a test Twilio number, test Jobber account, and test Supabase project. Verify both emergency and qualified branches, out-of-area, duplicate caller/call ID, absent phone/name, Jobber GraphQL `errors` and `userErrors`, failed Twilio, and the error workflow. Do not use a real customer's number for testing.

## Behavior and limits

- Regex parsing is deliberately conservative. It does **not** infer a caller's name, service area, appointment, or booking from ambiguous transcript text. Out-of-area is only routed when the transcript explicitly says so; non-emergency calls missing a name or service go to a `needs_review` log and owner SMS, with no CRM write or caller SMS. This is not a replacement for the live-call qualification prompt.
- Emergency requests are marked `HIGH PRIORITY` in the Jobber request title; Jobber request creation has no priority field. An owner SMS is a separate branch. The workflow does **not** schedule a technician or guarantee a booking.
- A Supabase read checks matching `from_number` or `call_id` in the last 60 minutes. It is not atomic. Production hardening should reserve `call_id` transactionally before external effects so simultaneous webhook deliveries cannot create duplicate Jobber records.
- n8n acknowledges the webhook immediately. Failures after the acknowledgement do not cause Retell to retry; the separate error workflow records and alerts them. Observe n8n executions and use manual recovery for partial Jobber/Twilio failures.
- Retell per-minute charges and Twilio telephony/SMS belong to the client's agreed budget/retainer. This post-call workflow has no separate LLM or voice-generation cost.

## References

- Retell events and payload: https://docs.retellai.com/features/webhook-overview
- Retell signature: https://docs.retellai.com/features/secure-webhook
- n8n error workflows: https://docs.n8n.io/build/flow-logic/handle-errors-gracefully/
- Jobber GraphQL and error responses: https://developer.getjobber.com/docs/using_jobbers_api/api_queries_and_mutations/
