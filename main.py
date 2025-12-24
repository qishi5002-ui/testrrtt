# main.py — AutoPanel (Master Shop + Connected Seller Bots) — FULL FEATURES
# python-telegram-bot==20.8  |  SQLite  |  Railway-ready

import os
import time
import re
import secrets
import asyncio
import sqlite3
import logging
from typing import Optional, Dict, Any, List, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# ENV VARS (Railway Variables)
# =========================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
SUPER_ADMIN_ID = int((os.getenv("SUPER_ADMIN_ID") or os.getenv("ADMIN_ID") or "0").strip() or "0")

DB_FILE = (os.getenv("DB_FILE") or "data.db").strip()
STORE_NAME = (os.getenv("STORE_NAME") or "AutoPanel").strip()
CURRENCY = (os.getenv("CURRENCY") or "USDT").strip()

PLAN_A_PRICE = float((os.getenv("PLAN_A_PRICE") or "5").strip() or "5")   # Branded
PLAN_B_PRICE = float((os.getenv("PLAN_B_PRICE") or "10").strip() or "10") # White-label
PLAN_DAYS = int((os.getenv("PLAN_DAYS") or "30").strip() or "30")

MASTER_BOT_USERNAME = (os.getenv("MASTER_BOT_USERNAME") or "").strip().lstrip("@")

BRAND_LINE = "Bot made by @RekkoOwn"
MAIN_CREATED_LINE = "Bot created by @RekkoOwn"
PUBLIC_GROUP_LINE = "Group Chat : @AutoPanels"

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN")
if SUPER_ADMIN_ID <= 0:
    raise RuntimeError("Missing SUPER_ADMIN_ID / ADMIN_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("autopanel")

# =========================
# UTIL
# =========================
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

async def safe_delete(bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass

def parse_float(text: str) -> Optional[float]:
    try:
        return float(text.strip().replace(",", ""))
    except Exception:
        return None

def is_super(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID

def gen_order_id() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "OD" + "".join(secrets.choice(alphabet) for _ in range(10))

def parse_channel_username(link: str) -> Optional[str]:
    link = (link or "").strip()
    if not link:
        return None
    m = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]{5,})", link)
    if m:
        return m.group(1)
    if link.startswith("@") and len(link) > 2:
        return link[1:]
    return None

# =========================
# DB
# =========================
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()

    # Users table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        last_name TEXT DEFAULT '',
        last_seen INTEGER DEFAULT 0
    )""")

    # Sellers + seller bots
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sellers(
        seller_id INTEGER PRIMARY KEY,
        sub_until INTEGER DEFAULT 0,
        plan TEXT DEFAULT 'branded', -- branded/whitelabel
        banned_shop INTEGER DEFAULT 0,
        banned_panel INTEGER DEFAULT 0,
        restricted_until INTEGER DEFAULT 0
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS seller_bots(
        seller_id INTEGER PRIMARY KEY,
        bot_token TEXT NOT NULL,
        bot_username TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")

    # Shop settings
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shop_settings(
        shop_owner_id INTEGER PRIMARY KEY,
        wallet_message TEXT DEFAULT '',
        welcome_text TEXT DEFAULT '',
        welcome_file_id TEXT DEFAULT '',
        welcome_file_type TEXT DEFAULT '', -- photo/video
        connect_desc TEXT DEFAULT '',
        referral_percent REAL DEFAULT 0
    )""")

    # Multiple deposit methods
    cur.execute("""
    CREATE TABLE IF NOT EXISTS wallet_methods(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        instructions TEXT NOT NULL,
        qr_file_id TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1
    )""")

    # Balances (per shop)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS balances(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        balance REAL DEFAULT 0,
        PRIMARY KEY(shop_owner_id, user_id)
    )""")

    # User bans per shop
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_bans(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        banned INTEGER DEFAULT 0,
        restricted_until INTEGER DEFAULT 0,
        PRIMARY KEY(shop_owner_id, user_id)
    )""")

    # Catalog
    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        file_type TEXT DEFAULT ''
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cocategories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        category_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        file_type TEXT DEFAULT ''
    )""")

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
        tg_link TEXT DEFAULT '' -- PUBLIC channel link for join-gate
    )""")

    # Keys (1 line = 1 stock)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_keys(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        key_line TEXT NOT NULL,
        delivered_once INTEGER DEFAULT 0,
        delivered_to INTEGER DEFAULT 0,
        delivered_at INTEGER DEFAULT 0
    )""")

    # Orders + exact delivered keys
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        order_id TEXT NOT NULL,
        product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        qty INTEGER NOT NULL,
        total REAL NOT NULL,
        created_at INTEGER NOT NULL
    )""")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_unique ON orders(shop_owner_id, order_id)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS order_keys(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        order_id TEXT NOT NULL,
        key_line TEXT NOT NULL
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_order_keys_lookup ON order_keys(shop_owner_id, order_id)")

    # Deposits (photo proof required)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposit_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        method_id INTEGER DEFAULT 0,
        proof_file_id TEXT NOT NULL,
        status TEXT NOT NULL, -- pending/approved/rejected
        created_at INTEGER NOT NULL,
        handled_by INTEGER DEFAULT 0,
        handled_at INTEGER DEFAULT 0,
        admin_chat_id INTEGER DEFAULT 0,
        admin_msg_id INTEGER DEFAULT 0
    )""")

    # Support tickets + messages
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL, -- open/closed
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ticket_messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""")

    # Transactions / history
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL, -- deposit/purchase/balance_edit/plan/ref_reward
        amount REAL DEFAULT 0,
        note TEXT DEFAULT '',
        qty INTEGER DEFAULT 1,
        created_at INTEGER NOT NULL
    )""")

    # Referrals (per shop)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS referrals(
        shop_owner_id INTEGER NOT NULL,
        referred_id INTEGER NOT NULL,
        referrer_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(shop_owner_id, referred_id)
    )""")

    conn.commit()
    conn.close()

    ensure_shop_settings(SUPER_ADMIN_ID)
    s = get_shop_settings(SUPER_ADMIN_ID)
    if not (s["welcome_text"] or "").strip():
        set_shop_setting(
            SUPER_ADMIN_ID,
            "welcome_text",
            f"✅ Welcome to <b>{esc(STORE_NAME)}</b>\n"
            f"Get your 24/7 Store Panel Here !!\n\n"
            f"{MAIN_CREATED_LINE}\n{PUBLIC_GROUP_LINE}"
        )
    if not (s["connect_desc"] or "").strip():
        set_shop_setting(
            SUPER_ADMIN_ID,
            "connect_desc",
            "🤖 <b>Connect My Bot</b>\n\n"
            "Create your own bot at @BotFather, then connect your token here.\n"
            "Deposit to Main Shop wallet first.\n\n"
            f"Plan A: <b>{money(PLAN_A_PRICE)} {esc(CURRENCY)}</b> / {PLAN_DAYS} days (Branded welcome)\n"
            f"Plan B: <b>{money(PLAN_B_PRICE)} {esc(CURRENCY)}</b> / {PLAN_DAYS} days (White-Label)\n"
        )

# --- settings helpers ---
def ensure_shop_settings(shop_owner_id: int):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    if not cur.fetchone():
        cur.execute("""
        INSERT INTO shop_settings(shop_owner_id,wallet_message,welcome_text,welcome_file_id,welcome_file_type,connect_desc,referral_percent)
        VALUES(?,?,?,?,?,?,?)
        """, (shop_owner_id, "", "", "", "", "", 0.0))
        conn.commit()
    conn.close()

def get_shop_settings(shop_owner_id: int) -> sqlite3.Row:
    ensure_shop_settings(shop_owner_id)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    r = cur.fetchone()
    conn.close()
    return r

def set_shop_setting(shop_owner_id: int, field: str, value: Any):
    ensure_shop_settings(shop_owner_id)
    conn = db(); cur = conn.cursor()
    cur.execute(f"UPDATE shop_settings SET {field}=? WHERE shop_owner_id=?", (value, shop_owner_id))
    conn.commit()
    conn.close()

# --- users helpers ---
def upsert_user(u):
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO users(user_id, username, first_name, last_name, last_seen) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, "
        "last_name=excluded.last_name, last_seen=excluded.last_seen",
        (u.id, u.username or "", u.first_name or "", u.last_name or "", ts())
    )
    conn.commit(); conn.close()

def user_row(uid: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    conn.close()
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

# --- balances / history helpers ---
def ensure_balance(shop_owner_id: int, uid: int):
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,0)", (shop_owner_id, uid))
    conn.commit(); conn.close()

def get_balance(shop_owner_id: int, uid: int) -> float:
    ensure_balance(shop_owner_id, uid)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT balance FROM balances WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    r = cur.fetchone()
    conn.close()
    return float(r["balance"] or 0) if r else 0.0

def set_balance(shop_owner_id: int, uid: int, val: float):
    val = max(0.0, float(val))
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,?) "
        "ON CONFLICT(shop_owner_id, user_id) DO UPDATE SET balance=excluded.balance",
        (shop_owner_id, uid, val)
    )
    conn.commit(); conn.close()

def add_balance(shop_owner_id: int, uid: int, delta: float) -> float:
    return_val = get_balance(shop_owner_id, uid) + float(delta)
    if return_val < 0:
        return_val = 0.0
    set_balance(shop_owner_id, uid, return_val)
    return return_val

def log_tx(shop_owner_id: int, uid: int, kind: str, amount: float, note: str = "", qty: int = 1):
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions(shop_owner_id,user_id,kind,amount,note,qty,created_at) VALUES(?,?,?,?,?,?,?)",
        (shop_owner_id, uid, kind, float(amount), note or "", int(qty or 1), ts())
    )
    conn.commit(); conn.close()

def list_tx(shop_owner_id: int, uid: int, limit: int = 30) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "SELECT * FROM transactions WHERE shop_owner_id=? AND user_id=? ORDER BY id DESC LIMIT ?",
        (shop_owner_id, uid, int(limit))
    )
    rows = cur.fetchall()
    conn.close()
    return rows

# --- bans helpers ---
def is_banned_user(shop_owner_id: int, uid: int) -> bool:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT banned, restricted_until FROM user_bans WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    r = cur.fetchone()
    conn.close()
    if not r:
        return False
    if int(r["banned"] or 0) == 1:
        return True
    if int(r["restricted_until"] or 0) > ts():
        return True
    return False

def ban_user(shop_owner_id: int, uid: int, banned: int):
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_bans(shop_owner_id,user_id,banned,restricted_until) VALUES(?,?,?,0) "
        "ON CONFLICT(shop_owner_id,user_id) DO UPDATE SET banned=excluded.banned",
        (shop_owner_id, uid, int(banned))
    )
    conn.commit(); conn.close()

def restrict_user(shop_owner_id: int, uid: int, days: int):
    until = ts() + max(0, int(days)) * 86400
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_bans(shop_owner_id,user_id,banned,restricted_until) VALUES(?,?,0,?) "
        "ON CONFLICT(shop_owner_id,user_id) DO UPDATE SET restricted_until=excluded.restricted_until, banned=0",
        (shop_owner_id, uid, until)
    )
    conn.commit(); conn.close()

# --- seller helpers ---
def ensure_seller(seller_id: int):
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO sellers(seller_id, sub_until, plan) VALUES(?,?,?)", (seller_id, 0, "branded"))
    conn.commit(); conn.close()

def seller_row(seller_id: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM sellers WHERE seller_id=?", (seller_id,))
    r = cur.fetchone()
    conn.close()
    return r

def seller_plan(seller_id: int) -> str:
    if is_super(seller_id):
        return "whitelabel"
    r = seller_row(seller_id)
    return (r["plan"] if r else "branded") or "branded"

def seller_set_plan(seller_id: int, plan: str):
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

def seller_add_days(seller_id: int, days: int):
    ensure_seller(seller_id)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT sub_until FROM sellers WHERE seller_id=?", (seller_id,))
    r = cur.fetchone()
    base = max(int(r["sub_until"] or 0), ts())
    cur.execute("UPDATE sellers SET sub_until=? WHERE seller_id=?", (base + int(days) * 86400, seller_id))
    conn.commit(); conn.close()

def super_set_seller_flag(seller_id: int, field: str, val: int):
    ensure_seller(seller_id)
    conn = db(); cur = conn.cursor()
    cur.execute(f"UPDATE sellers SET {field}=? WHERE seller_id=?", (int(val), seller_id))
    conn.commit(); conn.close()

def super_restrict_seller(seller_id: int, days: int):
    ensure_seller(seller_id)
    until = ts() + max(0, int(days)) * 86400
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE sellers SET restricted_until=? WHERE seller_id=?", (until, seller_id))
    conn.commit(); conn.close()

def list_sellers_only() -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("""
        SELECT s.* FROM sellers s
        WHERE s.sub_until>0 OR EXISTS(SELECT 1 FROM seller_bots b WHERE b.seller_id=s.seller_id)
        ORDER BY s.sub_until DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

# --- seller bot helpers ---
def upsert_seller_bot(seller_id: int, token: str, username: str):
    ensure_seller(seller_id)
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO seller_bots(seller_id, bot_token, bot_username, enabled, created_at, updated_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(seller_id) DO UPDATE SET bot_token=excluded.bot_token,
            bot_username=excluded.bot_username, enabled=1, updated_at=excluded.updated_at
    """, (seller_id, token, username, 1, ts(), ts()))
    conn.commit(); conn.close()

def get_seller_bot(seller_id: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM seller_bots WHERE seller_id=?", (seller_id,))
    r = cur.fetchone()
    conn.close()
    return r

def list_enabled_seller_bots() -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM seller_bots WHERE enabled=1")
    rows = cur.fetchall()
    conn.close()
    return rows

def disable_seller_bot(seller_id: int):
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE seller_bots SET enabled=0, updated_at=? WHERE seller_id=?", (ts(), seller_id))
    conn.commit(); conn.close()

# --- wallet methods helpers ---
def list_wallet_methods(shop_owner_id: int) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM wallet_methods WHERE shop_owner_id=? AND enabled=1 ORDER BY id DESC", (shop_owner_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_wallet_method(shop_owner_id: int, mid: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM wallet_methods WHERE shop_owner_id=? AND id=?", (shop_owner_id, mid))
    r = cur.fetchone()
    conn.close()
    return r

def add_wallet_method(shop_owner_id: int, title: str, instructions: str, qr_file_id: str = "") -> int:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO wallet_methods(shop_owner_id,title,instructions,qr_file_id,enabled) VALUES(?,?,?,?,1)",
        (shop_owner_id, title.strip(), instructions.strip(), (qr_file_id or "").strip())
    )
    mid = int(cur.lastrowid)
    conn.commit(); conn.close()
    return mid

def disable_wallet_method(shop_owner_id: int, mid: int):
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE wallet_methods SET enabled=0 WHERE shop_owner_id=? AND id=?", (shop_owner_id, mid))
    conn.commit(); conn.close()

# --- referrals helpers ---
def set_referrer(shop_owner_id: int, referred_id: int, referrer_id: int) -> bool:
    if referred_id == referrer_id:
        return False
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM referrals WHERE shop_owner_id=? AND referred_id=?", (shop_owner_id, referred_id))
    if cur.fetchone():
        conn.close()
        return False
    cur.execute(
        "INSERT INTO referrals(shop_owner_id,referred_id,referrer_id,created_at) VALUES(?,?,?,?)",
        (shop_owner_id, referred_id, referrer_id, ts())
    )
    conn.commit(); conn.close()
    return True

def get_referrer(shop_owner_id: int, referred_id: int) -> Optional[int]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT referrer_id FROM referrals WHERE shop_owner_id=? AND referred_id=?", (shop_owner_id, referred_id))
    r = cur.fetchone()
    conn.close()
    return int(r["referrer_id"]) if r else None

def referral_count(shop_owner_id: int, referrer_id: int) -> int:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(1) c FROM referrals WHERE shop_owner_id=? AND referrer_id=?", (shop_owner_id, referrer_id))
    r = cur.fetchone()
    conn.close()
    return int(r["c"] or 0) if r else 0

# --- catalog helpers ---
def cat_list(shop_owner_id: int) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE shop_owner_id=? ORDER BY id DESC", (shop_owner_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def cocat_list(shop_owner_id: int, cat_id: int) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM cocategories WHERE shop_owner_id=? AND category_id=? ORDER BY id DESC", (shop_owner_id, cat_id))
    rows = cur.fetchall()
    conn.close()
    return rows

def prod_list(shop_owner_id: int, cat_id: int, sub_id: int) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("""
        SELECT * FROM products
        WHERE shop_owner_id=? AND category_id=? AND cocategory_id=?
        ORDER BY id DESC
    """, (shop_owner_id, cat_id, sub_id))
    rows = cur.fetchall()
    conn.close()
    return rows

