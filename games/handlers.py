import logging
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus

from config import CHANNEL_ID, CHANNEL_URL, FREE_GAMES, BRAND_NAME, REFERRAL_REWARD, COINS_PER_DAILY
from db import upsert_user, get_user, reward, leaderboard, rank_of, claim_daily, set_referral, is_banned
from sales import has_license, purchase_link
from . import social, hokm, hokm_flow
from .catalog import GAMES, TRUTH, DARE, QUESTIONS, WORDS, LETTERS
from .engine import (ROOMS, create_room, get_room, close_room, add_player, player_name,
                      ttt_winner, ttt_move, ttt_ai, new_ludo)

log = logging.getLogger("hexiron-games.handlers")

# Group games and how many players they need.
MIN_PLAYERS = {"hokm": 4, "mafia": 4, "ludo": 2, "werewolf": 4}
MAX_PLAYERS = {"hokm": 4, "mafia": 8, "ludo": 4, "werewolf": 8}
GROUP_GAMES = set(MIN_PLAYERS)


def menu():
    rows = []
    items = list(GAMES.items())
    for i in range(0, len(items), 2):
        rows.append([InlineKeyboardButton(v[0], callback_data=f"game:{k}") for k, v in items[i:i + 2]])
    rows += [
        [InlineKeyboardButton("👤 پروفایل", callback_data="profile"),
         InlineKeyboardButton("🏆 رتبه‌بندی", callback_data="top")],
        [InlineKeyboardButton("🎁 جایزه روزانه", callback_data="daily"),
         InlineKeyboardButton("👥 دعوت دوستان", callback_data="invite")],
    ]
    return InlineKeyboardMarkup(rows)


async def member_ok(bot, uid):
    if not CHANNEL_ID:
        return True
    try:
        m = await bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception as e:
        log.info("بررسی عضویت کانال برای %s ناموفق بود: %s", uid, e)
        return False


async def gate(update, game):
    """True if `game` may be launched here. Sends the relevant prompt otherwise.

    Premium is licensed per CHAT (group or private), not per user and not per
    individual game — one HEXIRON GAMES purchase unlocks all 8 Premium games
    for that chat, matching how HEXIRON SALES licenses every other product.
    """
    uid = update.effective_user.id
    if is_banned(uid):
        await update.effective_message.reply_text("🚫 شما در این ربات مسدود شده‌اید.")
        return False

    if game in FREE_GAMES:
        if not await member_ok(update.get_bot(), uid):
            kb = [[InlineKeyboardButton("📢 عضویت در کانال", url=CHANNEL_URL or "https://t.me/")],
                  [InlineKeyboardButton("✅ بررسی عضویت", callback_data=f"check:{game}")]]
            await update.effective_message.reply_text(
                "🔒 برای بازی‌های رایگان ابتدا عضو کانال شوید.", reply_markup=InlineKeyboardMarkup(kb))
            return False
        return True

    chat_id = update.effective_chat.id
    if not await has_license(chat_id):
        url = purchase_link(chat_id)
        kb = [[InlineKeyboardButton("💎 خرید / فعال‌سازی", url=url)]] if url.startswith("http") else []
        await update.effective_message.reply_text(
            f"🔐 {GAMES[game][0]} نسخه Premium است.\n"
            "این چت هنوز اشتراک HEXIRON GAMES ندارد. با یک خرید، همه‌ی ۸ بازی Premium "
            "برای این چت (نه فقط این بازی) فعال می‌شود.",
            reply_markup=InlineKeyboardMarkup(kb) if kb else None)
        return False
    return True


def register_user(update):
    u = update.effective_user
    upsert_user(u.id, u.full_name, u.username or "")


