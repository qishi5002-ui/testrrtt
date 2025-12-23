# ============================================================
# AutoPanel Bot — PART 1 / 3 (FOUNDATION)
# Roles, DB, Helpers, Sessions, Seller Subscription Core
# Python 3.11+ | python-telegram-bot 20.x
# ============================================================

import os
import re
import time
import sqlite3
import logging
from typing import Optional, List, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -------------------------
# CONFIG (ENV)
# -------------------------
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

SUPER_ADMIN_ID = int((os.getenv("SUPER_ADMIN_ID") or os.getenv("ADMIN_ID") or "0").strip() or "0")
if SUPER_ADMIN_ID <= 0:
    raise RuntimeError("SUPER_ADMIN_ID missing")

STORE_NAME = (os.getenv("STORE_NAME") or "AutoPanel").strip()
CURRENCY = (os.getenv("CURRENCY") or "USDT").strip()

SELLER_SUB_PRICE = float(os.getenv("SELLER_SUB_PRICE", "10"))
SELLER_SUB_DAYS = int(os.getenv("SELLER_SUB_DAYS", "30"))

DB_FILE = os.getenv("DB_FILE", "data.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("autopanel")

# -------------------------
# UTILS
# -------------------------
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

# -------------------------
# DB
# -------------------------
def db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()

    # USERS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        last_name TEXT DEFAULT '',
        last_seen INTEGER DEFAULT 0
    )
    """)

    # SESSION: which shop user is in; locked=1 means seller-customer locked to seller shop
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
        user_id INTEGER PRIMARY KEY,
        shop_owner_id INTEGER NOT NULL,
        locked INTEGER DEFAULT 0
    )
    """)

    # SELLERS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sellers(
        seller_id INTEGER PRIMARY KEY,
        sub_until INTEGER DEFAULT 0,
        banned_shop INTEGER DEFAULT 0,
        restricted_until INTEGER DEFAULT 0,
        banned_panel INTEGER DEFAULT 0,
        balance REAL DEFAULT 0
    )
    """)

    # SHOP SETTINGS (per shop_owner_id)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shop_settings(
        shop_owner_id INTEGER PRIMARY KEY,
        wallet_address TEXT DEFAULT '',
        wallet_message TEXT DEFAULT '',
        welcome_text TEXT DEFAULT '',
        welcome_file_id TEXT DEFAULT '',
        welcome_file_type TEXT DEFAULT '',
        seller_desc TEXT DEFAULT ''
    )
    """)

    # BALANCES (per shop)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS balances(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        balance REAL DEFAULT 0,
        PRIMARY KEY (shop_owner_id, user_id)
    )
    """)

    # USER BANS (per shop)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_bans(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        banned INTEGER DEFAULT 1,
        PRIMARY KEY (shop_owner_id, user_id)
    )
    """)

    # CATALOG
    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        file_type TEXT DEFAULT ''
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

    # PRODUCT KEYS (1 line = 1 stock)
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

    # TRANSACTIONS (History)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL,         -- deposit/purchase/adjust/seller_sub
        amount REAL DEFAULT 0,
        note TEXT DEFAULT '',
        qty INTEGER DEFAULT 1,
        created_at INTEGER NOT NULL
    )
    """)

    # DEPOSITS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposit_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        proof_file_id TEXT NOT NULL,
        status TEXT NOT NULL,       -- pending/approved/rejected
        created_at INTEGER NOT NULL,
        handled_by INTEGER DEFAULT 0,
        handled_at INTEGER DEFAULT 0
    )
    """)

    # SUPPORT
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL,       -- open/closed
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

    conn.commit()

    # Ensure main shop settings + defaults
    ensure_shop_settings(SUPER_ADMIN_ID)

    cur.execute("SELECT welcome_text FROM shop_settings WHERE shop_owner_id=?", (SUPER_ADMIN_ID,))
    r = cur.fetchone()
    if r and not (r["welcome_text"] or "").strip():
        cur.execute(
            "UPDATE shop_settings SET welcome_text=? WHERE shop_owner_id=?",
            (
                f"✅ Welcome to <b>{esc(STORE_NAME)}</b>\n"
                f"Get your 24/7 Store Panel Here !!\n\n"
                f"Bot created by @RekkoOwn",
                SUPER_ADMIN_ID,
            ),
        )
        conn.commit()

    cur.execute("SELECT seller_desc FROM shop_settings WHERE shop_owner_id=?", (SUPER_ADMIN_ID,))
    r = cur.fetchone()
    if r and not (r["seller_desc"] or "").strip():
        cur.execute(
            "UPDATE shop_settings SET seller_desc=? WHERE shop_owner_id=?",
            (
                "⭐ <b>Become a Seller</b>\n\n"
                "• Your own shop\n"
                "• Your own products\n"
                "• Your own wallet & deposits\n"
                "• Your own support\n\n"
                f"Price: <b>{money(SELLER_SUB_PRICE)} {esc(CURRENCY)}</b> / "
                f"<b>{SELLER_SUB_DAYS} days</b>\n"
                "Renew early to stack days.",
                SUPER_ADMIN_ID,
            ),
        )
        conn.commit()

    conn.close()

def ensure_shop_settings(shop_owner_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO shop_settings(shop_owner_id, wallet_address, wallet_message, welcome_text, welcome_file_id, welcome_file_type, seller_desc) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                shop_owner_id,
                "",
                "",
                f"✅ Welcome to <b>{esc(STORE_NAME)}</b>\nGet your 24/7 Store Panel Here !!",
                "",
                "",
                "",
            ),
        )
        conn.commit()
    conn.close()

def get_shop_settings(shop_owner_id: int) -> sqlite3.Row:
    ensure_shop_settings(shop_owner_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    r = cur.fetchone()
    conn.close()
    return r

# -------------------------
# USERS / SESSION
# -------------------------
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

def set_session(uid: int, shop_owner_id: int, locked: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions(user_id, shop_owner_id, locked) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET shop_owner_id=excluded.shop_owner_id, locked=excluded.locked",
        (uid, shop_owner_id, locked),
    )
    conn.commit()
    conn.close()

def get_session(uid: int) -> Tuple[int, int]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT shop_owner_id, locked FROM sessions WHERE user_id=?", (uid,))
    r = cur.fetchone()
    conn.close()
    if not r:
        return SUPER_ADMIN_ID, 0
    return int(r["shop_owner_id"]), int(r["locked"] or 0)

# -------------------------
# SELLER CORE
# -------------------------
def seller_row(uid: int) -> Optional[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM sellers WHERE seller_id=?", (uid,))
    r = cur.fetchone()
    conn.close()
    return r

def ensure_seller(uid: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO sellers(seller_id, sub_until) VALUES(?,?)", (uid, 0))
    conn.commit()
    conn.close()

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

def seller_days_left(uid: int) -> int:
    if is_super(uid):
        return 10**9
    r = seller_row(uid)
    if not r:
        return 0
    return max(0, int(r["sub_until"] or 0) - ts()) // 86400

def seller_extend_from_main_shop(uid: int) -> bool:
    """
    Seller renews from SELLER PANEL, but payment is from MAIN SHOP balance.
    Deduct SELLER_SUB_PRICE from main shop balance, then add SELLER_SUB_DAYS.
    """
    ensure_seller(uid)
    # check balance at main shop
    bal = get_balance(SUPER_ADMIN_ID, uid)
    if bal < SELLER_SUB_PRICE:
        return False

    # deduct
    set_balance(SUPER_ADMIN_ID, uid, bal - SELLER_SUB_PRICE)
    log_tx(SUPER_ADMIN_ID, uid, "seller_sub", -SELLER_SUB_PRICE, "Subscription payment")

    # extend days
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT sub_until FROM sellers WHERE seller_id=?", (uid,))
    r = cur.fetchone()
    base = max(int(r["sub_until"] or 0), ts())
    cur.execute("UPDATE sellers SET sub_until=? WHERE seller_id=?", (base + SELLER_SUB_DAYS * 86400, uid))
    conn.commit()
    conn.close()
    return True

# -------------------------
# BALANCE / HISTORY
# -------------------------
def ensure_balance(shop_owner_id: int, uid: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,0)",
        (shop_owner_id, uid),
    )
    conn.commit()
    conn.close()

def get_balance(shop_owner_id: int, uid: int) -> float:
    ensure_balance(shop_owner_id, uid)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM balances WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    r = cur.fetchone()
    conn.close()
    return float(r["balance"]) if r else 0.0

def set_balance(shop_owner_id: int, uid: int, val: float) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,?) "
        "ON CONFLICT(shop_owner_id, user_id) DO UPDATE SET balance=excluded.balance",
        (shop_owner_id, uid, max(0.0, float(val))),
    )
    conn.commit()
    conn.close()