def cat_get(shop_owner_id: int, cid: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE shop_owner_id=? AND id=?", (shop_owner_id, cid))
    r = cur.fetchone()
    conn.close()
    return r

def cocat_get(shop_owner_id: int, sid: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM cocategories WHERE shop_owner_id=? AND id=?", (shop_owner_id, sid))
    r = cur.fetchone()
    conn.close()
    return r

def prod_get(shop_owner_id: int, pid: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE shop_owner_id=? AND id=?", (shop_owner_id, pid))
    r = cur.fetchone()
    conn.close()
    return r

def stock_count(shop_owner_id: int, pid: int) -> int:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(1) c FROM product_keys WHERE shop_owner_id=? AND product_id=? AND delivered_once=0", (shop_owner_id, pid))
    r = cur.fetchone()
    conn.close()
    return int(r["c"] or 0) if r else 0

def add_keys(shop_owner_id: int, pid: int, lines: List[str]) -> int:
    lines = [l.strip() for l in lines if l.strip()]
    if not lines:
        return 0
    conn = db(); cur = conn.cursor()
    cur.executemany(
        "INSERT INTO product_keys(shop_owner_id,product_id,key_line) VALUES(?,?,?)",
        [(shop_owner_id, pid, l) for l in lines]
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n

def pop_keys(shop_owner_id: int, pid: int, uid: int, qty: int) -> List[str]:
    conn = db(); cur = conn.cursor()
    cur.execute("""
        SELECT id, key_line FROM product_keys
        WHERE shop_owner_id=? AND product_id=? AND delivered_once=0
        ORDER BY id ASC LIMIT ?
    """, (shop_owner_id, pid, int(qty)))
    rows = cur.fetchall()
    ids = [int(r["id"]) for r in rows]
    keys = [r["key_line"] for r in rows]
    if ids:
        placeholders = ",".join(["?"] * len(ids))
        cur.execute(
            f"UPDATE product_keys SET delivered_once=1, delivered_to=?, delivered_at=? WHERE id IN ({placeholders})",
            (uid, ts(), *ids)
        )
    conn.commit(); conn.close()
    return keys

# --- orders helpers ---
def save_order(shop_owner_id: int, user_id: int, order_id: str, product_id: int, product_name: str, qty: int, total: float, keys: List[str]):
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO orders(shop_owner_id,user_id,order_id,product_id,product_name,qty,total,created_at)
        VALUES(?,?,?,?,?,?,?,?)
    """, (shop_owner_id, user_id, order_id, product_id, product_name, int(qty), float(total), ts()))
    if keys:
        cur.executemany(
            "INSERT INTO order_keys(shop_owner_id,order_id,key_line) VALUES(?,?,?)",
            [(shop_owner_id, order_id, k) for k in keys]
        )
    conn.commit(); conn.close()

def list_orders_for_user(shop_owner_id: int, user_id: int, limit: int = 30) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "SELECT * FROM orders WHERE shop_owner_id=? AND user_id=? ORDER BY id DESC LIMIT ?",
        (shop_owner_id, user_id, int(limit))
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def get_order(shop_owner_id: int, order_id: str) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE shop_owner_id=? AND order_id=? LIMIT 1", (shop_owner_id, order_id))
    r = cur.fetchone()
    conn.close()
    return r

def get_order_keys(shop_owner_id: int, order_id: str) -> List[str]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT key_line FROM order_keys WHERE shop_owner_id=? AND order_id=? ORDER BY id ASC", (shop_owner_id, order_id))
    rows = cur.fetchall()
    conn.close()
    return [x["key_line"] for x in rows]

# --- support helpers ---
def get_open_ticket(shop_owner_id: int, user_id: int) -> Optional[int]:
    conn = db(); cur = conn.cursor()
    cur.execute("""
        SELECT id FROM tickets
        WHERE shop_owner_id=? AND user_id=? AND status='open'
        ORDER BY id DESC LIMIT 1
    """, (shop_owner_id, user_id))
    r = cur.fetchone()
    conn.close()
    return int(r["id"]) if r else None

def create_ticket(shop_owner_id: int, user_id: int) -> int:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO tickets(shop_owner_id,user_id,status,created_at,updated_at) VALUES(?,?,?,?,?)",
        (shop_owner_id, user_id, "open", ts(), ts())
    )
    tid = int(cur.lastrowid)
    conn.commit(); conn.close()
    return tid

def add_ticket_msg(ticket_id: int, sender_id: int, text: str):
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticket_messages(ticket_id,sender_id,text,created_at) VALUES(?,?,?,?)",
        (ticket_id, sender_id, text, ts())
    )
    cur.execute("UPDATE tickets SET updated_at=? WHERE id=?", (ts(), ticket_id))
    conn.commit(); conn.close()

# =========================
# BRANDING
# =========================
def render_welcome_text(shop_owner_id: int) -> str:
    s = get_shop_settings(shop_owner_id)
    base = (s["welcome_text"] or "").strip()
    if shop_owner_id == SUPER_ADMIN_ID:
        # Main shop: always contains created + group in default; admin can edit freely.
        return base
    # Seller shop: branding removed only if whitelabel and active
    if seller_active(shop_owner_id) and seller_plan(shop_owner_id) == "whitelabel":
        return base
    if BRAND_LINE not in base:
        return (base + "\n\n" + BRAND_LINE).strip()
    return base

# =========================
# MENUS
# =========================
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

# =========================
# MULTI-BOT MANAGER (seller bots)
# =========================
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
                if not seller_active(sid) or int((seller_row(sid)["banned_shop"] or 0)) == 1:
                    disable_seller_bot(sid)
                    await MANAGER.stop_seller_bot(sid)
            await asyncio.sleep(60)
        except Exception:
            log.exception("watchdog loop")
            await asyncio.sleep(60)

# =========================
# STATE MACHINE
# =========================
def set_state(context: ContextTypes.DEFAULT_TYPE, key: str, data: Dict[str, Any]):
    context.user_data["state"] = key
    context.user_data["state_data"] = data

def get_state(context: ContextTypes.DEFAULT_TYPE) -> Tuple[Optional[str], Dict[str, Any]]:
    return context.user_data.get("state"), (context.user_data.get("state_data") or {})

def clear_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("state", None)
    context.user_data.pop("state_data", None)

def now_text(t: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
    except Exception:
        return str(t)


# =========================
# HANDLERS REGISTRATION (per bot)
# =========================
def register_handlers(app: Application, shop_owner_id: int, bot_kind: str):
    async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await cmd_start(update, context, shop_owner_id)

    async def _cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await on_callback(update, context, shop_owner_id, bot_kind)

    async def _msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await on_message(update, context, shop_owner_id, bot_kind)

    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CallbackQueryHandler(_cb))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, _msg))
    def current_shop_id() -> int:
        return shop_owner_id if bot_kind == "seller" else SUPER_ADMIN_ID

    def shop_title() -> str:
        sid = current_shop_id()
        if bot_kind == "seller":
            return f"🏬 <b>{esc(user_display(sid))} Shop</b>\n\n"
        return f"🏬 <b>{esc(STORE_NAME)}</b>\n\n"

    def menu_for(uid: int) -> InlineKeyboardMarkup:
        sid = current_shop_id()
        if bot_kind == "seller":
            return seller_menu(uid, sid)
        return master_menu(uid)

    async def show_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
        sid = current_shop_id()
        uid = update.effective_user.id
        upsert_user(update.effective_user)
        ensure_balance(sid, uid)

        # Referral deep-link for MASTER SHOP only
        if bot_kind == "master":
            args = (context.args or [])
            if args and args[0].startswith("ref_"):
                try:
                    referrer = int(args[0].split("_", 1)[1])
                    if referrer > 0 and referrer != uid:
                        set_referrer(SUPER_ADMIN_ID, uid, referrer)
                except Exception:
                    pass

        s = get_shop_settings(sid)
        file_id = (s["welcome_file_id"] or "").strip()
        ftype = (s["welcome_file_type"] or "").strip()

        text = render_welcome_text(sid)
        caption = shop_title() + (text or "")

        # If user tapped menu, cancel any state
        clear_state(context)

        if file_id and ftype == "photo":
            await context.bot.send_photo(update.effective_chat.id, photo=file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=menu_for(uid))
        elif file_id and ftype == "video":
            await context.bot.send_video(update.effective_chat.id, video=file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=menu_for(uid))
        else:
            await context.bot.send_message(update.effective_chat.id, caption, parse_mode=ParseMode.HTML, reply_markup=menu_for(uid))

    async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await show_welcome(update, context)

    # =========================
    # PRODUCTS FLOW
    # =========================
    async def products_home(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = True):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        if is_banned_user(sid, uid):
            await q.answer()
            await q.message.reply_text("❌ You are restricted from this shop.")
            return

        cats = cat_list(sid)
        buttons = []
        for c in cats[:60]:
            buttons.append(InlineKeyboardButton(f"📁 {c['name']}", callback_data=f"p:cat:{c['id']}"))
        buttons.append(InlineKeyboardButton("🏠 Menu", callback_data="m:menu"))
        markup = grid(buttons, 2)

        text = "🛒 <b>Categories</b>\nSelect a category:"
        await q.answer()
        if edit:
            await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        else:
            await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)

    async def products_cat(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        if is_banned_user(sid, uid):
            await q.answer()
            await q.message.reply_text("❌ You are restricted from this shop.")
            return

        subs = cocat_list(sid, cat_id)
        buttons = []
        for srow in subs[:80]:
            buttons.append(InlineKeyboardButton(f"📂 {srow['name']}", callback_data=f"p:sub:{cat_id}:{srow['id']}"))
        buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="m:products"))
        buttons.append(InlineKeyboardButton("🏠 Menu", callback_data="m:menu"))
        markup = grid(buttons, 2)

        await q.answer()
        await q.message.edit_text("📁 <b>Sub Categories</b>\nSelect one:", parse_mode=ParseMode.HTML, reply_markup=markup)

    async def products_sub(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int, sub_id: int):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        if is_banned_user(sid, uid):
            await q.answer()
            await q.message.reply_text("❌ You are restricted from this shop.")
            return

        prods = prod_list(sid, cat_id, sub_id)
        buttons = []
        for p in prods[:80]:
            buttons.append(InlineKeyboardButton(f"🧩 {p['name']}", callback_data=f"p:item:{p['id']}"))
        buttons.append(InlineKeyboardButton("⬅️ Back", callback_data=f"p:cat:{cat_id}"))
        buttons.append(InlineKeyboardButton("🏠 Menu", callback_data="m:menu"))
        markup = grid(buttons, 2)

        await q.answer()
        await q.message.edit_text("🧾 <b>Products</b>\nSelect a product:", parse_mode=ParseMode.HTML, reply_markup=markup)

    async def product_view(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        if is_banned_user(sid, uid):
            await q.answer()
            await q.message.reply_text("❌ You are restricted from this shop.")
            return

        p = prod_get(sid, pid)
        if not p:
            await q.answer()
            await q.message.reply_text("Product not found.")
            return

        qty_key = f"qty_{sid}_{pid}"
        qty = int(context.user_data.get(qty_key, 1))
        qty = max(1, qty)

        stock = stock_count(sid, pid)
        bal = get_balance(sid, uid)

        desc = (p["description"] or "").strip()
        price = float(p["price"])
        total = price * qty

        text = (
            f"🧩 <b>{esc(p['name'])}</b>\n"
            f"Price: <b>{money(price)} {esc(CURRENCY)}</b>\n"
            f"Stock: <b>{stock}</b>\n"
            f"Your Balance: <b>{money(bal)} {esc(CURRENCY)}</b>\n\n"
        )
        if desc:
            text += f"{esc(desc)}\n\n"
        text += f"Qty: <b>{qty}</b>\nTotal: <b>{money(total)} {esc(CURRENCY)}</b>\n"

        buttons = [
            InlineKeyboardButton("➖", callback_data=f"p:qty:{pid}:-1"),
            InlineKeyboardButton("➕", callback_data=f"p:qty:{pid}:1"),
            InlineKeyboardButton("✅ Buy", callback_data=f"p:buy:{pid}")
        ]
        rows = [buttons]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"p:sub:{p['category_id']}:{p['cocategory_id']}")])
        rows.append([InlineKeyboardButton("🏠 Menu", callback_data="m:menu")])

        await q.answer()
        await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    async def product_qty(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int, delta: int):
        q = update.callback_query
        sid = current_shop_id()
        qty_key = f"qty_{sid}_{pid}"
        qty = int(context.user_data.get(qty_key, 1))
        qty = max(1, qty + int(delta))
        context.user_data[qty_key] = qty
        await product_view(update, context, pid)

    async def product_buy(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        if is_banned_user(sid, uid):
            await q.answer()
            await q.message.reply_text("❌ You are restricted from this shop.")
            return

        p = prod_get(sid, pid)
        if not p:
            await q.answer()
            await q.message.reply_text("Product not found.")
            return

        qty = int(context.user_data.get(f"qty_{sid}_{pid}", 1))
        qty = max(1, qty)

        stock = stock_count(sid, pid)
        if stock < qty:
            await q.answer()
            await q.message.reply_text("❌ Out of stock / not enough stock.")
            return

        price = float(p["price"])
        total = price * qty

        bal = get_balance(sid, uid)
        if bal < total:
            await q.answer()
            await q.message.reply_text(f"❌ Not enough balance.\nBalance: {money(bal)} {esc(CURRENCY)}", parse_mode=ParseMode.HTML)
            return

        # Deduct
        set_balance(sid, uid, bal - total)

        # Deliver keys
        keys = pop_keys(sid, pid, uid, qty)

        order_id = gen_order_id()
        save_order(sid, uid, order_id, pid, p["name"], qty, total, keys)

        # Transaction note includes Order ID so it appears in user history
        log_tx(sid, uid, "purchase", -total, f"{p['name']} | {order_id}", qty)

        msg = (
            f"✅ <b>Purchase Successful</b>\n\n"
            f"Order ID: <b>{esc(order_id)}</b>\n"
            f"Product: <b>{esc(p['name'])}</b>\n"
            f"Qty: <b>{qty}</b>\n"
            f"Paid: <b>{money(total)} {esc(CURRENCY)}</b>\n\n"
            f"<b>Key(s):</b>\n" + "\n".join([f"<code>{esc(k)}</code>" for k in keys])
        )

        rows = []
        if (p["tg_link"] or "").strip():
            rows.append([InlineKeyboardButton("📦 Get File", callback_data=f"p:file:{pid}")])
        rows.append([InlineKeyboardButton("🏠 Menu", callback_data="m:menu")])

        await q.answer()
        await q.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

        # Notify owner + super admin
        try:
            keys_preview = keys[0] if keys else ""
            keys_text = "\n".join(keys) if keys else "(no keys)"

            if bot_kind == "seller":
                # seller owner = shop_owner_id
                await context.bot.send_message(
                    shop_owner_id,
                    (
                        f"🔔 <b>New Order</b>\n"
                        f"Order ID: <b>{esc(order_id)}</b>\n"
                        f"User: {esc(user_display(uid))} (ID: <code>{uid}</code>)\n"
                        f"Product: <b>{esc(p['name'])}</b>\n"
                        f"Qty: <b>{qty}</b>\n"
                        f"Total: <b>{money(total)} {esc(CURRENCY)}</b>\n\n"
                        f"<b>Keys:</b>\n<code>{esc(keys_text)}</code>"
                    ),
                    parse_mode=ParseMode.HTML
                )
                await context.bot.send_message(
                    SUPER_ADMIN_ID,
                    (
                        f"🔔 <b>Seller Order</b>\n"
                        f"Seller: <b>{esc(user_display(shop_owner_id))}</b> (ID: <code>{shop_owner_id}</code>)\n"
                        f"Order ID: <b>{esc(order_id)}</b>\n"
                        f"User: {esc(user_display(uid))} (ID: <code>{uid}</code>)\n"
                        f"Product: <b>{esc(p['name'])}</b>\n"
                        f"Qty: <b>{qty}</b>\n"
                        f"Total: <b>{money(total)} {esc(CURRENCY)}</b>\n\n"
                        f"<b>Keys:</b>\n<code>{esc(keys_text)}</code>"
                    ),
                    parse_mode=ParseMode.HTML
                )
            else:
                await context.bot.send_message(
                    SUPER_ADMIN_ID,
                    (
                        f"🔔 <b>Main Shop Order</b>\n"
                        f"Order ID: <b>{esc(order_id)}</b>\n"
                        f"User: {esc(user_display(uid))} (ID: <code>{uid}</code>)\n"
                        f"Product: <b>{esc(p['name'])}</b>\n"
                        f"Qty: <b>{qty}</b>\n"
                        f"Total: <b>{money(total)} {esc(CURRENCY)}</b>\n\n"
                        f"<b>Keys:</b>\n<code>{esc(keys_text)}</code>"
                    ),
                    parse_mode=ParseMode.HTML
                )
        except Exception:
            pass

    async def product_get_file(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int):
        """
        Join-gate: user must join PUBLIC channel (username). We don't reveal private link.
        """
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        p = prod_get(sid, pid)
        if not p:
            await q.answer()
            await q.message.reply_text("Product not found.")
            return

        link = (p["tg_link"] or "").strip()
        uname = parse_channel_username(link)
        if not uname:
            await q.answer()
            await q.message.reply_text("❌ This product has no public channel configured.")
            return

        text = (
            f"📦 <b>Get File</b>\n\n"
            f"To access the file, you must join the channel:\n"
            f"➡️ <b>@{esc(uname)}</b>\n\n"
            f"After joining, press <b>I Joined</b>."
        )
        rows = [
            [InlineKeyboardButton("✅ I Joined", callback_data=f"p:joincheck:{pid}")],
            [InlineKeyboardButton("🏠 Menu", callback_data="m:menu")]
        ]
        await q.answer()
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    async def product_join_check(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        p = prod_get(sid, pid)
        if not p:
            await q.answer()
            await q.message.reply_text("Product not found.")
            return

        uname = parse_channel_username((p["tg_link"] or "").strip())
        if not uname:
            await q.answer()
            await q.message.reply_text("❌ Channel not configured.")
            return

        try:
            member = await context.bot.get_chat_member(chat_id=f"@{uname}", user_id=uid)
            status = member.status
            if status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                # Access granted (we do NOT show private links; just confirm)
                await q.answer("Access granted ✅", show_alert=True)
                await q.message.reply_text("✅ Access confirmed. Please check the channel posts/files.", parse_mode=ParseMode.HTML)
                return
        except Exception:
            pass

        await q.answer("Not joined yet", show_alert=True)
        await q.message.reply_text("❌ You must join the channel first, then press I Joined again.", parse_mode=ParseMode.HTML)

    # =========================
    # WALLET / DEPOSIT
    # =========================
    async def wallet_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id
        bal = get_balance(sid, uid)

        methods = list_wallet_methods(sid)
        text = f"💰 <b>Wallet</b>\nBalance: <b>{money(bal)} {esc(CURRENCY)}</b>\n\nSelect deposit method:"
        btns = []
        for mth in methods[:30]:
            btns.append(InlineKeyboardButton(f"➕ Deposit — {mth['title']}", callback_data=f"w:method:{mth['id']}"))
        btns.append(InlineKeyboardButton("🏠 Menu", callback_data="m:menu"))
        await q.answer()
        await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=grid(btns, 1))

    async def wallet_method(update: Update, context: ContextTypes.DEFAULT_TYPE, mid: int):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        mth = get_wallet_method(sid, mid)
        if not mth or int(mth["enabled"] or 0) != 1:
            await q.answer()
            await q.message.reply_text("Deposit method not found.")
            return

        instr = (mth["instructions"] or "").strip()
        text = (
            f"💳 <b>{esc(mth['title'])}</b>\n\n"
            f"{esc(instr)}\n\n"
            f"Send your deposit <b>amount</b> now (example: 50)\n"
            f"Then you must upload a <b>PHOTO proof</b>."
        )

        # set state to get amount
        set_state(context, "DEP_AMOUNT", {"method_id": mid})
        await q.answer()
        # show qr if exists
        qr = (mth["qr_file_id"] or "").strip()
        if qr:
            await q.message.reply_photo(photo=qr, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="m:wallet")]]))
        else:
            await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="m:wallet")]]))

    # =========================
    # HISTORY
    # =========================
    async def history_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        rows = list_tx(sid, uid, 30)
        if not rows:
            await q.answer()
            await q.message.edit_text("📜 <b>History</b>\nNo history yet.", parse_mode=ParseMode.HTML, reply_markup=kb([[InlineKeyboardButton("🏠 Menu", callback_data="m:menu")]]))
            return

        lines = ["📜 <b>History</b>"]
        for r in rows:
            kind = (r["kind"] or "").strip()
            amt = float(r["amount"] or 0)
            note = (r["note"] or "").strip()
            qty = int(r["qty"] or 1)
            t = now_text(int(r["created_at"] or 0))

            if kind == "deposit":
                lines.append(f"\n💰 Deposited: <b>{money(abs(amt))} {esc(CURRENCY)}</b>\nTotal Balance: <b>{money(get_balance(sid, uid))} {esc(CURRENCY)}</b>\n📅 {esc(t)}")
            elif kind == "purchase":
                prod_name = note
                oid = ""
                if " | " in note:
                    prod_name, oid = note.split(" | ", 1)
                    prod_name = prod_name.strip()
                    oid = oid.strip()
                if oid:
                    lines.append(
                        f"\n🧾 Order ID: <b>{esc(oid)}</b>\n"
                        f"Product: <b>{esc(prod_name)}</b> (x{qty})\n"
                        f"Amount: <b>{money(abs(amt))} {esc(CURRENCY)}</b>\n"
                        f"📅 {esc(t)}"
                    )
                else:
                    lines.append(f"\n🛒 Purchased: <b>{esc(prod_name)}</b> (x{qty}) — <b>{money(abs(amt))} {esc(CURRENCY)}</b>\n📅 {esc(t)}")
            elif kind == "ref_reward":
                lines.append(f"\n🎁 Referral Reward: <b>{money(abs(amt))} {esc(CURRENCY)}</b>\n{esc(note)}\n📅 {esc(t)}")
            else:
                sign = "+" if amt >= 0 else "-"
                lines.append(f"\n⚙️ {esc(kind)}: <b>{sign}{money(abs(amt))} {esc(CURRENCY)}</b>\n{esc(note)}\n📅 {esc(t)}")

        await q.answer()
        await q.message.edit_text("\n".join(lines)[:3900], parse_mode=ParseMode.HTML, reply_markup=kb([[InlineKeyboardButton("🏠 Menu", callback_data="m:menu")]]))

    # =========================
    # SUPPORT INBOX (draft -> done)
    # =========================
    async def support_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        if is_banned_user(sid, uid):
            await q.answer()
            await q.message.reply_text("❌ You are restricted from this shop.")
            return

        text = "🆘 <b>Support Inbox</b>\n\nType your message. When ready, press <b>Done</b>."
        set_state(context, "SUPPORT_DRAFT", {"draft": ""})
        await q.answer()
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb([[InlineKeyboardButton("✅ Done", callback_data="s:done")],
                                                                                   [InlineKeyboardButton("🏠 Menu", callback_data="m:menu")]]))

    async def support_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        state, data = get_state(context)
        draft = (data.get("draft") or "").strip() if state == "SUPPORT_DRAFT" else ""
        if not draft:
            await q.answer("Send a message first", show_alert=True)
            return

        tid = get_open_ticket(sid, uid)
        if not tid:
            tid = create_ticket(sid, uid)
        add_ticket_msg(tid, uid, draft)

        clear_state(context)
        await q.answer("Sent ✅", show_alert=False)

        # Notify shop owner (seller owner or super admin for main shop)
        try:
            owner = sid if bot_kind == "seller" else SUPER_ADMIN_ID
            await context.bot.send_message(
                owner,
                f"🆘 <b>New Support</b>\nShop: <b>{esc(user_display(sid))}</b>\nUser: {esc(user_display(uid))} (ID: <code>{uid}</code>)\n\n{esc(draft)}",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Reply", callback_data=f"a:ticket:{sid}:{uid}")]])
            )
        except Exception:
            pass

        await q.message.reply_text("✅ Support message sent.", parse_mode=ParseMode.HTML, reply_markup=kb([[InlineKeyboardButton("🏠 Menu", callback_data="m:menu")]]))

    # =========================
    # CONNECT MY BOT (master only)
    # =========================
    async def connect_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        if bot_kind != "master":
            await q.answer()
            await q.message.reply_text("This option is only in Main Shop.")
            return

        s = get_shop_settings(SUPER_ADMIN_ID)
        desc = (s["connect_desc"] or "").strip()
        text = desc or "🤖 Connect My Bot"
        rows = [
            [InlineKeyboardButton(f"Plan A — {money(PLAN_A_PRICE)} {esc(CURRENCY)}", callback_data="c:plan:branded")],
            [InlineKeyboardButton(f"Plan B — {money(PLAN_B_PRICE)} {esc(CURRENCY)}", callback_data="c:plan:whitelabel")],
            [InlineKeyboardButton("🏠 Menu", callback_data="m:menu")]
        ]
        await q.answer()
        await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    async def connect_choose_plan(update: Update, context: ContextTypes.DEFAULT_TYPE, plan: str):
        q = update.callback_query
        uid = update.effective_user.id

        price = PLAN_A_PRICE if plan == "branded" else PLAN_B_PRICE
        bal = get_balance(SUPER_ADMIN_ID, uid)
        if bal < price:
            await q.answer()
            await q.message.reply_text(f"❌ Not enough balance.\nBalance: {money(bal)} {esc(CURRENCY)}", parse_mode=ParseMode.HTML)
            return

        # ask token
        set_state(context, "CONNECT_TOKEN", {"plan": plan, "price": price})
        await q.answer()
        await q.message.reply_text(
            "Send your <b>Bot Token</b> now (from @BotFather).\n\nExample:\n123456:ABC-DEF...",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("🏠 Menu", callback_data="m:menu")]])
        )

    # =========================
    # EXTEND SUBSCRIPTION (seller owner from seller bot)
    # =========================
    async def extend_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        sid = current_shop_id()
        uid = update.effective_user.id

        if bot_kind != "seller":
            await q.answer()
            await q.message.reply_text("This option is only inside seller bot.")
            return
        if uid != sid and not is_super(uid):
            await q.answer()
            await q.message.reply_text("❌ Only the seller owner can extend.")
            return

        days_left = seller_days_left(sid)
        plan = seller_plan(sid)
        text = (
            f"⏳ <b>Subscription</b>\n"
            f"Plan: <b>{esc(plan)}</b>\n"
            f"Days left: <b>{days_left}</b>\n\n"
            f"Extend in Main Shop (deducts from your Main Shop wallet).\n"
            f"Plan A: {money(PLAN_A_PRICE)} {esc(CURRENCY)} / {PLAN_DAYS} days\n"
            f"Plan B: {money(PLAN_B_PRICE)} {esc(CURRENCY)} / {PLAN_DAYS} days"
        )

        deep = ""
        if MASTER_BOT_USERNAME:
            deep = f"https://t.me/{MASTER_BOT_USERNAME}?start=extend_{sid}"
        rows = []
        if deep:
            rows.append([InlineKeyboardButton("➡️ Open Main Shop", url=deep)])
        rows.append([InlineKeyboardButton("🏠 Menu", callback_data="m:menu")])

        await q.answer()
        await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    # =========================
    # ADMIN PANEL (seller owner & super admin)
    # =========================
    def can_admin(uid: int) -> bool:
        sid = current_shop_id()
        if is_super(uid):
            return True
        if bot_kind == "seller" and uid == sid:
            # seller owner
            r = seller_row(sid)
            if r and int(r["banned_panel"] or 0) == 1:
                return False
            return True
        if bot_kind == "master" and is_super(uid):
            return True
        return False

    async def admin_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return
        rows = [
            [InlineKeyboardButton("👥 Users", callback_data="a:users:0")],
            [InlineKeyboardButton("🖼 Edit Welcome", callback_data="a:welcome")],
            [InlineKeyboardButton("💳 Wallet Methods", callback_data="a:wallet")],
            [InlineKeyboardButton("🧩 Manage Products", callback_data="a:catalog")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="a:broadcast")],
            [InlineKeyboardButton("🎁 Referral %", callback_data="a:refpct")],
            [InlineKeyboardButton("🏠 Menu", callback_data="m:menu")],
        ]
        await q.answer()
        await q.message.edit_text("🛠 <b>Admin Panel</b>", parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    # Admin: Users list
    async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE, offset: int = 0, query: str = ""):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return

        conn = db(); cur = conn.cursor()
        if query:
            like = f"%{query.strip().lstrip('@')}%"
            cur.execute("""
                SELECT b.user_id, b.balance, u.username, u.first_name, u.last_name
                FROM balances b
                LEFT JOIN users u ON u.user_id=b.user_id
                WHERE b.shop_owner_id=? AND (u.username LIKE ? OR u.first_name LIKE ? OR u.last_name LIKE ?)
                ORDER BY b.balance DESC LIMIT 20
            """, (sid, like, like, like))
        else:
            cur.execute("""
                SELECT b.user_id, b.balance, u.username, u.first_name, u.last_name
                FROM balances b
                LEFT JOIN users u ON u.user_id=b.user_id
                WHERE b.shop_owner_id=?
                ORDER BY b.rowid DESC LIMIT 20 OFFSET ?
            """, (sid, int(offset)))
        rows = cur.fetchall()
        conn.close()

        btns = [InlineKeyboardButton("🔍 Search", callback_data="a:users_search")]
        for r in rows:
            u_id = int(r["user_id"])
            un = (r["username"] or "").strip()
            title = f"@{un}" if un else (f"{(r['first_name'] or '').strip()} {(r['last_name'] or '').strip()}".strip() or str(u_id))
            btns.append(InlineKeyboardButton(f"{title} | {money(float(r['balance'] or 0))} {CURRENCY}", callback_data=f"a:user:{u_id}"))

        nav = []
        if not query and offset >= 20:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"a:users:{offset-20}"))
        if not query and len(rows) == 20:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"a:users:{offset+20}"))
        if nav:
            btns.extend(nav)
        btns.append(InlineKeyboardButton("⬅️ Back", callback_data="m:admin"))
        await q.answer()
        await q.message.edit_text("👥 <b>Users</b>\nTap a user:", parse_mode=ParseMode.HTML, reply_markup=grid(btns, 1))

    async def admin_user_view(update: Update, context: ContextTypes.DEFAULT_TYPE, target_uid: int):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return

        bal = get_balance(sid, target_uid)
        refc = referral_count(sid, target_uid)
        orders = list_orders_for_user(sid, target_uid, 10)

        lines = [
            "👤 <b>User Info</b>",
            f"Username: {esc(user_display(target_uid))}",
            f"Telegram ID: <code>{target_uid}</code>",
            f"Balance: <b>{money(bal)} {esc(CURRENCY)}</b>",
            f"Referrals: <b>{refc}</b>",
            "",
            "🧾 <b>Order History</b>"
        ]
        if not orders:
            lines.append("No orders.")
        else:
            for o in orders:
                oid = o["order_id"]
                dt = now_text(int(o["created_at"] or 0))
                amt = float(o["total"] or 0)
                # show 1 key preview (example Key278272)
                ks = get_order_keys(sid, oid)
                preview = ks[0] if ks else ""
                lines.append(f"\n🧾 <b>{esc(oid)}</b>\n📅 {esc(dt)}\n💰 {money(amt)} {esc(CURRENCY)}\n🔑 Key: {esc(preview)}")

        rows = [
            [InlineKeyboardButton("➕ Add Balance", callback_data=f"a:baladd:{target_uid}")],
            [InlineKeyboardButton("➖ Deduct Balance", callback_data=f"a:baldeduct:{target_uid}")],
            [InlineKeyboardButton("🚫 Ban", callback_data=f"a:ban:{target_uid}:1"),
             InlineKeyboardButton("✅ Unban", callback_data=f"a:ban:{target_uid}:0")],
            [InlineKeyboardButton("⏳ Restrict 7d", callback_data=f"a:restrict:{target_uid}:7"),
             InlineKeyboardButton("⏳ 14d", callback_data=f"a:restrict:{target_uid}:14"),
             InlineKeyboardButton("⏳ 30d", callback_data=f"a:restrict:{target_uid}:30")],
        ]
        # Order buttons (tap to view exact keys)
        if orders:
            rows.append([InlineKeyboardButton("— Orders —", callback_data="noop")])
            for o in orders[:8]:
                rows.append([InlineKeyboardButton(f"🧾 {o['order_id']}", callback_data=f"a:order:{target_uid}:{o['order_id']}")])

        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="a:users:0")])
        await q.answer()
        await q.message.edit_text("\n".join(lines)[:3900], parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    async def admin_order_view(update: Update, context: ContextTypes.DEFAULT_TYPE, target_uid: int, order_id: str):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return

        o = get_order(sid, order_id)
        if not o or int(o["user_id"]) != int(target_uid):
            await q.answer("Order not found", show_alert=True)
            return

        keys = get_order_keys(sid, order_id)
        dt = now_text(int(o["created_at"] or 0))
        text = (
            f"🧾 <b>Order Details</b>\n\n"
            f"Order ID: <b>{esc(order_id)}</b>\n"
            f"User: {esc(user_display(target_uid))} (ID: <code>{target_uid}</code>)\n"
            f"Product: <b>{esc(o['product_name'])}</b>\n"
            f"Qty: <b>{int(o['qty'])}</b>\n"
            f"Amount: <b>{money(float(o['total']))} {esc(CURRENCY)}</b>\n"
            f"Date: <b>{esc(dt)}</b>\n\n"
            f"🔑 <b>Exact Key(s) Delivered</b>\n" +
            ("\n".join([f"<code>{esc(k)}</code>" for k in keys]) if keys else "<i>(none)</i>")
        )
        await q.answer()
        await q.message.edit_text(text[:3900], parse_mode=ParseMode.HTML, reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data=f"a:user:{target_uid}")]]))

    # Admin: edit welcome
    async def admin_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return
        set_state(context, "WELCOME_TEXT", {})
        await q.answer()
        await q.message.reply_text(
            "🖼 <b>Edit Welcome</b>\nSend new welcome <b>text</b> now.\n\n(Optional) after that, send a photo/video to set media.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="m:admin")]])
        )

    # Admin: wallet methods manage
    async def admin_wallet_methods(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return

        methods = list_wallet_methods(sid)
        lines = ["💳 <b>Wallet Methods</b>"]
        btns = [InlineKeyboardButton("➕ Add Method", callback_data="a:wallet_add")]
        for mth in methods[:20]:
            lines.append(f"\n• <b>{esc(mth['title'])}</b> (ID: {mth['id']})")
            btns.append(InlineKeyboardButton(f"🗑 Delete {mth['title']}", callback_data=f"a:wallet_del:{mth['id']}"))
        btns.append(InlineKeyboardButton("⬅️ Back", callback_data="m:admin"))
        await q.answer()
        await q.message.edit_text("\n".join(lines)[:3900], parse_mode=ParseMode.HTML, reply_markup=grid(btns, 1))

    async def admin_wallet_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        uid = update.effective_user.id
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return
        set_state(context, "WALLET_ADD_TITLE", {})
        await q.answer()
        await q.message.reply_text("Send method <b>title</b> (example: USDT TRC20 / GCash / PayNow)", parse_mode=ParseMode.HTML)

    async def admin_wallet_del(update: Update, context: ContextTypes.DEFAULT_TYPE, mid: int):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return
        disable_wallet_method(sid, mid)
        await q.answer("Deleted", show_alert=False)
        await admin_wallet_methods(update, context)

    # Admin: catalog manager
    async def admin_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return

        btns = [
            InlineKeyboardButton("➕ Add Category", callback_data="a:cat_add"),
            InlineKeyboardButton("📁 Categories", callback_data="a:cats"),
            InlineKeyboardButton("⬅️ Back", callback_data="m:admin"),
        ]
        await q.answer()
        await q.message.edit_text("🧩 <b>Manage Products</b>", parse_mode=ParseMode.HTML, reply_markup=grid(btns, 1))

    # Admin: list categories -> subs -> products (buttons only)
    async def admin_cats(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return
        cats = cat_list(sid)
        btns = [InlineKeyboardButton("➕ Add Category", callback_data="a:cat_add")]
        for c in cats[:40]:
            btns.append(InlineKeyboardButton(f"📁 {c['name']}", callback_data=f"a:cat:{c['id']}"))
        btns.append(InlineKeyboardButton("⬅️ Back", callback_data="a:catalog"))
        await q.answer()
        await q.message.edit_text("📁 <b>Categories</b>", parse_mode=ParseMode.HTML, reply_markup=grid(btns, 1))

    async def admin_cat_view(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return
        subs = cocat_list(sid, cat_id)
        btns = [
            InlineKeyboardButton("➕ Add Sub-Category", callback_data=f"a:sub_add:{cat_id}"),
        ]
        for srow in subs[:40]:
            btns.append(InlineKeyboardButton(f"📂 {srow['name']}", callback_data=f"a:sub:{cat_id}:{srow['id']}"))
        btns.append(InlineKeyboardButton("⬅️ Back", callback_data="a:cats"))
        await q.answer()
        await q.message.edit_text("📂 <b>Sub-Categories</b>", parse_mode=ParseMode.HTML, reply_markup=grid(btns, 1))

    async def admin_sub_view(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int, sub_id: int):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return
        prods = prod_list(sid, cat_id, sub_id)
        btns = [InlineKeyboardButton("➕ Add Product", callback_data=f"a:prod_add:{cat_id}:{sub_id}")]
        for p in prods[:40]:
            btns.append(InlineKeyboardButton(f"🧩 {p['name']}", callback_data=f"a:prod:{p['id']}"))
        btns.append(InlineKeyboardButton("⬅️ Back", callback_data=f"a:cat:{cat_id}"))
        await q.answer()
        await q.message.edit_text("🧾 <b>Products</b>", parse_mode=ParseMode.HTML, reply_markup=grid(btns, 1))

    async def admin_prod_view(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return

        p = prod_get(sid, pid)
        if not p:
            await q.answer("Not found", show_alert=True)
            return
        stock = stock_count(sid, pid)
        text = (
            f"🧩 <b>{esc(p['name'])}</b>\n"
            f"Price: <b>{money(float(p['price']))} {esc(CURRENCY)}</b>\n"
            f"Stock lines: <b>{stock}</b>\n"
            f"Channel: <b>{esc(p['tg_link'] or '')}</b>\n"
        )
        btns = [
            [InlineKeyboardButton("✏️ Edit Name", callback_data=f"a:prod_edit_name:{pid}")],
            [InlineKeyboardButton("💲 Edit Price", callback_data=f"a:prod_edit_price:{pid}")],
            [InlineKeyboardButton("📝 Edit Description", callback_data=f"a:prod_edit_desc:{pid}")],
            [InlineKeyboardButton("🔗 Set Channel Link", callback_data=f"a:prod_edit_link:{pid}")],
            [InlineKeyboardButton("🔑 Add Keys", callback_data=f"a:keys_add:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"a:sub:{p['category_id']}:{p['cocategory_id']}")],
        ]
        await q.answer()
        await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb(btns))

    async def admin_refpct(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return
        s = get_shop_settings(sid)
        pct = float(s["referral_percent"] or 0)
        set_state(context, "REF_PCT", {})
        await q.answer()
        await q.message.reply_text(
            f"🎁 <b>Referral %</b>\nCurrent: <b>{money(pct)}%</b>\n\nSend new percent (example: 5)",
            parse_mode=ParseMode.HTML
        )

    async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        uid = update.effective_user.id
        if not can_admin(uid):
            await q.answer("No access", show_alert=True)
            return
        set_state(context, "BROADCAST", {})
        await q.answer()
        await q.message.reply_text(
            "📢 <b>Broadcast</b>\nSend the message now (text/photo/video). It will be sent to all shop users.",
            parse_mode=ParseMode.HTML
        )

    # =========================
    # SUPER ADMIN BUTTON (master only)
    # =========================
    async def super_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        if bot_kind != "master" or not is_super(update.effective_user.id):
            await q.answer("No access", show_alert=True)
            return
        btns = [
            InlineKeyboardButton("🏪 Sellers List", callback_data="su:sellers"),
            InlineKeyboardButton("⬅️ Back", callback_data="m:menu"),
        ]
        await q.answer()
        await q.message.edit_text("👑 <b>Super Admin</b>", parse_mode=ParseMode.HTML, reply_markup=grid(btns, 1))

    async def super_sellers(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        if bot_kind != "master" or not is_super(update.effective_user.id):
            await q.answer("No access", show_alert=True)
            return
        sellers = list_sellers_only()
        btns = []
        for srow in sellers[:50]:
            sid = int(srow["seller_id"])
            btns.append(InlineKeyboardButton(f"{user_display(sid)} | {seller_plan(sid)} | {seller_days_left(sid)}d", callback_data=f"su:seller:{sid}"))
        btns.append(InlineKeyboardButton("⬅️ Back", callback_data="m:super"))
        await q.answer()
        await q.message.edit_text("🏪 <b>Sellers</b>", parse_mode=ParseMode.HTML, reply_markup=grid(btns, 1))

    async def super_seller_view(update: Update, context: ContextTypes.DEFAULT_TYPE, seller_id: int):
        q = update.callback_query
        if bot_kind != "master" or not is_super(update.effective_user.id):
            await q.answer("No access", show_alert=True)
            return
        r = seller_row(seller_id)
        if not r:
            await q.answer("Not found", show_alert=True)
            return
        text = (
            f"🏪 <b>Seller</b>\n"
            f"User: {esc(user_display(seller_id))}\n"
            f"ID: <code>{seller_id}</code>\n"
            f"Plan: <b>{esc(seller_plan(seller_id))}</b>\n"
            f"Days left: <b>{seller_days_left(seller_id)}</b>\n"
            f"Shop banned: <b>{int(r['banned_shop'] or 0)}</b>\n"
            f"Panel banned: <b>{int(r['banned_panel'] or 0)}</b>\n"
        )
        rows = [
            [InlineKeyboardButton("🚫 Ban Shop", callback_data=f"su:ban_shop:{seller_id}:1"),
             InlineKeyboardButton("✅ Unban Shop", callback_data=f"su:ban_shop:{seller_id}:0")],
            [InlineKeyboardButton("🚫 Ban Panel", callback_data=f"su:ban_panel:{seller_id}:1"),
             InlineKeyboardButton("✅ Unban Panel", callback_data=f"su:ban_panel:{seller_id}:0")],
            [InlineKeyboardButton("⏳ Restrict 7d", callback_data=f"su:restrict:{seller_id}:7"),
             InlineKeyboardButton("⏳ 30d", callback_data=f"su:restrict:{seller_id}:30")],
            [InlineKeyboardButton("👥 View Seller Users", callback_data=f"su:users:{seller_id}:0")],
            [InlineKeyboardButton("⬅️ Back", callback_data="su:sellers")]
        ]
        await q.answer()
        await q.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    async def super_seller_users(update: Update, context: ContextTypes.DEFAULT_TYPE, seller_id: int, offset: int = 0):
        q = update.callback_query
        if bot_kind != "master" or not is_super(update.effective_user.id):
            await q.answer("No access", show_alert=True)
            return
        conn = db(); cur = conn.cursor()
        cur.execute("""
            SELECT b.user_id, b.balance, u.username, u.first_name, u.last_name
            FROM balances b LEFT JOIN users u ON u.user_id=b.user_id
            WHERE b.shop_owner_id=?
            ORDER BY b.rowid DESC LIMIT 20 OFFSET ?
        """, (seller_id, int(offset)))
        rows = cur.fetchall()
        conn.close()

        btns = []
        for r in rows:
            uid = int(r["user_id"])
            un = (r["username"] or "").strip()
            title = f"@{un}" if un else (f"{(r['first_name'] or '').strip()} {(r['last_name'] or '').strip()}".strip() or str(uid))
            btns.append(InlineKeyboardButton(f"{title} | {money(float(r['balance'] or 0))} {CURRENCY}", callback_data=f"su:user:{seller_id}:{uid}"))
        nav = []
        if offset >= 20:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"su:users:{seller_id}:{offset-20}"))
        if len(rows) == 20:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"su:users:{seller_id}:{offset+20}"))
        if nav:
            btns.extend(nav)
        btns.append(InlineKeyboardButton("⬅️ Back", callback_data=f"su:seller:{seller_id}"))
        await q.answer()
        await q.message.edit_text("👥 <b>Seller Users</b>", parse_mode=ParseMode.HTML, reply_markup=grid(btns, 1))

    # =========================
    # CALLBACK ROUTER
    # =========================
    async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        data = (q.data or "").strip()
        uid = update.effective_user.id
        sid = current_shop_id()

        # Cancel last state on menu/back
        if data in ("m:menu",):
            await show_welcome(update, context)
            return

        # NOOP
        if data == "noop":
            await q.answer()
            return

        # Main menu buttons
        if data == "m:products":
            await products_home(update, context)
            return
        if data == "m:wallet":
            await wallet_home(update, context)
            return
        if data == "m:history":
            await history_home(update, context)
            return
        if data == "m:support":
            await support_home(update, context)
            return
        if data == "m:connect":
            await connect_home(update, context)
            return
        if data == "m:extend":
            await extend_home(update, context)
            return
        if data == "m:admin":
            await admin_home(update, context)
            return
        if data == "m:super":
            await super_home(update, context)
            return

        # Products tree
        if data.startswith("p:cat:"):
            await products_cat(update, context, int(data.split(":")[2]))
            return
        if data.startswith("p:sub:"):
            _, _, cat_id, sub_id = data.split(":")
            await products_sub(update, context, int(cat_id), int(sub_id))
            return
        if data.startswith("p:item:"):
            await product_view(update, context, int(data.split(":")[2]))
            return
        if data.startswith("p:qty:"):
            _, _, pid, delta = data.split(":")
            await product_qty(update, context, int(pid), int(delta))
            return
        if data.startswith("p:buy:"):
            await product_buy(update, context, int(data.split(":")[2]))
            return
        if data.startswith("p:file:"):
            await product_get_file(update, context, int(data.split(":")[2]))
            return
        if data.startswith("p:joincheck:"):
            await product_join_check(update, context, int(data.split(":")[2]))
            return

        # Wallet deposit method
        if data.startswith("w:method:"):
            await wallet_method(update, context, int(data.split(":")[2]))
            return

        # Support done
        if data == "s:done":
            await support_done(update, context)
            return

        # Connect plans
        if data.startswith("c:plan:"):
            plan = data.split(":")[2]
            await connect_choose_plan(update, context, plan)
            return

        # Admin panel routes
        if data.startswith("a:users_search"):
            await q.answer()
            set_state(context, "ADMIN_USER_SEARCH", {})
            await q.message.reply_text("Send username to search (example: @john)", parse_mode=ParseMode.HTML)
            return

        if data.startswith("a:users:"):
            off = int(data.split(":")[2])
            await admin_users(update, context, off, "")
            return

        if data.startswith("a:user:"):
            target = int(data.split(":")[2])
            await admin_user_view(update, context, target)
            return

        if data.startswith("a:order:"):
            _, _, target_uid, oid = data.split(":", 3)
            await admin_order_view(update, context, int(target_uid), oid)
            return

        if data.startswith("a:baladd:"):
            target = int(data.split(":")[2])
            set_state(context, "BAL_ADD", {"target": target})
            await q.answer()
            await q.message.reply_text("Send amount to <b>ADD</b>:", parse_mode=ParseMode.HTML)
            return

        if data.startswith("a:baldeduct:"):
            target = int(data.split(":")[2])
            set_state(context, "BAL_DEDUCT", {"target": target})
            await q.answer()
            await q.message.reply_text("Send amount to <b>DEDUCT</b>:", parse_mode=ParseMode.HTML)
            return

        if data.startswith("a:ban:"):
            _, _, target, flag = data.split(":")
            ban_user(sid, int(target), int(flag))
            await q.answer("Updated", show_alert=False)
            await admin_user_view(update, context, int(target))
            return

        if data.startswith("a:restrict:"):
            _, _, target, days = data.split(":")
            restrict_user(sid, int(target), int(days))
            await q.answer("Restricted", show_alert=False)
            await admin_user_view(update, context, int(target))
            return

        if data == "a:welcome":
            await admin_welcome(update, context)
            return

        if data == "a:wallet":
            await admin_wallet_methods(update, context)
            return
        if data == "a:wallet_add":
            await admin_wallet_add(update, context)
            return
        if data.startswith("a:wallet_del:"):
            await admin_wallet_del(update, context, int(data.split(":")[2]))
            return

        if data == "a:catalog":
            await admin_catalog(update, context)
            return
        if data == "a:cats":
            await admin_cats(update, context)
            return
        if data.startswith("a:cat_add"):
            await q.answer()
            set_state(context, "CAT_ADD", {})
            await q.message.reply_text("Send new <b>Category name</b>:", parse_mode=ParseMode.HTML)
            return
        if data.startswith("a:cat:"):
            await admin_cat_view(update, context, int(data.split(":")[2]))
            return
        if data.startswith("a:sub_add:"):
            cat_id = int(data.split(":")[2])
            await q.answer()
            set_state(context, "SUB_ADD", {"cat_id": cat_id})
            await q.message.reply_text("Send new <b>Sub-Category name</b>:", parse_mode=ParseMode.HTML)
            return
        if data.startswith("a:sub:"):
            _, _, cat_id, sub_id = data.split(":")
            await admin_sub_view(update, context, int(cat_id), int(sub_id))
            return
        if data.startswith("a:prod_add:"):
            _, _, cat_id, sub_id = data.split(":")
            await q.answer()
            set_state(context, "PROD_ADD_NAME", {"cat_id": int(cat_id), "sub_id": int(sub_id)})
            await q.message.reply_text("Send new <b>Product name</b>:", parse_mode=ParseMode.HTML)
            return
        if data.startswith("a:prod:"):
            await admin_prod_view(update, context, int(data.split(":")[2]))
            return

        # product edit
        if data.startswith("a:prod_edit_name:"):
            pid = int(data.split(":")[2])
            set_state(context, "PROD_EDIT_NAME", {"pid": pid})
            await q.answer()
            await q.message.reply_text("Send new <b>Product name</b>:", parse_mode=ParseMode.HTML)
            return

        if data.startswith("a:prod_edit_price:"):
            pid = int(data.split(":")[2])
            set_state(context, "PROD_EDIT_PRICE", {"pid": pid})
            await q.answer()
            await q.message.reply_text("Send new <b>Product price</b>:", parse_mode=ParseMode.HTML)
            return

        if data.startswith("a:prod_edit_desc:"):
            pid = int(data.split(":")[2])
            set_state(context, "PROD_EDIT_DESC", {"pid": pid})
            await q.answer()
            await q.message.reply_text("Send new <b>Product description</b> (or 'none' to clear):", parse_mode=ParseMode.HTML)
            return

        if data.startswith("a:prod_edit_link:"):
            pid = int(data.split(":")[2])
            set_state(context, "PROD_EDIT_LINK", {"pid": pid})
            await q.answer()
            await q.message.reply_text("Send public channel link (example: @mychannel or https://t.me/mychannel) :", parse_mode=ParseMode.HTML)
            return

        if data.startswith("a:keys_add:"):
            pid = int(data.split(":")[2])
            set_state(context, "KEYS_ADD", {"pid": pid})
            await q.answer()
            await q.message.reply_text("Send keys now (multiple lines). Each line = 1 stock.\n\nWhen done send: <b>DONE</b>", parse_mode=ParseMode.HTML)
            return

        if data == "a:broadcast":
            await admin_broadcast(update, context)
            return

        if data == "a:refpct":
            await admin_refpct(update, context)
            return

        # Ticket reply button
        if data.startswith("a:ticket:"):
            _, _, shop_id, user_id = data.split(":")
            set_state(context, "TICKET_REPLY", {"shop_id": int(shop_id), "user_id": int(user_id)})
            await q.answer()
            await q.message.reply_text("Send reply message now:", parse_mode=ParseMode.HTML)
            return

        # Super admin routes
        if data == "su:sellers":
            await super_sellers(update, context)
            return
        if data.startswith("su:seller:"):
            await super_seller_view(update, context, int(data.split(":")[2]))
            return
        if data.startswith("su:ban_shop:"):
            _, _, sid2, flag = data.split(":")
            super_set_seller_flag(int(sid2), "banned_shop", int(flag))
            await q.answer("Updated", show_alert=False)
            await super_seller_view(update, context, int(sid2))
            return
        if data.startswith("su:ban_panel:"):
            _, _, sid2, flag = data.split(":")
            super_set_seller_flag(int(sid2), "banned_panel", int(flag))
            await q.answer("Updated", show_alert=False)
            await super_seller_view(update, context, int(sid2))
            return
        if data.startswith("su:restrict:"):
            _, _, sid2, days = data.split(":")
            super_restrict_seller(int(sid2), int(days))
            await q.answer("Restricted", show_alert=False)
            await super_seller_view(update, context, int(sid2))
            return
        if data.startswith("su:users:"):
            _, _, sid2, off = data.split(":")
            await super_seller_users(update, context, int(sid2), int(off))
            return
        if data.startswith("su:user:"):
            # optional: open user view inside seller shop context (reuse admin_user_view by temporarily switching sid)
            await q.answer("Use seller bot to manage users.", show_alert=True)
            return

        # Deposit approve/reject callbacks handled in Part 2/3
        # If unknown:
        await q.answer()

    # =========================
    # MESSAGE HANDLER (states)
    # =========================
    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        sid = current_shop_id()
        uid = update.effective_user.id
        upsert_user(update.effective_user)
        ensure_balance(sid, uid)

        state, data = get_state(context)

        # Support draft appending
        if state == "SUPPORT_DRAFT":
            txt = (update.message.text or "").strip()
            if not txt:
                return
            cur = (data.get("draft") or "")
            newv = (cur + ("\n" if cur else "") + txt).strip()
            set_state(context, "SUPPORT_DRAFT", {"draft": newv})
            await update.message.reply_text("✅ Added. Press <b>Done</b> when ready.", parse_mode=ParseMode.HTML)
            return

        # Deposit amount
        if state == "DEP_AMOUNT":
            amt = parse_float(update.message.text or "")
            if amt is None or amt <= 0:
                await update.message.reply_text("Send a valid amount (example: 50)")
                return
            set_state(context, "DEP_PROOF", {"amount": float(amt), "method_id": int(data.get("method_id", 0))})
            await update.message.reply_text("Now upload a <b>PHOTO proof</b>.", parse_mode=ParseMode.HTML)
            return

        # Deposit proof photo
        if state == "DEP_PROOF":
            if not update.message.photo:
                await update.message.reply_text("❌ Please send a PHOTO proof.")
                return
            proof = update.message.photo[-1].file_id
            amount = float(data.get("amount", 0))
            method_id = int(data.get("method_id", 0))
            clear_state(context)

            # Create pending deposit request + notify admin/owner
            conn = db(); cur = conn.cursor()
            cur.execute("""
                INSERT INTO deposit_requests(shop_owner_id,user_id,amount,method_id,proof_file_id,status,created_at)
                VALUES(?,?,?,?,?,'pending',?)
            """, (sid, uid, amount, method_id, proof, ts()))
            dep_id = int(cur.lastrowid)
            conn.commit(); conn.close()

            await update.message.reply_text("✅ Deposit submitted. Waiting for approval.", parse_mode=ParseMode.HTML)

            # notify owner/admin: seller owner for seller bot; super admin for master
            owner = sid if bot_kind == "seller" else SUPER_ADMIN_ID
            try:
                cap = (
                    f"💰 <b>Deposit Request</b>\n"
                    f"Shop: <b>{esc(user_display(sid))}</b>\n"
                    f"User: {esc(user_display(uid))} (ID: <code>{uid}</code>)\n"
                    f"Amount: <b>{money(amount)} {esc(CURRENCY)}</b>\n"
                    f"Request ID: <code>{dep_id}</code>"
                )
                m = await context.bot.send_photo(
                    owner,
                    photo=proof,
                    caption=cap,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [InlineKeyboardButton("✅ Approve", callback_data=f"d:ok:{dep_id}"),
                         InlineKeyboardButton("❌ Reject", callback_data=f"d:no:{dep_id}")]
                    ])
                )
                # Save admin message to delete later
                conn = db(); cur = conn.cursor()
                cur.execute("UPDATE deposit_requests SET admin_chat_id=?, admin_msg_id=? WHERE id=?", (int(owner), int(m.message_id), dep_id))
                conn.commit(); conn.close()
            except Exception:
                pass
            return

        # Admin: user search
        if state == "ADMIN_USER_SEARCH":
            query = (update.message.text or "").strip()
            clear_state(context)
            fake_update = Update(update.update_id, callback_query=update.callback_query)
            # easiest: just send a new list using admin_users via reply
            # We'll store query in user_data and trigger by sending an inline instruction
            context.user_data["last_user_search"] = query
            await update.message.reply_text("Searching...", parse_mode=ParseMode.HTML)
            # show results by calling admin_users with query via a helper in Part 2/3 (handled there)
            context.user_data["__pending_admin_search__"] = query
            return

        # Admin: balance add/deduct
        if state in ("BAL_ADD", "BAL_DEDUCT"):
            amt = parse_float(update.message.text or "")
            if amt is None or amt <= 0:
                await update.message.reply_text("Send a valid amount (example: 10)")
                return
            target = int(data.get("target", 0))
            if state == "BAL_ADD":
                add_balance(sid, target, amt)
                log_tx(sid, target, "balance_edit", amt, f"Added by {user_display(uid)}")
                await update.message.reply_text("✅ Added.")
            else:
                add_balance(sid, target, -amt)
                log_tx(sid, target, "balance_edit", -amt, f"Deducted by {user_display(uid)}")
                await update.message.reply_text("✅ Deducted.")
            clear_state(context)
            return

        # Admin: welcome text
        if state == "WELCOME_TEXT":
            text = (update.message.text or "").strip()
            if not text:
                await update.message.reply_text("Send some text.")
                return
            set_shop_setting(sid, "welcome_text", text)
            set_state(context, "WELCOME_MEDIA", {})
            await update.message.reply_text("✅ Welcome text updated.\n(Optional) now send a photo/video to set media, or type DONE to finish.")
            return

        if state == "WELCOME_MEDIA":
            if update.message.text and update.message.text.strip().upper() == "DONE":
                clear_state(context)
                await update.message.reply_text("✅ Done.")
                return
            if update.message.photo:
                fid = update.message.photo[-1].file_id
                set_shop_setting(sid, "welcome_file_id", fid)
                set_shop_setting(sid, "welcome_file_type", "photo")
                clear_state(context)
                await update.message.reply_text("✅ Welcome photo set.")
                return
            if update.message.video:
                fid = update.message.video.file_id
                set_shop_setting(sid, "welcome_file_id", fid)
                set_shop_setting(sid, "welcome_file_type", "video")
                clear_state(context)
                await update.message.reply_text("✅ Welcome video set.")
                return
            await update.message.reply_text("Send PHOTO/VIDEO or DONE.")
            return

        # Admin: wallet add flow
        if state == "WALLET_ADD_TITLE":
            title = (update.message.text or "").strip()
            if not title:
                await update.message.reply_text("Send a title.")
                return
            set_state(context, "WALLET_ADD_INSTR", {"title": title})
            await update.message.reply_text("Send instructions text now:")
            return

        if state == "WALLET_ADD_INSTR":
            instr = (update.message.text or "").strip()
            if not instr:
                await update.message.reply_text("Send instructions.")
                return
            title = data.get("title", "Deposit")
            add_wallet_method(sid, title, instr, "")
            clear_state(context)
            await update.message.reply_text("✅ Deposit method added.")
            return

        # Admin: catalog add/edit flows
        if state == "CAT_ADD":
            name = (update.message.text or "").strip()
            if not name:
                await update.message.reply_text("Send a name.")
                return
            conn = db(); cur = conn.cursor()
            cur.execute("INSERT INTO categories(shop_owner_id,name) VALUES(?,?)", (sid, name))
            conn.commit(); conn.close()
            clear_state(context)
            await update.message.reply_text("✅ Category added.")
            return

        if state == "SUB_ADD":
            name = (update.message.text or "").strip()
            if not name:
                await update.message.reply_text("Send a name.")
                return
            cat_id = int(data.get("cat_id", 0))
            conn = db(); cur = conn.cursor()
            cur.execute("INSERT INTO cocategories(shop_owner_id,category_id,name) VALUES(?,?,?)", (sid, cat_id, name))
            conn.commit(); conn.close()
            clear_state(context)
            await update.message.reply_text("✅ Sub-Category added.")
            return

        if state == "PROD_ADD_NAME":
            name = (update.message.text or "").strip()
            if not name:
                await update.message.reply_text("Send a name.")
                return
            set_state(context, "PROD_ADD_PRICE", {"name": name, "cat_id": int(data["cat_id"]), "sub_id": int(data["sub_id"])})
            await update.message.reply_text("Send price (example: 10)")
            return

        if state == "PROD_ADD_PRICE":
            price = parse_float(update.message.text or "")
            if price is None or price <= 0:
                await update.message.reply_text("Send valid price (example: 10)")
                return
            name = data["name"]
            cat_id = int(data["cat_id"])
            sub_id = int(data["sub_id"])
            conn = db(); cur = conn.cursor()
            cur.execute("""
                INSERT INTO products(shop_owner_id,category_id,cocategory_id,name,price,description,tg_link)
                VALUES(?,?,?,?,?,?,?)
            """, (sid, cat_id, sub_id, name, float(price), "", ""))
            conn.commit(); conn.close()
            clear_state(context)
            await update.message.reply_text("✅ Product added.")
            return

        if state == "PROD_EDIT_NAME":
            newname = (update.message.text or "").strip()
            if not newname:
                await update.message.reply_text("Send a name.")
                return
            pid = int(data["pid"])
            conn = db(); cur = conn.cursor()
            cur.execute("UPDATE products SET name=? WHERE shop_owner_id=? AND id=?", (newname, sid, pid))
            conn.commit(); conn.close()
            clear_state(context)
            await update.message.reply_text("✅ Updated.")
            return

        if state == "PROD_EDIT_PRICE":
            newp = parse_float(update.message.text or "")
            if newp is None or newp <= 0:
                await update.message.reply_text("Send valid price.")
                return
            pid = int(data["pid"])
            conn = db(); cur = conn.cursor()
            cur.execute("UPDATE products SET price=? WHERE shop_owner_id=? AND id=?", (float(newp), sid, pid))
            conn.commit(); conn.close()
            clear_state(context)
            await update.message.reply_text("✅ Updated.")
            return

        if state == "PROD_EDIT_DESC":
            txt = (update.message.text or "").strip()
            if txt.lower() == "none":
                txt = ""
            pid = int(data["pid"])
            conn = db(); cur = conn.cursor()
            cur.execute("UPDATE products SET description=? WHERE shop_owner_id=? AND id=?", (txt, sid, pid))
            conn.commit(); conn.close()
            clear_state(context)
            await update.message.reply_text("✅ Updated.")
            return

        if state == "PROD_EDIT_LINK":
            txt = (update.message.text or "").strip()
            pid = int(data["pid"])
            conn = db(); cur = conn.cursor()
            cur.execute("UPDATE products SET tg_link=? WHERE shop_owner_id=? AND id=?", (txt, sid, pid))
            conn.commit(); conn.close()
            clear_state(context)
            await update.message.reply_text("✅ Updated.")
            return

        if state == "KEYS_ADD":
            txt = (update.message.text or "")
            if txt.strip().upper() == "DONE":
                clear_state(context)
                await update.message.reply_text("✅ Done adding keys.")
                return
            pid = int(data["pid"])
            # allow multi-line keys
            lines = txt.splitlines()
            n = add_keys(sid, pid, lines)
            await update.message.reply_text(f"✅ Added {n} keys. Send more or DONE.")
            return

        # Admin: referral percent
        if state == "REF_PCT":
            pct = parse_float(update.message.text or "")
            if pct is None or pct < 0:
                await update.message.reply_text("Send valid percent (example: 5)")
                return
            set_shop_setting(sid, "referral_percent", float(pct))
            clear_state(context)
            await update.message.reply_text("✅ Referral percent updated.")
            return

        # Broadcast
        if state == "BROADCAST":
            clear_state(context)
            user_ids = []
            conn = db(); cur = conn.cursor()
            cur.execute("SELECT user_id FROM balances WHERE shop_owner_id=?", (sid,))
            user_ids = [int(x["user_id"]) for x in cur.fetchall()]
            conn.close()

            sent = 0
            # send text/photo/video
            try:
                if update.message.photo:
                    fid = update.message.photo[-1].file_id
                    cap = update.message.caption or ""
                    for u in user_ids:
                        try:
                            await context.bot.send_photo(u, photo=fid, caption=cap, parse_mode=ParseMode.HTML)
                            sent += 1
                        except Exception:
                            pass
                elif update.message.video:
                    fid = update.message.video.file_id
                    cap = update.message.caption or ""
                    for u in user_ids:
                        try:
                            await context.bot.send_video(u, video=fid, caption=cap, parse_mode=ParseMode.HTML)
                            sent += 1
                        except Exception:
                            pass
                else:
                    txt = update.message.text or ""
                    for u in user_ids:
                        try:
                            await context.bot.send_message(u, txt, parse_mode=ParseMode.HTML)
                            sent += 1
                        except Exception:
                            pass
            except Exception:
                pass
            await update.message.reply_text(f"✅ Broadcast sent to {sent} users.")
            return

        # Ticket reply
        if state == "TICKET_REPLY":
            msg = (update.message.text or "").strip()
            if not msg:
                await update.message.reply_text("Send reply text.")
                return
            shop_id = int(data["shop_id"])
            user_id = int(data["user_id"])
            # create open ticket if needed
            tid = get_open_ticket(shop_id, user_id)
            if not tid:
                tid = create_ticket(shop_id, user_id)
            add_ticket_msg(tid, uid, msg)
            clear_state(context)
            await update.message.reply_text("✅ Replied.")
            try:
                await context.bot.send_message(user_id, f"🆘 <b>Support Reply</b>\n\n{esc(msg)}", parse_mode=ParseMode.HTML)
            except Exception:
                pass
            return

        # CONNECT TOKEN flow
        if state == "CONNECT_TOKEN":
            token = (update.message.text or "").strip()
            plan = data.get("plan", "branded")
            price = float(data.get("price", PLAN_A_PRICE))

            # Basic token format check
            if ":" not in token or len(token) < 30:
                await update.message.reply_text("❌ Invalid token. Send correct token from @BotFather.")
                return

            # Deduct from MAIN SHOP balance
            bal = get_balance(SUPER_ADMIN_ID, uid)
            if bal < price:
                clear_state(context)
                await update.message.reply_text("❌ Not enough balance anymore. Deposit and try again.")
                return
            set_balance(SUPER_ADMIN_ID, uid, bal - price)
            log_tx(SUPER_ADMIN_ID, uid, "plan", -price, f"Connect bot plan={plan}")

            # Save seller + plan + days, start bot
            ensure_seller(uid)
            seller_set_plan(uid, plan)
            seller_add_days(uid, PLAN_DAYS)

            # Fetch bot username via getMe
            username = ""
            try:
                tmp_app = Application.builder().token(token).build()
                await tmp_app.initialize()
                me = await tmp_app.bot.get_me()
                username = (me.username or "")
                await tmp_app.shutdown()
            except Exception:
                username = ""

            upsert_seller_bot(uid, token, username)

            # Start seller bot now
            try:
                await MANAGER.start_seller_bot(uid, token)
            except Exception:
                pass

            clear_state(context)
            await update.message.reply_text(
                f"✅ Bot connected!\nPlan: {plan}\nDays added: {PLAN_DAYS}\nBot: @{username}" if username else f"✅ Bot connected!\nPlan: {plan}\nDays added: {PLAN_DAYS}",
                parse_mode=ParseMode.HTML
            )
            return

        # Otherwise ignore random messages
        return

    # Deposit approve/reject callback
    async def deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        uid = update.effective_user.id
        sid = current_shop_id()

        if not can_admin(uid) and not is_super(uid) and not (bot_kind == "seller" and uid == sid):
            await q.answer("No access", show_alert=True)
            return

        data = (q.data or "")
        if not (data.startswith("d:ok:") or data.startswith("d:no:")):
            return

        dep_id = int(data.split(":")[2])
        conn = db(); cur = conn.cursor()
        cur.execute("SELECT * FROM deposit_requests WHERE id=?", (dep_id,))
        dep = cur.fetchone()
        if not dep:
            conn.close()
            await q.answer("Not found", show_alert=True)
            return
        if dep["status"] != "pending":
            conn.close()
            await q.answer("Already handled", show_alert=True)
            return

        action = "approved" if data.startswith("d:ok:") else "rejected"
        cur.execute("UPDATE deposit_requests SET status=?, handled_by=?, handled_at=? WHERE id=?",
                    (action, uid, ts(), dep_id))
        conn.commit(); conn.close()

        # delete admin message
        try:
            await safe_delete(context.bot, int(dep["admin_chat_id"] or 0), int(dep["admin_msg_id"] or 0))
        except Exception:
            pass

        if action == "approved":
            amount = float(dep["amount"] or 0)
            user_id = int(dep["user_id"])
            shop_id = int(dep["shop_owner_id"])

            # credit depositor full amount
            add_balance(shop_id, user_id, amount)
            log_tx(shop_id, user_id, "deposit", amount, "deposit_ok")

            # referral reward
            sset = get_shop_settings(shop_id)
            pct = float(sset["referral_percent"] or 0)
            referrer = get_referrer(shop_id, user_id)
            if referrer and pct > 0:
                reward = (pct / 100.0) * amount
                if reward > 0:
                    add_balance(shop_id, referrer, reward)
                    log_tx(shop_id, referrer, "ref_reward", reward, f"From {user_display(user_id)}")

            await q.answer("Approved ✅", show_alert=False)
            try:
                await context.bot.send_message(user_id, f"✅ Deposit approved: <b>{money(amount)} {esc(CURRENCY)}</b>", parse_mode=ParseMode.HTML)
            except Exception:
                pass
        else:
            await q.answer("Rejected ❌", show_alert=False)
            try:
                await context.bot.send_message(int(dep["user_id"]), "❌ Deposit rejected.", parse_mode=ParseMode.HTML)
            except Exception:
                pass

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(deposit_callback, pattern=r"^d:(ok|no):"))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))


# =========================
# MAIN APP START (master bot)
# =========================
async def main():
    init_db()

    # Start seller bots from DB
    for r in list_enabled_seller_bots():
        sid = int(r["seller_id"])
        token = (r["bot_token"] or "").strip()
        if token and seller_active(sid) and int((seller_row(sid)["banned_shop"] or 0)) == 0:
            try:
                await MANAGER.start_seller_bot(sid, token)
            except Exception:
                pass

    app = Application.builder().token(BOT_TOKEN).build()
    register_handlers(app, shop_owner_id=SUPER_ADMIN_ID, bot_kind="master")

    await app.initialize()
    await app.start()
    asyncio.create_task(watchdog())
    await app.updater.start_polling(drop_pending_updates=True)
    await app.updater.idle()


if __name__ == "__main__":
    asyncio.run(main())


# write file
out_path = Path("/mnt/data/main.py")
out_path.write_text(main, encoding="utf-8")
str(out_path)


✅ Done — I’ll resend **FULL `main.py` in 3 parts** right now (so your connection won’t break it).

### How to use
1) Create a file named **main.py**  
2) Paste **Part 1**, then **Part 2**, then **Part 3** (in order)  
3) Save and deploy

---

## ✅ PART 1 / 3

```python
# main.py — AutoPanel (Master Shop + Connected Seller Bots) — FULL FEATURES
# python-telegram-bot==20.8  |  SQLite  |  Railway-ready

import os
import time
import re
import secrets
import asyncio
import sqlite3
import logging
from typing import Optional, Dict, Any, List, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# ENV VARS (Railway Variables)
# =========================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
SUPER_ADMIN_ID = int((os.getenv("SUPER_ADMIN_ID") or os.getenv("ADMIN_ID") or "0").strip() or "0")

DB_FILE = (os.getenv("DB_FILE") or "data.db").strip()
STORE_NAME = (os.getenv("STORE_NAME") or "AutoPanel").strip()
CURRENCY = (os.getenv("CURRENCY") or "USDT").strip()

PLAN_A_PRICE = float((os.getenv("PLAN_A_PRICE") or "5").strip() or "5")   # Branded
PLAN_B_PRICE = float((os.getenv("PLAN_B_PRICE") or "10").strip() or "10") # White-label
PLAN_DAYS = int((os.getenv("PLAN_DAYS") or "30").strip() or "30")

MASTER_BOT_USERNAME = (os.getenv("MASTER_BOT_USERNAME") or "").strip().lstrip("@")

BRAND_LINE = "Bot made by @RekkoOwn"
MAIN_CREATED_LINE = "Bot created by @RekkoOwn"
PUBLIC_GROUP_LINE = "Group Chat : @AutoPanels"

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN")
if SUPER_ADMIN_ID <= 0:
    raise RuntimeError("Missing SUPER_ADMIN_ID / ADMIN_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("autopanel")

# =========================
# UTIL
# =========================
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

async def safe_delete(bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass

def parse_float(text: str) -> Optional[float]:
    try:
        return float(text.strip().replace(",", ""))
    except Exception:
        return None

def is_super(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID

def gen_order_id() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "OD" + "".join(secrets.choice(alphabet) for _ in range(10))

def parse_channel_username(link: str) -> Optional[str]:
    link = (link or "").strip()
    if not link:
        return None
    m = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]{5,})", link)
    if m:
        return m.group(1)
    if link.startswith("@") and len(link) > 2:
        return link[1:]
    return None

# =========================
# DB
# =========================
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        last_name TEXT DEFAULT '',
        last_seen INTEGER DEFAULT 0
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sellers(
        seller_id INTEGER PRIMARY KEY,
        sub_until INTEGER DEFAULT 0,
        plan TEXT DEFAULT 'branded', -- branded/whitelabel
        banned_shop INTEGER DEFAULT 0,
        banned_panel INTEGER DEFAULT 0,
        restricted_until INTEGER DEFAULT 0
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS seller_bots(
        seller_id INTEGER PRIMARY KEY,
        bot_token TEXT NOT NULL,
        bot_username TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS shop_settings(
        shop_owner_id INTEGER PRIMARY KEY,
        wallet_message TEXT DEFAULT '',
        welcome_text TEXT DEFAULT '',
        welcome_file_id TEXT DEFAULT '',
        welcome_file_type TEXT DEFAULT '', -- photo/video
        connect_desc TEXT DEFAULT '',
        referral_percent REAL DEFAULT 0
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS wallet_methods(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        instructions TEXT NOT NULL,
        qr_file_id TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS balances(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        balance REAL DEFAULT 0,
        PRIMARY KEY(shop_owner_id, user_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_bans(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        banned INTEGER DEFAULT 0,
        restricted_until INTEGER DEFAULT 0,
        PRIMARY KEY(shop_owner_id, user_id)
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        file_type TEXT DEFAULT ''
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cocategories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        category_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        file_type TEXT DEFAULT ''
    )""")

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
        tg_link TEXT DEFAULT '' -- PUBLIC channel link for join-gate
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_keys(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        key_line TEXT NOT NULL,
        delivered_once INTEGER DEFAULT 0,
        delivered_to INTEGER DEFAULT 0,
        delivered_at INTEGER DEFAULT 0
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        order_id TEXT NOT NULL,
        product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        qty INTEGER NOT NULL,
        total REAL NOT NULL,
        created_at INTEGER NOT NULL
    )""")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_unique ON orders(shop_owner_id, order_id)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS order_keys(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        order_id TEXT NOT NULL,
        key_line TEXT NOT NULL
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_order_keys_lookup ON order_keys(shop_owner_id, order_id)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposit_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        method_id INTEGER DEFAULT 0,
        proof_file_id TEXT NOT NULL,
        status TEXT NOT NULL, -- pending/approved/rejected
        created_at INTEGER NOT NULL,
        handled_by INTEGER DEFAULT 0,
        handled_at INTEGER DEFAULT 0,
        admin_chat_id INTEGER DEFAULT 0,
        admin_msg_id INTEGER DEFAULT 0
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL, -- open/closed
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ticket_messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL, -- deposit/purchase/balance_edit/plan/ref_reward
        amount REAL DEFAULT 0,
        note TEXT DEFAULT '',
        qty INTEGER DEFAULT 1,
        created_at INTEGER NOT NULL
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS referrals(
        shop_owner_id INTEGER NOT NULL,
        referred_id INTEGER NOT NULL,
        referrer_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(shop_owner_id, referred_id)
    )""")

    conn.commit()
    conn.close()

    ensure_shop_settings(SUPER_ADMIN_ID)
    s = get_shop_settings(SUPER_ADMIN_ID)
    if not (s["welcome_text"] or "").strip():
        set_shop_setting(
            SUPER_ADMIN_ID,
            "welcome_text",
            f"✅ Welcome to <b>{esc(STORE_NAME)}</b>\n"
            f"Get your 24/7 Store Panel Here !!\n\n"
            f"{MAIN_CREATED_LINE}\n{PUBLIC_GROUP_LINE}"
        )
    if not (s["connect_desc"] or "").strip():
        set_shop_setting(
            SUPER_ADMIN_ID,
            "connect_desc",
            "🤖 <b>Connect My Bot</b>\n\n"
            "Create your own bot at @BotFather, then connect your token here.\n"
            "Deposit to Main Shop wallet first.\n\n"
            f"Plan A: <b>{money(PLAN_A_PRICE)} {esc(CURRENCY)}</b> / {PLAN_DAYS} days (Branded welcome)\n"
            f"Plan B: <b>{money(PLAN_B_PRICE)} {esc(CURRENCY)}</b> / {PLAN_DAYS} days (White-Label)\n"
        )

def ensure_shop_settings(shop_owner_id: int):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    if not cur.fetchone():
        cur.execute("""
        INSERT INTO shop_settings(shop_owner_id,wallet_message,welcome_text,welcome_file_id,welcome_file_type,connect_desc,referral_percent)
        VALUES(?,?,?,?,?,?,?)
        """, (shop_owner_id, "", "", "", "", "", 0.0))
        conn.commit()
    conn.close()

def get_shop_settings(shop_owner_id: int) -> sqlite3.Row:
    ensure_shop_settings(shop_owner_id)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    r = cur.fetchone()
    conn.close()
    return r

def set_shop_setting(shop_owner_id: int, field: str, value: Any):
    ensure_shop_settings(shop_owner_id)
    conn = db(); cur = conn.cursor()
    cur.execute(f"UPDATE shop_settings SET {field}=? WHERE shop_owner_id=?", (value, shop_owner_id))
    conn.commit()
    conn.close()

def upsert_user(u):
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO users(user_id, username, first_name, last_name, last_seen) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, "
        "last_name=excluded.last_name, last_seen=excluded.last_seen",
        (u.id, u.username or "", u.first_name or "", u.last_name or "", ts())
    )
    conn.commit(); conn.close()

def user_row(uid: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    conn.close()
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

def ensure_balance(shop_owner_id: int, uid: int):
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,0)", (shop_owner_id, uid))
    conn.commit(); conn.close()

def get_balance(shop_owner_id: int, uid: int) -> float:
    ensure_balance(shop_owner_id, uid)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT balance FROM balances WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    r = cur.fetchone()
    conn.close()
    return float(r["balance"] or 0) if r else 0.0

def set_balance(shop_owner_id: int, uid: int, val: float):
    val = max(0.0, float(val))
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,?) "
        "ON CONFLICT(shop_owner_id, user_id) DO UPDATE SET balance=excluded.balance",
        (shop_owner_id, uid, val)
    )
    conn.commit(); conn.close()

