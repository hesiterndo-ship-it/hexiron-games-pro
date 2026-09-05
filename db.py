import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from config import DB_PATH


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=15000")
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          username TEXT DEFAULT '',
          xp INTEGER NOT NULL DEFAULT 0,
          coins INTEGER NOT NULL DEFAULT 0,
          wins INTEGER NOT NULL DEFAULT 0,
          games INTEGER NOT NULL DEFAULT 0,
          referrals INTEGER NOT NULL DEFAULT 0,
          referred_by INTEGER,
          last_daily TEXT,
          banned INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          kind TEXT NOT NULL,
          xp INTEGER NOT NULL DEFAULT 0,
          coins INTEGER NOT NULL DEFAULT 0,
          meta TEXT DEFAULT '',
          created_at TEXT NOT NULL
        );
        -- One HEXIRON GAMES license per Telegram chat (group or private DM),
        -- mirroring HEXIRON SALES' own (product_id, group_id) license model.
        -- A single purchase unlocks every Premium game inside that chat.
        CREATE TABLE IF NOT EXISTS group_licenses(
          chat_id INTEGER PRIMARY KEY,
          active INTEGER NOT NULL DEFAULT 0,
          expires_at INTEGER,
          plan_name TEXT DEFAULT '',
          source TEXT NOT NULL DEFAULT 'sales',
          checked_at INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS referrals(
          referrer_id INTEGER NOT NULL,
          referred_id INTEGER PRIMARY KEY,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_users_xp ON users(xp DESC);
        CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, created_at DESC);
        """)
        _migrate_legacy_licenses(c)


def _migrate_legacy_licenses(c):
    """Best-effort cleanup if an older per-user/per-game `licenses` table exists."""
    row = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='licenses'"
    ).fetchone()
    if row:
        try:
            c.execute("DROP TABLE licenses")
        except sqlite3.Error:
            pass


def upsert_user(user_id, name, username=""):
    t = now()
    with conn() as c:
        c.execute("""INSERT INTO users(user_id,name,username,created_at,updated_at)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(user_id) DO UPDATE SET
                     name=excluded.name, username=excluded.username, updated_at=excluded.updated_at""",
                  (user_id, name[:120], username[:120], t, t))


def get_user(user_id):
    with conn() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def is_banned(user_id) -> bool:
    with conn() as c:
        row = c.execute("SELECT banned FROM users WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and row["banned"])


def set_banned(user_id, banned: bool):
    with conn() as c:
        c.execute("UPDATE users SET banned=?,updated_at=? WHERE user_id=?",
                  (1 if banned else 0, now(), user_id))
        return c.total_changes > 0


def reward(user_id, xp=0, coins=0, win=False, kind="game", meta=""):
    with conn() as c:
        c.execute("""UPDATE users SET xp=xp+?, coins=coins+?, games=games+1,
                     wins=wins+?, updated_at=? WHERE user_id=?""",
                  (xp, coins, 1 if win else 0, now(), user_id))
        c.execute("""INSERT INTO events(user_id,kind,xp,coins,meta,created_at)
                     VALUES(?,?,?,?,?,?)""", (user_id, kind, xp, coins, meta, now()))


def add_coins(user_id, coins, kind="coins", meta=""):
    with conn() as c:
        c.execute("UPDATE users SET coins=coins+?,updated_at=? WHERE user_id=?",
                  (coins, now(), user_id))
        c.execute("""INSERT INTO events(user_id,kind,coins,meta,created_at)
                     VALUES(?,?,?,?,?)""", (user_id, kind, coins, meta, now()))


def claim_daily(user_id, amount):
    today = datetime.now(timezone.utc).date().isoformat()
    with conn() as c:
        row = c.execute(
            "SELECT last_daily FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if row and row["last_daily"] == today:
            return False
        updated = c.execute(
            """UPDATE users SET coins=coins+?, last_daily=?, updated_at=?
               WHERE user_id=? AND (last_daily IS NULL OR last_daily<>?)""",
            (amount, today, now(), user_id, today),
        )
        if updated.rowcount == 0:
            return False
        c.execute("""INSERT INTO events(user_id,kind,coins,created_at)
                     VALUES(?,?,?,?)""", (user_id, "daily", amount, now()))
        return True


def leaderboard(limit=10):
    with conn() as c:
        return c.execute("""SELECT name,xp,coins,wins,games FROM users
                            ORDER BY xp DESC,wins DESC LIMIT ?""", (limit,)).fetchall()


def rank_of(user_id):
    with conn() as c:
        row = c.execute("SELECT xp FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return 0
        return c.execute("SELECT COUNT(*)+1 FROM users WHERE xp>?", (row["xp"],)).fetchone()[0]


def set_referral(referrer, referred):
    if referrer == referred:
        return False
    with conn() as c:
        exists = c.execute("SELECT 1 FROM referrals WHERE referred_id=?", (referred,)).fetchone()
        if exists:
            return False
        if not c.execute("SELECT 1 FROM users WHERE user_id=?", (referrer,)).fetchone():
            return False
        c.execute("INSERT INTO referrals(referrer_id,referred_id,created_at) VALUES(?,?,?)",
                  (referrer, referred, now()))
        c.execute("UPDATE users SET referred_by=?,updated_at=? WHERE user_id=?",
                  (referrer, now(), referred))
        c.execute("UPDATE users SET referrals=referrals+1 WHERE user_id=?", (referrer,))
        return True


# --- Group licenses (HEXIRON SALES integration) ---------------------------

def license_cached(chat_id) -> bool:
    with conn() as c:
        row = c.execute(
            "SELECT active,expires_at FROM group_licenses WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if not row or not row["active"]:
            return False
        if row["expires_at"] and row["expires_at"] < int(time.time()):
            return False
        return True


def license_cache_fresh(chat_id, ttl_seconds) -> bool:
    """True if we checked the Sales API recently enough to skip a network call."""
    with conn() as c:
        row = c.execute(
            "SELECT checked_at FROM group_licenses WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return bool(row and (int(time.time()) - row["checked_at"]) < ttl_seconds)


def cache_license(chat_id, active, expires_at=None, plan_name="", source="sales"):
    with conn() as c:
        c.execute("""INSERT INTO group_licenses(chat_id,active,expires_at,plan_name,source,checked_at,updated_at)
                     VALUES(?,?,?,?,?,?,?)
                     ON CONFLICT(chat_id) DO UPDATE SET
                     active=excluded.active, expires_at=excluded.expires_at,
                     plan_name=excluded.plan_name, source=excluded.source,
                     checked_at=excluded.checked_at, updated_at=excluded.updated_at""",
                  (chat_id, 1 if active else 0, expires_at, plan_name, source,
                   int(time.time()), now()))


def manual_grant_license(chat_id, active: bool, days: int = 30):
    expires_at = int(time.time()) + days * 86400 if active else None
    cache_license(chat_id, active, expires_at, plan_name="manual (admin)", source="manual")


def all_user_ids():
    with conn() as c:
        return [r["user_id"] for r in c.execute("SELECT user_id FROM users WHERE banned=0").fetchall()]


def stats_overview():
    with conn() as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        banned = c.execute("SELECT COUNT(*) FROM users WHERE banned=1").fetchone()[0]
        games_played = c.execute("SELECT COALESCE(SUM(games),0) FROM users").fetchone()[0]
        active_licenses = c.execute(
            "SELECT COUNT(*) FROM group_licenses WHERE active=1 AND (expires_at IS NULL OR expires_at>?)",
            (int(time.time()),),
        ).fetchone()[0]
        today = datetime.now(timezone.utc).date().isoformat()
        daily_claims = c.execute(
            "SELECT COUNT(*) FROM users WHERE last_daily=?", (today,)
        ).fetchone()[0]
        return {
            "users": users,
            "banned": banned,
            "games_played": games_played,
            "active_licenses": active_licenses,
            "daily_claims_today": daily_claims,
        }
