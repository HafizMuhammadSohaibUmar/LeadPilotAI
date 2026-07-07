"""All LLM prompts, kept in one place so business owners / prompt engineers
can tune voice + behavior without touching graph logic.

Every prompt instructs the model to answer in ONE short sentence — long
answers destroy perceived latency on a phone call.
"""

SYSTEM_PROMPT = """You are a friendly, efficient phone receptionist for {business_name}, \
a home services company. You are on a live phone call, so:
- Keep every reply to ONE short sentence (max 25 words).
- Never use emojis, markdown, or bullet points — this is spoken audio.
- Be warm but efficient; callers may be stressed about a home emergency.
- Never invent prices, availability, or technician names.
- Never reveal you are an AI unless directly asked; if asked, answer honestly."""

GREETING_TEMPLATE = (
    "Thanks for calling {business_name}! I can help you get service scheduled. "
    "May I have your name, please?"
)

SERVICE_IDENTIFICATION_PROMPT = """The caller said: "{user_input}"

Classify their home-service need into EXACTLY one of these categories:
hvac_repair, hvac_maintenance, plumbing_emergency, plumbing_routine,
roofing_inspection, roofing_repair, electrical, pest_control, garage_door, other

Respond with ONLY the category name, nothing else."""

SERVICE_QUESTION = (
    "Thanks{name_part}! What can we help you with today — for example heating, "
    "cooling, plumbing, roofing, electrical, pest control, or a garage door?"
)

URGENCY_ASSESSMENT_PROMPT = """The caller needs: {service_type}.
They said: "{user_input}"

Classify the urgency as EXACTLY one of: EMERGENCY, SAME_DAY, SCHEDULED
- EMERGENCY: active danger or property damage happening right now
- SAME_DAY: urgent discomfort or risk, needs someone today
- SCHEDULED: routine work, caller is flexible

Respond with ONLY the single word, nothing else."""

URGENCY_QUESTION = (
    "Got it, {service_description}. Is this an emergency happening right now, "
    "something you need handled today, or can we schedule a convenient time?"
)

LOCATION_QUESTION = (
    "Okay. What's the service address, including the ZIP code?"
)

ZIP_EXTRACTION_PROMPT = """Extract the 5-digit US ZIP code from this address, if present:
"{user_input}"

Respond with ONLY the 5-digit ZIP code, or NONE if no ZIP code is present."""

NAME_EXTRACTION_PROMPT = """The caller was asked for their name and replied:
"{user_input}"

Respond with ONLY their name (first name, or first and last), nothing else.
If no name is present, respond with NONE."""

CONTACT_QUESTION = (
    "Perfect. Is {caller_number} the best number for our technician to call you back on?"
)

CONTACT_CONFIRMATION_PROMPT = """The caller was asked whether {caller_number} is the best \
callback number. They replied: "{user_input}"

If they confirmed, respond with ONLY: {caller_number}
If they gave a different number, respond with ONLY that number in digits.
If unclear, respond with ONLY: {caller_number}"""

EMERGENCY_RESPONSE = (
    "I understand — that's an emergency. I'm alerting our on-call technician right now, "
    "and help is on the way. Please stay safe, and shut off the source if you can do so safely."
)

LEAD_CONFIRMATION = (
    "You're all set, {caller_name}! I've booked your {service_description} request and "
    "you'll get a text confirmation shortly. Our team will call you back very soon."
)

OUT_OF_AREA_APOLOGY = (
    "I'm so sorry, but that address is outside our service area, so we can't help this time."
)

OUT_OF_AREA_REFERRAL = (
    " You might try {referral_name} at {referral_phone} — they cover your area. "
    "Thanks so much for calling!"
)

OUT_OF_AREA_CLOSE = " Thanks so much for calling, and have a great day!"

DUPLICATE_RESPONSE = (
    "It looks like we already have your recent request on file, {caller_name} — "
    "our team is on it and will call you back shortly. Thanks for your patience!"
)

VOICEMAIL_FALLBACK = (
    "We're sorry — our virtual assistant is temporarily unavailable. Please leave "
    "your name, address, and a description of the problem after the tone, and "
    "we'll call you back as soon as possible."
)

SILENCE_PROMPT = "Are you still there? I'm happy to help whenever you're ready."

SILENCE_GOODBYE = (
    "It seems we've lost you. Please call back anytime — goodbye!"
)

CLARIFICATION_FALLBACK = "I'm sorry, I didn't quite catch that — could you say it again?"

# Spoken-friendly descriptions used inside sentences.
SERVICE_DESCRIPTIONS = {
    "hvac_repair": "a heating or cooling repair",
    "hvac_maintenance": "HVAC maintenance",
    "plumbing_emergency": "an urgent plumbing issue",
    "plumbing_routine": "a plumbing job",
    "roofing_inspection": "a roof inspection",
    "roofing_repair": "a roof repair",
    "electrical": "an electrical issue",
    "pest_control": "pest control",
    "garage_door": "a garage door issue",
    "other": "your service request",
}