def log_tx(shop_owner_id: int, uid: int, kind: str, amount: float, note: str = "", qty: int = 1) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions(shop_owner_id, user_id, kind, amount, note, qty, created_at) VALUES(?,?,?,?,?,?,?)",
        (shop_owner_id, uid, kind, float(amount), note or "", int(qty or 1), ts()),
    )
    conn.commit()
    conn.close()

# -------------------------
# START ARG (seller link)
# -------------------------
def parse_start_arg(arg: str) -> Optional[int]:
    m = re.match(r"^s_(\d+)$", (arg or "").strip())
    return int(m.group(1)) if m else None

# -------------------------
# SAFE DELETE
# -------------------------
async def safe_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int) -> None:
    try:
        await context.application.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except BadRequest:
        pass
    except Exception:
        pass

# ============================================================
# AutoPanel Bot — PART 2 / 3
# Menus, /start, Browsing, Quantity +/- Buy, Keys Stock,
# Admin Panel (Catalog + Product Edit + Keys + Private Link),
# Share Shop, Seller Subscription (redirect to main shop pay)
# ============================================================

# -------------------------
# Extra helpers (Part 2)
# -------------------------
def kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)

def two_cols(buttons: List[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i+2])
    return InlineKeyboardMarkup(rows)

def one_col(buttons: List[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[b] for b in buttons])

def shop_name(shop_owner_id: int) -> str:
    if shop_owner_id == SUPER_ADMIN_ID:
        return f"{STORE_NAME} (Main Shop)"
    return f"{user_display(shop_owner_id)} Shop"

def user_display(uid: int) -> str:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT username, first_name, last_name FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    conn.close()
    if not r:
        return str(uid)
    un = (r["username"] or "").strip()
    if un:
        return f"@{un}"
    name = " ".join([x for x in [(r["first_name"] or "").strip(), (r["last_name"] or "").strip()] if x]).strip()
    return name or str(uid)

def is_banned(shop_owner_id: int, uid: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT banned FROM user_bans WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    r = cur.fetchone()
    conn.close()
    return bool(r and int(r["banned"] or 0) == 1)

def set_ban(shop_owner_id: int, uid: int, banned: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_bans(shop_owner_id, user_id, banned) VALUES(?,?,?) "
        "ON CONFLICT(shop_owner_id, user_id) DO UPDATE SET banned=excluded.banned",
        (shop_owner_id, uid, int(banned)),
    )
    conn.commit()
    conn.close()

def count_stock(shop_owner_id: int, product_id: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(1) AS c FROM product_keys WHERE shop_owner_id=? AND product_id=? AND delivered_once=0",
        (shop_owner_id, product_id),
    )
    r = cur.fetchone()
    conn.close()
    return int(r["c"] or 0) if r else 0

def get_product(shop_owner_id: int, product_id: int) -> Optional[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM products WHERE shop_owner_id=? AND id=?",
        (shop_owner_id, product_id),
    )
    r = cur.fetchone()
    conn.close()
    return r

def get_category(shop_owner_id: int, cat_id: int) -> Optional[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE shop_owner_id=? AND id=?", (shop_owner_id, cat_id))
    r = cur.fetchone()
    conn.close()
    return r

def get_cocat(shop_owner_id: int, cocat_id: int) -> Optional[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cocategories WHERE shop_owner_id=? AND id=?", (shop_owner_id, cocat_id))
    r = cur.fetchone()
    conn.close()
    return r

async def notify_purchase(context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int, buyer_id: int, prod: sqlite3.Row, qty: int, total: float) -> None:
    msg = (
        "🛒 <b>New Purchase</b>\n\n"
        f"Shop: <b>{esc(shop_name(shop_owner_id))}</b>\n"
        f"Buyer: <b>{esc(user_display(buyer_id))}</b>\n"
        f"Product: <b>{esc(prod['name'])}</b>\n"
        f"Qty: <b>{qty}</b>\n"
        f"Paid: <b>{money(total)} {esc(CURRENCY)}</b>"
    )
    # Notify seller (if seller shop)
    if shop_owner_id != SUPER_ADMIN_ID:
        try:
            await context.bot.send_message(chat_id=shop_owner_id, text=msg, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        # Also notify super admin
        try:
            await context.bot.send_message(chat_id=SUPER_ADMIN_ID, text=msg, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    else:
        # main shop -> super admin only
        try:
            await context.bot.send_message(chat_id=SUPER_ADMIN_ID, text=msg, parse_mode=ParseMode.HTML)
        except Exception:
            pass

# -------------------------
# Keyboards (Two-row layout)
# -------------------------
def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    shop_owner_id, locked = get_session(uid)

    # Seller customer (locked) -> seller shop only
    if locked == 1:
        return two_cols([
            InlineKeyboardButton("🛒 Products", callback_data="m:products"),
            InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
            InlineKeyboardButton("🆘 Support", callback_data="m:support"),
        ])

    # Super Admin in main shop
    if is_super(uid) and shop_owner_id == SUPER_ADMIN_ID:
        return two_cols([
            InlineKeyboardButton("🛒 Products", callback_data="m:products"),
            InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
            InlineKeyboardButton("🆘 Support", callback_data="m:support"),
            InlineKeyboardButton("⭐ Become Seller", callback_data="m:become_seller"),
            InlineKeyboardButton("🛠 Admin Panel", callback_data="m:admin"),
            InlineKeyboardButton("👑 Super Admin", callback_data="m:super"),
        ])

    # Seller owner inside THEIR shop (not locked)
    if seller_panel_allowed(uid) and shop_owner_id == uid:
        return two_cols([
            InlineKeyboardButton("🛒 Products", callback_data="m:products"),
            InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
            InlineKeyboardButton("🆘 Support", callback_data="m:support"),
            InlineKeyboardButton("🛠 Admin Panel", callback_data="m:admin"),
            InlineKeyboardButton("🏬 Main Shop", callback_data="m:mainshop"),
            InlineKeyboardButton("🔗 Share My Shop", callback_data="m:share"),
        ])

    # Normal user in main shop
    if shop_owner_id == SUPER_ADMIN_ID:
        return two_cols([
            InlineKeyboardButton("🛒 Products", callback_data="m:products"),
            InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
            InlineKeyboardButton("🆘 Support", callback_data="m:support"),
            InlineKeyboardButton("⭐ Become Seller", callback_data="m:become_seller"),
        ])

    # Fallback (should not happen often)
    return two_cols([
        InlineKeyboardButton("🛒 Products", callback_data="m:products"),
        InlineKeyboardButton("💰 Wallet", callback_data="m:wallet"),
        InlineKeyboardButton("🆘 Support", callback_data="m:support"),
    ])

def back_to_menu_kb() -> InlineKeyboardMarkup:
    return two_cols([
        InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
    ])

# -------------------------
# Render Welcome (text/photo/video)
# -------------------------
async def send_welcome_to_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)

    s = get_shop_settings(shop_owner_id)
    text = (s["welcome_text"] or "").strip()
    file_id = (s["welcome_file_id"] or "").strip()
    file_type = (s["welcome_file_type"] or "").strip()

    # Footer rule: seller customers should NOT see "Bot created by @RekkoOwn" when seller shop active,
    # but if seller subscription ends and they go back to main shop, main shop welcome includes footer.
    # Seller shops default welcome excludes footer already; seller can customize.

    title = f"🏬 <b>{esc(shop_name(shop_owner_id))}</b>\n\n"
    caption = title + text if text else title

    if file_id and file_type == "photo":
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(uid),
        )
    elif file_id and file_type == "video":
        await context.bot.send_video(
            chat_id=update.effective_chat.id,
            video=file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(uid),
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(uid),
        )

# -------------------------
# /start handler
# -------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    upsert_user(u)

    uid = u.id
    arg = context.args[0] if context.args else ""

    # If user opens a seller shop via deep link: /start s_<sellerid>
    seller_id = parse_start_arg(arg)
    if seller_id and seller_id != SUPER_ADMIN_ID and seller_active(seller_id):
        # Seller customer locked into seller shop
        ensure_shop_settings(seller_id)
        set_session(uid, seller_id, 1)
        # create balance row in seller shop (0)
        ensure_balance(seller_id, uid)
        await send_welcome_to_chat(update, context)
        return

    # If super admin: always main shop
    if is_super(uid):
        set_session(uid, SUPER_ADMIN_ID, 0)
        ensure_balance(SUPER_ADMIN_ID, uid)
        await send_welcome_to_chat(update, context)
        return

    # If seller owner and active: default into THEIR shop (not locked)
    if seller_panel_allowed(uid):
        ensure_shop_settings(uid)
        set_session(uid, uid, 0)
        ensure_balance(uid, uid)  # seller can have balance in their shop too (optional)
        ensure_balance(SUPER_ADMIN_ID, uid)  # seller also has balance in main shop for subscription payments
        await send_welcome_to_chat(update, context)
        return

    # Normal user: main shop
    set_session(uid, SUPER_ADMIN_ID, 0)
    ensure_balance(SUPER_ADMIN_ID, uid)
    await send_welcome_to_chat(update, context)

# -------------------------
# Navigation actions
# -------------------------
async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE, delete_current: bool = True) -> None:
    if delete_current:
        await delete_callback_message(update, context)
    # reset transient state
    context.user_data.pop("nav_stack", None)
    context.user_data.pop("qty", None)
    context.user_data.pop("mode", None)
    context.user_data.pop("draft", None)
    # re-send welcome/menu
    fake_update = update
    await send_welcome_to_chat(fake_update, context)

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

# -------------------------
# Menus: Products (Category -> CoCat -> Products)
# -------------------------
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)

    if is_banned(shop_owner_id, uid):
        await update.callback_query.answer("You are banned from this shop.", show_alert=True)
        return

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM categories WHERE shop_owner_id=? ORDER BY id DESC", (shop_owner_id,))
    rows = cur.fetchall()
    conn.close()

    buttons: List[InlineKeyboardButton] = []
    for r in rows:
        buttons.append(InlineKeyboardButton(r["name"], callback_data=f"cat:{r['id']}"))

    if not buttons:
        await update.callback_query.message.reply_text(
            "No categories yet.",
            reply_markup=back_to_menu_kb(),
        )
        return

    push_nav(context, "cats")
    await update.callback_query.message.reply_text(
        f"🛒 <b>Categories</b>\nShop: <b>{esc(shop_name(shop_owner_id))}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=two_cols(buttons + [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]),
    )

async def show_cocats(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)

    cat = get_category(shop_owner_id, cat_id)
    if not cat:
        await update.callback_query.answer("Category not found.", show_alert=True)
        return

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name FROM cocategories WHERE shop_owner_id=? AND category_id=? ORDER BY id DESC",
        (shop_owner_id, cat_id),
    )
    rows = cur.fetchall()
    conn.close()

    buttons = [InlineKeyboardButton(r["name"], callback_data=f"cocat:{r['id']}") for r in rows]
    push_nav(context, f"cat:{cat_id}")

    extra = [
        InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
    ]
    await update.callback_query.message.reply_text(
        f"📂 <b>{esc(cat['name'])}</b>\nSelect a sub-category:",
        parse_mode=ParseMode.HTML,
        reply_markup=two_cols(buttons + extra),
    )

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE, cocat_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)

    cocat = get_cocat(shop_owner_id, cocat_id)
    if not cocat:
        await update.callback_query.answer("Not found.", show_alert=True)
        return

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name FROM products WHERE shop_owner_id=? AND cocategory_id=? ORDER BY id DESC",
        (shop_owner_id, cocat_id),
    )
    rows = cur.fetchall()
    conn.close()

    buttons = [InlineKeyboardButton(r["name"], callback_data=f"prod:{r['id']}") for r in rows]
    push_nav(context, f"cocat:{cocat_id}")

    extra = [
        InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
    ]

    await update.callback_query.message.reply_text(
        f"🧾 <b>{esc(cocat['name'])}</b>\nChoose a product:",
        parse_mode=ParseMode.HTML,
        reply_markup=two_cols(buttons + extra),
    )

