"""
RiskIt Bot – Basic Anonymous Photo Sharing

Users send one or more photos privately → get a batch confirmation → the bot posts
all of them anonymously to the target group/channel (no username visible).

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
#   GROUP_CHAT_ID        - target supergroup or channel ID (starts with -100...)
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
# user_id -> {
#     "photos": [{"file_id": str, "caption": str|None}, ...],
#     "chat_id": int,
#     "confirm_msg_id": int | None
# }
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


def can_post_photos(user_id: int, count: int = 1) -> tuple[bool, str | None]:
    """Check whether the user can post 'count' photos right now.
    Returns (is_limited, message_if_limited)
    """
    if count < 1:
        return False, None

    data = load_rate_limits()
    uid = str(user_id)
    now = time.time()

    # Cooldown check (applies when starting or adding to a batch)
    last = data.get("last_posts", {}).get(uid)
    if last is not None and (now - last) < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        return True, f"⏳ Rate limit: please wait {remaining} seconds before posting more."

    # Daily limit check (account for the whole batch)
    today = get_today_str()
    current = data.get("daily", {}).get(today, {}).get(uid, 0)
    if current + count > DAILY_LIMIT:
        can_still = DAILY_LIMIT - current
        if can_still <= 0:
            return True, f"📅 Daily limit reached ({DAILY_LIMIT} posts per day). Come back tomorrow!"
        return True, f"📅 Daily limit: you can only post {can_still} more photo(s) today."

    return False, None


def is_rate_limited(user_id: int):
    """Legacy single-photo check. Returns (is_limited: bool, message: str|None)"""
    return can_post_photos(user_id, 1)


def record_batch(user_id: int, count: int = 1) -> None:
    """Record that 'count' photos were posted by the user."""
    if count < 1:
        return
    data = load_rate_limits()
    uid = str(user_id)
    now = time.time()

    data.setdefault("last_posts", {})[uid] = now

    today = get_today_str()
    daily = data.setdefault("daily", {}).setdefault(today, {})
    daily[uid] = daily.get(uid, 0) + count

    # Prune daily entries older than 7 days
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
    data["daily"] = {d: v for d, v in data.get("daily", {}).items() if d >= cutoff}

    save_rate_limits(data)


def record_post(user_id: int) -> None:
    """Record a single photo post (kept for compatibility)."""
    record_batch(user_id, 1)


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
    "📸 Send one or more photos (each can have its own short caption).\n\n"
    "You will get a confirmation showing the total count. Tap to post the whole batch anonymously."
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
    batch = pending_confirmations.pop(user_id, None)
    if batch:
        count = len(batch.get("photos", []))
        msg = f"❌ Batch of {count} photo{'s' if count != 1 else ''} cleared."
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("No pending photos to cancel.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive photo(s) in private chat. Collect into a batch and show/update confirmation."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Please send photos to me in a private chat only.")
        return

    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    file_id = photo.file_id
    user_caption = update.message.caption

    # Get or create the user's batch
    if user_id not in pending_confirmations:
        pending_confirmations[user_id] = {
            "photos": [],
            "chat_id": update.effective_chat.id,
            "confirm_msg_id": None,
        }

    batch = pending_confirmations[user_id]
    photos_list: list = batch["photos"]
    photos_list.append({"file_id": file_id, "caption": user_caption})
    current_count = len(photos_list)

    # Soft daily limit check before allowing the addition (prevents huge batches when over limit)
    limited, limit_msg = can_post_photos(user_id, current_count)
    if limited and current_count == 1:
        # Only block starting a new batch if already over limit
        photos_list.pop()  # remove the one we just added
        if not photos_list:
            pending_confirmations.pop(user_id, None)
        await update.message.reply_text(limit_msg)
        return
    # For additional photos, we allow the append but will strictly check on "Post All"

    # Dynamic button with current count
    keyboard = [
        [
            InlineKeyboardButton(f"✅ Post All ({current_count})", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ Cancel All", callback_data="confirm_no"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    summary_text = (
        f"📸 <b>{current_count} photo{'s' if current_count != 1 else ''} in your batch.</b>\n\n"
        "Do you want to post <b>all of them anonymously</b> in the group?\n\n"
        "• Keep sending more photos to add them to this batch\n"
        "• Posts will appear from the bot (your username stays hidden)\n"
        "• Reactions will be enabled on each photo"
    )

    chat_id = update.effective_chat.id

    try:
        if current_count == 1:
            # First photo in batch: use the photo itself as a nice visual preview + buttons
            sent = await update.message.reply_photo(
                photo=file_id,
                caption=summary_text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            batch["confirm_msg_id"] = sent.message_id
        else:
            # Additional photos: edit the existing confirmation (photo or text message)
            confirm_msg_id = batch.get("confirm_msg_id")
            if confirm_msg_id:
                try:
                    await context.bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=confirm_msg_id,
                        caption=summary_text,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                    )
                except Exception:
                    # Message too old or can't be edited — send fresh text summary
                    sent = await update.message.reply_text(
                        summary_text, parse_mode="HTML", reply_markup=reply_markup
                    )
                    batch["confirm_msg_id"] = sent.message_id
            else:
                sent = await update.message.reply_text(
                    summary_text, parse_mode="HTML", reply_markup=reply_markup
                )
                batch["confirm_msg_id"] = sent.message_id

        # Short non-intrusive ack (quoted reply to the user's photo)
        await update.message.reply_text(f"✅ Added • Total: {current_count}", quote=True)

    except Exception as e:
        logger.error(f"Failed to update batch confirmation for user {user_id}: {e}")
        # Don't clear the batch on transient error — user can try sending another or /cancel
        await update.message.reply_text(
            "⚠️ Had trouble updating the confirmation. You can still send more photos or use /cancel."
        )


async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Yes / Cancel button presses for single or batch confirmations."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    batch = pending_confirmations.get(user_id)
    if not batch:
        await query.edit_message_caption(
            "❌ This confirmation expired or was already handled.",
            reply_markup=None,
        )
        return

    photos: list = batch.get("photos", [])
    count = len(photos)
    chat_id = batch.get("chat_id", user_id)

    if query.data == "confirm_no":
        pending_confirmations.pop(user_id, None)
        try:
            await query.edit_message_caption(
                f"❌ Cancelled. {count} photo{'s' if count != 1 else ''} were not posted.",
                reply_markup=None,
            )
        except Exception:
            pass
        return

    if query.data == "confirm_yes":
        if not photos:
            pending_confirmations.pop(user_id, None)
            await query.edit_message_caption("❌ Nothing to post.", reply_markup=None)
            return

        # Strict check with the full batch size
        limited, limit_msg = can_post_photos(user_id, count)
        if limited:
            # We keep the batch so user can cancel or remove some (future) or try later
            await query.edit_message_caption(limit_msg, reply_markup=None)
            return

        # Post every photo in the batch (individually so each gets reactions)
        posted = 0
        errors = 0
        try:
            for p in photos:
                try:
                    await context.bot.send_photo(
                        chat_id=GROUP_CHAT_ID,
                        photo=p["file_id"],
                        caption=p.get("caption"),
                    )
                    posted += 1
                except Exception as photo_err:
                    errors += 1
                    logger.error(f"Failed to post one photo from batch for user {user_id}: {photo_err}")

            record_batch(user_id, posted)
            pending_confirmations.pop(user_id, None)

            if errors > 0:
                success = f"✅ Posted {posted} photo(s) anonymously.\n⚠️ {errors} failed to post."
            else:
                success = f"✅ Posted {posted} photo{'s' if posted != 1 else ''} anonymously to the group!\n\nThank you."

            try:
                await query.edit_message_caption(success, reply_markup=None)
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text=success)

        except Exception as e:
            logger.error(f"Failed during batch post for user {user_id}: {e}")
            pending_confirmations.pop(user_id, None)
            error_text = (
                "❌ Failed to post the batch.\n"
                "Make sure the bot is an admin in the target chat with 'Post Messages' permission."
            )
            try:
                await query.edit_message_caption(error_text, reply_markup=None)
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text=error_text)


async def handle_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all for non-photo, non-command messages in private chat."""
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(
        "📸 Send photos to add them to your anonymous batch.\n"
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
    logger.info(f"Target Chat ID (group or channel): {GROUP_CHAT_ID}")
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
