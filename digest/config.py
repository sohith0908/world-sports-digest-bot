from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int = 0) -> int:
    raw = _env(name, str(default))
    if not raw:
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    discord_token: str
    channel_id: int
    guild_id: int
    mod_channel_id: int
    gemini_api_key: str
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    gemini_model: str
    football_data_api_key: str
    thesportsdb_api_key: str
    require_human_review: bool
    spoiler_scores: bool
    role_ping_id: int
    hinglish_india: bool
    thread_details: bool
    feed_cache_minutes: int
    log_level: str
    tz: ZoneInfo


settings: Settings | None = None


def load_settings(*, require_runtime: bool = True) -> Settings:
    global settings
    token = _env("DISCORD_TOKEN")
    channel_id = _env_int("CHANNEL_ID", 0)
    guild_id = _env_int("GUILD_ID", 0)

    if require_runtime:
        if not token or token in {"paste-your-token-here", "paste-your-bot-token-here"}:
            raise SystemExit("Set DISCORD_TOKEN in .env")
        if channel_id <= 0:
            raise SystemExit("Set CHANNEL_ID in .env (Discord channel snowflake)")
        if guild_id <= 0:
            raise SystemExit("Set GUILD_ID in .env (Discord server snowflake)")

    loaded = Settings(
        discord_token=token,
        channel_id=channel_id,
        guild_id=guild_id,
        mod_channel_id=_env_int("MOD_CHANNEL_ID", 0),
        gemini_api_key=_env("GEMINI_API_KEY"),
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_base_url=_env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        openai_model=_env("OPENAI_MODEL", "gpt-4o-mini"),
        gemini_model=_env("GEMINI_MODEL"),
        football_data_api_key=_env("FOOTBALL_DATA_API_KEY"),
        thesportsdb_api_key=_env("THESPORTSDB_API_KEY", "123") or "123",
        require_human_review=_env_bool("REQUIRE_HUMAN_REVIEW", False),
        spoiler_scores=_env_bool("SPOILER_SCORES", False),
        role_ping_id=_env_int("ROLE_PING_ID", 0),
        hinglish_india=_env_bool("HINGLISH_INDIA", False),
        thread_details=_env_bool("THREAD_DETAILS", True),
        feed_cache_minutes=max(1, _env_int("FEED_CACHE_MINUTES", 8)),
        log_level=_env("LOG_LEVEL", "INFO").upper() or "INFO",
        tz=ZoneInfo("Asia/Kolkata"),
    )
    settings = loaded
    return loaded


def get_settings() -> Settings:
    global settings
    if settings is None:
        return load_settings(require_runtime=False)
    return settings
