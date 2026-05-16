# 🎟️ Ticket Tracker

A small web app for tracking event tickets you've bought and resold.
Forward booking emails to a dedicated inbox; Claude extracts the details
automatically. One admin account (you) can add/edit/delete; viewer accounts
can sign in to see the list.

---

## What you'll get when it's set up

- A web link like `https://your-tickets.onrender.com` you can open on any device
- A login screen with two accounts:
  - **admin** — full access (add, edit, delete, trigger inbox check)
  - **viewer** — read-only (share this login with anyone you trust)
- Forward a ticket confirmation to a Gmail inbox → it appears in your list
  within a few minutes, status set to `bought`
- When you sell a ticket, set its status to `sold` and fill in the sold
  price — profit (in GBP, with USD conversion) appears automatically

---

## Part 1 — One-time setup (≈ 45 minutes, all on the web)

You'll create **five free things** and one paid thing (~£6/month for hosting).
Take it one step at a time; nothing here requires any installation on your
computer.

### Step 1 — Anthropic API key (for the AI email parser)

1. Go to **<https://console.anthropic.com/>** and sign up.
2. Click **Plans** in the side menu and add **$5 of credit**
   (≈ £4 — easily lasts thousands of emails).
3. Click **API keys** → **Create Key** → give it any name → click **Create**.
4. **Copy the key** (starts with `sk-ant-…`) and paste it somewhere safe.
   You won't be able to see it again.

### Step 2 — Dedicated Gmail inbox (for receiving forwarded emails)

1. Create a brand new Gmail account just for tickets, e.g.
   `yourname.tickets@gmail.com`. (You won't be reading it; it's just a
   forwarding target.)
2. Turn on **2-Step Verification** at
   <https://myaccount.google.com/security>.
3. Go to <https://myaccount.google.com/apppasswords> and create an
   **App Password** (pick "Mail" / "Other (Custom)", name it "Ticket
   Tracker"). Copy the 16-character password and save it.
4. In your **personal** email, set up forwarding so booking emails go to
   this new inbox — either set up auto-forward rules for senders like
   Ticketmaster / AXS / See Tickets, or just forward them manually when
   they arrive.

### Step 3 — Free Postgres database (Neon)

This is where your ticket data will live.

1. Go to **<https://neon.tech>** and sign up (free, no credit card needed).
2. Click **Create project**. Defaults are fine; pick a region close to the UK
   (London / Frankfurt).
3. On the project dashboard, find the **connection string**. It looks like:
   `postgresql://user:password@ep-something.eu-central-1.aws.neon.tech/neondb?sslmode=require`
4. **Copy the entire string** and save it.

### Step 4 — Get the code on GitHub

GitHub hosts your code so Render can deploy from it.

1. Sign up at **<https://github.com>** (free).
2. Click the **+** in the top-right → **New repository**.
3. Name it `ticket-tracker`, leave it as **Public**, click **Create
   repository**.
4. On the empty repo page, click **uploading an existing file**.
5. **Unzip** the `ticket-tracker.zip` I gave you on your computer, then
   open the unzipped folder. Select **all the files inside it** (not the
   folder itself) and drag them onto the GitHub upload area.
6. Scroll down and click **Commit changes**.

You should now see files like `requirements.txt`, `app/`, `static/` etc.
in your repo.

### Step 5 — Deploy to Render

1. Go to **<https://render.com>** and sign up using your GitHub account
   (easier — Render will be able to read your repo).
2. On the dashboard, click **New +** → **Blueprint**.
3. Connect your `ticket-tracker` GitHub repo. Render reads `render.yaml`
   from the repo automatically.
4. Render will show you a list of environment variables that need values.
   Paste in:

| Variable | What to put |
|---|---|
| `ADMIN_USERNAME` | something memorable for you, e.g. `alex` |
| `ADMIN_PASSWORD` | a strong password (this is yours) |
| `VIEWER_USERNAME` | e.g. `viewer` |
| `VIEWER_PASSWORD` | a different password (this is what you share) |
| `ANTHROPIC_API_KEY` | the `sk-ant-…` key from Step 1 |
| `DATABASE_URL` | the Neon connection string from Step 3 |
| `IMAP_USER` | the Gmail address from Step 2 |
| `IMAP_PASSWORD` | the 16-character App Password from Step 2 |

(`SECRET_KEY` will be generated automatically.)

5. Click **Apply**. Render will install Python, install the dependencies,
   and start your app. This takes ~3 minutes the first time. When it
   finishes you'll see a green "Live" badge and a URL like
   `https://ticket-tracker-xxxx.onrender.com`.

### Step 6 — Open the link and log in

Visit your Render URL. You should see the login page. Sign in with your
admin username/password.

The app's now running 24/7. Bookmark the URL on your phone and laptop.

---

## Part 2 — Daily use

### Recording a new ticket
1. Forward the booking confirmation to your tickets Gmail address.
2. Within ~5 minutes, refresh the app — a new row will appear with
   status `bought`. (You can also click **"Poll inbox now"** to check
   immediately.)

### When you list it for resale
1. Click **Edit** on the row.
2. Change status to `listed`.

### When it sells
1. Click **Edit**.
2. Change status to `sold` and fill in **Sold for**.
3. The **Profit (GBP)** column updates automatically (converting USD if
   needed, using today's FX rate).

### Sharing read-only access
Give other people the `VIEWER_USERNAME` and `VIEWER_PASSWORD`. They go to
your Render URL, log in, and see the list — but no edit/delete buttons.

To revoke their access later, change `VIEWER_PASSWORD` in Render's
**Environment** tab. The app restarts in ~30s with the new password.

---

## Cost summary

| Service | Cost |
|---|---|
| Render Starter web service | ~$7/month (~£5.50) — always on |
| Neon Postgres free tier | £0 |
| Gmail | £0 |
| Anthropic API | ~$0.001 per email (so $5 of credit ≈ 5,000 emails) |

If you'd rather use Render's free tier to save the £5/month, you can —
but Render's free web services sleep after 15 minutes of inactivity, which
stops the email polling from working reliably. You'd have to log in once
in a while to wake it up.

---

## Tweaks you might want later

- **Statuses** — edit the list in `app/templates/edit.html`
  (currently `bought / listed / sold / used / refunded`).
- **Currencies** — add to the `<select>` in the same template;
  conversion in `app/currency.py` already handles any 3-letter ISO code.
- **Better email parsing** — edit `PROMPT` in `app/parser.py` to bias
  Claude towards the specific platforms you use.
- **More viewer accounts with individual passwords** — would require
  swapping the env-var auth for a proper user table. Ask me and I'll add
  it.

---

## Optional: running it on your own laptop instead of on the web

Skip this section if you're hosted on Render — you don't need it.

1. Install Python 3.10+ from <https://www.python.org/downloads/>
   (tick "Add Python to PATH" on the Windows installer).
2. Open PowerShell, navigate to the unzipped folder, then run:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   ```
3. Open `.env` in Notepad and fill in your values (skip `DATABASE_URL` to
   use a local SQLite file).
4. Run:
   ```
   python run.py
   ```
5. Open <http://127.0.0.1:8000> in your browser.
