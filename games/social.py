"""
Telegram-facing orchestration for Mafia and Werewolf: sending DMs, posting
group announcements, and scheduling night/day phase timers via JobQueue.
The actual game-state logic lives in games/mafia.py and games/werewolf.py.
"""
import logging
import random

from db import reward
from . import mafia, werewolf
from .engine import close_room, get_room, is_ai
from config import (
    MAFIA_NIGHT_SECONDS, MAFIA_DAY_SECONDS,
    WEREWOLF_NIGHT_SECONDS, WEREWOLF_DAY_SECONDS,
)

log = logging.getLogger("hexiron-games.social")

MIN_MAFIA_PLAYERS = 4
MIN_WEREWOLF_PLAYERS = 4


def _cancel_jobs(room):
    for job in room.jobs:
        try:
            job.schedule_removal()
        except Exception:
            pass
    room.jobs = []


async def _safe_dm(context, uid, text, **kwargs):
    try:
        await context.bot.send_message(uid, text, **kwargs)
        return True
    except Exception as e:
        log.info("DM به کاربر %s ارسال نشد: %s", uid, e)
        return False


# ============================== MAFIA ======================================

async def start_mafia(room, context):
    roles = mafia.assign_roles(room)
    for uid, role in roles.items():
        await _safe_dm(context, uid, f"🎭 نقش شما در مافیا: {role}")
    await context.bot.send_message(
        room.chat_id,
        f"🕵️ مافیا شروع شد ({len(room.players)} بازیکن). نقش‌ها به صورت خصوصی ارسال شد.")
    await announce_mafia_night(room, context)


async def announce_mafia_night(room, context):
    _cancel_jobs(room)
    room.data["phase"] = "night"
    room.data["night_doctor_acted"] = False
    room.data["night_detective_acted"] = False
    for m in mafia.alive_mafia(room):
        if is_ai(m):
            continue
        await _safe_dm(context, m, "🔪 شب شد. قربانی امشب را انتخاب کن:",
                        reply_markup=mafia.night_targets_keyboard(room, exclude_self=None))
    doctor = next((u for u in room.data["alive"] if room.data["roles"][u] == mafia.DOCTOR_ROLE), None)
    if doctor and not is_ai(doctor):
        await _safe_dm(context, doctor, "💉 امشب چه کسی را نجات می‌دهی؟", reply_markup=mafia.doctor_keyboard(room))
    detective = next((u for u in room.data["alive"] if room.data["roles"][u] == mafia.DETECTIVE_ROLE), None)
    if detective and not is_ai(detective):
        await _safe_dm(context, detective, "🔍 امشب چه کسی را بررسی می‌کنی؟",
                        reply_markup=mafia.detective_keyboard(room, detective))
    await context.bot.send_message(
        room.chat_id,
        f"🌙 شب {room.data['round']} — منتظر تصمیم نقش‌های خاص باشید (حداکثر {MAFIA_NIGHT_SECONDS} ثانیه).")
    # AI seats act immediately (no button to wait on); this lets the readiness
    # check below fire as soon as the remaining humans finish, or the timeout
    # job resolves the night if a human is slow/AFK.
    async with room.lock:
        for m in mafia.alive_mafia(room):
            if is_ai(m):
                others = [u for u in mafia.alive_others(room)]
                if others:
                    mafia.register_mafia_vote(room, m, random.choice(others))
        if doctor and is_ai(doctor):
            room.data["night_save"] = random.choice(list(room.data["alive"]))
            room.data["night_doctor_acted"] = True
        if detective and is_ai(detective):
            room.data["night_detective_acted"] = True
    job = context.job_queue.run_once(mafia_night_timeout, MAFIA_NIGHT_SECONDS,
                                      chat_id=room.chat_id, name=f"mfnight:{room.chat_id}")
    room.jobs.append(job)
    await maybe_resolve_mafia_night_early(room, context)


def _mafia_night_ready(room):
    if len(room.data["night_mafia_votes"]) < len(mafia.alive_mafia(room)):
        return False
    has_doctor = any(room.data["roles"][u] == mafia.DOCTOR_ROLE for u in room.data["alive"])
    if has_doctor and not room.data["night_doctor_acted"]:
        return False
    has_detective = any(room.data["roles"][u] == mafia.DETECTIVE_ROLE for u in room.data["alive"])
    if has_detective and not room.data["night_detective_acted"]:
        return False
    return True


async def maybe_resolve_mafia_night_early(room, context):
    if room.data.get("phase") == "night" and _mafia_night_ready(room):
        _cancel_jobs(room)
        await resolve_mafia_night(room, context)


async def mafia_night_timeout(context):
    room = get_room(context.job.chat_id)
    if not room or room.game != "mafia" or room.data.get("phase") != "night":
        return
    await resolve_mafia_night(room, context)


