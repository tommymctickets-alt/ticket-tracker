"""Match a sale email to existing tickets in the database."""
import logging

from sqlalchemy.orm import Session

from app.models import Ticket

log = logging.getLogger(__name__)


def _normalise(s: str) -> str:
    return (s or "").strip().lower()


def find_matching_tickets(db: Session, sale: dict) -> list[Ticket]:
    """Return tickets that this sale email refers to.

    Strategy (in order of preference):
      1. Same artist + same event_date, with status in (bought, listed).
      2. Prefer rows whose seat_number matches one of the sale's listed seats.
      3. Otherwise FIFO (oldest unsold first), up to the sale's ticket count.
    """
    artist = sale.get("artist")
    event_date = sale.get("event_date")
    sale_tickets = sale.get("tickets") or []
    quantity = max(1, len(sale_tickets))

    if not artist:
        log.warning("Sale email has no artist; can't match")
        return []

    # Step 1: filter candidates
    q = db.query(Ticket).filter(Ticket.status.in_(["bought", "listed"]))
    q = q.filter(Ticket.artist.ilike(f"%{artist.strip()}%"))
    if event_date:
        q = q.filter(Ticket.event_date == event_date)
    candidates = q.order_by(Ticket.id).all()

    if not candidates:
        return []

    # Step 2: prefer seat matches
    sale_seats = [_normalise(t.get("seat_number")) for t in sale_tickets if t.get("seat_number")]
    sale_seats = [s for s in sale_seats if s]

    if sale_seats:
        seat_matched = []
        rest = []
        for c in candidates:
            c_seat = _normalise(c.seat_number)
            if c_seat and any(s in c_seat or c_seat in s for s in sale_seats):
                seat_matched.append(c)
            else:
                rest.append(c)
        ordered = seat_matched + rest
    else:
        ordered = candidates

    return ordered[:quantity]


def apply_sale(db: Session, sale: dict) -> int:
    """Mark matching tickets as sold and record price + buyer/delivery details."""
    matches = find_matching_tickets(db, sale)
    if not matches:
        log.warning(
            f"Sale email matched no tickets: artist={sale.get('artist')!r}, "
            f"date={sale.get('event_date')!r}"
        )
        return 0

    total = sale.get("total_amount")
    currency = (sale.get("currency") or "GBP").upper()
    per_ticket = round(total / len(matches), 2) if total else None

    # Buyer / delivery info from the sale email (applies to all matched tickets)
    info = sale.get("sale_info") or {}

    for t in matches:
        t.status = "sold"
        if per_ticket is not None:
            t.price_sold_amount = per_ticket
            t.price_sold_currency = currency
        # Newly-sold tickets default to NOT yet delivered
        t.delivered = False

        # Only overwrite fields that the email actually populated
        if info.get("buyer_name"):       t.buyer_name = info["buyer_name"]
        if info.get("buyer_email"):      t.buyer_email = info["buyer_email"]
        if info.get("buyer_phone"):      t.buyer_phone = info["buyer_phone"]
        if info.get("delivery_method"):  t.delivery_method = info["delivery_method"]
        if info.get("delivery_deadline"): t.delivery_deadline = info["delivery_deadline"]
        if info.get("order_reference"):  t.order_reference = info["order_reference"]
        if info.get("platform"):         t.sale_platform = info["platform"]
        if info.get("delivery_notes"):   t.delivery_notes = info["delivery_notes"]

    log.info(
        f"Sale applied to {len(matches)} ticket(s) "
        f"({sale.get('artist')!r} on {sale.get('event_date')}, "
        f"{currency} {per_ticket} each, buyer={info.get('buyer_name')!r})"
    )
    return len(matches)
