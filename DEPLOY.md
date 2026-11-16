# XAUUSD Bot — Deploy guide (public link)

The panel is a FastAPI app. Customers get a real public link only once it
is deployed on a public host. This guide gets you that link for free.

## Why Render (free hosting, checked 16 Nov 2026)

Free-hosting reality in 2026: most "free" tiers are gone or are trials.
- **Render free — the pick.** Permanent free tier, no credit card. 750
  instance-hours/month, 512 MB RAM. Gives you a public
  `https://xauusd-bot.onrender.com` link. Catch: it sleeps after 15
  minutes with no traffic (first visitor waits 30–60 seconds for it to
  wake). Fine for a customer panel.
- **Vercel Hobby — no.** Free, but the terms say personal, non-commercial
  use only. Selling a bot on it breaks the terms.
- **Railway / Fly.io / Koyeb — no.** No usable free tier for new accounts
  any more (trial credit only, then paid).
- **PythonAnywhere free — backup option.** Permanent free tier, but
  FastAPI support is limited (it prefers Flask/Django) and external
  network access is restricted.

## Deploy steps (about 20 minutes)

### 1. Put the code on GitHub
- Create a free GitHub account if needed, make a new repository
  (e.g. `xauusd-bot`), and upload this whole `xauusd-bot-web` folder.
  (GitHub web upload works: Add file → Upload files.)

### 2. Render (free account, no card)
- Sign up at render.com (email + password).
- Dashboard → **New → Blueprint** → connect the GitHub repo → Render
  reads `render.yaml`.
- Fill the prompted env vars:
  - `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — from step 3 below
    (you can deploy without them first, then add them and redeploy).
  - `APP_BASE_URL` — leave empty for the first deploy, then set it to
    your Render URL (see step 4) and redeploy once.
- Click Deploy. The public link appears on the service page
  (e.g. `https://xauusd-bot.onrender.com`).

### 3. Google sign-in setup (one time, free)
Customers log in with Gmail, so Google needs to know the app:
1. Go to [Google Cloud Console](https://console.cloud.google.com/) and
   sign in with your Gmail.
2. Create a project (any name, e.g. "XAUUSD Bot").
3. Left menu → **APIs & Services → OAuth consent screen** → User type
   **External** → fill App name ("XAUUSD Bot") and your support email →
   Save. (No verification needed while it stays in "Testing" — but
   Testing mode only allows up to 100 test users; add your Gmail as a
   test user, and customers can sign in once you add them or publish
   the app.)
4. **Credentials → Create Credentials → OAuth client ID** → Application
   type **Web application** → name it → under **Authorized redirect
   URIs** add exactly:
   `https://<your-app>.onrender.com/auth/google/callback`
   (replace `<your-app>` with your real Render service name).
5. Copy the **Client ID** and **Client secret** into Render → your
   service → Environment → `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` →
   Save (auto-redeploys).

### 4. Set the base URL
In Render → Environment, set `APP_BASE_URL` to your exact public URL
(e.g. `https://xauusd-bot.onrender.com`, no trailing slash) and save.
This is what Google redirects back to after sign-in.

### 5. Test the link yourself
Open the public URL on your phone → **Sign in with Gmail** → your admin
account is `sheraz.majeed372@gmail.com` (it sees the Admin tab; customers
do not). Connect a test MT5 account, pay $20 (test with a real small
payment only when ready), Start the bot.

## Admin: user control
The Admin tab (only your Gmail sees it) lets you, per customer:
- **Start / Stop** their bot (force).
- **+30d license** — grant or extend a license (free trial, or goodwill).
- **Revoke** — cancel a license and stop their bot.
- **Block / Unblock** — blocked users cannot sign in and their bots stop.
  Payment records are kept even for blocked users.
- **Settings** — change the monthly price or the TRC20 deposit address
  without redeploying.
- **Backup** — download all records as JSON
  (`GET /api/admin/backup`, admin only). Do this regularly (see below).

## Important: database on free Render
The app stores everything in a SQLite file (`xauusd_bot.db`). On Render's
free tier that file is **wiped whenever the service redeploys**. So:
- Download a backup from the Admin tab after every few customers, and
  before any redeploy.
- When the business grows, move to a free hosted Postgres (Neon or
  Supabase free tier) — that is the proper fix and needs a small code
  change (swap the SQLite layer for Postgres). Ask before doing it.

## The trading engine (separate, on your Windows server)
The panel alone does not trade. The actual trading runs in
`~/workspace/xauusd-bot-server/engine.py` on a Windows computer/server
that stays on 24/7 with MT5 installed. It polls the deployed panel's
`/api/engine/customers` with `ENGINE_API_KEY` (auto-generated in Render →
Environment; copy it into the engine's `.env` as `ENGINE_API_KEY` and set
`ENGINE_API_URL` to your public panel URL), trades each enabled licensed
account with the 6-bot rules, and reports trades back. See that folder's
README.md. The engine needs a Windows machine: your own PC left on, or a
cheap Windows VPS — that part is your infrastructure, not the customer's.
