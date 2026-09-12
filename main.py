
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, BRAND_NAME, TELEGRAM_PROXY_URL
from db import init_db, reward
from admin import admin, admin_callback, handle_admin_text
from games.engine import get_room, close_room
from games.handlers import (
    start,
    games,
    profile,
    top,
    daily,
    invite,
    callback,
    help_cmd,
    menu_button_router,
    MENU_BUTTON_PATTERN,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

log = logging.getLogger("hexiron-games")


# ============================================================
# HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def health_server():
    try:
        server = HTTPServer(("0.0.0.0", 8080), HealthHandler)
        log.info("Health server listening on 0.0.0.0:8080")
        server.serve_forever()
    except Exception:
        log.exception("Health server failed to start")


# ============================================================
# TEXT ROUTER
# ============================================================

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    # Admin mid-flow input
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

        await update.message.reply_text(
            "🏆 درست حدس زدی! +20 XP +10 Coins"
        )

    elif room.game == "speed" and txt == room.data.get("target"):
        reward(uid, 15, 8, True, "speed_win")
        close_room(update.effective_chat.id)

        await update.message.reply_text(
            f"⚡ {update.effective_user.full_name} برنده شد! +15 XP +8 Coins"
        )

    elif room.game == "name":
        if txt:
            reward(uid, 10, 5, False, "name_submit")

            await update.message.reply_text(
                "✅ جواب‌ها ثبت شد. +10 XP"
            )


# ============================================================
# ERROR HANDLER
# ============================================================

async def on_error(update, context):
    log.error(
        "خطای پردازش‌نشده هنگام رسیدگی به یک آپدیت:",
        exc_info=context.error,
    )


# ============================================================
# BUILD APPLICATION
# ============================================================

def build_application():
    builder = Application.builder().token(BOT_TOKEN)

    if TELEGRAM_PROXY_URL:
        log.info("اتصال به تلگرام از طریق پراکسی برقرار می‌شود.")

        builder = (
            builder
            .proxy(TELEGRAM_PROXY_URL)
            .get_updates_proxy(TELEGRAM_PROXY_URL)
        )

    else:
        log.warning(
            "SOCKS5_PROXY_URL تنظیم نشده. اگر سرور روی ایران هاست شده "
            "و به تلگرام دسترسی مستقیم ندارد، اتصال تلگرام ممکن است "
            "ناموفق شود."
        )

    app = builder.build()

    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("games", games))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("invite", invite))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin))

    # Admin callbacks first
    app.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern=r"^adm:",
        )
    )

    # General game callbacks
    app.add_handler(
        CallbackQueryHandler(callback)
    )

    # Bottom keyboard buttons
    app.add_handler(
        MessageHandler(
            filters.Regex(MENU_BUTTON_PATTERN) & ~filters.COMMAND,
            menu_button_router,
        )
    )

    # Normal text / game answers
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router,
        )
    )

    return app


# ============================================================
# TELEGRAM RUNNER
# ============================================================

def run_bot():
    backoff = 5

    while True:
        app = None

        try:
            log.info("ساخت Application جدید...")

            app = build_application()

            log.info("%s در حال راه‌اندازی...", BRAND_NAME)

            app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
            )

            # If run_polling exits normally, stop retrying.
            log.info("Telegram application stopped normally.")
            break

        except Exception:
            log.exception(
                "اتصال/اجرای ربات با خطا مواجه شد. "
                "Application فعلی دور انداخته می‌شود."
            )

            log.info(
                "تلاش مجدد در %s ثانیه...",
                backoff,
            )

            time.sleep(backoff)

            backoff = min(
                backoff * 2,
                60,
            )

        finally:
            app = None


# ============================================================
# MAIN
# ============================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required")

    init_db()

    # Health server must stay alive independently from Telegram.
    threading.Thread(
        target=health_server,
        daemon=True,
        name="health-server",
    ).start()

    # Telegram runner
    run_bot()


if __name__ == "__main__":
    main()
