# world-sports-digest-bot

Discord bot that posts a daily **worldwide sports highlights** digest from yesterday’s news — AI summaries, fact checks, World Highlights roundup, and sport-by-sport sections.

## Setup

1. Create a Discord bot at the [Developer Portal](https://discord.com/developers/applications) and invite it with message + embed permissions.
2. Copy `.env.example` → `.env` and fill in values (`DISCORD_TOKEN`, `CHANNEL_ID`, `GUILD_ID` required).
3. Install and run:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

### Docker (keeps the bot online)

```bash
docker compose up -d --build
```

Or deploy the `Procfile` worker on Railway/Render/Fly with your `.env` vars.

## Commands

- `/news` — post digest now  
- `/news mode:dry-run` — preview to mod channel only (no mark-as-posted)  
- `/news mode:refresh` — bypass feed cache and rebuild  

Daily post: **08:00 IST**. Health report: **08:10 IST** (to `MOD_CHANNEL_ID`).

## Useful env vars

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` | Summaries / fact-check |
| `MOD_CHANNEL_ID` | Health, dry-run, review |
| `THREAD_DETAILS` | `1` = per-sport embeds in a thread |
| `ROLE_PING_ID` | Optional role to ping on post |
| `HINGLISH_INDIA` | `1` = short Hinglish line on India stories |
| `FEED_CACHE_MINUTES` | RSS cache TTL (default 8) |
| `FOOTBALL_DATA_API_KEY` | Stronger football results |
| `LOG_LEVEL` | `INFO` or `DEBUG` |

## Tests

```bash
python tools/test_world_digest.py
```