# -------------------------
# Product view + Quantity +/- + Buy
# -------------------------
async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)

    prod = get_product(shop_owner_id, product_id)
    if not prod:
        await update.callback_query.answer("Product not found.", show_alert=True)
        return

    stock = count_stock(shop_owner_id, product_id)
    qty_map = context.user_data.get("qty") or {}
    qty = int(qty_map.get(str(product_id), 1))
    if qty < 1:
        qty = 1
    if stock > 0:
        qty = min(qty, stock)
    else:
        qty = 1

    qty_map[str(product_id)] = qty
    context.user_data["qty"] = qty_map

    desc = (prod["description"] or "").strip()
    total = float(prod["price"]) * qty

    text = (
        f"🛍 <b>{esc(prod['name'])}</b>\n"
        f"Price: <b>{money(prod['price'])} {esc(CURRENCY)}</b>\n"
        f"Stock: <b>{stock}</b>\n\n"
        f"Quantity: <b>{qty}</b>\n"
        f"Total: <b>{money(total)} {esc(CURRENCY)}</b>"
    )
    if desc:
        text += f"\n\n📝 {esc(desc)}"

    buttons: List[List[InlineKeyboardButton]] = []
    buttons.append([
        InlineKeyboardButton("➖", callback_data=f"qty:{product_id}:dec"),
        InlineKeyboardButton("➕", callback_data=f"qty:{product_id}:inc"),
    ])
    buttons.append([InlineKeyboardButton("✅ Buy", callback_data=f"buy:{product_id}")])

    # Get File button only after purchase; link remains hidden until purchased.
    buttons.append([
        InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
    ])

    push_nav(context, f"prod:{product_id}")
    await update.callback_query.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb(buttons),
    )

