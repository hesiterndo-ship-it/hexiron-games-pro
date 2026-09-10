"""Telegram-facing orchestration for Hokm — deals, trump selection, and
turn-by-turn card play via private DMs plus a shared group scoreboard."""
import logging

from db import reward
from . import hokm
from .engine import close_room, card_text, is_ai

log = logging.getLogger("hexiron-games.hokm_flow")


async def _safe_dm(context, uid, text, **kwargs):
    try:
        await context.bot.send_message(uid, text, **kwargs)
        return True
    except Exception as e:
        log.info("DM حکم به کاربر %s ارسال نشد: %s", uid, e)
        return False


async def start_hokm(room, context):
    hokm.start_match(room)
    await context.bot.send_message(
        room.chat_id,
        "🃏 حکم شروع شد!\nتیم ۱: " + " و ".join(room.names.get(u, str(u)) for u in (room.players[0], room.players[2])) +
        "\nتیم ۲: " + " و ".join(room.names.get(u, str(u)) for u in (room.players[1], room.players[3])))
    await deal_hand(room, context)


async def deal_hand(room, context):
    hakem, initial = hokm.start_hand(room)
    await context.bot.send_message(
        room.chat_id,
        f"🂠 دست {room.data['hand_number']} — حاکم این دست: {room.names.get(hakem, str(hakem))}\n"
        "در انتظار انتخاب حکم توسط حاکم...")
    if is_ai(hakem):
        suit = hokm.ai_choose_trump(room)
        await trump_chosen(room, context, suit)
        return
    cards = " | ".join(card_text(c) for c in initial)
    await _safe_dm(context, hakem, f"🃏 کارت‌های شما:\n{cards}\n\nخال حکم را انتخاب کن:",
                    reply_markup=hokm.trump_keyboard(room))


async def trump_chosen(room, context, suit):
    if not hokm.choose_trump(room, suit):
        return False
    await context.bot.send_message(room.chat_id, f"👑 حکم این دست: {suit}\nکارت‌ها پخش شد.")
    for uid in room.players:
        await send_hand(room, context, uid)
    if is_ai(room.data["turn"]):
        ai_uid = room.data["turn"]
        ai_card = hokm.ai_choose_card(room, ai_uid)
        await play_card(room, context, ai_uid, ai_card)
    else:
        await announce_turn(room, context)
    return True


async def send_hand(room, context, uid):
    if is_ai(uid):
        return
    hand = room.data["hands"][uid]
    cards = " | ".join(card_text(c) for c in hand)
    turn_note = " (نوبت شماست، کارت را بزنید)" if room.data["turn"] == uid else ""
    await _safe_dm(context, uid, f"🃏 دست شما ({len(hand)} کارت):{turn_note}", reply_markup=hokm.hand_keyboard(room, uid))


async def announce_turn(room, context):
    turn = room.data["turn"]
    trick = room.data["current_trick"]
    trick_txt = " ".join(f"{room.names.get(u, str(u))}:{card_text(c)}" for u, c in trick) or "—"
    await context.bot.send_message(
        room.chat_id,
        f"🎴 دست جاری: {trick_txt}\n▶️ نوبت: {room.names.get(turn, str(turn))}\n\n{hokm.score_text(room)}")


async def play_card(room, context, uid, card):
    try:
        result = hokm.play_card(room, uid, card)
    except hokm.IllegalMove as e:
        return False, str(e)

    if result["trick_complete"]:
        winner_name = room.names.get(result["trick_winner"], str(result["trick_winner"]))
        cards_txt = " ".join(f"{room.names.get(u, str(u))}:{card_text(c)}" for u, c in result["trick_cards"])
        await context.bot.send_message(room.chat_id, f"🏆 برنده این دست کارت: {winner_name}\n{cards_txt}")

    if result.get("hand_complete"):
        team = result["hand_winner_team"] + 1
        await context.bot.send_message(
            room.chat_id,
            f"🎉 تیم {team} این دست بازی را برد! (+{result['points_awarded']} امتیاز)\n{hokm.score_text(room)}")
        if result.get("match_complete"):
            await end_hokm(room, context, result["match_winner_team"])
            return True, None
        await deal_hand(room, context)
        return True, None

    # normal continue: hand off to whoever's turn it is now. If that's an AI
    # seat, let it play immediately (recursing handles runs of several AI
    # players in a row); a human turn is the recursion's base case.
    next_uid = room.data["turn"]
    if is_ai(next_uid):
        ai_card = hokm.ai_choose_card(room, next_uid)
        return await play_card(room, context, next_uid, ai_card)

    await send_hand(room, context, uid)
    if room.data["turn"] != uid:
        await send_hand(room, context, room.data["turn"])
    await announce_turn(room, context)
    return True, None


async def end_hokm(room, context, winner_team):
    team_players = (room.players[0], room.players[2]) if winner_team == 0 else (room.players[1], room.players[3])
    names = " و ".join(room.names.get(u, str(u)) for u in team_players)
    await context.bot.send_message(room.chat_id, f"🏆 تیم {winner_team + 1} ({names}) برنده مسابقه حکم شد!")
    for u in room.players:
        win = u in team_players
        reward(u, xp=30 if win else 10, coins=18 if win else 5, win=win, kind="hokm")
    close_room(room.chat_id)
