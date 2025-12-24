# AutoPanel Telegram Bot - FULL (single-file) - Railway Ready
# python-telegram-bot==20.8 | Python 3.11+
#
# ✅ Multi-shop: Main Shop (Super Admin) + Seller Shops (deep link /start s_<seller_id>)
# ✅ Seller customers are locked to seller shop (cannot see main shop)
# ✅ Main Shop users: Products / Wallet / History / Support / Become Seller
# ✅ Seller shop users: Products / Wallet / History / Support (NO Become Seller, NO Main Shop)
# ✅ Seller (owner) menu: Products / Wallet / History / Support / Admin Panel / Subscription / Main Shop / Share My Shop
# ✅ Super Admin menu: all + Admin Panel + Super Admin button (only for SUPER_ADMIN_ID)
# ✅ Deposits require photo proof; approve/reject by shop owner (seller for their shop, super admin for main shop)
# ✅ Support Inbox: user drafts message → presses DONE → ticket sent; owners can reply; messages grouped per ticket
# ✅ History: clean format (Deposited / Purchase / Subscription / Balance edits)
# ✅ Admin Panel (Seller & Super Admin): category → co-category → products, edit product, add keys, set private link, edit price/desc/media
# ✅ Keys as stock: 1 line = 1 stock; 0 = out of stock; users choose qty +/- to buy
# ✅ Purchase delivers keys + optional "Get File" button to reveal private link
# ✅ Sellers can extend subscription (+SELLER_SUB_DAYS) from MAIN SHOP balance (deducts there)
# ✅ Super Admin: manage sellers (ban shop/panel, restrict days), view/edit main-shop users & sellers, reply support, ban users from shop
#
# ENV:
#   BOT_TOKEN (required)
#   SUPER_ADMIN_ID (required)  (alias ADMIN_ID supported)
#   STORE_NAME (required)
#   CURRENCY (required)
# Optional:
#   DB_FILE (default data.db)
#   SELLER_SUB_PRICE (default 10)
#   SELLER_SUB_DAYS (default 30)

import os
import re
import time
import sqlite3
import logging
from typing import Optional, List, Dict, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

# -------------------- Config --------------------
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN")

SUPER_ADMIN_ID = int((os.getenv("SUPER_ADMIN_ID") or os.getenv("ADMIN_ID") or "0").strip() or "0")
if SUPER_ADMIN_ID <= 0:
    raise RuntimeError("Missing SUPER_ADMIN_ID / ADMIN_ID")

STORE_NAME = (os.getenv("STORE_NAME") or "AutoPanel").strip()
CURRENCY = (os.getenv("CURRENCY") or "USDT").strip()

BRAND_TEXT = "Bot created by @RekkoOwn\nGroup : @AutoPanels"

DB_FILE = (os.getenv("DB_FILE") or "data.db").strip()
SELLER_SUB_PRICE = float((os.getenv("SELLER_SUB_PRICE") or "10").strip() or "10")
SELLER_SUB_DAYS = int((os.getenv("SELLER_SUB_DAYS") or "30").strip() or "30")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("autopanel")

# -------------------- Helpers --------------------
def ts() -> int:
    return int(time.time())

def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def money(x: float) -> str:
    x = float(x)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.2f}".rstrip("0").rstrip(".")

def is_super(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)

def two_cols(btns: List[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])
    return InlineKeyboardMarkup(rows)

async def safe_delete(app: Application, chat_id: int, message_id: int) -> None:
    try:
        await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

async def delete_cb_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.callback_query and update.callback_query.message:
            m = update.callback_query.message
            await safe_delete(context.application, m.chat_id, m.message_id)
    except Exception:
        pass

def parse_start_arg(arg: str) -> Optional[int]:
    m = re.match(r"^s_(\d+)$", (arg or "").strip())
    return int(m.group(1)) if m else None