async def change_qty(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, delta: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    prod = get_product(shop_owner_id, product_id)
    if not prod:
        await update.callback_query.answer("Not found.", show_alert=True)
        return

    stock = count_stock(shop_owner_id, product_id)
    qty_map = context.user_data.get("qty") or {}
    qty = int(qty_map.get(str(product_id), 1))
    qty = max(1, qty + delta)
    if stock > 0:
        qty = min(qty, stock)
    qty_map[str(product_id)] = qty
    context.user_data["qty"] = qty_map

    await update.callback_query.answer(f"Qty: {qty}")
    # show again (fresh message, then delete previous)
    await delete_callback_message(update, context)
    # simulate view by sending product again
    class _Fake:
        callback_query = update.callback_query
    await show_product(update, context, product_id)

async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)

    if is_banned(shop_owner_id, uid):
        await update.callback_query.answer("You are banned from this shop.", show_alert=True)
        return

    prod = get_product(shop_owner_id, product_id)
    if not prod:
        await update.callback_query.answer("Product not found.", show_alert=True)
        return

    qty_map = context.user_data.get("qty") or {}
    qty = int(qty_map.get(str(product_id), 1))
    qty = max(1, qty)

    stock = count_stock(shop_owner_id, product_id)
    if stock <= 0:
        await update.callback_query.answer("Out of stock.", show_alert=True)
        return
    if qty > stock:
        qty = stock
        qty_map[str(product_id)] = qty
        context.user_data["qty"] = qty_map

    total = float(prod["price"]) * qty
    bal = get_balance(shop_owner_id, uid)
    if bal < total:
        await update.callback_query.answer("Not enough balance.", show_alert=True)
        return

    # Reserve keys
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, key_line FROM product_keys WHERE shop_owner_id=? AND product_id=? AND delivered_once=0 ORDER BY id ASC LIMIT ?",
        (shop_owner_id, product_id, qty),
    )
    keys = cur.fetchall()
    if len(keys) < qty:
        conn.close()
        await update.callback_query.answer("Out of stock.", show_alert=True)
        return

    # Deduct balance
    set_balance(shop_owner_id, uid, bal - total)

    # Mark keys delivered
    now = ts()
    key_lines: List[str] = []
    for k in keys:
        key_lines.append(k["key_line"])
        cur.execute(
            "UPDATE product_keys SET delivered_once=1, delivered_to=?, delivered_at=? WHERE id=?",
            (uid, now, k["id"]),
        )
    conn.commit()
    conn.close()

    # Log history
    log_tx(shop_owner_id, uid, "purchase", -total, note=prod["name"], qty=qty)

    # Notify seller + super admin
    await notify_purchase(context, shop_owner_id, uid, prod, qty, total)

    # Delivery message with hidden link behind button
    msg = (
        "✅ <b>Purchase Successful</b>\n\n"
        f"Product: <b>{esc(prod['name'])}</b>\n"
        f"Quantity: <b>{qty}</b>\n"
        f"Paid: <b>{money(total)} {esc(CURRENCY)}</b>\n"
        f"Total Balance: <b>{money(get_balance(shop_owner_id, uid))} {esc(CURRENCY)}</b>\n\n"
        "🔑 <b>Keys</b>\n"
        + "\n".join([esc(x) for x in key_lines])
    )

    rows: List[List[InlineKeyboardButton]] = []
    if (prod["tg_link"] or "").strip():
        rows.append([InlineKeyboardButton("📁 Get File", callback_data=f"getfile:{product_id}")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")])

    await delete_callback_message(update, context)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=msg,
        parse_mode=ParseMode.HTML,
        reply_markup=kb(rows),
        disable_web_page_preview=True,
    )

async def get_file_link(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)

    prod = get_product(shop_owner_id, product_id)
    if not prod:
        await update.callback_query.answer("Not found.", show_alert=True)
        return

    link = (prod["tg_link"] or "").strip()
    if not link:
        await update.callback_query.answer("No link set.", show_alert=True)
        return

    await update.callback_query.answer()
    # Send link as separate message (hidden until button click)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📁 <b>Private Link</b>\n{esc(link)}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=two_cols([
            InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
            InlineKeyboardButton("⬅️ Back", callback_data="nav:back"),
        ]),
    )

# -------------------------
# Wallet / Support / Become Seller placeholders (Part 3 completes)
# -------------------------
async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    bal = get_balance(shop_owner_id, uid)

    s = get_shop_settings(shop_owner_id)
    wallet_msg = (s["wallet_message"] or "").strip()
    wallet_addr = (s["wallet_address"] or "").strip()

    txt = (
        f"💰 <b>Wallet</b>\n"
        f"Shop: <b>{esc(shop_name(shop_owner_id))}</b>\n\n"
        f"Balance: <b>{money(bal)} {esc(CURRENCY)}</b>\n\n"
    )
    if wallet_msg:
        txt += f"{esc(wallet_msg)}\n\n"
    if wallet_addr:
        txt += f"Address:\n<code>{esc(wallet_addr)}</code>\n\n"
    txt += "Deposit requires photo proof. (Deposit flow is in Part 3)"

    await update.callback_query.message.reply_text(
        txt, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb()
    )

async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.message.reply_text(
        "🆘 Support inbox is handled in Part 3 (draft → DONE).",
        reply_markup=back_to_menu_kb(),
    )

async def show_become_seller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    if shop_owner_id != SUPER_ADMIN_ID:
        await update.callback_query.answer("Only available in Main Shop.", show_alert=True)
        return

    s = get_shop_settings(SUPER_ADMIN_ID)
    desc = (s["seller_desc"] or "").strip()
    if not desc:
        desc = (
            f"⭐ Become a Seller\n\nPrice: {money(SELLER_SUB_PRICE)} {CURRENCY} / {SELLER_SUB_DAYS} days"
        )

    await update.callback_query.message.reply_text(
        desc,
        parse_mode=ParseMode.HTML,
        reply_markup=two_cols([
            InlineKeyboardButton("✅ Purchase", callback_data="seller:buy"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
        ]),
    )

# -------------------------
# Seller buy (Main shop) -> grants seller sub + locks future rules (Part 3 finalizes)
# -------------------------
def add_seller_days(uid: int, days: int) -> None:
    ensure_seller(uid)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT sub_until FROM sellers WHERE seller_id=?", (uid,))
    r = cur.fetchone()
    base = max(int(r["sub_until"] or 0), ts())
    cur.execute("UPDATE sellers SET sub_until=? WHERE seller_id=?", (base + int(days) * 86400, uid))
    conn.commit()
    conn.close()

async def buy_seller_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    # payment always from main shop balance
    bal = get_balance(SUPER_ADMIN_ID, uid)
    if bal < SELLER_SUB_PRICE:
        await update.callback_query.answer("Not enough balance in Main Shop.", show_alert=True)
        return

    # deduct main shop balance
    set_balance(SUPER_ADMIN_ID, uid, bal - SELLER_SUB_PRICE)
    log_tx(SUPER_ADMIN_ID, uid, "seller_sub", -SELLER_SUB_PRICE, "Become Seller")

    # extend seller sub
    add_seller_days(uid, SELLER_SUB_DAYS)

    # move user into THEIR shop after purchase, but as seller owner (not locked)
    ensure_shop_settings(uid)
    set_session(uid, uid, 0)

    await delete_callback_message(update, context)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "✅ <b>You are now a Seller!</b>\n\n"
            f"Subscription: <b>+{SELLER_SUB_DAYS} days</b>\n"
            "Your shop is ready.\n"
            "Use <b>Admin Panel</b> to add categories & products."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(uid),
    )

# -------------------------
# Seller "Subscription" menu (inside Seller Admin Panel) -> redirect to main shop pay
# -------------------------
async def seller_subscription_screen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not seller_panel_allowed(uid) and not is_super(uid):
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return

    days_left = seller_days_left(uid)
    txt = (
        "📅 <b>Subscription</b>\n\n"
        f"Days Left: <b>{days_left}</b>\n\n"
        f"Extend: <b>+{SELLER_SUB_DAYS} days</b>\n"
        f"Cost: <b>{money(SELLER_SUB_PRICE)} {esc(CURRENCY)}</b>\n\n"
        "⚠️ Payment will be deducted from your <b>Main Shop</b> balance."
    )
    await update.callback_query.message.reply_text(
        txt,
        parse_mode=ParseMode.HTML,
        reply_markup=two_cols([
            InlineKeyboardButton(f"✅ Extend +{SELLER_SUB_DAYS} days", callback_data="seller:extend"),
            InlineKeyboardButton("🏬 Main Shop", callback_data="m:mainshop"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
        ]),
    )

async def seller_extend_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not seller_panel_allowed(uid) and not is_super(uid):
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return

    ok = seller_extend_from_main_shop(uid)
    if not ok:
        await update.callback_query.answer("Not enough balance in Main Shop.", show_alert=True)
        return

    await delete_callback_message(update, context)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "✅ <b>Subscription Extended</b>\n\n"
            f"Added: <b>+{SELLER_SUB_DAYS} days</b>\n"
            f"Cost: <b>{money(SELLER_SUB_PRICE)} {esc(CURRENCY)}</b>\n"
            f"Days Left: <b>{seller_days_left(uid)}</b>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(uid),
    )