def add_balance(shop_owner_id: int, uid: int, delta: float) -> float:
    v = get_balance(shop_owner_id, uid) + float(delta)
    if v < 0:
        v = 0.0
    set_balance(shop_owner_id, uid, v)
    return v

def log_tx(shop_owner_id: int, uid: int, kind: str, amount: float, note: str = "", qty: int = 1):
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions(shop_owner_id,user_id,kind,amount,note,qty,created_at) VALUES(?,?,?,?,?,?,?)",
        (shop_owner_id, uid, kind, float(amount), note or "", int(qty or 1), ts())
    )
    conn.commit(); conn.close()

def list_tx(shop_owner_id: int, uid: int, limit: int = 30) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "SELECT * FROM transactions WHERE shop_owner_id=? AND user_id=? ORDER BY id DESC LIMIT ?",
        (shop_owner_id, uid, int(limit))
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def is_banned_user(shop_owner_id: int, uid: int) -> bool:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT banned, restricted_until FROM user_bans WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    r = cur.fetchone()
    conn.close()
    if not r:
        return False
    if int(r["banned"] or 0) == 1:
        return True
    if int(r["restricted_until"] or 0) > ts():
        return True
    return False

def ban_user(shop_owner_id: int, uid: int, banned: int):
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_bans(shop_owner_id,user_id,banned,restricted_until) VALUES(?,?,?,0) "
        "ON CONFLICT(shop_owner_id,user_id) DO UPDATE SET banned=excluded.banned",
        (shop_owner_id, uid, int(banned))
    )
    conn.commit(); conn.close()

