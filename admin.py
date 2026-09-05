import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_IDS, BRAND_NAME, BROADCAST_BATCH_SIZE, BROADCAST_BATCH_DELAY
from db import leaderboard, stats_overview, set_banned, all_user_ids, manual_grant_license

log = logging.getLogger("hexiron-games.admin")


def is_admin(uid):
    return uid in ADMIN_IDS


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 آمار کلی", callback_data="adm:stats"),
         InlineKeyboardButton("🏆 لیدربرد", callback_data="adm:top")],
        [InlineKeyboardButton("📢 پیام همگانی", callback_data="adm:broadcast"),
         InlineKeyboardButton("🔑 مدیریت لایسنس", callback_data="adm:license")],
        [InlineKeyboardButton("🚫 مسدود کردن کاربر", callback_data="adm:ban"),
         InlineKeyboardButton("✅ رفع مسدودی", callback_data="adm:unban")],
    ])


async def admin(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    await update.message.reply_text(f"🛠 پنل مدیریت {BRAND_NAME}", reply_markup=admin_menu())


async def admin_callback(update, context):
    """Registered with pattern=^adm: — only ever called for admin-menu taps."""
    q = update.callback_query
    uid = q.from_user.id
    if not is_admin(uid):
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await q.answer()
    action = q.data.split(":", 1)[1]

    if action == "stats":
        s = stats_overview()
        await q.message.reply_text(
            "📊 آمار کلی\n\n"
            f"👤 کاربران: {s['users']}\n🚫 مسدود: {s['banned']}\n"
            f"🎮 مجموع بازی‌های انجام‌شده: {s['games_played']}\n"
            f"💎 لایسنس‌های فعال (گروه/چت): {s['active_licenses']}\n"
            f"🎁 جایزه روزانه گرفته‌شده امروز: {s['daily_claims_today']}")
    elif action == "top":
        rows = leaderboard(15)
        await q.message.reply_text("🏆 Top 15\n\n" + "\n".join(
            f"{i}. {r['name']} — XP {r['xp']} — Coins {r['coins']}" for i, r in enumerate(rows, 1)))
    elif action == "broadcast":
        context.user_data["admin_pending"] = "broadcast"
        await q.message.reply_text("📢 متن پیام همگانی را در پیام بعدی بفرستید. برای لغو /cancel را بفرستید.")
    elif action == "license":
        context.user_data["admin_pending"] = "license_grant"
        await q.message.reply_text(
            "🔑 برای فعال/غیرفعال‌سازی دستی لایسنس یک چت (برای مواقعی که Sales API در دسترس نیست)، "
            "آیدی عددی چت و تعداد روز را با فاصله بفرستید.\n"
            "مثال فعال‌سازی ۳۰ روزه: -100123456789 30\n"
            "مثال غیرفعال‌سازی: -100123456789 0\n"
            "برای لغو /cancel را بفرستید.")
    elif action == "ban":
        context.user_data["admin_pending"] = "ban"
        await q.message.reply_text("🚫 آیدی عددی کاربری که باید مسدود شود را بفرستید.")
    elif action == "unban":
        context.user_data["admin_pending"] = "unban"
        await q.message.reply_text("✅ آیدی عددی کاربری که باید رفع مسدودی شود را بفرستید.")


async def handle_admin_text(update, context):
    """Called from text_router before any game logic. Returns True if it
    consumed this message (i.e. the admin was mid-flow filling something in)."""
    uid = update.effective_user.id
    pending = context.user_data.get("admin_pending")
    if not pending or not is_admin(uid):
        return False

    txt = update.message.text.strip()
    if txt == "/cancel":
        context.user_data.pop("admin_pending", None)
        await update.message.reply_text("لغو شد.")
        return True

    if pending == "broadcast":
        context.user_data.pop("admin_pending", None)
        await update.message.reply_text("📢 در حال ارسال پیام همگانی؛ گزارش نهایی پس از پایان ارسال می‌شود...")
        asyncio.create_task(_broadcast(context, uid, txt))
        return True

    if pending == "license_grant":
        parts = txt.split()
        if len(parts) != 2 or not parts[0].lstrip("-").isdigit() or not parts[1].isdigit():
            await update.message.reply_text("❌ فرمت اشتباه است. مثال: -100123456789 30")
            return True
        chat_id, days = int(parts[0]), int(parts[1])
        manual_grant_license(chat_id, active=days > 0, days=days)
        context.user_data.pop("admin_pending", None)
        await update.message.reply_text(
            f"✅ لایسنس چت {chat_id} برای {days} روز فعال شد." if days > 0
            else f"✅ لایسنس چت {chat_id} غیرفعال شد.")
        return True

    if pending in ("ban", "unban"):
        if not txt.lstrip("-").isdigit():
            await update.message.reply_text("❌ آیدی عددی معتبر نیست.")
            return True
        target = int(txt)
        set_banned(target, pending == "ban")
        context.user_data.pop("admin_pending", None)
        await update.message.reply_text(
            f"🚫 کاربر {target} مسدود شد." if pending == "ban" else f"✅ کاربر {target} رفع مسدودی شد.")
        return True

    context.user_data.pop("admin_pending", None)
    return False


async def _broadcast(context, admin_uid, text):
    ids = all_user_ids()
    sent = failed = 0
    for i, uid in enumerate(ids, 1):
        try:
            await context.bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        if i % BROADCAST_BATCH_SIZE == 0:
            await asyncio.sleep(BROADCAST_BATCH_DELAY)
    try:
        await context.bot.send_message(
            admin_uid, f"📢 پیام همگانی تمام شد.\n✅ ارسال موفق: {sent}\n❌ ناموفق: {failed}")
    except Exception:
        log.warning("گزارش پایان broadcast به ادمین ارسال نشد.")
