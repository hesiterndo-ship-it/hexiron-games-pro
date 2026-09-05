"""
Mafia (مافیا) — night/day social deduction engine.

Roles: mafia (kill at night), doctor (save one player at night),
detective (investigate one player's alignment at night), citizens.
Day: open discussion + a public lynch vote. Repeats until one side wins.
"""
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

MAFIA_ROLE = "مافیا"
DOCTOR_ROLE = "دکتر"
DETECTIVE_ROLE = "کارآگاه"
CITIZEN_ROLE = "شهروند"


def mafia_count(n_players):
    if n_players <= 5:
        return 1
    if n_players <= 7:
        return 2
    return 3


def assign_roles(room):
    n = len(room.players)
    n_mafia = mafia_count(n)
    roles = [MAFIA_ROLE] * n_mafia
    if n >= 5:
        roles.append(DETECTIVE_ROLE)
    if n >= 6:
        roles.append(DOCTOR_ROLE)
    roles += [CITIZEN_ROLE] * max(0, n - len(roles))
    random.shuffle(roles)
    roles_map = dict(zip(room.players, roles))
    room.data["roles"] = roles_map
    room.data["alive"] = set(room.players)
    room.data["phase"] = "night"
    room.data["round"] = 1
    room.data["night_kill"] = None
    room.data["night_save"] = None
    room.data["night_mafia_votes"] = {}
    room.data["day_votes"] = {}
    room.data["last_investigation"] = {}
    return roles_map


def alive_mafia(room):
    return [u for u in room.data["alive"] if room.data["roles"][u] == MAFIA_ROLE]


def alive_others(room):
    return [u for u in room.data["alive"] if room.data["roles"][u] != MAFIA_ROLE]


def check_win(room):
    mafia = alive_mafia(room)
    others = alive_others(room)
    if not mafia:
        return "village"
    if len(mafia) >= len(others):
        return "mafia"
    return None


def register_mafia_vote(room, voter, target):
    room.data["night_mafia_votes"][voter] = target


def resolve_night(room):
    """Tally mafia votes (majority, ties broken randomly) and apply doctor save.
    Returns dict describing the outcome."""
    votes = room.data["night_mafia_votes"]
    result = {"killed": None, "saved": False}
    if votes:
        tally = {}
        for target in votes.values():
            tally[target] = tally.get(target, 0) + 1
        best = max(tally.values())
        top_targets = [t for t, v in tally.items() if v == best]
        target = random.choice(top_targets)
        saved_uid = room.data.get("night_save")
        if target == saved_uid:
            result["saved"] = True
        else:
            room.data["alive"].discard(target)
            result["killed"] = target
    room.data["night_mafia_votes"] = {}
    room.data["night_save"] = None
    return result


def register_day_vote(room, voter, target):
    room.data["day_votes"][voter] = target


def resolve_day(room):
    votes = room.data["day_votes"]
    result = {"lynched": None, "tie": False}
    if votes:
        tally = {}
        for target in votes.values():
            tally[target] = tally.get(target, 0) + 1
        best = max(tally.values())
        top_targets = [t for t, v in tally.items() if v == best]
        if len(top_targets) > 1:
            result["tie"] = True
        else:
            target = top_targets[0]
            room.data["alive"].discard(target)
            result["lynched"] = target
    room.data["day_votes"] = {}
    return result


def night_targets_keyboard(room, exclude_self=None):
    rows = []
    for uid in sorted(room.data["alive"]):
        if uid == exclude_self:
            continue
        rows.append([InlineKeyboardButton(room.names.get(uid, str(uid)),
                                           callback_data=f"mf_night:{room.chat_id}:{uid}")])
    return InlineKeyboardMarkup(rows)


def doctor_keyboard(room):
    rows = [[InlineKeyboardButton(room.names.get(uid, str(uid)),
                                   callback_data=f"mf_save:{room.chat_id}:{uid}")]
            for uid in sorted(room.data["alive"])]
    return InlineKeyboardMarkup(rows)


def detective_keyboard(room, exclude_self):
    rows = [[InlineKeyboardButton(room.names.get(uid, str(uid)),
                                   callback_data=f"mf_invest:{room.chat_id}:{uid}")]
            for uid in sorted(room.data["alive"]) if uid != exclude_self]
    return InlineKeyboardMarkup(rows)


def day_vote_keyboard(room):
    rows = [[InlineKeyboardButton(room.names.get(uid, str(uid)),
                                   callback_data=f"mf_vote:{room.chat_id}:{uid}")]
            for uid in sorted(room.data["alive"])]
    return InlineKeyboardMarkup(rows)


def alive_list_text(room):
    return "\n".join(f"• {room.names.get(u, str(u))}" for u in sorted(room.data["alive"]))