def restrict_user(shop_owner_id: int, uid: int, days: int):
    until = ts() + max(0, int(days)) * 86400
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_bans(shop_owner_id,user_id,banned,restricted_until) VALUES(?,?,0,?) "
        "ON CONFLICT(shop_owner_id,user_id) DO UPDATE SET restricted_until=excluded.restricted_until, banned=0",
        (shop_owner_id, uid, until)
    )
    conn.commit(); conn.close()

def ensure_seller(seller_id: int):
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO sellers(seller_id, sub_until, plan) VALUES(?,?,?)", (seller_id, 0, "branded"))
    conn.commit(); conn.close()

def seller_row(seller_id: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM sellers WHERE seller_id=?", (seller_id,))
    r = cur.fetchone()
    conn.close()
    return r

def seller_plan(seller_id: int) -> str:
    if is_super(seller_id):
        return "whitelabel"
    r = seller_row(seller_id)
    return (r["plan"] if r else "branded") or "branded"

def seller_set_plan(seller_id: int, plan: str):
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

def seller_add_days(seller_id: int, days: int):
    ensure_seller(seller_id)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT sub_until FROM sellers WHERE seller_id=?", (seller_id,))
    r = cur.fetchone()
    base = max(int(r["sub_until"] or 0), ts())
    cur.execute("UPDATE sellers SET sub_until=? WHERE seller_id=?", (base + int(days) * 86400, seller_id))
    conn.commit(); conn.close()

def super_set_seller_flag(seller_id: int, field: str, val: int):
    ensure_seller(seller_id)
    conn = db(); cur = conn.cursor()
    cur.execute(f"UPDATE sellers SET {field}=? WHERE seller_id=?", (int(val), seller_id))
    conn.commit(); conn.close()

def super_restrict_seller(seller_id: int, days: int):
    ensure_seller(seller_id)
    until = ts() + max(0, int(days)) * 86400
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE sellers SET restricted_until=? WHERE seller_id=?", (until, seller_id))
    conn.commit(); conn.close()

def list_sellers_only() -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("""
        SELECT s.* FROM sellers s
        WHERE s.sub_until>0 OR EXISTS(SELECT 1 FROM seller_bots b WHERE b.seller_id=s.seller_id)
        ORDER BY s.sub_until DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

def upsert_seller_bot(seller_id: int, token: str, username: str):
    ensure_seller(seller_id)
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO seller_bots(seller_id, bot_token, bot_username, enabled, created_at, updated_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(seller_id) DO UPDATE SET bot_token=excluded.bot_token,
            bot_username=excluded.bot_username, enabled=1, updated_at=excluded.updated_at
    """, (seller_id, token, username, 1, ts(), ts()))
    conn.commit(); conn.close()

def list_enabled_seller_bots() -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM seller_bots WHERE enabled=1")
    rows = cur.fetchall()
    conn.close()
    return rows


def disable_seller_bot(seller_id: int):
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE seller_bots SET enabled=0, updated_at=? WHERE seller_id=?", (ts(), seller_id))
    conn.commit(); conn.close()

# --- wallet methods ---
def list_wallet_methods(shop_owner_id: int) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM wallet_methods WHERE shop_owner_id=? AND enabled=1 ORDER BY id DESC", (shop_owner_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_wallet_method(shop_owner_id: int, mid: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM wallet_methods WHERE shop_owner_id=? AND id=?", (shop_owner_id, mid))
    r = cur.fetchone()
    conn.close()
    return r

