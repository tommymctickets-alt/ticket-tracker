"""Use Claude to extract structured ticket data from a forwarded email."""
import json
import logging
import os

from anthropic import Anthropic

log = logging.getLogger(__name__)

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "is_ticket": {
            "type": "boolean",
            "description": "True ONLY if this is a real ticket booking confirmation, transfer, or resale notification.",
        },
        "artist": {"type": ["string", "null"], "description": "Performer, band, team, or event name."},
        "location": {"type": ["string", "null"], "description": "Venue name and city, e.g. 'O2 Arena, London'."},
        "notes": {
            "type": ["string", "null"],
            "description": "Section, ticket type, quantity, or other relevant details.",
        },
        "seat_number": {
            "type": ["string", "null"],
            "description": "Seat info, e.g. 'Block 102 Row K Seat 12' or 'Standing'.",
        },
        "event_date": {
            "type": ["string", "null"],
            "description": "Event date in ISO 8601 (YYYY-MM-DD). Null if not stated.",
        },
        "price_amount": {"type": ["number", "null"], "description": "Total price paid as a number."},
        "price_currency": {
            "type": ["string", "null"],
            "description": "ISO currency code: GBP or USD.",
        },
    },
    "required": ["is_ticket"],
}

PROMPT = """You extract structured ticket info from a forwarded email.

Common senders: Ticketmaster, AXS, See Tickets, Eventim, Songkick, DICE, StubHub, viagogo, Twickets.

If the email is NOT a real ticket booking, transfer, or resale, set is_ticket to false and leave other fields null.

Output ONLY valid JSON matching this schema (no prose, no markdown fences):
{schema}

Email subject: {subject}

Email body:
---
{body}
---"""


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```", 2)
        s = s[1] if len(s) > 1 else ""
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
        # If the model put a closing fence, drop it
        if s.endswith("```"):
            s = s[:-3].strip()
    return s


def extract_ticket(subject: str, body: str) -> dict | None:
    """Returns extracted fields, or None if the email isn't a ticket."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set")
        return None

    client = Anthropic(api_key=api_key)
    body = (body or "")[:20000]  # cap to keep cost low

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": PROMPT.format(
                    schema=json.dumps(EXTRACTION_SCHEMA, indent=2),
                    subject=subject or "(no subject)",
                    body=body,
                ),
            }
        ],
    )

    text = _strip_fences(response.content[0].text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning(f"Parser returned non-JSON: {text[:300]}")
        return None

    if not data.get("is_ticket"):
        return None

    return data