# -------------------- DB init --------------------
def init_db() -> None:
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

    # which shop user is currently in
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
        user_id INTEGER PRIMARY KEY,
        shop_owner_id INTEGER NOT NULL,
        locked INTEGER DEFAULT 0
    )""")

    # main seller record
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sellers(
        seller_id INTEGER PRIMARY KEY,
        sub_until INTEGER DEFAULT 0,
        banned_shop INTEGER DEFAULT 0,
        banned_panel INTEGER DEFAULT 0,
        restricted_until INTEGER DEFAULT 0
    )""")

    # per shop settings (wallet & welcome)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shop_settings(
        shop_owner_id INTEGER PRIMARY KEY,
        wallet_message TEXT DEFAULT '',
        welcome_text TEXT DEFAULT '',
        welcome_file_id TEXT DEFAULT '',
        welcome_file_type TEXT DEFAULT '',
        seller_desc TEXT DEFAULT ''
    )""")

    # balances per shop
    cur.execute("""
    CREATE TABLE IF NOT EXISTS balances(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        balance REAL DEFAULT 0,
        PRIMARY KEY(shop_owner_id, user_id)
    )""")

    # payment methods
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_methods(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        instructions TEXT NOT NULL
    )""")


    # bans per shop
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_bans(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        banned INTEGER DEFAULT 1,
        PRIMARY KEY(shop_owner_id, user_id)
    )""")

    # catalog
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
        tg_link TEXT DEFAULT ''
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

    # clean history
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        amount REAL DEFAULT 0,
        note TEXT DEFAULT '',
        qty INTEGER DEFAULT 1,
        created_at INTEGER NOT NULL
    )""")

    # deposits
    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposit_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        proof_file_id TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        handled_by INTEGER DEFAULT 0,
        handled_at INTEGER DEFAULT 0,
        admin_msg_chat_id INTEGER DEFAULT 0,
        admin_msg_id INTEGER DEFAULT 0
    )""")

    # support tickets
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL,
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

    conn.commit()
    conn.close()

    ensure_shop_settings(SUPER_ADMIN_ID)
    s = get_shop_settings(SUPER_ADMIN_ID)
    if not (s["welcome_text"] or "").strip():
        set_shop_setting(SUPER_ADMIN_ID, "welcome_text",
                         f"✅ Welcome to <b>{esc(STORE_NAME)}</b>\nGet your 24/7 Store Panel Here !!\n\nBot created by @RekkoOwn\nGroup : @AutoPanels")
    if not (s["seller_desc"] or "").strip():
        set_shop_setting(SUPER_ADMIN_ID, "seller_desc",
                         "⭐ <b>Become a Seller</b>\n\n"
                         "✅ Your own shop\n"
                         "✅ Your own products & wallet\n"
                         "✅ Your own deposits & support\n\n"
                         f"Price: <b>{money(SELLER_SUB_PRICE)} {esc(CURRENCY)}</b> / <b>{SELLER_SUB_DAYS} days</b>\n"
                         "Renew early to stack days.")

def ensure_shop_settings(shop_owner_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO shop_settings(shop_owner_id, wallet_message, welcome_text, welcome_file_id, welcome_file_type, seller_desc) "
            "VALUES(?,?,?,?,?,?)",
            (shop_owner_id, "", f"✅ Welcome to <b>{esc(STORE_NAME)}</b>\nGet your 24/7 Store Panel Here !!", "", "", ""),
        )
        conn.commit()
    conn.close()


def ensure_payment_methods(shop_owner_id: int):
    """Ensure at least TRC-20 exists for this shop. Uses wallet_message as default instructions."""
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(1) c FROM payment_methods WHERE shop_owner_id=?", (shop_owner_id,))
    c = cur.fetchone()["c"]
    if int(c or 0) == 0:
        s = get_shop_settings(shop_owner_id)
        wm = (s["wallet_message"] or "").strip()
        if not wm:
            wm = "Send deposit proof photo after you pay."
        cur.execute("INSERT INTO payment_methods(shop_owner_id,name,instructions) VALUES(?,?,?)",
                    (shop_owner_id, "TRC-20", wm))
        conn.commit()
    conn.close()

def list_payment_methods(shop_owner_id: int):
    ensure_payment_methods(shop_owner_id)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id,name,instructions FROM payment_methods WHERE shop_owner_id=? ORDER BY id ASC", (shop_owner_id,))
    rows = cur.fetchall(); conn.close()
    return rows

def get_payment_method(shop_owner_id: int, method_id: int):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id,name,instructions FROM payment_methods WHERE shop_owner_id=? AND id=?",
                (shop_owner_id, method_id))
    r = cur.fetchone(); conn.close()
    return r

def upsert_payment_method(shop_owner_id: int, name: str, instructions: str):
    name = (name or "").strip()
    instructions = (instructions or "").strip()
    if not name:
        return
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT INTO payment_methods(shop_owner_id,name,instructions) VALUES(?,?,?)",
                (shop_owner_id, name, instructions or ""))
    conn.commit(); conn.close()

def update_payment_method_text(shop_owner_id: int, method_id: int, instructions: str):
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE payment_methods SET instructions=? WHERE shop_owner_id=? AND id=?",
                ((instructions or ""), shop_owner_id, method_id))
    conn.commit(); conn.close()

def delete_payment_method(shop_owner_id: int, method_id: int):
    conn = db(); cur = conn.cursor()
    cur.execute("DELETE FROM payment_methods WHERE shop_owner_id=? AND id=?", (shop_owner_id, method_id))
    conn.commit(); conn.close()
def get_shop_settings(shop_owner_id: int) -> sqlite3.Row:
    ensure_shop_settings(shop_owner_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    r = cur.fetchone()
    conn.close()
    return r

def set_shop_setting(shop_owner_id: int, field: str, value: str) -> None:
    if field not in {"wallet_message", "welcome_text", "welcome_file_id", "welcome_file_type", "seller_desc"}:
        raise ValueError("Bad field")
    ensure_shop_settings(shop_owner_id)
    conn = db()
    cur = conn.cursor()
    cur.execute(f"UPDATE shop_settings SET {field}=? WHERE shop_owner_id=?", (value or "", shop_owner_id))
    conn.commit()
    conn.close()

def upsert_user(u) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users(user_id, username, first_name, last_name, last_seen) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, "
        "last_name=excluded.last_name, last_seen=excluded.last_seen",
        (u.id, u.username or "", u.first_name or "", u.last_name or "", ts()),
    )
    conn.commit()
    conn.close()

def user_display(uid: int) -> str:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT username, first_name, last_name FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone(); conn.close()
    if not r:
        return str(uid)
    un = (r["username"] or "").strip()
    if un:
        return f"@{un}"
    name = " ".join([x for x in [(r["first_name"] or "").strip(), (r["last_name"] or "").strip()] if x]).strip()
    return name or str(uid)

def shop_name(shop_owner_id: int) -> str:
    if shop_owner_id == SUPER_ADMIN_ID:
        return f"{STORE_NAME} (Main Shop)"
    return f"{user_display(shop_owner_id)} Shop"

def set_session(uid: int, shop_owner_id: int, locked: int) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions(user_id, shop_owner_id, locked) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET shop_owner_id=excluded.shop_owner_id, locked=excluded.locked",
        (uid, shop_owner_id, locked),
    )
    conn.commit(); conn.close()

def get_session(uid: int) -> Tuple[int, int]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT shop_owner_id, locked FROM sessions WHERE user_id=?", (uid,))
    r = cur.fetchone(); conn.close()
    if not r:
        return SUPER_ADMIN_ID, 0
    return int(r["shop_owner_id"]), int(r["locked"] or 0)

def ensure_balance(shop_owner_id: int, uid: int) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,0)", (shop_owner_id, uid))
    conn.commit(); conn.close()

def get_balance(shop_owner_id: int, uid: int) -> float:
    ensure_balance(shop_owner_id, uid)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT balance FROM balances WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    r = cur.fetchone(); conn.close()
    return float(r["balance"]) if r else 0.0

def set_balance(shop_owner_id: int, uid: int, val: float) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,?) "
        "ON CONFLICT(shop_owner_id, user_id) DO UPDATE SET balance=excluded.balance",
        (shop_owner_id, uid, max(0.0, float(val))),
    )
    conn.commit(); conn.close()

def log_tx(shop_owner_id: int, uid: int, kind: str, amount: float, note: str = "", qty: int = 1) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions(shop_owner_id, user_id, kind, amount, note, qty, created_at) VALUES(?,?,?,?,?,?,?)",
        (shop_owner_id, uid, kind, float(amount), note or "", int(qty or 1), ts()),
    )
    conn.commit(); conn.close()

def is_banned(shop_owner_id: int, uid: int) -> bool:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT banned FROM user_bans WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    r = cur.fetchone(); conn.close()
    return bool(r and int(r["banned"] or 0) == 1)

def set_ban(shop_owner_id: int, uid: int, banned: int) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_bans(shop_owner_id, user_id, banned) VALUES(?,?,?) "
        "ON CONFLICT(shop_owner_id, user_id) DO UPDATE SET banned=excluded.banned",
        (shop_owner_id, uid, int(banned)),
    )
    conn.commit(); conn.close()

# -------------------- Seller controls --------------------
def ensure_seller(uid: int) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO sellers(seller_id, sub_until) VALUES(?,0)", (uid,))
    conn.commit(); conn.close()

def seller_row(uid: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM sellers WHERE seller_id=?", (uid,))
    r = cur.fetchone(); conn.close()
    return r

def seller_days_left(uid: int) -> int:
    if is_super(uid):
        return 10**9
    r = seller_row(uid)
    if not r:
        return 0
    return max(0, int(r["sub_until"] or 0) - ts()) // 86400

def seller_active(uid: int) -> bool:
    if is_super(uid):
        return True
    r = seller_row(uid)
    if not r:
        return False
    if int(r["banned_shop"] or 0) == 1:
        return False
    if int(r["restricted_until"] or 0) > ts():
        return False
    return int(r["sub_until"] or 0) > ts()

def seller_panel_allowed(uid: int) -> bool:
    if is_super(uid):
        return True
    r = seller_row(uid)
    if not r:
        return False
    if int(r["banned_panel"] or 0) == 1:
        return False
    return seller_active(uid)

def add_seller_days(uid: int, days: int) -> None:
    ensure_seller(uid)
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT sub_until FROM sellers WHERE seller_id=?", (uid,))
    r = cur.fetchone()
    base = max(int(r["sub_until"] or 0), ts())
    cur.execute("UPDATE sellers SET sub_until=? WHERE seller_id=?", (base + int(days) * 86400, uid))
    conn.commit(); conn.close()

def seller_extend_from_main_shop(uid: int) -> bool:
    bal = get_balance(SUPER_ADMIN_ID, uid)
    if bal < SELLER_SUB_PRICE:
        return False
    set_balance(SUPER_ADMIN_ID, uid, bal - SELLER_SUB_PRICE)
    log_tx(SUPER_ADMIN_ID, uid, "seller_sub", -SELLER_SUB_PRICE, "Subscription payment", 1)
    add_seller_days(uid, SELLER_SUB_DAYS)
    return True

# -------------------- Catalog helpers --------------------
def count_stock(shop_owner_id: int, product_id: int) -> int:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(1) AS c FROM product_keys WHERE shop_owner_id=? AND product_id=? AND delivered_once=0",
                (shop_owner_id, product_id))
    r = cur.fetchone(); conn.close()
    return int(r["c"] or 0) if r else 0

def get_product(shop_owner_id: int, product_id: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE shop_owner_id=? AND id=?", (shop_owner_id, product_id))
    r = cur.fetchone(); conn.close()
    return r

def get_category(shop_owner_id: int, cat_id: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE shop_owner_id=? AND id=?", (shop_owner_id, cat_id))
    r = cur.fetchone(); conn.close()
    return r

def get_cocat(shop_owner_id: int, cocat_id: int) -> Optional[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM cocategories WHERE shop_owner_id=? AND id=?", (shop_owner_id, cocat_id))
    r = cur.fetchone(); conn.close()
    return r

# -------------------- UI: menus --------------------
def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    shop_owner_id, locked = get_session(uid)

    # locked = seller-shop customer (cannot see main shop)
    if locked == 1:
        btns = [
            InlineKeyboardButton("🛒 Products", callback_data="m:products"),
            InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
            InlineKeyboardButton("📜 History", callback_data="m:history"),
            InlineKeyboardButton("🆘 Support", callback_data="m:support"),
        ]
        return two_cols(btns)

    # super admin in main shop
    if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID:
        btns = [
            InlineKeyboardButton("🛒 Products", callback_data="m:products"),
            InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
            InlineKeyboardButton("📜 History", callback_data="m:history"),
            InlineKeyboardButton("🆘 Support", callback_data="m:support"),
            InlineKeyboardButton("⭐ Become Seller", callback_data="m:become_seller"),
            InlineKeyboardButton("🛠 Admin Panel", callback_data="m:admin"),
            InlineKeyboardButton("👑 Super Admin", callback_data="m:super"),
        ]
        return two_cols(btns)

    # seller owner in own shop
    if seller_panel_allowed(uid) and shop_owner_id == uid:
        btns = [
            InlineKeyboardButton("🛒 Products", callback_data="m:products"),
            InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
            InlineKeyboardButton("📜 History", callback_data="m:history"),
            InlineKeyboardButton("🆘 Support", callback_data="m:support"),
            InlineKeyboardButton("🛠 Admin Panel", callback_data="m:admin"),
            InlineKeyboardButton("📅 Subscription", callback_data="m:sub"),
            InlineKeyboardButton("🏬 Main Shop", callback_data="m:mainshop"),
            InlineKeyboardButton("🔗 Share My Shop", callback_data="m:share"),
        ]
        return two_cols(btns)

    # main shop user
    if shop_owner_id == SUPER_ADMIN_ID:
        btns = [
            InlineKeyboardButton("🛒 Products", callback_data="m:products"),
            InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
            InlineKeyboardButton("📜 History", callback_data="m:history"),
            InlineKeyboardButton("🆘 Support", callback_data="m:support"),
            InlineKeyboardButton("⭐ Become Seller", callback_data="m:become_seller"),
        ]
        return two_cols(btns)

    # fallback (shouldn't normally happen)
    btns = [
        InlineKeyboardButton("🛒 Products", callback_data="m:products"),
        InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
        InlineKeyboardButton("📜 History", callback_data="m:history"),
        InlineKeyboardButton("🆘 Support", callback_data="m:support"),
    ]
    return two_cols(btns)

def back_home_kb() -> InlineKeyboardMarkup:
    return two_cols([
        InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
        InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
    ])

# -------------------- Navigation + mode --------------------
def set_mode(context: ContextTypes.DEFAULT_TYPE, mode: str, data: Dict) -> None:
    context.user_data["mode"] = mode
    context.user_data["mode_data"] = data

def clear_mode(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("mode", None)
    context.user_data.pop("mode_data", None)

def push_nav(context: ContextTypes.DEFAULT_TYPE, tag: str) -> None:
    st = context.user_data.get("nav_stack") or []
    st.append(tag)
    context.user_data["nav_stack"] = st

def pop_nav(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    st = context.user_data.get("nav_stack") or []
    if not st:
        return None
    tag = st.pop()
    context.user_data["nav_stack"] = st
    return tag

# -------------------- Welcome --------------------
async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    s = get_shop_settings(shop_owner_id)

    title = f"🏬 <b>{esc(shop_name(shop_owner_id))}</b>\n\n"
    text = (s["welcome_text"] or "").strip()
    file_id = (s["welcome_file_id"] or "").strip()
    ftype = (s["welcome_file_type"] or "").strip()

    # Branding: show only in WELCOME message.
    # Main shop: show BRAND_TEXT only for non-sellers (and not super admin).
    # Seller shops / sellers: hide branding here (keeps your old behavior).
    # First strip any existing branding lines:
    text = re.sub(r"\n?Bot created by @RekkoOwn\s*$", "", text).strip()
    text = re.sub(r"\n?Group\s*:\s*@AutoPanels\s*$", "", text).strip()

    if shop_owner_id == SUPER_ADMIN_ID and (not seller_active(uid)) and (not is_super(uid)):
        if BRAND_TEXT not in text:
            text = (text + "\n\n" + BRAND_TEXT).strip()

    caption = title + (text if text else "")

    if file_id and ftype == "photo":
        await context.bot.send_photo(update.effective_chat.id, photo=file_id, caption=caption,
                                     parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(uid))
    elif file_id and ftype == "video":
        await context.bot.send_video(update.effective_chat.id, video=file_id, caption=caption,
                                     parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(uid))
    else:
        await context.bot.send_message(update.effective_chat.id, text=caption,
                                       parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(uid))

# -------------------- /start --------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    arg = context.args[0] if context.args else ""
    seller_id = parse_start_arg(arg)

    # Enter seller shop via deep link -> lock
    if seller_id and seller_id != SUPER_ADMIN_ID and seller_active(seller_id):
        ensure_shop_settings(seller_id)
        set_session(uid, seller_id, 1)
        ensure_balance(seller_id, uid)
        await send_welcome(update, context)
        return

    # Super admin always in main shop
    if is_super(uid):
        set_session(uid, SUPER_ADMIN_ID, 0)
        ensure_balance(SUPER_ADMIN_ID, uid)
        await send_welcome(update, context)
        return

    # Seller owner: default to own shop (NOT locked)
    if seller_panel_allowed(uid):
        ensure_shop_settings(uid)
        set_session(uid, uid, 0)
        ensure_balance(uid, uid)
        ensure_balance(SUPER_ADMIN_ID, uid)  # for subscription payments
        await send_welcome(update, context)
        return

    # Normal user -> main shop
    set_session(uid, SUPER_ADMIN_ID, 0)
    ensure_balance(SUPER_ADMIN_ID, uid)
    await send_welcome(update, context)

async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE, delete_current: bool = True) -> None:
    clear_mode(context)
    context.user_data.pop("qty", None)
    context.user_data.pop("draft_support", None)
    context.user_data.pop("draft_deposit_amt", None)
    if delete_current:
        await delete_cb_message(update, context)
    await send_welcome(update, context)

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    context.user_data.pop("draft_support", None)
    context.user_data.pop("draft_deposit_amt", None)
    await delete_cb_message(update, context)
    pop_nav(context)  # current
    prev = pop_nav(context)
    if not prev:
        await send_welcome(update, context); return
    if prev == "cats":
        await show_categories(update, context); return
    if prev.startswith("cat:"):
        await show_cocats(update, context, int(prev.split(":")[1])); return
    if prev.startswith("cocat:"):
        await show_products(update, context, int(prev.split(":")[1])); return
    if prev.startswith("prod:"):
        await show_product(update, context, int(prev.split(":")[1])); return
    if prev.startswith("admincats"):
        await admin_categories(update, context); return
    if prev.startswith("admincat:"):
        await admin_cocats(update, context, int(prev.split(":")[1])); return
    if prev.startswith("admincocat:"):
        await admin_products(update, context, int(prev.split(":")[1])); return
    if prev.startswith("adminprod:"):
        await admin_product_edit(update, context, int(prev.split(":")[1])); return
    await send_welcome(update, context)

# -------------------- Public browsing: Categories / Products --------------------
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    if is_banned(shop_owner_id, uid):
        await update.callback_query.answer("You are banned from this shop.", show_alert=True); return
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id, name FROM categories WHERE shop_owner_id=? ORDER BY id DESC", (shop_owner_id,))
    rows = cur.fetchall(); conn.close()
    btns = [InlineKeyboardButton(r["name"], callback_data=f"cat:{r['id']}") for r in rows]
    btns += [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]
    push_nav(context, "cats")
    await update.callback_query.message.reply_text("🛒 <b>Categories</b>", parse_mode=ParseMode.HTML,
                                                  reply_markup=two_cols(btns) if btns else back_home_kb())

async def show_cocats(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    cat = get_category(shop_owner_id, cat_id)
    if not cat:
        await update.callback_query.answer("Not found.", show_alert=True); return
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id, name FROM cocategories WHERE shop_owner_id=? AND category_id=? ORDER BY id DESC",
                (shop_owner_id, cat_id))
    rows = cur.fetchall(); conn.close()
    push_nav(context, f"cat:{cat_id}")
    btns = [InlineKeyboardButton(r["name"], callback_data=f"cocat:{r['id']}") for r in rows]
    btns += [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]
    await update.callback_query.message.reply_text(f"📂 <b>{esc(cat['name'])}</b>", parse_mode=ParseMode.HTML,
                                                  reply_markup=two_cols(btns))

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE, cocat_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    cc = get_cocat(shop_owner_id, cocat_id)
    if not cc:
        await update.callback_query.answer("Not found.", show_alert=True); return
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id, name FROM products WHERE shop_owner_id=? AND cocategory_id=? ORDER BY id DESC",
                (shop_owner_id, cocat_id))
    rows = cur.fetchall(); conn.close()
    push_nav(context, f"cocat:{cocat_id}")
    btns = [InlineKeyboardButton(r["name"], callback_data=f"prod:{r['id']}") for r in rows]
    btns += [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]
    await update.callback_query.message.reply_text(f"🧾 <b>{esc(cc['name'])}</b>", parse_mode=ParseMode.HTML,
                                                  reply_markup=two_cols(btns))

# -------------------- Product view + buy --------------------
async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    prod = get_product(shop_owner_id, product_id)
    if not prod:
        await update.callback_query.answer("Not found.", show_alert=True); return
    stock = count_stock(shop_owner_id, product_id)
    qty_map = context.user_data.get("qty") or {}
    qty = int(qty_map.get(str(product_id), 1))
    qty = max(1, qty)
    qty = min(qty, stock) if stock > 0 else 1
    qty_map[str(product_id)] = qty
    context.user_data["qty"] = qty_map
    total = float(prod["price"]) * qty
    desc = (prod["description"] or "").strip()
    push_nav(context, f"prod:{product_id}")

    txt = (f"🛍 <b>{esc(prod['name'])}</b>\n"
           f"Price: <b>{money(prod['price'])} {esc(CURRENCY)}</b>\n"
           f"Stock: <b>{stock}</b>\n\n"
           f"Quantity: <b>{qty}</b>\n"
           f"Total: <b>{money(total)} {esc(CURRENCY)}</b>")
    if desc:
        txt += f"\n\n📝 {esc(desc)}"

    rows = [
        [InlineKeyboardButton("➖", callback_data=f"qty:{product_id}:dec"),
         InlineKeyboardButton("➕", callback_data=f"qty:{product_id}:inc")],
        [InlineKeyboardButton("✅ Buy", callback_data=f"buy:{product_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
    ]

    # send product media if exists
    file_id = (prod["file_id"] or "").strip()
    ftype = (prod["file_type"] or "").strip()
    await delete_cb_message(update, context)
    if file_id and ftype == "photo":
        await context.bot.send_photo(update.effective_chat.id, photo=file_id, caption=txt,
                                     parse_mode=ParseMode.HTML, reply_markup=kb(rows))
    elif file_id and ftype == "video":
        await context.bot.send_video(update.effective_chat.id, video=file_id, caption=txt,
                                     parse_mode=ParseMode.HTML, reply_markup=kb(rows))
    else:
        await context.bot.send_message(update.effective_chat.id, txt, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

async def change_qty(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, delta: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    prod = get_product(shop_owner_id, product_id)
    if not prod:
        await update.callback_query.answer("Not found.", show_alert=True); return
    stock = count_stock(shop_owner_id, product_id)
    qty_map = context.user_data.get("qty") or {}
    qty = int(qty_map.get(str(product_id), 1))
    qty = max(1, qty + delta)
    if stock > 0:
        qty = min(qty, stock)
    qty_map[str(product_id)] = qty
    context.user_data["qty"] = qty_map
    await update.callback_query.answer(f"Qty: {qty}")
    await show_product(update, context, product_id)

async def notify_purchase(context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, buyer_id: int, prod: sqlite3.Row, qty: int, total: float) -> None:
    msg = ("🛒 <b>New Purchase</b>\n\n"
           f"Shop: <b>{esc(shop_name(shop_owner_id))}</b>\n"
           f"Buyer: <b>{esc(user_display(buyer_id))}</b>\n"
           f"Product: <b>{esc(prod['name'])}</b>\n"
           f"Qty: <b>{qty}</b>\n"
           f"Paid: <b>{money(total)} {esc(CURRENCY)}</b>")
    # seller shop: notify seller + super
    if shop_owner_id != SUPER_ADMIN_ID:
        for rid in [shop_owner_id, SUPER_ADMIN_ID]:
            try: await context.bot.send_message(rid, msg, parse_mode=ParseMode.HTML)
            except Exception: pass
    else:
        try: await context.bot.send_message(SUPER_ADMIN_ID, msg, parse_mode=ParseMode.HTML)
        except Exception: pass

async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    if is_banned(shop_owner_id, uid):
        await update.callback_query.answer("You are banned.", show_alert=True); return
    prod = get_product(shop_owner_id, product_id)
    if not prod:
        await update.callback_query.answer("Not found.", show_alert=True); return

    qty_map = context.user_data.get("qty") or {}
    qty = int(qty_map.get(str(product_id), 1))
    qty = max(1, qty)

    stock = count_stock(shop_owner_id, product_id)
    if stock <= 0:
        await update.callback_query.answer("Out of stock.", show_alert=True); return
    if qty > stock:
        qty = stock

    total = float(prod["price"]) * qty
    bal = get_balance(shop_owner_id, uid)
    if bal < total:
        await update.callback_query.answer("Not enough balance.", show_alert=True); return

    conn = db(); cur = conn.cursor()
    cur.execute("""SELECT id, key_line FROM product_keys
                   WHERE shop_owner_id=? AND product_id=? AND delivered_once=0
                   ORDER BY id ASC LIMIT ?""",
                (shop_owner_id, product_id, qty))
    keys = cur.fetchall()
    if len(keys) < qty:
        conn.close()
        await update.callback_query.answer("Out of stock.", show_alert=True); return

    set_balance(shop_owner_id, uid, bal - total)
    now = ts()
    key_lines = []
    for k in keys:
        key_lines.append(k["key_line"])
        cur.execute("UPDATE product_keys SET delivered_once=1, delivered_to=?, delivered_at=? WHERE id=?",
                    (uid, now, k["id"]))
    conn.commit(); conn.close()

    log_tx(shop_owner_id, uid, "purchase", -total, note=prod["name"], qty=qty)
    await notify_purchase(context, shop_owner_id, uid, prod, qty, total)

    rows = []
    if (prod["tg_link"] or "").strip():
        rows.append([InlineKeyboardButton("📁 Get File", callback_data=f"getfile:{product_id}")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")])

    msg = ("✅ <b>Purchase Successful</b>\n\n"
           f"Product: <b>{esc(prod['name'])}</b>\n"
           f"Quantity: <b>{qty}</b>\n"
           f"Paid: <b>{money(total)} {esc(CURRENCY)}</b>\n"
           f"Total Balance: <b>{money(get_balance(shop_owner_id, uid))} {esc(CURRENCY)}</b>\n\n"
           "🔑 <b>Keys</b>\n" + "\n".join([esc(x) for x in key_lines]))

    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, msg, parse_mode=ParseMode.HTML,
                                   reply_markup=kb(rows), disable_web_page_preview=True)

async def get_file(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    prod = get_product(get_session(update.effective_user.id)[0], product_id)
    if not prod:
        await update.callback_query.answer("Not found.", show_alert=True); return
    link = (prod["tg_link"] or "").strip()
    if not link:
        await update.callback_query.answer("No link set.", show_alert=True); return
    await update.callback_query.answer()
    await context.bot.send_message(update.effective_chat.id,
                                   f"📁 <b>Private Link</b>\n{esc(link)}",
                                   parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True,
                                   reply_markup=back_home_kb())

# -------------------- Wallet + Deposits --------------------
async def wallet_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    bal = get_balance(shop_owner_id, uid)
    s = get_shop_settings(shop_owner_id)
    ensure_payment_methods(shop_owner_id)
    wallet_msg = (s["wallet_message"] or "").strip()
    if not wallet_msg:
        wallet_msg = "Send deposit proof photo after you pay."
    txt = (f"💰 <b>Wallet</b>\n\n"
           f"Balance: <b>{money(bal)} {esc(CURRENCY)}</b>\n\n"
           f"{esc(wallet_msg)}")
    rows = [
        [InlineKeyboardButton("➕ Top up", callback_data="dep:start")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
         InlineKeyboardButton("⬅️ Back", callback_data="nav:back")],
    ]
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, txt, parse_mode=ParseMode.HTML, reply_markup=kb(rows))


async def pm_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    # only shop owner or super admin can manage
    if not (uid == shop_owner_id or is_super(uid)):
        await q.message.reply_text("❌ Not allowed.")
        return
    ensure_payment_methods(shop_owner_id)
    methods = list_payment_methods(shop_owner_id)
    await delete_cb_message(update, context)

    rows = []
    for r in methods:
        rows.append([InlineKeyboardButton(f"✏️ {r['name']}", callback_data=f"pm:edit:{r['id']}"),
                     InlineKeyboardButton("🗑", callback_data=f"pm:del:{r['id']}")])
    rows.append([InlineKeyboardButton("➕ Add Method", callback_data="pm:add")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
                 InlineKeyboardButton("🏠 Home", callback_data="nav:home")])

    await context.bot.send_message(update.effective_chat.id,
                                   "💳 <b>Payment Methods</b>\nEdit the instructions for each method.",
                                   parse_mode=ParseMode.HTML,
                                   reply_markup=kb(rows))

async def pm_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    if not (uid == shop_owner_id or is_super(uid)):
        await update.callback_query.message.reply_text("❌ Not allowed.")
        return
    set_mode(context, "pm_add_name", {})
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "Send the payment method name (example: PAYPAL):", reply_markup=back_home_kb())

async def pm_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE, method_id: int) -> None:
    await update.callback_query.answer()
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    if not (uid == shop_owner_id or is_super(uid)):
        await update.callback_query.message.reply_text("❌ Not allowed.")
        return
    pm = get_payment_method(shop_owner_id, method_id)
    if not pm:
        await update.callback_query.message.reply_text("Not found.")
        return
    context.user_data["pm_edit_id"] = method_id
    set_mode(context, "pm_edit_text", {})
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id,
                                   f"Send new instructions for <b>{esc(pm['name'])}</b>:",
                                   parse_mode=ParseMode.HTML,
                                   reply_markup=back_home_kb())

async def pm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, method_id: int) -> None:
    await update.callback_query.answer()
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    if not (uid == shop_owner_id or is_super(uid)):
        await update.callback_query.message.reply_text("❌ Not allowed.")
        return
    delete_payment_method(shop_owner_id, method_id)
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "✅ Deleted.", reply_markup=kb([[InlineKeyboardButton("⬅️ Back", callback_data="pm:list")]]))
async def dep_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    ensure_payment_methods(shop_owner_id)
    methods = list_payment_methods(shop_owner_id)
    await delete_cb_message(update, context)

    if not methods:
        await context.bot.send_message(update.effective_chat.id,
                                       "❌ No payment methods available.",
                                       reply_markup=back_home_kb())
        return

    rows = []
    for r in methods:
        rows.append([InlineKeyboardButton(str(r["name"]), callback_data=f"dep:method:{r['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="m:wallet")])
    await context.bot.send_message(update.effective_chat.id,
                                   "➕ <b>Top up</b>\n\nSelect a payment method:",
                                   parse_mode=ParseMode.HTML,
                                   reply_markup=kb(rows))


async def dep_method_select(update: Update, context: ContextTypes.DEFAULT_TYPE, method_id: int) -> None:
    await update.callback_query.answer()
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    pm = get_payment_method(shop_owner_id, method_id)
    if not pm:
        await delete_cb_message(update, context)
        await context.bot.send_message(update.effective_chat.id, "❌ Payment method not found.", reply_markup=back_home_kb())
        return

    context.user_data["draft_dep_method_id"] = int(method_id)

    # show custom instructions, then ask for amount
    await delete_cb_message(update, context)
    msg = f"💳 <b>{esc(pm['name'])}</b>\n\n{esc(pm['instructions'] or '')}\n\nSend the amount you deposited (numbers only)."
    set_mode(context, "dep_amount", {})
    await context.bot.send_message(update.effective_chat.id, msg, parse_mode=ParseMode.HTML, reply_markup=back_home_kb())

async def dep_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        amt = float((update.message.text or "").strip())
        if amt <= 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("Send a valid amount (example: 10).", reply_markup=back_home_kb())
        return
    context.user_data["draft_deposit_amt"] = amt
    set_mode(context, "dep_proof", {})
    await update.message.reply_text("Now send a <b>PHOTO</b> proof of your deposit.", parse_mode=ParseMode.HTML,
                                    reply_markup=back_home_kb())

async def dep_proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.photo:
        await update.message.reply_text("Please send a PHOTO.", reply_markup=back_home_kb())
        return
    amt = float(context.user_data.get("draft_deposit_amt") or 0)
    if amt <= 0:
        await update.message.reply_text("Deposit cancelled. Try again.", reply_markup=back_home_kb())
        clear_mode(context); return

    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    proof_file_id = update.message.photo[-1].file_id

    conn = db(); cur = conn.cursor()
    cur.execute("""INSERT INTO deposit_requests(shop_owner_id, user_id, amount, proof_file_id, status, created_at)
                   VALUES(?,?,?,?,?,?)""", (shop_owner_id, uid, amt, proof_file_id, "pending", ts()))
    dep_id = cur.lastrowid
    conn.commit(); conn.close()

    # notify owner(s)
    txt = ("💳 <b>Deposit Request</b>\n\n"
           f"Shop: <b>{esc(shop_name(shop_owner_id))}</b>\n"
           f"User: <b>{esc(user_display(uid))}</b>\n"
           f"Amount: <b>{money(amt)} {esc(CURRENCY)}</b>\n\n"
           f"Request ID: <code>{dep_id}</code>")
    rows = [[InlineKeyboardButton("✅ Approve", callback_data=f"dep:approve:{dep_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"dep:reject:{dep_id}")]]
    # seller shop -> seller only; main shop -> super only
    notify_ids = [shop_owner_id] if shop_owner_id != SUPER_ADMIN_ID else [SUPER_ADMIN_ID]
    for rid in notify_ids:
        try:
            msg = await context.bot.send_photo(rid, photo=proof_file_id, caption=txt,
                                               parse_mode=ParseMode.HTML, reply_markup=kb(rows))
            # store msg ids to delete later
            conn = db(); cur = conn.cursor()
            cur.execute("""UPDATE deposit_requests SET admin_msg_chat_id=?, admin_msg_id=? WHERE id=?""",
                        (rid, msg.message_id, dep_id))
            conn.commit(); conn.close()
        except Exception:
            pass

    clear_mode(context)
    context.user_data.pop("draft_deposit_amt", None)
    await update.message.reply_text("✅ Deposit request sent. Please wait for approval.", reply_markup=two_cols([
        InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
        InlineKeyboardButton("📜 History", callback_data="m:history"),
    ]))

async def dep_handle(update: Update, context: ContextTypes.DEFAULT_TYPE, dep_id: int, approve: bool) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)

    conn = db(); cur = conn.cursor()
    cur.execute("SELECT * FROM deposit_requests WHERE id=?", (dep_id,))
    dep = cur.fetchone()
    if not dep:
        conn.close()
        await update.callback_query.answer("Not found.", show_alert=True); return

    # permission: only shop owner can handle; super can handle main shop
    owner = int(dep["shop_owner_id"])
    if owner == SUPER_ADMIN_ID:
        if not is_super(uid):
            conn.close()
            await update.callback_query.answer("Not allowed.", show_alert=True); return
    else:
        if uid != owner and not is_super(uid):
            conn.close()
            await update.callback_query.answer("Not allowed.", show_alert=True); return

    if dep["status"] != "pending":
        conn.close()
        await update.callback_query.answer("Already handled.", show_alert=True); return

    user_id = int(dep["user_id"])
    amount = float(dep["amount"])
    status = "approved" if approve else "rejected"
    cur.execute("""UPDATE deposit_requests SET status=?, handled_by=?, handled_at=? WHERE id=?""",
                (status, uid, ts(), dep_id))
    conn.commit(); conn.close()

    # apply
    if approve:
        bal = get_balance(owner, user_id)
        set_balance(owner, user_id, bal + amount)
        log_tx(owner, user_id, "deposit", amount, "Deposited", 1)

        try:
            await context.bot.send_message(user_id,
                                           f"✅ Deposit approved: <b>{money(amount)} {esc(CURRENCY)}</b>\n"
                                           f"Total Balance: <b>{money(get_balance(owner, user_id))} {esc(CURRENCY)}</b>",
                                           parse_mode=ParseMode.HTML)
        except Exception:
            pass
    else:
        try:
            await context.bot.send_message(user_id,
                                           f"❌ Deposit rejected: <b>{money(amount)} {esc(CURRENCY)}</b>",
                                           parse_mode=ParseMode.HTML)
        except Exception:
            pass

    # delete admin message
    try:
        chat_id = int(dep["admin_msg_chat_id"] or 0)
        msg_id = int(dep["admin_msg_id"] or 0)
        if chat_id and msg_id:
            await safe_delete(context.application, chat_id, msg_id)
    except Exception:
        pass

    await update.callback_query.answer("Done.")
    await delete_cb_message(update, context)

# -------------------- History --------------------
def fmt_time(t: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(t)))
    except Exception:
        return ""

async def history_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    conn = db(); cur = conn.cursor()
    cur.execute("""SELECT * FROM transactions
                   WHERE shop_owner_id=? AND user_id=?
                   ORDER BY id DESC LIMIT 25""", (shop_owner_id, uid))
    rows = cur.fetchall(); conn.close()

    lines = ["📜 <b>History</b>\n"]
    bal = get_balance(shop_owner_id, uid)
    for r in rows:
        kind = r["kind"]
        amt = float(r["amount"] or 0)
        note = (r["note"] or "").strip()
        qty = int(r["qty"] or 1)
        when = fmt_time(int(r["created_at"] or 0))

        if kind == "deposit":
            lines.append(f"✅ Deposited: <b>{money(amt)} {esc(CURRENCY)}</b>")
        elif kind == "purchase":
            lines.append(f"🛒 Purchased: <b>{esc(note)}</b> x<b>{qty}</b> — <b>{money(-amt)} {esc(CURRENCY)}</b>")
        elif kind == "bal_add":
            lines.append(f"➕ Balance added: <b>{money(amt)} {esc(CURRENCY)}</b>")
        elif kind == "bal_deduct":
            lines.append(f"➖ Balance deducted: <b>{money(-amt)} {esc(CURRENCY)}</b>")
        elif kind == "seller_sub":
            lines.append(f"📅 Subscription: <b>{money(-amt)} {esc(CURRENCY)}</b>")
        else:
            sign = "+" if amt >= 0 else "-"
            lines.append(f"{esc(kind)}: <b>{sign}{money(abs(amt))} {esc(CURRENCY)}</b>")
        if when:
            lines.append(f"🕒 {esc(when)}\n")

    lines.append(f"Total Balance: <b>{money(bal)} {esc(CURRENCY)}</b>")
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "\n".join(lines), parse_mode=ParseMode.HTML,
                                   reply_markup=back_home_kb())

# -------------------- Support Inbox --------------------
def get_open_ticket(shop_owner_id: int, user_id: int) -> Optional[int]:
    conn = db(); cur = conn.cursor()
    cur.execute("""SELECT id FROM tickets
                   WHERE shop_owner_id=? AND user_id=? AND status='open'
                   ORDER BY id DESC LIMIT 1""", (shop_owner_id, user_id))
    r = cur.fetchone(); conn.close()
    return int(r["id"]) if r else None

def create_ticket(shop_owner_id: int, user_id: int) -> int:
    conn = db(); cur = conn.cursor()
    now = ts()
    cur.execute("""INSERT INTO tickets(shop_owner_id, user_id, status, created_at, updated_at)
                   VALUES(?,?,?,?,?)""", (shop_owner_id, user_id, "open", now, now))
    tid = cur.lastrowid
    conn.commit(); conn.close()
    return int(tid)

def add_ticket_msg(ticket_id: int, sender_id: int, text: str) -> None:
    conn = db(); cur = conn.cursor()
    cur.execute("INSERT INTO ticket_messages(ticket_id, sender_id, text, created_at) VALUES(?,?,?,?)",
                (ticket_id, sender_id, text, ts()))
    cur.execute("UPDATE tickets SET updated_at=? WHERE id=?", (ts(), ticket_id))
    conn.commit(); conn.close()

async def support_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    context.user_data["draft_support"] = ""
    set_mode(context, "support_draft", {})
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id,
                                   "🆘 <b>Support</b>\n\nSend your message now.\nWhen finished, press <b>DONE</b>.",
                                   parse_mode=ParseMode.HTML,
                                   reply_markup=two_cols([
                                       InlineKeyboardButton("✅ DONE", callback_data="support:done"),
                                       InlineKeyboardButton("❌ Cancel", callback_data="support:cancel"),
                                   ]))

async def support_collect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    t = (update.message.text or "").strip()
    if not t:
        return
    draft = (context.user_data.get("draft_support") or "")
    draft = (draft + "\n" + t).strip() if draft else t
    context.user_data["draft_support"] = draft
    await update.message.reply_text("Added. Send more or press DONE.", reply_markup=two_cols([
        InlineKeyboardButton("✅ DONE", callback_data="support:done"),
        InlineKeyboardButton("❌ Cancel", callback_data="support:cancel"),
    ]))

async def support_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    msg = (context.user_data.get("draft_support") or "").strip()
    if not msg:
        await update.callback_query.answer("Empty.", show_alert=True); return
    tid = get_open_ticket(shop_owner_id, uid) or create_ticket(shop_owner_id, uid)
    add_ticket_msg(tid, uid, msg)

    # notify owner(s)
    text = ("📩 <b>Support Ticket</b>\n\n"
            f"Shop: <b>{esc(shop_name(shop_owner_id))}</b>\n"
            f"User: <b>{esc(user_display(uid))}</b>\n"
            f"Ticket: <code>{tid}</code>\n\n"
            f"{esc(msg)}")
    rows = [[InlineKeyboardButton("✉️ Reply", callback_data=f"t:reply:{tid}"),
             InlineKeyboardButton("✅ Close", callback_data=f"t:close:{tid}")]]
    notify_ids = [shop_owner_id] if shop_owner_id != SUPER_ADMIN_ID else [SUPER_ADMIN_ID]
    for rid in notify_ids:
        try:
            await context.bot.send_message(rid, text, parse_mode=ParseMode.HTML, reply_markup=kb(rows))
        except Exception:
            pass

    clear_mode(context)
    context.user_data.pop("draft_support", None)
    await update.callback_query.answer("Sent.")
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "✅ Support message sent.", reply_markup=back_home_kb())

async def support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    context.user_data.pop("draft_support", None)
    await update.callback_query.answer("Cancelled.")
    await go_home(update, context, delete_current=True)

async def ticket_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE, tid: int) -> None:
    uid = update.effective_user.id
    # Only seller owner for their shop OR super admin for main shop
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT shop_owner_id, user_id, status FROM tickets WHERE id=?", (tid,))
    t = cur.fetchone(); conn.close()
    if not t:
        await update.callback_query.answer("Not found.", show_alert=True); return
    owner = int(t["shop_owner_id"])
    if owner == SUPER_ADMIN_ID:
        if not is_super(uid):
            await update.callback_query.answer("Not allowed.", show_alert=True); return
    else:
        if uid != owner and not is_super(uid):
            await update.callback_query.answer("Not allowed.", show_alert=True); return

    set_mode(context, "ticket_reply", {"tid": tid, "to": int(t["user_id"]), "owner": owner})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id,
                                   f"✉️ Reply to ticket <code>{tid}</code>\nSend your reply text now.",
                                   parse_mode=ParseMode.HTML,
                                   reply_markup=back_home_kb())

async def ticket_reply_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    md = context.user_data.get("mode_data") or {}
    tid = int(md.get("tid") or 0)
    to_id = int(md.get("to") or 0)
    owner = int(md.get("owner") or 0)
    txt = (update.message.text or "").strip()
    if not tid or not to_id or not txt:
        await update.message.reply_text("Cancelled.", reply_markup=back_home_kb())
        clear_mode(context); return

    add_ticket_msg(tid, update.effective_user.id, txt)
    try:
        await context.bot.send_message(to_id, f"✉️ <b>Support Reply</b>\n\n{esc(txt)}",
                                       parse_mode=ParseMode.HTML)
    except Exception:
        pass

    await update.message.reply_text("✅ Replied.", reply_markup=back_home_kb())
    clear_mode(context)

async def ticket_close(update: Update, context: ContextTypes.DEFAULT_TYPE, tid: int) -> None:
    uid = update.effective_user.id
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT shop_owner_id, user_id FROM tickets WHERE id=?", (tid,))
    t = cur.fetchone()
    if not t:
        conn.close()
        await update.callback_query.answer("Not found.", show_alert=True); return
    owner = int(t["shop_owner_id"])
    if owner == SUPER_ADMIN_ID:
        if not is_super(uid):
            conn.close()
            await update.callback_query.answer("Not allowed.", show_alert=True); return
    else:
        if uid != owner and not is_super(uid):
            conn.close()
            await update.callback_query.answer("Not allowed.", show_alert=True); return
    cur.execute("UPDATE tickets SET status='closed', updated_at=? WHERE id=?", (ts(), tid))
    conn.commit(); conn.close()
    await update.callback_query.answer("Closed.")
    await delete_cb_message(update, context)

# -------------------- Become Seller --------------------
async def become_seller_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if is_super(uid):
        await update.callback_query.answer("You are Super Admin.", show_alert=True); return
    shop_owner_id, locked = get_session(uid)
    if locked == 1:
        await update.callback_query.answer("Not available in seller shop.", show_alert=True); return
    s = get_shop_settings(SUPER_ADMIN_ID)
    desc = (s["seller_desc"] or "").strip()
    txt = desc if desc else f"Price: {money(SELLER_SUB_PRICE)} {CURRENCY} / {SELLER_SUB_DAYS} days"
    rows = [
        [InlineKeyboardButton(f"✅ Buy ({money(SELLER_SUB_PRICE)} {esc(CURRENCY)} / {SELLER_SUB_DAYS}d)", callback_data="seller:buy")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
         InlineKeyboardButton("⬅️ Back", callback_data="nav:back")],
    ]
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, txt, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

async def seller_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if is_super(uid):
        await update.callback_query.answer("Not needed.", show_alert=True); return
    # pay from MAIN shop balance
    bal = get_balance(SUPER_ADMIN_ID, uid)
    if bal < SELLER_SUB_PRICE:
        await update.callback_query.answer("Not enough balance in Main Shop.", show_alert=True); return
    set_balance(SUPER_ADMIN_ID, uid, bal - SELLER_SUB_PRICE)
    log_tx(SUPER_ADMIN_ID, uid, "seller_sub", -SELLER_SUB_PRICE, "Become Seller", 1)
    ensure_seller(uid)
    add_seller_days(uid, SELLER_SUB_DAYS)
    ensure_shop_settings(uid)
    await update.callback_query.answer("Success.")
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id,
                                   f"✅ You are now a Seller.\nDays left: <b>{seller_days_left(uid)}</b>",
                                   parse_mode=ParseMode.HTML,
                                   reply_markup=two_cols([
                                       InlineKeyboardButton("🔗 Share My Shop", callback_data="m:share"),
                                       InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
                                   ]))

# -------------------- Seller subscription menu --------------------
async def sub_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not seller_panel_allowed(uid):
        await update.callback_query.answer("Not a seller.", show_alert=True); return
    days = seller_days_left(uid)
    txt = (f"📅 <b>Subscription</b>\n\n"
           f"Days left: <b>{days}</b>\n\n"
           f"Extend: <b>{money(SELLER_SUB_PRICE)} {esc(CURRENCY)}</b> → +<b>{SELLER_SUB_DAYS}</b> days\n"
           f"Payment is deducted from your <b>Main Shop</b> balance.")
    rows = [
        [InlineKeyboardButton("➕ Extend Subscription", callback_data="sub:extend")],
        [InlineKeyboardButton("🏬 Main Shop", callback_data="m:mainshop"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
    ]
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, txt, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

async def sub_extend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not seller_panel_allowed(uid):
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    ok = seller_extend_from_main_shop(uid)
    if not ok:
        await update.callback_query.answer("Not enough balance in Main Shop.", show_alert=True); return
    await update.callback_query.answer("Extended.")
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id,
                                   f"✅ Subscription extended.\nDays left: <b>{seller_days_left(uid)}</b>",
                                   parse_mode=ParseMode.HTML,
                                   reply_markup=back_home_kb())

# -------------------- Switch to Main shop (seller only) --------------------
async def to_main_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not seller_panel_allowed(uid) and not is_super(uid):
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    # seller users (locked) cannot reach here
    set_session(uid, SUPER_ADMIN_ID, 0)
    ensure_balance(SUPER_ADMIN_ID, uid)
    await update.callback_query.answer()
    await go_home(update, context, delete_current=True)

# -------------------- Share shop link (seller only) --------------------
async def share_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not seller_panel_allowed(uid):
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start=s_{uid}"
    txt = f"🔗 <b>Your Shop Link</b>\n\n{esc(link)}"
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, txt, parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True,
                                   reply_markup=back_home_kb())

# -------------------- Admin Panel (Seller & Super) --------------------
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    if locked == 1:
        await update.callback_query.answer("Not available.", show_alert=True); return
    if not (seller_panel_allowed(uid) or is_super(uid)):
        await update.callback_query.answer("Not allowed.", show_alert=True); return

    # For super: admin panel works on current session shop (main shop if super is in main)
    # For seller: admin panel works on their own shop (session should be uid if seller owner)
    target_shop = shop_owner_id
    if not is_super(uid):
        target_shop = uid

    push_nav(context, "admincats")
    rows = [
        [InlineKeyboardButton("📂 Manage Categories", callback_data="a:cats")],
        [InlineKeyboardButton("👥 Users", callback_data="a:users")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
         InlineKeyboardButton("⬅️ Back", callback_data="nav:back")],
    ]
    if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID:
        rows.insert(1, [InlineKeyboardButton("🧑‍💼 Sellers", callback_data="a:sellers")])
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id,
                                   f"🛠 <b>Admin Panel</b>\nShop: <b>{esc(shop_name(target_shop))}</b>",
                                   parse_mode=ParseMode.HTML, reply_markup=kb(rows))

# ---- Admin: Categories
async def admin_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id, name FROM categories WHERE shop_owner_id=? ORDER BY id DESC", (target,))
    cats = cur.fetchall(); conn.close()

    btns = [InlineKeyboardButton(f"📂 {c['name']}", callback_data=f"a:cat:{c['id']}") for c in cats]
    btns.insert(0, InlineKeyboardButton("➕ Add Category", callback_data="a:addcat"))
    btns += [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]
    push_nav(context, "admincats")
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "📂 <b>Categories</b>", parse_mode=ParseMode.HTML,
                                   reply_markup=two_cols(btns))

async def admin_add_cat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, "add_category_name", {})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id,
                                   "➕ <b>Add Category</b>\nSend category name.",
                                   parse_mode=ParseMode.HTML, reply_markup=back_home_kb())

async def admin_add_cat_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Send a name.", reply_markup=back_home_kb()); return
    set_mode(context, "add_category_desc", {"name": name})
    await update.message.reply_text("Send description (or '-' for none).", reply_markup=back_home_kb())

async def admin_add_cat_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    desc = (update.message.text or "").strip()
    if desc == "-":
        desc = ""
    md = context.user_data.get("mode_data") or {}
    name = md.get("name") or ""
    set_mode(context, "add_category_media", {"name": name, "desc": desc})
    await update.message.reply_text("Send photo/video for category, or type '-' to skip.", reply_markup=back_home_kb())

async def admin_add_cat_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    md = context.user_data.get("mode_data") or {}
    name = md.get("name") or ""
    desc = md.get("desc") or ""

    file_id = ""
    ftype = ""
    if update.message.text and update.message.text.strip() == "-":
        pass
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        ftype = "photo"
    elif update.message.video:
        file_id = update.message.video.file_id
        ftype = "video"
    else:
        await update.message.reply_text("Send photo/video or '-' to skip.", reply_markup=back_home_kb()); return

    conn = db(); cur = conn.cursor()
    cur.execute("""INSERT INTO categories(shop_owner_id, name, description, file_id, file_type)
                   VALUES(?,?,?,?,?)""", (target, name, desc, file_id, ftype))
    conn.commit(); conn.close()
    clear_mode(context)
    await update.message.reply_text("✅ Category created.", reply_markup=back_home_kb())

async def admin_cocats(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    cat = get_category(target, cat_id)
    if not cat:
        await update.callback_query.answer("Not found.", show_alert=True); return

    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id, name FROM cocategories WHERE shop_owner_id=? AND category_id=? ORDER BY id DESC",
                (target, cat_id))
    cocats = cur.fetchall(); conn.close()

    btns = [InlineKeyboardButton(f"🗂 {c['name']}", callback_data=f"a:cocat:{c['id']}") for c in cocats]
    btns.insert(0, InlineKeyboardButton("➕ Add Co-Category", callback_data=f"a:addcocat:{cat_id}"))
    btns += [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]
    push_nav(context, f"admincat:{cat_id}")
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, f"🗂 <b>{esc(cat['name'])}</b>", parse_mode=ParseMode.HTML,
                                   reply_markup=two_cols(btns))

async def admin_add_cocat(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int) -> None:
    set_mode(context, "add_cocat_name", {"cat_id": cat_id})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "➕ <b>Add Co-Category</b>\nSend name.",
                                   parse_mode=ParseMode.HTML, reply_markup=back_home_kb())

async def admin_add_cocat_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Send a name.", reply_markup=back_home_kb()); return
    md = context.user_data.get("mode_data") or {}
    set_mode(context, "add_cocat_desc", {"cat_id": int(md.get("cat_id")), "name": name})
    await update.message.reply_text("Send description (or '-' for none).", reply_markup=back_home_kb())

async def admin_add_cocat_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    desc = (update.message.text or "").strip()
    if desc == "-":
        desc = ""
    md = context.user_data.get("mode_data") or {}
    set_mode(context, "add_cocat_media", {"cat_id": int(md.get("cat_id")), "name": md.get("name"), "desc": desc})
    await update.message.reply_text("Send photo/video for co-category, or '-' to skip.", reply_markup=back_home_kb())

async def admin_add_cocat_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    md = context.user_data.get("mode_data") or {}
    cat_id = int(md.get("cat_id") or 0)
    name = (md.get("name") or "").strip()
    desc = (md.get("desc") or "").strip()

    file_id = ""
    ftype = ""
    if update.message.text and update.message.text.strip() == "-":
        pass
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        ftype = "photo"
    elif update.message.video:
        file_id = update.message.video.file_id
        ftype = "video"
    else:
        await update.message.reply_text("Send photo/video or '-' to skip.", reply_markup=back_home_kb()); return

    conn = db(); cur = conn.cursor()
    cur.execute("""INSERT INTO cocategories(shop_owner_id, category_id, name, description, file_id, file_type)
                   VALUES(?,?,?,?,?,?)""", (target, cat_id, name, desc, file_id, ftype))
    conn.commit(); conn.close()
    clear_mode(context)
    await update.message.reply_text("✅ Co-category created.", reply_markup=back_home_kb())

# ---- Admin: Products in co-category
async def admin_products(update: Update, context: ContextTypes.DEFAULT_TYPE, cocat_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    cc = get_cocat(target, cocat_id)
    if not cc:
        await update.callback_query.answer("Not found.", show_alert=True); return

    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id, name FROM products WHERE shop_owner_id=? AND cocategory_id=? ORDER BY id DESC",
                (target, cocat_id))
    prods = cur.fetchall(); conn.close()

    btns = [InlineKeyboardButton(f"🛍 {p['name']}", callback_data=f"a:prod:{p['id']}") for p in prods]
    btns.insert(0, InlineKeyboardButton("➕ Add Product", callback_data=f"a:addprod:{cocat_id}"))
    btns += [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]
    push_nav(context, f"admincocat:{cocat_id}")
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, f"🧾 <b>{esc(cc['name'])}</b>", parse_mode=ParseMode.HTML,
                                   reply_markup=two_cols(btns))

async def admin_add_prod(update: Update, context: ContextTypes.DEFAULT_TYPE, cocat_id: int) -> None:
    set_mode(context, "add_prod_name", {"cocat_id": cocat_id})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "➕ <b>Add Product</b>\nSend product name.",
                                   parse_mode=ParseMode.HTML, reply_markup=back_home_kb())

async def admin_add_prod_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Send a name.", reply_markup=back_home_kb()); return
    md = context.user_data.get("mode_data") or {}
    set_mode(context, "add_prod_price", {"cocat_id": int(md.get("cocat_id")), "name": name})
    await update.message.reply_text("Send price (numbers only).", reply_markup=back_home_kb())

async def admin_add_prod_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        price = float((update.message.text or "").strip())
        if price < 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("Send a valid price.", reply_markup=back_home_kb()); return
    md = context.user_data.get("mode_data") or {}
    set_mode(context, "add_prod_desc", {"cocat_id": int(md.get("cocat_id")), "name": md.get("name"), "price": price})
    await update.message.reply_text("Send description (or '-' for none).", reply_markup=back_home_kb())

async def admin_add_prod_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    desc = (update.message.text or "").strip()
    if desc == "-":
        desc = ""
    md = context.user_data.get("mode_data") or {}
    set_mode(context, "add_prod_media", {"cocat_id": int(md.get("cocat_id")), "name": md.get("name"), "price": float(md.get("price")), "desc": desc})
    await update.message.reply_text("Send product photo/video, or '-' to skip.", reply_markup=back_home_kb())

async def admin_add_prod_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    md = context.user_data.get("mode_data") or {}
    cocat_id = int(md.get("cocat_id") or 0)
    name = (md.get("name") or "").strip()
    price = float(md.get("price") or 0)
    desc = (md.get("desc") or "").strip()

    file_id = ""
    ftype = ""
    if update.message.text and update.message.text.strip() == "-":
        pass
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        ftype = "photo"
    elif update.message.video:
        file_id = update.message.video.file_id
        ftype = "video"
    else:
        await update.message.reply_text("Send photo/video or '-' to skip.", reply_markup=back_home_kb()); return

    # derive category_id from cocat
    cc = get_cocat(target, cocat_id)
    if not cc:
        await update.message.reply_text("Co-category not found.", reply_markup=back_home_kb()); return
    category_id = int(cc["category_id"])

    conn = db(); cur = conn.cursor()
    cur.execute("""INSERT INTO products(shop_owner_id, category_id, cocategory_id, name, price, description, file_id, file_type, tg_link)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (target, category_id, cocat_id, name, price, desc, file_id, ftype, ""))
    pid = cur.lastrowid
    conn.commit(); conn.close()
    clear_mode(context)
    await update.message.reply_text(f"✅ Product created. (ID {pid})", reply_markup=back_home_kb())

