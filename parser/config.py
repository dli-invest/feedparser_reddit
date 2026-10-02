import os
import re
from dataclasses import dataclass, field
from typing import List, Optional
import yaml

@dataclass
class RedditConfig:
    username: str
    password: str
    user_data_dir: str = "./chrome_profile"
    headless: bool = False

@dataclass
class SubredditRule:
    name: str
    sort: str = "hot"
    limit: int = 25

@dataclass
class FilterConfig:
    min_score: int = 10
    min_comments: int = 5
    keywords: List[str] = field(default_factory=list)
    tickers: List[str] = field(default_factory=list)

@dataclass
class DiscordConfig:
    enabled: bool = False
    webhook_url: str = ""
    embed_color: int = 3447003

@dataclass
class StorageConfig:
    csv_file: str = "run_results.csv"

@dataclass
class AppConfig:
    reddit: RedditConfig
    subreddits: List[SubredditRule]
    filters: FilterConfig
    discord: DiscordConfig
    storage: StorageConfig


def _resolve_env_vars(data):
    """Interpolate ${ENV_VAR} syntax in YAML."""
    if isinstance(data, dict):
        return {k: _resolve_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_resolve_env_vars(i) for i in data]
    elif isinstance(data, str):
        matches = re.findall(r"\$\{([A-Za-z0-9_]+)\}", data)
        for env_var in matches:
            data = data.replace(f"${{{env_var}}}", os.getenv(env_var, ""))
        return data
    return data


def load_config(path: str) -> AppConfig:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f)

    data = _resolve_env_vars(raw_data)

    reddit_cfg = RedditConfig(
        username=data.get("reddit", {}).get("username", ""),
        password=data.get("reddit", {}).get("password", ""),
        user_data_dir=data.get("reddit", {}).get("user_data_dir", "./chrome_profile"),
        headless=data.get("reddit", {}).get("headless", False),
    )

    subreddits = [
        SubredditRule(
            name=s.get("name"),
            sort=s.get("sort", "hot"),
            limit=s.get("limit", 25)
        )
        for s in data.get("subreddits", [])
    ]

    filter_cfg = FilterConfig(
        min_score=data.get("filters", {}).get("min_score", 0),
        min_comments=data.get("filters", {}).get("min_comments", 0),
        keywords=[k.lower() for k in data.get("filters", {}).get("keywords", [])],
        tickers=[t.upper() for t in data.get("filters", {}).get("tickers", [])]
    )

    discord_cfg = DiscordConfig(
        enabled=data.get("discord", {}).get("enabled", False),
        webhook_url=data.get("discord", {}).get("webhook_url", ""),
        embed_color=data.get("discord", {}).get("embed_color", 3447003),
    )

    storage_cfg = StorageConfig(
        csv_file=data.get("storage", {}).get("csv_file", "run_results.csv")
    )

    return AppConfig(
        reddit=reddit_cfg,
        subreddits=subreddits,
        filters=filter_cfg,
        discord=discord_cfg,
        storage=storage_cfg
    )