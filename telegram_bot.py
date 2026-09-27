"""
NIET Cloud Portal Automation - Private Telegram Bot Integration
Provides secure, personal Telegram bot access for student attendance, today's schedule,
tomorrow's schedule, weekly timetable, attendance target calculations, and automatic
daily morning notifications.

Uses python-telegram-bot with long polling (no webhooks or ngrok required).
Reuses existing portal.py and assistant.py extraction logic and caching.
"""

import os
import sys
import json
import asyncio
import logging
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Set, Optional

from dotenv import load_dotenv

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Telegram library imports
from telegram import Update, BotCommand
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Project modules
import assistant
import portal

# Setup Base Paths & Environment
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    load_dotenv(dotenv_path=ENV_FILE)
else:
    load_dotenv()

# Read Secrets & Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
COLLEGE_PASSWORD = os.getenv("COLLEGE_PASSWORD", "").strip()
COLLEGE_ID = os.getenv("COLLEGE_ID", "").strip()

# Morning Notification Settings (Configurable via .env)
MORNING_NOTIFICATION_ENABLED = os.getenv("MORNING_NOTIFICATION_ENABLED", "true").strip().lower() in ("true", "1", "yes")
MORNING_NOTIFICATION_TIME = os.getenv("MORNING_NOTIFICATION_TIME", "08:00").strip()
TIMEZONE_NAME = os.getenv("TIMEZONE", "Asia/Kolkata").strip()

# State persistence for duplicate notification protection
STATE_FILE = BASE_DIR / "notification_state.json"

# Response Messages
UNAUTHORIZED_MESSAGE = "Unauthorized user"
LOADING_MESSAGE = "🔄 Fetching your information from NIET Cloud..."

# Setup Logging (Terminal only, technical details never sent to user)
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)
logger = logging.getLogger("NIETTelegramBot")


def get_app_timezone():
    """Returns the configured timezone (Asia/Kolkata by default) with resilient fallback."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TIMEZONE_NAME)
    except Exception:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo("Asia/Kolkata")
        except Exception:
            from datetime import timezone, timedelta
            return timezone(timedelta(hours=5, minutes=30), name="IST")


def get_last_notification_date() -> Optional[str]:
    """Reads the last successful notification date (YYYY-MM-DD) from persistent state file."""
    if not STATE_FILE.exists():
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("last_notification_date")
    except Exception:
        return None


def set_last_notification_date(date_str: str) -> None:
    """Saves the last successful notification date to avoid duplicate daily sends."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_notification_date": date_str}, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save notification state: {e}")


def get_allowed_user_ids() -> Set[int]:
    """
    Reads TELEGRAM_ALLOWED_USER_ID from environment.
    Treated strictly as numeric Telegram user ID(s).
    Never hardcoded in source code.
    """
    raw_ids = os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip()
    if not raw_ids:
        raw_ids = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()

    allowed = set()
    if raw_ids:
        for item in raw_ids.split(","):
            item = item.strip()
            if item.isdigit():
                allowed.add(int(item))
    return allowed


def is_user_authorized(user_id: Optional[int]) -> bool:
    """
    Checks if a Telegram user is authorized.
    Only the Telegram user whose numeric ID matches TELEGRAM_ALLOWED_USER_ID can use protected commands.
    """
    if user_id is None:
        return False
    allowed_ids = get_allowed_user_ids()
    if not allowed_ids:
        return False
    return user_id in allowed_ids


