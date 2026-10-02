from datetime import time

import discord
from discord import app_commands
from discord.ext import commands, tasks
import httpx

from digest.config import load_settings
from digest.logging_util import setup_logging
from digest.pipeline import health_message, ping_ai_health, post_digest
from digest.review import PersistentReviewView, review_embed

settings = load_settings(require_runtime=True)
log = setup_logging()

TOKEN = settings.discord_token
CHANNEL_ID = settings.channel_id
MOD_CHANNEL_ID = settings.mod_channel_id
GUILD_ID = settings.guild_id
TZ = settings.tz
POST_AT = time(hour=8, minute=0, tzinfo=TZ)
HEALTH_AT = time(hour=8, minute=10, tzinfo=TZ)
GUILD = discord.Object(id=GUILD_ID)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
_synced = False
_last_health: dict | None = None


def _role_prefix() -> str:
    if settings.role_ping_id:
        return f"<@&{settings.role_ping_id}> "
    return ""


async def send_pack(header: str, embeds: list[discord.Embed], details: list[discord.Embed]):
    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    content = f"{_role_prefix()}{header}".strip()
    msg = await channel.send(content=content, embeds=(embeds or details)[:10])
    if details and settings.thread_details:
        try:
            thread = await msg.create_thread(name="Sport details", auto_archive_duration=1440)
            # Discord: max 10 embeds per message — chunk if needed.
            for i in range(0, len(details), 10):
                await thread.send(embeds=details[i : i + 10])
        except Exception:
            log.exception("thread_details_failed")
            if embeds and details:
                for i in range(0, len(details), 10):
                    await channel.send(embeds=details[i : i + 10])


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


async def startup_health_check():
    problems: list[str] = []
    try:
        channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
        if channel is None:
            problems.append("CHANNEL_ID unreachable")
    except Exception as exc:
        problems.append(f"CHANNEL_ID error: {type(exc).__name__}")

    if MOD_CHANNEL_ID:
        try:
            mod = bot.get_channel(MOD_CHANNEL_ID) or await bot.fetch_channel(MOD_CHANNEL_ID)
            if mod is None:
                problems.append("MOD_CHANNEL_ID unreachable")
        except Exception as exc:
            problems.append(f"MOD_CHANNEL_ID error: {type(exc).__name__}")

    ok, detail = await bot.loop.run_in_executor(None, ping_ai_health)
    if not ok:
        problems.append(f"AI health failed: {detail}")
    else:
        log.info("startup_ai_ok %s", detail)

    # Lightweight outbound check.
    try:
        with httpx.Client(timeout=8.0) as client:
            client.get("https://feeds.bbci.co.uk/sport/rss.xml")
    except Exception as exc:
        problems.append(f"RSS reachability: {type(exc).__name__}")

    if problems:
        await alert_mod("**Startup health issues**\n" + "\n".join(f"- {p}" for p in problems))
    else:
        log.info("startup_health_ok")


@bot.tree.command(
    name="news",
    description="Worldwide sports highlights from yesterday",
    guild=GUILD,
)
@app_commands.describe(
    mode="post = normal channel post; dry-run = preview to mod only; refresh = bypass feed cache",
)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="post", value="post"),
        app_commands.Choice(name="dry-run", value="dry-run"),
        app_commands.Choice(name="refresh", value="refresh"),
    ]
)
async def news_command(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str] | None = None,
):
    global _last_health
    await interaction.response.defer(thinking=True)
    mode_val = mode.value if mode else "post"
    dry_run = mode_val == "dry-run"
    bypass = mode_val == "refresh"

    status_msg = await interaction.followup.send("Starting digest…", wait=True)

    async def progress(msg: str):
        try:
            await status_msg.edit(content=msg)
        except Exception:
            pass

    async def pack(header, embeds, details):
        content = f"{_role_prefix()}{header}".strip()
        if dry_run:
            target = (
                bot.get_channel(MOD_CHANNEL_ID)
                or await bot.fetch_channel(MOD_CHANNEL_ID)
                if MOD_CHANNEL_ID
                else None
            )
            if target is None:
                await interaction.followup.send("Dry-run needs MOD_CHANNEL_ID in .env")
                return
            preview = f"**DRY RUN**\n{content}"
            msg = await target.send(content=preview, embeds=(embeds or details)[:10])
            if details and settings.thread_details:
                try:
                    thread = await msg.create_thread(
                        name="Dry-run sport details", auto_archive_duration=1440
                    )
                    for i in range(0, len(details), 10):
                        await thread.send(embeds=details[i : i + 10])
                except Exception:
                    log.exception("dry_run_thread_failed")
            await interaction.followup.send("Dry-run posted to mod channel.")
            return

        channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
        msg = await channel.send(content=content, embeds=(embeds or details)[:10])
        if details and settings.thread_details:
            try:
                thread = await msg.create_thread(
                    name="Sport details", auto_archive_duration=1440
                )
                for i in range(0, len(details), 10):
                    await thread.send(embeds=details[i : i + 10])
            except Exception:
                log.exception("thread_details_failed")
        await status_msg.edit(content="Digest posted.")

    async def text(msg):
        await interaction.followup.send(msg)

    try:
        stats = await post_digest(
            pack,
            text,
            None if dry_run else send_review,
            notify_empty=True,
            bypass_cache=bypass,
            dry_run=dry_run,
            on_progress=progress,
        )
        _last_health = stats
        if stats.get("error"):
            await alert_mod(f"`/news` failed: `{stats['error']}`")
            await status_msg.edit(content="Digest failed. Mods were alerted.")
    except Exception:
        log.exception("news_command_failed")
        await alert_mod("`/news` crashed. Check digest.log.")
        await status_msg.edit(content="Could not build the digest. Mods were alerted.")


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

    if not _synced:
        synced = await bot.tree.sync(guild=GUILD)
        _synced = True
        print(f"Synced {len(synced)} command(s) for guild {GUILD_ID}")

    print(f"Daily post {POST_AT.strftime('%H:%M')} IST · health {HEALTH_AT.strftime('%H:%M')} IST")
    if not daily_post.is_running():
        daily_post.start()
    if not daily_health.is_running():
        daily_health.start()

    await startup_health_check()


def main():
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
