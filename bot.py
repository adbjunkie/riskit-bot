"""
RiskIt Bot – Basic Anonymous Photo Sharing

Users send photos privately → confirm → the bot posts anonymously to the target supergroup.
No username is shown; the post comes from the bot account.

Designed to be deployed via GitHub + Railway (or Docker).
All secrets come from environment variables.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Load .env file if present (for local development).
# On Railway / production, variables are injected by the platform.
load_dotenv()

# ==================== CONFIGURATION (Environment Variables) ====================
# Required:
#   TELEGRAM_BOT_TOKEN   - from @BotFather
#   GROUP_CHAT_ID        - target supergroup ID (starts with -100...)
#
# Optional:
#   ADMIN_USER_ID        - your Telegram user ID (for future admin features)
#   COOLDOWN_SECONDS     - default: 300 (5 minutes)
#   DAILY_LIMIT          - default: 10
#   DATA_DIR             - default: "data" (Railway Volume should point here)

def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}\n"
            f"Set it in Railway (Variables tab) or in a local .env file."
        )
    return value

def _get_int_env(name: str, default: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        if default is not None:
            return default
        raise RuntimeError(f"Missing required environment variable: {name}")
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer, got: {raw!r}")

TELEGRAM_BOT_TOKEN = _require_env("TELEGRAM_BOT_TOKEN")
GROUP_CHAT_ID = _get_int_env("GROUP_CHAT_ID")

# Optional
ADMIN_USER_ID = _get_int_env("ADMIN_USER_ID", 0) or None

# Rate limits (override via env if desired)
COOLDOWN_SECONDS = _get_int_env("COOLDOWN_SECONDS", 5 * 60)
DAILY_LIMIT = _get_int_env("DAILY_LIMIT", 10)

# Data dir for rate_limits.json (persist this on Railway with a Volume)
DATA_DIR = os.getenv("DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)
RATE_LIMITS_FILE = os.path.join(DATA_DIR, "rate_limits.json")

# ==================== LOGGING ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# In-memory pending confirmations (lost on restart — fine for this flow)
# user_id -> {"file_id": str, "caption": str|None, "chat_id": int, ...}
pending_confirmations: dict = {}

# ==================== RATE LIMITING ====================

def load_rate_limits() -> dict:
    if os.path.exists(RATE_LIMITS_FILE):
        try:
            with open(RATE_LIMITS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load rate limits: {e}")
    return {"last_posts": {}, "daily": {}}


def save_rate_limits(data: dict) -> None:
    try:
        with open(RATE_LIMITS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save rate limits: {e}")


def get_today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def is_rate_limited(user_id: int):
    """Returns (is_limited: bool, message: str|None)"""
    data = load_rate_limits()
    uid = str(user_id)
    now = time.time()

    # Cooldown
    last = data.get("last_posts", {}).get(uid)
    if last is not None and (now - last) < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        return True, f"⏳ Rate limit: please wait {remaining} seconds before posting again."

    # Daily limit
    today = get_today_str()
    count = data.get("daily", {}).get(today, {}).get(uid, 0)
    if count >= DAILY_LIMIT:
        return True, f"📅 Daily limit reached ({DAILY_LIMIT} posts per day). Come back tomorrow!"

    return False, None


def record_post(user_id: int) -> None:
    data = load_rate_limits()
    uid = str(user_id)
    now = time.time()

    data.setdefault("last_posts", {})[uid] = now

    today = get_today_str()
    daily = data.setdefault("daily", {}).setdefault(today, {})
    daily[uid] = daily.get(uid, 0) + 1

    # Prune daily entries older than 7 days
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
    data["daily"] = {d: v for d, v in data.get("daily", {}).items() if d >= cutoff}

    save_rate_limits(data)


# ==================== TEXTS ====================

RULES_TEXT = """⚠️ <b>RISK IT — Anonymous Photo Sharing</b>

<b>Basic Rules:</b>
• Only post <b>your own</b> photos (no reposts, no stolen content)
• No underage, illegal, revenge, or non-consensual material
• No spam, extreme gore, or hate speech
• Once posted, the photo is visible to everyone in the group
• Be respectful — the group has moderators

This is a <b>basic version</b>. More features (AI, battles, etc.) coming later.