def add_wallet_method(shop_owner_id: int, title: str, instructions: str, qr_file_id: str = "") -> int:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO wallet_methods(shop_owner_id,title,instructions,qr_file_id,enabled) VALUES(?,?,?,?,1)",
        (shop_owner_id, title.strip(), instructions.strip(), (qr_file_id or "").strip())
    )
    mid = int(cur.lastrowid)
    conn.commit(); conn.close()
    return mid

def disable_wallet_method(shop_owner_id: int, mid: int):
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE wallet_methods SET enabled=0 WHERE shop_owner_id=? AND id=?", (shop_owner_id, mid))
    conn.commit(); conn.close()

# --- referrals ---
def set_referrer(shop_owner_id: int, referred_id: int, referrer_id: int) -> bool:
    if referred_id == referrer_id:
        return False
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM referrals WHERE shop_owner_id=? AND referred_id=?", (shop_owner_id, referred_id))
    if cur.fetchone():
        conn.close()
        return False
    cur.execute(
        "INSERT INTO referrals(shop_owner_id,referred_id,referrer_id,created_at) VALUES(?,?,?,?)",
        (shop_owner_id, referred_id, referrer_id, ts())
    )
    conn.commit(); conn.close()
    return True

def get_referrer(shop_owner_id: int, referred_id: int) -> Optional[int]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT referrer_id FROM referrals WHERE shop_owner_id=? AND referred_id=?", (shop_owner_id, referred_id))
    r = cur.fetchone()
    conn.close()
    return int(r["referrer_id"]) if r else None

def referral_count(shop_owner_id: int, referrer_id: int) -> int:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(1) c FROM referrals WHERE shop_owner_id=? AND referrer_id=?", (shop_owner_id, referrer_id))
    r = cur.fetchone()
    conn.close()
    return int(r["c"] or 0) if r else 0

# --- catalog ---
def cat_list(shop_owner_id: int) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE shop_owner_id=? ORDER BY id DESC", (shop_owner_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def cocat_list(shop_owner_id: int, cat_id: int) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM cocategories WHERE shop_owner_id=? AND category_id=? ORDER BY id DESC", (shop_owner_id, cat_id))
    rows = cur.fetchall()
    conn.close()
    return rows

def prod_list(shop_owner_id: int, cat_id: int, sub_id: int) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("""
        SELECT * FROM products
        WHERE shop_owner_id=? AND category_id=? AND cocategory_id=?
        ORDER BY id DESC
    """, (shop_owner_id, cat_id, sub_id))
    rows = cur.fetchall()
    conn.close()
    return rows