# ---- Admin: product edit screen
async def admin_product_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    if locked == 1:
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid
    prod = get_product(target, product_id)
    if not prod:
        await update.callback_query.answer("Not found.", show_alert=True); return
    stock = count_stock(target, product_id)
    push_nav(context, f"adminprod:{product_id}")
    txt = (f"🛠 <b>Edit Product</b>\n\n"
           f"Name: <b>{esc(prod['name'])}</b>\n"
           f"Price: <b>{money(prod['price'])} {esc(CURRENCY)}</b>\n"
           f"Stock (keys): <b>{stock}</b>\n"
           f"Private Link: <b>{'SET' if (prod['tg_link'] or '').strip() else 'NOT SET'}</b>")
    rows = [
        [InlineKeyboardButton("✏️ Edit Name", callback_data=f"pe:name:{product_id}"),
         InlineKeyboardButton("💲 Edit Price", callback_data=f"pe:price:{product_id}")],
        [InlineKeyboardButton("📝 Edit Description", callback_data=f"pe:desc:{product_id}"),
         InlineKeyboardButton("🖼 Edit Media", callback_data=f"pe:media:{product_id}")],
        [InlineKeyboardButton("🔑 Add Keys", callback_data=f"pe:addkeys:{product_id}"),
         InlineKeyboardButton("📁 Set Private Link", callback_data=f"pe:link:{product_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
    ]
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, txt, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

async def pe_set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, pid: int) -> None:
    set_mode(context, mode, {"pid": pid})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    prompts = {
        "pe_name": "Send new product name.",
        "pe_price": "Send new price (numbers only).",
        "pe_desc": "Send new description (or '-' to clear).",
        "pe_link": "Send new private link (or '-' to clear).",
        "pe_addkeys": "Send keys (one key per line). Each line = 1 stock.",
        "pe_media": "Send new photo/video for product, or '-' to clear.",
    }
    await context.bot.send_message(update.effective_chat.id, prompts.get(mode, "Send value."),
                                   reply_markup=back_home_kb())

async def pe_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    md = context.user_data.get("mode_data") or {}
    pid = int(md.get("pid") or 0)
    mode = context.user_data.get("mode") or ""
    if not pid or not mode:
        return
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    if mode == "pe_name":
        val = (update.message.text or "").strip()
        if not val:
            await update.message.reply_text("Send a name.", reply_markup=back_home_kb()); return
        conn = db(); cur = conn.cursor()
        cur.execute("UPDATE products SET name=? WHERE shop_owner_id=? AND id=?", (val, target, pid))
        conn.commit(); conn.close()
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=back_home_kb())
        return

    if mode == "pe_price":
        try:
            val = float((update.message.text or "").strip())
            if val < 0: raise ValueError()
        except Exception:
            await update.message.reply_text("Send a valid price.", reply_markup=back_home_kb()); return
        conn = db(); cur = conn.cursor()
        cur.execute("UPDATE products SET price=? WHERE shop_owner_id=? AND id=?", (val, target, pid))
        conn.commit(); conn.close()
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=back_home_kb())
        return

    if mode == "pe_desc":
        val = (update.message.text or "").strip()
        if val == "-":
            val = ""
        conn = db(); cur = conn.cursor()
        cur.execute("UPDATE products SET description=? WHERE shop_owner_id=? AND id=?", (val, target, pid))
        conn.commit(); conn.close()
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=back_home_kb())
        return

    if mode == "pe_link":
        val = (update.message.text or "").strip()
        if val == "-":
            val = ""
        conn = db(); cur = conn.cursor()
        cur.execute("UPDATE products SET tg_link=? WHERE shop_owner_id=? AND id=?", (val, target, pid))
        conn.commit(); conn.close()
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=back_home_kb())
        return

    if mode == "pe_addkeys":
        text = (update.message.text or "").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            await update.message.reply_text("Send at least 1 key line.", reply_markup=back_home_kb()); return
        conn = db(); cur = conn.cursor()
        cur.executemany("""INSERT INTO product_keys(shop_owner_id, product_id, key_line, delivered_once)
                           VALUES(?,?,?,0)""", [(target, pid, ln) for ln in lines])
        conn.commit(); conn.close()
        clear_mode(context)
        await update.message.reply_text(f"✅ Added {len(lines)} keys.", reply_markup=back_home_kb())
        return