async def resolve_mafia_night(room, context):
    result = mafia.resolve_night(room)
    if result["killed"]:
        name = room.names.get(result["killed"], str(result["killed"]))
        role = room.data["roles"][result["killed"]]
        text = f"☠️ {name} امشب کشته شد. (نقش: {role})"
    elif result["saved"]:
        text = "💉 دکتر جلوی یک قتل را گرفت؛ امشب کسی نمرد."
    else:
        text = "🌙 امشب کسی کشته نشد."
    await context.bot.send_message(room.chat_id, text)
    winner = mafia.check_win(room)
    if winner:
        await end_mafia(room, context, winner)
        return
    await announce_mafia_day(room, context)


async def announce_mafia_day(room, context):
    _cancel_jobs(room)
    room.data["phase"] = "day"
    text = f"☀️ روز {room.data['round']} — بحث کنید، سپس رأی بدهید:\n\n{mafia.alive_list_text(room)}"
    await context.bot.send_message(room.chat_id, text, reply_markup=mafia.day_vote_keyboard(room))
    async with room.lock:
        for u in room.data["alive"]:
            if is_ai(u):
                choices = [t for t in room.data["alive"] if t != u]
                if choices:
                    mafia.register_day_vote(room, u, random.choice(choices))
    job = context.job_queue.run_once(mafia_day_timeout, MAFIA_DAY_SECONDS,
                                      chat_id=room.chat_id, name=f"mfday:{room.chat_id}")
    room.jobs.append(job)
    await maybe_resolve_mafia_day_early(room, context)


async def maybe_resolve_mafia_day_early(room, context):
    if room.data.get("phase") == "day" and len(room.data["day_votes"]) >= len(room.data["alive"]):
        _cancel_jobs(room)
        await resolve_mafia_day(room, context)


async def mafia_day_timeout(context):
    room = get_room(context.job.chat_id)
    if not room or room.game != "mafia" or room.data.get("phase") != "day":
        return
    await resolve_mafia_day(room, context)


async def resolve_mafia_day(room, context):
    result = mafia.resolve_day(room)
    if result["tie"]:
        text = "⚖️ رأی‌ها مساوی شد؛ امروز کسی اعدام نشد."
    elif result["lynched"]:
        name = room.names.get(result["lynched"], str(result["lynched"]))
        role = room.data["roles"][result["lynched"]]
        text = f"⚖️ {name} با رأی جمع اعدام شد. (نقش: {role})"
    else:
        text = "⚖️ رأیی ثبت نشد؛ امروز کسی اعدام نشد."
    await context.bot.send_message(room.chat_id, text)
    winner = mafia.check_win(room)
    if winner:
        await end_mafia(room, context, winner)
        return
    room.data["round"] += 1
    await announce_mafia_night(room, context)


async def end_mafia(room, context, winner):
    _cancel_jobs(room)
    roles = room.data["roles"]
    lines = [f"{room.names.get(u, str(u))} — {r}" for u, r in roles.items()]
    winner_text = "🏆 روستاییان بردند!" if winner == "village" else "🏆 مافیا بردند!"
    await context.bot.send_message(room.chat_id, winner_text + "\n\nنقش‌ها:\n" + "\n".join(lines))
    for u in room.players:
        win = (winner == "village" and roles[u] != mafia.MAFIA_ROLE) or \
              (winner == "mafia" and roles[u] == mafia.MAFIA_ROLE)
        reward(u, xp=25 if win else 8, coins=15 if win else 3, win=win, kind="mafia", game="mafia")
    close_room(room.chat_id)


# ============================= WEREWOLF ====================================

async def start_werewolf(room, context):
    roles = werewolf.assign_roles(room)
    for uid, role in roles.items():
        await _safe_dm(context, uid, f"🎭 نقش شما در گرگینه: {role}")
    await context.bot.send_message(
        room.chat_id,
        f"🐺 گرگینه شروع شد ({len(room.players)} بازیکن). نقش‌ها به صورت خصوصی ارسال شد.")
    await announce_werewolf_night(room, context)


async def announce_werewolf_night(room, context):
    _cancel_jobs(room)
    room.data["phase"] = "night"
    room.data["night_seer_acted"] = False
    for w in werewolf.alive_wolves(room):
        if is_ai(w):
            continue
        await _safe_dm(context, w, "🌕 شب شد. طعمه امشب را انتخاب کن:",
                        reply_markup=werewolf.night_targets_keyboard(room))
    seer = next((u for u in room.data["alive"] if room.data["roles"][u] == werewolf.SEER_ROLE), None)
    if seer and not is_ai(seer):
        await _safe_dm(context, seer, "🔮 چه کسی را بررسی می‌کنی؟",
                        reply_markup=werewolf.seer_keyboard(room, seer))
    await context.bot.send_message(
        room.chat_id,
        f"🌙 شب {room.data['round']} — منتظر تصمیم گرگینه‌ها باشید (حداکثر {WEREWOLF_NIGHT_SECONDS} ثانیه).")
    async with room.lock:
        for w in werewolf.alive_wolves(room):
            if is_ai(w):
                others = werewolf.alive_others(room)
                if others:
                    werewolf.register_wolf_vote(room, w, random.choice(others))
        if seer and is_ai(seer):
            room.data["night_seer_acted"] = True
    job = context.job_queue.run_once(werewolf_night_timeout, WEREWOLF_NIGHT_SECONDS,
                                      chat_id=room.chat_id, name=f"wfnight:{room.chat_id}")
    room.jobs.append(job)
    await maybe_resolve_werewolf_night_early(room, context)


