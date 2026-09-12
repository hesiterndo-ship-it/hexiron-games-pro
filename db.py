import sqlite3
import time
import json
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
          daily_streak INTEGER NOT NULL DEFAULT 0,
          best_daily_streak INTEGER NOT NULL DEFAULT 0,
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
        CREATE TABLE IF NOT EXISTS game_stats(
          user_id INTEGER NOT NULL,
          game TEXT NOT NULL,
          played INTEGER NOT NULL DEFAULT 0,
          wins INTEGER NOT NULL DEFAULT 0,
          losses INTEGER NOT NULL DEFAULT 0,
          draws INTEGER NOT NULL DEFAULT 0,
          xp_earned INTEGER NOT NULL DEFAULT 0,
          coins_earned INTEGER NOT NULL DEFAULT 0,
          last_played TEXT,
          PRIMARY KEY(user_id, game)
        );
        CREATE INDEX IF NOT EXISTS idx_game_stats_game ON game_stats(game, wins DESC);
        CREATE TABLE IF NOT EXISTS achievements(
          user_id INTEGER NOT NULL,
          achievement TEXT NOT NULL,
          unlocked_at TEXT NOT NULL,
          PRIMARY KEY(user_id, achievement)
        );
        CREATE INDEX IF NOT EXISTS idx_achievements_user ON achievements(user_id);
        """)
        _migrate_legacy_licenses(c)
        _migrate_users(c)


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



def _migrate_users(c):
    """Migration-safe additions for databases created by older releases."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    if "daily_streak" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN daily_streak INTEGER NOT NULL DEFAULT 0")
    if "best_daily_streak" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN best_daily_streak INTEGER NOT NULL DEFAULT 0")


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


def reward(user_id, xp=0, coins=0, win=False, kind="game", meta="", game=None, draw=False):
    """Apply a reward and, for game results, atomically update per-game stats."""
    with conn() as c:
        exists = c.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not exists:
            return False
        c.execute("""UPDATE users SET xp=xp+?, coins=coins+?, games=games+1,
                     wins=wins+?, updated_at=? WHERE user_id=?""",
                  (xp, coins, 1 if win else 0, now(), user_id))
        c.execute("""INSERT INTO events(user_id,kind,xp,coins,meta,created_at)
                     VALUES(?,?,?,?,?,?)""", (user_id, kind, xp, coins, meta, now()))
        if game:
            losses = 0 if win or draw else 1
            draws = 1 if draw else 0
            c.execute("""INSERT INTO game_stats(user_id,game,played,wins,losses,draws,xp_earned,coins_earned,last_played)
                         VALUES(?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(user_id,game) DO UPDATE SET
                         played=played+1,wins=wins+excluded.wins,losses=losses+excluded.losses,
                         draws=draws+excluded.draws,xp_earned=xp_earned+excluded.xp_earned,
                         coins_earned=coins_earned+excluded.coins_earned,last_played=excluded.last_played""",
                      (user_id, game, 1, 1 if win else 0, losses, draws, xp, coins, now()))
            _unlock_achievements(c, user_id, game)
        return True


def _unlock_achievements(c, user_id, game=None):
    row = c.execute("SELECT games,wins,coins,daily_streak,best_daily_streak FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return []
    total_games, wins, coins, streak, best = row
    rules = [
        ("first_game", total_games >= 1),
        ("ten_games", total_games >= 10),
        ("first_win", wins >= 1),
        ("ten_wins", wins >= 10),
        ("rich_500", coins >= 500),
        ("streak_3", best >= 3),
        ("streak_7", best >= 7),
    ]
    if game:
        gs = c.execute("SELECT wins FROM game_stats WHERE user_id=? AND game=?", (user_id, game)).fetchone()
        if gs and gs[0] >= 5:
            rules.append((f"master_{game}", True))
    unlocked=[]
    for key, ok in rules:
        if ok:
            cur=c.execute("INSERT OR IGNORE INTO achievements(user_id,achievement,unlocked_at) VALUES(?,?,?)", (user_id,key,now()))
            if cur.rowcount:
                unlocked.append(key)
    return unlocked


def add_coins(user_id, coins, kind="coins", meta=""):
    with conn() as c:
        c.execute("UPDATE users SET coins=coins+?,updated_at=? WHERE user_id=?",
                  (coins, now(), user_id))
        c.execute("""INSERT INTO events(user_id,kind,coins,meta,created_at)
                     VALUES(?,?,?,?,?)""", (user_id, kind, coins, meta, now()))


def claim_daily(user_id, amount):
    today = datetime.now(timezone.utc).date().isoformat()
    with conn() as c:
        row = c.execute("SELECT last_daily,daily_streak,best_daily_streak FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row or row["last_daily"] == today:
            return False
        streak = int(row["daily_streak"] or 0)
        if row["last_daily"]:
            try:
                last = datetime.fromisoformat(row["last_daily"]).date()
                delta = (datetime.fromisoformat(today).date() - last).days
                streak = streak + 1 if delta == 1 else 1
            except ValueError:
                streak = 1
        else:
            streak = 1
        best = max(int(row["best_daily_streak"] or 0), streak)
        updated = c.execute("""UPDATE users SET coins=coins+?, last_daily=?, daily_streak=?, best_daily_streak=?, updated_at=?
                              WHERE user_id=? AND (last_daily IS NULL OR last_daily<>?)""",
                           (amount, today, streak, best, now(), user_id, today))
        if updated.rowcount == 0:
            return False
        c.execute("INSERT INTO events(user_id,kind,coins,meta,created_at) VALUES(?,?,?,?,?)",
                  (user_id, "daily", amount, json.dumps({"streak":streak}), now()))
        _unlock_achievements(c, user_id)
        return True


def get_game_stats(user_id):
    with conn() as c:
        return c.execute("SELECT * FROM game_stats WHERE user_id=? ORDER BY wins DESC,played DESC", (user_id,)).fetchall()


def get_achievements(user_id):
    with conn() as c:
        return c.execute("SELECT achievement,unlocked_at FROM achievements WHERE user_id=? ORDER BY unlocked_at", (user_id,)).fetchall()


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
