"""FastAPI app: web UI + login + API + background email poller."""
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth import (
    Forbidden,
    NeedsLogin,
    authenticate,
    current_user,
    require_admin,
    require_user,
)
from app.currency import compute_profit_gbp
from app.database import get_db, init_db
from app.email_poller import poll_inbox
from app.models import Ticket

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    init_db()
    if os.getenv("IMAP_USER"):
        interval = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            poll_inbox,
            "interval",
            minutes=interval,
            next_run_time=datetime.now(),
        )
        scheduler.start()
        log.info(f"Email polling every {interval} minute(s)")
    else:
        log.info("IMAP not configured; running without inbox polling")
    yield
    if scheduler and scheduler.running:
        scheduler.shutdown()


app = FastAPI(lifespan=lifespan)

# Cookie sessions
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    secret_key = secrets.token_urlsafe(32)
    log.warning("SECRET_KEY not set; generated an ephemeral one (sessions reset on restart)")
app.add_middleware(SessionMiddleware, secret_key=secret_key, https_only=False, same_site="lax")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")


# ---- exception handlers redirect unauthenticated/forbidden requests ----

@app.exception_handler(NeedsLogin)
async def _needs_login(request: Request, _exc: NeedsLogin):
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden(request: Request, _exc: Forbidden):
    return RedirectResponse("/", status_code=303)


def render(name: str, request: Request, **context):
    return templates.TemplateResponse(
        name,
        {"request": request, "user": current_user(request), **context},
    )


# ---- auth routes ----

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", error: str = ""):
    return render("login.html", request, next=next, error=error)


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    user = authenticate(username.strip(), password)
    if not user:
        return RedirectResponse(
            f"/login?next={next}&error=Invalid+username+or+password",
            status_code=303,
        )
    request.session["user"] = user
    # Only allow internal redirect targets
    if not next.startswith("/"):
        next = "/"
    return RedirectResponse(next, status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---- ticket views ----

def _ticket_to_view(t: Ticket) -> dict:
    return {
        "id": t.id,
        "artist": t.artist,
        "location": t.location,
        "notes": t.notes,
        "seat_number": t.seat_number,
        "event_date": t.event_date,
        "status": t.status,
        "price_bought_amount": t.price_bought_amount,
        "price_bought_currency": t.price_bought_currency,
        "price_sold_amount": t.price_sold_amount,
        "price_sold_currency": t.price_sold_currency,
        "profit_gbp": compute_profit_gbp(
            t.price_bought_amount,
            t.price_bought_currency,
            t.price_sold_amount,
            t.price_sold_currency,
        ),
        "buyer_name": t.buyer_name,
        "buyer_email": t.buyer_email,
        "delivery_method": t.delivery_method,
        "delivery_deadline": t.delivery_deadline,
        "order_reference": t.order_reference,
        "sale_platform": t.sale_platform,
        "delivery_notes": t.delivery_notes,
        "delivered": bool(t.delivered),
    }


STATUS_ORDER = ["bought", "listed", "sold", "used", "refunded"]


def _group_stats(tickets) -> dict:
    """Per-group summary in GBP."""
    from app.currency import convert_to_gbp
    invested = sum(
        (convert_to_gbp(t.price_bought_amount, t.price_bought_currency) or 0)
        for t in tickets
    )
    earned = sum(
        (convert_to_gbp(t.price_sold_amount, t.price_sold_currency) or 0)
        for t in tickets
    )
    profit = sum(
        (compute_profit_gbp(
            t.price_bought_amount, t.price_bought_currency,
            t.price_sold_amount, t.price_sold_currency,
        ) or 0)
        for t in tickets
    )
    return {
        "invested_gbp": round(invested, 2),
        "earned_gbp": round(earned, 2),
        "profit_gbp": round(profit, 2),
    }


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_user),
    status: str = "",
    pending_delivery: int = 0,
):
    all_tickets = (
        db.query(Ticket)
        .order_by(Ticket.event_date.desc().nullslast(), Ticket.id.desc())
        .all()
    )

    # Counts per status (for tab badges) — over ALL tickets, not just visible
    counts = {s: 0 for s in STATUS_ORDER}
    for t in all_tickets:
        if t.status in counts:
            counts[t.status] += 1

    # Pending-delivery count (sold AND not yet delivered)
    pending_count = sum(1 for t in all_tickets if t.status == "sold" and not t.delivered)

    # Decide which groups to render
    if pending_delivery:
        # Special view: just the pending-delivery sold tickets
        pending = [t for t in all_tickets if t.status == "sold" and not t.delivered]
        groups = [{
            "status": "sold",
            "tickets": [_ticket_to_view(t) for t in pending],
            "count": len(pending),
            **_group_stats(pending),
            "is_pending_view": True,
        }]
        status = ""
    elif status in STATUS_ORDER:
        in_group = [t for t in all_tickets if t.status == status]
        groups = [{
            "status": status,
            "tickets": [_ticket_to_view(t) for t in in_group],
            "count": len(in_group),
            **_group_stats(in_group),
        }]
    else:
        status = ""  # normalise for tab highlight
        groups = []
        for s in STATUS_ORDER:
            in_group = [t for t in all_tickets if t.status == s]
            groups.append({
                "status": s,
                "tickets": [_ticket_to_view(t) for t in in_group],
                "count": len(in_group),
                **_group_stats(in_group),
            })

    # Top-line summary across all tickets (regardless of filter)
    summary = {
        "total_tickets": len(all_tickets),
        "outstanding_gbp": _group_stats(
            [t for t in all_tickets if t.status in ("bought", "listed")]
        )["invested_gbp"],
        "realized_profit_gbp": _group_stats(
            [t for t in all_tickets if t.status == "sold"]
        )["profit_gbp"],
        "pending_delivery_count": pending_count,
    }

    return render(
        "index.html", request,
        groups=groups,
        counts=counts,
        current_filter=status,
        pending_view=bool(pending_delivery),
        summary=summary,
        status_order=STATUS_ORDER,
    )