def _werewolf_night_ready(room):
    if len(room.data["night_wolf_votes"]) < len(werewolf.alive_wolves(room)):
        return False
    has_seer = any(room.data["roles"][u] == werewolf.SEER_ROLE for u in room.data["alive"])
    if has_seer and not room.data["night_seer_acted"]:
        return False
    return True


async def maybe_resolve_werewolf_night_early(room, context):
    if room.data.get("phase") == "night" and _werewolf_night_ready(room):
        _cancel_jobs(room)
        await resolve_werewolf_night(room, context)


async def werewolf_night_timeout(context):
    room = get_room(context.job.chat_id)
    if not room or room.game != "werewolf" or room.data.get("phase") != "night":
        return
    await resolve_werewolf_night(room, context)


async def resolve_werewolf_night(room, context):
    result = werewolf.resolve_night(room)
    if result["killed"]:
        name = room.names.get(result["killed"], str(result["killed"]))
        role = room.data["roles"][result["killed"]]
        text = f"☠️ {name} امشب طعمه گرگینه‌ها شد. (نقش: {role})"
    else:
        text = "🌙 امشب کسی کشته نشد."
    await context.bot.send_message(room.chat_id, text)
    winner = werewolf.check_win(room)
    if winner:
        await end_werewolf(room, context, winner)
        return
    await announce_werewolf_day(room, context)


async def announce_werewolf_day(room, context):
    _cancel_jobs(room)
    room.data["phase"] = "day"
    text = f"☀️ روز {room.data['round']} — بحث کنید، سپس رأی بدهید:\n\n{werewolf.alive_list_text(room)}"
    await context.bot.send_message(room.chat_id, text, reply_markup=werewolf.day_vote_keyboard(room))
    async with room.lock:
        for u in room.data["alive"]:
            if is_ai(u):
                choices = [t for t in room.data["alive"] if t != u]
                if choices:
                    werewolf.register_day_vote(room, u, random.choice(choices))
    job = context.job_queue.run_once(werewolf_day_timeout, WEREWOLF_DAY_SECONDS,
                                      chat_id=room.chat_id, name=f"wfday:{room.chat_id}")
    room.jobs.append(job)
    await maybe_resolve_werewolf_day_early(room, context)


async def maybe_resolve_werewolf_day_early(room, context):
    if room.data.get("phase") == "day" and len(room.data["day_votes"]) >= len(room.data["alive"]):
        _cancel_jobs(room)
        await resolve_werewolf_day(room, context)


async def werewolf_day_timeout(context):
    room = get_room(context.job.chat_id)
    if not room or room.game != "werewolf" or room.data.get("phase") != "day":
        return
    await resolve_werewolf_day(room, context)


async def resolve_werewolf_day(room, context):
    result = werewolf.resolve_day(room)
    if result["tie"]:
        text = "⚖️ رأی‌ها مساوی شد؛ امروز کسی اخراج نشد."
    elif result["lynched"]:
        name = room.names.get(result["lynched"], str(result["lynched"]))
        role = room.data["roles"][result["lynched"]]
        text = f"⚖️ {name} با رأی جمع از روستا اخراج شد. (نقش: {role})"
    else:
        text = "⚖️ رأیی ثبت نشد؛ امروز کسی اخراج نشد."
    await context.bot.send_message(room.chat_id, text)
    winner = werewolf.check_win(room)
    if winner:
        await end_werewolf(room, context, winner)
        return
    room.data["round"] += 1
    await announce_werewolf_night(room, context)


async def end_werewolf(room, context, winner):
    _cancel_jobs(room)
    roles = room.data["roles"]
    lines = [f"{room.names.get(u, str(u))} — {r}" for u, r in roles.items()]
    winner_text = "🏆 روستاییان بردند!" if winner == "village" else "🏆 گرگینه‌ها بردند!"
    await context.bot.send_message(room.chat_id, winner_text + "\n\nنقش‌ها:\n" + "\n".join(lines))
    for u in room.players:
        win = (winner == "village" and roles[u] != werewolf.WOLF_ROLE) or \
              (winner == "wolves" and roles[u] == werewolf.WOLF_ROLE)
        reward(u, xp=25 if win else 8, coins=15 if win else 3, win=win, kind="werewolf", game="werewolf")
    close_room(room.chat_id)
