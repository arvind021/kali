#!/usr/bin/env python3
# online_check_bot.py
# Requires: pip install pyrogram tgcrypto python-dotenv
# .env must contain BOT_TOKEN=<your_bot_token>

import os
import time
import sqlite3
import csv
from datetime import datetime
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message, User, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# ---- Load config ----
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Set BOT_TOKEN in .env")

app = Client("online_filter_bot", bot_token=BOT_TOKEN)

# ---- SQLite DB setup ----
DB = "members.db"
conn = sqlite3.connect(DB, check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS members (
    chat_id   INTEGER,
    user_id   INTEGER,
    username  TEXT,
    first_name TEXT,
    last_name TEXT,
    is_bot    INTEGER,
    is_deleted INTEGER,
    last_seen INTEGER,
    PRIMARY KEY(chat_id, user_id)
)
""")
conn.commit()

# ---- DB helper functions ----
def upsert_user(chat_id: int, user: User, seen_ts: int = None):
    """Insert or update user record when we see activity or join events."""
    if user is None:
        return
    uid = user.id
    username = user.username or ""
    first = user.first_name or ""
    last = user.last_name or ""
    is_bot = 1 if getattr(user, "is_bot", False) else 0
    is_deleted = 1 if getattr(user, "is_deleted", False) else 0

    if seen_ts is None:
        seen_ts = int(time.time())

    cur.execute("""
    INSERT INTO members(chat_id, user_id, username, first_name, last_name, is_bot, is_deleted, last_seen)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(chat_id, user_id) DO UPDATE SET
      username=excluded.username,
      first_name=excluded.first_name,
      last_name=excluded.last_name,
      is_bot=excluded.is_bot,
      is_deleted=excluded.is_deleted,
      last_seen=excluded.last_seen
    """, (chat_id, uid, username, first, last, is_bot, is_deleted, seen_ts))
    conn.commit()

def mark_left(chat_id: int, user: User):
    """Optional: mark last_seen when user left."""
    if user is None:
        return
    upsert_user(chat_id, user, int(time.time()))

def fetch_all_members(chat_id: int):
    cur.execute("SELECT user_id, username, first_name, last_name, is_bot, is_deleted, last_seen FROM members WHERE chat_id = ?", (chat_id,))
    rows = cur.fetchall()
    result = []
    for r in rows:
        result.append({
            "user_id": r[0],
            "username": r[1],
            "first_name": r[2],
            "last_name": r[3],
            "is_bot": bool(r[4]),
            "is_deleted": bool(r[5]),
            "last_seen": r[6]
        })
    return result

# ---- Utility: is admin ----
async def is_chat_admin(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
        status = getattr(member, "status", "")
        return status in ("creator", "administrator")
    except Exception:
        return False

# ---- Record activity handlers ----
@app.on_message(filters.group & ~filters.private)
async def message_logger(client: Client, message: Message):
    # Record sender activity
    u = message.from_user
    if u:
        try:
            upsert_user(message.chat.id, u, int(time.time()))
        except Exception:
            pass

@app.on_message(filters.new_chat_members)
async def new_member_handler(client: Client, message: Message):
    for u in message.new_chat_members:
        try:
            upsert_user(message.chat.id, u, int(time.time()))
        except Exception:
            pass

@app.on_message(filters.left_chat_member)
async def left_member_handler(client: Client, message: Message):
    u = message.left_chat_member
    if u:
        try:
            mark_left(message.chat.id, u)
        except Exception:
            pass

# ---- Report generator ----
def generate_report(threshold_minutes: int, members: list):
    now = int(time.time())
    threshold_sec = threshold_minutes * 60

    bots = [m for m in members if m["is_bot"]]
    deleted = [m for m in members if m["is_deleted"]]
    online_ish = [m for m in members if (m["last_seen"] is not None and (now - m["last_seen"]) <= threshold_sec and not m["is_bot"] and not m["is_deleted"])]
    offline_ish = [m for m in members if (m["last_seen"] is not None and (now - m["last_seen"]) > threshold_sec and not m["is_bot"] and not m["is_deleted"])]

    text = f"📊 <b>Check Report</b>\n"
    # human-friendly display for days/hours/minutes
    if threshold_minutes >= 24*60:
        days = threshold_minutes // (24*60)
        text += f"🕒 Threshold: <b>{days} day(s)</b>\n\n"
    elif threshold_minutes >= 60:
        hrs = threshold_minutes // 60
        text += f"🕒 Threshold: <b>{hrs} hour(s)</b>\n\n"
    else:
        text += f"🕒 Threshold: <b>{threshold_minutes} minutes</b>\n\n"

    text += f"👥 Total tracked members: {len(members)}\n"
    text += f"🟢 Online-ish: {len(online_ish)}\n"
    text += f"⚪ Offline-ish: {len(offline_ish)}\n"
    text += f"🤖 Bots: {len(bots)}\n"
    text += f"❌ Deleted Accounts: {len(deleted)}\n\n"

    def short(u):
        name = u['username'] or u['first_name'] or str(u['user_id'])
        return f"{name} (`{u['user_id']}`)"

    if online_ish:
        text += "🟢 <b>Online-ish</b> (examples):\n" + "\n".join(short(u) for u in online_ish[:30]) + "\n\n"

    if offline_ish:
        text += "⚪ <b>Offline-ish</b> (examples):\n" + "\n".join(short(u) for u in offline_ish[:30]) + "\n\n"

    if bots:
        text += "🤖 <b>Bots</b>:\n" + "\n".join(short(u) for u in bots[:30]) + "\n\n"

    if deleted:
        text += "❌ <b>Deleted Accounts</b>:\n" + "\n".join(short(u) for u in deleted[:30]) + "\n\n"

    return text

# ---- Buttons layout ----
BUTTONS = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🟢 30 Min", callback_data="th_30"),
        InlineKeyboardButton("🟢 60 Min", callback_data="th_60"),
    ],
    [
        InlineKeyboardButton("🟢 120 Min", callback_data="th_120"),
    ],
    [
        InlineKeyboardButton("🟡 1 Day", callback_data="th_1440"),
        InlineKeyboardButton("🟡 2 Day", callback_data="th_2880"),
    ]
])

# ---- /cheak command (admin-only) ----
@app.on_message(filters.command("cheak") & filters.group)
async def cmd_cheak(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    chat_id = message.chat.id
    user_id = user.id

    if not await is_chat_admin(client, chat_id, user_id):
        await message.reply_text("❌ सिर्फ़ ग्रुप के admins ही यह कमांड चला सकते हैं।", quote=True)
        return

    # If user passed a direct argument, run immediate report (e.g., /cheak 60 or /cheak 1d)
    parts = message.text.split()
    if len(parts) >= 2:
        arg = parts[1].lower()
        threshold_minutes = 30
        try:
            if arg.endswith("d"):
                days = int(arg[:-1])
                threshold_minutes = days * 24 * 60
            else:
                threshold_minutes = int(arg)
        except:
            threshold_minutes = 30

        members = fetch_all_members(chat_id)
        if not members:
            await message.reply_text("अभी तक किसी सदस्य की activity रिकॉर्ड नहीं हुई है।")
            return
        report = generate_report(threshold_minutes, members)
        await message.reply_text(report, disable_web_page_preview=True, reply_markup=BUTTONS)
        return

    # otherwise show UI buttons
    await message.reply_text(
        "🕹 <b>Select Time Filter:</b>\nChoose how you want to check Online/Offline members.",
        reply_markup=BUTTONS
    )

# ---- Callback handler for buttons (admin-only) ----
@app.on_callback_query(filters.regex(r"^th_"))
async def cb_threshold(client: Client, callback: CallbackQuery):
    user = callback.from_user
    if not user:
        await callback.answer("Invalid user.", show_alert=True)
        return

    chat_id = callback.message.chat.id
    user_id = user.id

    if not await is_chat_admin(client, chat_id, user_id):
        await callback.answer("❌ सिर्फ़ ग्रुप के admins ही रिपोर्ट देख सकते हैं.", show_alert=True)
        return

    # parse minutes from callback_data like "th_60"
    try:
        minutes = int(callback.data.split("_")[1])
    except:
        minutes = 30

    members = fetch_all_members(chat_id)
    if not members:
        try:
            await callback.message.edit_text("अभी तक किसी सदस्य की activity रिकॉर्ड नहीं हुई है।")
        except:
            pass
        await callback.answer()
        return

    report = generate_report(minutes, members)

    try:
        await callback.message.edit_text(report, disable_web_page_preview=True, reply_markup=BUTTONS)
    except Exception:
        # fallback to answer with alert if edit fails
        await callback.answer("Unable to show report here.", show_alert=True)
        return

    await callback.answer()

# ---- /export command (admin-only) ----
@app.on_message(filters.command("export") & filters.group)
async def cmd_export(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    chat_id = message.chat.id
    user_id = user.id
    if not await is_chat_admin(client, chat_id, user_id):
        await message.reply_text("❌ सिर्फ़ ग्रुप के admins ही export कर सकते हैं।", quote=True)
        return

    members = fetch_all_members(chat_id)
    if not members:
        await message.reply_text("No data to export.")
        return

    fname = f"members_{chat_id}_{int(time.time())}.csv"
    try:
        with open(fname, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["user_id","username","first_name","last_name","is_bot","is_deleted","last_seen_iso"])
            for m in members:
                ts = m["last_seen"]
                iso = datetime.utcfromtimestamp(ts).isoformat() if ts else ""
                w.writerow([m["user_id"], m["username"], m["first_name"], m["last_name"], int(m["is_bot"]), int(m["is_deleted"]), iso])
        await message.reply_document(fname, caption="Exported known members (based on activity).")
    except Exception as e:
        await message.reply_text(f"Export failed: {e}")
    finally:
        try:
            if os.path.exists(fname):
                os.remove(fname)
        except:
            pass

# ---- Graceful shutdown / run ----
if __name__ == "__main__":
    print("Starting online check bot...")
    app.run()
