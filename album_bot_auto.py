"""
Pyrogram Album Maker Bot

Features:
- Accepts photos, videos, animations, and documents (image/video mime types).
- Auto-sends albums when threshold is reached or after inactivity.
- Handles Telegram limits (max 10 items per album).
- Prevents data loss for queues > 10 items.
- intelligently switches to single file send if queue has only 1 item.
"""
from collections import defaultdict
import os
import asyncio

from pyrogram import Client, filters
from config import *
from pyrogram.types import (
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

# App
app = Client("album_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# In-memory store: user_id -> list of items
# Each item: {"type": "photo"|"video", "file_id": str, "caption": Optional[str]}
pending = defaultdict(list)
# Timers: user_id -> asyncio.Task
timers = {}
# Send locks so we don't send multiple albums concurrently for same user
send_locks = defaultdict(asyncio.Lock)


def _make_input_media(item, with_caption=False):
    typ = item.get("type")
    file_id = item.get("file_id")
    caption = item.get("caption") if with_caption else None

    if typ == "photo":
        return InputMediaPhoto(media=file_id, caption=caption)
    elif typ == "video":
        return InputMediaVideo(media=file_id, caption=caption)
    else:
        raise ValueError("Unsupported media type")


def _cancel_timer(user_id: int):
    """Cancels the existing timer for a user if it exists."""
    task = timers.pop(user_id, None)
    if task and not task.done():
        task.cancel()


def _start_timer(client: Client, user_id: int, chat_id: int):
    """Start (or restart) inactivity timer for auto-send."""
    _cancel_timer(user_id)

    async def _wait_and_send():
        try:
            await asyncio.sleep(AUTO_SEND_DELAY)
            # Only send if items exist
            if pending.get(user_id):
                await send_album_for_user(client, user_id, chat_id)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception(f"Error in auto-send timer for user {user_id}")

    task = asyncio.create_task(_wait_and_send())
    timers[user_id] = task


async def send_album_for_user(client: Client, user_id: int, chat_id: int):
    """
    Sends pending items. 
    - Takes first 10 items.
    - If 1 item: sends as single message.
    - If >1 items: sends as album.
    - If >10 items originally: keeps remainder in queue and restarts timer.
    """
    async with send_locks[user_id]:
        items = pending.get(user_id, [])
        if not items:
            return

        # 1. Slice the batch (Telegram limit is 10)
        to_send = items[:10]
        remaining = items[10:]

        try:
            # SCENARIO A: Single Item (Cannot use send_media_group)
            if len(to_send) == 1:
                item = to_send[0]
                typ = item["type"]
                file_id = item["file_id"]
                caption = item["caption"]
                
                if typ == "photo":
                    await client.send_photo(chat_id, file_id, caption=caption)
                elif typ == "video":
                    await client.send_video(chat_id, file_id, caption=caption)
                logger.info(f"Sent single item for user {user_id}")

            # SCENARIO B: Album (2-10 items)
            else:
                media = []
                for i, item in enumerate(to_send):
                    # Attach caption only to the first item (or customize logic here)
                    media.append(_make_input_media(item, with_caption=(i == 0)))

                await client.send_media_group(chat_id=chat_id, media=media)
                logger.info(f"Sent album for user {user_id} with {len(media)} items")

        except Exception as e:
            logger.exception(f"Failed to send media for user {user_id}")
            try:
                await client.send_message(chat_id, f"Failed to send media: `{e}`")
            except Exception:
                pass

        # 2. Handle State after sending
        if remaining:
            # Update pending with what's left
            pending[user_id] = remaining
            # Restart timer to allow user to add more to the next batch, 
            # or auto-send the remainder after the delay.
            _start_timer(client, user_id, chat_id)
            logger.info(f"User {user_id} has {len(remaining)} items remaining. Timer restarted.")
        else:
            # Queue is empty
            pending.pop(user_id, None)
            _cancel_timer(user_id)


@app.on_message(filters.private & (filters.photo | filters.video | filters.animation | filters.document))
async def collect_media(client: Client, message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    # 1. Detect Media Type
    if message.photo:
        file_id = message.photo.file_id
        typ = "photo"
    elif message.video:
        file_id = message.video.file_id
        typ = "video"
    elif message.animation:
        file_id = message.animation.file_id
        typ = "video"
    elif message.document:
        mime = message.document.mime_type or ""
        file_id = message.document.file_id
        if mime.startswith("image/"):
            typ = "photo"
        elif mime.startswith("video/"):
            typ = "video"
        else:
            await message.reply_text("Unsupported document type. Please send images or videos.")
            return
    else:
        return

    caption = message.caption or None

    # 2. Add to Queue
    pending[user_id].append({"type": typ, "file_id": file_id, "caption": caption})
    total = len(pending[user_id])

    # 3. Check Threshold or Start Timer
    if total >= AUTO_SEND_THRESHOLD:
        _cancel_timer(user_id)
        await send_album_for_user(client, user_id, chat_id)
    else:
        _start_timer(client, user_id, chat_id)
        
        # UX Fix: Only reply on the FIRST item to avoid spamming.
        if total == 1:
            await message.reply_text(
                f"**Album started!**\n"
                f"Send more photos/videos to group them.\n"
                f"Auto-sending after {AUTO_SEND_DELAY}s of silence.",
                quote=True
            )


@app.on_message(filters.private & filters.command("send_album"))
async def send_album_command(client: Client, message: Message):
    user_id = message.from_user.id
    # Cancel timer so we don't double-send
    _cancel_timer(user_id)
    await send_album_for_user(client, user_id, message.chat.id)


@app.on_message(filters.private & filters.command("cancel"))
async def cancel_queue(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in pending:
        pending.pop(user_id, None)
        _cancel_timer(user_id)
        await message.reply_text("❌ Album queue cleared.")
    else:
        await message.reply_text("Queue is already empty.")


@app.on_message(filters.private & filters.command("status"))
async def status(client: Client, message: Message):
    user_id = message.from_user.id
    total = len(pending.get(user_id, []))
    await message.reply_text(f"📁 Current queue: **{total}** items.")


@app.on_message(filters.private & filters.command("start"))
async def start(client: Client, message: Message):
    # First send the photo
    await message.reply_photo(
        photo="https://i.ibb.co/QjdgRJG4/Neo-Matrix90.jpg",  # Replace with your image URL
        caption="👋 **Hi!**\n\n"
                "Send me photos or videos. I will group them into an album automatically.\n\n"
                f"• **Threshold:** `{AUTO_SEND_THRESHOLD} items`\n"
                f"• **Delay:** `{AUTO_SEND_DELAY} seconds`\n\n"
                "Commands:\n"
                "/send_album - Force send now\n"
                "/cancel - Clear queue\n"
                "/status - Check queue"
    )


if __name__ == "__main__":
    if BOT_TOKEN == "YOUR-BOT-TOKEN-HERE" or not BOT_TOKEN:
        logger.error("Please set BOT_TOKEN environment variable.")
        raise SystemExit(1)

    print("Bot is running...")
    app.run()