async def pe_media_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    md = context.user_data.get("mode_data") or {}
    pid = int(md.get("pid") or 0)
    if not pid:
        return
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    file_id = ""
    ftype = ""
    if update.message.text and update.message.text.strip() == "-":
        pass
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        ftype = "photo"
    elif update.message.video:
        file_id = update.message.video.file_id
        ftype = "video"
    else:
        await update.message.reply_text("Send photo/video or '-' to clear.", reply_markup=back_home_kb()); return

    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE products SET file_id=?, file_type=? WHERE shop_owner_id=? AND id=?",
                (file_id, ftype, target, pid))
    conn.commit(); conn.close()
    clear_mode(context)
    await update.message.reply_text("✅ Updated media.", reply_markup=back_home_kb())

# -------------------- Admin: Users listing/search + balance edit + ban + reply support --------------------
def list_users_in_shop(shop_owner_id: int, limit: int = 50) -> List[sqlite3.Row]:
    conn = db(); cur = conn.cursor()
    cur.execute("""SELECT u.user_id, u.username, u.first_name, u.last_name
                   FROM users u
                   JOIN balances b ON b.user_id=u.user_id AND b.shop_owner_id=?
                   ORDER BY u.last_seen DESC LIMIT ?""", (shop_owner_id, limit))
    rows = cur.fetchall(); conn.close()
    return rows

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    if locked == 1:
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    if not (seller_panel_allowed(uid) or is_super(uid)):
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    rows = list_users_in_shop(target, 60)
    btns = []
    for r in rows:
        u = r["user_id"]
        name = r["username"] or ""
        label = f"@{name}" if name else f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or str(u)
        btns.append(InlineKeyboardButton(label, callback_data=f"au:view:{u}"))
    btns.insert(0, InlineKeyboardButton("🔎 Search Username", callback_data="au:search"))
    btns += [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "👥 <b>Users</b>\n(Click a username)",
                                   parse_mode=ParseMode.HTML, reply_markup=two_cols(btns))

