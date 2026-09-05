"""
Werewolf (گرگینه) — simplified night/day engine, sibling of Mafia but
without a doctor: werewolves kill at night, the seer investigates, and the
village lynches by day vote.
"""
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

WOLF_ROLE = "گرگینه"
SEER_ROLE = "پیشگو"
VILLAGER_ROLE = "روستایی"


def wolf_count(n_players):
    if n_players <= 6:
        return 1
    return 2


def assign_roles(room):
    n = len(room.players)
    n_wolves = wolf_count(n)
    roles = [WOLF_ROLE] * n_wolves
    if n >= 4:
        roles.append(SEER_ROLE)
    roles += [VILLAGER_ROLE] * max(0, n - len(roles))
    random.shuffle(roles)
    roles_map = dict(zip(room.players, roles))
    room.data["roles"] = roles_map
    room.data["alive"] = set(room.players)
    room.data["phase"] = "night"
    room.data["round"] = 1
    room.data["night_wolf_votes"] = {}
    room.data["day_votes"] = {}
    return roles_map


def alive_wolves(room):
    return [u for u in room.data["alive"] if room.data["roles"][u] == WOLF_ROLE]


def alive_others(room):
    return [u for u in room.data["alive"] if room.data["roles"][u] != WOLF_ROLE]


def check_win(room):
    wolves = alive_wolves(room)
    others = alive_others(room)
    if not wolves:
        return "village"
    if len(wolves) >= len(others):
        return "wolves"
    return None


def register_wolf_vote(room, voter, target):
    room.data["night_wolf_votes"][voter] = target


def resolve_night(room):
    votes = room.data["night_wolf_votes"]
    result = {"killed": None}
    if votes:
        tally = {}
        for t in votes.values():
            tally[t] = tally.get(t, 0) + 1
        best = max(tally.values())
        top = [t for t, v in tally.items() if v == best]
        target = random.choice(top)
        room.data["alive"].discard(target)
        result["killed"] = target
    room.data["night_wolf_votes"] = {}
    return result


def register_day_vote(room, voter, target):
    room.data["day_votes"][voter] = target


def resolve_day(room):
    votes = room.data["day_votes"]
    result = {"lynched": None, "tie": False}
    if votes:
        tally = {}
        for t in votes.values():
            tally[t] = tally.get(t, 0) + 1
        best = max(tally.values())
        top = [t for t, v in tally.items() if v == best]
        if len(top) > 1:
            result["tie"] = True
        else:
            target = top[0]
            room.data["alive"].discard(target)
            result["lynched"] = target
    room.data["day_votes"] = {}
    return result


def night_targets_keyboard(room, exclude_self=None):
    rows = [[InlineKeyboardButton(room.names.get(uid, str(uid)),
                                   callback_data=f"wf_night:{room.chat_id}:{uid}")]
            for uid in sorted(room.data["alive"]) if uid != exclude_self]
    return InlineKeyboardMarkup(rows)


def seer_keyboard(room, exclude_self):
    rows = [[InlineKeyboardButton(room.names.get(uid, str(uid)),
                                   callback_data=f"wf_seer:{room.chat_id}:{uid}")]
            for uid in sorted(room.data["alive"]) if uid != exclude_self]
    return InlineKeyboardMarkup(rows)


def day_vote_keyboard(room):
    rows = [[InlineKeyboardButton(room.names.get(uid, str(uid)),
                                   callback_data=f"wf_vote:{room.chat_id}:{uid}")]
            for uid in sorted(room.data["alive"])]
    return InlineKeyboardMarkup(rows)


def alive_list_text(room):
    return "\n".join(f"• {room.names.get(u, str(u))}" for u in sorted(room.data["alive"]))