def check_authorization(func):
    """
    Decorator to verify authorization BEFORE executing any handler.
    Unauthorized users receive a generic denial message without exposing any
    college details, attendance, timetable, or error info.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id if update.effective_user else None
        if not is_user_authorized(user_id):
            if update.effective_message:
                await update.effective_message.reply_text(UNAUTHORIZED_MESSAGE)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def sanitize_text(text: str) -> str:
    """
    Redacts any sensitive tokens, passwords, or credentials from log strings.
    """
    if not text:
        return ""
    if TELEGRAM_BOT_TOKEN and len(TELEGRAM_BOT_TOKEN) > 5:
        text = text.replace(TELEGRAM_BOT_TOKEN, "[REDACTED_TOKEN]")
    if COLLEGE_PASSWORD and len(COLLEGE_PASSWORD) > 2:
        text = text.replace(COLLEGE_PASSWORD, "[REDACTED_PASSWORD]")
    return text


def format_error_for_user(err: Exception) -> str:
    """
    Generates a clean, friendly error message without exposing credentials,
    tokens, cookies, or technical stack traces.
    """
    err_str = str(err).lower()
    if any(keyword in err_str for keyword in ("timeout", "connection", "connect", "unavailable", "502", "503", "504", "net::")):
        return "⚠️ NIET Cloud is currently unavailable. Please try again in a few minutes."
    return "⚠️ I couldn't retrieve the requested information right now. Please try again."


async def send_safe_message(update: Update, text: str) -> Optional[any]:
    """
    Sends message trying Markdown first, falling back to plain text if needed.
    """
    clean_text = sanitize_text(text)
    if not update.effective_message:
        return None

    try:
        return await update.effective_message.reply_text(clean_text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        try:
            return await update.effective_message.reply_text(clean_text, parse_mode=None)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return None


async def edit_or_reply(status_msg, update: Update, text: str):
    """
    Updates the temporary loading message with the final result.
    Falls back to a new message if editing fails.
    """
    clean_text = sanitize_text(text)
    try:
        await status_msg.edit_text(clean_text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        try:
            await status_msg.edit_text(clean_text, parse_mode=None)
        except Exception:
            await send_safe_message(update, clean_text)


# ==============================================================================
# AUTOMATIC MORNING NOTIFICATION SCHEDULER
# ==============================================================================

_scheduler_running = False

async def morning_scheduler_loop(bot):
    """
    Background worker loop running inside the Telegram bot event loop.
    Checks time in TIMEZONE every 30 seconds.
    Sends daily morning notification to TELEGRAM_ALLOWED_USER_ID at MORNING_NOTIFICATION_TIME.
    Tracks state in notification_state.json to prevent duplicate notifications.
    """
    global _scheduler_running
    if _scheduler_running:
        logger.info("Morning notification scheduler loop is already active.")
        return
    _scheduler_running = True

    logger.info(
        f"Morning notification scheduler started. "
        f"Time: {MORNING_NOTIFICATION_TIME} | Timezone: {TIMEZONE_NAME} | Enabled: {MORNING_NOTIFICATION_ENABLED}"
    )

    while True:
        try:
            if not MORNING_NOTIFICATION_ENABLED:
                await asyncio.sleep(60)
                continue

            tz = get_app_timezone()
            now = datetime.now(tz)
            today_str = now.strftime("%Y-%m-%d")
            current_hhmm = now.strftime("%H:%M")

            if current_hhmm == MORNING_NOTIFICATION_TIME:
                last_date = get_last_notification_date()
                if last_date != today_str:
                    allowed_ids = get_allowed_user_ids()
                    if allowed_ids:
                        # Generate the morning message in worker thread
                        msg = await asyncio.to_thread(assistant.cmd_morning)
                        clean_msg = sanitize_text(msg)
                        for uid in allowed_ids:
                            try:
                                await bot.send_message(chat_id=uid, text=clean_msg, parse_mode=None)
                                logger.info(f"Morning notification successfully sent to user {uid} for {today_str}")
                            except Exception as e:
                                logger.error(f"Failed to send morning notification to user {uid}: {type(e).__name__}")
                        set_last_notification_date(today_str)
                    else:
                        logger.warning("No TELEGRAM_ALLOWED_USER_ID set; cannot dispatch morning notification.")

            await asyncio.sleep(30)
        except asyncio.CancelledError:
            logger.info("Morning notification scheduler cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in morning scheduler loop: {type(e).__name__}")
            await asyncio.sleep(30)


# ==============================================================================
# TELEGRAM COMMAND HANDLERS
# ==============================================================================

@check_authorization
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /start
    Returns the clean welcome message.
    """
    msg = assistant.format_start_message()
    await send_safe_message(update, msg)


@check_authorization
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /help
    Shows available commands with friendly descriptions.
    """
    msg = assistant.format_help_message()
    await send_safe_message(update, msg)


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /id and /myid
    Helps verify the Telegram user ID.
    Does NOT access NIET Cloud or expose any student/college data.
    """
    user_id = update.effective_user.id if update.effective_user else None
    if not is_user_authorized(user_id):
        if update.effective_message:
            await update.effective_message.reply_text(UNAUTHORIZED_MESSAGE)
        return

    msg = f"🆔 Your Telegram User ID is: `{user_id}`"
    await send_safe_message(update, msg)


