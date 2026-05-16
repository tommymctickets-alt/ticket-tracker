"""ORM models."""
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    artist = Column(String, nullable=False)
    location = Column(String, nullable=True)
    notes = Column(Text, nullable=True)  # flexible: section / type / quantity / etc.
    seat_number = Column(String, nullable=True)
    event_date = Column(String, nullable=True)  # ISO YYYY-MM-DD
    status = Column(String, default="bought")   # bought, listed, sold, used, refunded

    price_bought_amount = Column(Float, nullable=True)
    price_bought_currency = Column(String, default="GBP")
    price_sold_amount = Column(Float, nullable=True)
    price_sold_currency = Column(String, default="GBP")

    # Where the ticket was originally bought (axs, ticketmaster, see tickets, etc.)
    purchase_platform = Column(String, nullable=True)
    # Format of the ticket (mobile, pdf, paper, e-ticket, will call, etc.)
    ticket_type = Column(String, nullable=True)
    # Who paid for this ticket
    paid_by = Column(String, nullable=True)

    # Sale & delivery info (populated when a sale email is processed)
    buyer_name = Column(String, nullable=True)
    buyer_email = Column(String, nullable=True)
    buyer_phone = Column(String, nullable=True)
    delivery_method = Column(String, nullable=True)
    delivery_deadline = Column(String, nullable=True)  # ISO YYYY-MM-DD
    order_reference = Column(String, nullable=True)
    sale_platform = Column(String, nullable=True)  # viagogo, stubhub, twickets, etc.
    delivery_notes = Column(Text, nullable=True)
    delivered = Column(Boolean, default=False, nullable=False)

    source_email_id = Column(String, nullable=True, unique=True)
    raw_email_subject = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ProcessedEmail(Base):
    __tablename__ = "processed_emails"

    message_id = Column(String, primary_key=True)
    processed_at = Column(DateTime, server_default=func.now())
    ticket_id = Column(Integer, nullable=True)