async def au_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, "au_search", {})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "Send username to search (without @).",
                                   reply_markup=back_home_kb())

async def au_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = (update.message.text or "").strip().lstrip("@")
    if not q:
        await update.message.reply_text("Send username.", reply_markup=back_home_kb()); return
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    conn = db(); cur = conn.cursor()
    cur.execute("""SELECT user_id FROM users WHERE lower(username)=lower(?) LIMIT 1""", (q,))
    r = cur.fetchone(); conn.close()
    if not r:
        await update.message.reply_text("Not found.", reply_markup=back_home_kb()); return
    clear_mode(context)
    await admin_user_view_by_id(update, context, int(r["user_id"]), target)

async def admin_user_view_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, target_shop: int) -> None:
    bal = get_balance(target_shop, user_id)
    banned = is_banned(target_shop, user_id)
    txt = (f"👤 <b>User</b>\n\n"
           f"Username: <b>{esc(user_display(user_id))}</b>\n"
           f"Telegram ID: <code>{user_id}</code>\n"
           f"Balance: <b>{money(bal)} {esc(CURRENCY)}</b>\n"
           f"Banned: <b>{'YES' if banned else 'NO'}</b>")
    rows = [
        [InlineKeyboardButton("➕ Add Balance", callback_data=f"au:add:{user_id}"),
         InlineKeyboardButton("➖ Deduct Balance", callback_data=f"au:deduct:{user_id}")],
        [InlineKeyboardButton("🆘 View Support", callback_data=f"au:tickets:{user_id}")],
        [InlineKeyboardButton("🚫 Ban" if not banned else "✅ Unban", callback_data=f"au:ban:{user_id}:{0 if banned else 1}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
    ]
    await context.bot.send_message(update.effective_chat.id, txt, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

async def au_view(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await admin_user_view_by_id(update, context, user_id, target)

async def au_balance_start(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, add: bool) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    if locked == 1:
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid
    set_mode(context, "au_bal_amt", {"user_id": user_id, "add": 1 if add else 0, "shop": target})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id,
                                   "Send amount (numbers only).",
                                   reply_markup=back_home_kb())

async def au_bal_amt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    md = context.user_data.get("mode_data") or {}
    user_id = int(md.get("user_id") or 0)
    add = int(md.get("add") or 0) == 1
    target = int(md.get("shop") or 0)
    try:
        amt = float((update.message.text or "").strip())
        if amt <= 0: raise ValueError()
    except Exception:
        await update.message.reply_text("Send a valid amount.", reply_markup=back_home_kb()); return

    bal = get_balance(target, user_id)
    if add:
        set_balance(target, user_id, bal + amt)
        log_tx(target, user_id, "bal_add", amt, "Balance added", 1)
    else:
        set_balance(target, user_id, max(0.0, bal - amt))
        log_tx(target, user_id, "bal_deduct", -amt, "Balance deducted", 1)

    clear_mode(context)
    await update.message.reply_text("✅ Updated.", reply_markup=back_home_kb())

async def au_ban_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, banned: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    if locked == 1:
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid
    set_ban(target, user_id, banned)
    await update.callback_query.answer("Updated.")
    await delete_cb_message(update, context)

async def au_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    if locked == 1:
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    conn = db(); cur = conn.cursor()
    cur.execute("""SELECT id, status, updated_at FROM tickets
                   WHERE shop_owner_id=? AND user_id=?
                   ORDER BY updated_at DESC LIMIT 20""", (target, user_id))
    rows = cur.fetchall(); conn.close()

    btns = []
    for r in rows:
        tid = int(r["id"])
        st = r["status"]
        btns.append(InlineKeyboardButton(f"Ticket #{tid} ({st})", callback_data=f"t:view:{tid}"))
    btns += [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id,
                                   f"🆘 <b>Tickets for {esc(user_display(user_id))}</b>",
                                   parse_mode=ParseMode.HTML, reply_markup=two_cols(btns) if btns else back_home_kb())

async def ticket_view(update: Update, context: ContextTypes.DEFAULT_TYPE, tid: int) -> None:
    uid = update.effective_user.id
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT shop_owner_id, user_id, status FROM tickets WHERE id=?", (tid,))
    t = cur.fetchone()
    if not t:
        conn.close()
        await update.callback_query.answer("Not found.", show_alert=True); return
    owner = int(t["shop_owner_id"]); user_id = int(t["user_id"])
    # permission
    if owner == SUPER_ADMIN_ID:
        if not is_super(uid):
            conn.close()
            await update.callback_query.answer("Not allowed.", show_alert=True); return
    else:
        if uid != owner and not is_super(uid):
            conn.close()
            await update.callback_query.answer("Not allowed.", show_alert=True); return
    cur.execute("""SELECT sender_id, text, created_at FROM ticket_messages
                   WHERE ticket_id=? ORDER BY id ASC LIMIT 30""", (tid,))
    msgs = cur.fetchall(); conn.close()

    lines = [f"📩 <b>Ticket #{tid}</b> ({esc(t['status'])})",
             f"User: <b>{esc(user_display(user_id))}</b> (<code>{user_id}</code>)\n"]
    for m in msgs:
        sender = int(m["sender_id"])
        who = "User" if sender == user_id else "Admin"
        lines.append(f"<b>{who}:</b> {esc(m['text'])}")
    rows = [[InlineKeyboardButton("✉️ Reply", callback_data=f"t:reply:{tid}"),
             InlineKeyboardButton("✅ Close", callback_data=f"t:close:{tid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]]
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "\n".join(lines), parse_mode=ParseMode.HTML,
                                   reply_markup=kb(rows))

# -------------------- Super Admin: Sellers management --------------------
async def super_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not is_super(uid):
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    rows = [
        [InlineKeyboardButton("🧑‍💼 Sellers", callback_data="sa:sellers")],
        [InlineKeyboardButton("✏️ Edit Main Welcome", callback_data="sa:welctxt")],
        [InlineKeyboardButton("💳 Edit Main Wallet Message", callback_data="sa:walletmsg")],
        [InlineKeyboardButton("⭐ Edit Become Seller Text", callback_data="sa:sellerdesc")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
         InlineKeyboardButton("⬅️ Back", callback_data="nav:back")],
    ]
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "👑 <b>Super Admin</b>", parse_mode=ParseMode.HTML,
                                   reply_markup=kb(rows))

async def sa_edit_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, prompt: str) -> None:
    set_mode(context, mode, {})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, prompt, parse_mode=ParseMode.HTML,
                                   reply_markup=back_home_kb())

async def sa_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_super(update.effective_user.id):
        return
    mode = context.user_data.get("mode") or ""
    txt = update.message.text or ""
    if mode == "sa_welctxt":
        set_shop_setting(SUPER_ADMIN_ID, "welcome_text", txt)
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=back_home_kb())
    elif mode == "sa_walletmsg":
        set_shop_setting(SUPER_ADMIN_ID, "wallet_message", txt)
        # keep TRC-20 method in sync
        ensure_payment_methods(SUPER_ADMIN_ID)
        rows = list_payment_methods(SUPER_ADMIN_ID)
        if rows:
            # update first method (TRC-20)
            update_payment_method_text(SUPER_ADMIN_ID, int(rows[0]["id"]), txt)
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=back_home_kb())
    elif mode == "sa_sellerdesc":
        set_shop_setting(SUPER_ADMIN_ID, "seller_desc", txt)
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=back_home_kb())