def prod_get(shop_owner_id: int, pid: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE shop_owner_id=? AND id=?", (shop_owner_id, pid))
    r = cur.fetchone()
    conn.close()
    return r

def stock_count(shop_owner_id: int, pid: int) -> int:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(1) c FROM product_keys WHERE shop_owner_id=? AND product_id=? AND delivered_once=0", (shop_owner_id, pid))
    r = cur.fetchone()
    conn.close()
    return int(r["c"] or 0) if r else 0

def add_keys(shop_owner_id: int, pid: int, lines: List[str]) -> int:
    lines = [l.strip() for l in lines if l.strip()]
    if not lines:
        return 0
    conn = db(); cur = conn.cursor()
    cur.executemany(
        "INSERT INTO product_keys(shop_owner_id,product_id,key_line) VALUES(?,?,?)",
        [(shop_owner_id, pid, l) for l in lines]
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n

def pop_keys(shop_owner_id: int, pid: int, uid: int, qty: int) -> List[str]:
    conn = db(); cur = conn.cursor()
    cur.execute("""
        SELECT id, key_line FROM product_keys
        WHERE shop_owner_id=? AND product_id=? AND delivered_once=0
        ORDER BY id ASC LIMIT ?
    """, (shop_owner_id, pid, int(qty)))
    rows = cur.fetchall()
    ids = [int(r["id"]) for r in rows]
    keys = [r["key_line"] for r in rows]
    if ids:
        placeholders = ",".join(["?"] * len(ids))
        cur.execute(
            f"UPDATE product_keys SET delivered_once=1, delivered_to=?, delivered_at=? WHERE id IN ({placeholders})",
            (uid, ts(), *ids)
        )
    conn.commit(); conn.close()
    return keys

# --- orders ---
def save_order(shop_owner_id: int, user_id: int, order_id: str, product_id: int, product_name: str, qty: int, total: float, keys: List[str]):
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO orders(shop_owner_id,user_id,order_id,product_id,product_name,qty,total,created_at)
        VALUES(?,?,?,?,?,?,?,?)
    """, (shop_owner_id, user_id, order_id, product_id, product_name, int(qty), float(total), ts()))
    if keys:
        cur.executemany(
            "INSERT INTO order_keys(shop_owner_id,order_id,key_line) VALUES(?,?,?)",
            [(shop_owner_id, order_id, k) for k in keys]
        )
    conn.commit(); conn.close()

def list_orders_for_user(shop_owner_id: int, user_id: int, limit: int = 30) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "SELECT * FROM orders WHERE shop_owner_id=? AND user_id=? ORDER BY id DESC LIMIT ?",
        (shop_owner_id, user_id, int(limit))
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def get_order(shop_owner_id: int, order_id: str) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE shop_owner_id=? AND order_id=? LIMIT 1", (shop_owner_id, order_id))
    r = cur.fetchone()
    conn.close()
    return r

def get_order_keys(shop_owner_id: int, order_id: str) -> List[str]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT key_line FROM order_keys WHERE shop_owner_id=? AND order_id=? ORDER BY id ASC", (shop_owner_id, order_id))
    rows = cur.fetchall()
    conn.close()
    return [x["key_line"] for x in rows]

# --- support ---
def get_open_ticket(shop_owner_id: int, user_id: int) -> Optional[int]:
    conn = db(); cur = conn.cursor()
    cur.execute("""
        SELECT id FROM tickets
        WHERE shop_owner_id=? AND user_id=? AND status='open'
        ORDER BY id DESC LIMIT 1
    """, (shop_owner_id, user_id))
    r = cur.fetchone()
    conn.close()
    return int(r["id"]) if r else None

def create_ticket(shop_owner_id: int, user_id: int) -> int:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO tickets(shop_owner_id,user_id,status,created_at,updated_at) VALUES(?,?,?,?,?)",
        (shop_owner_id, user_id, "open", ts(), ts())
    )
    tid = int(cur.lastrowid)
    conn.commit(); conn.close()
    return tid

def add_ticket_msg(ticket_id: int, sender_id: int, text: str):
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticket_messages(ticket_id,sender_id,text,created_at) VALUES(?,?,?,?)",
        (ticket_id, sender_id, text, ts())
    )
    cur.execute("UPDATE tickets SET updated_at=? WHERE id=?", (ts(), ticket_id))
    conn.commit(); conn.close()

# =========================
# BRANDING
# =========================
def render_welcome_text(shop_owner_id: int) -> str:
    s = get_shop_settings(shop_owner_id)
    base = (s["welcome_text"] or "").strip()
    if shop_owner_id == SUPER_ADMIN_ID:
        return base
    if seller_active(shop_owner_id) and seller_plan(shop_owner_id) == "whitelabel":
        return base
    if BRAND_LINE not in base:
        return (base + "\n\n" + BRAND_LINE).strip()
    return base

# =========================
# MENUS
# =========================
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

# =========================
# MULTI-BOT MANAGER
# =========================
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
                if not seller_active(sid) or int((seller_row(sid)["banned_shop"] or 0)) == 1:
                    disable_seller_bot(sid)
                    await MANAGER.stop_seller_bot(sid)
            await asyncio.sleep(60)
        except Exception:
            log.exception("watchdog loop")
            await asyncio.sleep(60)

# =========================
# STATE MACHINE
# =========================
def set_state(context: ContextTypes.DEFAULT_TYPE, key: str, data: Dict[str, Any]):
    context.user_data["state"] = key
    context.user_data["state_data"] = data

def get_state(context: ContextTypes.DEFAULT_TYPE) -> Tuple[Optional[str], Dict[str, Any]]:
    return context.user_data.get("state"), (context.user_data.get("state_data") or {})

def clear_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("state", None)
    context.user_data.pop("state_data", None)

def now_text(t: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
    except Exception:
        return str(t)


# =========================
# NOTIFY
# =========================
async def notify_shop_owner(app: Application, shop_owner_id: int, text: str):
    try:
        await app.bot.send_message(chat_id=shop_owner_id, text=text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception:
        pass

async def notify_super(app: Application, text: str):
    await notify_shop_owner(app, SUPER_ADMIN_ID, text)

# =========================
# JOIN GATE for Get Files
# =========================
async def must_join_channel(bot, user_id: int, public_link: str) -> Tuple[bool, str]:
    # returns (ok, channel_username)
    ch = parse_channel_username(public_link)
    if not ch:
        return True, ""
    try:
        member = await bot.get_chat_member(chat_id=f"@{ch}", user_id=user_id)
        status = member.status
        if status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return True, ch
        return False, ch
    except Exception:
        return False, ch

# =========================
# COMMON UI
# =========================
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    clear_state(context)

    uid = update.effective_user.id
    upsert_user(update.effective_user)

    if is_banned_user(shop_owner_id, uid):
        text = "⛔ You are banned or restricted from this shop."
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text=text)
        else:
            await update.message.reply_text(text=text)
        return

    # Seller bot: users see seller menu; in master bot, always master menu
    is_master_bot = (shop_owner_id == SUPER_ADMIN_ID)

    # In seller bot, if user is the seller (owner) or super, show admin buttons too
    if is_master_bot:
        msg = render_welcome_text(SUPER_ADMIN_ID)
        markup = master_menu(uid)
    else:
        # seller shop users must never see master shop
        msg = render_welcome_text(shop_owner_id)
        markup = seller_menu(uid, seller_id=shop_owner_id)

    # Make main menu same as welcome message (your requirement)
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=markup)
        except Exception:
            await update.callback_query.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=markup)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=markup)

async def delete_prev_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # delete the message that had the button they pressed (clean UI)
    try:
        if update.callback_query:
            await safe_delete(context.bot, update.effective_chat.id, update.callback_query.message.message_id)
    except Exception:
        pass

# =========================
# /start
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    # referral param only for MASTER SHOP
    uid = update.effective_user.id
    upsert_user(update.effective_user)
    ensure_balance(shop_owner_id, uid)

    # record user for admin panels (important)
    # nothing else needed; users table is the list base

    if shop_owner_id == SUPER_ADMIN_ID:
        try:
            if context.args:
                arg = context.args[0].strip()
                if arg.startswith("ref_"):
                    rid = int(arg.replace("ref_", "").strip())
                    if rid > 0 and rid != uid:
                        set_referrer(SUPER_ADMIN_ID, uid, rid)
        except Exception:
            pass

    await show_main_menu(update, context, shop_owner_id)

# =========================
# PRODUCTS FLOW
# Category > Sub-category > Products > Qty + Buy
# =========================
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    cats = cat_list(shop_owner_id)
    buttons = []
    for c in cats[:60]:
        buttons.append(InlineKeyboardButton(f"📁 {c['name']}", callback_data=f"p:cat:{c['id']}"))
    buttons.append(InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu"))
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("📦 <b>Categories</b>", parse_mode=ParseMode.HTML, reply_markup=grid(buttons, 2))

async def show_cocategories(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, cat_id: int):
    subs = cocat_list(shop_owner_id, cat_id)
    buttons = []
    for s in subs[:60]:
        buttons.append(InlineKeyboardButton(f"📂 {s['name']}", callback_data=f"p:sub:{cat_id}:{s['id']}"))
    buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="m:products"))
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("📦 <b>Sub-Categories</b>", parse_mode=ParseMode.HTML, reply_markup=grid(buttons, 2))

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, cat_id: int, sub_id: int):
    prods = prod_list(shop_owner_id, cat_id, sub_id)
    buttons = []
    for p in prods[:60]:
        st = stock_count(shop_owner_id, int(p["id"]))
        price = money(float(p["price"]))
        suffix = "✅" if st > 0 else "❌"
        buttons.append(InlineKeyboardButton(f"{suffix} {p['name']} ({price} {CURRENCY})", callback_data=f"p:prod:{p['id']}"))
    buttons.append(InlineKeyboardButton("⬅️ Back", callback_data=f"p:cat:{cat_id}"))
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("🛒 <b>Products</b>", parse_mode=ParseMode.HTML, reply_markup=grid(buttons, 1))

async def show_product_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, pid: int):
    p = prod_get(shop_owner_id, pid)
    if not p:
        await update.callback_query.answer("Product not found", show_alert=True)
        return
    st = stock_count(shop_owner_id, pid)
    qty = context.user_data.get(f"qty_{shop_owner_id}_{pid}", 1)
    qty = max(1, int(qty))
    price = float(p["price"])
    total = price * qty

    desc = (p["description"] or "").strip()
    text = (
        f"🛒 <b>{esc(p['name'])}</b>\n"
        f"Price: <b>{money(price)} {esc(CURRENCY)}</b>\n"
        f"Stock: <b>{st}</b>\n"
        f"Qty: <b>{qty}</b>\n"
        f"Total: <b>{money(total)} {esc(CURRENCY)}</b>\n"
    )
    if desc:
        text += f"\n{esc(desc)}"

    buttons = [
        InlineKeyboardButton("➖", callback_data=f"p:qty:-:{pid}"),
        InlineKeyboardButton("➕", callback_data=f"p:qty:+:{pid}"),
    ]
    rows = [
        buttons,
        [InlineKeyboardButton("✅ Buy Now", callback_data=f"p:buy:{pid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="m:products")],
    ]

    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

async def adjust_qty(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, sign: str, pid: int):
    key = f"qty_{shop_owner_id}_{pid}"
    qty = int(context.user_data.get(key, 1))
    if sign == "+":
        qty += 1
    else:
        qty -= 1
    qty = max(1, qty)
    context.user_data[key] = qty
    await show_product_detail(update, context, shop_owner_id, pid)

async def do_buy(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, pid: int):
    uid = update.effective_user.id
    p = prod_get(shop_owner_id, pid)
    if not p:
        await update.callback_query.answer("Product not found", show_alert=True)
        return

    st = stock_count(shop_owner_id, pid)
    qty = int(context.user_data.get(f"qty_{shop_owner_id}_{pid}", 1))
    qty = max(1, qty)

    if st < qty:
        await update.callback_query.answer("Out of stock", show_alert=True)
        return

    price = float(p["price"])
    total = price * qty
    bal = get_balance(shop_owner_id, uid)
    if bal < total:
        await update.callback_query.answer("Not enough balance", show_alert=True)
        return

    # deduct
    add_balance(shop_owner_id, uid, -total)

    # referral reward ONLY in MASTER SHOP deposits; purchases do not reward by default
    # deliver keys
    keys = pop_keys(shop_owner_id, pid, uid, qty)
    order_id = gen_order_id()

    save_order(
        shop_owner_id=shop_owner_id,
        user_id=uid,
        order_id=order_id,
        product_id=pid,
        product_name=p["name"],
        qty=qty,
        total=total,
        keys=keys
    )
    log_tx(shop_owner_id, uid, "purchase", -total, f"Order {order_id} - {p['name']}", qty=qty)

    # Notify shop owner
    key_preview = keys[0] if keys else ""
    owner_note = (
        f"🧾 <b>New Order</b>\n"
        f"Shop: <b>{esc('Main Shop' if shop_owner_id==SUPER_ADMIN_ID else user_display(shop_owner_id))}</b>\n"
        f"Order ID: <b>{esc(order_id)}</b>\n"
        f"Buyer: <b>{esc(user_display(uid))}</b> (<code>{uid}</code>)\n"
        f"Product: <b>{esc(p['name'])}</b>\n"
        f"Qty: <b>{qty}</b>\n"
        f"Amount: <b>{money(total)} {esc(CURRENCY)}</b>\n"
        f"Key (first): <code>{esc(key_preview)}</code>\n"
    )
    await notify_shop_owner(context.application, shop_owner_id, owner_note)
    if shop_owner_id != SUPER_ADMIN_ID:
        await notify_super(context.application, owner_note)

    # send user delivery (Key + Get Files gated by join)
    delivered = "\n".join([f"<code>{esc(k)}</code>" for k in keys]) if keys else "<i>No key</i>"
    out = (
        f"✅ <b>Purchase Successful</b>\n"
        f"Order ID: <b>{esc(order_id)}</b>\n"
        f"Product: <b>{esc(p['name'])}</b>\n"
        f"Qty: <b>{qty}</b>\n"
        f"Total: <b>{money(total)} {esc(CURRENCY)}</b>\n\n"
        f"🔑 <b>Keys Delivered</b>\n{delivered}\n"
    )

    rows = []
    rows.append([InlineKeyboardButton("📦 Get Files", callback_data=f"p:getfiles:{pid}:{order_id}")])
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")])

    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(out, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

async def get_files(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, pid: int, order_id: str):
    p = prod_get(shop_owner_id, pid)
    if not p:
        await update.callback_query.answer("Not found", show_alert=True)
        return

    public_link = (p["tg_link"] or "").strip()
    ok, ch = await must_join_channel(context.bot, update.effective_user.id, public_link)
    if not ok:
        # DO NOT show private link. Ask to join.
        btns = [
            [InlineKeyboardButton("✅ Join Channel", url=f"https://t.me/{ch}")],
            [InlineKeyboardButton("🔄 I Joined", callback_data=f"p:getfiles:{pid}:{order_id}")],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")],
        ]
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            "🔒 <b>Join required</b>\n\nPlease join the channel first, then press <b>I Joined</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb(btns)
        )
        return

    # After join check, show message "Get File" (still not exposing private link)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "✅ <b>Access Granted</b>\n\nYou are verified as joined.\n\nNow use the channel pinned / files section to get the content.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")]])
    )

# =========================
# WALLET / DEPOSIT (proof required)
# + extra wallet methods in Admin Panel
# =========================
async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    uid = update.effective_user.id
    bal = get_balance(shop_owner_id, uid)
    s = get_shop_settings(shop_owner_id)

    methods = list_wallet_methods(shop_owner_id)
    text = f"💰 <b>Wallet</b>\nBalance: <b>{money(bal)} {esc(CURRENCY)}</b>\n\n"
    wmsg = (s["wallet_message"] or "").strip()
    if wmsg:
        text += f"{esc(wmsg)}\n\n"
    if methods:
        text += "Select a deposit method:\n"
        btns = []
        for m in methods[:10]:
            btns.append(InlineKeyboardButton(f"➕ Deposit: {m['title']}", callback_data=f"d:method:{m['id']}"))
        btns.append(InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu"))
        await delete_prev_msg(update, context)
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=grid(btns, 1))
        return

    btns = [
        [InlineKeyboardButton("➕ Deposit", callback_data="d:start")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")]
    ]
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb(btns))

async def deposit_method(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, method_id: int):
    m = get_wallet_method(shop_owner_id, method_id)
    if not m:
        await update.callback_query.answer("Method not found", show_alert=True)
        return
    txt = (
        f"➕ <b>Deposit Method</b>\n"
        f"<b>{esc(m['title'])}</b>\n\n"
        f"{esc(m['instructions'])}\n\n"
        f"Send deposit <b>amount</b> now."
    )
    set_state(context, "deposit_amount", {"shop_owner_id": shop_owner_id, "method_id": method_id})
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        txt,
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("⬅️ Cancel", callback_data="m:wallet")]])
    )

async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    set_state(context, "deposit_amount", {"shop_owner_id": shop_owner_id, "method_id": 0})
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "➕ <b>Deposit</b>\nSend deposit <b>amount</b> now.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("⬅️ Cancel", callback_data="m:wallet")]])
    )

async def deposit_amount_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "deposit_amount":
        return
    amt = parse_float(update.message.text or "")
    if amt is None or amt <= 0:
        await update.message.reply_text("❌ Invalid amount. Send a number like 10")
        return
    data["amount"] = float(amt)
    set_state(context, "deposit_proof", data)
    await update.message.reply_text("✅ Now send <b>photo proof</b> of payment.", parse_mode=ParseMode.HTML)

async def deposit_proof_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "deposit_proof":
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Please send a <b>photo</b> proof.", parse_mode=ParseMode.HTML)
        return
    shop_owner_id = int(data["shop_owner_id"])
    method_id = int(data.get("method_id", 0))
    amount = float(data["amount"])
    uid = update.effective_user.id

    file_id = update.message.photo[-1].file_id

    # insert request
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO deposit_requests(shop_owner_id,user_id,amount,method_id,proof_file_id,status,created_at)
        VALUES(?,?,?,?,?,'pending',?)
    """, (shop_owner_id, uid, amount, method_id, file_id, ts()))
    rid = int(cur.lastrowid)
    conn.commit(); conn.close()

    clear_state(context)

    # notify shop owner (seller or super for main shop)
    note = (
        f"🧾 <b>Deposit Request</b>\n"
        f"Shop: <b>{esc('Main Shop' if shop_owner_id==SUPER_ADMIN_ID else user_display(shop_owner_id))}</b>\n"
        f"User: <b>{esc(user_display(uid))}</b> (<code>{uid}</code>)\n"
        f"Amount: <b>{money(amount)} {esc(CURRENCY)}</b>\n"
        f"Request ID: <code>{rid}</code>\n"
        f"Proof attached below.\n"
    )
    try:
        msg = await context.application.bot.send_photo(
            chat_id=shop_owner_id,
            photo=file_id,
            caption=note,
            parse_mode=ParseMode.HTML,
            reply_markup=kb([
                [InlineKeyboardButton("✅ Approve", callback_data=f"dep:approve:{rid}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"dep:reject:{rid}")]
            ])
        )
        # store admin msg ids so we can delete after handled
        conn = db(); cur = conn.cursor()
        cur.execute("UPDATE deposit_requests SET admin_chat_id=?, admin_msg_id=? WHERE id=?", (shop_owner_id, msg.message_id, rid))
        conn.commit(); conn.close()
    except Exception:
        pass

    if shop_owner_id != SUPER_ADMIN_ID:
        # do NOT send seller deposits to super admin (your requirement)
        pass
    else:
        # main shop deposit -> super admin already notified
        pass

    await update.message.reply_text("✅ Deposit sent. Please wait for approval.", reply_markup=kb([[InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")]]))

async def handle_deposit_action(update: Update, context: ContextTypes.DEFAULT_TYPE, approve: bool, rid: int):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM deposit_requests WHERE id=?", (rid,))
    r = cur.fetchone()
    if not r:
        conn.close()
        await update.callback_query.answer("Not found", show_alert=True)
        return
    if r["status"] != "pending":
        conn.close()
        await update.callback_query.answer("Already handled", show_alert=True)
        return

    shop_owner_id = int(r["shop_owner_id"])
    user_id = int(r["user_id"])
    amount = float(r["amount"])
    admin_chat_id = int(r["admin_chat_id"] or 0)
    admin_msg_id = int(r["admin_msg_id"] or 0)

    if approve:
        add_balance(shop_owner_id, user_id, amount)
        log_tx(shop_owner_id, user_id, "deposit", amount, f"Deposited: {money(amount)} {CURRENCY}")
        cur.execute("UPDATE deposit_requests SET status='approved', handled_by=?, handled_at=? WHERE id=?", (update.effective_user.id, ts(), rid))
        conn.commit()
        # referral reward only in MAIN SHOP
        if shop_owner_id == SUPER_ADMIN_ID:
            percent = float(get_shop_settings(SUPER_ADMIN_ID)["referral_percent"] or 0)
            if percent > 0:
                ref = get_referrer(SUPER_ADMIN_ID, user_id)
                if ref:
                    reward = (amount * percent) / 100.0
                    if reward > 0:
                        add_balance(SUPER_ADMIN_ID, ref, reward)
                        log_tx(SUPER_ADMIN_ID, ref, "ref_reward", reward, f"Referral reward {percent}% from {user_display(user_id)} deposit")
                        try:
                            await context.application.bot.send_message(
                                chat_id=ref,
                                text=f"🎁 <b>Referral Reward</b>\nYou earned <b>{money(reward)} {CURRENCY}</b> from {esc(user_display(user_id))} deposit.",
                                parse_mode=ParseMode.HTML
                            )
                        except Exception:
                            pass
    else:
        cur.execute("UPDATE deposit_requests SET status='rejected', handled_by=?, handled_at=? WHERE id=?", (update.effective_user.id, ts(), rid))
        conn.commit()

    conn.close()

    # delete admin approval message
    if admin_chat_id and admin_msg_id:
        await safe_delete(context.application.bot, admin_chat_id, admin_msg_id)

    await update.callback_query.answer("Done")
    try:
        await context.application.bot.send_message(
            chat_id=user_id,
            text=("✅ Deposit approved!" if approve else "❌ Deposit rejected."),
        )
    except Exception:
        pass

# =========================
# HISTORY (clean format + order id + date + key example)
# =========================
async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    uid = update.effective_user.id
    orders = list_orders_for_user(shop_owner_id, uid, limit=20)
    bal = get_balance(shop_owner_id, uid)

    lines = ["📜 <b>History</b>"]
    if not orders:
        lines.append("\nNo purchases yet.")
    else:
        for o in orders:
            oid = o["order_id"]
            dt = now_text(int(o["created_at"]))
            amt = money(float(o["total"]))
            keys = get_order_keys(shop_owner_id, oid)
            kshow = keys[0] if keys else ""
            # show “Key: Example, Key278272”
            lines.append(
                f"\n• <b>{esc(o['product_name'])}</b>\n"
                f"Order ID: <code>{esc(oid)}</code>\n"
                f"Date: <b>{esc(dt)}</b>\n"
                f"Amount: <b>{esc(amt)} {esc(CURRENCY)}</b>\n"
                f"Key: <code>{esc(kshow)}</code>"
            )

    lines.append(f"\n\nTotal Balance: <b>{money(bal)} {esc(CURRENCY)}</b>")
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb([[InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")]]))

# =========================
# SUPPORT INBOX (Draft -> Done)
# =========================
async def support_open(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    uid = update.effective_user.id
    tid = get_open_ticket(shop_owner_id, uid)
    if not tid:
        tid = create_ticket(shop_owner_id, uid)
    set_state(context, "support_draft", {"shop_owner_id": shop_owner_id, "ticket_id": tid, "draft": []})

    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "🆘 <b>Support Inbox</b>\n\nSend your messages now.\nWhen ready, press <b>Done</b> to send.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb([
            [InlineKeyboardButton("✅ Done", callback_data="sup:done")],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")],
        ])
    )

async def support_draft_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "support_draft":
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    draft = data.get("draft", [])
    draft.append(text)
    data["draft"] = draft
    set_state(context, "support_draft", data)
    await update.message.reply_text("✅ Added. Send more or press <b>Done</b>.", parse_mode=ParseMode.HTML)

async def support_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "support_draft":
        await update.callback_query.answer()
        return
    shop_owner_id = int(data["shop_owner_id"])
    tid = int(data["ticket_id"])
    uid = update.effective_user.id
    draft: List[str] = data.get("draft", [])
    if not draft:
        await update.callback_query.answer("Send a message first", show_alert=True)
        return

    full = "\n".join(draft).strip()
    add_ticket_msg(tid, uid, full)
    clear_state(context)

    # notify shop owner + super admin if main shop
    note = (
        f"🆘 <b>New Support Message</b>\n"
        f"Shop: <b>{esc('Main Shop' if shop_owner_id==SUPER_ADMIN_ID else user_display(shop_owner_id))}</b>\n"
        f"From: <b>{esc(user_display(uid))}</b> (<code>{uid}</code>)\n\n"
        f"{esc(full)}"
    )
    await notify_shop_owner(context.application, shop_owner_id, note)
    if shop_owner_id != SUPER_ADMIN_ID:
        await notify_super(context.application, note)

    await update.callback_query.answer()
    await update.callback_query.message.reply_text("✅ Sent to support.", reply_markup=kb([[InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")]]))

# =========================
# CONNECT MY BOT (MASTER SHOP ONLY)
# Plans: $5 branded, $10 whitelabel; upgrade +5; downgrade when renew $5
# =========================
async def connect_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_shop_settings(SUPER_ADMIN_ID)
    desc = (s["connect_desc"] or "").strip()
    if not desc:
        desc = "🤖 Connect My Bot"
    # show options when click
    btns = [
        [InlineKeyboardButton(f"Plan A: {money(PLAN_A_PRICE)} {CURRENCY} / {PLAN_DAYS} days", callback_data="con:plan:A")],
        [InlineKeyboardButton(f"Plan B: {money(PLAN_B_PRICE)} {CURRENCY} / {PLAN_DAYS} days", callback_data="con:plan:B")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")],
    ]
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(desc, parse_mode=ParseMode.HTML, reply_markup=kb(btns), disable_web_page_preview=True)

async def connect_choose(update: Update, context: ContextTypes.DEFAULT_TYPE, plan: str):
    uid = update.effective_user.id
    ensure_seller(uid)  # treat connected users as sellers

    if is_super(uid):
        await update.callback_query.answer("Super Admin does not need to purchase", show_alert=True)
        return

    # If user already whitelabel and tries to pay $5 -> not allowed
    current = seller_plan(uid)
    if current == "whitelabel" and plan == "A":
        await update.callback_query.answer("White-label cannot renew using $5 plan", show_alert=True)
        return

    price = PLAN_A_PRICE if plan == "A" else PLAN_B_PRICE
    bal = get_balance(SUPER_ADMIN_ID, uid)  # payment from MAIN SHOP wallet
    if bal < price:
        await update.callback_query.answer("Not enough balance in Main Shop wallet", show_alert=True)
        return

    add_balance(SUPER_ADMIN_ID, uid, -price)
    log_tx(SUPER_ADMIN_ID, uid, "plan", -price, f"Connect Plan {plan} - {PLAN_DAYS} days")

    seller_add_days(uid, PLAN_DAYS)

    if plan == "B":
        seller_set_plan(uid, "whitelabel")
    else:
        seller_set_plan(uid, "branded")

    await notify_super(
        context.application,
        f"🤖 <b>Connect My Bot Purchase</b>\nUser: <b>{esc(user_display(uid))}</b> (<code>{uid}</code>)\nPlan: <b>{'A (Branded)' if plan=='A' else 'B (White-label)'}</b>\nDays added: <b>{PLAN_DAYS}</b>\n"
    )

    # ask token now
    set_state(context, "connect_token", {"uid": uid})
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "✅ Subscription activated.\n\nNow send your <b>Bot Token</b> from @BotFather.\n\n(Example: 123:ABC...)",
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")]])
    )

async def connect_token_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "connect_token":
        return
    uid = update.effective_user.id
    if uid != int(data.get("uid", 0)):
        return
    token = (update.message.text or "").strip()
    if not re.match(r"^\d+:[A-Za-z0-9_-]{20,}$", token):
        await update.message.reply_text("❌ Invalid token. Send the BotFather token again.")
        return

    # try fetch bot username quickly
    try:
        tmp = Application.builder().token(token).build()
        await tmp.initialize()
        me = await tmp.bot.get_me()
        await tmp.shutdown()
        bot_username = me.username or ""
    except Exception:
        bot_username = ""

    upsert_seller_bot(uid, token, bot_username)
    ensure_shop_settings(uid)

    # start seller bot if active
    if seller_active(uid):
        try:
            await MANAGER.start_seller_bot(uid, token)
        except Exception:
            pass

    clear_state(context)
    await update.message.reply_text(
        f"✅ Connected!\nYour bot username: @{bot_username}" if bot_username else "✅ Connected!",
        reply_markup=kb([[InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")]])
    )

# =========================
# EXTEND SUBSCRIPTION (in seller bot: bring to master shop)
# =========================
async def extend_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    uid = update.effective_user.id
    if shop_owner_id == SUPER_ADMIN_ID:
        await update.callback_query.answer("Use Connect My Bot to renew", show_alert=True)
        return
    if uid != shop_owner_id and not is_super(uid):
        await update.callback_query.answer("Not allowed", show_alert=True)
        return

    days_left = seller_days_left(shop_owner_id)
    text = f"⏳ <b>Subscription</b>\nDays left: <b>{days_left}</b>\n\nRenew in Main Shop."
    # link to master bot
    if MASTER_BOT_USERNAME:
        url = f"https://t.me/{MASTER_BOT_USERNAME}"
        btn = [[InlineKeyboardButton("🏪 Go to Main Shop", url=url)]]
    else:
        btn = [[InlineKeyboardButton("🏪 Go to Main Shop", callback_data="m:menu")]]
    btn.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")])

    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb(btn))

# =========================
# ADMIN PANEL (Seller + Super Admin)
# - Users List (no user IDs typed)
# - Edit Welcome
# - Edit Wallet Message
# - Manage Categories / Sub / Products / Keys / Private Link
# - Broadcast
# =========================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    uid = update.effective_user.id
    is_owner = (uid == shop_owner_id) or is_super(uid)
    if not is_owner:
        await update.callback_query.answer("Not allowed", show_alert=True)
        return

    days = seller_days_left(shop_owner_id) if shop_owner_id != SUPER_ADMIN_ID else 10**9
    plan = "Super" if shop_owner_id == SUPER_ADMIN_ID else seller_plan(shop_owner_id)

    header = f"🛠 <b>Admin Panel</b>\nShop: <b>{esc('Main Shop' if shop_owner_id==SUPER_ADMIN_ID else user_display(shop_owner_id))}</b>\n"
    if shop_owner_id != SUPER_ADMIN_ID:
        header += f"Plan: <b>{esc(plan)}</b>\nDays left: <b>{days}</b>\n"

    btns = [
        [InlineKeyboardButton("👥 Users List", callback_data="ad:users")],
        [InlineKeyboardButton("✉️ Broadcast", callback_data="ad:broadcast")],
        [InlineKeyboardButton("🖼 Edit Welcome", callback_data="ad:wel")],
        [InlineKeyboardButton("💳 Edit Wallet Message", callback_data="ad:walletmsg")],
        [InlineKeyboardButton("➕ Add Wallet Deposit Method", callback_data="ad:addmethod")],
        [InlineKeyboardButton("🧩 Manage Catalog", callback_data="ad:catalog")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")],
    ]
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(header, parse_mode=ParseMode.HTML, reply_markup=kb(btns))

async def admin_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    # show usernames list + search button
    conn = db(); cur = conn.cursor()
    cur.execute("""
        SELECT u.user_id,u.username,u.first_name,u.last_name
        FROM users u
        JOIN balances b ON b.user_id=u.user_id AND b.shop_owner_id=?
        ORDER BY u.last_seen DESC LIMIT 50
    """, (shop_owner_id,))
    rows = cur.fetchall()
    conn.close()

    lines = ["👥 <b>Users</b>\n(Click a user)\n"]
    btns = [[InlineKeyboardButton("🔎 Search User", callback_data="ad:searchuser")]]
    for r in rows:
        uid = int(r["user_id"])
        title = user_display(uid)
        btns.append([InlineKeyboardButton(title, callback_data=f"ad:user:{uid}")])
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="m:admin")])

    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb(btns))

async def admin_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, target_id: int):
    bal = get_balance(shop_owner_id, target_id)
    un = user_display(target_id)
    refc = referral_count(SUPER_ADMIN_ID, target_id) if shop_owner_id == SUPER_ADMIN_ID else 0

    # order list clickable
    orders = list_orders_for_user(shop_owner_id, target_id, limit=10)
    txt = (
        f"👤 <b>User</b>: <b>{esc(un)}</b>\n"
        f"Telegram ID: <code>{target_id}</code>\n"
        f"Balance: <b>{money(bal)} {esc(CURRENCY)}</b>\n"
    )
    if shop_owner_id == SUPER_ADMIN_ID:
        txt += f"Referrals: <b>{refc}</b>\n"
    txt += "\n🧾 <b>Order History</b>\n"

    btns: List[List[InlineKeyboardButton]] = []
    if not orders:
        txt += "No orders.\n"
    else:
        for o in orders:
            dt = now_text(int(o["created_at"]))
            txt += f"• <code>{esc(o['order_id'])}</code> | {esc(dt)} | {money(float(o['total']))} {esc(CURRENCY)}\n"
            btns.append([InlineKeyboardButton(f"🧾 {o['order_id']}", callback_data=f"ad:order:{target_id}:{o['order_id']}")])

    btns = [
        [InlineKeyboardButton("➕ Add Balance", callback_data=f"ad:bal:+:{target_id}"),
         InlineKeyboardButton("➖ Deduct Balance", callback_data=f"ad:bal:-:{target_id}")],
        [InlineKeyboardButton("⛔ Ban From Shop", callback_data=f"ad:ban:{target_id}"),
         InlineKeyboardButton("⏳ Restrict 7d", callback_data=f"ad:res:{target_id}:7")],
        [InlineKeyboardButton("⏳ Restrict 14d", callback_data=f"ad:res:{target_id}:14"),
         InlineKeyboardButton("⏳ Restrict 30d", callback_data=f"ad:res:{target_id}:30")],
    ] + btns + [
        [InlineKeyboardButton("⬅️ Back", callback_data="ad:users")]
    ]

    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb(btns))

async def admin_order_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, target_id: int, order_id: str):
    o = get_order(shop_owner_id, order_id)
    if not o:
        await update.callback_query.answer("Order not found", show_alert=True)
        return
    keys = get_order_keys(shop_owner_id, order_id)
    kshow = "\n".join([f"<code>{esc(k)}</code>" for k in keys]) if keys else "<i>No keys</i>"
    dt = now_text(int(o["created_at"]))
    txt = (
        f"🧾 <b>Order</b>\n"
        f"User: <b>{esc(user_display(target_id))}</b>\n"
        f"Order ID: <code>{esc(order_id)}</code>\n"
        f"Date: <b>{esc(dt)}</b>\n"
        f"Product: <b>{esc(o['product_name'])}</b>\n"
        f"Qty: <b>{o['qty']}</b>\n"
        f"Amount: <b>{money(float(o['total']))} {esc(CURRENCY)}</b>\n\n"
        f"🔑 <b>Exact Keys Delivered</b>\n{kshow}"
    )
    btns = [[InlineKeyboardButton("⬅️ Back", callback_data=f"ad:user:{target_id}")]]
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb(btns))

async def admin_balance_action(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, sign: str, target_id: int):
    set_state(context, "bal_edit", {"shop_owner_id": shop_owner_id, "sign": sign, "target_id": target_id})
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        f"Send amount to {'ADD' if sign=='+' else 'DEDUCT'} for {esc(user_display(target_id))}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("⬅️ Cancel", callback_data=f"ad:user:{target_id}")]])
    )

async def admin_balance_amount_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "bal_edit":
        return
    amt = parse_float(update.message.text or "")
    if amt is None or amt <= 0:
        await update.message.reply_text("❌ Invalid amount.")
        return
    shop_owner_id = int(data["shop_owner_id"])
    sign = data["sign"]
    target_id = int(data["target_id"])
    delta = amt if sign == "+" else -amt
    add_balance(shop_owner_id, target_id, delta)
    log_tx(shop_owner_id, target_id, "balance_edit", delta, f"Admin balance {'add' if delta>0 else 'deduct'}")
    clear_state(context)
    await update.message.reply_text("✅ Updated.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data=f"ad:user:{target_id}")]]))

async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, target_id: int):
    ban_user(shop_owner_id, target_id, 1)
    await update.callback_query.answer("Banned")
    await admin_user_detail(update, context, shop_owner_id, target_id)

async def admin_res(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, target_id: int, days: int):
    restrict_user(shop_owner_id, target_id, days)
    await update.callback_query.answer("Restricted")
    await admin_user_detail(update, context, shop_owner_id, target_id)

# --- Admin: Welcome (text + photo/video) ---
async def admin_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    set_state(context, "wel_text", {"shop_owner_id": shop_owner_id})
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Send new <b>welcome text</b> now.\n(You can send / skip file after.)",
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("⬅️ Cancel", callback_data="m:admin")]])
    )

async def admin_welcome_text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "wel_text":
        return
    shop_owner_id = int(data["shop_owner_id"])
    text = (update.message.text or "").strip()
    set_shop_setting(shop_owner_id, "welcome_text", text)
    # ask for media
    set_state(context, "wel_media", {"shop_owner_id": shop_owner_id})
    await update.message.reply_text(
        "✅ Saved welcome text.\nNow send a <b>photo</b> or <b>video</b> for welcome (or type <b>skip</b>).",
        parse_mode=ParseMode.HTML
    )

async def admin_welcome_media_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "wel_media":
        return
    shop_owner_id = int(data["shop_owner_id"])
    if update.message.text and update.message.text.lower().strip() == "skip":
        set_shop_setting(shop_owner_id, "welcome_file_id", "")
        set_shop_setting(shop_owner_id, "welcome_file_type", "")
        clear_state(context)
        await update.message.reply_text("✅ Welcome updated.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="m:admin")]]))
        return

    if update.message.photo:
        fid = update.message.photo[-1].file_id
        set_shop_setting(shop_owner_id, "welcome_file_id", fid)
        set_shop_setting(shop_owner_id, "welcome_file_type", "photo")
        clear_state(context)
        await update.message.reply_text("✅ Welcome updated (photo).", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="m:admin")]]))
        return
    if update.message.video:
        fid = update.message.video.file_id
        set_shop_setting(shop_owner_id, "welcome_file_id", fid)
        set_shop_setting(shop_owner_id, "welcome_file_type", "video")
        clear_state(context)
        await update.message.reply_text("✅ Welcome updated (video).", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="m:admin")]]))
        return

    await update.message.reply_text("❌ Send photo/video or type skip.")

