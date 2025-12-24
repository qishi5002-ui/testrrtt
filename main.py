# main.py — AutoPanel (Master Shop + Optional White‑Label Seller Bots) FULL
# Python 3.11+ | python-telegram-bot==20.8 | Railway-ready (polling)
#
# =========================
# ✅ WHAT THIS BOT INCLUDES
# =========================
# MASTER SHOP (your bot token):
# - User buttons: Products / Wallet / History / Support / Connect My Bot
# - Super Admin only: Admin Panel + Super Admin button
#
# SELLER WHITE‑LABEL (Option 2):
# - Seller connects their own BotFather token via "Connect My Bot" in Master Shop
# - Seller bot runs on same server (started/stopped automatically)
# - Seller bot users ONLY see seller shop (never see master shop)
# - Seller owner sees Admin Panel + Extend Subscription in seller bot
#
# PLANS (deducted from MASTER SHOP wallet balance):
# - Plan A: $5 / 30 days => Branded (WELCOME ONLY appends "Bot made by @RekkoOwn")
# - Plan B: $10 / 30 days => White‑Label (WELCOME has no branding)
# Rules:
# - White‑Label cannot pay $5 (blocked; must renew with $10)
# - Branded paying $5 while ACTIVE upgrades to White‑Label
# - Paying $10 always sets/keeps White‑Label
#
# ADMIN PANEL (same in Master + Seller bots, controls CURRENT shop):
# - Users List (list + search) -> edit balance (+/- custom), ban/unban, restrict 7/14/30, reply support
# - Broadcast (text OR photo OR video to all users in that shop)
# - Edit Welcome (text + optional photo/video) — branding applied ONLY at render time for seller shops
# - Edit Wallet Message (any wallet type/instructions)
# - Manage Categories -> Co‑Categories -> Products (each can have optional description + media)
# - Keys per product (1 line = 1 stock). 0 stock => Out of Stock.
# - Product optional Private Telegram Link revealed via "📦 Get File" button (hidden)
#
# WALLET / DEPOSIT:
# - Deposit requires amount + PHOTO proof
# - Approvals go to shop owner:
#     - Master shop deposits -> SUPER ADMIN
#     - Seller shop deposits -> that seller owner (NOT super admin)
# - Approve/Reject deletes the admin request message after handling
#
# SUPPORT:
# - User writes messages, presses DONE to send.
# - Shop owner replies; user receives.
#
# HISTORY:
# - Clean formatting (Deposited / Purchased / Balance edit / Plan)
#
# SUPER ADMIN BUTTON (only your ID) includes:
# - Sellers list (search + click): ban/restrict shop, ban panel, edit seller master-balance, warn expiring
#
# ====================
# ENVIRONMENT VARIABLES
# ====================
# Required:
#   BOT_TOKEN
#   SUPER_ADMIN_ID   (or ADMIN_ID)
# Optional:
#   DB_FILE=data.db
#   STORE_NAME=AutoPanel
#   CURRENCY=USDT
#   PLAN_A_PRICE=5
#   PLAN_B_PRICE=10
#   PLAN_DAYS=30
#   MASTER_BOT_USERNAME=YourMasterBotUsername   (needed for seller "Extend Subscription" deep link)
#
# NOTE:
# - No /admin commands are needed. Everything is buttons + prompts.
# - No asking for user_id/seller_id. Lists + search are used.
# - This file is intentionally self-contained.

import os
import time
import re
import asyncio
import sqlite3
import logging
from typing import Optional, Dict, List, Tuple, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

# ---------------- CONFIG ----------------
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
SUPER_ADMIN_ID = int((os.getenv("SUPER_ADMIN_ID") or os.getenv("ADMIN_ID") or "0").strip() or "0")

DB_FILE = (os.getenv("DB_FILE") or "data.db").strip()
STORE_NAME = (os.getenv("STORE_NAME") or "AutoPanel").strip()
CURRENCY = (os.getenv("CURRENCY") or "USDT").strip()

PLAN_A_PRICE = float((os.getenv("PLAN_A_PRICE") or "5").strip() or "5")    # $5 branded
PLAN_B_PRICE = float((os.getenv("PLAN_B_PRICE") or "10").strip() or "10")  # $10 whitelabel
PLAN_DAYS = int((os.getenv("PLAN_DAYS") or "30").strip() or "30")
MASTER_BOT_USERNAME = (os.getenv("MASTER_BOT_USERNAME") or "").strip().lstrip("@")

BRAND_LINE = "Bot made by @RekkoOwn"

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN")
if SUPER_ADMIN_ID <= 0:
    raise RuntimeError("Missing SUPER_ADMIN_ID / ADMIN_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("autopanel")

# ---------------- UTIL ----------------
def ts() -> int:
    return int(time.time())

def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def money(x: float) -> str:
    x = float(x)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.2f}".rstrip("0").rstrip(".")

def kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)

def grid(btns: List[InlineKeyboardButton], cols: int = 2) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(btns), cols):
        rows.append(btns[i:i+cols])
    return InlineKeyboardMarkup(rows)