async def sa_sellers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_super(update.effective_user.id):
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    conn = db(); cur = conn.cursor()
    cur.execute("""SELECT s.seller_id, s.sub_until, s.banned_shop, s.banned_panel, s.restricted_until, u.username
                   FROM sellers s LEFT JOIN users u ON u.user_id=s.seller_id
                   ORDER BY s.sub_until DESC LIMIT 80""")
    rows = cur.fetchall(); conn.close()
    btns = [InlineKeyboardButton(user_display(r["seller_id"]), callback_data=f"sa:sv:{r['seller_id']}") for r in rows]
    btns.insert(0, InlineKeyboardButton("🔎 Search Seller", callback_data="sa:ssearch"))
    btns += [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
             InlineKeyboardButton("⬅️ Back", callback_data="nav:back")]
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "🧑‍💼 <b>Sellers</b>", parse_mode=ParseMode.HTML,
                                   reply_markup=two_cols(btns))

async def sa_ssearch_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, "sa_ssearch", {})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "Send seller username to search (without @).",
                                   reply_markup=back_home_kb())

async def sa_ssearch_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = (update.message.text or "").strip().lstrip("@")
    if not q:
        await update.message.reply_text("Send username.", reply_markup=back_home_kb()); return
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE lower(username)=lower(?) LIMIT 1", (q,))
    r = cur.fetchone(); conn.close()
    if not r:
        await update.message.reply_text("Not found.", reply_markup=back_home_kb()); return
    clear_mode(context)
    await sa_seller_view(update, context, int(r["user_id"]))

