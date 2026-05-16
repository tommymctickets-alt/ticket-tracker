"""Poll a Gmail inbox via IMAP and process each new email."""
import email
import imaplib
import logging
import os
import re
from email.header import decode_header

from app.database import SessionLocal
from app.matcher import apply_sale
from app.models import ProcessedEmail, Ticket
from app.parser import extract_email

log = logging.getLogger(__name__)


def _decode_header(s):
    if not s:
        return ""
    parts = decode_header(s)
    out = []
    for txt, enc in parts:
        if isinstance(txt, bytes):
            try:
                out.append(txt.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                out.append(txt.decode("utf-8", errors="replace"))
        else:
            out.append(txt)
    return "".join(out)


def _decode_payload(part):
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _get_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ):
                text = _decode_payload(part)
                if text.strip():
                    return text
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html = _decode_payload(part)
                return re.sub(r"<[^>]+>", " ", html)
        return ""
    return _decode_payload(msg)


def _create_purchase(db, extracted: dict, message_id: str, subject: str) -> int:
    """Create one Ticket row per individual ticket in the purchase email."""
    artist = extracted.get("artist") or "Unknown"
    location = extracted.get("location")
    event_date = extracted.get("event_date")
    total = extracted.get("total_amount")
    currency = (extracted.get("currency") or "GBP").upper()

    items = extracted.get("tickets") or []
    if not items:
        # Unusual but possible — create a single row if there's enough info to be useful
        items = [{"seat_number": None, "notes": None}]

    per_ticket = round(total / len(items), 2) if total else None

    for i, item in enumerate(items):
        t = Ticket(
            artist=artist,
            location=location,
            notes=item.get("notes"),
            seat_number=item.get("seat_number"),
            event_date=event_date,
            status="bought",
            price_bought_amount=per_ticket,
            price_bought_currency=currency,
            source_email_id=f"{message_id}-{i}",  # keep unique constraint happy
            raw_email_subject=subject,
        )
        db.add(t)

    log.info(
        f"  -> created {len(items)} ticket(s) for {artist!r} "
        f"({currency} {per_ticket} each)"
    )
    return len(items)


def poll_inbox() -> int:
    """Check inbox for new emails; return the number of *new tickets created*."""
    host = os.getenv("IMAP_HOST", "imap.gmail.com")
    user = os.getenv("IMAP_USER")
    password = os.getenv("IMAP_PASSWORD")

    if not user or not password:
        log.warning("IMAP credentials not set; skipping poll")
        return 0

    created = 0
    try:
        m = imaplib.IMAP4_SSL(host)
        m.login(user, password)
        m.select("INBOX")

        status, data = m.search(None, "UNSEEN")
        if status != "OK":
            log.error("IMAP search failed")
            return 0

        ids = data[0].split()
        if not ids:
            log.info("Inbox poll: no new mail")
            return 0
        log.info(f"Inbox poll: {len(ids)} new email(s)")

        db = SessionLocal()
        try:
            for msg_id in ids:
                _, msg_data = m.fetch(msg_id, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                message_id = msg.get("Message-ID") or f"local-{msg_id.decode()}"
                if db.query(ProcessedEmail).filter_by(message_id=message_id).first():
                    continue

                subject = _decode_header(msg.get("Subject", ""))
                body = _get_body(msg)
                log.info(f"Parsing: {subject[:80]}")

                try:
                    extracted = extract_email(subject, body)
                except Exception as e:
                    log.exception(f"Parser error: {e}")
                    extracted = None

                if not extracted:
                    log.info("  -> couldn't parse, skipping")
                elif extracted.get("email_type") == "purchase":
                    n = _create_purchase(db, extracted, message_id, subject)
                    created += n
                elif extracted.get("email_type") == "sale":
                    n = apply_sale(db, extracted)
                    if n == 0:
                        log.info("  -> sale email, but no matching tickets found")
                else:
                    log.info("  -> not a ticket email")

                db.add(ProcessedEmail(message_id=message_id))
                db.commit()
        finally:
            db.close()

        m.close()
        m.logout()
    except Exception as e:
        log.exception(f"poll_inbox failed: {e}")

    return created
