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
        "purchase_platform": {
            "type": ["string", "null"],
            "description": (
                "(Purchase emails) The platform/site where the ticket was bought, "
                "based on the sender or branding: 'axs', 'ticketmaster', 'see tickets', "
                "'eventim', 'dice', 'songkick', 'gigsandtours', etc. Lowercase."
            ),
        },
        "ticket_type": {
            "type": ["string", "null"],
            "description": (
                "Format of the ticket: 'mobile' (transferable via app), 'pdf' "
                "(print-at-home), 'paper' (posted), 'e-ticket', 'will call', "
                "'wristband', etc. Lowercase."
            ),
        },
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
                        "description": "Seat info for this single ticket.",
                    },
                    "notes": {
                        "type": ["string", "null"],
                        "description": "Section, ticket type, or other detail for this single ticket.",
                    },
                },
            },
        },
        "sale_info": {
            "type": ["object", "null"],
            "description": "Only populate for SALE emails. Null for purchases and other.",
            "properties": {
                "buyer_name": {"type": ["string", "null"], "description": "Name of the buyer if mentioned."},
                "buyer_email": {"type": ["string", "null"], "description": "Buyer email if mentioned (for ticket transfer)."},
                "buyer_phone": {"type": ["string", "null"], "description": "Buyer phone number if mentioned (often required for mobile transfers)."},
                "delivery_method": {
                    "type": ["string", "null"],
                    "description": "How tickets must be delivered, e.g. 'Mobile transfer', 'Email PDF', 'Courier', 'Instant download'.",
                },
                "delivery_deadline": {
                    "type": ["string", "null"],
                    "description": "ISO YYYY-MM-DD by which tickets must be delivered to the buyer.",
                },
                "order_reference": {"type": ["string", "null"], "description": "Order ID / transaction ID from the platform."},
                "platform": {"type": ["string", "null"], "description": "Platform that handled the sale: viagogo, stubhub, twickets, vivid, etc."},
                "delivery_notes": {"type": ["string", "null"], "description": "Any other delivery instructions from the email."},
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

Step 3 — For PURCHASE emails, also identify:
  - purchase_platform: lowercase platform name from the sender or branding (axs, ticketmaster, see tickets, eventim, dice, etc.)
  - ticket_type: lowercase format word — 'mobile' / 'pdf' / 'paper' / 'e-ticket' / 'will call' / 'wristband'.
    Clues: "Add to Wallet" / "in your app" => mobile. "Print at home" / "PDF attached" => pdf. "Mailed" / "Royal Mail" => paper.

Step 4 — Extract the tickets array:
  Output one entry for EACH individual ticket.
  - 4 specific seats listed -> 4 entries, one per seat.
  - "4 x General Admission" with no seats -> 4 entries with seat_number = "General Admission".
  - 1 ticket -> 1 entry.
  - "other" email -> empty array [].

Step 5 — For SALE emails ONLY, populate sale_info:
  - buyer_name, buyer_email, buyer_phone (look hard — phone is often a long digit string near the name)
  - delivery_method (e.g. "Mobile transfer", "Email PDF", "Courier", "Instant download")
  - delivery_deadline (ISO date by which tickets must be sent)
  - order_reference (platform's order/transaction ID)
  - platform (viagogo / stubhub / twickets / etc., based on sender)
  - delivery_notes (any specific instructions from the email)
  For purchase and other emails, set sale_info to null.

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
