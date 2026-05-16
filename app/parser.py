"""Use Claude to extract ticket info from a forwarded email.

Returns one of:
  {"email_type": "purchase", "artist": ..., "event_date": ..., "total_amount": ...,
   "currency": ..., "tickets": [{seat_number, notes}, ...]}
  {"email_type": "sale",     ...same shape...}
  {"email_type": "other"}
"""
import json
import logging
import os

from anthropic import Anthropic

log = logging.getLogger(__name__)

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "email_type": {
            "type": "string",
            "enum": ["purchase", "sale", "other"],
            "description": (
                "'purchase' = booking confirmation / ticket transfer received "
                "(Ticketmaster, AXS, See Tickets, Eventim, DICE, etc.). "
                "'sale' = the recipient's tickets were sold/resold (StubHub, "
                "Twickets, viagogo, etc.). 'other' = anything else."
            ),
        },
        "artist": {"type": ["string", "null"], "description": "Performer, band, team, or event name."},
        "location": {"type": ["string", "null"], "description": "Venue name and city."},
        "event_date": {"type": ["string", "null"], "description": "ISO YYYY-MM-DD. Null if not stated."},
        "total_amount": {
            "type": ["number", "null"],
            "description": "TOTAL transaction amount as a number, not per-ticket. Null for free transfers.",
        },
        "currency": {"type": ["string", "null"], "description": "ISO code: GBP or USD."},
        "tickets": {
            "type": "array",
            "description": (
                "One entry per INDIVIDUAL ticket / seat. If 4 seats are listed, "
                "output 4 entries. If 'tickets' is empty, the array should be []."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "seat_number": {
                        "type": ["string", "null"],
                        "description": "Seat info for this single ticket, e.g. 'Block 102 Row K Seat 12' or 'Standing'.",
                    },
                    "notes": {
                        "type": ["string", "null"],
                        "description": "Section, ticket type, or other detail for this single ticket.",
                    },
                },
            },
        },
    },
    "required": ["email_type"],
}

PROMPT = """You extract structured info from a forwarded email about event tickets.

Step 1 — Decide email_type:
  "purchase" = recipient BOUGHT tickets (booking confirmation, ticket transfer received, receipt from Ticketmaster, AXS, See Tickets, Eventim, DICE, Songkick, etc.)
  "sale"     = recipient's tickets were SOLD/resold (notification from StubHub, Twickets, viagogo, Vivid Seats, etc.)
  "other"    = anything else (marketing, newsletter, account notification, password reset, etc.) -> tickets: []

Step 2 — Extract event info: artist, location, event_date (YYYY-MM-DD), total_amount (entire transaction, NOT per-ticket), currency.

Step 3 — Extract the tickets array:
  Output one entry for EACH individual ticket.
  - 4 specific seats listed -> 4 entries, one per seat.
  - "4 x General Admission" with no seats -> 4 entries with seat_number = "General Admission".
  - 1 ticket -> 1 entry.
  - "other" email -> empty array [].

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
        if s.endswith("```"):
            s = s[:-3].strip()
    return s


def extract_email(subject: str, body: str) -> dict | None:
    """Return the parsed dict, or None on error."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set")
        return None

    client = Anthropic(api_key=api_key)
    body = (body or "")[:20000]

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": PROMPT.format(
                schema=json.dumps(EXTRACTION_SCHEMA, indent=2),
                subject=subject or "(no subject)",
                body=body,
            ),
        }],
    )

    text = _strip_fences(response.content[0].text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning(f"Parser returned non-JSON: {text[:300]}")
        return None

    # Normalise: always have tickets as a list
    if "tickets" not in data or data["tickets"] is None:
        data["tickets"] = []
    if data.get("currency"):
        data["currency"] = data["currency"].upper()

    return data
