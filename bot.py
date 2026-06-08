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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Always-visible persistent menu buttons."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📸 Leak Photo"), KeyboardButton("📧 Leak Email")],
            [KeyboardButton("📱 Leak Phone"), KeyboardButton("📷 Leak Instagram")],
            [KeyboardButton("📜 Rules"), KeyboardButton("❌ Cancel")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


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

RULES_TEXT = """⚠️ <b>RISK IT — Anonymous Leak Bot</b>

<b>What this bot is:</b>
RiskIt lets you anonymously leak photos + personal info (email, phone, Instagram) to the target group/channel.

<b>Where leaks go:</b>
After you confirm, the bot posts them from its own account into the configured group/channel. 
No one sees your username or that it came from you.

<b>Basic Rules:</b>
• Only leak <b>your own</b> real info and photos (no stolen / fake / revenge content)
• No underage, illegal, or non-consensual material
• No spam, extreme gore, or hate
• Once posted, it's visible to everyone in the target chat forever (or until deleted by admins)
• Be respectful — moderators can remove content

This is the basic version. Use the menu buttons below.

Choose an option from the keyboard to start leaking."""

POST_INSTRUCTIONS = (
    "Use the buttons at the bottom to start a leak.\n\n"
    "📸 Leak Photo → send one or more photos (batch supported)\n"
    "📧 Leak Email / 📱 Leak Phone / 📷 Leak Instagram → send the info as text\n\n"
    "You will always get a clear confirmation before anything is posted anonymously to the group."
)


# ==================== HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return

    welcome = (
        "🔥 <b>Welcome to RiskIt Bot</b> — the anonymous leak bot.\n\n"
        "This is a private tool for <b>anonymous leaks</b>.\n"
        "• Photos (with optional captions)\n"
        "• Emails\n"
        "• Phone numbers\n"
        "• Instagram accounts\n\n"
        "<b>Where do the leaks go?</b>\n"
        "Everything you confirm is posted <b>anonymously</b> from the bot account "
        "(no username, no trace back to you) into the target group/channel.\n\n"
        "Use the buttons below to choose what you want to leak. "
        "You will always get a confirmation before anything is posted."
    )
    await update.message.reply_text(welcome, parse_mode="HTML", reply_markup=get_main_keyboard())
    await update.message.reply_text(POST_INSTRUCTIONS, reply_markup=get_main_keyboard())


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(RULES_TEXT, parse_mode="HTML", reply_markup=get_main_keyboard())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    batch = pending_confirmations.pop(user_id, None)
    if batch:
        count = len(batch.get("photos", [])) or (1 if batch.get("value") else 0)
        msg = f"❌ Pending leak cleared ({count} item{'s' if count != 1 else ''})."
    else:
        msg = "No pending leak to cancel."
    await update.message.reply_text(msg, reply_markup=get_main_keyboard())


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive photo(s) in private chat. Collect into a batch and show/update confirmation."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Please send photos to me in a private chat only.")
        return

    user_id = update.effective_user.id

    # Prevent mixing text leaks with photos
    if user_id in pending_confirmations:
        existing = pending_confirmations[user_id]
        if existing.get("type") != "photo":
            await update.message.reply_text(
                "You have a pending text leak (email/phone/IG). Finish it or tap ❌ Cancel first, then send photos.",
                reply_markup=get_main_keyboard()
            )
            return
        # else it's an existing photo batch — allow adding more

    photo = update.message.photo[-1]
    file_id = photo.file_id
    user_caption = update.message.caption

    # Get or create the user's batch
    if user_id not in pending_confirmations:
        pending_confirmations[user_id] = {
            "type": "photo",
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
        "Do you want to post <b>all of them anonymously</b>?\n\n"
        "⚠️ They will be posted from the bot into the target group/channel (no trace to you).\n\n"
        "• Keep sending more photos to add them\n"
        "• Each photo gets its own reactions"
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
        await update.message.reply_text(
            f"✅ Added • Total: {current_count}",
            quote=True,
            reply_markup=get_main_keyboard(),
        )

    except Exception as e:
        logger.error(f"Failed to update batch confirmation for user {user_id}: {e}")
        # Don't clear the batch on transient error — user can try sending another or /cancel
        await update.message.reply_text(
            "⚠️ Had trouble updating the confirmation. You can still send more photos or use /cancel."
        )


async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Yes / Cancel button presses for photo batches or text leaks (email/phone/IG)."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    batch = pending_confirmations.get(user_id)
    if not batch:
        try:
            await query.edit_message_text(
                "❌ This confirmation expired or was already handled.",
                reply_markup=None,
            )
        except Exception:
            pass
        return

    leak_type = batch.get("type", "photo")
    chat_id = batch.get("chat_id", user_id)

    if query.data == "confirm_no":
        pending_confirmations.pop(user_id, None)
        try:
            if leak_type == "photo":
                count = len(batch.get("photos", []))
                await query.edit_message_caption(
                    f"❌ Cancelled. {count} photo{'s' if count != 1 else ''} were not posted.",
                    reply_markup=None,
                )
            else:
                await query.edit_message_text(
                    f"❌ Cancelled. The {leak_type} was not leaked.",
                    reply_markup=None,
                )
        except Exception:
            pass
        return

    if query.data == "confirm_yes":
        if leak_type == "photo":
            photos: list = batch.get("photos", [])
            count = len(photos)
            if count == 0:
                pending_confirmations.pop(user_id, None)
                await query.edit_message_caption("❌ Nothing to post.", reply_markup=None)
                return

            # Strict rate limit check for the full batch
            limited, limit_msg = can_post_photos(user_id, count)
            if limited:
                await query.edit_message_caption(limit_msg, reply_markup=None)
                return

            # Post photos individually
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
                    success = f"✅ Posted {posted} photo{'s' if posted != 1 else ''} anonymously!\n\nThank you. Use the buttons below for more leaks."

                try:
                    await query.edit_message_caption(success, reply_markup=None)
                except Exception:
                    await context.bot.send_message(
                        chat_id=chat_id, text=success, reply_markup=get_main_keyboard()
                    )

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
                    await context.bot.send_message(
                        chat_id=chat_id, text=error_text, reply_markup=get_main_keyboard()
                    )
            return

        # --- Text leaks (email / phone / instagram) ---
        value = batch.get("value")
        if not value:
            pending_confirmations.pop(user_id, None)
            await query.edit_message_text("❌ No value to leak.", reply_markup=None)
            return

        # Rate limit check (single item)
        limited, limit_msg = can_post_photos(user_id, 1)
        if limited:
            await query.edit_message_text(limit_msg, reply_markup=None)
            return

        emoji = {"email": "📧", "phone": "📱", "instagram": "📷"}[leak_type]
        leak_text = f"{emoji} Leaked {leak_type.capitalize()}:\n{value}"

        try:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=leak_text)

            record_batch(user_id, 1)
            pending_confirmations.pop(user_id, None)

            success = "✅ Leaked anonymously!\n\nThank you."
            try:
                await query.edit_message_text(success, reply_markup=None)
            except Exception:
                await context.bot.send_message(
                    chat_id=chat_id, text=success, reply_markup=get_main_keyboard()
                )

        except Exception as e:
            logger.error(f"Failed to post {leak_type} leak for user {user_id}: {e}")
            pending_confirmations.pop(user_id, None)
            error_text = (
                "❌ Failed to post the leak.\n"
                "Make sure the bot is an admin in the target chat with 'Post Messages' permission."
            )
            try:
                await query.edit_message_text(error_text, reply_markup=None)
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text=error_text)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle menu button presses (always visible ReplyKeyboard) and text input for email/phone/IG leaks."""
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    # --- Menu button actions (persistent keyboard) ---
    if text == "📸 Leak Photo":
        await update.message.reply_text(
            "📸 <b>Photo leak mode</b>\n\n"
            "Send one or more photos (you can send them individually or as an album).\n"
            "I'll show a live count and ask for confirmation before posting them anonymously to the group.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(),
        )
        return

    if text in ("📧 Leak Email", "📱 Leak Phone", "📷 Leak Instagram"):
        if user_id in pending_confirmations:
            existing_type = pending_confirmations[user_id].get("type")
            if existing_type == "photo":
                await update.message.reply_text(
                    "You have photos waiting for confirmation. Send more photos or tap ❌ Cancel first.",
                    reply_markup=get_main_keyboard(),
                )
            else:
                await update.message.reply_text(
                    "You already have a pending leak. Finish it (or tap ❌ Cancel) before starting another.",
                    reply_markup=get_main_keyboard(),
                )
            return

        leak_type = {
            "📧 Leak Email": "email",
            "📱 Leak Phone": "phone",
            "📷 Leak Instagram": "instagram",
        }[text]

        pending_confirmations[user_id] = {
            "type": leak_type,
            "value": None,
            "chat_id": update.effective_chat.id,
            "confirm_msg_id": None,
        }

        prompts = {
            "email": "Send the email address you want to leak (e.g. name@example.com):",
            "phone": "Send the phone number you want to leak (with country code if possible):",
            "instagram": "Send the Instagram username or profile link (e.g. @username or instagram.com/username):",
        }
        await update.message.reply_text(
            f"{prompts[leak_type]}\n\nYou will get a confirmation before it is posted anonymously.",
            reply_markup=get_main_keyboard(),
        )
        return

    if text == "📜 Rules":
        await update.message.reply_text(RULES_TEXT, parse_mode="HTML", reply_markup=get_main_keyboard())
        return

    if text == "❌ Cancel":
        # Reuse cancel logic
        batch = pending_confirmations.pop(user_id, None)
        if batch:
            count = len(batch.get("photos", [])) or (1 if batch.get("value") else 0)
            msg = f"❌ Pending leak cleared ({count} item{'s' if count != 1 else ''})."
        else:
            msg = "No pending leak to cancel."
        await update.message.reply_text(msg, reply_markup=get_main_keyboard())
        return

    # --- Text input for pending email/phone/IG leak ---
    if user_id in pending_confirmations:
        pending = pending_confirmations[user_id]
        if pending.get("type") in ("email", "phone", "instagram") and pending.get("value") is None:
            value = text
            if not value or len(value) < 2:
                await update.message.reply_text("Please send a valid value (not empty).")
                return

            pending["value"] = value
            leak_type = pending["type"]
            emoji = {"email": "📧", "phone": "📱", "instagram": "📷"}[leak_type]

            confirm_text = (
                f"{emoji} <b>Confirm leaking this {leak_type}</b> anonymously?\n\n"
                f"<code>{value}</code>\n\n"
                "⚠️ Once confirmed, this will be posted from the bot into the target group/channel. "
                "No one will know it came from you.\n\n"
                "Are you sure you want to leak it?"
            )

            kb = [
                [
                    InlineKeyboardButton("✅ Yes, Leak it", callback_data="confirm_yes"),
                    InlineKeyboardButton("❌ Cancel", callback_data="confirm_no"),
                ]
            ]
            sent = await update.message.reply_text(
                confirm_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
            )
            pending["confirm_msg_id"] = sent.message_id
            return

        # If there's a photo batch pending, remind user
        if pending.get("type") == "photo":
            await update.message.reply_text(
                "You have a photo batch pending. Send more photos or use the buttons on the confirmation message (or ❌ Cancel).",
                reply_markup=get_main_keyboard(),
            )
            return

    # Default / unknown text
    await update.message.reply_text(
        "Use the menu buttons at the bottom to leak something, or send a photo directly.\n"
        "Tap 📜 Rules for more info.",
        reply_markup=get_main_keyboard(),
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
            handle_text,
        )
    )
    application.add_handler(CallbackQueryHandler(handle_confirmation))

    logger.info("RiskIt Bot is running (polling). Use the menu buttons or /start.")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