async def start(update, context):
    register_user(update)
    if is_banned(update.effective_user.id):
        await update.message.reply_text("🚫 شما در این ربات مسدود شده‌اید.")
        return
    uid = update.effective_user.id
    args = context.args
    if args and args[0].startswith("ref_"):
        try:
            ref = int(args[0][4:])
            if set_referral(ref, uid):
                reward(ref, xp=20, coins=REFERRAL_REWARD, kind="referral")
                reward(uid, xp=10, coins=REFERRAL_REWARD, kind="referral")
        except Exception:
            pass
    await update.message.reply_text(
        f"🎮 {BRAND_NAME}\n\n۱۰ بازی، سیستم Premium، XP، Coins، رتبه‌بندی، دعوت دوستان و اتاق‌های چندنفره.\n\n"
        "🆓 رایگان: دوز + جرئت/حقیقت\n💎 Premium: ۸ بازی دیگر (یک خرید = فعال شدن همه برای این چت)",
        reply_markup=menu())


async def games(update, context):
    register_user(update)
    await update.effective_message.reply_text("🎮 بازی موردنظر را انتخاب کن:", reply_markup=menu())


async def profile(update, context):
    register_user(update)
    uid = update.effective_user.id
    r = get_user(uid)
    await update.effective_message.reply_text(
        f"👤 {r['name']}\n⭐ XP: {r['xp']}\n🪙 Coins: {r['coins']}\n"
        f"🏆 برد: {r['wins']}\n🎮 بازی: {r['games']}\n📈 رتبه: #{rank_of(uid)}\n👥 دعوت: {r['referrals']}")


async def top(update, context):
    rows = leaderboard(10)
    if not rows:
        text = "هنوز بازیکنی ثبت نشده."
    else:
        text = "🏆 TOP 10\n\n" + "\n".join(
            f"{i}. {r['name']} — ⭐{r['xp']} | 🏆{r['wins']} | 🪙{r['coins']}"
            for i, r in enumerate(rows, 1))
    await update.effective_message.reply_text(text)


async def daily(update, context):
    register_user(update)
    if claim_daily(update.effective_user.id, COINS_PER_DAILY):
        await update.effective_message.reply_text(f"🎁 جایزه روزانه: +{COINS_PER_DAILY} 🪙")
    else:
        await update.effective_message.reply_text("⏳ جایزه امروز را قبلاً گرفتی.")


async def invite(update, context):
    register_user(update)
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{update.effective_user.id}"
    await update.effective_message.reply_text(
        f"👥 لینک دعوت اختصاصی تو:\n{link}\n\nهر دعوت موفق: +{REFERRAL_REWARD} 🪙 برای تو و دوستت.")


async def launch(update, game):
    register_user(update)
    if not await gate(update, game):
        return
    chat = update.effective_chat.id
    uid = update.effective_user.id
    name = update.effective_user.full_name
    if game == "ttt":
        room = create_room(chat, game, uid, name)
        room.data["board"] = [" "] * 9
        await update.effective_message.reply_text(
            "❌⭕ دوز\nتو با X بازی می‌کنی.", reply_markup=ttt_kb(chat, room.data["board"]))
    elif game == "truth":
        await update.effective_message.reply_text("🎭 انتخاب کن:", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❤️ حقیقت", callback_data="truth:T"),
              InlineKeyboardButton("🔥 جرئت", callback_data="truth:D")]]))
    elif game == "quiz":
        await start_quiz(update)
    elif game == "word":
        room = create_room(chat, game, uid, name)
        room.data["word"] = random.choice(WORDS)
        await update.effective_message.reply_text("🎯 حدس کلمه\nکلمه انتخاب شده؛ حدست را به صورت پیام ارسال کن.")
    elif game == "speed":
        room = create_room(chat, game, uid, name)
        room.data["target"] = str(random.randint(10, 99))
        await update.effective_message.reply_text(
            f"⚡ سرعت عمل\nاولین نفر که عدد «{room.data['target']}» را ارسال کند برنده است!")
    elif game == "name":
        room = create_room(chat, game, uid, name)
        room.data["letter"] = random.choice(LETTERS)
        await update.effective_message.reply_text(
            f"🔤 اسم‌فامیل\nحرف: «{room.data['letter']}»\n"
            "هر بازیکن جواب‌های اسم، فامیل، شهر، غذا و حیوان را در یک پیام بفرستد.")
    elif game in GROUP_GAMES:
        room = create_room(chat, game, uid, name)
        maxp = MAX_PLAYERS[game]
        await update.effective_message.reply_text(
            f"{GAMES[game][0]}\n\nاتاق ساخته شد (ظرفیت {maxp} نفر). دوستان را اضافه کنید و «شروع» را بزنید.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ پیوستن", callback_data=f"join:{game}"),
                 InlineKeyboardButton("▶️ شروع", callback_data=f"startroom:{game}")],
                [InlineKeyboardButton("❌ بستن اتاق", callback_data="close")]]))


