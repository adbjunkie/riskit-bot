# RiskIt Bot (Basic)

**Anonymous photo sharing bot for Telegram.**

Users send **one or more photos** privately → confirm the batch with one tap → the bot posts them **anonymously** into your Telegram group or channel (no username visible, posts come from the bot).

This is the clean v1 implementation. Perfect base before adding AI enhancement, battles, vault, etc.

---

## Features (Current)

- Works only in **private chat** with the bot
- `/start` shows rules + clear instructions
- Send **one or many photos** (individually or as album) **or** leak email / phone / Instagram
- Always-visible persistent menu buttons (Leak Photo / Email / Phone / Instagram + Rules + Cancel) — commands still work too
- Clear welcome message explaining what the bot is and that leaks are posted anonymously to the target group/channel
- Batch confirmation for photos; simple confirmation for text leaks
- Truly anonymous: bot posts from its own account (no user info or forwarding)
- Each photo gets individual reactions; rate limiting counts batches and text leaks toward daily total.
- Clean, minimal, easy to extend

---

## Important: Target Chat Setup

Before deploying the bot:

1. Create a **Supergroup** or a **Channel**
2. Add the bot as **Administrator** in that chat
3. Enable **Post Messages** permission (required)
4. (Optional but recommended) Enable **Delete Messages**
5. Go to chat settings → **Reactions** → turn reactions on
6. Pin the rules (in groups) or post them as a pinned message (in channels)

**Tip:** Many people prefer a **Channel** (with an optional linked Discussion Group for comments) for a cleaner photo feed experience. The bot works with both.

---

## Deploying on Railway + GitHub (Recommended Path)

This project is set up to be pushed to GitHub and deployed on [Railway](https://railway.app).

### 1. Prepare for GitHub (Critical)

**Never commit real tokens or IDs.**

- The real secrets live in **environment variables** (not in code).
- `.gitignore` already ignores `.env`, `data/`, `__pycache__`, etc.
- Copy `.env.example` → `.env` **only for local testing** (never push `.env`).

### 2. Push to GitHub

```bash
cd riskitbot
git init
git add .
git commit -m "Initial RiskIt Bot (basic anonymous photo posting)"
git remote add origin https://github.com/YOUR_USERNAME/riskit-bot.git
git branch -M main
git push -u origin main
```

### 3. Deploy on Railway

1. Go to [Railway](https://railway.app) and log in (GitHub login is easiest).
2. Click **New Project** → **Deploy from GitHub repo**.
3. Select your `riskit-bot` repository.
4. Railway will detect the `Dockerfile` (or Python + Procfile) and start a deploy.

### 4. Set Environment Variables in Railway

In your Railway project → your service → **Variables** tab, add:

| Variable                | Required | Example                          | Notes |
|-------------------------|----------|----------------------------------|-------|
| `TELEGRAM_BOT_TOKEN`    | Yes      | `123456:ABCdef...`               | From @BotFather |
| `GROUP_CHAT_ID`         | Yes      | `-1001234567890`                 | Group or Channel ID (see below) |
| `ADMIN_USER_ID`         | No       | `5815775162`                     | Your user ID from @userinfobot |
| `COOLDOWN_SECONDS`      | No       | `300`                            | 5 minutes default |
| `DAILY_LIMIT`           | No       | `10`                             | Posts per user per day |
| `DATA_DIR`              | No       | `/app/data`                      | Important for persistence |

**How to get `GROUP_CHAT_ID`**:
- Add the bot to your target **supergroup** or **channel** as admin
- Send any message there
- Forward that message to [@userinfobot](https://t.me/userinfobot) or visit:
  `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`

### 5. Persistent Storage (Rate Limits) on Railway

By default Railway containers are **ephemeral** — rate limit data will reset on every deploy/restart.

**Recommended**:
1. In Railway, go to your service → **Settings** → **Volumes**
2. Create a new Volume
3. Mount path: `/app/data`
4. Set environment variable `DATA_DIR=/app/data` (already the default in the image)

This way `rate_limits.json` survives restarts and deploys.

### 6. After First Deploy

- Railway will give you a public URL (ignore it — this bot is polling only).
- The service should stay awake on paid plans. On free tier it may sleep after inactivity.
- Check the **Deploy Logs** tab for startup messages (you should see your Group Chat ID logged).

---

## Local Development

```bash
# 1. Copy example env
cp .env.example .env

# 2. Edit .env with your real values

# 3. Install & run
pip install -r requirements.txt
python bot.py
```

Or with Docker Compose (for local testing with persistence):

```bash
docker compose up -d --build
docker compose logs -f
```

---

## How the Flow Works

1. User sends `/start` (or taps a menu button) → sees explanation of the bot + where leaks go + always-visible buttons
2. Choose leak type from the persistent bottom menu:
   - 📸 Leak Photo → send photos (batch supported)
   - 📧 Leak Email / 📱 Leak Phone / 📷 Leak Instagram → send the info as text
3. Bot asks for confirmation (with clear warning that it will be posted anonymously to the target group/channel)
4. Confirm once → bot posts it anonymously (photos individually, text as labeled messages)
5. Keyboard stays visible for next leak

Everything is posted from the **bot account** — no trace to the sender. Rate limits apply per item / per photo in batch.

---

## Commands

| Command   | Where     | What it does                        |
|-----------|-----------|-------------------------------------|
| `/start`  | Private   | Show rules and instructions         |
| `/rules`  | Private   | Re-show the rules                   |
| `/cancel` | Private   | Discard a photo waiting for confirmation |

---

## File Structure

```
riskitbot/
├── .env.example           # Template — copy to .env for local dev
├── .gitignore
├── Dockerfile
├── Procfile               # For Railway (non-Docker) worker process
├── README.md
├── bot.py                 # All the logic
├── docker-compose.yml
├── requirements.txt
└── data/                  # Created at runtime (rate_limits.json lives here)
```

---

## Environment Variables Reference

All configuration is done via environment variables (perfect for Railway, Render, Fly.io, etc.).

See the table in the "Deploy on Railway" section above.

---

## Future Ideas (after this basic version works)

- AI image enhancement / variations before or after posting
- "Risk Score" or community reactions → leaderboard
- Battles / voting rounds
- Personal vault of posted images
- Video support
- Admin review queue before posting
- SQLite + proper history

---

## Notes & Safety

- The anonymity is real because we use `send_photo` with the bot's credentials, not message forwarding.
- Only accept photos in **private** chats.
- You are responsible for moderating the group.
- This is intentionally minimal — add complexity only after the core flow is stable.

---

## Troubleshooting

- **"Missing required environment variable"** → You forgot to set `TELEGRAM_BOT_TOKEN` or `GROUP_CHAT_ID` in Railway Variables.
- **Posts fail with permission error** → Bot is not admin or is missing "Post Messages" in the supergroup.
- **Rate limits reset every deploy** → You are not using a Railway Volume at `/app/data`.
- **Bot doesn't respond** → Make sure you're messaging the bot in a **private** 1:1 chat, not in a group.

---

Ready to go live? Push to GitHub → connect on Railway → set the two required variables + attach a Volume.

Once it's running smoothly, we can start building the AI layer on top.
