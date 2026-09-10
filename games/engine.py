import asyncio
import random
import time
from dataclasses import dataclass, field


@dataclass
class Room:
    game: str
    chat_id: int
    host_id: int
    players: list = field(default_factory=list)
    data: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    # Display names for players, keyed by user_id — needed to render buttons
    # (vote/target lists) without extra Telegram API calls.
    names: dict = field(default_factory=dict)
    # asyncio.Lock so two near-simultaneous callback taps on the same room
    # (e.g. two players voting in the same instant) can't corrupt room.data.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Scheduled telegram.ext Job handles (night/day timers) — cancelled on close
    # so a finished/closed room never fires a stale phase-timeout callback.
    jobs: list = field(default_factory=list)


ROOMS = {}


def get_room(chat_id):
    return ROOMS.get(chat_id)


def create_room(chat_id, game, host_id, host_name=""):
    room = Room(game, chat_id, host_id, [host_id])
    room.names[host_id] = host_name or str(host_id)
    ROOMS[chat_id] = room
    return room


def close_room(chat_id):
    room = ROOMS.pop(chat_id, None)
    if room:
        for job in room.jobs:
            try:
                job.schedule_removal()
            except Exception:
                pass
    return room


def add_player(room, uid, name="", max_players=4):
    if uid in room.players:
        return "already"
    if len(room.players) >= max_players:
        return "full"
    room.players.append(uid)
    room.names[uid] = name or str(uid)
    return "ok"


def player_name(room, uid):
    return room.names.get(uid, str(uid))


# --- AI-controlled players (single-player / fill-empty-seats mode) ---------
# AI players use negative fake ids, scoped to a single room's player list, so
# they never collide with a real Telegram user id (always positive) and need
# no separate bookkeeping across rooms.

def is_ai(uid) -> bool:
    return uid is not None and uid < 0


def add_ai_players(room, count, max_players=None):
    """Fill up to `count` empty seats with AI players. Never exceeds
    `max_players` (if given) or duplicates an already-added AI slot."""
    added = []
    for _ in range(count):
        if max_players and len(room.players) >= max_players:
            break
        n = sum(1 for u in room.players if is_ai(u)) + 1
        ai_uid = -n
        room.players.append(ai_uid)
        room.names[ai_uid] = f"🤖 ربات {n}"
        added.append(ai_uid)
    return added


# --- Tic-Tac-Toe -----------------------------------------------------------

def ttt_winner(b, x):
    return any(all(b[i] == x for i in line) for line in
               [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 4, 8), (2, 4, 6), (0, 3, 6), (1, 4, 7), (2, 5, 8)])


def ttt_move(b, i):
    if i < 0 or i >= 9 or b[i] != " ":
        return False
    b[i] = "X"
    return True


def ttt_ai(b):
    free = [i for i, v in enumerate(b) if v == " "]
    if not free:
        return
    # Win if possible, otherwise block, otherwise center/corner/random.
    for mark in ("O", "X"):
        for i in free:
            b[i] = mark
            if ttt_winner(b, mark):
                if mark == "O":
                    return
                b[i] = "O"
                return
            b[i] = " "
    for i in (4, 0, 2, 6, 8):
        if b[i] == " ":
            b[i] = "O"
            return
    b[random.choice(free)] = "O"


# --- Shared card deck (used by Hokm) ----------------------------------------

SUITS = ["♠", "♥", "♦", "♣"]
RANK_NAMES = {11: "J", 12: "Q", 13: "K", 14: "A"}


def deck():
    ranks = list(range(2, 15))
    d = [(s, r) for s in SUITS for r in ranks]
    random.shuffle(d)
    return d


def card_text(card):
    s, r = card
    return f"{RANK_NAMES.get(r, str(r))}{s}"


def new_ludo(room):
    room.data["pos"] = {uid: 0 for uid in room.players}
    room.data["turn"] = room.players[0]