async def sa_seller_view(update: Update, context: ContextTypes.DEFAULT_TYPE, seller_id: int) -> None:
    ensure_seller(seller_id)
    r = seller_row(seller_id)
    days = seller_days_left(seller_id)
    txt = (f"🧑‍💼 <b>Seller</b>\n\n"
           f"Username: <b>{esc(user_display(seller_id))}</b>\n"
           f"Telegram ID: <code>{seller_id}</code>\n"
           f"Days left: <b>{days}</b>\n"
           f"Banned Shop: <b>{'YES' if int(r['banned_shop'] or 0)==1 else 'NO'}</b>\n"
           f"Banned Panel: <b>{'YES' if int(r['banned_panel'] or 0)==1 else 'NO'}</b>\n")
    rows = [
        [InlineKeyboardButton("🚫 Ban Shop" if int(r["banned_shop"] or 0)==0 else "✅ Unban Shop",
                              callback_data=f"sa:ban_shop:{seller_id}:{1 if int(r['banned_shop'] or 0)==0 else 0}")],
        [InlineKeyboardButton("🚫 Ban Panel" if int(r["banned_panel"] or 0)==0 else "✅ Unban Panel",
                              callback_data=f"sa:ban_panel:{seller_id}:{1 if int(r['banned_panel'] or 0)==0 else 0}")],
        [InlineKeyboardButton("⏳ Restrict 7d", callback_data=f"sa:restrict:{seller_id}:7"),
         InlineKeyboardButton("⏳ Restrict 30d", callback_data=f"sa:restrict:{seller_id}:30")],
        [InlineKeyboardButton("⏳ Restrict 90d", callback_data=f"sa:restrict:{seller_id}:90"),
         InlineKeyboardButton("✅ Remove Restrict", callback_data=f"sa:restrict:{seller_id}:0")],
        [InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
    ]
    await context.bot.send_message(update.effective_chat.id, txt, parse_mode=ParseMode.HTML, reply_markup=kb(rows))

async def sa_seller_view_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, seller_id: int) -> None:
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await sa_seller_view(update, context, seller_id)

def sa_update_seller(seller_id: int, field: str, val: int) -> None:
    if field not in {"banned_shop", "banned_panel", "restricted_until"}:
        raise ValueError()
    ensure_seller(seller_id)
    conn = db(); cur = conn.cursor()
    cur.execute(f"UPDATE sellers SET {field}=? WHERE seller_id=?", (val, seller_id))
    conn.commit(); conn.close()

async def sa_ban_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, seller_id: int, field: str, val: int) -> None:
    if not is_super(update.effective_user.id):
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    sa_update_seller(seller_id, field, int(val))
    await update.callback_query.answer("Updated.")
    await delete_cb_message(update, context)

async def sa_restrict(update: Update, context: ContextTypes.DEFAULT_TYPE, seller_id: int, days: int) -> None:
    if not is_super(update.effective_user.id):
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    if days <= 0:
        sa_update_seller(seller_id, "restricted_until", 0)
    else:
        sa_update_seller(seller_id, "restricted_until", ts() + days * 86400)
    await update.callback_query.answer("Updated.")
    await delete_cb_message(update, context)

