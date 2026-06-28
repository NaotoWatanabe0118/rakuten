import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from src.exceptions import ConfigError


@dataclass
class Config:
    rakuten_username: str
    rakuten_password: str
    discord_webhook_url: str
    poll_interval_seconds: int
    headless: bool
    chrome_user_data_dir: str
    selenium_page_timeout: int
    searches_file: Path
    state_file: Path
    cookies_file: Path
    log_level: str
    log_file: str
    dry_run: bool = False


def load_config() -> Config:
    load_dotenv()

    missing = [
        v for v in ("RAKUTEN_USERNAME", "RAKUTEN_PASSWORD", "DISCORD_WEBHOOK_URL")
        if not os.environ.get(v)
    ]
    if missing:
        raise ConfigError(f"必須環境変数が未設定です: {', '.join(missing)}")

    return Config(
        rakuten_username=os.environ["RAKUTEN_USERNAME"],
        rakuten_password=os.environ["RAKUTEN_PASSWORD"],
        discord_webhook_url=os.environ["DISCORD_WEBHOOK_URL"],
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
        headless=os.environ.get("HEADLESS", "1") == "1",
        chrome_user_data_dir=os.environ.get("CHROME_USER_DATA_DIR", "/tmp/chrome_profile"),
        selenium_page_timeout=int(os.environ.get("SELENIUM_PAGE_TIMEOUT", "30")),
        searches_file=Path(os.environ.get("SEARCHES_FILE", "data/searches.json")),
        state_file=Path(os.environ.get("STATE_FILE", "data/stock_state.json")),
        cookies_file=Path(os.environ.get("COOKIES_FILE", "data/cookies.json")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        log_file=os.environ.get("LOG_FILE", "logs/bot.log"),
        dry_run=os.environ.get("DRY_RUN", "0") == "1",
    )


def setup_logging(config: Config) -> logging.Logger:
    log_dir = Path(config.log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, config.log_level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.log_file, encoding="utf-8"),
        ],
    )
    return logging.getLogger("rakuten_bot")
