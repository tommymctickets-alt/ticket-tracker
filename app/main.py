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
        "buyer_phone": t.buyer_phone,
        "delivery_method": t.delivery_method,
        "delivery_deadline": t.delivery_deadline,
        "order_reference": t.order_reference,
        "sale_platform": t.sale_platform,
        "delivery_notes": t.delivery_notes,
        "delivered": bool(t.delivered),
        "purchase_platform": t.purchase_platform,
        "ticket_type": t.ticket_type,
        "paid_by": t.paid_by,
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


SORT_COLUMNS = {
    "artist": Ticket.artist,
    "event_date": Ticket.event_date,
    "price_bought_amount": Ticket.price_bought_amount,
    "price_sold_amount": Ticket.price_sold_amount,
    "purchase_platform": Ticket.purchase_platform,
    "ticket_type": Ticket.ticket_type,
}


def _filter_url(params: dict, **overrides) -> str:
    """Build a /?... URL by merging params with overrides. None/empty drops the key."""
    from urllib.parse import urlencode
    p = dict(params)
    for k, v in overrides.items():
        if v in (None, "", 0, "0"):
            p.pop(k, None)
        else:
            p[k] = v
    # Drop the default sort (cleaner URLs)
    if p.get("sort") == "event_date" and p.get("order") == "desc":
        p.pop("sort", None)
        p.pop("order", None)
    p = {k: str(v) for k, v in p.items() if v not in (None, "", 0, "0")}
    if not p:
        return "/"
    return "/?" + urlencode(p)


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_user),
    status: str = "",
    pending_delivery: int = 0,
    platform: str = "",
    ticket_type: str = "",
    paid_by: str = "",
    sort: str = "event_date",
    order: str = "desc",
):
    # Validate sort + order
    if sort not in SORT_COLUMNS:
        sort = "event_date"
    if order not in ("asc", "desc"):
        order = "desc"

    sort_col = SORT_COLUMNS[sort]
    sort_expr = sort_col.desc().nullslast() if order == "desc" else sort_col.asc().nullsfirst()

    # Apply column filters
    q = db.query(Ticket)
    if platform:
        q = q.filter(Ticket.purchase_platform == platform.lower())
    if ticket_type:
        q = q.filter(Ticket.ticket_type == ticket_type.lower())
    if paid_by:
        q = q.filter(Ticket.paid_by == paid_by.lower())
    q = q.order_by(sort_expr, Ticket.id.desc())
    all_tickets = q.all()

    # Counts per status (reflect current platform/type filters)
    counts = {s: 0 for s in STATUS_ORDER}
    for t in all_tickets:
        if t.status in counts:
            counts[t.status] += 1

    pending_count = sum(1 for t in all_tickets if t.status == "sold" and not t.delivered)

    # Decide which groups to render
    if pending_delivery:
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
        status = ""
        groups = []
        for s in STATUS_ORDER:
            in_group = [t for t in all_tickets if t.status == s]
            groups.append({
                "status": s,
                "tickets": [_ticket_to_view(t) for t in in_group],
                "count": len(in_group),
                **_group_stats(in_group),
            })

    # Top-line summary uses ALL tickets (not filtered) so the numbers don't change as you filter
    all_unfiltered = db.query(Ticket).all()
    summary = {
        "total_tickets": len(all_unfiltered),
        "outstanding_gbp": _group_stats(
            [t for t in all_unfiltered if t.status in ("bought", "listed")]
        )["invested_gbp"],
        "realized_profit_gbp": _group_stats(
            [t for t in all_unfiltered if t.status == "sold"]
        )["profit_gbp"],
        "pending_delivery_count": sum(
            1 for t in all_unfiltered if t.status == "sold" and not t.delivered
        ),
    }

    # Build URLs (in Python; templates just use them)
    current_params = {
        "status": status, "pending_delivery": pending_delivery,
        "platform": platform, "ticket_type": ticket_type, "paid_by": paid_by,
        "sort": sort, "order": order,
    }

    def _sort_url(col):
        new_order = "asc" if (sort != col or order == "desc") else "desc"
        return _filter_url(current_params, sort=col, order=new_order)

    sort_urls = {col: _sort_url(col) for col in SORT_COLUMNS}

    tab_urls = {
        "all": _filter_url(current_params, status=None, pending_delivery=None),
    }
    if pending_count > 0 or pending_delivery:
        tab_urls["pending_delivery"] = _filter_url(
            current_params, status=None, pending_delivery=1
        )
    for s in STATUS_ORDER:
        tab_urls[s] = _filter_url(current_params, status=s, pending_delivery=None)

    remove_filter_urls = {
        "platform": _filter_url(current_params, platform=None),
        "ticket_type": _filter_url(current_params, ticket_type=None),
        "paid_by": _filter_url(current_params, paid_by=None),
    }

    def _cell_filter_url(field, value):
        if not value:
            return None
        current_value = {"platform": platform, "ticket_type": ticket_type, "paid_by": paid_by}.get(field, "")
        if current_value == value:
            return _filter_url(current_params, **{field: None})
        return _filter_url(current_params, **{field: value})

    return render(
        "index.html", request,
        groups=groups,
        counts=counts,
        current_filter=status,
        pending_view=bool(pending_delivery),
        summary=summary,
        status_order=STATUS_ORDER,
        current_sort=sort,
        current_order=order,
        sort_urls=sort_urls,
        tab_urls=tab_urls,
        active_platform=platform,
        active_ticket_type=ticket_type,
        active_paid_by=paid_by,
        remove_filter_urls=remove_filter_urls,
        cell_filter_url=_cell_filter_url,
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
    purchase_platform: str = Form(""),
    ticket_type: str = Form(""),
    paid_by: str = Form(""),
    buyer_name: str = Form(""),
    buyer_email: str = Form(""),
    buyer_phone: str = Form(""),
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
    t.purchase_platform = purchase_platform.strip().lower() or None
    t.ticket_type = ticket_type.strip().lower() or None
    t.paid_by = paid_by.strip().lower() or None
    t.buyer_name = buyer_name.strip() or None
    t.buyer_email = buyer_email.strip() or None
    t.buyer_phone = buyer_phone.strip() or None
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