# -------------------------
# Seller: Share My Shop (deep link)
# -------------------------
async def share_my_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not seller_panel_allowed(uid) and not is_super(uid):
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return

    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start=s_{uid}"

    await update.callback_query.message.reply_text(
        f"🔗 <b>Your Shop Link</b>\n\n{esc(link)}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=back_to_menu_kb(),
    )

# -------------------------
# Switch to Main Shop (for Sellers only)
# -------------------------
async def goto_main_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not seller_panel_allowed(uid) and not is_super(uid):
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return
    # Sellers can view main shop only via this button
    set_session(uid, SUPER_ADMIN_ID, 0)
    ensure_balance(SUPER_ADMIN_ID, uid)
    await delete_callback_message(update, context)
    await send_welcome_to_chat(update, context)

# -------------------------
# ADMIN PANEL (Catalog & Products Edit)
# -------------------------
def admin_kb(uid: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton("📂 Categories", callback_data="adm:cats"),
        InlineKeyboardButton("📅 Subscription", callback_data="adm:sub"),
    ]
    # Super admin can manage main shop too (and will also have Super Admin button elsewhere)
    # Sellers use this to manage their shop.
    return two_cols(buttons + [
        InlineKeyboardButton("⬅️ Back", callback_data="nav:home"),
    ])

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)

    # Only seller owners in their own shop OR super admin in main shop
    if locked == 1:
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return

    if is_super(uid):
        # allowed everywhere, but admin panel operates on CURRENT shop session
        pass
    else:
        if shop_owner_id != uid:
            await update.callback_query.answer("Open your shop to use Admin Panel.", show_alert=True)
            return
        if not seller_panel_allowed(uid):
            await update.callback_query.answer("Seller panel disabled/expired.", show_alert=True)
            return

    await update.callback_query.message.reply_text(
        f"🛠 <b>Admin Panel</b>\nShop: <b>{esc(shop_name(shop_owner_id))}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_kb(uid),
    )

async def adm_list_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    if locked == 1:
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return

    if not is_super(uid) and shop_owner_id != uid:
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM categories WHERE shop_owner_id=? ORDER BY id DESC", (shop_owner_id,))
    rows = cur.fetchall()
    conn.close()

    buttons = [InlineKeyboardButton(f"📂 {r['name']}", callback_data=f"adm:cat:{r['id']}") for r in rows]
    buttons.append(InlineKeyboardButton("➕ Add Category", callback_data="adm:addcat"))
    buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="m:admin"))

    await update.callback_query.message.reply_text(
        "📂 <b>Categories</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=two_cols(buttons),
    )

async def adm_open_category(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    if locked == 1:
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return
    if not is_super(uid) and shop_owner_id != uid:
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return

    cat = get_category(shop_owner_id, cat_id)
    if not cat:
        await update.callback_query.answer("Not found.", show_alert=True)
        return

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name FROM cocategories WHERE shop_owner_id=? AND category_id=? ORDER BY id DESC",
        (shop_owner_id, cat_id),
    )
    cocats = cur.fetchall()
    conn.close()

    buttons = [InlineKeyboardButton(f"🗂 {r['name']}", callback_data=f"adm:cocat:{r['id']}") for r in cocats]
    buttons.append(InlineKeyboardButton("➕ Add Co-Category", callback_data=f"adm:addcocat:{cat_id}"))
    buttons.append(InlineKeyboardButton("🗑 Delete Category", callback_data=f"adm:delcat:{cat_id}"))
    buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="adm:cats"))

    await update.callback_query.message.reply_text(
        f"📂 <b>{esc(cat['name'])}</b>\nManage sub-categories:",
        parse_mode=ParseMode.HTML,
        reply_markup=two_cols(buttons),
    )

async def adm_open_cocat(update: Update, context: ContextTypes.DEFAULT_TYPE, cocat_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    if locked == 1:
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return
    if not is_super(uid) and shop_owner_id != uid:
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return

    cocat = get_cocat(shop_owner_id, cocat_id)
    if not cocat:
        await update.callback_query.answer("Not found.", show_alert=True)
        return

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name FROM products WHERE shop_owner_id=? AND cocategory_id=? ORDER BY id DESC",
        (shop_owner_id, cocat_id),
    )
    prods = cur.fetchall()
    conn.close()

    buttons = [InlineKeyboardButton(f"🛍 {r['name']}", callback_data=f"adm:prod:{r['id']}") for r in prods]
    buttons.append(InlineKeyboardButton("➕ Add Product", callback_data=f"adm:addprod:{cocat_id}"))
    buttons.append(InlineKeyboardButton("🗑 Delete Co-Category", callback_data=f"adm:delcocat:{cocat_id}"))
    buttons.append(InlineKeyboardButton("⬅️ Back", callback_data=f"adm:cat:{cocat['category_id']}"))

    await update.callback_query.message.reply_text(
        f"🗂 <b>{esc(cocat['name'])}</b>\nManage products:",
        parse_mode=ParseMode.HTML,
        reply_markup=two_cols(buttons),
    )

async def adm_open_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    uid = update.effective_user.id
    shop_owner_id, locked = get_session(uid)
    if locked == 1:
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return
    if not is_super(uid) and shop_owner_id != uid:
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return

    prod = get_product(shop_owner_id, product_id)
    if not prod:
        await update.callback_query.answer("Not found.", show_alert=True)
        return

    stock = count_stock(shop_owner_id, product_id)
    txt = (
        f"🛍 <b>{esc(prod['name'])}</b>\n"
        f"Price: <b>{money(prod['price'])} {esc(CURRENCY)}</b>\n"
        f"Stock (keys): <b>{stock}</b>\n"
        f"Link set: <b>{'Yes' if (prod['tg_link'] or '').strip() else 'No'}</b>\n"
    )
    if (prod["description"] or "").strip():
        txt += f"\n📝 {esc(prod['description'])}"

    buttons = [
        InlineKeyboardButton("✏️ Edit Name", callback_data=f"adm:editname:{product_id}"),
        InlineKeyboardButton("💲 Edit Price", callback_data=f"adm:editprice:{product_id}"),
        InlineKeyboardButton("📝 Edit Description", callback_data=f"adm:editdesc:{product_id}"),
        InlineKeyboardButton("🔗 Set Private Link", callback_data=f"adm:setlink:{product_id}"),
        InlineKeyboardButton("🔑 Add Keys", callback_data=f"adm:addkeys:{product_id}"),
        InlineKeyboardButton("🗑 Delete Product", callback_data=f"adm:delprod:{product_id}"),
        InlineKeyboardButton("⬅️ Back", callback_data=f"adm:cocat:{prod['cocategory_id']}"),
    ]

    await update.callback_query.message.reply_text(
        txt, parse_mode=ParseMode.HTML, reply_markup=two_cols(buttons)
    )

# -------------------------
# Admin text-input modes (NO IDs, only the clicked item)
# -------------------------
def set_mode(context: ContextTypes.DEFAULT_TYPE, mode: str, data: dict) -> None:
    context.user_data["mode"] = mode
    context.user_data["mode_data"] = data

def clear_mode(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("mode", None)
    context.user_data.pop("mode_data", None)

async def admin_prompt_text(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str) -> None:
    await update.callback_query.message.reply_text(prompt, reply_markup=back_to_menu_kb())

async def admin_add_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, "addcat", {})
    await admin_prompt_text(update, context, "Send new <b>Category Name</b>:",)

async def admin_add_cocat_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int) -> None:
    set_mode(context, "addcocat", {"cat_id": cat_id})
    await admin_prompt_text(update, context, "Send new <b>Co-Category Name</b>:")

