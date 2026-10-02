import os
from datetime import time
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from digest.logging_util import setup_logging
from digest.pipeline import health_message, post_digest
from digest.review import PersistentReviewView, review_embed

load_dotenv()
log = setup_logging()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
MOD_CHANNEL_ID = int(os.getenv("MOD_CHANNEL_ID", "0") or "0")
GUILD_ID = int(os.getenv("GUILD_ID", "1555066072200257697"))
TZ = ZoneInfo("Asia/Kolkata")
POST_AT = time(hour=8, minute=0, tzinfo=TZ)
HEALTH_AT = time(hour=8, minute=10, tzinfo=TZ)
GUILD = discord.Object(id=GUILD_ID)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
_synced = False
_last_health: dict | None = None


async def send_pack(header: str, embeds: list[discord.Embed]):
    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    await channel.send(content=header, embeds=embeds[:10])


async def send_text(text: str):
    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    await channel.send(text)


async def send_review(item: dict):
    if not MOD_CHANNEL_ID:
        log.warning(
            "review_queued_no_mod_channel id=%s flags=%s",
            item.get("id"),
            item.get("flags"),
        )
        return
    channel = bot.get_channel(MOD_CHANNEL_ID) or await bot.fetch_channel(MOD_CHANNEL_ID)
    await channel.send(embed=review_embed(item), view=PersistentReviewView())


async def alert_mod(text: str):
    if not MOD_CHANNEL_ID:
        log.error("alert_mod_no_channel %s", text)
        return
    channel = bot.get_channel(MOD_CHANNEL_ID) or await bot.fetch_channel(MOD_CHANNEL_ID)
    await channel.send(text)


@bot.tree.command(
    name="news",
    description="Worldwide sports highlights from yesterday",
    guild=GUILD,
)
async def news_command(interaction: discord.Interaction):
    global _last_health
    await interaction.response.defer(thinking=True)

    async def pack(header, embeds):
        await interaction.followup.send(content=header, embeds=embeds[:10])

    async def text(msg):
        await interaction.followup.send(msg)

    try:
        stats = await post_digest(pack, text, send_review, notify_empty=True)
        _last_health = stats
        if stats.get("error"):
            await alert_mod(f"`/news` failed: `{stats['error']}`")
            await interaction.followup.send("Digest failed. Mods were alerted.")
    except Exception:
        log.exception("news_command_failed")
        await alert_mod("`/news` crashed. Check digest.log.")
        await interaction.followup.send("Could not build the digest. Mods were alerted.")


@tasks.loop(time=POST_AT)
async def daily_post():
    global _last_health
    try:
        stats = await post_digest(send_pack, send_text, send_review, notify_empty=False)
        _last_health = stats
        if stats.get("error"):
            await alert_mod(f"Daily digest failed quietly: `{stats['error']}`")
    except Exception:
        log.exception("daily_post_failed")
        await alert_mod("Daily digest crashed. Check `digest.log`.")


@tasks.loop(time=HEALTH_AT)
async def daily_health():
    try:
        await alert_mod(health_message(_last_health))
    except Exception:
        log.exception("daily_health_failed")


@daily_post.before_loop
async def before_daily_post():
    await bot.wait_until_ready()


@daily_health.before_loop
async def before_daily_health():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    global _synced
    print(f"Logged in as {bot.user}")
    bot.add_view(PersistentReviewView())

    # Guild sync overwrites old commands, so /subscribe etc. disappear.
    if not _synced:
        synced = await bot.tree.sync(guild=GUILD)
        _synced = True
        print(f"Synced {len(synced)} command(s) for guild {GUILD_ID}")

    print(f"Daily post {POST_AT.strftime('%H:%M')} IST · health {HEALTH_AT.strftime('%H:%M')} IST")
    if not daily_post.is_running():
        daily_post.start()
    if not daily_health.is_running():
        daily_health.start()


def main():
    if not TOKEN or TOKEN == "paste-your-token-here":
        raise SystemExit("Set DISCORD_TOKEN in .env")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