async def safe_delete(bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

def parse_float(text: str) -> Optional[float]:
    try:
        return float(text.strip().replace(",", ""))
    except Exception:
        return None

def is_super(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID

# ---------------- DB ----------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = db()
    cur = conn.cursor()

    cur.execute("CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', first_name TEXT DEFAULT '', last_name TEXT DEFAULT '', last_seen INTEGER DEFAULT 0)")
    cur.execute("CREATE TABLE IF NOT EXISTS sessions(user_id INTEGER PRIMARY KEY, shop_owner_id INTEGER NOT NULL, locked INTEGER DEFAULT 0)")

    # sellers: subscription and control flags
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sellers(
        seller_id INTEGER PRIMARY KEY,
        sub_until INTEGER DEFAULT 0,
        plan TEXT DEFAULT 'branded', -- branded / whitelabel
        banned_shop INTEGER DEFAULT 0,
        banned_panel INTEGER DEFAULT 0,
        restricted_until INTEGER DEFAULT 0
    )
    """)

    # seller bots token storage
    cur.execute("""
    CREATE TABLE IF NOT EXISTS seller_bots(
        seller_id INTEGER PRIMARY KEY,
        bot_token TEXT NOT NULL,
        bot_username TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """)

    # per-shop settings
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shop_settings(
        shop_owner_id INTEGER PRIMARY KEY,
        wallet_message TEXT DEFAULT '',
        welcome_text TEXT DEFAULT '',
        welcome_file_id TEXT DEFAULT '',
        welcome_file_type TEXT DEFAULT '', -- photo/video
        connect_desc TEXT DEFAULT ''
    )
    """)

    # balances: per shop per user
    cur.execute("""
    CREATE TABLE IF NOT EXISTS balances(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        balance REAL DEFAULT 0,
        PRIMARY KEY(shop_owner_id, user_id)
    )
    """)

    # bans/restrictions per shop per user
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_bans(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        banned INTEGER DEFAULT 0,
        restricted_until INTEGER DEFAULT 0,
        PRIMARY KEY(shop_owner_id, user_id)
    )
    """)

    # catalog hierarchy
    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        file_type TEXT DEFAULT '' -- photo/video
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cocategories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        category_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        file_type TEXT DEFAULT ''
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        category_id INTEGER NOT NULL,
        cocategory_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        description TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        file_type TEXT DEFAULT '',
        tg_link TEXT DEFAULT ''
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_keys(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        key_line TEXT NOT NULL,
        delivered_once INTEGER DEFAULT 0,
        delivered_to INTEGER DEFAULT 0,
        delivered_at INTEGER DEFAULT 0
    )
    """)

    # deposits
    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposit_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        proof_file_id TEXT NOT NULL,
        status TEXT NOT NULL, -- pending/approved/rejected
        created_at INTEGER NOT NULL,
        handled_by INTEGER DEFAULT 0,
        handled_at INTEGER DEFAULT 0,
        admin_chat_id INTEGER DEFAULT 0,
        admin_msg_id INTEGER DEFAULT 0
    )
    """)

    # support tickets
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL, -- open/closed
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ticket_messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )
    """)

    # history/ledger
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL, -- deposit/purchase/balance_edit/plan
        amount REAL DEFAULT 0,
        note TEXT DEFAULT '',
        qty INTEGER DEFAULT 1,
        created_at INTEGER NOT NULL
    )
    """)

    conn.commit()
    conn.close()

    ensure_shop_settings(SUPER_ADMIN_ID)
    s = get_shop_settings(SUPER_ADMIN_ID)
    if not (s["welcome_text"] or "").strip():
        set_shop_setting(SUPER_ADMIN_ID, "welcome_text", f"✅ Welcome to <b>{esc(STORE_NAME)}</b>\nGet your 24/7 Store Panel Here !!\n\nBot created by @RekkoOwn")
    if not (s["connect_desc"] or "").strip():
        set_shop_setting(
            SUPER_ADMIN_ID,
            "connect_desc",
            "🤖 <b>Connect My Bot</b>\n\n"
            "Create your own bot at @BotFather, then connect your token here.\n"
            "You must deposit to Main Shop wallet first.\n\n"
            f"Plan A: <b>{money(PLAN_A_PRICE)} {esc(CURRENCY)}</b> / {PLAN_DAYS} days (Branded welcome)\n"
            f"Plan B: <b>{money(PLAN_B_PRICE)} {esc(CURRENCY)}</b> / {PLAN_DAYS} days (White‑Label)\n"
        )

# --- settings ---
def ensure_shop_settings(shop_owner_id: int) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    if not cur.fetchone():
        cur.execute("""INSERT INTO shop_settings(shop_owner_id,wallet_message,welcome_text,welcome_file_id,welcome_file_type,connect_desc)
                       VALUES(?,?,?,?,?,?)""",
                    (shop_owner_id, "", "", "", "", ""))
        conn.commit()
    conn.close()

def get_shop_settings(shop_owner_id: int) -> sqlite3.Row:
    ensure_shop_settings(shop_owner_id)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    r = cur.fetchone(); conn.close()
    return r

def set_shop_setting(shop_owner_id: int, field: str, value: str) -> None:
    ensure_shop_settings(shop_owner_id)
    conn = db(); cur = conn.cursor()
    cur.execute(f"UPDATE shop_settings SET {field}=? WHERE shop_owner_id=?", (value or "", shop_owner_id))
    conn.commit(); conn.close()

# --- users ---
def upsert_user(u) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO users(user_id, username, first_name, last_name, last_seen) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name, last_seen=excluded.last_seen",
        (u.id, u.username or "", u.first_name or "", u.last_name or "", ts())
    )
    conn.commit(); conn.close()

def user_row(uid: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone(); conn.close()
    return r

def user_display(uid: int) -> str:
    r = user_row(uid)
    if not r:
        return str(uid)
    un = (r["username"] or "").strip()
    if un:
        return f"@{un}"
    name = " ".join([x for x in [(r["first_name"] or "").strip(), (r["last_name"] or "").strip()] if x]).strip()
    return name or str(uid)

# --- session (master only) ---
def set_session(uid: int, shop_owner_id: int, locked: int) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions(user_id, shop_owner_id, locked) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET shop_owner_id=excluded.shop_owner_id, locked=excluded.locked",
        (uid, shop_owner_id, int(locked))
    )
    conn.commit(); conn.close()

def get_session(uid: int) -> Tuple[int, int]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT shop_owner_id, locked FROM sessions WHERE user_id=?", (uid,))
    r = cur.fetchone(); conn.close()
    if not r:
        return SUPER_ADMIN_ID, 0
    return int(r["shop_owner_id"]), int(r["locked"] or 0)

# --- balances ---
def ensure_balance(shop_owner_id: int, uid: int) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,0)", (shop_owner_id, uid))
    conn.commit(); conn.close()

def get_balance(shop_owner_id: int, uid: int) -> float:
    ensure_balance(shop_owner_id, uid)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT balance FROM balances WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    r = cur.fetchone(); conn.close()
    return float(r["balance"] or 0) if r else 0.0

def set_balance(shop_owner_id: int, uid: int, val: float) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,?) "
        "ON CONFLICT(shop_owner_id, user_id) DO UPDATE SET balance=excluded.balance",
        (shop_owner_id, uid, max(0.0, float(val)))
    )
    conn.commit(); conn.close()

def add_balance(shop_owner_id: int, uid: int, delta: float) -> float:
    bal = get_balance(shop_owner_id, uid)
    newv = max(0.0, bal + float(delta))
    set_balance(shop_owner_id, uid, newv)
    return newv

def list_shop_user_ids(shop_owner_id: int) -> List[int]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM balances WHERE shop_owner_id=?", (shop_owner_id,))
    rows = cur.fetchall(); conn.close()
    return [int(r["user_id"]) for r in rows]

def log_tx(shop_owner_id: int, uid: int, kind: str, amount: float, note: str = "", qty: int = 1) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT INTO transactions(shop_owner_id,user_id,kind,amount,note,qty,created_at) VALUES(?,?,?,?,?,?,?)",
                (shop_owner_id, uid, kind, float(amount), note or "", int(qty or 1), ts()))
    conn.commit(); conn.close()

# --- bans ---
def is_banned_user(shop_owner_id: int, uid: int) -> bool:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT banned, restricted_until FROM user_bans WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    r = cur.fetchone(); conn.close()
    if not r:
        return False
    if int(r["banned"] or 0) == 1:
        return True
    if int(r["restricted_until"] or 0) > ts():
        return True
    return False

def ban_user(shop_owner_id: int, uid: int, banned: int) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute("""INSERT INTO user_bans(shop_owner_id,user_id,banned,restricted_until) VALUES(?,?,?,0)
                   ON CONFLICT(shop_owner_id,user_id) DO UPDATE SET banned=excluded.banned""",
                (shop_owner_id, uid, int(banned)))
    conn.commit(); conn.close()

def restrict_user(shop_owner_id: int, uid: int, days: int) -> None:
    until = ts() + max(0, int(days)) * 86400
    conn = db(); cur = conn.cursor()
    cur.execute("""INSERT INTO user_bans(shop_owner_id,user_id,banned,restricted_until) VALUES(?,?,0,?)
                   ON CONFLICT(shop_owner_id,user_id) DO UPDATE SET restricted_until=excluded.restricted_until, banned=0""",
                (shop_owner_id, uid, until))
    conn.commit(); conn.close()

# --- sellers / plans ---
def ensure_seller(seller_id: int) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO sellers(seller_id, sub_until, plan) VALUES(?,?,?)", (seller_id, 0, "branded"))
    conn.commit(); conn.close()

def seller_row(seller_id: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM sellers WHERE seller_id=?", (seller_id,))
    r = cur.fetchone(); conn.close()
    return r

def seller_plan(seller_id: int) -> str:
    if is_super(seller_id):
        return "whitelabel"
    r = seller_row(seller_id)
    return (r["plan"] if r else "branded") or "branded"

def seller_set_plan(seller_id: int, plan: str) -> None:
    ensure_seller(seller_id)
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE sellers SET plan=? WHERE seller_id=?", (plan, seller_id))
    conn.commit(); conn.close()

def seller_days_left(seller_id: int) -> int:
    if is_super(seller_id):
        return 10**9
    r = seller_row(seller_id)
    if not r:
        return 0
    return max(0, int(r["sub_until"] or 0) - ts()) // 86400

def seller_active(seller_id: int) -> bool:
    if is_super(seller_id):
        return True
    r = seller_row(seller_id)
    if not r:
        return False
    if int(r["banned_shop"] or 0) == 1:
        return False
    if int(r["restricted_until"] or 0) > ts():
        return False
    return int(r["sub_until"] or 0) > ts()

def seller_add_days(seller_id: int, days: int) -> None:
    ensure_seller(seller_id)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT sub_until FROM sellers WHERE seller_id=?", (seller_id,))
    r = cur.fetchone()
    base = max(int(r["sub_until"] or 0), ts())
    cur.execute("UPDATE sellers SET sub_until=? WHERE seller_id=?", (base + int(days)*86400, seller_id))
    conn.commit(); conn.close()

def super_set_seller_flag(seller_id: int, field: str, val: int) -> None:
    ensure_seller(seller_id)
    conn = db(); cur = conn.cursor()
    cur.execute(f"UPDATE sellers SET {field}=? WHERE seller_id=?", (int(val), seller_id))
    conn.commit(); conn.close()

def super_restrict_seller(seller_id: int, days: int) -> None:
    ensure_seller(seller_id)
    until = ts() + max(0, int(days)) * 86400
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE sellers SET restricted_until=? WHERE seller_id=?", (until, seller_id))
    conn.commit(); conn.close()

def list_sellers() -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM sellers ORDER BY sub_until DESC")
    rows = cur.fetchall(); conn.close()
    return rows

# --- seller bots ---
def upsert_seller_bot(seller_id: int, token: str, username: str) -> None:
    ensure_seller(seller_id)
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO seller_bots(seller_id, bot_token, bot_username, enabled, created_at, updated_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(seller_id) DO UPDATE SET bot_token=excluded.bot_token, bot_username=excluded.bot_username,
            enabled=1, updated_at=excluded.updated_at
    """, (seller_id, token, username, 1, ts(), ts()))
    conn.commit(); conn.close()

def get_seller_bot(seller_id: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM seller_bots WHERE seller_id=?", (seller_id,))
    r = cur.fetchone(); conn.close()
    return r

def list_enabled_seller_bots() -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM seller_bots WHERE enabled=1")
    rows = cur.fetchall(); conn.close()
    return rows

def disable_seller_bot(seller_id: int) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE seller_bots SET enabled=0, updated_at=? WHERE seller_id=?", (ts(), seller_id))
    conn.commit(); conn.close()

# --- catalog ---
def list_categories(shop_owner_id: int) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE shop_owner_id=? ORDER BY id DESC", (shop_owner_id,))
    rows = cur.fetchall(); conn.close()
    return rows

def list_cocats(shop_owner_id: int, cat_id: int) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM cocategories WHERE shop_owner_id=? AND category_id=? ORDER BY id DESC", (shop_owner_id, cat_id))
    rows = cur.fetchall(); conn.close()
    return rows

def list_products(shop_owner_id: int, cat_id: int, cocat_id: int) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE shop_owner_id=? AND category_id=? AND cocategory_id=? ORDER BY id DESC",
                (shop_owner_id, cat_id, cocat_id))
    rows = cur.fetchall(); conn.close()
    return rows

def get_product(shop_owner_id: int, pid: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE shop_owner_id=? AND id=?", (shop_owner_id, pid))
    r = cur.fetchone(); conn.close()
    return r

def stock_count(shop_owner_id: int, pid: int) -> int:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(1) c FROM product_keys WHERE shop_owner_id=? AND product_id=? AND delivered_once=0",
                (shop_owner_id, pid))
    r = cur.fetchone(); conn.close()
    return int(r["c"] or 0) if r else 0

def pop_keys(shop_owner_id: int, pid: int, uid: int, qty: int) -> List[str]:
    conn = db(); cur = conn.cursor()
    cur.execute("""SELECT id, key_line FROM product_keys
                   WHERE shop_owner_id=? AND product_id=? AND delivered_once=0
                   ORDER BY id ASC LIMIT ?""",
                (shop_owner_id, pid, qty))
    rows = cur.fetchall()
    ids = [int(r["id"]) for r in rows]
    keys = [r["key_line"] for r in rows]
    if ids:
        cur.execute(f"""UPDATE product_keys
                        SET delivered_once=1, delivered_to=?, delivered_at=?
                        WHERE id IN ({",".join(["?"]*len(ids))})""",
                    (uid, ts(), *ids))
    conn.commit(); conn.close()
    return keys

# --- support ---
def get_open_ticket(shop_owner_id: int, user_id: int) -> Optional[int]:
    conn = db(); cur = conn.cursor()
    cur.execute("""SELECT id FROM tickets
                   WHERE shop_owner_id=? AND user_id=? AND status='open'
                   ORDER BY id DESC LIMIT 1""",
                (shop_owner_id, user_id))
    r = cur.fetchone(); conn.close()
    return int(r["id"]) if r else None

def create_ticket(shop_owner_id: int, user_id: int) -> int:
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT INTO tickets(shop_owner_id,user_id,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                (shop_owner_id, user_id, "open", ts(), ts()))
    tid = cur.lastrowid
    conn.commit(); conn.close()
    return int(tid)