async def admin_add_product_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, cocat_id: int) -> None:
    set_mode(context, "addprod_name", {"cocat_id": cocat_id})
    await admin_prompt_text(update, context, "Send <b>Product Name</b>:")

# product edits
async def admin_edit_name_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    set_mode(context, "editname", {"product_id": product_id})
    await admin_prompt_text(update, context, "Send new <b>Product Name</b>:")

async def admin_edit_price_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    set_mode(context, "editprice", {"product_id": product_id})
    await admin_prompt_text(update, context, "Send new <b>Price</b> (number):")

async def admin_edit_desc_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    set_mode(context, "editdesc", {"product_id": product_id})
    await admin_prompt_text(update, context, "Send new <b>Description</b> (or '-' to clear):")

async def admin_set_link_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    set_mode(context, "setlink", {"product_id": product_id})
    await admin_prompt_text(update, context, "Send <b>Private Telegram Link</b> (or '-' to clear):")

async def admin_add_keys_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> None:
    set_mode(context, "addkeys", {"product_id": product_id})
    await admin_prompt_text(
        update,
        context,
        "Send keys (one key per line).\n\n<b>1 line = 1 stock</b>",
    )

# -------------------------
# Admin deletes (button-only)
# -------------------------
def del_category(shop_owner_id: int, cat_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    # cascade manually
    cur.execute("SELECT id FROM cocategories WHERE shop_owner_id=? AND category_id=?", (shop_owner_id, cat_id))
    cocats = [int(r["id"]) for r in cur.fetchall()]
    for cc in cocats:
        cur.execute("SELECT id FROM products WHERE shop_owner_id=? AND cocategory_id=?", (shop_owner_id, cc))
        prods = [int(r["id"]) for r in cur.fetchall()]
        for pid in prods:
            cur.execute("DELETE FROM product_keys WHERE shop_owner_id=? AND product_id=?", (shop_owner_id, pid))
        cur.execute("DELETE FROM products WHERE shop_owner_id=? AND cocategory_id=?", (shop_owner_id, cc))
    cur.execute("DELETE FROM cocategories WHERE shop_owner_id=? AND category_id=?", (shop_owner_id, cat_id))
    cur.execute("DELETE FROM categories WHERE shop_owner_id=? AND id=?", (shop_owner_id, cat_id))
    conn.commit()
    conn.close()

def del_cocat(shop_owner_id: int, cocat_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM products WHERE shop_owner_id=? AND cocategory_id=?", (shop_owner_id, cocat_id))
    prods = [int(r["id"]) for r in cur.fetchall()]
    for pid in prods:
        cur.execute("DELETE FROM product_keys WHERE shop_owner_id=? AND product_id=?", (shop_owner_id, pid))
    cur.execute("DELETE FROM products WHERE shop_owner_id=? AND cocategory_id=?", (shop_owner_id, cocat_id))
    cur.execute("DELETE FROM cocategories WHERE shop_owner_id=? AND id=?", (shop_owner_id, cocat_id))
    conn.commit()
    conn.close()

def del_product(shop_owner_id: int, product_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM product_keys WHERE shop_owner_id=? AND product_id=?", (shop_owner_id, product_id))
    cur.execute("DELETE FROM products WHERE shop_owner_id=? AND id=?", (shop_owner_id, product_id))
    conn.commit()
    conn.close()

# -------------------------
# Text message handler (admin modes only here; Support/Deposit in Part 3)
# -------------------------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    uid = update.effective_user.id
    upsert_user(update.effective_user)

    mode = context.user_data.get("mode")
    data = context.user_data.get("mode_data") or {}
    if not mode:
        return

    shop_owner_id, locked = get_session(uid)
    # Admin modes only for seller owner in their shop OR super admin in current shop
    if locked == 1:
        clear_mode(context)
        return
    if not is_super(uid) and shop_owner_id != uid:
        clear_mode(context)
        return

    text = (update.message.text or "").strip()

    # ADD CATEGORY
    if mode == "addcat":
        if not text:
            return
        conn = db()
        cur = conn.cursor()
        cur.execute("INSERT INTO categories(shop_owner_id, name) VALUES(?,?)", (shop_owner_id, text))
        conn.commit()
        conn.close()
        clear_mode(context)
        await update.message.reply_text("✅ Category created.", reply_markup=admin_kb(uid))
        return

    # ADD COCAT
    if mode == "addcocat":
        cat_id = int(data.get("cat_id"))
        if not text:
            return
        if not get_category(shop_owner_id, cat_id):
            clear_mode(context)
            return
        conn = db()
        cur = conn.cursor()
        cur.execute("INSERT INTO cocategories(shop_owner_id, category_id, name) VALUES(?,?,?)", (shop_owner_id, cat_id, text))
        conn.commit()
        conn.close()
        clear_mode(context)
        await update.message.reply_text("✅ Co-Category created.", reply_markup=admin_kb(uid))
        return

    # ADD PRODUCT FLOW: name -> price -> description(optional)
    if mode == "addprod_name":
        cocat_id = int(data.get("cocat_id"))
        if not get_cocat(shop_owner_id, cocat_id):
            clear_mode(context)
            return
        if not text:
            return
        set_mode(context, "addprod_price", {"cocat_id": cocat_id, "name": text})
        await update.message.reply_text("Send <b>Price</b> (number):", parse_mode=ParseMode.HTML)
        return

    if mode == "addprod_price":
        cocat_id = int(data.get("cocat_id"))
        name = data.get("name", "")
        try:
            price = float(text)
        except Exception:
            await update.message.reply_text("Send a valid number price.")
            return
        set_mode(context, "addprod_desc", {"cocat_id": cocat_id, "name": name, "price": price})
        await update.message.reply_text("Send <b>Description</b> (or '-' for none):", parse_mode=ParseMode.HTML)
        return

    if mode == "addprod_desc":
        cocat_id = int(data.get("cocat_id"))
        cocat = get_cocat(shop_owner_id, cocat_id)
        if not cocat:
            clear_mode(context)
            return
        name = data.get("name", "")
        price = float(data.get("price"))
        desc = "" if text == "-" else text
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO products(shop_owner_id, category_id, cocategory_id, name, price, description) VALUES(?,?,?,?,?,?)",
            (shop_owner_id, int(cocat["category_id"]), cocat_id, name, price, desc),
        )
        conn.commit()
        conn.close()
        clear_mode(context)
        await update.message.reply_text("✅ Product created. Now you can add keys & link.", reply_markup=admin_kb(uid))
        return

    # EDIT NAME
    if mode == "editname":
        product_id = int(data.get("product_id"))
        if not get_product(shop_owner_id, product_id):
            clear_mode(context)
            return
        conn = db()
        cur = conn.cursor()
        cur.execute("UPDATE products SET name=? WHERE shop_owner_id=? AND id=?", (text, shop_owner_id, product_id))
        conn.commit()
        conn.close()
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=admin_kb(uid))
        return

    # EDIT PRICE
    if mode == "editprice":
        product_id = int(data.get("product_id"))
        if not get_product(shop_owner_id, product_id):
            clear_mode(context)
            return
        try:
            price = float(text)
        except Exception:
            await update.message.reply_text("Send a valid number.")
            return
        conn = db()
        cur = conn.cursor()
        cur.execute("UPDATE products SET price=? WHERE shop_owner_id=? AND id=?", (price, shop_owner_id, product_id))
        conn.commit()
        conn.close()
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=admin_kb(uid))
        return

    # EDIT DESC
    if mode == "editdesc":
        product_id = int(data.get("product_id"))
        if not get_product(shop_owner_id, product_id):
            clear_mode(context)
            return
        desc = "" if text == "-" else text
        conn = db()
        cur = conn.cursor()
        cur.execute("UPDATE products SET description=? WHERE shop_owner_id=? AND id=?", (desc, shop_owner_id, product_id))
        conn.commit()
        conn.close()
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=admin_kb(uid))
        return

    # SET LINK
    if mode == "setlink":
        product_id = int(data.get("product_id"))
        if not get_product(shop_owner_id, product_id):
            clear_mode(context)
            return
        link = "" if text == "-" else text
        conn = db()
        cur = conn.cursor()
        cur.execute("UPDATE products SET tg_link=? WHERE shop_owner_id=? AND id=?", (link, shop_owner_id, product_id))
        conn.commit()
        conn.close()
        clear_mode(context)
        await update.message.reply_text("✅ Updated.", reply_markup=admin_kb(uid))
        return

    # ADD KEYS (multi-line)
    if mode == "addkeys":
        product_id = int(data.get("product_id"))
        if not get_product(shop_owner_id, product_id):
            clear_mode(context)
            return
        lines = [ln.strip() for ln in (update.message.text or "").splitlines()]
        lines = [ln for ln in lines if ln]
        if not lines:
            await update.message.reply_text("Send at least 1 key line.")
            return
        conn = db()
        cur = conn.cursor()
        for ln in lines:
            cur.execute(
                "INSERT INTO product_keys(shop_owner_id, product_id, key_line, delivered_once) VALUES(?,?,?,0)",
                (shop_owner_id, product_id, ln),
            )
        conn.commit()
        conn.close()
        clear_mode(context)
        await update.message.reply_text(f"✅ Added {len(lines)} keys.", reply_markup=admin_kb(uid))
        return

    clear_mode(context)