@app.get("/tickets/new", response_class=HTMLResponse)
def new_ticket_form(request: Request, _user: dict = Depends(require_admin)):
    return render("edit.html", request, ticket=None)


@app.get("/tickets/{ticket_id}/edit", response_class=HTMLResponse)
def edit_ticket_form(
    ticket_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    t = db.get(Ticket, ticket_id)
    if not t:
        raise HTTPException(404)
    return render("edit.html", request, ticket=_ticket_to_view(t))


def _to_float(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


@app.post("/tickets")
def create_or_update_ticket(
    db: Session = Depends(get_db),
    _user: dict = Depends(require_admin),
    id: str = Form(""),
    artist: str = Form(...),
    location: str = Form(""),
    notes: str = Form(""),
    seat_number: str = Form(""),
    event_date: str = Form(""),
    status: str = Form("bought"),
    price_bought_amount: str = Form(""),
    price_bought_currency: str = Form("GBP"),
    price_sold_amount: str = Form(""),
    price_sold_currency: str = Form("GBP"),
    buyer_name: str = Form(""),
    buyer_email: str = Form(""),
    delivery_method: str = Form(""),
    delivery_deadline: str = Form(""),
    order_reference: str = Form(""),
    sale_platform: str = Form(""),
    delivery_notes: str = Form(""),
    delivered: str = Form(""),
):
    if id:
        t = db.get(Ticket, int(id))
        if not t:
            raise HTTPException(404)
    else:
        t = Ticket()
        db.add(t)

    t.artist = artist.strip()
    t.location = location.strip() or None
    t.notes = notes.strip() or None
    t.seat_number = seat_number.strip() or None
    t.event_date = event_date.strip() or None
    t.status = status
    t.price_bought_amount = _to_float(price_bought_amount)
    t.price_bought_currency = price_bought_currency
    t.price_sold_amount = _to_float(price_sold_amount)
    t.price_sold_currency = price_sold_currency
    t.buyer_name = buyer_name.strip() or None
    t.buyer_email = buyer_email.strip() or None
    t.delivery_method = delivery_method.strip() or None
    t.delivery_deadline = delivery_deadline.strip() or None
    t.order_reference = order_reference.strip() or None
    t.sale_platform = sale_platform.strip() or None
    t.delivery_notes = delivery_notes.strip() or None
    t.delivered = bool(delivered)

    db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/tickets/{ticket_id}/deliver")
def mark_delivered(
    ticket_id: int,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    t = db.get(Ticket, ticket_id)
    if t:
        t.delivered = True
        db.commit()
    # Redirect back to wherever they came from if possible — fall back to pending view
    return RedirectResponse("/?pending_delivery=1", status_code=303)


@app.post("/tickets/{ticket_id}/delete")
def delete_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    t = db.get(Ticket, ticket_id)
    if t:
        db.delete(t)
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/poll")
def trigger_poll(_user: dict = Depends(require_admin)):
    poll_inbox()
    return RedirectResponse("/", status_code=303)


@app.get("/healthz")
def healthz():
    """Used by the host to check the app is alive."""
    return {"ok": True}
