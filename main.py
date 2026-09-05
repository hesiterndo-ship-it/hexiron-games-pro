import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from config import BOT_TOKEN, BRAND_NAME
from db import init_db, reward
from admin import admin, admin_callback, handle_admin_text
from games.engine import get_room, close_room
from games.handlers import start, games, profile, top, daily, invite, callback

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("hexiron-games")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def health_server():
    HTTPServer(("0.0.0.0", 8080), HealthHandler).serve_forever()


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    # Admin mid-flow input (broadcast text, license grant, ban id, ...) wins first.
    if await handle_admin_text(update, context):
        return

    room = get_room(update.effective_chat.id)
    if not room:
        return
    txt = update.message.text.strip()
    uid = update.effective_user.id
    if room.game == "word" and txt == room.data.get("word"):
        reward(uid, 20, 10, True, "word_win")
        close_room(update.effective_chat.id)
        await update.message.reply_text("🏆 درست حدس زدی! +20 XP +10 Coins")
    elif room.game == "speed" and txt == room.data.get("target"):
        reward(uid, 15, 8, True, "speed_win")
        close_room(update.effective_chat.id)
        await update.message.reply_text(f"⚡ {update.effective_user.full_name} برنده شد! +15 XP +8 Coins")
    elif room.game == "name":
        if txt:
            reward(uid, 10, 5, False, "name_submit")
            await update.message.reply_text("✅ جواب‌ها ثبت شد. +10 XP")


async def help_cmd(update, context):
    await update.message.reply_text(
        f"📚 {BRAND_NAME}\n\n"
        "/games — فهرست بازی‌ها\n/profile — پروفایل\n/top — رتبه‌بندی\n"
        "/daily — جایزه روزانه\n/invite — لینک دعوت\n/help — راهنما\n\n"
        "بازی‌های گروهی (حکم، مافیا، گرگینه، منچ) از داخل منوی بازی ساخته می‌شوند.")


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("خطای پردازش‌نشده هنگام رسیدگی به یک آپدیت:", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required")
    init_db()
    threading.Thread(target=health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("games", games))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("invite", invite))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin))

    # Admin-menu callbacks must be checked before the general game callback.
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^adm:"))
    app.add_handler(CallbackQueryHandler(callback))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    log.info("%s در حال راه‌اندازی...", BRAND_NAME)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