# --- Admin: Wallet message ---
async def admin_walletmsg(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    set_state(context, "wallet_msg", {"shop_owner_id": shop_owner_id})
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Send new <b>Wallet Message</b> now.\n(You can put any wallet text, not only TRC-20.)",
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("⬅️ Cancel", callback_data="m:admin")]])
    )

async def admin_walletmsg_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "wallet_msg":
        return
    shop_owner_id = int(data["shop_owner_id"])
    set_shop_setting(shop_owner_id, "wallet_message", (update.message.text or "").strip())
    clear_state(context)
    await update.message.reply_text("✅ Saved.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="m:admin")]]))

# --- Admin: Add extra deposit method ---
async def admin_add_method(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    set_state(context, "add_method_title", {"shop_owner_id": shop_owner_id})
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Send deposit method <b>Title</b> (example: Binance Pay).",
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("⬅️ Cancel", callback_data="m:admin")]])
    )

async def admin_add_method_title_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "add_method_title":
        return
    data["title"] = (update.message.text or "").strip()
    set_state(context, "add_method_instr", data)
    await update.message.reply_text("Now send <b>Instructions</b> for this method.", parse_mode=ParseMode.HTML)

async def admin_add_method_instr_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "add_method_instr":
        return
    data["instructions"] = (update.message.text or "").strip()
    set_state(context, "add_method_qr", data)
    await update.message.reply_text("Optional: send QR <b>photo</b> now, or type <b>skip</b>.", parse_mode=ParseMode.HTML)

async def admin_add_method_qr_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "add_method_qr":
        return
    shop_owner_id = int(data["shop_owner_id"])
    qr = ""
    if update.message.photo:
        qr = update.message.photo[-1].file_id
    elif update.message.text and update.message.text.lower().strip() == "skip":
        qr = ""
    else:
        await update.message.reply_text("Send a photo or type skip.")
        return
    add_wallet_method(shop_owner_id, data["title"], data["instructions"], qr)
    clear_state(context)
    await update.message.reply_text("✅ Deposit method added.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="m:admin")]]))

# --- Admin: Broadcast ---
async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    set_state(context, "broadcast", {"shop_owner_id": shop_owner_id})
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "✉️ <b>Broadcast</b>\nSend the message you want to broadcast to all users now.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb([[InlineKeyboardButton("⬅️ Cancel", callback_data="m:admin")]])
    )

async def admin_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "broadcast":
        return
    shop_owner_id = int(data["shop_owner_id"])
    text = (update.message.text or "").strip()
    if not text:
        return
    clear_state(context)

    # all users with balance row in this shop
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM balances WHERE shop_owner_id=?", (shop_owner_id,))
    ids = [int(r["user_id"]) for r in cur.fetchall()]
    conn.close()

    sent = 0
    for uid in ids:
        try:
            await context.application.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception:
            pass

    await update.message.reply_text(f"✅ Broadcast sent to {sent} users.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="m:admin")]]))

# --- Admin: Catalog Management (button based, no typing IDs) ---
async def admin_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    btns = [
        [InlineKeyboardButton("➕ Add Category", callback_data="cat:add")],
        [InlineKeyboardButton("➕ Add Sub-Category", callback_data="sub:add")],
        [InlineKeyboardButton("➕ Add Product", callback_data="prd:add")],
        [InlineKeyboardButton("✏️ Edit Product", callback_data="prd:edit")],
        [InlineKeyboardButton("🔑 Add Keys To Product", callback_data="key:add")],
        [InlineKeyboardButton("🔗 Set Product Channel Link", callback_data="prd:link")],
        [InlineKeyboardButton("⬅️ Back", callback_data="m:admin")],
    ]
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("🧩 <b>Manage Catalog</b>", parse_mode=ParseMode.HTML, reply_markup=kb(btns))

async def choose_category_buttons(shop_owner_id: int, prefix: str) -> InlineKeyboardMarkup:
    cats = cat_list(shop_owner_id)
    btns = []
    for c in cats[:40]:
        btns.append([InlineKeyboardButton(c["name"], callback_data=f"{prefix}:{c['id']}")])
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="ad:catalog")])
    return kb(btns)

async def choose_sub_buttons(shop_owner_id: int, cat_id: int, prefix: str) -> InlineKeyboardMarkup:
    subs = cocat_list(shop_owner_id, cat_id)
    btns = []
    for s in subs[:40]:
        btns.append([InlineKeyboardButton(s["name"], callback_data=f"{prefix}:{cat_id}:{s['id']}")])
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="ad:catalog")])
    return kb(btns)

async def choose_product_buttons(shop_owner_id: int, cat_id: int, sub_id: int, prefix: str) -> InlineKeyboardMarkup:
    prods = prod_list(shop_owner_id, cat_id, sub_id)
    btns = []
    for p in prods[:50]:
        btns.append([InlineKeyboardButton(p["name"], callback_data=f"{prefix}:{p['id']}")])
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="ad:catalog")])
    return kb(btns)

