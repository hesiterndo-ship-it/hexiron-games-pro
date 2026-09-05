import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _bool_env(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = _int_env("OWNER_ID", 0)
# Additional admins besides OWNER_ID (comma separated numeric ids)
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
if OWNER_ID:
    ADMIN_IDS.add(OWNER_ID)

CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "").strip()

# --- HEXIRON SALES integration -------------------------------------------
# Must match the real hexiron-sales API: GET {SALES_API_URL}/api/v1/license
#   ?product=<PRODUCT_ID>&group_id=<chat_id>   header X-API-Key
SALES_API_URL = os.getenv("SALES_API_URL", "").rstrip("/")
SALES_API_KEY = os.getenv("SALES_API_KEY", "").strip()
# Product id as registered inside HEXIRON SALES admin panel (Products section).
# An admin must create a product with this exact id there before licenses work.
SALES_PRODUCT_ID = os.getenv("SALES_PRODUCT_ID", "hexiron-games").strip()
# Username (no @) of the HEXIRON SALES telegram bot, used to build the buy link.
SALES_BOT_USERNAME = os.getenv("SALES_BOT_USERNAME", "").strip().lstrip("@")
LICENSE_CACHE_TTL_SECONDS = _int_env("LICENSE_CACHE_TTL_SECONDS", 300)

DB_PATH = Path(os.getenv("DB_PATH", "data/hexiron_games.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

BRAND_NAME = os.getenv("BRAND_NAME", "HEXIRON GAMES")
FREE_GAMES = {x.strip() for x in os.getenv("FREE_GAMES", "ttt,truth").split(",") if x.strip()}
COINS_PER_DAILY = _int_env("COINS_PER_DAILY", 25)
REFERRAL_REWARD = _int_env("REFERRAL_REWARD", 100)

# --- Timed phases for social games (seconds) ------------------------------
MAFIA_NIGHT_SECONDS = _int_env("MAFIA_NIGHT_SECONDS", 45)
MAFIA_DAY_SECONDS = _int_env("MAFIA_DAY_SECONDS", 60)
WEREWOLF_NIGHT_SECONDS = _int_env("WEREWOLF_NIGHT_SECONDS", 45)
WEREWOLF_DAY_SECONDS = _int_env("WEREWOLF_DAY_SECONDS", 60)
ROOM_IDLE_TIMEOUT_SECONDS = _int_env("ROOM_IDLE_TIMEOUT_SECONDS", 900)

# Broadcast throttling so we don't hit Telegram's flood limits
BROADCAST_BATCH_SIZE = _int_env("BROADCAST_BATCH_SIZE", 25)
BROADCAST_BATCH_DELAY = _int_env("BROADCAST_BATCH_DELAY", 1)