# -------------------------
# Callback router
# -------------------------
async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    uid = update.effective_user.id
    upsert_user(update.effective_user)

    data = q.data or ""

    # NAV
    if data == "nav:home":
        await q.answer()
        await go_home(update, context, delete_current=True)
        return
    if data == "nav:back":
        await q.answer()
        await delete_callback_message(update, context)
        pop_nav(context)  # discard current view tag
        last = pop_nav(context)
        if not last:
            await go_home(update, context, delete_current=False)
            return
        # render previous
        if last == "cats":
            await q.message.reply_text("⬅️ Back", reply_markup=back_to_menu_kb())
            return
        if last.startswith("cat:"):
            await show_cocats(update, context, int(last.split(":")[1]))
            return
        if last.startswith("cocat:"):
            await show_products(update, context, int(last.split(":")[1]))
            return
        if last.startswith("prod:"):
            await show_product(update, context, int(last.split(":")[1]))
            return
        await go_home(update, context, delete_current=False)
        return

    # MAIN MENU ACTIONS
    if data.startswith("m:"):
        await q.answer()
        action = data.split(":", 1)[1]
        if action == "products":
            await show_categories(update, context)
            return
        if action == "wallet":
            await show_wallet(update, context)
            return
        if action == "support":
            await show_support(update, context)
            return
        if action == "become_seller":
            await show_become_seller(update, context)
            return
        if action == "admin":
            await show_admin_panel(update, context)
            return
        if action == "mainshop":
            await goto_main_shop(update, context)
            return
        if action == "share":
            await share_my_shop(update, context)
            return
        # super admin menu is Part 3
        if action == "super":
            await q.message.reply_text("👑 Super Admin tools are in Part 3.", reply_markup=back_to_menu_kb())
            return

    # Browse callbacks
    if data.startswith("cat:"):
        await q.answer()
        await show_cocats(update, context, int(data.split(":")[1]))
        return
    if data.startswith("cocat:"):
        await q.answer()
        await show_products(update, context, int(data.split(":")[1]))
        return
    if data.startswith("prod:"):
        await q.answer()
        await show_product(update, context, int(data.split(":")[1]))
        return

    # qty
    if data.startswith("qty:"):
        await q.answer()
        _, pid, op = data.split(":")
        await change_qty(update, context, int(pid), 1 if op == "inc" else -1)
        return

    # buy
    if data.startswith("buy:"):
        await q.answer()
        await buy_product(update, context, int(data.split(":")[1]))
        return

    # getfile
    if data.startswith("getfile:"):
        await q.answer()
        await get_file_link(update, context, int(data.split(":")[1]))
        return

    # become seller purchase
    if data == "seller:buy":
        await q.answer()
        await buy_seller_subscription(update, context)
        return

    # seller subscription extend
    if data == "seller:extend":
        await q.answer()
        await seller_extend_subscription(update, context)
        return

    # Admin Panel callbacks
    if data == "adm:cats":
        await q.answer()
        await adm_list_categories(update, context)
        return
    if data == "adm:sub":
        await q.answer()
        await seller_subscription_screen(update, context)
        return

    if data.startswith("adm:cat:"):
        await q.answer()
        await adm_open_category(update, context, int(data.split(":")[2]))
        return
    if data.startswith("adm:cocat:"):
        await q.answer()
        await adm_open_cocat(update, context, int(data.split(":")[2]))
        return
    if data.startswith("adm:prod:"):
        await q.answer()
        await adm_open_product(update, context, int(data.split(":")[2]))
        return

    if data == "adm:addcat":
        await q.answer()
        await admin_add_category_prompt(update, context)
        return
    if data.startswith("adm:addcocat:"):
        await q.answer()
        await admin_add_cocat_prompt(update, context, int(data.split(":")[2]))
        return
    if data.startswith("adm:addprod:"):
        await q.answer()
        await admin_add_product_prompt(update, context, int(data.split(":")[2]))
        return

    if data.startswith("adm:editname:"):
        await q.answer()
        await admin_edit_name_prompt(update, context, int(data.split(":")[2]))
        return
    if data.startswith("adm:editprice:"):
        await q.answer()
        await admin_edit_price_prompt(update, context, int(data.split(":")[2]))
        return
    if data.startswith("adm:editdesc:"):
        await q.answer()
        await admin_edit_desc_prompt(update, context, int(data.split(":")[2]))
        return
    if data.startswith("adm:setlink:"):
        await q.answer()
        await admin_set_link_prompt(update, context, int(data.split(":")[2]))
        return
    if data.startswith("adm:addkeys:"):
        await q.answer()
        await admin_add_keys_prompt(update, context, int(data.split(":")[2]))
        return

    if data.startswith("adm:delcat:"):
        await q.answer()
        uid = update.effective_user.id
        shop_owner_id, locked = get_session(uid)
        if locked == 1:
            return
        if not is_super(uid) and shop_owner_id != uid:
            return
        del_category(shop_owner_id, int(data.split(":")[2]))
        await delete_callback_message(update, context)
        await q.message.reply_text("🗑 Deleted category.", reply_markup=admin_kb(uid))
        return

    if data.startswith("adm:delcocat:"):
        await q.answer()
        uid = update.effective_user.id
        shop_owner_id, locked = get_session(uid)
        if locked == 1:
            return
        if not is_super(uid) and shop_owner_id != uid:
            return
        del_cocat(shop_owner_id, int(data.split(":")[2]))
        await delete_callback_message(update, context)
        await q.message.reply_text("🗑 Deleted co-category.", reply_markup=admin_kb(uid))
        return

    if data.startswith("adm:delprod:"):
        await q.answer()
        uid = update.effective_user.id
        shop_owner_id, locked = get_session(uid)
        if locked == 1:
            return
        if not is_super(uid) and shop_owner_id != uid:
            return
        del_product(shop_owner_id, int(data.split(":")[2]))
        await delete_callback_message(update, context)
        await q.message.reply_text("🗑 Deleted product.", reply_markup=admin_kb(uid))
        return

    # Unknown
    await q.answer()

# -------------------------
# Register handlers in Part 3 main()
# -------------------------

# ============================================================
# AutoPanel Bot — PART 3 / 3
# Wallet Deposits (photo proof), History (clean),
# Support (draft → DONE), Super Admin tools, main()
# ============================================================