# Add Category
async def cat_add(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    set_state(context, "cat_add_name", {"shop_owner_id": shop_owner_id})
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Send <b>Category Name</b>.", parse_mode=ParseMode.HTML)

async def cat_add_name_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "cat_add_name":
        return
    data["name"] = (update.message.text or "").strip()
    set_state(context, "cat_add_desc", data)
    await update.message.reply_text("Optional: send <b>Description</b> or type <b>skip</b>.", parse_mode=ParseMode.HTML)

async def cat_add_desc_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "cat_add_desc":
        return
    if update.message.text and update.message.text.lower().strip() == "skip":
        data["desc"] = ""
    else:
        data["desc"] = (update.message.text or "").strip()
    set_state(context, "cat_add_media", data)
    await update.message.reply_text("Optional: send category <b>photo/video</b> or type <b>skip</b>.", parse_mode=ParseMode.HTML)

async def cat_add_media_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "cat_add_media":
        return
    shop_owner_id = int(data["shop_owner_id"])
    fid = ""; ftype = ""
    if update.message.photo:
        fid = update.message.photo[-1].file_id; ftype = "photo"
    elif update.message.video:
        fid = update.message.video.file_id; ftype = "video"
    elif update.message.text and update.message.text.lower().strip() == "skip":
        fid = ""; ftype = ""
    else:
        await update.message.reply_text("Send photo/video or type skip.")
        return
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO categories(shop_owner_id,name,description,file_id,file_type) VALUES(?,?,?,?,?)",
        (shop_owner_id, data["name"], data.get("desc",""), fid, ftype)
    )
    conn.commit(); conn.close()
    clear_state(context)
    await update.message.reply_text("✅ Category added.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="ad:catalog")]]))

# Add Sub-category
async def sub_add(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Category:", reply_markup=await choose_category_buttons(shop_owner_id, "sub:pickcat"))

async def sub_pickcat(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, cat_id: int):
    set_state(context, "sub_add_name", {"shop_owner_id": shop_owner_id, "cat_id": cat_id})
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Send <b>Sub-Category Name</b>.", parse_mode=ParseMode.HTML)

async def sub_add_name_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "sub_add_name":
        return
    data["name"] = (update.message.text or "").strip()
    set_state(context, "sub_add_desc", data)
    await update.message.reply_text("Optional: send <b>Description</b> or type <b>skip</b>.", parse_mode=ParseMode.HTML)

async def sub_add_desc_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "sub_add_desc":
        return
    if update.message.text and update.message.text.lower().strip() == "skip":
        data["desc"] = ""
    else:
        data["desc"] = (update.message.text or "").strip()
    set_state(context, "sub_add_media", data)
    await update.message.reply_text("Optional: send sub-category <b>photo/video</b> or type <b>skip</b>.", parse_mode=ParseMode.HTML)

async def sub_add_media_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "sub_add_media":
        return
    shop_owner_id = int(data["shop_owner_id"]); cat_id = int(data["cat_id"])
    fid = ""; ftype = ""
    if update.message.photo:
        fid = update.message.photo[-1].file_id; ftype = "photo"
    elif update.message.video:
        fid = update.message.video.file_id; ftype = "video"
    elif update.message.text and update.message.text.lower().strip() == "skip":
        fid = ""; ftype = ""
    else:
        await update.message.reply_text("Send photo/video or type skip.")
        return
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO cocategories(shop_owner_id,category_id,name,description,file_id,file_type) VALUES(?,?,?,?,?,?)",
        (shop_owner_id, cat_id, data["name"], data.get("desc",""), fid, ftype)
    )
    conn.commit(); conn.close()
    clear_state(context)
    await update.message.reply_text("✅ Sub-Category added.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="ad:catalog")]]))

# Add Product
async def prd_add(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Category:", reply_markup=await choose_category_buttons(shop_owner_id, "prd:pickcat"))

async def prd_pickcat(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, cat_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Sub-Category:", reply_markup=await choose_sub_buttons(shop_owner_id, cat_id, "prd:picksub"))

async def prd_picksub(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, cat_id: int, sub_id: int):
    set_state(context, "prd_add_name", {"shop_owner_id": shop_owner_id, "cat_id": cat_id, "sub_id": sub_id})
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Send <b>Product Name</b>.", parse_mode=ParseMode.HTML)

async def prd_add_name_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "prd_add_name":
        return
    data["name"] = (update.message.text or "").strip()
    set_state(context, "prd_add_price", data)
    await update.message.reply_text("Send <b>Price</b> (number).", parse_mode=ParseMode.HTML)

async def prd_add_price_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "prd_add_price":
        return
    amt = parse_float(update.message.text or "")
    if amt is None or amt <= 0:
        await update.message.reply_text("❌ Invalid price.")
        return
    data["price"] = float(amt)
    set_state(context, "prd_add_desc", data)
    await update.message.reply_text("Optional: send <b>Description</b> or type <b>skip</b>.", parse_mode=ParseMode.HTML)

async def prd_add_desc_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "prd_add_desc":
        return
    if update.message.text and update.message.text.lower().strip() == "skip":
        data["desc"] = ""
    else:
        data["desc"] = (update.message.text or "").strip()
    set_state(context, "prd_add_media", data)
    await update.message.reply_text("Optional: send product <b>photo/video</b> or type <b>skip</b>.", parse_mode=ParseMode.HTML)

async def prd_add_media_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "prd_add_media":
        return
    shop_owner_id = int(data["shop_owner_id"])
    fid=""; ftype=""
    if update.message.photo:
        fid=update.message.photo[-1].file_id; ftype="photo"
    elif update.message.video:
        fid=update.message.video.file_id; ftype="video"
    elif update.message.text and update.message.text.lower().strip()=="skip":
        fid=""; ftype=""
    else:
        await update.message.reply_text("Send photo/video or type skip.")
        return
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO products(shop_owner_id,category_id,cocategory_id,name,price,description,file_id,file_type,tg_link)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (shop_owner_id, int(data["cat_id"]), int(data["sub_id"]), data["name"], float(data["price"]), data.get("desc",""), fid, ftype, ""))
    conn.commit(); conn.close()
    clear_state(context)
    await update.message.reply_text("✅ Product added.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="ad:catalog")]]))

# Edit Product name/price/desc
async def prd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Category:", reply_markup=await choose_category_buttons(shop_owner_id, "pe:cat"))

async def pe_cat(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, cat_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Sub-Category:", reply_markup=await choose_sub_buttons(shop_owner_id, cat_id, "pe:sub"))

async def pe_sub(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, cat_id: int, sub_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Product:", reply_markup=await choose_product_buttons(shop_owner_id, cat_id, sub_id, "pe:prd"))

async def pe_prd(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, pid: int):
    set_state(context, "pe_field", {"shop_owner_id": shop_owner_id, "pid": pid})
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Choose what to edit:",
        reply_markup=kb([
            [InlineKeyboardButton("✏️ Name", callback_data="pef:name"),
             InlineKeyboardButton("💲 Price", callback_data="pef:price")],
            [InlineKeyboardButton("📝 Description", callback_data="pef:desc")],
            [InlineKeyboardButton("⬅️ Back", callback_data="ad:catalog")]
        ])
    )

async def pef(update: Update, context: ContextTypes.DEFAULT_TYPE, field: str):
    st, data = get_state(context)
    if st != "pe_field":
        await update.callback_query.answer()
        return
    data["field"] = field
    set_state(context, "pe_value", data)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(f"Send new value for <b>{esc(field)}</b>.", parse_mode=ParseMode.HTML)

async def pe_value_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "pe_value":
        return
    shop_owner_id = int(data["shop_owner_id"]); pid = int(data["pid"]); field = data["field"]
    val = (update.message.text or "").strip()
    conn = db(); cur = conn.cursor()
    if field == "price":
        amt = parse_float(val)
        if amt is None or amt <= 0:
            await update.message.reply_text("❌ Invalid price.")
            conn.close()
            return
        cur.execute("UPDATE products SET price=? WHERE shop_owner_id=? AND id=?", (float(amt), shop_owner_id, pid))
    elif field == "name":
        cur.execute("UPDATE products SET name=? WHERE shop_owner_id=? AND id=?", (val, shop_owner_id, pid))
    else:
        cur.execute("UPDATE products SET description=? WHERE shop_owner_id=? AND id=?", (val, shop_owner_id, pid))
    conn.commit(); conn.close()
    clear_state(context)
    await update.message.reply_text("✅ Updated.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="ad:catalog")]]))

# Add Keys
async def key_add(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Category:", reply_markup=await choose_category_buttons(shop_owner_id, "k:cat"))

async def k_cat(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, cat_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Sub-Category:", reply_markup=await choose_sub_buttons(shop_owner_id, cat_id, "k:sub"))

async def k_sub(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, cat_id: int, sub_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Product:", reply_markup=await choose_product_buttons(shop_owner_id, cat_id, sub_id, "k:prd"))

async def k_prd(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, pid: int):
    set_state(context, "keys_lines", {"shop_owner_id": shop_owner_id, "pid": pid})
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Send keys now.\n<b>1 line = 1 stock</b>\n\nSend multiple lines in one message.",
        parse_mode=ParseMode.HTML
    )

async def keys_lines_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "keys_lines":
        return
    shop_owner_id = int(data["shop_owner_id"]); pid = int(data["pid"])
    text = (update.message.text or "").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    n = add_keys(shop_owner_id, pid, lines)
    clear_state(context)
    await update.message.reply_text(f"✅ Added {n} keys.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="ad:catalog")]]))

# Set Product Channel Link (join-gate)
async def prd_link(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Category:", reply_markup=await choose_category_buttons(shop_owner_id, "pl:cat"))

async def pl_cat(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, cat_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Sub-Category:", reply_markup=await choose_sub_buttons(shop_owner_id, cat_id, "pl:sub"))

async def pl_sub(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, cat_id: int, sub_id: int):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Choose Product:", reply_markup=await choose_product_buttons(shop_owner_id, cat_id, sub_id, "pl:prd"))

async def pl_prd(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, pid: int):
    set_state(context, "prd_link_set", {"shop_owner_id": shop_owner_id, "pid": pid})
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Send PUBLIC channel link for join-gate.\nExample: https://t.me/MyChannel",
        parse_mode=ParseMode.HTML
    )

async def prd_link_set_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st != "prd_link_set":
        return
    shop_owner_id = int(data["shop_owner_id"]); pid = int(data["pid"])
    link = (update.message.text or "").strip()
    if not parse_channel_username(link):
        await update.message.reply_text("❌ Invalid link. Send a t.me link or @channelusername")
        return
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE products SET tg_link=? WHERE shop_owner_id=? AND id=?", (link, shop_owner_id, pid))
    conn.commit(); conn.close()
    clear_state(context)
    await update.message.reply_text("✅ Saved channel link.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="ad:catalog")]]))

# =========================
# SUPER ADMIN BUTTON (ONLY SUPER ADMIN SEES)
# - Sellers List (search + usernames) restrict/ban shop/panel + warn ending
# - Users list for ban/restrict
# =========================
async def super_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super(update.effective_user.id):
        await update.callback_query.answer("Not allowed", show_alert=True)
        return
    btns = [
        [InlineKeyboardButton("🧑‍💻 Sellers List", callback_data="su:sellers")],
        [InlineKeyboardButton("👥 Users List", callback_data="su:users")],
        [InlineKeyboardButton("⚠️ Warn Ending Subs", callback_data="su:warn")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="m:menu")],
    ]
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("👑 <b>Super Admin</b>", parse_mode=ParseMode.HTML, reply_markup=kb(btns))

async def super_sellers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = list_sellers_only()
    btns = [[InlineKeyboardButton("🔎 Search Seller", callback_data="su:searchseller")]]
    for r in rows[:50]:
        sid = int(r["seller_id"])
        days = seller_days_left(sid)
        btns.append([InlineKeyboardButton(f"{user_display(sid)} (days {days})", callback_data=f"su:seller:{sid}")])
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="m:super")])
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("🧑‍💻 <b>Sellers</b>", parse_mode=ParseMode.HTML, reply_markup=kb(btns))

async def super_seller_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, sid: int):
    r = seller_row(sid)
    if not r:
        await update.callback_query.answer("Not found", show_alert=True)
        return
    days = seller_days_left(sid)
    txt = (
        f"🧑‍💻 <b>Seller</b>: <b>{esc(user_display(sid))}</b>\n"
        f"Telegram ID: <code>{sid}</code>\n"
        f"Plan: <b>{esc(seller_plan(sid))}</b>\n"
        f"Days left: <b>{days}</b>\n"
        f"Banned shop: <b>{int(r['banned_shop'] or 0)}</b>\n"
        f"Banned panel: <b>{int(r['banned_panel'] or 0)}</b>\n"
    )
    btns = [
        [InlineKeyboardButton("⛔ Ban Shop", callback_data=f"su:ban_shop:{sid}"),
         InlineKeyboardButton("✅ Unban Shop", callback_data=f"su:unban_shop:{sid}")],
        [InlineKeyboardButton("⛔ Ban Panel", callback_data=f"su:ban_panel:{sid}"),
         InlineKeyboardButton("✅ Unban Panel", callback_data=f"su:unban_panel:{sid}")],
        [InlineKeyboardButton("⏳ Restrict 7d", callback_data=f"su:res:{sid}:7"),
         InlineKeyboardButton("⏳ Restrict 30d", callback_data=f"su:res:{sid}:30")],
        [InlineKeyboardButton("⬅️ Back", callback_data="su:sellers")],
    ]
    await delete_prev_msg(update, context)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb(btns))

async def super_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = list_sellers_only()
    sent = 0
    for r in rows:
        sid = int(r["seller_id"])
        if not seller_active(sid):
            continue
        days = seller_days_left(sid)
        if days <= 3:
            try:
                await context.application.bot.send_message(
                    chat_id=sid,
                    text=f"⚠️ Your subscription is ending soon.\nDays left: {days}\nPlease renew in Main Shop.",
                )
                sent += 1
            except Exception:
                pass
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(f"✅ Warned {sent} sellers.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="m:super")]]))

# =========================
# SEARCH (admin + super)
# =========================
async def search_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, shop_owner_id: int):
    set_state(context, mode, {"shop_owner_id": shop_owner_id})
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Send username to search (example: rekkoown).", reply_markup=kb([[InlineKeyboardButton("⬅️ Cancel", callback_data="m:admin" if mode.startswith("ad") else "m:super")]]))

async def search_user_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st, data = get_state(context)
    if st not in ("ad_search_user", "su_search_seller"):
        return
    q = (update.message.text or "").strip().lstrip("@").lower()
    shop_owner_id = int(data.get("shop_owner_id", SUPER_ADMIN_ID))
    clear_state(context)
    if not q:
        return

    conn = db(); cur = conn.cursor()
    cur.execute("SELECT user_id, username FROM users WHERE LOWER(username)=?", (q,))
    r = cur.fetchone()
    conn.close()
    if not r:
        await update.message.reply_text("❌ Not found.")
        return
    uid = int(r["user_id"])

    if st == "ad_search_user":
        await update.message.reply_text("✅ Found.", reply_markup=kb([[InlineKeyboardButton("Open User", callback_data=f"ad:user:{uid}")]]))
    else:
        await update.message.reply_text("✅ Found.", reply_markup=kb([[InlineKeyboardButton("Open Seller", callback_data=f"su:seller:{uid}")]]))

# =========================
# ROUTER (callbacks)
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, bot_kind: str):
    q = update.callback_query
    data = q.data or ""
    uid = update.effective_user.id

    # if seller panel banned
    if shop_owner_id != SUPER_ADMIN_ID and (uid == shop_owner_id) and (seller_row(shop_owner_id) and int(seller_row(shop_owner_id)["banned_panel"] or 0) == 1):
        await q.answer("Admin Panel banned", show_alert=True)
        return

    if data == "m:menu":
        await show_main_menu(update, context, shop_owner_id)
        return
    if data == "m:products":
        await show_categories(update, context, shop_owner_id)
        return
    if data.startswith("p:cat:"):
        await show_cocategories(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data.startswith("p:sub:"):
        _, _, cat_id, sub_id = data.split(":")
        await show_products(update, context, shop_owner_id, int(cat_id), int(sub_id))
        return
    if data.startswith("p:prod:"):
        await show_product_detail(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data.startswith("p:qty:"):
        _, _, sign, pid = data.split(":")
        await adjust_qty(update, context, shop_owner_id, sign, int(pid))
        return
    if data.startswith("p:buy:"):
        await do_buy(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data.startswith("p:getfiles:"):
        _, _, pid, oid = data.split(":")
        await get_files(update, context, shop_owner_id, int(pid), oid)
        return

    if data == "m:wallet":
        await show_wallet(update, context, shop_owner_id)
        return
    if data == "d:start":
        await deposit_start(update, context, shop_owner_id)
        return
    if data.startswith("d:method:"):
        await deposit_method(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data.startswith("dep:approve:"):
        await handle_deposit_action(update, context, True, int(data.split(":")[2]))
        return
    if data.startswith("dep:reject:"):
        await handle_deposit_action(update, context, False, int(data.split(":")[2]))
        return

    if data == "m:history":
        await show_history(update, context, shop_owner_id)
        return

    if data == "m:support":
        await support_open(update, context, shop_owner_id)
        return
    if data == "sup:done":
        await support_done(update, context)
        return

    # master connect
    if data == "m:connect":
        if shop_owner_id != SUPER_ADMIN_ID:
            await q.answer("Only in Main Shop", show_alert=True)
            return
        await connect_show(update, context)
        return
    if data.startswith("con:plan:"):
        if shop_owner_id != SUPER_ADMIN_ID:
            await q.answer("Only in Main Shop", show_alert=True)
            return
        plan = data.split(":")[2]
        await connect_choose(update, context, plan)
        return

    if data == "m:extend":
        await extend_subscription(update, context, shop_owner_id)
        return

    # Admin panel
    if data == "m:admin":
        await admin_panel(update, context, shop_owner_id)
        return
    if data == "ad:users":
        await admin_users_list(update, context, shop_owner_id)
        return
    if data.startswith("ad:user:"):
        await admin_user_detail(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data.startswith("ad:order:"):
        _, _, target, oid = data.split(":")
        await admin_order_detail(update, context, shop_owner_id, int(target), oid)
        return
    if data.startswith("ad:bal:"):
        _, _, sign, target = data.split(":")
        await admin_balance_action(update, context, shop_owner_id, sign, int(target))
        return
    if data.startswith("ad:ban:"):
        await admin_ban(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data.startswith("ad:res:"):
        _, _, target, days = data.split(":")
        await admin_res(update, context, shop_owner_id, int(target), int(days))
        return
    if data == "ad:wel":
        await admin_welcome(update, context, shop_owner_id)
        return
    if data == "ad:walletmsg":
        await admin_walletmsg(update, context, shop_owner_id)
        return
    if data == "ad:addmethod":
        await admin_add_method(update, context, shop_owner_id)
        return
    if data == "ad:broadcast":
        await admin_broadcast(update, context, shop_owner_id)
        return
    if data == "ad:catalog":
        await admin_catalog(update, context, shop_owner_id)
        return
    if data == "cat:add":
        await cat_add(update, context, shop_owner_id)
        return
    if data == "sub:add":
        await sub_add(update, context, shop_owner_id)
        return
    if data.startswith("sub:pickcat:"):
        await sub_pickcat(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data == "prd:add":
        await prd_add(update, context, shop_owner_id)
        return
    if data.startswith("prd:pickcat:"):
        await prd_pickcat(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data.startswith("prd:picksub:"):
        _, _, cat_id, sub_id = data.split(":")
        await prd_picksub(update, context, shop_owner_id, int(cat_id), int(sub_id))
        return
    if data == "prd:edit":
        await prd_edit(update, context, shop_owner_id)
        return
    if data.startswith("pe:cat:"):
        await pe_cat(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data.startswith("pe:sub:"):
        _, _, cat_id, sub_id = data.split(":")
        await pe_sub(update, context, shop_owner_id, int(cat_id), int(sub_id))
        return
    if data.startswith("pe:prd:"):
        await pe_prd(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data.startswith("pef:"):
        await pef(update, context, data.split(":")[1])
        return
    if data == "key:add":
        await key_add(update, context, shop_owner_id)
        return
    if data.startswith("k:cat:"):
        await k_cat(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data.startswith("k:sub:"):
        _, _, cat_id, sub_id = data.split(":")
        await k_sub(update, context, shop_owner_id, int(cat_id), int(sub_id))
        return
    if data.startswith("k:prd:"):
        await k_prd(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data == "prd:link":
        await prd_link(update, context, shop_owner_id)
        return
    if data.startswith("pl:cat:"):
        await pl_cat(update, context, shop_owner_id, int(data.split(":")[2]))
        return
    if data.startswith("pl:sub:"):
        _, _, cat_id, sub_id = data.split(":")
        await pl_sub(update, context, shop_owner_id, int(cat_id), int(sub_id))
        return
    if data.startswith("pl:prd:"):
        await pl_prd(update, context, shop_owner_id, int(data.split(":")[2]))
        return

    # Super Admin panel (only in master bot)
    if data == "m:super":
        await super_panel(update, context)
        return
    if data == "su:sellers":
        await super_sellers(update, context)
        return
    if data.startswith("su:seller:"):
        await super_seller_detail(update, context, int(data.split(":")[2]))
        return
    if data.startswith("su:ban_shop:"):
        sid = int(data.split(":")[2]); super_set_seller_flag(sid, "banned_shop", 1)
        await q.answer("Banned shop")
        await super_seller_detail(update, context, sid)
        return
    if data.startswith("su:unban_shop:"):
        sid = int(data.split(":")[2]); super_set_seller_flag(sid, "banned_shop", 0)
        await q.answer("Unbanned shop")
        await super_seller_detail(update, context, sid)
        return
    if data.startswith("su:ban_panel:"):
        sid = int(data.split(":")[2]); super_set_seller_flag(sid, "banned_panel", 1)
        await q.answer("Banned panel")
        await super_seller_detail(update, context, sid)
        return
    if data.startswith("su:unban_panel:"):
        sid = int(data.split(":")[2]); super_set_seller_flag(sid, "banned_panel", 0)
        await q.answer("Unbanned panel")
        await super_seller_detail(update, context, sid)
        return
    if data.startswith("su:res:"):
        _, _, sid, days = data.split(":")
        super_restrict_seller(int(sid), int(days))
        await q.answer("Restricted")
        await super_seller_detail(update, context, int(sid))
        return
    if data == "su:warn":
        await super_warn(update, context)
        return
    if data == "ad:searchuser":
        await search_prompt(update, context, "ad_search_user", shop_owner_id)
        return
    if data == "su:searchseller":
        await search_prompt(update, context, "su_search_seller", SUPER_ADMIN_ID)
        return

    await q.answer()

# =========================
# TEXT / MEDIA HANDLER (stateful)
# =========================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, bot_kind: str):
    upsert_user(update.effective_user)

    st, _ = get_state(context)
    if st in ("deposit_amount",):
        await deposit_amount_msg(update, context); return
    if st in ("deposit_proof",):
        await deposit_proof_msg(update, context); return
    if st in ("support_draft",):
        await support_draft_msg(update, context); return
    if st in ("connect_token",):
        await connect_token_msg(update, context); return
    if st in ("bal_edit",):
        await admin_balance_amount_msg(update, context); return
    if st in ("wel_text",):
        await admin_welcome_text_msg(update, context); return
    if st in ("wel_media",):
        await admin_welcome_media_msg(update, context); return
    if st in ("wallet_msg",):
        await admin_walletmsg_text(update, context); return
    if st in ("add_method_title",):
        await admin_add_method_title_msg(update, context); return
    if st in ("add_method_instr",):
        await admin_add_method_instr_msg(update, context); return
    if st in ("add_method_qr",):
        await admin_add_method_qr_msg(update, context); return
    if st in ("broadcast",):
        await admin_broadcast_msg(update, context); return
    if st in ("cat_add_name",):
        await cat_add_name_msg(update, context); return
    if st in ("cat_add_desc",):
        await cat_add_desc_msg(update, context); return
    if st in ("cat_add_media",):
        await cat_add_media_msg(update, context); return
    if st in ("sub_add_name",):
        await sub_add_name_msg(update, context); return
    if st in ("sub_add_desc",):
        await sub_add_desc_msg(update, context); return
    if st in ("sub_add_media",):
        await sub_add_media_msg(update, context); return
    if st in ("prd_add_name",):
        await prd_add_name_msg(update, context); return
    if st in ("prd_add_price",):
        await prd_add_price_msg(update, context); return
    if st in ("prd_add_desc",):
        await prd_add_desc_msg(update, context); return
    if st in ("prd_add_media",):
        await prd_add_media_msg(update, context); return
    if st in ("pe_value",):
        await pe_value_msg(update, context); return
    if st in ("keys_lines",):
        await keys_lines_msg(update, context); return
    if st in ("prd_link_set",):
        await prd_link_set_msg(update, context); return
    if st in ("ad_search_user", "su_search_seller"):
        await search_user_msg(update, context); return

# =========================
# HANDLER REGISTRY
# =========================
def register_handlers(app: Application, shop_owner_id: int, bot_kind: str):
    app.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, shop_owner_id)))
    app.add_handler(CallbackQueryHandler(lambda u, c: on_callback(u, c, shop_owner_id, bot_kind)))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, lambda u, c: on_message(u, c, shop_owner_id, bot_kind)))

# =========================
# MASTER RUNNER
# =========================
async def start_all_seller_bots():
    bots = list_enabled_seller_bots()
    for r in bots:
        sid = int(r["seller_id"])
        if not seller_active(sid):
            continue
        try:
            await MANAGER.start_seller_bot(sid, r["bot_token"])
        except Exception:
            log.exception("Failed starting seller bot %s", sid)

async def main():
    init_db()

    master = Application.builder().token(BOT_TOKEN).build()
    register_handlers(master, shop_owner_id=SUPER_ADMIN_ID, bot_kind="master")

    await master.initialize()
    await master.start()
    asyncio.create_task(master.updater.start_polling(drop_pending_updates=True))

    # start seller bots
    await start_all_seller_bots()

    asyncio.create_task(watchdog())

    log.info("MASTER bot running. SUPER_ADMIN_ID=%s", SUPER_ADMIN_ID)
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