# -------------------- Seller settings edit (wallet/welcome) --------------------
async def seller_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not seller_panel_allowed(uid) and not is_super(uid):
        await update.callback_query.answer("Not allowed.", show_alert=True); return
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid
    rows = [
        [InlineKeyboardButton("✏️ Edit Welcome Text", callback_data="set:welctxt")],
        [InlineKeyboardButton("🖼 Set Welcome Photo/Video", callback_data="set:welcmedia")],
        [InlineKeyboardButton("💳 Edit Wallet Message", callback_data="set:walletmsg")],
        [InlineKeyboardButton("💳 Payment Methods", callback_data="pm:list")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
         InlineKeyboardButton("⬅️ Back", callback_data="nav:back")],
    ]
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "⚙️ <b>Shop Settings</b>", parse_mode=ParseMode.HTML, reply_markup=kb(rows))

async def set_welctxt_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, "set_welctxt", {})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "Send new welcome text (HTML allowed).", reply_markup=back_home_kb())

async def set_walletmsg_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, "set_walletmsg", {})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "Send new wallet message.", reply_markup=back_home_kb())

async def set_welcmedia_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, "set_welcmedia", {})
    await update.callback_query.answer()
    await delete_cb_message(update, context)
    await context.bot.send_message(update.effective_chat.id, "Send welcome PHOTO or VIDEO, or '-' to clear.", reply_markup=back_home_kb())

async def settings_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    mode = context.user_data.get("mode") or ""
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    if mode == "set_welctxt":
        set_shop_setting(target, "welcome_text", update.message.text or "")
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=back_home_kb())
    elif mode == "set_walletmsg":
        set_shop_setting(target, "wallet_message", update.message.text or "")
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=back_home_kb())

async def settings_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    mode = context.user_data.get("mode") or ""
    if mode != "set_welcmedia":
        return
    shop_owner_id, _ = get_session(uid)
    target = SUPER_ADMIN_ID if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID else uid

    file_id = ""
    ftype = ""
    if update.message.text and update.message.text.strip() == "-":
        pass
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        ftype = "photo"
    elif update.message.video:
        file_id = update.message.video.file_id
        ftype = "video"
    else:
        await update.message.reply_text("Send photo/video or '-' to clear.", reply_markup=back_home_kb())
        return

    set_shop_setting(target, "welcome_file_id", file_id)
    set_shop_setting(target, "welcome_file_type", ftype)
    clear_mode(context)
    await update.message.reply_text("✅ Updated welcome media.", reply_markup=back_home_kb())

# -------------------- Callback router --------------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data or ""
    uid = update.effective_user.id
    upsert_user(update.effective_user)

    # navigation
    if data == "nav:home":
        await q.answer()
        await go_home(update, context, delete_current=True); return
    if data == "nav:back":
        await q.answer()
        await go_back(update, context); return

    # main menu actions
    if data == "m:products":
        await q.answer()
        await show_categories(update, context); return
    if data == "m:wallet":
        await q.answer()
        await wallet_view(update, context); return
    if data == "m:history":
        await q.answer()
        await history_view(update, context); return
    if data == "m:support":
        await support_view(update, context); return
    if data == "m:become_seller":
        await become_seller_view(update, context); return
    if data == "m:admin":
        await admin_menu(update, context); return
    if data == "m:super":
        await super_menu(update, context); return
    if data == "m:sub":
        await sub_view(update, context); return
    if data == "m:mainshop":
        await q.answer()
        await to_main_shop(update, context); return
    if data == "m:share":
        await q.answer()
        await share_shop(update, context); return

    # categories browse
    if data.startswith("cat:"):
        await q.answer()
        await show_cocats(update, context, int(data.split(":")[1])); return
    if data.startswith("cocat:"):
        await q.answer()
        await show_products(update, context, int(data.split(":")[1])); return
    if data.startswith("prod:"):
        await q.answer()
        await show_product(update, context, int(data.split(":")[1])); return

    # qty / buy
    if data.startswith("qty:"):
        _, pid, action = data.split(":")
        await change_qty(update, context, int(pid), -1 if action == "dec" else 1); return
    if data.startswith("buy:"):
        await q.answer()
        await buy_product(update, context, int(data.split(":")[1])); return
    if data.startswith("getfile:"):
        await get_file(update, context, int(data.split(":")[1])); return

    # deposits
    if data == "dep:start":
        await dep_start(update, context); return
    if data.startswith("dep:approve:"):
        await dep_handle(update, context, int(data.split(":")[2]), True); return
    if data.startswith("dep:reject:"):
        await dep_handle(update, context, int(data.split(":")[2]), False); return


    # payment methods (admin)
    if data == "pm:list":
        await pm_list(update, context); return
    if data == "pm:add":
        await pm_add_start(update, context); return
    if data.startswith("pm:edit:"):
        await pm_edit_start(update, context, int(data.split(":")[2])); return
    if data.startswith("pm:del:"):
        await pm_delete(update, context, int(data.split(":")[2])); return
    # support
    if data == "support:done":
        await support_done(update, context); return
    if data == "support:cancel":
        await support_cancel(update, context); return
    if data.startswith("t:reply:"):
        await ticket_reply_start(update, context, int(data.split(":")[2])); return
    if data.startswith("t:close:"):
        await ticket_close(update, context, int(data.split(":")[2])); return
    if data.startswith("t:view:"):
        await q.answer()
        await ticket_view(update, context, int(data.split(":")[2])); return

    # become seller
    if data == "seller:buy":
        await seller_buy(update, context); return

    # subscription extend
    if data == "sub:extend":
        await sub_extend(update, context); return

    # admin categories
    if data == "a:cats":
        await q.answer()
        await admin_categories(update, context); return
    if data == "a:addcat":
        await admin_add_cat(update, context); return
    if data.startswith("a:cat:"):
        await q.answer()
        await admin_cocats(update, context, int(data.split(":")[2])); return
    if data.startswith("a:addcocat:"):
        await admin_add_cocat(update, context, int(data.split(":")[2])); return
    if data.startswith("a:cocat:"):
        await q.answer()
        await admin_products(update, context, int(data.split(":")[2])); return
    if data.startswith("a:addprod:"):
        await admin_add_prod(update, context, int(data.split(":")[2])); return
    if data.startswith("a:prod:"):
        await q.answer()
        await admin_product_edit(update, context, int(data.split(":")[2])); return
    if data == "a:users":
        await q.answer()
        await admin_users(update, context); return
    if data == "au:search":
        await au_search_start(update, context); return
    if data.startswith("au:view:"):
        await au_view(update, context, int(data.split(":")[2])); return
    if data.startswith("au:add:"):
        await au_balance_start(update, context, int(data.split(":")[2]), True); return
    if data.startswith("au:deduct:"):
        await au_balance_start(update, context, int(data.split(":")[2]), False); return
    if data.startswith("au:ban:"):
        _, _, user_id, banv = data.split(":")
        await au_ban_toggle(update, context, int(user_id), int(banv)); return
    if data.startswith("au:tickets:"):
        await q.answer()
        await au_tickets(update, context, int(data.split(":")[2])); return

    # super admin seller list/search
    if data == "sa:sellers":
        await q.answer()
        await sa_sellers(update, context); return
    if data == "sa:ssearch":
        await sa_ssearch_start(update, context); return
    if data.startswith("sa:sv:"):
        await sa_seller_view_cb(update, context, int(data.split(":")[2])); return
    if data.startswith("sa:ban_shop:"):
        _, _, seller_id, val = data.split(":")
        await sa_ban_toggle(update, context, int(seller_id), "banned_shop", int(val)); return
    if data.startswith("sa:ban_panel:"):
        _, _, seller_id, val = data.split(":")
        await sa_ban_toggle(update, context, int(seller_id), "banned_panel", int(val)); return
    if data.startswith("sa:restrict:"):
        _, _, seller_id, days = data.split(":")
        await sa_restrict(update, context, int(seller_id), int(days)); return
    if data == "sa:welctxt":
        await sa_edit_prompt(update, context, "sa_welctxt", "Send new MAIN welcome text."); return
    if data == "sa:walletmsg":
        await sa_edit_prompt(update, context, "sa_walletmsg", "Send new MAIN wallet message."); return
    if data == "sa:sellerdesc":
        await sa_edit_prompt(update, context, "sa_sellerdesc", "Send new Become Seller description."); return

    # product edit actions
    if data.startswith("pe:name:"):
        await pe_set_mode(update, context, "pe_name", int(data.split(":")[2])); return
    if data.startswith("pe:price:"):
        await pe_set_mode(update, context, "pe_price", int(data.split(":")[2])); return
    if data.startswith("pe:desc:"):
        await pe_set_mode(update, context, "pe_desc", int(data.split(":")[2])); return
    if data.startswith("pe:link:"):
        await pe_set_mode(update, context, "pe_link", int(data.split(":")[2])); return
    if data.startswith("pe:addkeys:"):
        await pe_set_mode(update, context, "pe_addkeys", int(data.split(":")[2])); return
    if data.startswith("pe:media:"):
        await pe_set_mode(update, context, "pe_media", int(data.split(":")[2])); return

    await q.answer()

# -------------------- Text / Media router --------------------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    upsert_user(update.effective_user)
    mode = context.user_data.get("mode") or ""

    # support draft
    if mode == "support_draft":
        await support_collect(update, context); return

    # deposit
    if mode == "dep_amount":
        await dep_amount_received(update, context); return
    if mode == "dep_proof":
        await dep_proof_received(update, context); return

    # add category flow
    if mode == "add_category_name":
        await admin_add_cat_name(update, context); return
    if mode == "add_category_desc":
        await admin_add_cat_desc(update, context); return
    if mode == "add_category_media":
        await admin_add_cat_media(update, context); return

    # add cocat flow
    if mode == "add_cocat_name":
        await admin_add_cocat_name(update, context); return
    if mode == "add_cocat_desc":
        await admin_add_cocat_desc(update, context); return
    if mode == "add_cocat_media":
        await admin_add_cocat_media(update, context); return

    # add product flow
    if mode == "add_prod_name":
        await admin_add_prod_name(update, context); return
    if mode == "add_prod_price":
        await admin_add_prod_price(update, context); return
    if mode == "add_prod_desc":
        await admin_add_prod_desc(update, context); return
    if mode == "add_prod_media":
        await admin_add_prod_media(update, context); return

    # product edit text modes
    if mode in {"pe_name", "pe_price", "pe_desc", "pe_link", "pe_addkeys"}:
        await pe_text_received(update, context); return

    # admin search user
    if mode == "au_search":
        await au_search_text(update, context); return

    # admin balance adjust
    if mode == "au_bal_amt":
        await au_bal_amt(update, context); return

    # super edit modes
    if mode in {"sa_welctxt", "sa_walletmsg", "sa_sellerdesc"}:
        await sa_edit_text(update, context); return

    # seller search
    if mode == "sa_ssearch":
        await sa_ssearch_text(update, context); return

    # settings edit
    if mode in {"set_welctxt", "set_walletmsg"}:
        await settings_text_handler(update, context); return

    # ticket reply
    if mode == "ticket_reply":
        await ticket_reply_text(update, context); return

    # fallback ignore
    return

async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    upsert_user(update.effective_user)
    mode = context.user_data.get("mode") or ""
    if mode == "add_category_media":
        await admin_add_cat_media(update, context); return
    if mode == "add_cocat_media":
        await admin_add_cocat_media(update, context); return
    if mode == "add_prod_media":
        await admin_add_prod_media(update, context); return
    if mode == "pe_media":
        await pe_media_received(update, context); return
    if mode == "set_welcmedia":
        await settings_media_handler(update, context); return
    if mode == "dep_proof":
        await dep_proof_received(update, context); return

# -------------------- Build app --------------------
def build_app() -> Application:
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, on_media))
    return app

def main() -> None:
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
