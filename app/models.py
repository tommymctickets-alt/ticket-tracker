"""ORM models."""
from sqlalchemy import Column, DateTime, Float, Integer, String, Text
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

    source_email_id = Column(String, nullable=True, unique=True)
    raw_email_subject = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ProcessedEmail(Base):
    __tablename__ = "processed_emails"

    message_id = Column(String, primary_key=True)
    processed_at = Column(DateTime, server_default=func.now())
    ticket_id = Column(Integer, nullable=True)
