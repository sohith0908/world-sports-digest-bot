from __future__ import annotations

import os

import discord

from digest.db import get_pending, mark_posted, set_pending_status
from digest.feeds import SPORT_COLORS, SPORT_EMOJI
from digest.textutil import clean_text


def _review_id_from_message(message: discord.Message | None) -> int | None:
    if not message or not message.embeds:
        return None
    footer = message.embeds[0].footer.text or ""
    if "id=" not in footer:
        return None
    try:
        return int(footer.split("id=")[-1].strip())
    except Exception:
        return None


class PersistentReviewView(discord.ui.View):
    """Persistent approve/reject buttons for extreme-claim reviews."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
        custom_id="digest:mod:approve",
    )
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        review_id = _review_id_from_message(interaction.message)
        item = get_pending(review_id) if review_id else None
        if not item:
            await interaction.followup.send("Already handled or missing.", ephemeral=True)
            return

        channel_id = int(os.getenv("CHANNEL_ID", "0"))
        channel = interaction.client.get_channel(channel_id)
        if channel is None:
            channel = await interaction.client.fetch_channel(channel_id)

        emoji = SPORT_EMOJI.get(item["sport"], "")
        desc = clean_text(item["key_fact"])
        if item.get("why_it_matters"):
            desc += f"\nWhy it matters: {clean_text(item['why_it_matters'])}"
        desc += f"\n[Read more]({item['url']})"
        embed = discord.Embed(
            title=clean_text(f"{emoji} {item['sport'].upper()}"),
            description=f"**{clean_text(item['headline'])}**\n{desc}",
            color=SPORT_COLORS.get(item["sport"], 0x5865F2),
        )
        await channel.send(content="Approved from review queue:", embeds=[embed])
        mark_posted(
            item["url"],
            item.get("article_title") or item["headline"],
            item["sport"],
            item["headline"],
        )
        set_pending_status(review_id, "approved")
        await interaction.followup.send("Posted to the news channel.", ephemeral=True)
        try:
            await interaction.message.edit(
                content=f"Approved by {interaction.user}",
                view=None,
            )
        except Exception:
            pass

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.danger,
        custom_id="digest:mod:reject",
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        review_id = _review_id_from_message(interaction.message)
        if not review_id or not get_pending(review_id):
            await interaction.followup.send("Already handled or missing.", ephemeral=True)
            return
        set_pending_status(review_id, "rejected")
        await interaction.followup.send("Rejected.", ephemeral=True)
        try:
            await interaction.message.edit(
                content=f"Rejected by {interaction.user}",
                view=None,
            )
        except Exception:
            pass


def review_embed(item: dict) -> discord.Embed:
    emoji = SPORT_EMOJI.get(item["sport"], "")
    why = item.get("why_it_matters") or "NONE"
    embed = discord.Embed(
        title=f"Review needed · {emoji} {item['sport']}",
        description=(
            f"**{clean_text(item['headline'])}**\n"
            f"{clean_text(item['key_fact'])}\n"
            f"Why it matters: {clean_text(why)}\n"
            f"Flags: `{item.get('flags', '')}`\n"
            f"[Source]({item['url']})"
        ),
        color=0xFFA000,
    )
    embed.set_footer(text=f"pending review id={item['id']}")
    return embed
