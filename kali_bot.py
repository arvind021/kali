#!/usr/bin/env python3
# kali_bot.py
# Requires: pip install pyrogram tgcrypto python-dotenv
# .env must contain API_ID, API_HASH, BOT_TOKEN

import os
import sys
import time
import sqlite3
import csv
import logging
import traceback
import platform
from datetime import datetime
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message, User, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# ---- Load config ----
load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Validation
if not API_ID or not API_HASH or not BOT_TOKEN:
    raise SystemExit("❌ Missing API_ID, API_HASH, or BOT_TOKEN in .env")

try:
    API_ID = int(API_ID)
except:
    raise SystemExit("❌ API_ID must be an integer (example: API_ID=1234567)")

# ---- Correct Pyrogram v2 Client ----
app = Client(
    "online_filter_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ------------------- DEBUG LOGGING SETUP -------------------
LOGFILE = "bot_events.log"
EXCLOG = "uncaught_exceptions.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOGFILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("kali_bot")

def excepthook(exc_type, exc, tb):
    tb_str = "".join(traceback.format_exception(exc_type, exc, tb))
    logger.error("UNCAUGHT EXCEPTION:\n" + tb_str)
    try:
        with open(EXCLOG, "a", encoding="utf-8") as ef:
            ef.write(f"\n\n==== {datetime.utcnow().isoformat()} UTC ====\n")
            ef.write(tb_str)
    except:
        pass

sys.excepthook = excepthook

START_TIME = time.time()

def uptime_text():
    s = int(time.time() - START_TIME)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"

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

    try:
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
    except Exception:
        logger.exception("DB upsert failed for user %s in chat %s", uid, chat_id)

def mark_left(chat_id: int, user: User):
    if user:
        upsert_user(chat_id, user, int(time.time()))

def fetch_all_members(chat_id: int):
    try:
        cur.execute("SELECT user_id, username, first_name, last_name, is_bot, is_deleted, last_seen FROM members WHERE chat_id = ?", (chat_id,))
        rows = cur.fetchall()
    except Exception:
        logger.exception("DB fetch failed for chat %s", chat_id)
        rows = []
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

# ---- Admin check function ----
async def is_chat_admin(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        logger.exception("is_chat_admin failed for %s in chat %s", user_id, chat_id)
        return False

# ---- Activity Tracking ----
@app.on_message(filters.group)
async def message_logger(client, message: Message):
    try:
        if message.from_user:
            upsert_user(message.chat.id, message.from_user, int(time.time()))
    except Exception:
        logger.exception("message_logger failed")

@app.on_message(filters.new_chat_members)
async def new_member_handler(client, message: Message):
    try:
        for u in message.new_chat_members:
            upsert_user(message.chat.id, u, int(time.time()))
            logger.info("New member in %s: %s (@%s)", message.chat.id, u.id, getattr(u, "username", None))
            # If bot itself added, send a welcome
            me = await client.get_me()
            if u.id == me.id:
                try:
                    await message.reply_text("Hello! I am online — admins can use /cheak and /export.")
                except:
                    pass
    except Exception:
        logger.exception("new_member_handler failed")

@app.on_message(filters.left_chat_member)
async def left_member_handler(client, message: Message):
    try:
        if message.left_chat_member:
            mark_left(message.chat.id, message.left_chat_member)
            u = message.left_chat_member
            logger.info("Left member from %s: %s (@%s)", message.chat.id, u.id, getattr(u, "username", None))
    except Exception:
        logger.exception("left_member_handler failed")

# ---- Simple debug handlers: ping + log commands ----
@app.on_message(filters.command("ping") & (filters.group | filters.private))
async def cmd_ping(client, message: Message):
    try:
        await message.reply_text(f"PONG — uptime {uptime_text()}")
        logger.info("PING from %s in chat %s", getattr(message.from_user, "id", None), message.chat.id)
    except Exception:
        logger.exception("ping handler failed")

@app.on_message(filters.group & filters.regex(r"^/"))
async def log_group_commands(client, message: Message):
    try:
        cmd = (message.text or "").split()[0]
        logger.info("CMD in %s from %s: %s", message.chat.id, getattr(message.from_user, "id", None), cmd)
    except Exception:
        logger.exception("log_group_commands failed")

# ---- Report generator ----
def generate_report(threshold_minutes: int, members: list):
    now = int(time.time())
    threshold_sec = threshold_minutes * 60

    bots = [m for m in members if m["is_bot"]]
    deleted = [m for m in members if m["is_deleted"]]
    online_ish = [m for m in members if (m["last_seen"] is not None and (now - m["last_seen"]) <= threshold_sec and not m["is_bot"] and not m["is_deleted"])]
    offline_ish = [m for m in members if (m["last_seen"] is not None and (now - m["last_seen"]) > threshold_sec and not m["is_bot"] and not m["is_deleted"])]

    text = "📊 <b>Check Report</b>\n"

    # Display threshold nicely
    if threshold_minutes >= 1440:
        text += f"🕒 Threshold: <b>{threshold_minutes // 1440} Day(s)</b>\n\n"
    elif threshold_minutes >= 60:
        text += f"🕒 Threshold: <b>{threshold_minutes // 60} Hour(s)</b>\n\n"
    else:
        text += f"🕒 Threshold: <b>{threshold_minutes} Minutes</b>\n\n"

    text += f"👥 Total tracked: {len(members)}\n"
    text += f"🟢 Online-ish: {len(online_ish)}\n"
    text += f"⚪ Offline-ish: {len(offline_ish)}\n"
    text += f"🤖 Bots: {len(bots)}\n"
    text += f"❌ Deleted: {len(deleted)}\n\n"

    def short(u):
        return f"{u['username'] or u['first_name'] or u['user_id']} (`{u['user_id']}`)"

    if online_ish:
        text += "🟢 <b>Online-ish:</b>\n" + "\n".join(short(u) for u in online_ish[:30]) + "\n\n"
    if offline_ish:
        text += "⚪ <b>Offline-ish:</b>\n" + "\n".join(short(u) for u in offline_ish[:30]) + "\n\n"
    if bots:
        text += "🤖 <b>Bots:</b>\n" + "\n".join(short(u) for u in bots[:30]) + "\n\n"
    if deleted:
        text += "❌ <b>Deleted:</b>\n" + "\n".join(short(u) for u in deleted[:30]) + "\n\n"

    return text

# ---- Buttons ----
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

# ---- /cheak ----
@app.on_message(filters.command("cheak") & filters.group)
async def cmd_cheak(client, message: Message):
    try:
        logger.info("/cheak triggered by %s in chat %s", getattr(message.from_user, "id", None), message.chat.id)
    except:
        logger.exception("failed to log /cheak trigger")

    user = message.from_user
    if not user:
        return
    user_id = user.id
    chat_id = message.chat.id

    if not await is_chat_admin(client, chat_id, user_id):
        return await message.reply_text("❌ Only admins can use this command.")

    # Direct argument: /cheak 60 or /cheak 1d etc
    parts = message.text.split()
    if len(parts) >= 2:
        arg = parts[1].lower()
        try:
            if arg.endswith("d"):
                threshold_minutes = int(arg[:-1]) * 1440
            else:
                threshold_minutes = int(arg)
        except:
            threshold_minutes = 30

        members = fetch_all_members(chat_id)
        if not members:
            return await message.reply_text("No member data yet.")

        report = generate_report(threshold_minutes, members)
        return await message.reply_text(report, reply_markup=BUTTONS)

    # Otherwise show UI buttons
    await message.reply_text(
        "🕹 <b>Select Time Filter</b>",
        reply_markup=BUTTONS
    )

# ---- Callback buttons ----
@app.on_callback_query(filters.regex("^th_"))
async def cb_threshold(client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id

    if not await is_chat_admin(client, chat_id, user_id):
        return await callback.answer("❌ Only admins allowed.", show_alert=True)

    try:
        minutes = int(callback.data.split("_")[1])
    except:
        minutes = 30

    members = fetch_all_members(chat_id)

    if not members:
        return await callback.message.edit_text("No member activity recorded.")

    report = generate_report(minutes, members)

    await callback.message.edit_text(
        report,
        reply_markup=BUTTONS
    )

    await callback.answer()

# ---- /export ----
@app.on_message(filters.command("export") & filters.group)
async def cmd_export(client, message: Message):
    user = message.from_user
    if not user:
        return
    user_id = user.id
    chat_id = message.chat.id

    if not await is_chat_admin(client, chat_id, user_id):
        return await message.reply_text("❌ Only admins can export data.")

    members = fetch_all_members(chat_id)
    if not members:
        return await message.reply_text("No data available to export.")

    fname = f"members_{chat_id}_{int(time.time())}.csv"

    try:
        with open(fname, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["user_id", "username", "first_name", "last_name", "is_bot", "is_deleted", "last_seen_iso"])

            for m in members:
                iso = datetime.utcfromtimestamp(m["last_seen"]).isoformat() if m["last_seen"] else ""
                w.writerow([m["user_id"], m["username"], m["first_name"], m["last_name"], int(m["is_bot"]), int(m["is_deleted"]), iso])

        await message.reply_document(fname, caption="Exported list.")
    except Exception:
        logger.exception("export failed")
        await message.reply_text("Export failed.")
    finally:
        try:
            if os.path.exists(fname):
                os.remove(fname)
        except:
            pass

# ---- Run bot ----
if __name__ == "__main__":
    logger.info("Starting online check bot... Uptime reset")
    print("Starting online check bot...")
    app.run()
