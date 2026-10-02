# D_BOT — World Sports Highlights

Discord bot that posts a daily worldwide sports highlights digest from yesterday’s news.

## Setup

1. Create a Discord bot at [Discord Developer Portal](https://discord.com/developers/applications), invite it to your server with message permissions.
2. Copy `.env.example` → `.env` and fill in values.
3. Install and run:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Commands

- `/news` — build and post the digest now

Daily post runs at **08:00 IST**; health report at **08:10 IST** (to `MOD_CHANNEL_ID` if set).

## Env vars

| Variable | Required | Purpose |
|----------|----------|---------|
| `DISCORD_TOKEN` | yes | Bot token |
| `CHANNEL_ID` | yes | Channel for digests |
| `GUILD_ID` | yes | Server ID (slash-command sync) |
| `GEMINI_API_KEY` | recommended | Summaries / fact-check |
| `MOD_CHANNEL_ID` | no | Health / review channel |
| `REQUIRE_HUMAN_REVIEW` | no | `1` = mod Approve/Reject buttons |
| `SPOILER_SCORES` | no | `1` = spoiler-tag scores |