def add_ticket_msg(ticket_id: int, sender_id: int, text: str) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT INTO ticket_messages(ticket_id,sender_id,text,created_at) VALUES(?,?,?,?)",
                (ticket_id, sender_id, text, ts()))
    cur.execute("UPDATE tickets SET updated_at=? WHERE id=?", (ts(), ticket_id))
    conn.commit(); conn.close()

# ---------------- BRANDING (WELCOME ONLY) ----------------
def render_welcome_text(shop_owner_id: int) -> str:
    s = get_shop_settings(shop_owner_id)
    base = (s["welcome_text"] or "").strip()
    # Seller shops: apply branding ONLY if branded or expired; remove if whitelabel active.
    if shop_owner_id != SUPER_ADMIN_ID:
        if seller_active(shop_owner_id) and seller_plan(shop_owner_id) == "whitelabel":
            return base
        if BRAND_LINE not in base:
            return (base + "\n\n" + BRAND_LINE).strip()
        return base
    # Master shop: you can edit the text freely (you can include branding yourself)
    return base

# ---------------- MENUS ----------------
def master_menu(uid: int) -> InlineKeyboardMarkup:
    btns = [
        InlineKeyboardButton("🛒 Products", callback_data="m:products"),
        InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
        InlineKeyboardButton("📜 History", callback_data="m:history"),
        InlineKeyboardButton("🆘 Support", callback_data="m:support"),
        InlineKeyboardButton("🤖 Connect My Bot", callback_data="m:connect"),
    ]
    if is_super(uid):
        btns += [
            InlineKeyboardButton("🛠 Admin Panel", callback_data="m:admin"),
            InlineKeyboardButton("👑 Super Admin", callback_data="m:super"),
        ]
    return grid(btns, 2)

def seller_menu(uid: int, seller_id: int) -> InlineKeyboardMarkup:
    btns = [
        InlineKeyboardButton("🛒 Products", callback_data="m:products"),
        InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
        InlineKeyboardButton("📜 History", callback_data="m:history"),
        InlineKeyboardButton("🆘 Support", callback_data="m:support"),
    ]
    if uid == seller_id or is_super(uid):
        btns += [
            InlineKeyboardButton("🛠 Admin Panel", callback_data="m:admin"),
            InlineKeyboardButton("⏳ Extend Subscription", callback_data="m:extend"),
        ]
    return grid(btns, 2)