def ttt_kb(chat_id, b):
    return InlineKeyboardMarkup([[InlineKeyboardButton(b[i] if b[i] != " " else "·",
                                                         callback_data=f"ttt:{chat_id}:{i}")
                                   for i in range(r, r + 3)] for r in (0, 3, 6)])


async def start_quiz(update):
    q, o, c = random.choice(QUESTIONS)
    chat = update.effective_chat.id
    room = create_room(chat, "quiz", update.effective_user.id, update.effective_user.full_name)
    room.data["correct"] = c
    await update.effective_message.reply_text("🧠 " + q, reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton(x, callback_data=f"quiz:{chat}:{i}") for i, x in enumerate(o)]]))


def ludo_kb(room):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎲 تاس", callback_data=f"roll:{room.chat_id}")]])


async def callback(update, context):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = q.from_user.id
    upsert_user(uid, q.from_user.full_name, q.from_user.username or "")

    if is_banned(uid) and not d.startswith(("game:", "profile", "top")):
        return

    if d.startswith("check:"):
        g = d.split(":")[1]
        await q.message.reply_text(
            "✅ عضویت تأیید شد." if await member_ok(context.bot, uid) else "❌ هنوز عضو کانال نیستی.")
        return
    if d == "profile":
        return await profile(update, context)
    if d == "top":
        return await top(update, context)
    if d == "daily":
        return await daily(update, context)
    if d == "invite":
        return await invite(update, context)
    if d.startswith("game:"):
        return await launch(update, d.split(":")[1])

    if d.startswith("truth:"):
        truth = d.endswith(":T")
        reward(uid, xp=5, coins=2, kind="truth")
        await q.message.reply_text(("❤️ حقیقت: " if truth else "🔥 جرئت: ") + random.choice(TRUTH if truth else DARE))
        return

    if d.startswith("ttt:"):
        _, chat_s, idx_s = d.split(":")
        room = get_room(int(chat_s))
        if not room or room.game != "ttt":
            return
        if uid != room.host_id:
            await q.answer("این بازی متعلق به شما نیست.", show_alert=True)
            return
        i = int(idx_s)
        b = room.data["board"]
        if not ttt_move(b, i):
            return
        if ttt_winner(b, "X"):
            reward(uid, 20, 10, True, "ttt_win")
            close_room(room.chat_id)
            await q.message.edit_text("🏆 بردی! +20 XP +10 Coins")
            return
        if " " not in b:
            reward(uid, 8, 4, False, "ttt_draw")
            close_room(room.chat_id)
            await q.message.edit_text("🤝 مساوی شد.")
            return
        ttt_ai(b)
        if ttt_winner(b, "O"):
            reward(uid, 5, 2, False, "ttt_loss")
            close_room(room.chat_id)
            await q.message.edit_text("🤖 ربات برنده شد. دفعه بعد می‌بری!")
            return
        await q.message.edit_reply_markup(reply_markup=ttt_kb(room.chat_id, b))
        return

    if d.startswith("quiz:"):
        _, chat_s, idx_s = d.split(":")
        room = get_room(int(chat_s))
        if not room or room.game != "quiz":
            return
        if uid != room.host_id:
            await q.answer("این کوییز متعلق به شما نیست.", show_alert=True)
            return
        ok = int(idx_s) == room.data["correct"]
        reward(uid, 15 if ok else 3, 7 if ok else 1, ok, "quiz")
        close_room(room.chat_id)
        await q.message.edit_text("🏆 جواب درست بود! +15 XP +7 Coins" if ok else "❌ جواب اشتباه بود.")
        return

    if d.startswith("join:"):
        room = get_room(q.message.chat.id)
        if not room:
            return
        game = d.split(":")[1]
        result = add_player(room, uid, q.from_user.full_name, MAX_PLAYERS.get(game, 8))
        await q.message.reply_text(
            "✅ وارد اتاق شدی." if result == "ok" else
            ("ℹ️ قبلاً داخل اتاقی." if result == "already" else "❌ ظرفیت اتاق تکمیل است."))
        return

    if d.startswith("startroom:"):
        room = get_room(q.message.chat.id)
        if not room or room.host_id != uid:
            await q.message.reply_text("فقط سازنده اتاق می‌تواند شروع کند.")
            return
        game = room.game
        minp = MIN_PLAYERS[game]
        if game == "hokm" and len(room.players) != 4:
            await q.message.reply_text("🃏 حکم دقیقاً به ۴ بازیکن نیاز دارد (نه بیشتر، نه کمتر).")
            return
        if len(room.players) < minp:
            await q.message.reply_text(f"حداقل {minp} بازیکن لازم است.")
            return
        if game == "hokm":
            await hokm_flow.start_hokm(room, context)
        elif game == "ludo":
            new_ludo(room)
            await q.message.reply_text("🎲 منچ شروع شد. هر نفر با دکمه تاس بازی می‌کند.",
                                        reply_markup=ludo_kb(room))
        elif game == "mafia":
            await social.start_mafia(room, context)
        elif game == "werewolf":
            await social.start_werewolf(room, context)
        return

    if d == "close":
        room = get_room(q.message.chat.id)
        if room and room.host_id == uid:
            close_room(q.message.chat.id)
            await q.message.reply_text("❌ اتاق بسته شد.")
        return

    if d.startswith("roll:"):
        chat_s = d.split(":")[1]
        room = get_room(int(chat_s))
        if not room or room.game != "ludo":
            return
        if room.data["turn"] != uid:
            await q.answer("⏳ نوبت بازیکن دیگری است.", show_alert=True)
            return
        n = random.randint(1, 6)
        room.data["pos"][uid] += n
        idx = room.players.index(uid)
        room.data["turn"] = room.players[(idx + 1) % len(room.players)]
        if room.data["pos"][uid] >= 30:
            reward(uid, 35, 20, True, "ludo_win")
            close_room(room.chat_id)
            await q.message.edit_text(f"🏆 {q.from_user.full_name} برنده منچ شد!")
        else:
            await q.message.edit_text(
                "🎲 تاس: " + str(n) + "\n\n" +
                "\n".join(f"👤 {player_name(room, u)}: {room.data['pos'][u]}/30" for u in room.players),
                reply_markup=ludo_kb(room))
        return

    # ---- Mafia night/day callbacks (may arrive from a private DM chat) ----
    if d.startswith("mf_night:"):
        _, chat_s, target_s = d.split(":")
        room = get_room(int(chat_s))
        if not room or room.game != "mafia" or room.data.get("phase") != "night":
            return
        from . import mafia
        if room.data["roles"].get(uid) != mafia.MAFIA_ROLE or uid not in room.data["alive"]:
            return
        async with room.lock:
            mafia.register_mafia_vote(room, uid, int(target_s))
        await q.edit_message_text(f"🔪 هدف انتخاب شد: {player_name(room, int(target_s))}")
        await social.maybe_resolve_mafia_night_early(room, context)
        return

    if d.startswith("mf_save:"):
        _, chat_s, target_s = d.split(":")
        room = get_room(int(chat_s))
        if not room or room.game != "mafia" or room.data.get("phase") != "night":
            return
        from . import mafia
        if room.data["roles"].get(uid) != mafia.DOCTOR_ROLE or uid not in room.data["alive"]:
            return
        async with room.lock:
            room.data["night_save"] = int(target_s)
            room.data["night_doctor_acted"] = True
        await q.edit_message_text(f"💉 امشب از {player_name(room, int(target_s))} محافظت می‌کنی.")
        await social.maybe_resolve_mafia_night_early(room, context)
        return

    if d.startswith("mf_invest:"):
        _, chat_s, target_s = d.split(":")
        room = get_room(int(chat_s))
        if not room or room.game != "mafia" or room.data.get("phase") != "night":
            return
        from . import mafia
        if room.data["roles"].get(uid) != mafia.DETECTIVE_ROLE or uid not in room.data["alive"]:
            return
        target = int(target_s)
        is_mafia = room.data["roles"].get(target) == mafia.MAFIA_ROLE
        async with room.lock:
            room.data["night_detective_acted"] = True
        verdict = "مافیا است 🔴" if is_mafia else "مافیا نیست 🟢"
        await q.edit_message_text(f"🔍 نتیجه بررسی {player_name(room, target)}: {verdict}")
        await social.maybe_resolve_mafia_night_early(room, context)
        return

    if d.startswith("mf_vote:"):
        _, chat_s, target_s = d.split(":")
        room = get_room(int(chat_s))
        if not room or room.game != "mafia" or room.data.get("phase") != "day":
            return
        if uid not in room.data["alive"]:
            await q.answer("شما دیگر زنده نیستید.", show_alert=True)
            return
        from . import mafia
        async with room.lock:
            mafia.register_day_vote(room, uid, int(target_s))
        await q.answer(f"رأی شما ثبت شد: {player_name(room, int(target_s))}")
        await social.maybe_resolve_mafia_day_early(room, context)
        return

    # ---- Werewolf night/day callbacks -------------------------------------
    if d.startswith("wf_night:"):
        _, chat_s, target_s = d.split(":")
        room = get_room(int(chat_s))
        if not room or room.game != "werewolf" or room.data.get("phase") != "night":
            return
        from . import werewolf
        if room.data["roles"].get(uid) != werewolf.WOLF_ROLE or uid not in room.data["alive"]:
            return
        async with room.lock:
            werewolf.register_wolf_vote(room, uid, int(target_s))
        await q.edit_message_text(f"🌕 طعمه انتخاب شد: {player_name(room, int(target_s))}")
        await social.maybe_resolve_werewolf_night_early(room, context)
        return

    if d.startswith("wf_seer:"):
        _, chat_s, target_s = d.split(":")
        room = get_room(int(chat_s))
        if not room or room.game != "werewolf" or room.data.get("phase") != "night":
            return
        from . import werewolf
        if room.data["roles"].get(uid) != werewolf.SEER_ROLE or uid not in room.data["alive"]:
            return
        target = int(target_s)
        is_wolf = room.data["roles"].get(target) == werewolf.WOLF_ROLE
        async with room.lock:
            room.data["night_seer_acted"] = True
        verdict = "گرگینه است 🔴" if is_wolf else "گرگینه نیست 🟢"
        await q.edit_message_text(f"🔮 نتیجه بررسی {player_name(room, target)}: {verdict}")
        await social.maybe_resolve_werewolf_night_early(room, context)
        return

    if d.startswith("wf_vote:"):
        _, chat_s, target_s = d.split(":")
        room = get_room(int(chat_s))
        if not room or room.game != "werewolf" or room.data.get("phase") != "day":
            return
        if uid not in room.data["alive"]:
            await q.answer("شما دیگر زنده نیستید.", show_alert=True)
            return
        from . import werewolf
        async with room.lock:
            werewolf.register_day_vote(room, uid, int(target_s))
        await q.answer(f"رأی شما ثبت شد: {player_name(room, int(target_s))}")
        await social.maybe_resolve_werewolf_day_early(room, context)
        return

    # ---- Hokm callbacks -----------------------------------------------------
    if d.startswith("hk_trump:"):
        _, chat_s, suit = d.split(":")
        room = get_room(int(chat_s))
        if not room or room.game != "hokm" or room.data.get("hakem") != uid or room.data.get("trump"):
            return
        await q.edit_message_text(f"👑 حکم انتخاب شد: {suit}")
        await hokm_flow.trump_chosen(room, context, suit)
        return

    if d.startswith("hk_play:"):
        _, chat_s, enc = d.split(":")
        room = get_room(int(chat_s))
        if not room or room.game != "hokm":
            return
        card = hokm.parse_card(enc)
        async with room.lock:
            ok, err = await hokm_flow.play_card(room, context, uid, card)
        if not ok:
            await q.answer(err, show_alert=True)
        return

    if d == "hk_noop":
        await q.answer("الان نمی‌توانید این کارت را بازی کنید.")
        return