@check_authorization
async def attendance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /attendance
    Retrieves overall attendance, attended classes, absent classes,
    and subject-wise breakdown with status indicators.
    """
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    status_msg = await update.message.reply_text(LOADING_MESSAGE)

    try:
        response = await asyncio.to_thread(assistant.cmd_attendance)
        await edit_or_reply(status_msg, update, response)
    except Exception as e:
        logger.error(f"Error in /attendance: {type(e).__name__} - {sanitize_text(str(e))}")
        user_err = format_error_for_user(e)
        await edit_or_reply(status_msg, update, user_err)


@check_authorization
async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /today
    Retrieves today's classes with time, subject, faculty, and room.
    """
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    status_msg = await update.message.reply_text(LOADING_MESSAGE)

    try:
        response = await asyncio.to_thread(assistant.cmd_today)
        await edit_or_reply(status_msg, update, response)
    except Exception as e:
        logger.error(f"Error in /today: {type(e).__name__} - {sanitize_text(str(e))}")
        user_err = format_error_for_user(e)
        await edit_or_reply(status_msg, update, user_err)


@check_authorization
async def tomorrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /tomorrow
    Retrieves tomorrow's classes with time, subject, faculty, and room.
    """
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    status_msg = await update.message.reply_text(LOADING_MESSAGE)

    try:
        response = await asyncio.to_thread(assistant.cmd_tomorrow)
        await edit_or_reply(status_msg, update, response)
    except Exception as e:
        logger.error(f"Error in /tomorrow: {type(e).__name__} - {sanitize_text(str(e))}")
        user_err = format_error_for_user(e)
        await edit_or_reply(status_msg, update, user_err)


@check_authorization
async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /schedule
    Retrieves the complete Monday to Friday weekly timetable.
    """
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    status_msg = await update.message.reply_text(LOADING_MESSAGE)

    try:
        response = await asyncio.to_thread(assistant.cmd_schedule)
        await edit_or_reply(status_msg, update, response)
    except Exception as e:
        logger.error(f"Error in /schedule: {type(e).__name__} - {sanitize_text(str(e))}")
        user_err = format_error_for_user(e)
        await edit_or_reply(status_msg, update, user_err)


@check_authorization
async def all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /all
    Returns attendance summary followed by today's classes.
    """
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    status_msg = await update.message.reply_text(LOADING_MESSAGE)

    try:
        response = await asyncio.to_thread(assistant.cmd_all)
        await edit_or_reply(status_msg, update, response)
    except Exception as e:
        logger.error(f"Error in /all: {type(e).__name__} - {sanitize_text(str(e))}")
        user_err = format_error_for_user(e)
        await edit_or_reply(status_msg, update, user_err)


@check_authorization
async def morning_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /morning
    Manually triggers the daily morning notification message for testing.
    """
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    status_msg = await update.message.reply_text(LOADING_MESSAGE)

    try:
        response = await asyncio.to_thread(assistant.cmd_morning)
        await edit_or_reply(status_msg, update, response)
    except Exception as e:
        logger.error(f"Error in /morning: {type(e).__name__} - {sanitize_text(str(e))}")
        user_err = format_error_for_user(e)
        await edit_or_reply(status_msg, update, user_err)


@check_authorization
async def low_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /low
    Displays subjects with attendance below 75%.
    """
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    status_msg = await update.message.reply_text(LOADING_MESSAGE)

    try:
        response = await asyncio.to_thread(assistant.cmd_low)
        await edit_or_reply(status_msg, update, response)
    except Exception as e:
        logger.error(f"Error in /low: {type(e).__name__} - {sanitize_text(str(e))}")
        user_err = format_error_for_user(e)
        await edit_or_reply(status_msg, update, user_err)


@check_authorization
async def required_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /required
    Calculates consecutive classes needed to achieve 75% or safe classes to miss.
    """
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    status_msg = await update.message.reply_text(LOADING_MESSAGE)

    try:
        response = await asyncio.to_thread(assistant.cmd_required)
        await edit_or_reply(status_msg, update, response)
    except Exception as e:
        logger.error(f"Error in /required: {type(e).__name__} - {sanitize_text(str(e))}")
        user_err = format_error_for_user(e)
        await edit_or_reply(status_msg, update, user_err)


