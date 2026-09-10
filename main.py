import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from config import BOT_TOKEN, BRAND_NAME, TELEGRAM_PROXY_URL
from db import init_db, reward
from admin import admin, admin_callback, handle_admin_text
from games.engine import get_room, close_room
from games.handlers import (start, games, profile, top, daily, invite, callback,
                             help_cmd, menu_button_router, MENU_BUTTON_PATTERN)

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


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("خطای پردازش‌نشده هنگام رسیدگی به یک آپدیت:", exc_info=context.error)


def build_application():
    builder = Application.builder().token(BOT_TOKEN)
    if TELEGRAM_PROXY_URL:
        log.info("اتصال به تلگرام از طریق پراکسی برقرار می‌شود.")
        # Both the general Bot API calls AND the long-polling getUpdates loop
        # need to go through the proxy, otherwise polling itself will hang.
        builder = builder.proxy(TELEGRAM_PROXY_URL).get_updates_proxy(TELEGRAM_PROXY_URL)
    else:
        log.warning(
            "SOCKS5_PROXY_URL تنظیم نشده. اگر سرور روی ایران هاست شده و به "
            "تلگرام دسترسی مستقیم ندارد، ربات در همین‌جا هنگ می‌کند یا کرش "
            "می‌کند و health check لیارا ناموفق می‌شود.")
    return builder.build()


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required")
    init_db()
    threading.Thread(target=health_server, daemon=True).start()

    app = build_application()
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

    # Non-slash entry point: the persistent bottom-keyboard buttons (checked
    # first, via an exact-label regex) so the bot is fully usable in a group
    # without anyone typing a "/" command. Falls through to text_router below
    # for anything that isn't one of the known button labels (e.g. in-game
    # text answers for the word/speed/name games).
    app.add_handler(MessageHandler(filters.Regex(MENU_BUTTON_PATTERN) & ~filters.COMMAND, menu_button_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    log.info("%s در حال راه‌اندازی...", BRAND_NAME)
    backoff = 5
    while True:
        try:
            app.run_polling(allowed_updates=Update.ALL_TYPES)
            break  # run_polling only returns after a graceful shutdown
        except Exception as e:
            log.error(
                "اتصال به تلگرام برقرار نشد (اگر روی ایران هاست شده‌اید، "
                "SOCKS5_PROXY_URL را بررسی کنید): %s", e)
            log.info("تلاش مجدد در %s ثانیه...", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    main()