def back_menu_kb(bot_kind: str) -> InlineKeyboardMarkup:
    return kb([[InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")]])

# ---------------- MULTI‑BOT MANAGER ----------------
class BotManager:
    def __init__(self):
        self.apps: Dict[int, Application] = {}
        self.tasks: Dict[int, asyncio.Task] = {}

    async def start_seller_bot(self, seller_id: int, token: str):
        await self.stop_seller_bot(seller_id)
        app = Application.builder().token(token).build()
        register_handlers(app, shop_owner_id=seller_id, bot_kind="seller")
        await app.initialize()
        await app.start()
        task = asyncio.create_task(app.updater.start_polling(drop_pending_updates=True))
        self.apps[seller_id] = app
        self.tasks[seller_id] = task
        log.info("Started seller bot seller_id=%s", seller_id)

    async def stop_seller_bot(self, seller_id: int):
        task = self.tasks.pop(seller_id, None)
        app = self.apps.pop(seller_id, None)
        if not app:
            return
        try:
            if task:
                task.cancel()
        except Exception:
            pass
        try:
            await app.updater.stop()
        except Exception:
            pass
        try:
            await app.stop()
            await app.shutdown()
        except Exception:
            pass
        log.info("Stopped seller bot seller_id=%s", seller_id)

MANAGER = BotManager()

async def watchdog():
    while True:
        try:
            for r in list_enabled_seller_bots():
                sid = int(r["seller_id"])
                # stop bot if inactive/banned/restricted/expired
                if not seller_active(sid) or int(seller_row(sid)["banned_shop"] or 0) == 1:
                    disable_seller_bot(sid)
                    await MANAGER.stop_seller_bot(sid)
            await asyncio.sleep(60)
        except Exception:
            log.exception("watchdog loop")
            await asyncio.sleep(60)

# ---------------- USER STATE HELPERS ----------------
def set_state(context: ContextTypes.DEFAULT_TYPE, key: str, data: Dict[str, Any]):
    context.user_data["state"] = key
    context.user_data["state_data"] = data

def get_state(context: ContextTypes.DEFAULT_TYPE) -> Tuple[Optional[str], Dict[str, Any]]:
    return context.user_data.get("state"), (context.user_data.get("state_data") or {})

def clear_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("state", None)
    context.user_data.pop("state_data", None)

# ---------------- HANDLERS (REGISTER PER BOT) ----------------
def register_handlers(app: Application, shop_owner_id: int, bot_kind: str):
    # bot_kind: "master" or "seller"
    async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_id: int):
        ensure_shop_settings(shop_id)
        s = get_shop_settings(shop_id)
        file_id = (s["welcome_file_id"] or "").strip()
        ftype = (s["welcome_file_type"] or "").strip()

        if bot_kind == "seller":
            title = f"🏬 <b>{esc(user_display(shop_id))} Shop</b>\n\n"
            menu = seller_menu(update.effective_user.id, shop_id)
        else:
            title = f"🏬 <b>{esc(STORE_NAME)}</b>\n\n"
            menu = master_menu(update.effective_user.id)

        text = render_welcome_text(shop_id)
        caption = title + (text or "")

        if file_id and ftype == "photo":
            await context.bot.send_photo(update.effective_chat.id, photo=file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=menu)
        elif file_id and ftype == "video":
            await context.bot.send_video(update.effective_chat.id, video=file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=menu)
        else:
            await context.bot.send_message(update.effective_chat.id, caption, parse_mode=ParseMode.HTML, reply_markup=menu)

    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        upsert_user(update.effective_user)
        uid = update.effective_user.id

        if bot_kind == "seller":
            ensure_balance(shop_owner_id, uid)
            await send_welcome(update, context, shop_owner_id)
            return

        # master: lock session to master shop
        set_session(uid, SUPER_ADMIN_ID, 0)
        ensure_balance(SUPER_ADMIN_ID, uid)
        await send_welcome(update, context, SUPER_ADMIN_ID)

        # start arguments
        arg = context.args[0] if context.args else ""
        if arg == "extend":
            await show_extend_master(update, context, uid)

    async def menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            await update.callback_query.answer()
        clear_state(context)
        uid = update.effective_user.id
        if bot_kind == "seller":
            await context.bot.send_message(update.effective_chat.id, "Main Menu:", reply_markup=seller_menu(uid, shop_owner_id))
        else:
            await context.bot.send_message(update.effective_chat.id, "Main Menu:", reply_markup=master_menu(uid))

    # ---------- Products ----------
    def current_shop_id() -> int:
        return shop_owner_id if bot_kind == "seller" else SUPER_ADMIN_ID

    async def products_root(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = current_shop_id()
        uid = update.effective_user.id
        if is_banned_user(sid, uid):
            await update.callback_query.message.reply_text("❌ You are restricted from this shop.", reply_markup=back_menu_kb(bot_kind))
            return
        cats = list_categories(sid)
        if not cats:
            await update.callback_query.message.reply_text("No categories yet.", reply_markup=back_menu_kb(bot_kind))
            return
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"p:cat:{c['id']}")] for c in cats[:50]]
        rows.append([InlineKeyboardButton("⬅️ Menu", callback_data="m:menu")])
        await update.callback_query.message.reply_text("Select Category:", reply_markup=kb(rows))

    async def products_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = current_shop_id()
        uid = update.effective_user.id
        if is_banned_user(sid, uid):
            await update.callback_query.message.reply_text("❌ You are restricted from this shop.", reply_markup=back_menu_kb(bot_kind))
            return
        cat_id = int(update.callback_query.data.split(":")[2])
        subs = list_cocats(sid, cat_id)
        if not subs:
            await update.callback_query.message.reply_text("No sub-categories yet.", reply_markup=back_menu_kb(bot_kind))
            return
        rows = [[InlineKeyboardButton(sc["name"], callback_data=f"p:sub:{cat_id}:{sc['id']}")] for sc in subs[:50]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="m:products"), InlineKeyboardButton("🏠 Menu", callback_data="m:menu")])
        await update.callback_query.message.reply_text("Select Sub-Category:", reply_markup=kb(rows))

    async def products_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = current_shop_id()
        uid = update.effective_user.id
        if is_banned_user(sid, uid):
            await update.callback_query.message.reply_text("❌ You are restricted from this shop.", reply_markup=back_menu_kb(bot_kind))
            return
        _, _, _, cat_s, sub_s = update.callback_query.data.split(":")
        cat_id = int(cat_s); sub_id = int(sub_s)
        prods = list_products(sid, cat_id, sub_id)
        if not prods:
            await update.callback_query.message.reply_text("No products yet.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data=f"p:cat:{cat_id}")],[InlineKeyboardButton("🏠 Menu", callback_data="m:menu")]]))
            return
        rows = [[InlineKeyboardButton(p["name"], callback_data=f"p:prod:{p['id']}")] for p in prods[:50]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"p:cat:{cat_id}"), InlineKeyboardButton("🏠 Menu", callback_data="m:menu")])
        await update.callback_query.message.reply_text("Select Product:", reply_markup=kb(rows))

    async def product_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = current_shop_id()
        pid = int(update.callback_query.data.split(":")[2])
        p = get_product(sid, pid)
        if not p:
            await update.callback_query.message.reply_text("Product not found.", reply_markup=back_menu_kb(bot_kind))
            return
        stock = stock_count(sid, pid)
        price = float(p["price"])
        qty = int(context.user_data.get(f"qty_{sid}_{pid}", 1))
        qty = max(1, qty)
        total = price * qty
        desc = (p["description"] or "").strip()
        text = (
            f"<b>{esc(p['name'])}</b>\n\n"
            f"Price: <b>{money(price)} {esc(CURRENCY)}</b>\n"
            f"Stock: <b>{stock}</b>\n"
            f"Qty: <b>{qty}</b>\n"
            f"Total: <b>{money(total)} {esc(CURRENCY)}</b>"
        )
        if desc:
            text += f"\n\n{esc(desc)}"

        rows = [
            [InlineKeyboardButton("➖", callback_data=f"p:q:-:{pid}"),
             InlineKeyboardButton("➕", callback_data=f"p:q:+:{pid}"),
             InlineKeyboardButton("✅ Buy", callback_data=f"p:buy:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"p:sub:{p['category_id']}:{p['cocategory_id']}")],
            [InlineKeyboardButton("🏠 Menu", callback_data="m:menu")],
        ]
        file_id = (p["file_id"] or "").strip()
        ftype = (p["file_type"] or "").strip()
        if file_id and ftype == "photo":
            await update.callback_query.message.reply_photo(photo=file_id, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb(rows))
        elif file_id and ftype == "video":
            await update.callback_query.message.reply_video(video=file_id, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb(rows))
        else:
            await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    async def product_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = current_shop_id()
        _, _, sign, pid_s = update.callback_query.data.split(":")
        pid = int(pid_s)
        key = f"qty_{sid}_{pid}"
        cur = int(context.user_data.get(key, 1))
        if sign == "+":
            cur += 1
        else:
            cur = max(1, cur - 1)
        context.user_data[key] = cur
        await update.callback_query.message.reply_text("✅ Updated quantity.", reply_markup=kb([[InlineKeyboardButton("View Product", callback_data=f"p:prod:{pid}")]]))

    async def product_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = current_shop_id()
        uid = update.effective_user.id
        if is_banned_user(sid, uid):
            await update.callback_query.message.reply_text("❌ You are restricted from this shop.", reply_markup=back_menu_kb(bot_kind))
            return
        pid = int(update.callback_query.data.split(":")[2])
        p = get_product(sid, pid)
        if not p:
            await update.callback_query.message.reply_text("Product not found.", reply_markup=back_menu_kb(bot_kind))
            return
        qty = int(context.user_data.get(f"qty_{sid}_{pid}", 1))
        qty = max(1, qty)
        stock = stock_count(sid, pid)
        if stock < qty:
            await update.callback_query.message.reply_text("❌ Out of stock / not enough stock.", reply_markup=back_menu_kb(bot_kind))
            return
        price = float(p["price"])
        total = price * qty
        bal = get_balance(sid, uid)
        if bal < total:
            await update.callback_query.message.reply_text(f"❌ Not enough balance.\nBalance: {money(bal)} {esc(CURRENCY)}", parse_mode=ParseMode.HTML, reply_markup=back_menu_kb(bot_kind))
            return

        set_balance(sid, uid, bal - total)
        log_tx(sid, uid, "purchase", -total, p["name"], qty)
        keys = pop_keys(sid, pid, uid, qty)
        link = (p["tg_link"] or "").strip()

        msg = (
            f"✅ <b>Purchase Successful</b>\n\n"
            f"Product: <b>{esc(p['name'])}</b>\n"
            f"Qty: <b>{qty}</b>\n"
            f"Paid: <b>{money(total)} {esc(CURRENCY)}</b>\n\n"
            f"<b>Key(s):</b>\n" + "\n".join([f"<code>{esc(k)}</code>" for k in keys])
        )
        rows = []
        if link:
            rows.append([InlineKeyboardButton("📦 Get File", callback_data=f"p:file:{pid}")])
        rows.append([InlineKeyboardButton("🏠 Menu", callback_data="m:menu")])
        await update.callback_query.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

        # notifications
        try:
            if bot_kind == "seller":
                await context.bot.send_message(chat_id=shop_owner_id, text=f"🔔 New purchase\nUser: {user_display(uid)}\nProduct: {p['name']}\nQty: {qty}\nTotal: {money(total)} {CURRENCY}")
                await context.bot.send_message(chat_id=SUPER_ADMIN_ID, text=f"🔔 Seller purchase\nSeller: {user_display(shop_owner_id)}\nUser: {user_display(uid)}\nProduct: {p['name']}\nQty: {qty}\nTotal: {money(total)} {CURRENCY}")
            else:
                await context.bot.send_message(chat_id=SUPER_ADMIN_ID, text=f"🔔 Main shop purchase\nUser: {user_display(uid)}\nProduct: {p['name']}\nQty: {qty}\nTotal: {money(total)} {CURRENCY}")
        except Exception:
            pass

    async def product_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = current_shop_id()
        pid = int(update.callback_query.data.split(":")[2])
        p = get_product(sid, pid)
        if not p:
            await update.callback_query.message.reply_text("Not found.", reply_markup=back_menu_kb(bot_kind))
            return
        link = (p["tg_link"] or "").strip()
        if not link:
            await update.callback_query.message.reply_text("No private link set.", reply_markup=back_menu_kb(bot_kind))
            return
        await update.callback_query.message.reply_text(f"📦 <b>Private Link</b>\n{esc(link)}", parse_mode=ParseMode.HTML, reply_markup=back_menu_kb(bot_kind))

    # ---------- Wallet / Deposit ----------
    async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = current_shop_id()
        uid = update.effective_user.id
        if is_banned_user(sid, uid):
            await update.callback_query.message.reply_text("❌ You are restricted from this shop.", reply_markup=back_menu_kb(bot_kind))
            return
        s = get_shop_settings(sid)
        bal = get_balance(sid, uid)
        wmsg = (s["wallet_message"] or "").strip() or "No wallet message set yet."
        text = f"💰 <b>Wallet</b>\n\nBalance: <b>{money(bal)} {esc(CURRENCY)}</b>\n\n{esc(wmsg)}"
        await update.callback_query.message.reply_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=kb([
                [InlineKeyboardButton("➕ Deposit", callback_data="w:deposit")],
                [InlineKeyboardButton("⬅️ Menu", callback_data="m:menu")]
            ])
        )

    async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = current_shop_id()
        set_state(context, "deposit_amount", {"shop_id": sid})
        await update.callback_query.message.reply_text("Send deposit amount (example: 10):", reply_markup=back_menu_kb(bot_kind))

    async def deposit_amount_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state, data = get_state(context)
        if state != "deposit_amount":
            return
        amt = parse_float(update.message.text or "")
        if amt is None or amt <= 0:
            await update.message.reply_text("❌ Invalid amount. Send a number (example: 10).")
            return
        set_state(context, "deposit_proof", {"shop_id": int(data["shop_id"]), "amount": float(amt)})
        await update.message.reply_text("Now send a PHOTO proof of payment.")

    async def deposit_proof_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state, data = get_state(context)
        if state != "deposit_proof":
            return
        if not update.message.photo:
            await update.message.reply_text("❌ Please send a PHOTO proof.")
            return
        sid = int(data["shop_id"])
        uid = update.effective_user.id
        amt = float(data["amount"])
        file_id = update.message.photo[-1].file_id

        conn = db(); cur = conn.cursor()
        cur.execute("""INSERT INTO deposit_requests(shop_owner_id,user_id,amount,proof_file_id,status,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (sid, uid, amt, file_id, "pending", ts()))
        req_id = cur.lastrowid
        conn.commit(); conn.close()

        clear_state(context)
        await update.message.reply_text("✅ Deposit submitted. Waiting for approval.", reply_markup=back_menu_kb(bot_kind))

        # send to shop owner (not super admin for seller shop)
        owner_chat = sid if sid != SUPER_ADMIN_ID else SUPER_ADMIN_ID
        try:
            m = await context.bot.send_photo(
                chat_id=owner_chat,
                photo=file_id,
                caption=f"💳 <b>Deposit Request</b>\n\nUser: {esc(user_display(uid))}\nAmount: <b>{money(amt)} {esc(CURRENCY)}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"d:ok:{req_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"d:no:{req_id}")
                ]])
            )
            conn = db(); cur = conn.cursor()
            cur.execute("UPDATE deposit_requests SET admin_chat_id=?, admin_msg_id=? WHERE id=?", (owner_chat, m.message_id, req_id))
            conn.commit(); conn.close()
        except Exception:
            pass

    async def deposit_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, decision, rid_s = update.callback_query.data.split(":")
        rid = int(rid_s)
        conn = db(); cur = conn.cursor()
        cur.execute("SELECT * FROM deposit_requests WHERE id=?", (rid,))
        r = cur.fetchone()
        if not r:
            conn.close()
            await update.callback_query.message.reply_text("Request not found.")
            return
        sid = int(r["shop_owner_id"])
        uid = update.effective_user.id
        # permission: shop owner only (or super admin for master shop)
        if not ((uid == sid) or (sid == SUPER_ADMIN_ID and is_super(uid))):
            conn.close()
            await update.callback_query.message.reply_text("❌ Not allowed.")
            return
        if r["status"] != "pending":
            conn.close()
            await update.callback_query.message.reply_text("Already handled.")
            return

        user_id = int(r["user_id"])
        amt = float(r["amount"])
        if decision == "ok":
            add_balance(sid, user_id, amt)
            log_tx(sid, user_id, "deposit", amt, "")
            cur.execute("UPDATE deposit_requests SET status='approved', handled_by=?, handled_at=? WHERE id=?",
                        (uid, ts(), rid))
            conn.commit(); conn.close()
            try:
                await context.bot.send_message(chat_id=user_id,
                                               text=f"✅ Deposited: {money(amt)} {CURRENCY}\nTotal Balance: {money(get_balance(sid, user_id))} {CURRENCY}")
            except Exception:
                pass
        else:
            cur.execute("UPDATE deposit_requests SET status='rejected', handled_by=?, handled_at=? WHERE id=?",
                        (uid, ts(), rid))
            conn.commit(); conn.close()
            try:
                await context.bot.send_message(chat_id=user_id, text=f"❌ Deposit Rejected: {money(amt)} {CURRENCY}")
            except Exception:
                pass

        # delete admin message
        try:
            await safe_delete(context.bot, int(r["admin_chat_id"]), int(r["admin_msg_id"]))
        except Exception:
            pass

    # ---------- History ----------
    async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = current_shop_id()
        uid = update.effective_user.id
        if is_banned_user(sid, uid):
            await update.callback_query.message.reply_text("❌ You are restricted from this shop.", reply_markup=back_menu_kb(bot_kind))
            return
        conn = db(); cur = conn.cursor()
        cur.execute("SELECT * FROM transactions WHERE shop_owner_id=? AND user_id=? ORDER BY id DESC LIMIT 30", (sid, uid))
        rows = cur.fetchall(); conn.close()
        bal = get_balance(sid, uid)
        if not rows:
            await update.callback_query.message.reply_text(f"📜 <b>History</b>\n\nNo history yet.\n\nTotal Balance: <b>{money(bal)} {esc(CURRENCY)}</b>",
                                                          parse_mode=ParseMode.HTML, reply_markup=back_menu_kb(bot_kind))
            return
        lines = ["📜 <b>History</b>\n"]
        for r in rows:
            kind = r["kind"]
            amt = float(r["amount"] or 0)
            note = (r["note"] or "").strip()
            qty = int(r["qty"] or 1)
            if kind == "deposit":
                lines.append(f"✅ Deposited: <b>{money(amt)} {esc(CURRENCY)}</b>")
            elif kind == "purchase":
                lines.append(f"🛒 Purchased: <b>{esc(note)}</b> (x{qty}) — <b>{money(abs(amt))} {esc(CURRENCY)}</b>")
            elif kind == "balance_edit":
                sign = "+" if amt >= 0 else "-"
                lines.append(f"⚙️ Balance {sign}: <b>{money(abs(amt))} {esc(CURRENCY)}</b>")
            elif kind == "plan":
                lines.append(f"🤖 Plan: <b>{esc(note)}</b> — <b>{money(abs(amt))} {esc(CURRENCY)}</b>")
            else:
                lines.append(f"{esc(kind)}: <b>{money(amt)} {esc(CURRENCY)}</b>")
        lines.append(f"\nTotal Balance: <b>{money(bal)} {esc(CURRENCY)}</b>")
        await update.callback_query.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_menu_kb(bot_kind))

    # ---------- Support ----------
    async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = current_shop_id()
        uid = update.effective_user.id
        if is_banned_user(sid, uid):
            await update.callback_query.message.reply_text("❌ You are restricted from this shop.", reply_markup=back_menu_kb(bot_kind))
            return
        set_state(context, "support_draft", {"shop_id": sid, "text": ""})
        await update.callback_query.message.reply_text(
            "Type your support message. When finished, press DONE.",
            reply_markup=kb([
                [InlineKeyboardButton("✅ DONE", callback_data="s:done")],
                [InlineKeyboardButton("⬅️ Cancel", callback_data="m:menu")]
            ])
        )

    async def support_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state, data = get_state(context)
        if state != "support_draft":
            return
        t = (update.message.text or "").strip()
        if not t:
            return
        data["text"] = (data.get("text", "") + ("\n" if data.get("text") else "") + t)[:3500]
        set_state(context, "support_draft", data)
        await update.message.reply_text("Added. Press DONE when finished.", reply_markup=kb([[InlineKeyboardButton("✅ DONE", callback_data="s:done")]]))

    async def support_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        state, data = get_state(context)
        if state != "support_draft":
            await update.callback_query.message.reply_text("No draft.", reply_markup=back_menu_kb(bot_kind))
            return
        sid = int(data["shop_id"])
        uid = update.effective_user.id
        text = (data.get("text") or "").strip()
        if not text:
            await update.callback_query.message.reply_text("Send a message first.", reply_markup=back_menu_kb(bot_kind))
            return
        clear_state(context)
        tid = get_open_ticket(sid, uid)
        if not tid:
            tid = create_ticket(sid, uid)
        add_ticket_msg(tid, uid, text)
        await update.callback_query.message.reply_text("✅ Sent to support.", reply_markup=back_menu_kb(bot_kind))

        # notify owner (no super admin id shown)
        owner = sid if sid != SUPER_ADMIN_ID else SUPER_ADMIN_ID
        try:
            await context.bot.send_message(
                chat_id=owner,
                text=f"🆘 <b>Support Ticket</b>\nUser: {esc(user_display(uid))}\n\n{esc(text)}",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("↩️ Reply", callback_data=f"a:reply:{uid}:{sid}")]])
            )
        except Exception:
            pass

    async def admin_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, uid_s, sid_s = update.callback_query.data.split(":")
        target_uid = int(uid_s)
        sid = int(sid_s)
        # only shop owner can reply (or super admin for master shop)
        me = update.effective_user.id
        if not ((me == sid) or (sid == SUPER_ADMIN_ID and is_super(me))):
            await update.callback_query.message.reply_text("❌ Not allowed.")
            return
        set_state(context, "admin_reply", {"target_uid": target_uid, "shop_id": sid})
        await update.callback_query.message.reply_text(f"Reply to {user_display(target_uid)} (send text):", reply_markup=kb([[InlineKeyboardButton("❌ Cancel", callback_data="m:menu")]]))

    async def admin_reply_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state, data = get_state(context)
        if state != "admin_reply":
            return
        target_uid = int(data["target_uid"])
        sid = int(data["shop_id"])
        text = (update.message.text or "").strip()
        if not text:
            return
        clear_state(context)
        try:
            await context.bot.send_message(chat_id=target_uid, text=f"✅ Support Reply:\n\n{text}")
        except Exception:
            pass
        await update.message.reply_text("✅ Replied.", reply_markup=back_menu_kb(bot_kind))

    # ---------- Connect My Bot (master only) ----------
    async def connect_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        if bot_kind != "master":
            await update.callback_query.message.reply_text("This is only available in Main Shop.", reply_markup=back_menu_kb(bot_kind))
            return
        uid = update.effective_user.id
        ensure_seller(uid)
        s = get_shop_settings(SUPER_ADMIN_ID)
        desc = (s["connect_desc"] or "").strip()
        bal = get_balance(SUPER_ADMIN_ID, uid)
        cur_plan = seller_plan(uid)
        days_left = seller_days_left(uid)
        txt = f"{desc}\n\nYour Main Shop balance: <b>{money(bal)} {esc(CURRENCY)}</b>\nYour plan: <b>{esc(cur_plan)}</b>\nDays left: <b>{days_left}</b>"
        rows = []
        if cur_plan != "whitelabel":
            rows.append([InlineKeyboardButton(f"Plan A — {money(PLAN_A_PRICE)} {CURRENCY}", callback_data="c:plan:a")])
        rows.append([InlineKeyboardButton(f"Plan B — {money(PLAN_B_PRICE)} {CURRENCY}", callback_data="c:plan:b")])
        rows.append([InlineKeyboardButton("⬅️ Menu", callback_data="m:menu")])
        await update.callback_query.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    async def choose_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        if bot_kind != "master":
            return
        plan = update.callback_query.data.split(":")[2]  # a/b
        uid = update.effective_user.id
        ensure_seller(uid)
        cur_plan = seller_plan(uid)

        if cur_plan == "whitelabel" and plan == "a":
            await update.callback_query.message.reply_text("❌ White‑Label cannot pay $5. Please choose Plan B.", reply_markup=back_menu_kb(bot_kind))
            return

        price = PLAN_A_PRICE if plan == "a" else PLAN_B_PRICE
        bal = get_balance(SUPER_ADMIN_ID, uid)
        if bal < price:
            await update.callback_query.message.reply_text("❌ Not enough Main Shop balance. Deposit first.", reply_markup=back_menu_kb(bot_kind))
            return

        # deduct master wallet
        set_balance(SUPER_ADMIN_ID, uid, bal - price)

        note_plan = ""
        # Apply plan rules:
        if plan == "b":
            seller_set_plan(uid, "whitelabel")
            note_plan = "White‑Label"
        else:
            # Plan A ($5):
            # - If currently branded and ACTIVE => upgrade to whitelabel
            # - Else => set branded (first purchase)
            if cur_plan == "branded" and seller_active(uid):
                seller_set_plan(uid, "whitelabel")
                note_plan = "White‑Label (upgrade via $5)"
            else:
                seller_set_plan(uid, "branded")
                note_plan = "Branded"

        seller_add_days(uid, PLAN_DAYS)
        log_tx(SUPER_ADMIN_ID, uid, "plan", -price, note_plan, 1)

        set_state(context, "await_token", {"seller_id": uid})
        await update.callback_query.message.reply_text(
            f"✅ Plan activated: <b>{esc(note_plan)}</b>\nNow send your <b>BotFather token</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_menu_kb(bot_kind)
        )

    async def token_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state, data = get_state(context)
        if state != "await_token":
            return
        if bot_kind != "master":
            return
        uid = update.effective_user.id
        seller_id = int(data["seller_id"])
        if uid != seller_id:
            return
        token = (update.message.text or "").strip()

        # validate token
        try:
            tmp = Application.builder().token(token).build()
            await tmp.initialize()
            me = await tmp.bot.get_me()
            await tmp.shutdown()
            bot_username = me.username or ""
        except Exception:
            await update.message.reply_text("❌ Invalid token. Try again.")
            return

        upsert_seller_bot(seller_id, token, bot_username)
        ensure_shop_settings(seller_id)
        clear_state(context)

        # start seller bot only if seller is active and not banned
        if seller_active(seller_id) and int(seller_row(seller_id)["banned_shop"] or 0) == 0:
            await MANAGER.start_seller_bot(seller_id, token)

        await update.message.reply_text(f"✅ Connected!\nYour bot is running: @{bot_username}\nOpen it and press /start.")

    # ---------- Extend Subscription ----------
    async def extend_in_seller(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        if bot_kind != "seller":
            return
        uid = update.effective_user.id
        if uid != shop_owner_id and not is_super(uid):
            await update.callback_query.message.reply_text("❌ Only the seller owner can use this.", reply_markup=back_menu_kb(bot_kind))
            return
        days_left = seller_days_left(shop_owner_id)
        status = "✅ Active" if days_left > 0 else "❌ Ended"
        plan = seller_plan(shop_owner_id)
        txt = f"⏳ <b>Subscription</b>\nStatus: <b>{status}</b>\nDays left: <b>{days_left}</b>\nPlan: <b>{esc(plan)}</b>\n\nRenew in Main Shop."
        if not MASTER_BOT_USERNAME:
            await update.callback_query.message.reply_text(txt + "\n\n⚠️ Set env MASTER_BOT_USERNAME to show the renew button.", parse_mode=ParseMode.HTML, reply_markup=back_menu_kb(bot_kind))
            return
        url = f"https://t.me/{MASTER_BOT_USERNAME}?start=extend"
        await update.callback_query.message.reply_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("🏬 Open Main Shop (Renew)", url=url)],
                             [InlineKeyboardButton("⬅️ Menu", callback_data="m:menu")]])
        )

    async def show_extend_master(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
        ensure_seller(uid)
        cur_plan = seller_plan(uid)
        days_left = seller_days_left(uid)
        bal = get_balance(SUPER_ADMIN_ID, uid)
        txt = f"⏳ <b>Extend Subscription</b>\n\nDays left: <b>{days_left}</b>\nCurrent plan: <b>{esc(cur_plan)}</b>\nMain Shop balance: <b>{money(bal)} {esc(CURRENCY)}</b>\n\nChoose:"
        rows = []
        if cur_plan != "whitelabel":
            rows.append([InlineKeyboardButton(f"Pay {money(PLAN_A_PRICE)} {CURRENCY} (Plan A)", callback_data="e:plan:a")])
        rows.append([InlineKeyboardButton(f"Pay {money(PLAN_B_PRICE)} {CURRENCY} (Plan B)", callback_data="e:plan:b")])
        rows.append([InlineKeyboardButton("🏠 Menu", callback_data="m:menu")])
        await update.effective_chat.send_message(txt, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    async def extend_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        if bot_kind != "master":
            return
        uid = update.effective_user.id
        plan = update.callback_query.data.split(":")[2]
        ensure_seller(uid)
        cur_plan = seller_plan(uid)
        if cur_plan == "whitelabel" and plan == "a":
            await update.callback_query.message.reply_text("❌ White‑Label cannot pay $5. Please choose Plan B.", reply_markup=back_menu_kb(bot_kind))
            return
        price = PLAN_A_PRICE if plan == "a" else PLAN_B_PRICE
        bal = get_balance(SUPER_ADMIN_ID, uid)
        if bal < price:
            await update.callback_query.message.reply_text("❌ Not enough Main Shop balance. Deposit first.", reply_markup=back_menu_kb(bot_kind))
            return

        set_balance(SUPER_ADMIN_ID, uid, bal - price)

        # Extend logic:
        # - Plan B => keep/set whitelabel
        # - Plan A => if whitelabel -> blocked already; else upgrade to whitelabel (your rule for renewal $5 upgrade)
        if plan == "b":
            seller_set_plan(uid, "whitelabel")
            note = "White‑Label"
        else:
            seller_set_plan(uid, "whitelabel")
            note = "White‑Label (upgrade via $5)"

        seller_add_days(uid, PLAN_DAYS)
        log_tx(SUPER_ADMIN_ID, uid, "plan", -price, note, 1)
        await update.callback_query.message.reply_text(f"✅ Renewed.\nPlan: {note}\nDays left: {seller_days_left(uid)}", reply_markup=back_menu_kb(bot_kind))

        # restart seller bot if connected and enabled and active
        sb = get_seller_bot(uid)
        if sb and int(sb["enabled"] or 0) == 1 and seller_active(uid):
            try:
                await MANAGER.start_seller_bot(uid, sb["bot_token"])
            except Exception:
                pass

    # ---------- Admin Panel ----------
    def admin_panel_kb(sid: int) -> InlineKeyboardMarkup:
        return grid([
            InlineKeyboardButton("👥 Users List", callback_data=f"a:users:{sid}"),
            InlineKeyboardButton("📢 Broadcast", callback_data=f"a:bcast:{sid}"),
            InlineKeyboardButton("🖼 Edit Welcome", callback_data=f"a:welcome:{sid}"),
            InlineKeyboardButton("💳 Edit Wallet", callback_data=f"a:wallet:{sid}"),
            InlineKeyboardButton("🧩 Manage Products", callback_data=f"a:manage:{sid}"),
            InlineKeyboardButton("⬅️ Menu", callback_data="m:menu"),
        ], 2)

    def can_use_admin(uid: int) -> bool:
        if bot_kind == "seller":
            # seller owner or super admin
            r = seller_row(shop_owner_id)
            if r and int(r["banned_panel"] or 0) == 1 and not is_super(uid):
                return False
            return uid == shop_owner_id or is_super(uid)
        return is_super(uid)

    async def admin_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        uid = update.effective_user.id
        if not can_use_admin(uid):
            await update.callback_query.message.reply_text("❌ Not allowed.", reply_markup=back_menu_kb(bot_kind))
            return
        sid = current_shop_id()
        await update.callback_query.message.reply_text("🛠 <b>Admin Panel</b>", parse_mode=ParseMode.HTML, reply_markup=admin_panel_kb(sid))

    # Users list + search
    async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s = update.callback_query.data.split(":")
        sid = int(sid_s)
        conn = db(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM balances WHERE shop_owner_id=? ORDER BY rowid DESC LIMIT 60", (sid,))
        ids = [int(r["user_id"]) for r in cur.fetchall()]
        conn.close()
        ids = [i for i in ids if i != sid]
        rows = [[InlineKeyboardButton(user_display(i), callback_data=f"u:open:{sid}:{i}")] for i in ids[:30]]
        rows.append([InlineKeyboardButton("🔍 Search", callback_data=f"u:search:{sid}"), InlineKeyboardButton("⬅️ Admin", callback_data="m:admin")])
        await update.callback_query.message.reply_text("Select a user:", reply_markup=kb(rows))

    async def admin_user_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s = update.callback_query.data.split(":")
        sid = int(sid_s)
        set_state(context, "user_search", {"shop_id": sid})
        await update.callback_query.message.reply_text("Type username to search (example: rekko):", reply_markup=admin_panel_kb(sid))

    async def admin_user_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state, data = get_state(context)
        if state != "user_search":
            return
        sid = int(data["shop_id"])
        q = (update.message.text or "").strip().lstrip("@").lower()
        clear_state(context)
        conn = db(); cur = conn.cursor()
        cur.execute("""
            SELECT u.user_id FROM users u
            JOIN balances b ON b.user_id=u.user_id AND b.shop_owner_id=?
            WHERE lower(u.username) LIKE ?
            LIMIT 30
        """, (sid, f"%{q}%"))
        ids = [int(r["user_id"]) for r in cur.fetchall()]
        conn.close()
        if not ids:
            await update.message.reply_text("No matches.", reply_markup=admin_panel_kb(sid))
            return
        rows = [[InlineKeyboardButton(user_display(i), callback_data=f"u:open:{sid}:{i}")] for i in ids]
        rows.append([InlineKeyboardButton("⬅️ Admin", callback_data="m:admin")])
        await update.message.reply_text("Matches:", reply_markup=kb(rows))

    async def admin_user_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s, uid_s = update.callback_query.data.split(":")
        sid = int(sid_s); target = int(uid_s)
        bal = get_balance(sid, target)
        txt = f"👤 <b>User</b>: {esc(user_display(target))}\nBalance: <b>{money(bal)} {esc(CURRENCY)}</b>"
        await update.callback_query.message.reply_text(
            txt, parse_mode=ParseMode.HTML,
            reply_markup=kb([
                [InlineKeyboardButton("➕ Add", callback_data=f"u:add:{sid}:{target}"),
                 InlineKeyboardButton("➖ Deduct", callback_data=f"u:ded:{sid}:{target}")],
                [InlineKeyboardButton("🚫 Ban", callback_data=f"u:ban:{sid}:{target}"),
                 InlineKeyboardButton("✅ Unban", callback_data=f"u:unban:{sid}:{target}")],
                [InlineKeyboardButton("⏳ 7d", callback_data=f"u:res:{sid}:{target}:7"),
                 InlineKeyboardButton("⏳ 14d", callback_data=f"u:res:{sid}:{target}:14"),
                 InlineKeyboardButton("⏳ 30d", callback_data=f"u:res:{sid}:{target}:30")],
                [InlineKeyboardButton("↩️ Reply Support", callback_data=f"a:reply:{target}:{sid}")],
                [InlineKeyboardButton("⬅️ Users", callback_data=f"a:users:{sid}")]
            ])
        )

    async def admin_edit_amount_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, sid: int, target: int):
        set_state(context, "edit_balance", {"shop_id": sid, "target": target, "mode": mode})
        await update.callback_query.message.reply_text("Send amount (number):", reply_markup=admin_panel_kb(sid))

    async def admin_user_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s, target_s = update.callback_query.data.split(":")
        await admin_edit_amount_prompt(update, context, "add", int(sid_s), int(target_s))

    async def admin_user_ded(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s, target_s = update.callback_query.data.split(":")
        await admin_edit_amount_prompt(update, context, "ded", int(sid_s), int(target_s))

    async def admin_user_edit_balance_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state, data = get_state(context)
        if state != "edit_balance":
            return
        amt = parse_float(update.message.text or "")
        if amt is None or amt <= 0:
            await update.message.reply_text("❌ Invalid number.")
            return
        sid = int(data["shop_id"]); target = int(data["target"]); mode = data["mode"]
        clear_state(context)
        if mode == "add":
            add_balance(sid, target, amt)
            log_tx(sid, target, "balance_edit", amt, "")
        else:
            add_balance(sid, target, -amt)
            log_tx(sid, target, "balance_edit", -amt, "")
        await update.message.reply_text(f"✅ Updated.\nNew Balance: {money(get_balance(sid, target))} {CURRENCY}", reply_markup=admin_panel_kb(sid))

    async def admin_user_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s, target_s = update.callback_query.data.split(":")
        ban_user(int(sid_s), int(target_s), 1)
        await update.callback_query.message.reply_text("✅ Banned.", reply_markup=back_menu_kb(bot_kind))

    async def admin_user_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s, target_s = update.callback_query.data.split(":")
        ban_user(int(sid_s), int(target_s), 0)
        await update.callback_query.message.reply_text("✅ Unbanned.", reply_markup=back_menu_kb(bot_kind))

    async def admin_user_restrict(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s, target_s, days_s = update.callback_query.data.split(":")
        restrict_user(int(sid_s), int(target_s), int(days_s))
        await update.callback_query.message.reply_text("✅ Restricted.", reply_markup=back_menu_kb(bot_kind))

    # Broadcast
    async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s = update.callback_query.data.split(":")
        sid = int(sid_s)
        set_state(context, "broadcast", {"shop_id": sid, "file_id": "", "file_type": "", "text": ""})
        await update.callback_query.message.reply_text(
            "📢 Send broadcast now (TEXT, or PHOTO/VIDEO with caption). Then press DONE.",
            reply_markup=kb([[InlineKeyboardButton("✅ DONE", callback_data="b:done")],
                             [InlineKeyboardButton("❌ Cancel", callback_data="b:cancel")]])
        )

    async def broadcast_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state, data = get_state(context)
        if state != "broadcast":
            return
        msg = update.message
        if msg.photo:
            data["file_id"] = msg.photo[-1].file_id
            data["file_type"] = "photo"
            data["text"] = (msg.caption or "").strip()
        elif msg.video:
            data["file_id"] = msg.video.file_id
            data["file_type"] = "video"
            data["text"] = (msg.caption or "").strip()
        else:
            data["text"] = (msg.text or "").strip()
        set_state(context, "broadcast", data)
        await msg.reply_text("✅ Saved. Press DONE to send.", reply_markup=kb([[InlineKeyboardButton("✅ DONE", callback_data="b:done")]]))

    async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        clear_state(context)
        await update.callback_query.message.reply_text("✅ Broadcast cancelled.", reply_markup=back_menu_kb(bot_kind))

    async def broadcast_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        state, data = get_state(context)
        if state != "broadcast":
            await update.callback_query.message.reply_text("No broadcast prepared.", reply_markup=back_menu_kb(bot_kind))
            return
        sid = int(data["shop_id"])
        file_id = (data.get("file_id") or "").strip()
        ftype = (data.get("file_type") or "").strip()
        text = (data.get("text") or "").strip()
        clear_state(context)

        recipients = list_shop_user_ids(sid)
        status_msg = await update.callback_query.message.reply_text(f"📢 Broadcasting to <b>{len(recipients)}</b> users…", parse_mode=ParseMode.HTML)
        sent = 0; failed = 0
        for uid in recipients:
            try:
                if file_id and ftype == "photo":
                    await context.bot.send_photo(uid, photo=file_id, caption=text or None)
                elif file_id and ftype == "video":
                    await context.bot.send_video(uid, video=file_id, caption=text or None)
                else:
                    await context.bot.send_message(uid, text or " ")
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.03)
        try:
            await status_msg.edit_text(f"✅ Broadcast done.\nSent: <b>{sent}</b>\nFailed: <b>{failed}</b>", parse_mode=ParseMode.HTML)
        except Exception:
            pass

    # Edit welcome / wallet
    async def edit_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s = update.callback_query.data.split(":")
        sid = int(sid_s)
        set_state(context, "edit_welcome", {"shop_id": sid})
        await update.callback_query.message.reply_text("Send welcome TEXT, or PHOTO/VIDEO with caption.", reply_markup=admin_panel_kb(sid))

    async def edit_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s = update.callback_query.data.split(":")
        sid = int(sid_s)
        set_state(context, "edit_wallet", {"shop_id": sid})
        await update.callback_query.message.reply_text("Send new wallet message text:", reply_markup=admin_panel_kb(sid))

    async def manage_root(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s = update.callback_query.data.split(":")
        sid = int(sid_s)
        cats = list_categories(sid)
        rows = [[InlineKeyboardButton("➕ Add Category", callback_data=f"mg:addcat:{sid}")]]
        for c in cats[:25]:
            rows.append([InlineKeyboardButton(f"📁 {c['name']}", callback_data=f"mg:cat:{sid}:{c['id']}")])
        rows.append([InlineKeyboardButton("⬅️ Admin", callback_data="m:admin")])
        await update.callback_query.message.reply_text("🧩 Manage Products:", reply_markup=kb(rows))

    # Manage flows (simple prompts)
    async def mg_addcat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s = update.callback_query.data.split(":")
        sid = int(sid_s)
        set_state(context, "mg_addcat", {"shop_id": sid})
        await update.callback_query.message.reply_text("Send category name:", reply_markup=admin_panel_kb(sid))

    async def mg_cat_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s, cat_s = update.callback_query.data.split(":")
        sid = int(sid_s); cat_id = int(cat_s)
        subs = list_cocats(sid, cat_id)
        rows = [
            [InlineKeyboardButton("➕ Add Sub-Category", callback_data=f"mg:addsub:{sid}:{cat_id}")],
            [InlineKeyboardButton("✏️ Edit Category", callback_data=f"mg:editcat:{sid}:{cat_id}")],
            [InlineKeyboardButton("🗑 Delete Category", callback_data=f"mg:delcat:{sid}:{cat_id}")],
        ]
        for sc in subs[:25]:
            rows.append([InlineKeyboardButton(f"📂 {sc['name']}", callback_data=f"mg:sub:{sid}:{cat_id}:{sc['id']}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"a:manage:{sid}")])
        await update.callback_query.message.reply_text("Category menu:", reply_markup=kb(rows))

    async def mg_sub_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s, cat_s, sub_s = update.callback_query.data.split(":")
        sid = int(sid_s); cat_id = int(cat_s); sub_id = int(sub_s)
        prods = list_products(sid, cat_id, sub_id)
        rows = [
            [InlineKeyboardButton("➕ Add Product", callback_data=f"mg:addprod:{sid}:{cat_id}:{sub_id}")],
            [InlineKeyboardButton("✏️ Edit Sub-Category", callback_data=f"mg:editsub:{sid}:{sub_id}")],
            [InlineKeyboardButton("🗑 Delete Sub-Category", callback_data=f"mg:delsub:{sid}:{sub_id}:{cat_id}")],
        ]
        for p in prods[:25]:
            rows.append([InlineKeyboardButton(f"🛒 {p['name']}", callback_data=f"mg:prod:{sid}:{p['id']}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"mg:cat:{sid}:{cat_id}")])
        await update.callback_query.message.reply_text("Sub-Category menu:", reply_markup=kb(rows))

    async def mg_prod_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        _, _, sid_s, pid_s = update.callback_query.data.split(":")
        sid = int(sid_s); pid = int(pid_s)
        p = get_product(sid, pid)
        if not p:
            await update.callback_query.message.reply_text("Product not found.")
            return
        st = stock_count(sid, pid)
        rows = [
            [InlineKeyboardButton("✏️ Edit Name/Price", callback_data=f"mg:editprod:{sid}:{pid}")],
            [InlineKeyboardButton("📝 Edit Description", callback_data=f"mg:desc:{sid}:{pid}")],
            [InlineKeyboardButton("🖼 Set Media", callback_data=f"mg:media:{sid}:{pid}")],
            [InlineKeyboardButton("🔗 Set Private Link", callback_data=f"mg:link:{sid}:{pid}")],
            [InlineKeyboardButton(f"🔑 Add Keys (stock {st})", callback_data=f"mg:keys:{sid}:{pid}")],
            [InlineKeyboardButton("🗑 Delete Product", callback_data=f"mg:delprod:{sid}:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"mg:sub:{sid}:{p['category_id']}:{p['cocategory_id']}")],
        ]
        await update.callback_query.message.reply_text("Product manager:", reply_markup=kb(rows))

    # ---------- SUPER ADMIN BUTTON ----------
    async def super_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        if not is_super(update.effective_user.id):
            await update.callback_query.message.reply_text("❌ Not allowed.")
            return
        rows = [
            [InlineKeyboardButton("👥 Sellers List", callback_data="sa:sellers")],
            [InlineKeyboardButton("⬅️ Menu", callback_data="m:menu")]
        ]
        await update.callback_query.message.reply_text("👑 <b>Super Admin</b>", parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    async def super_sellers(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sellers = list_sellers()
        rows = [[InlineKeyboardButton(user_display(int(s["seller_id"])), callback_data=f"sa:sel:{int(s['seller_id'])}")]
                for s in sellers[:40]]
        rows.append([InlineKeyboardButton("🔍 Search", callback_data="sa:search"), InlineKeyboardButton("⬅️ Back", callback_data="m:super")])
        await update.callback_query.message.reply_text("Sellers:", reply_markup=kb(rows))

    async def super_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        set_state(context, "super_search", {})
        await update.callback_query.message.reply_text("Type seller username to search:", reply_markup=kb([[InlineKeyboardButton("⬅️ Cancel", callback_data="m:super")]]))

    async def super_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state, _ = get_state(context)
        if state != "super_search":
            return
        q = (update.message.text or "").strip().lstrip("@").lower()
        clear_state(context)
        sellers = list_sellers()
        matched = []
        for s in sellers:
            sid = int(s["seller_id"])
            u = user_row(sid)
            if u and (u["username"] or "").lower().find(q) != -1:
                matched.append(sid)
        if not matched:
            await update.message.reply_text("No matches.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="m:super")]]))
            return
        rows = [[InlineKeyboardButton(user_display(sid), callback_data=f"sa:sel:{sid}")] for sid in matched[:40]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="m:super")])
        await update.message.reply_text("Matches:", reply_markup=kb(rows))

    async def super_seller_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        sid = int(update.callback_query.data.split(":")[2])
        r = seller_row(sid)
        if not r:
            await update.callback_query.message.reply_text("Seller not found.")
            return
        days = seller_days_left(sid)
        plan = seller_plan(sid)
        txt = f"👤 Seller: <b>{esc(user_display(sid))}</b>\nPlan: <b>{esc(plan)}</b>\nDays left: <b>{days}</b>"
        rows = [
            [InlineKeyboardButton("🚫 Ban Shop", callback_data=f"sa:ban:{sid}"), InlineKeyboardButton("✅ Unban Shop", callback_data=f"sa:unban:{sid}")],
            [InlineKeyboardButton("🛑 Ban Panel", callback_data=f"sa:banp:{sid}"), InlineKeyboardButton("✅ Unban Panel", callback_data=f"sa:unbanp:{sid}")],
            [InlineKeyboardButton("⏳ Restrict 7d", callback_data=f"sa:res:{sid}:7"),
             InlineKeyboardButton("⏳ 30d", callback_data=f"sa:res:{sid}:30")],
            [InlineKeyboardButton("💰 Edit Seller Balance", callback_data=f"sa:bal:{sid}")],
            [InlineKeyboardButton("🔔 Warn Expiring", callback_data=f"sa:warn:{sid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="sa:sellers")]
        ]
        await update.callback_query.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    async def super_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        parts = update.callback_query.data.split(":")
        act = parts[1]
        sid = int(parts[2])
        if act == "ban":
            super_set_seller_flag(sid, "banned_shop", 1)
            # stop bot
            try:
                disable_seller_bot(sid)
                await MANAGER.stop_seller_bot(sid)
            except Exception:
                pass
            await update.callback_query.message.reply_text("✅ Seller shop banned.")
        elif act == "unban":
            super_set_seller_flag(sid, "banned_shop", 0)
            # restart if active and connected
            sb = get_seller_bot(sid)
            if sb and seller_active(sid) and int(sb["enabled"] or 0) == 1:
                try:
                    await MANAGER.start_seller_bot(sid, sb["bot_token"])
                except Exception:
                    pass
            await update.callback_query.message.reply_text("✅ Seller shop unbanned.")
        elif act == "banp":
            super_set_seller_flag(sid, "banned_panel", 1)
            await update.callback_query.message.reply_text("✅ Seller panel banned.")
        elif act == "unbanp":
            super_set_seller_flag(sid, "banned_panel", 0)
            await update.callback_query.message.reply_text("✅ Seller panel unbanned.")
        elif act == "res":
            days = int(parts[3])
            super_restrict_seller(sid, days)
            try:
                disable_seller_bot(sid)
                await MANAGER.stop_seller_bot(sid)
            except Exception:
                pass
            await update.callback_query.message.reply_text(f"✅ Restricted for {days} days.")
        elif act == "bal":
            set_state(context, "super_edit_balance", {"seller_id": sid})
            await update.callback_query.message.reply_text("Send amount (+add or -deduct), example: +10 or -5")
        elif act == "warn":
            try:
                await context.bot.send_message(chat_id=sid, text="⏳ Your subscription is ending soon. Please renew in Main Shop.")
            except Exception:
                pass
            await update.callback_query.message.reply_text("✅ Warning sent.")

    async def super_edit_balance_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state, data = get_state(context)
        if state != "super_edit_balance":
            return
        sid = int(data["seller_id"])
        t = (update.message.text or "").strip().replace(" ", "")
        clear_state(context)
        m = re.fullmatch(r"([+-])(\d+(?:\.\d+)?)", t)
        if not m:
            await update.message.reply_text("❌ Invalid. Example: +10 or -5")
            return
        sign = m.group(1)
        amt = float(m.group(2))
        # seller balance means seller's balance in MASTER shop wallet (shop_owner_id=SUPER_ADMIN_ID)
        if sign == "+":
            add_balance(SUPER_ADMIN_ID, sid, amt)
            log_tx(SUPER_ADMIN_ID, sid, "balance_edit", amt, "Super admin")
        else:
            add_balance(SUPER_ADMIN_ID, sid, -amt)
            log_tx(SUPER_ADMIN_ID, sid, "balance_edit", -amt, "Super admin")
        await update.message.reply_text(f"✅ Updated seller balance. New: {money(get_balance(SUPER_ADMIN_ID, sid))} {CURRENCY}")

    # ---------- message handler for states (deposit/support/admin edits/broadcast/manage) ----------
    async def text_or_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
        upsert_user(update.effective_user)
        state, data = get_state(context)

        # deposit
        if state == "deposit_amount":
            await deposit_amount_msg(update, context)
            return
        if state == "deposit_proof":
            await deposit_proof_msg(update, context)
            return

        # support draft
        if state == "support_draft":
            await support_collect(update, context)
            return

        # admin reply
        if state == "admin_reply":
            await admin_reply_text(update, context)
            return

        # token
        if state == "await_token":
            await token_text(update, context)
            return

        # admin user search
        if state == "user_search":
            await admin_user_search_text(update, context)
            return

        # admin edit balance
        if state == "edit_balance":
            await admin_user_edit_balance_text(update, context)
            return

        # broadcast
        if state == "broadcast":
            await broadcast_collect(update, context)
            return

        # edit welcome
        if state == "edit_welcome":
            sid = int(data["shop_id"])
            msg = update.message
            if msg.photo:
                set_shop_setting(sid, "welcome_file_id", msg.photo[-1].file_id)
                set_shop_setting(sid, "welcome_file_type", "photo")
                set_shop_setting(sid, "welcome_text", msg.caption or "")
            elif msg.video:
                set_shop_setting(sid, "welcome_file_id", msg.video.file_id)
                set_shop_setting(sid, "welcome_file_type", "video")
                set_shop_setting(sid, "welcome_text", msg.caption or "")
            else:
                set_shop_setting(sid, "welcome_file_id", "")
                set_shop_setting(sid, "welcome_file_type", "")
                set_shop_setting(sid, "welcome_text", msg.text or "")
            clear_state(context)
            await msg.reply_text("✅ Welcome updated.", reply_markup=admin_panel_kb(sid))
            return

        # edit wallet
        if state == "edit_wallet":
            sid = int(data["shop_id"])
            set_shop_setting(sid, "wallet_message", update.message.text or "")
            clear_state(context)
            await update.message.reply_text("✅ Wallet message updated.", reply_markup=admin_panel_kb(sid))
            return

        # super admin seller balance
        if state == "super_edit_balance":
            await super_edit_balance_text(update, context)
            return

        # super admin search
        if state == "super_search":
            await super_search_text(update, context)
            return

        # manage prompts (minimal but functional)
        if state == "mg_addcat":
            sid = int(data["shop_id"])
            name = (update.message.text or "").strip()
            if not name:
                return
            conn = db(); cur = conn.cursor()
            cur.execute("INSERT INTO categories(shop_owner_id,name) VALUES(?,?)", (sid, name))
            conn.commit(); conn.close()
            clear_state(context)
            await update.message.reply_text("✅ Category added.", reply_markup=admin_panel_kb(sid))
            return

        # fallback
        await update.message.reply_text("Use the buttons in the menu. Type /start to reopen.")

    # ---------- main callback router ----------
    async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        data = q.data
        uid = update.effective_user.id

        # general menu
        if data == "m:menu":
            await menu_cb(update, context); return

        if data == "m:products":
            await products_root(update, context); return
        if data.startswith("p:cat:"):
            await products_cat(update, context); return
        if data.startswith("p:sub:"):
            await products_sub(update, context); return
        if data.startswith("p:prod:"):
            await product_view(update, context); return
        if data.startswith("p:q:"):
            await product_qty(update, context); return
        if data.startswith("p:buy:"):
            await product_buy(update, context); return
        if data.startswith("p:file:"):
            await product_file(update, context); return

        if data == "m:wallet":
            await wallet(update, context); return
        if data == "w:deposit":
            await deposit_start(update, context); return

        if data.startswith("d:"):
            await deposit_decision(update, context); return

        if data == "m:history":
            await history(update, context); return

        if data == "m:support":
            await support_start(update, context); return
        if data == "s:done":
            await support_done(update, context); return

        if data.startswith("a:reply:"):
            await admin_reply_start(update, context); return

        # connect / plan
        if data == "m:connect":
            await connect_screen(update, context); return
        if data.startswith("c:plan:"):
            await choose_plan(update, context); return

        # extend
        if data == "m:extend":
            await extend_in_seller(update, context); return
        if data.startswith("e:plan:"):
            await extend_choose(update, context); return

        # admin panel
        if data == "m:admin":
            await admin_open(update, context); return

        if data.startswith("a:users:"):
            await admin_users(update, context); return
        if data.startswith("u:search:"):
            await admin_user_search(update, context); return
        if data.startswith("u:open:"):
            await admin_user_open(update, context); return
        if data.startswith("u:add:"):
            await admin_user_add(update, context); return
        if data.startswith("u:ded:"):
            await admin_user_ded(update, context); return
        if data.startswith("u:ban:"):
            await admin_user_ban(update, context); return
        if data.startswith("u:unban:"):
            await admin_user_unban(update, context); return
        if data.startswith("u:res:"):
            await admin_user_restrict(update, context); return

        if data.startswith("a:bcast:"):
            await broadcast_start(update, context); return
        if data == "b:done":
            await broadcast_done(update, context); return
        if data == "b:cancel":
            await broadcast_cancel(update, context); return

        if data.startswith("a:welcome:"):
            await edit_welcome(update, context); return
        if data.startswith("a:wallet:"):
            await edit_wallet(update, context); return
        if data.startswith("a:manage:"):
            await manage_root(update, context); return

        # manage minimal routes
        if data.startswith("mg:addcat:"):
            await mg_addcat(update, context); return
        if data.startswith("mg:cat:"):
            await mg_cat_open(update, context); return
        if data.startswith("mg:sub:"):
            await mg_sub_open(update, context); return
        if data.startswith("mg:prod:"):
            await mg_prod_open(update, context); return

        # super admin
        if data == "m:super":
            await super_open(update, context); return
        if data == "sa:sellers":
            await super_sellers(update, context); return
        if data == "sa:search":
            await super_search(update, context); return
        if data.startswith("sa:sel:"):
            await super_seller_open(update, context); return
        if data.startswith("sa:"):
            await super_action(update, context); return

        await q.answer()

    # register
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO, text_or_media))

# ---------------- MAIN ----------------
async def main():
    init_db()
    # start seller bots that are enabled and active
    for r in list_enabled_seller_bots():
        sid = int(r["seller_id"])
        if seller_active(sid) and int(seller_row(sid)["banned_shop"] or 0) == 0:
            try:
                await MANAGER.start_seller_bot(sid, r["bot_token"])
            except Exception:
                log.exception("Failed to start seller bot %s", sid)

    # start master app
    master = Application.builder().token(BOT_TOKEN).build()
    register_handlers(master, shop_owner_id=SUPER_ADMIN_ID, bot_kind="master")

    await master.initialize()
    await master.start()
    asyncio.create_task(master.updater.start_polling(drop_pending_updates=True))
    asyncio.create_task(watchdog())
    log.info("Master bot started.")

    # keep alive forever
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
