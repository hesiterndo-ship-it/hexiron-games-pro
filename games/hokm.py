"""
Real Hokm (حکم) engine — 4 players, 2 fixed teams (seats 0&2 vs 1&3).

Flow: hakem gets 5 cards -> picks trump -> everyone gets dealt up to 13 ->
trick-taking with mandatory follow-suit -> first team to 7 tricks wins the
hand -> first team to POINTS_TO_WIN_MATCH hand-points wins the match.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .engine import deck, card_text, SUITS

TRICKS_TO_WIN_HAND = 7
POINTS_TO_WIN_MATCH = 7


def teams_for(players):
    return {players[0]: 0, players[2]: 0, players[1]: 1, players[3]: 1}


def start_match(room):
    room.data["match_score"] = {0: 0, 1: 0}
    room.data["hakem"] = room.host_id
    room.data["hand_number"] = 0
    room.data["teams"] = teams_for(room.players)


def start_hand(room):
    room.data["hand_number"] = room.data.get("hand_number", 0) + 1
    hakem = room.data["hakem"]
    d = deck()
    hands = {uid: [] for uid in room.players}
    hands[hakem] = list(d[:5])
    room.data["hands"] = hands
    room.data["_rest_deck"] = d[5:]
    room.data["trump"] = None
    room.data["tricks_won"] = {0: 0, 1: 0}
    room.data["current_trick"] = []
    room.data["lead_suit"] = None
    room.data["trick_leader"] = None
    room.data["turn"] = None
    room.data["last_trick_summary"] = ""
    return hakem, hands[hakem]


def choose_trump(room, suit):
    if suit not in SUITS:
        return False
    hakem = room.data["hakem"]
    room.data["trump"] = suit
    hands = room.data["hands"]
    rest = room.data.pop("_rest_deck")
    others = [u for u in room.players if u != hakem]
    idx = 0
    for u in others:
        hands[u].extend(rest[idx:idx + 13])
        idx += 13
    hands[hakem].extend(rest[idx:idx + 8])
    room.data["trick_leader"] = hakem
    room.data["turn"] = hakem
    return True


def legal_cards(hand, lead_suit):
    if lead_suit is None:
        return list(hand)
    same_suit = [c for c in hand if c[0] == lead_suit]
    return same_suit if same_suit else list(hand)


def next_player(players, uid):
    idx = players.index(uid)
    return players[(idx + 1) % len(players)]


def trick_winner(trick, lead_suit, trump):
    def key(entry):
        _uid, (s, r) = entry
        if s == trump:
            return (2, r)
        if s == lead_suit:
            return (1, r)
        return (0, r)
    return max(trick, key=key)[0]


class IllegalMove(Exception):
    pass


def play_card(room, uid, card):
    """Attempt to play `card` for `uid`. Raises IllegalMove on any violation.
    Returns a dict describing what happened, for the caller to render."""
    if room.data["turn"] != uid:
        raise IllegalMove("نوبت شما نیست.")
    hand = room.data["hands"][uid]
    if card not in hand:
        raise IllegalMove("این کارت را در دست ندارید.")
    legal = legal_cards(hand, room.data["lead_suit"])
    if card not in legal:
        raise IllegalMove("باید همخال بازی کنید.")

    hand.remove(card)
    trick = room.data["current_trick"]
    trick.append((uid, card))
    if len(trick) == 1:
        room.data["lead_suit"] = card[0]

    result = {"trick_complete": False, "hand_complete": False, "match_complete": False}

    if len(trick) < len(room.players):
        room.data["turn"] = next_player(room.players, uid)
        return result

    # trick complete
    winner = trick_winner(trick, room.data["lead_suit"], room.data["trump"])
    team = room.data["teams"][winner]
    room.data["tricks_won"][team] += 1
    result["trick_complete"] = True
    result["trick_winner"] = winner
    result["trick_cards"] = list(trick)
    result["team_after"] = dict(room.data["tricks_won"])

    room.data["current_trick"] = []
    room.data["lead_suit"] = None
    room.data["trick_leader"] = winner
    room.data["turn"] = winner

    tw = room.data["tricks_won"]
    if tw[team] >= TRICKS_TO_WIN_HAND:
        loser_team = 1 - team
        points = 2 if tw[loser_team] == 0 else 1
        room.data["match_score"][team] += points
        result["hand_complete"] = True
        result["hand_winner_team"] = team
        result["points_awarded"] = points
        result["match_score"] = dict(room.data["match_score"])
        if room.data["match_score"][team] >= POINTS_TO_WIN_MATCH:
            result["match_complete"] = True
            result["match_winner_team"] = team
        else:
            # hakem rotates to the next player after the current hakem
            room.data["hakem"] = next_player(room.players, room.data["hakem"])
    return result


def trump_keyboard(room):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(s, callback_data=f"hk_trump:{room.chat_id}:{s}") for s in SUITS]])


def hand_keyboard(room, uid):
    hand = room.data["hands"][uid]
    legal = legal_cards(hand, room.data["lead_suit"]) if room.data["turn"] == uid else []
    rows, current = [], []
    for card in hand:
        label = card_text(card)
        if room.data["turn"] == uid and card in legal:
            enc = f"{card[0]}{card[1]}"
            current.append(InlineKeyboardButton(label, callback_data=f"hk_play:{room.chat_id}:{enc}"))
        else:
            current.append(InlineKeyboardButton(f"({label})", callback_data="hk_noop"))
        if len(current) == 4:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    return InlineKeyboardMarkup(rows)


def parse_card(token):
    suit, rank = token[0], int(token[1:])
    return (suit, rank)


def score_text(room):
    ms = room.data["match_score"]
    tw = room.data["tricks_won"]
    return (f"🃏 امتیاز مسابقه — تیم ۱: {ms[0]} | تیم ۲: {ms[1]}\n"
            f"دست جاری — تیم ۱: {tw[0]} | تیم ۲: {tw[1]} (حکم: {room.data['trump']})")