# -------------------------
# HISTORY (Clean format)
# -------------------------
async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT kind, amount, note, qty, created_at FROM transactions "
        "WHERE shop_owner_id=? AND user_id=? ORDER BY id DESC LIMIT 30",
        (shop_owner_id, uid),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.callback_query.message.reply_text(
            "📜 <b>History</b>\n\nNo records yet.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_kb(),
        )
        return

    lines = ["📜 <b>History</b>\n"]
    for r in rows:
        kind = r["kind"]
        amt = float(r["amount"] or 0)
        qty = int(r["qty"] or 1)
        note = (r["note"] or "").strip()

        if kind == "deposit":
            lines.append(f"Deposited: <b>+{money(amt)} {esc(CURRENCY)}</b>")
        elif kind == "purchase":
            lines.append(
                f"Purchased: <b>{esc(note)}</b>\n"
                f"Quantity: <b>{qty}</b>\n"
                f"Paid: <b>{money(abs(amt))} {esc(CURRENCY)}</b>"
            )
        elif kind == "adjust":
            sign = "+" if amt >= 0 else "-"
            lines.append(f"Balance Edited: <b>{sign}{money(abs(amt))} {esc(CURRENCY)}</b>")
        elif kind == "seller_sub":
            lines.append(f"Subscription: <b>{money(abs(amt))} {esc(CURRENCY)}</b>")
        lines.append("")

    await update.callback_query.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )

# -------------------------
# WALLET: Deposit (photo proof)
# -------------------------
async def wallet_deposit_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, "deposit_amount", {})
    await update.callback_query.message.reply_text(
        "💳 <b>Deposit</b>\n\nSend <b>amount</b> you want to deposit:",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb(),
    )

async def wallet_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)

    mode = context.user_data.get("mode")
    if mode != "deposit_amount":
        return

    try:
        amt = float((update.message.text or "").strip())
        if amt <= 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("Send a valid positive number.")
        return

    context.user_data["deposit_amount"] = amt
    set_mode(context, "deposit_proof", {})
    await update.message.reply_text("Now send <b>photo proof</b> of payment.", parse_mode=ParseMode.HTML)

async def wallet_proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)

    mode = context.user_data.get("mode")
    if mode != "deposit_proof":
        return
    if not update.message.photo:
        await update.message.reply_text("Please send a <b>photo</b> proof.", parse_mode=ParseMode.HTML)
        return

    amt = float(context.user_data.get("deposit_amount", 0))
    if amt <= 0:
        clear_mode(context)
        return

    file_id = update.message.photo[-1].file_id

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO deposit_requests(shop_owner_id, user_id, amount, proof_file_id, status, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (shop_owner_id, uid, amt, file_id, "pending", ts()),
    )
    conn.commit()
    conn.close()

    clear_mode(context)

    # Notify approver
    approver = shop_owner_id if shop_owner_id != SUPER_ADMIN_ID else SUPER_ADMIN_ID
    try:
        await context.bot.send_photo(
            chat_id=approver,
            photo=file_id,
            caption=(
                "💳 <b>Deposit Request</b>\n\n"
                f"User: <b>{esc(user_display(uid))}</b>\n"
                f"Amount: <b>{money(amt)} {esc(CURRENCY)}</b>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=two_cols([
                InlineKeyboardButton("✅ Approve", callback_data=f"dep:ok:{uid}:{amt}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"dep:no:{uid}:{amt}"),
            ]),
        )
    except Exception:
        pass

    await update.message.reply_text(
        "✅ Deposit submitted. Please wait for approval.",
        reply_markup=back_to_menu_kb(),
    )

# -------------------------
# Deposit Approve / Reject
# -------------------------
async def deposit_action(update: Update, context: ContextTypes.DEFAULT_TYPE, ok: bool, uid: int, amt: float) -> None:
    approver = update.effective_user.id
    shop_owner_id = approver if approver != SUPER_ADMIN_ID else SUPER_ADMIN_ID

    # Add or reject
    if ok:
        new_bal = get_balance(shop_owner_id, uid) + amt
        set_balance(shop_owner_id, uid, new_bal)
        log_tx(shop_owner_id, uid, "deposit", amt, "Deposit approved")
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    "✅ <b>Deposit Approved</b>\n\n"
                    f"Amount: <b>{money(amt)} {esc(CURRENCY)}</b>\n"
                    f"Total Balance: <b>{money(new_bal)} {esc(CURRENCY)}</b>"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text="❌ <b>Deposit Rejected</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    # Delete approval message
    await delete_callback_message(update, context)

# -------------------------
# SUPPORT (draft → DONE)
# -------------------------
async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, "support_draft", {"lines": []})
    await update.callback_query.message.reply_text(
        "🆘 <b>Support</b>\n\nSend your messages. When finished, tap <b>DONE</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=two_cols([
            InlineKeyboardButton("✅ DONE", callback_data="sup:done"),
            InlineKeyboardButton("⬅️ Back", callback_data="nav:home"),
        ]),
    )

async def support_collect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("mode") != "support_draft":
        return
    lines = context.user_data["mode_data"].setdefault("lines", [])
    lines.append(update.message.text or "")

async def support_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    shop_owner_id, _ = get_session(uid)
    data = context.user_data.get("mode_data") or {}
    lines = data.get("lines") or []
    clear_mode(context)
    if not lines:
        await update.callback_query.answer("No message sent.", show_alert=True)
        return

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO tickets(shop_owner_id, user_id, status, created_at, updated_at) VALUES(?,?,?,?,?)",
        (shop_owner_id, uid, "open", ts(), ts()),
    )
    tid = cur.lastrowid
    for ln in lines:
        cur.execute(
            "INSERT INTO ticket_messages(ticket_id, sender_id, text, created_at) VALUES(?,?,?,?)",
            (tid, uid, ln, ts()),
        )
    conn.commit()
    conn.close()

    # notify owner
    owner = shop_owner_id if shop_owner_id != SUPER_ADMIN_ID else SUPER_ADMIN_ID
    try:
        await context.bot.send_message(
            chat_id=owner,
            text=(
                "🆘 <b>New Support Ticket</b>\n\n"
                f"From: <b>{esc(user_display(uid))}</b>\n"
                f"Ticket ID: <b>{tid}</b>"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    await delete_callback_message(update, context)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ Support ticket sent.",
        reply_markup=back_to_menu_kb(),
    )

# -------------------------
# SUPER ADMIN TOOLS (ban / restrict sellers)
# -------------------------
async def super_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_super(update.effective_user.id):
        await update.callback_query.answer("Not allowed.", show_alert=True)
        return
    await update.callback_query.message.reply_text(
        "👑 <b>Super Admin</b>\n\nManage sellers.",
        parse_mode=ParseMode.HTML,
        reply_markup=two_cols([
            InlineKeyboardButton("🚫 Ban Seller Shop", callback_data="super:ban"),
            InlineKeyboardButton("⏳ Restrict 7 Days", callback_data="super:res7"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home"),
        ]),
    )

# -------------------------
# CALLBACK EXTENSIONS
# -------------------------
async def on_cb_part3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    data = q.data or ""

    # Wallet deposit
    if data == "wallet:deposit":
        await q.answer()
        await wallet_deposit_prompt(update, context)
        return

    # Deposit approve/reject
    if data.startswith("dep:"):
        await q.answer()
        _, act, uid, amt = data.split(":")
        await deposit_action(update, context, act == "ok", int(uid), float(amt))
        return

    # Support
    if data == "m:support":
        await q.answer()
        await support_start(update, context)
        return
    if data == "sup:done":
        await q.answer()
        await support_done(update, context)
        return

    # History
    if data == "m:history":
        await q.answer()
        await show_history(update, context)
        return

    # Super admin
    if data == "m:super":
        await q.answer()
        await super_panel(update, context)
        return

# -------------------------
# MAIN
# -------------------------
def main() -> None:
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))

    # Text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.PHOTO, wallet_proof_received))

    # Callbacks
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(CallbackQueryHandler(on_cb_part3))

    log.info("AutoPanel bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
