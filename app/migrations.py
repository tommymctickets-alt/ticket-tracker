"""Lightweight schema migrations. Idempotent — safe to run every startup."""
import logging

from sqlalchemy import inspect, text

log = logging.getLogger(__name__)

# Columns that need adding for the sale/delivery feature.
# All accept NULL except `delivered` which has a default.
NEW_TICKET_COLUMNS = [
    ("buyer_name", "VARCHAR"),
    ("buyer_email", "VARCHAR"),
    ("delivery_method", "VARCHAR"),
    ("delivery_deadline", "VARCHAR"),
    ("order_reference", "VARCHAR"),
    ("sale_platform", "VARCHAR"),
    ("delivery_notes", "TEXT"),
    ("delivered", "BOOLEAN NOT NULL DEFAULT FALSE"),
]


def ensure_schema(engine):
    """Add missing columns to the tickets table. Safe to re-run."""
    inspector = inspect(engine)
    if not inspector.has_table("tickets"):
        return  # fresh DB; create_all will handle it

    existing = {c["name"] for c in inspector.get_columns("tickets")}
    added = []

    with engine.begin() as conn:
        for col, type_clause in NEW_TICKET_COLUMNS:
            if col not in existing:
                conn.execute(text(f'ALTER TABLE tickets ADD COLUMN {col} {type_clause}'))
                added.append(col)

        # Backfill: existing rows already marked "sold" were almost certainly
        # delivered already (the user wasn't tracking it before this update),
        # so flag them as delivered to avoid an unwanted "pending" flood.
        if "delivered" in added:
            conn.execute(
                text("UPDATE tickets SET delivered = :v WHERE status = 'sold'"),
                {"v": True},
            )

    if added:
        log.info(f"Schema migration: added columns to tickets: {', '.join(added)}")