Send a photo in this private chat to continue."""

POST_INSTRUCTIONS = (
    "📸 Send a photo (you may include a short caption).\n\n"
    "You will get a confirmation before anything is posted."
)


# ==================== HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(RULES_TEXT, parse_mode="HTML")
    await update.message.reply_text(POST_INSTRUCTIONS)


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(RULES_TEXT, parse_mode="HTML")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    if user_id in pending_confirmations:
        pending_confirmations.pop(user_id, None)
        await update.message.reply_text("❌ Pending photo cleared. Send a new one anytime.")
    else:
        await update.message.reply_text("No pending photo to cancel.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive photo in private chat only, store it, ask for confirmation."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Please send photos to me in a private chat only.")
        return

    user_id = update.effective_user.id

    if user_id in pending_confirmations:
        await update.message.reply_text(
            "You already have a photo waiting for confirmation.\n"
            "Use /cancel to discard it, then send a new photo."
        )
        return

    limited, msg = is_rate_limited(user_id)
    if limited:
        await update.message.reply_text(msg)
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id
    user_caption = update.message.caption

    pending_confirmations[user_id] = {
        "file_id": file_id,
        "caption": user_caption,
        "chat_id": update.effective_chat.id,
    }

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Post it", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="confirm_no"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    confirm_caption = (
        "Do you want to post this photo <b>anonymously</b> in the group?\n\n"
        "• The post will appear from the bot (your username stays hidden)\n"
        "• Reactions are enabled in the group\n"
        "• If you added a caption it will be included"
    )
    if user_caption:
        safe_caption = user_caption.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        confirm_caption += f"\n\n<b>Your caption:</b>\n<i>{safe_caption}</i>"

    try:
        sent_msg = await update.message.reply_photo(
            photo=file_id,
            caption=confirm_caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        pending_confirmations[user_id]["confirm_msg_id"] = sent_msg.message_id
    except Exception as e:
        logger.error(f"Failed to send confirmation photo to user {user_id}: {e}")
        pending_confirmations.pop(user_id, None)
        await update.message.reply_text(
            "❌ Something went wrong while preparing the confirmation. Please try sending the photo again."
        )


async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Yes / Cancel button presses."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    item = pending_confirmations.get(user_id)
    if not item:
        await query.edit_message_caption(
            "❌ This confirmation expired or was already handled.",
            reply_markup=None,
        )
        return

    if query.data == "confirm_no":
        pending_confirmations.pop(user_id, None)
        try:
            await query.edit_message_caption("❌ Cancelled. Photo was not posted.", reply_markup=None)
        except Exception:
            pass
        return

    if query.data == "confirm_yes":
        limited, limit_msg = is_rate_limited(user_id)
        if limited:
            pending_confirmations.pop(user_id, None)
            await query.edit_message_caption(limit_msg, reply_markup=None)
            return

        try:
            await context.bot.send_photo(
                chat_id=GROUP_CHAT_ID,
                photo=item["file_id"],
                caption=item.get("caption"),
            )

            record_post(user_id)
            pending_confirmations.pop(user_id, None)

            success = "✅ Posted anonymously to the group!\n\nThank you."
            try:
                await query.edit_message_caption(success, reply_markup=None)
            except Exception:
                await context.bot.send_message(chat_id=item["chat_id"], text=success)

        except Exception as e:
            logger.error(f"Failed to send photo to group for user {user_id}: {e}")
            pending_confirmations.pop(user_id, None)
            error_text = (
                "❌ Failed to post to the group.\n"
                "Make sure the bot is an admin in the target supergroup with 'Post Messages' permission."
            )
            try:
                await query.edit_message_caption(error_text, reply_markup=None)
            except Exception:
                await context.bot.send_message(chat_id=item.get("chat_id", user_id), text=error_text)


async def handle_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all for non-photo, non-command messages in private chat."""
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(
        "📸 Send a photo to post it anonymously.\n"
        "Commands: /start  /rules  /cancel"
    )


# ==================== MAIN ====================

def main() -> None:
    # Safe token preview for logs
    token_preview = (
        TELEGRAM_BOT_TOKEN[:8] + "..." + TELEGRAM_BOT_TOKEN[-6:]
        if len(TELEGRAM_BOT_TOKEN) > 14 else "***"
    )

    logger.info("=== RiskIt Bot Starting ===")
    logger.info(f"Bot token: {token_preview}")
    logger.info(f"Target Group Chat ID: {GROUP_CHAT_ID}")
    if ADMIN_USER_ID:
        logger.info(f"Admin User ID: {ADMIN_USER_ID}")
    logger.info(f"Rate limits → Cooldown: {COOLDOWN_SECONDS}s | Daily: {DAILY_LIMIT}")
    logger.info(f"Data directory: {DATA_DIR} (mount a Railway Volume here for persistence)")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("rules", rules))
    application.add_handler(CommandHandler("cancel", cancel))

    application.add_handler(
        MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_photo)
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.PHOTO,
            handle_other,
        )
    )
    application.add_handler(CallbackQueryHandler(handle_confirmation))

    logger.info("RiskIt Bot is running (polling). Send /start in private chat.")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