@check_authorization
async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /dashboard
    Returns a comprehensive student executive dashboard.
    """
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    status_msg = await update.message.reply_text(LOADING_MESSAGE)

    try:
        response = await asyncio.to_thread(assistant.cmd_dashboard)
        await edit_or_reply(status_msg, update, response)
    except Exception as e:
        logger.error(f"Error in /dashboard: {type(e).__name__} - {sanitize_text(str(e))}")
        user_err = format_error_for_user(e)
        await edit_or_reply(status_msg, update, user_err)


@check_authorization
async def text_message_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles natural text input (e.g. typing 'attendance' without the leading slash).
    """
    if not update.message or not update.message.text:
        return

    user_text = update.message.text.strip()
    norm = user_text.lower()
    if norm.startswith("/"):
        norm = norm[1:].strip()

    if norm in ("attendance", "att", "attend"):
        await attendance_command(update, context)
    elif norm in ("today", "classes", "today's classes", "todays classes"):
        await today_command(update, context)
    elif norm in ("tomorrow", "tom", "next"):
        await tomorrow_command(update, context)
    elif norm in ("schedule", "timetable", "time table", "weekly"):
        await schedule_command(update, context)
    elif norm in ("all", "briefing", "overview", "summary"):
        await all_command(update, context)
    elif norm in ("morning", "notify", "daily"):
        await morning_command(update, context)
    elif norm in ("low", "shortage", "short"):
        await low_command(update, context)
    elif norm in ("required", "target", "bunk", "calc"):
        await required_command(update, context)
    elif norm in ("dashboard", "dash"):
        await dashboard_command(update, context)
    elif norm in ("help", "hi", "hello", "?"):
        await help_command(update, context)
    elif norm in ("start",):
        await start_command(update, context)
    elif norm in ("id", "myid"):
        await id_command(update, context)
    else:
        # Default help guide for unrecognized commands
        await help_command(update, context)


async def post_init_setup(application):
    """Registers bot commands with Telegram and launches the background scheduler."""
    commands = [
        BotCommand("dashboard", "Complete overview"),
        BotCommand("attendance", "View attendance"),
        BotCommand("low", "Subjects below 75%"),
        BotCommand("required", "Classes needed to reach 75%"),
        BotCommand("today", "Today's classes"),
        BotCommand("tomorrow", "Tomorrow's classes"),
        BotCommand("schedule", "Weekly timetable"),
        BotCommand("all", "Complete attendance information"),
        BotCommand("morning", "Test morning notification"),
        BotCommand("help", "Show commands"),
    ]
    try:
        await application.bot.set_my_commands(commands)
    except Exception as e:
        logger.warning(f"Could not set Telegram bot commands menu: {e}")

    # Launch background morning notification scheduler inside the bot event loop
    if MORNING_NOTIFICATION_ENABLED:
        application.create_task(morning_scheduler_loop(application.bot))

def run_bot():
    """Starts the Telegram bot using long polling."""
    if not TELEGRAM_BOT_TOKEN:
        print("\n" + "!" * 65, file=sys.stderr)
        print("CONFIGURATION ERROR: TELEGRAM_BOT_TOKEN is missing!", file=sys.stderr)
        print("!" * 65, file=sys.stderr)
        print("Please define TELEGRAM_BOT_TOKEN in your .env file.\n", file=sys.stderr)
        sys.exit(1)

    allowed = get_allowed_user_ids()

    # Startup Banner (Terminal only, requirement-compliant)
    print("=" * 65, flush=True)
    print("       NIET College Assistant - Telegram Bot", flush=True)
    print("=" * 65, flush=True)
    print("[*] Telegram bot is running...", flush=True)
    print("[*] Mode: Long Polling (Local Development)", flush=True)
    if allowed:
        print("[*] Access Control: Private (authorized user configured)", flush=True)
    else:
        print("[!] Access Control: Warning - TELEGRAM_ALLOWED_USER_ID is not configured in .env", flush=True)
    print("[*] Morning notification scheduler started.", flush=True)
    print(f"    Time: {MORNING_NOTIFICATION_TIME}", flush=True)
    print(f"    Timezone: {TIMEZONE_NAME}", flush=True)
    print(f"    Enabled: {str(MORNING_NOTIFICATION_ENABLED).lower()}", flush=True)
    print("[*] Press Ctrl+C in this terminal to stop.", flush=True)
    print("=" * 65, flush=True)

    # Build Application
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init_setup)
        .build()
    )

    # Register Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("dashboard", dashboard_command))
    application.add_handler(CommandHandler("attendance", attendance_command))
    application.add_handler(CommandHandler("low", low_command))
    application.add_handler(CommandHandler("required", required_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("tomorrow", tomorrow_command))
    application.add_handler(CommandHandler("schedule", schedule_command))
    application.add_handler(CommandHandler("all", all_command))
    application.add_handler(CommandHandler("morning", morning_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("myid", id_command))

    # Text message fallback dispatcher
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_dispatcher))

    # Run Long Polling
    try:
        application.run_polling(drop_pending_updates=True)
    except (KeyboardInterrupt, SystemExit):
        print("\n[*] Telegram bot stopped. Goodbye!\n", flush=True)
    except Exception as e:
        clean_err = sanitize_text(str(e))
        print(f"\n[!] Bot encountered an error: {clean_err}\n", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    run_bot()
