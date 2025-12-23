import os
import re
import json
import time
import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple, Dict, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
)
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

# ---------------------------
# Config (ENV)
# ---------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN env var")

SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", os.getenv("ADMIN_ID", "0")).strip() or "0")
if SUPER_ADMIN_ID <= 0:
    raise RuntimeError("Missing/invalid SUPER_ADMIN_ID (or ADMIN_ID) env var")

ADMIN_IDS = set()
_admin_ids_raw = os.getenv("ADMIN_IDS", "").strip()
if _admin_ids_raw:
    for p in re.split(r"[,\s]+", _admin_ids_raw):
        if p.strip().isdigit():
            ADMIN_IDS.add(int(p.strip()))

CURRENCY = os.getenv("CURRENCY", "USDT").strip()
STORE_NAME = os.getenv("STORE_NAME", "AutoPanel").strip()

# Main shop default wallet address (editable message; address itself usually fixed from env)
DEFAULT_MAIN_WALLET_ADDR = os.getenv("USDT_TRC20", "").strip() or os.getenv("MAIN_WALLET", "").strip()

SELLER_SUB_PRICE = float(os.getenv("SELLER_SUB_PRICE", "10").strip() or "10")
SELLER_SUB_DAYS = int(os.getenv("SELLER_SUB_DAYS", "30").strip() or "30")

DB_FILE = os.getenv("DB_FILE", "data.db").strip()

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("autopanel")

UTC = timezone.utc


# ---------------------------
# DB Helpers
# ---------------------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def now_ts() -> int:
    return int(time.time())


def fmt_money(x: float) -> str:
    # keep clean
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x))}"
    return f"{x:.2f}".rstrip("0").rstrip(".")


def escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def init_db() -> None:
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        last_seen INTEGER DEFAULT 0
    )
    """)

    # current shop context per user (who they are browsing)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        user_id INTEGER PRIMARY KEY,
        shop_owner_id INTEGER NOT NULL
    )
    """)

    # sellers (shop owners)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sellers (
        seller_id INTEGER PRIMARY KEY,
        sub_until INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0,
        restricted_until INTEGER DEFAULT 0,
        panel_banned INTEGER DEFAULT 0,
        balance REAL DEFAULT 0
    )
    """)

    # Shop settings per owner (includes main shop + seller shops)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shop_settings (
        shop_owner_id INTEGER PRIMARY KEY,
        wallet_address TEXT DEFAULT '',
        wallet_message TEXT DEFAULT '',
        welcome_text TEXT DEFAULT '',
        welcome_file_id TEXT DEFAULT '',
        welcome_file_type TEXT DEFAULT '',
        seller_desc TEXT DEFAULT ''
    )
    """)

    # balances per shop_owner_id and user_id
    cur.execute("""
    CREATE TABLE IF NOT EXISTS balances (
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        balance REAL DEFAULT 0,
        PRIMARY KEY (shop_owner_id, user_id)
    )
    """)

    # bans (main shop only for super admin, but stored per shop)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_bans (
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        banned INTEGER DEFAULT 1,
        PRIMARY KEY (shop_owner_id, user_id)
    )
    """)

    # Category / Co-category / Products
    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        file_type TEXT DEFAULT ''
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cocategories (
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
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        category_id INTEGER NOT NULL,
        cocategory_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        description TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        file_type TEXT DEFAULT '',
        key_text TEXT DEFAULT '',
        tg_link TEXT DEFAULT ''
    )
    """)

    # Transactions
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL, -- deposit_approved / purchase / balance_adjust / seller_sub
        amount REAL DEFAULT 0,
        note TEXT DEFAULT '',
        created_at INTEGER NOT NULL
    )
    """)

    # Deposit requests
    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposit_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        proof_file_id TEXT NOT NULL,
        status TEXT NOT NULL, -- pending/approved/rejected
        created_at INTEGER NOT NULL,
        handled_by INTEGER DEFAULT 0,
        handled_at INTEGER DEFAULT 0
    )
    """)

    # Support tickets
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL, -- open/closed
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ticket_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL, -- user or admin/seller
        text TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )
    """)

    conn.commit()

    # Ensure main shop settings exist
    ensure_shop_settings(SUPER_ADMIN_ID)
    # Default main wallet address (if env provided and DB empty)
    if DEFAULT_MAIN_WALLET_ADDR:
        cur.execute("SELECT wallet_address FROM shop_settings WHERE shop_owner_id=?", (SUPER_ADMIN_ID,))
        row = cur.fetchone()
        if row and not (row["wallet_address"] or "").strip():
            cur.execute(
                "UPDATE shop_settings SET wallet_address=? WHERE shop_owner_id=?",
                (DEFAULT_MAIN_WALLET_ADDR, SUPER_ADMIN_ID),
            )
            conn.commit()

    # Default welcome text for main shop (with footer)
    cur.execute("SELECT welcome_text FROM shop_settings WHERE shop_owner_id=?", (SUPER_ADMIN_ID,))
    row = cur.fetchone()
    if row and not (row["welcome_text"] or "").strip():
        default = (
            f"✅ Welcome to <b>{escape(STORE_NAME)}</b>\n"
            f"Get your 24/7 Store Panel Here !!\n\n"
            f"Bot created by @RekkoOwn"
        )
        cur.execute(
            "UPDATE shop_settings SET welcome_text=? WHERE shop_owner_id=?",
            (default, SUPER_ADMIN_ID),
        )
        conn.commit()

    # Default seller description
    cur.execute("SELECT seller_desc FROM shop_settings WHERE shop_owner_id=?", (SUPER_ADMIN_ID,))
    row = cur.fetchone()
    if row and not (row["seller_desc"] or "").strip():
        seller_desc = (
            "⭐ <b>Become a Seller</b>\n\n"
            "Open your own shop inside AutoPanel.\n\n"
            "✅ Your own products\n"
            "✅ Your own wallet & deposit approvals\n"
            "✅ Your own support inbox\n"
            "✅ Your own customers\n\n"
            f"Price: <b>{fmt_money(SELLER_SUB_PRICE)} {escape(CURRENCY)}</b> / <b>{SELLER_SUB_DAYS} days</b>\n"
            "Renew early to stack days."
        )
        cur.execute(
            "UPDATE shop_settings SET seller_desc=? WHERE shop_owner_id=?",
            (seller_desc, SUPER_ADMIN_ID),
        )
        conn.commit()

    conn.close()


def ensure_shop_settings(shop_owner_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT shop_owner_id FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    if not cur.fetchone():
        # Seller shops default welcome does NOT include RekkoOwn footer
        welcome = f"✅ Welcome to <b>{escape(STORE_NAME)}</b>\nGet your 24/7 Store Panel Here !!"
        cur.execute(
            "INSERT INTO shop_settings(shop_owner_id, wallet_address, wallet_message, welcome_text, welcome_file_id, welcome_file_type, seller_desc) "
            "VALUES(?,?,?,?,?,?,?)",
            (shop_owner_id, "", "", welcome, "", "", ""),
        )
        conn.commit()
    conn.close()


# ---------------------------
# Role / Permission
# ---------------------------
def is_superadmin(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS or is_superadmin(uid)


def get_seller_row(seller_id: int) -> Optional[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM sellers WHERE seller_id=?", (seller_id,))
    row = cur.fetchone()
    conn.close()
    return row


def is_active_seller(uid: int) -> bool:
    # Super admin is lifetime seller
    if is_superadmin(uid):
        return True
    row = get_seller_row(uid)
    if not row:
        return False
    if int(row["banned"]) == 1:
        return False
    sub_until = int(row["sub_until"] or 0)
    restricted_until = int(row["restricted_until"] or 0)
    if restricted_until and restricted_until > now_ts():
        return False
    return sub_until > now_ts()


def seller_panel_allowed(uid: int) -> bool:
    if is_superadmin(uid):
        return True
    row = get_seller_row(uid)
    if not row:
        return False
    if int(row["panel_banned"]) == 1:
        return False
    return is_active_seller(uid)


def seller_shop_allowed(seller_id: int) -> bool:
    if seller_id == SUPER_ADMIN_ID:
        return True
    row = get_seller_row(seller_id)
    if not row:
        return False
    if int(row["banned"]) == 1:
        return False
    restricted_until = int(row["restricted_until"] or 0)
    if restricted_until and restricted_until > now_ts():
        return False
    # shop exists while active subscription; if expired -> shop disabled
    sub_until = int(row["sub_until"] or 0)
    return sub_until > now_ts()


def is_banned_from_shop(shop_owner_id: int, user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT banned FROM user_bans WHERE shop_owner_id=? AND user_id=?",
        (shop_owner_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return bool(row and int(row["banned"]) == 1)


# ---------------------------
# Session: current shop context
# ---------------------------
def get_current_shop(uid: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT shop_owner_id FROM sessions WHERE user_id=?", (uid,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return SUPER_ADMIN_ID
    return int(row["shop_owner_id"])


def set_current_shop(uid: int, shop_owner_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO sessions(user_id, shop_owner_id) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET shop_owner_id=excluded.shop_owner_id",
                (uid, shop_owner_id))
    conn.commit()
    conn.close()


# ---------------------------
# Balances / Transactions
# ---------------------------
def get_balance(shop_owner_id: int, uid: int) -> float:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM balances WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    row = cur.fetchone()
    conn.close()
    return float(row["balance"]) if row else 0.0


def set_balance(shop_owner_id: int, uid: int, new_balance: float) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,?) "
        "ON CONFLICT(shop_owner_id, user_id) DO UPDATE SET balance=excluded.balance",
        (shop_owner_id, uid, float(new_balance)),
    )
    conn.commit()
    conn.close()


def add_balance(shop_owner_id: int, uid: int, delta: float) -> float:
    old = get_balance(shop_owner_id, uid)
    new = max(0.0, old + float(delta))
    set_balance(shop_owner_id, uid, new)
    return new


def log_tx(shop_owner_id: int, uid: int, kind: str, amount: float, note: str = "") -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions(shop_owner_id, user_id, kind, amount, note, created_at) VALUES(?,?,?,?,?,?)",
        (shop_owner_id, uid, kind, float(amount), note or "", now_ts()),
    )
    conn.commit()
    conn.close()


# ---------------------------
# Shop Settings
# ---------------------------
def get_shop_settings(shop_owner_id: int) -> sqlite3.Row:
    ensure_shop_settings(shop_owner_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    row = cur.fetchone()
    conn.close()
    assert row is not None
    return row


def set_shop_setting(shop_owner_id: int, field: str, value: str) -> None:
    if field not in {"wallet_address", "wallet_message", "welcome_text", "welcome_file_id", "welcome_file_type", "seller_desc"}:
        raise ValueError("Invalid setting field")
    ensure_shop_settings(shop_owner_id)
    conn = db()
    cur = conn.cursor()
    cur.execute(f"UPDATE shop_settings SET {field}=? WHERE shop_owner_id=?", (value or "", shop_owner_id))
    conn.commit()
    conn.close()


# ---------------------------
# Catalog Helpers
# ---------------------------
def list_categories(shop_owner_id: int) -> List[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE shop_owner_id=? ORDER BY id DESC", (shop_owner_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def list_cocategories(shop_owner_id: int, category_id: int) -> List[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM cocategories WHERE shop_owner_id=? AND category_id=? ORDER BY id DESC",
        (shop_owner_id, category_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def list_products(shop_owner_id: int, category_id: int, cocategory_id: int) -> List[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM products WHERE shop_owner_id=? AND category_id=? AND cocategory_id=? ORDER BY id DESC",
        (shop_owner_id, category_id, cocategory_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_product(shop_owner_id: int, product_id: int) -> Optional[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM products WHERE shop_owner_id=? AND id=?",
        (shop_owner_id, product_id),
    )
    row = cur.fetchone()
    conn.close()
    return row


# ---------------------------
# UI / Keyboards
# ---------------------------
def two_rows(buttons: List[InlineKeyboardButton], per_row: int = 2) -> List[List[InlineKeyboardButton]]:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for b in buttons:
        row.append(b)
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def kb_main_menu_for(uid: int, shop_owner_id: int) -> InlineKeyboardMarkup:
    # shop_owner_id indicates what shop the user is in currently
    # Normal main shop user: Products, Wallet, Support, Become Seller
    # Seller shop user: Products, Wallet, Support (no become seller)
    # Seller (shop owner): Products, Wallet, Support, Admin Panel (+ Main Shop optional)
    # Super Admin: Products, Wallet, Support, Admin Panel + Super Admin button on main menu
    btns: List[InlineKeyboardButton] = []

    if shop_owner_id != SUPER_ADMIN_ID and uid != shop_owner_id and not is_superadmin(uid):
        # seller shop user
        btns = [
            InlineKeyboardButton("🛒 Products", callback_data="U_PRODUCTS"),
            InlineKeyboardButton("💰 Wallet", callback_data="U_WALLET"),
            InlineKeyboardButton("🆘 Support", callback_data="U_SUPPORT"),
        ]
        return InlineKeyboardMarkup(two_rows(btns, 2))

    # owner viewing own shop OR main shop context
    btns = [
        InlineKeyboardButton("🛒 Products", callback_data="U_PRODUCTS"),
        InlineKeyboardButton("💰 Wallet", callback_data="U_WALLET"),
        InlineKeyboardButton("🆘 Support", callback_data="U_SUPPORT"),
    ]

    # Become Seller only for non-sellers and non-superadmin AND only in main shop
    if shop_owner_id == SUPER_ADMIN_ID and (not is_active_seller(uid)) and (not is_superadmin(uid)):
        btns.append(InlineKeyboardButton("⭐ Become Seller", callback_data="BECOME_SELLER"))

    # Admin panel for sellers (their own shop) + superadmin
    if (shop_owner_id == SUPER_ADMIN_ID and is_superadmin(uid)) or (shop_owner_id == uid and seller_panel_allowed(uid)):
        btns.append(InlineKeyboardButton("🛠 Admin Panel", callback_data="ADMIN_PANEL"))

    # super admin main menu gets Super Admin button (ONLY you)
    if is_superadmin(uid):
        btns.append(InlineKeyboardButton("👑 Super Admin", callback_data="SA_MENU"))

    return InlineKeyboardMarkup(two_rows(btns, 2))


def kb_back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]])


def kb_categories(shop_owner_id: int, categories: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for c in categories[:40]:
        rows.append([InlineKeyboardButton(f"📂 {c['name']}", callback_data=f"CAT:{shop_owner_id}:{c['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)


def kb_cocategories(shop_owner_id: int, category_id: int, cocats: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for cc in cocats[:40]:
        rows.append([InlineKeyboardButton(f"📁 {cc['name']}", callback_data=f"COCAT:{shop_owner_id}:{category_id}:{cc['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"U_PRODUCTS")])
    return InlineKeyboardMarkup(rows)


def kb_products(shop_owner_id: int, category_id: int, cocategory_id: int, prods: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for p in prods[:50]:
        price = fmt_money(float(p["price"]))
        rows.append([InlineKeyboardButton(f"🛒 {p['name']} — {price} {CURRENCY}", callback_data=f"PROD:{shop_owner_id}:{p['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"CAT:{shop_owner_id}:{category_id}")])
    return InlineKeyboardMarkup(rows)


def kb_product_view(shop_owner_id: int, product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Buy", callback_data=f"BUY:{shop_owner_id}:{product_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"PROD_BACK:{shop_owner_id}:{product_id}")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])


def kb_get_file(tg_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("📁 Get File", url=tg_link)]])


def kb_deposit_start() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Deposit", callback_data="DEP_START")],
        [InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")],
    ])


def kb_done_cancel(prefix: str) -> InlineKeyboardMarkup:
    # prefix identifies which draft we are finishing
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Done", callback_data=f"{prefix}_DONE"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"{prefix}_CANCEL"),
        ]
    ])


def kb_become_seller_pay() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💳 Pay {fmt_money(SELLER_SUB_PRICE)} {CURRENCY}", callback_data="SELLER_PAY")],
        [InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")],
    ])


def kb_admin_panel(uid: int, shop_owner_id: int) -> InlineKeyboardMarkup:
    # For sellers: only their own shop admin tools
    # For superadmin in main shop: main shop tools
    rows: List[List[InlineKeyboardButton]] = []

    rows.append([InlineKeyboardButton("🛒 Manage Shop", callback_data="M_SHOP")])
    rows.append([InlineKeyboardButton("💳 Approve Deposits", callback_data="M_DEPOSITS")])
    rows.append([InlineKeyboardButton("🆘 Support Inbox", callback_data="M_TICKETS")])

    # Users management ONLY inside admin panel (seller sees their shop users; superadmin sees main shop users)
    rows.append([InlineKeyboardButton("👥 Users", callback_data="M_USERS")])

    # Settings
    rows.append([InlineKeyboardButton("💳 Set Wallet Address", callback_data="M_SET_WALLET")])
    rows.append([InlineKeyboardButton("📝 Edit Wallet Message", callback_data="M_EDIT_WALLETMSG")])
    rows.append([InlineKeyboardButton("🖼 Edit Welcome Message", callback_data="M_EDIT_WELCOME")])

    # Seller extra
    if shop_owner_id == uid and seller_panel_allowed(uid):
        rows.append([InlineKeyboardButton("📣 Share My Shop", callback_data="M_SHARE")])
        # Allow seller (and super admin) to go to main shop; seller's users never see it
        rows.append([InlineKeyboardButton("🏬 Main Shop", callback_data="GO_MAIN_SHOP")])

    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)


def kb_shop_manage() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Category", callback_data="ADD_CAT")],
        [InlineKeyboardButton("➕ Add Co-Category", callback_data="ADD_COCAT")],
        [InlineKeyboardButton("➕ Add Product", callback_data="ADD_PROD")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ADMIN_PANEL")],
    ])


def kb_sa_main() -> InlineKeyboardMarkup:
    # Super admin top-level seller controls + search pages
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏪 Sellers", callback_data="SA_SELLERS")],
        [InlineKeyboardButton("👥 Users", callback_data="SA_USERS")],
        [InlineKeyboardButton("📝 Edit Become Seller Description", callback_data="SA_EDIT_SELLER_DESC")],
        [InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")],
    ])


def kb_sa_sellers_list(rows_sellers: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton("🔎 Search Seller", callback_data="SA_SELLER_SEARCH")])
    for r in rows_sellers[:40]:
        sid = int(r["seller_id"])
        uname = (get_user_display(sid) or f"Seller {sid}")
        rows.append([InlineKeyboardButton(f"🏪 {uname}", callback_data=f"SA_SELLER:{sid}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="SA_MENU")])
    return InlineKeyboardMarkup(rows)


def kb_sa_users_list(rows_users: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton("🔎 Search User", callback_data="SA_USER_SEARCH")])
    for r in rows_users[:50]:
        uid = int(r["user_id"])
        uname = get_user_display(uid) or f"User {uid}"
        rows.append([InlineKeyboardButton(f"👤 {uname}", callback_data=f"SA_USER:{uid}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="SA_MENU")])
    return InlineKeyboardMarkup(rows)


def kb_sa_seller_actions(seller_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Edit Seller Balance", callback_data=f"SA_SELLER_BAL:{seller_id}")],
        [InlineKeyboardButton("➕ Add 7 days", callback_data=f"SA_ADD_DAYS:{seller_id}:7")],
        [InlineKeyboardButton("➕ Add 14 days", callback_data=f"SA_ADD_DAYS:{seller_id}:14")],
        [InlineKeyboardButton("➕ Add 30 days", callback_data=f"SA_ADD_DAYS:{seller_id}:30")],
        [InlineKeyboardButton("⏳ Restrict 7 days", callback_data=f"SA_RESTRICT:{seller_id}:7")],
        [InlineKeyboardButton("⏳ Restrict 14 days", callback_data=f"SA_RESTRICT:{seller_id}:14")],
        [InlineKeyboardButton("⏳ Restrict 30 days", callback_data=f"SA_RESTRICT:{seller_id}:30")],
        [InlineKeyboardButton("🚫 Ban Seller Shop", callback_data=f"SA_BAN_SHOP:{seller_id}")],
        [InlineKeyboardButton("✅ Unban Seller Shop", callback_data=f"SA_UNBAN_SHOP:{seller_id}")],
        [InlineKeyboardButton("🚫 Ban Seller Panel", callback_data=f"SA_BAN_PANEL:{seller_id}")],
        [InlineKeyboardButton("✅ Unban Seller Panel", callback_data=f"SA_UNBAN_PANEL:{seller_id}")],
        [InlineKeyboardButton("🆘 Reply Seller Support", callback_data=f"SA_SELLER_TICKETS:{seller_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="SA_SELLERS")],
    ])


def kb_sa_user_actions(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Edit Balance", callback_data=f"SA_EDIT_BAL:{uid}")],
        [InlineKeyboardButton("🆘 Reply Support", callback_data=f"SA_USER_TICKETS:{uid}")],
        [InlineKeyboardButton("🚫 Ban From Main Shop", callback_data=f"SA_BAN_USER:{uid}")],
        [InlineKeyboardButton("✅ Unban From Main Shop", callback_data=f"SA_UNBAN_USER:{uid}")],
        [InlineKeyboardButton("📜 History", callback_data=f"SA_HIST:{uid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="SA_USERS")],
    ])


def kb_approve_reject_deposit(dep_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"DEP_OK:{dep_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"DEP_NO:{dep_id}"),
        ]
    ])


def kb_ticket_list(ticket_rows: List[sqlite3.Row], back_cb: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for t in ticket_rows[:40]:
        tid = int(t["id"])
        uid = int(t["user_id"])
        uname = get_user_display(uid) or f"User {uid}"
        rows.append([InlineKeyboardButton(f"🆘 {uname} (#{tid})", callback_data=f"TICKET:{tid}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)


def kb_ticket_actions(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Reply", callback_data=f"TICKET_REPLY:{ticket_id}")],
        [InlineKeyboardButton("✅ Close", callback_data=f"TICKET_CLOSE:{ticket_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="M_TICKETS")],
    ])


def kb_userlist_admin(shop_owner_id: int, user_rows: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    # Seller admin panel user list: no search for sellers (per your earlier lock),
    # but SUPER ADMIN does have search in SA menu (separate).
    rows: List[List[InlineKeyboardButton]] = []
    for r in user_rows[:50]:
        uid = int(r["user_id"])
        uname = get_user_display(uid) or f"User {uid}"
        rows.append([InlineKeyboardButton(f"👤 {uname}", callback_data=f"M_USER:{shop_owner_id}:{uid}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ADMIN_PANEL")])
    return InlineKeyboardMarkup(rows)


def kb_seller_user_actions(shop_owner_id: int, uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add +10", callback_data=f"M_BAL_ADD:{shop_owner_id}:{uid}:10")],
        [InlineKeyboardButton("➕ Add +50", callback_data=f"M_BAL_ADD:{shop_owner_id}:{uid}:50")],
        [InlineKeyboardButton("➖ Minus -10", callback_data=f"M_BAL_ADD:{shop_owner_id}:{uid}:-10")],
        [InlineKeyboardButton("➖ Minus -50", callback_data=f"M_BAL_ADD:{shop_owner_id}:{uid}:-50")],
        [InlineKeyboardButton("📜 History", callback_data=f"M_HIST:{shop_owner_id}:{uid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="M_USERS")],
    ])


# ---------------------------
# User display
# ---------------------------
def upsert_user(u) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users(user_id, username, first_name, last_name, last_seen) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name, last_seen=excluded.last_seen",
        (u.id, u.username or "", u.first_name or "", u.last_name or "", now_ts()),
    )
    conn.commit()
    conn.close()


def get_user_display(user_id: int) -> str:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT username, first_name, last_name FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    conn.close()
    if not r:
        return ""
    username = (r["username"] or "").strip()
    if username:
        return f"@{username}"
    name = " ".join([x for x in [(r["first_name"] or "").strip(), (r["last_name"] or "").strip()] if x]).strip()
    if name:
        return name
    return ""


# ---------------------------
# State machine (per user)
# ---------------------------
# We store small state in context.user_data:
# state = {
#   "mode": "...",
#   "draft": [ ... ],
#   "tmp": { ... }
# }
def set_mode(ctx: ContextTypes.DEFAULT_TYPE, mode: str, tmp: Optional[dict] = None) -> None:
    ctx.user_data["mode"] = mode
    ctx.user_data["draft"] = []
    ctx.user_data["tmp"] = tmp or {}


def clear_mode(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data.pop("mode", None)
    ctx.user_data.pop("draft", None)
    ctx.user_data.pop("tmp", None)


def get_mode(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    return str(ctx.user_data.get("mode") or "")


def add_draft(ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if "draft" not in ctx.user_data or not isinstance(ctx.user_data["draft"], list):
        ctx.user_data["draft"] = []
    ctx.user_data["draft"].append(text)


def get_draft(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    parts = ctx.user_data.get("draft") or []
    return "\n".join([p for p in parts if p]).strip()


def tmp(ctx: ContextTypes.DEFAULT_TYPE) -> dict:
    return ctx.user_data.get("tmp") or {}


# ---------------------------
# Messaging helpers
# ---------------------------
async def safe_delete_message(app: Application, chat_id: int, message_id: int) -> None:
    try:
        await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except BadRequest:
        pass
    except Exception:
        pass


async def safe_delete_q_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.callback_query and update.callback_query.message:
            await safe_delete_message(context.application, update.callback_query.message.chat_id, update.callback_query.message.message_id)
    except Exception:
        pass


async def send_shop_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int) -> None:
    settings = get_shop_settings(shop_owner_id)
    text = settings["welcome_text"] or ""
    file_id = (settings["welcome_file_id"] or "").strip()
    file_type = (settings["welcome_file_type"] or "").strip()

    kb = kb_main_menu_for(update.effective_user.id, shop_owner_id)

    if file_id and file_type == "photo":
        await update.effective_chat.send_photo(photo=file_id, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
    elif file_id and file_type == "video":
        await update.effective_chat.send_video(video=file_id, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_chat.send_message(text=text or "Welcome!", parse_mode=ParseMode.HTML, reply_markup=kb)


# ---------------------------
# START / ROUTING
# ---------------------------
def parse_start_param(param: str) -> Optional[int]:
    # /start s_<sellerid>
    if not param:
        return None
    m = re.match(r"^s_(\d+)$", param.strip())
    if m:
        return int(m.group(1))
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    upsert_user(u)

    # Determine entry shop
    entry_shop = SUPER_ADMIN_ID
    if context.args:
        sid = parse_start_param(context.args[0])
        if sid:
            # if seller shop is active
            if seller_shop_allowed(sid):
                entry_shop = sid
            else:
                entry_shop = SUPER_ADMIN_ID

    # Seller shop users must never see main shop:
    # If they enter via seller link -> lock them to that seller shop.
    set_current_shop(u.id, entry_shop)

    await send_shop_welcome(update, context, entry_shop)


# ---------------------------
# MAIN HANDLER
# ---------------------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    u = update.effective_user
    upsert_user(u)
    uid = u.id

    data = q.data or ""
    shop_owner_id = get_current_shop(uid)

    # Hard lock: seller shop users cannot switch to main shop
    if shop_owner_id != SUPER_ADMIN_ID and uid != shop_owner_id and not is_superadmin(uid):
        if data in {"GO_MAIN_SHOP"}:
            await q.answer("Not allowed.", show_alert=True)
            return

    # Cancel / Main menu must cancel last mode and delete last command message
    if data == "MAIN_MENU":
        clear_mode(context)
        await safe_delete_q_message(update, context)

        # lock seller-shop users inside seller shop
        current = get_current_shop(uid)
        if current != SUPER_ADMIN_ID and uid != current and not is_superadmin(uid):
            await send_shop_welcome(update, context, current)
            return

        # Sellers default to their own shop view; superadmin default to main shop
        if is_superadmin(uid):
            set_current_shop(uid, SUPER_ADMIN_ID)
            await send_shop_welcome(update, context, SUPER_ADMIN_ID)
            return

        # If seller, keep them in their own shop
        if is_active_seller(uid):
            set_current_shop(uid, uid)
            await send_shop_welcome(update, context, uid)
            return

        # Normal main user
        set_current_shop(uid, SUPER_ADMIN_ID)
        await send_shop_welcome(update, context, SUPER_ADMIN_ID)
        return

    # Super Admin main menu button (ONLY YOU)
    if data == "SA_MENU":
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("👑 <b>Super Admin</b>", parse_mode=ParseMode.HTML, reply_markup=kb_sa_main())
        return

    # Admin panel entry
    if data == "ADMIN_PANEL":
        # Must be shop owner (seller) or superadmin in main shop
        if is_superadmin(uid):
            set_current_shop(uid, SUPER_ADMIN_ID)
            await safe_delete_q_message(update, context)
            await update.effective_chat.send_message("🛠 <b>Admin Panel</b>", parse_mode=ParseMode.HTML, reply_markup=kb_admin_panel(uid, SUPER_ADMIN_ID))
            return

        if shop_owner_id == uid and seller_panel_allowed(uid):
            await safe_delete_q_message(update, context)
            await update.effective_chat.send_message("🛠 <b>Admin Panel</b>", parse_mode=ParseMode.HTML, reply_markup=kb_admin_panel(uid, uid))
            return

        await q.answer("Not allowed.", show_alert=True)
        return

    # Seller go main shop (SELLER ONLY, not seller users)
    if data == "GO_MAIN_SHOP":
        if is_superadmin(uid) or (shop_owner_id == uid and is_active_seller(uid)):
            set_current_shop(uid, SUPER_ADMIN_ID)
            await safe_delete_q_message(update, context)
            await send_shop_welcome(update, context, SUPER_ADMIN_ID)
            return
        await q.answer("Not allowed.", show_alert=True)
        return

    # USER buttons
    if data == "U_PRODUCTS":
        clear_mode(context)
        await safe_delete_q_message(update, context)

        # No shop picker: always current shop only
        current = get_current_shop(uid)
        # If seller shop is inactive, block seller shop users
        if current != SUPER_ADMIN_ID and current != uid and not is_superadmin(uid):
            if not seller_shop_allowed(current):
                await update.effective_chat.send_message("⛔ This seller shop is inactive.", reply_markup=kb_back_main())
                return

        cats = list_categories(current)
        if not cats:
            await update.effective_chat.send_message("No categories yet.", reply_markup=kb_back_main())
            return
        await update.effective_chat.send_message("📂 <b>Categories</b>", parse_mode=ParseMode.HTML, reply_markup=kb_categories(current, cats))
        return

    if data == "U_WALLET":
        clear_mode(context)
        await safe_delete_q_message(update, context)

        current = get_current_shop(uid)

        # ban check for main shop actions
        if is_banned_from_shop(current, uid):
            await update.effective_chat.send_message("⛔ You are banned from this shop.", reply_markup=kb_back_main())
            return

        bal = get_balance(current, uid)
        settings = get_shop_settings(current)
        wallet_addr = (settings["wallet_address"] or "").strip()
        wallet_msg = (settings["wallet_message"] or "").strip()

        lines = [
            f"💰 <b>Wallet</b>",
            f"Balance: <b>{fmt_money(bal)} {escape(CURRENCY)}</b>",
        ]
        if wallet_addr:
            lines.append(f"\n<b>Wallet Address:</b>\n<code>{escape(wallet_addr)}</code>")
        if wallet_msg:
            lines.append(f"\n<b>Note:</b>\n{escape(wallet_msg)}")

        await update.effective_chat.send_message("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb_deposit_start())
        return

    if data == "U_SUPPORT":
        clear_mode(context)
        await safe_delete_q_message(update, context)

        current = get_current_shop(uid)

        if is_banned_from_shop(current, uid):
            await update.effective_chat.send_message("⛔ You are banned from this shop.", reply_markup=kb_back_main())
            return

        set_mode(context, "SUPPORT_USER", {"shop_owner_id": current})
        await update.effective_chat.send_message(
            "🆘 <b>Support</b>\nSend your message(s) now.\nWhen ready, press ✅ Done.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_done_cancel("SUP"),
        )
        return

    # Become seller
    if data == "BECOME_SELLER":
        clear_mode(context)
        await safe_delete_q_message(update, context)

        # only main shop user and not seller and not superadmin
        if get_current_shop(uid) != SUPER_ADMIN_ID or is_active_seller(uid) or is_superadmin(uid):
            await q.answer("Not available.", show_alert=True)
            return
        desc = (get_shop_settings(SUPER_ADMIN_ID)["seller_desc"] or "").strip()
        await update.effective_chat.send_message(desc or "Become seller", parse_mode=ParseMode.HTML, reply_markup=kb_become_seller_pay())
        return

    if data == "SELLER_PAY":
        clear_mode(context)
        await safe_delete_q_message(update, context)

        if get_current_shop(uid) != SUPER_ADMIN_ID or is_active_seller(uid) or is_superadmin(uid):
            await q.answer("Not available.", show_alert=True)
            return

        if is_banned_from_shop(SUPER_ADMIN_ID, uid):
            await update.effective_chat.send_message("⛔ You are banned from this shop.", reply_markup=kb_back_main())
            return

        bal = get_balance(SUPER_ADMIN_ID, uid)
        if bal < SELLER_SUB_PRICE:
            await update.effective_chat.send_message(
                f"❌ Not enough balance.\nNeeded: {fmt_money(SELLER_SUB_PRICE)} {CURRENCY}\nYour balance: {fmt_money(bal)} {CURRENCY}",
                reply_markup=kb_back_main(),
            )
            return

        # Deduct
        new_bal = add_balance(SUPER_ADMIN_ID, uid, -SELLER_SUB_PRICE)
        log_tx(SUPER_ADMIN_ID, uid, "seller_sub", -SELLER_SUB_PRICE, f"Purchased seller subscription ({SELLER_SUB_DAYS} days)")
        # Create/extend seller
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM sellers WHERE seller_id=?", (uid,))
        row = cur.fetchone()
        now = now_ts()
        add_seconds = SELLER_SUB_DAYS * 24 * 3600

        if not row:
            cur.execute(
                "INSERT INTO sellers(seller_id, sub_until, banned, restricted_until, panel_banned, balance) VALUES(?,?,?,?,?,?)",
                (uid, now + add_seconds, 0, 0, 0, 0.0),
            )
        else:
            sub_until = int(row["sub_until"] or 0)
            new_until = (sub_until if sub_until > now else now) + add_seconds
            cur.execute("UPDATE sellers SET sub_until=? WHERE seller_id=?", (new_until, uid))
        conn.commit()
        conn.close()

        ensure_shop_settings(uid)

        # Switch seller to their own shop
        set_current_shop(uid, uid)

        await update.effective_chat.send_message(
            f"✅ You are now a <b>Seller</b>.\nSubscription updated.\n\nBalance: <b>{fmt_money(new_bal)} {CURRENCY}</b>",
            parse_mode=ParseMode.HTML,
        )
        await send_shop_welcome(update, context, uid)
        return

    # Deposit start indicated amount
    if data == "DEP_START":
        clear_mode(context)
        current = get_current_shop(uid)
        if is_banned_from_shop(current, uid):
            await q.answer("You are banned.", show_alert=True)
            return
        set_mode(context, "DEP_AMOUNT", {"shop_owner_id": current})
        await update.effective_chat.send_message(
            "➕ <b>Deposit</b>\nSend the deposit amount (example: 10).\nThen you must send a <b>photo proof</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="DEP_CANCEL")]]),
        )
        return

    if data == "DEP_CANCEL":
        clear_mode(context)
        await safe_delete_q_message(update, context)
        await send_shop_welcome(update, context, get_current_shop(uid))
        return

    # Category navigation
    if data.startswith("CAT:"):
        clear_mode(context)
        _, so, cid = data.split(":")
        so_id = int(so); cat_id = int(cid)
        # enforce current shop only (no cross shop)
        current = get_current_shop(uid)
        if so_id != current:
            await q.answer("Not available.", show_alert=True)
            return
        cocats = list_cocategories(so_id, cat_id)
        if not cocats:
            await update.effective_chat.send_message("No co-categories yet.", reply_markup=kb_back_main())
            return
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("📁 <b>Co-Categories</b>", parse_mode=ParseMode.HTML, reply_markup=kb_cocategories(so_id, cat_id, cocats))
        return

    if data.startswith("COCAT:"):
        clear_mode(context)
        _, so, cat, coc = data.split(":")
        so_id = int(so); cat_id = int(cat); coc_id = int(coc)
        current = get_current_shop(uid)
        if so_id != current:
            await q.answer("Not available.", show_alert=True)
            return
        prods = list_products(so_id, cat_id, coc_id)
        if not prods:
            await update.effective_chat.send_message("No products yet.", reply_markup=kb_back_main())
            return
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("🛒 <b>Products</b>", parse_mode=ParseMode.HTML, reply_markup=kb_products(so_id, cat_id, coc_id, prods))
        return

    if data.startswith("PROD:"):
        clear_mode(context)
        _, so, pid = data.split(":")
        so_id = int(so); p_id = int(pid)
        current = get_current_shop(uid)
        if so_id != current:
            await q.answer("Not available.", show_alert=True)
            return
        p = get_product(so_id, p_id)
        if not p:
            await q.answer("Not found.", show_alert=True)
            return
        price = fmt_money(float(p["price"]))
        desc = (p["description"] or "").strip()
        text = f"🛒 <b>{escape(p['name'])}</b>\nPrice: <b>{price} {escape(CURRENCY)}</b>"
        if desc:
            text += f"\n\n{escape(desc)}"
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=kb_product_view(so_id, p_id))
        return

    if data.startswith("PROD_BACK:"):
        clear_mode(context)
        # just go products list
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("🛒 Products", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="U_PRODUCTS")]]))
        return

    if data.startswith("BUY:"):
        clear_mode(context)
        _, so, pid = data.split(":")
        so_id = int(so); p_id = int(pid)
        current = get_current_shop(uid)
        if so_id != current:
            await q.answer("Not available.", show_alert=True)
            return

        if is_banned_from_shop(so_id, uid):
            await update.effective_chat.send_message("⛔ You are banned from this shop.", reply_markup=kb_back_main())
            return

        p = get_product(so_id, p_id)
        if not p:
            await q.answer("Not found.", show_alert=True)
            return
        price = float(p["price"])
        bal = get_balance(so_id, uid)
        if bal < price:
            await update.effective_chat.send_message(
                f"❌ Not enough balance.\nNeeded: {fmt_money(price)} {CURRENCY}\nYour balance: {fmt_money(bal)} {CURRENCY}",
                reply_markup=kb_back_main(),
            )
            return

        new_bal = add_balance(so_id, uid, -price)
        log_tx(so_id, uid, "purchase", -price, f"Purchased: {p['name']}")

        # Notify shop owner (main -> superadmin; seller shop -> seller only)
        buyer = get_user_display(uid) or f"{uid}"
        note = (
            f"🛒 <b>New Purchase</b>\n"
            f"Shop: <b>{'Main Shop' if so_id == SUPER_ADMIN_ID else 'Seller Shop'}</b>\n"
            f"Buyer: <b>{escape(buyer)}</b>\n"
            f"Product: <b>{escape(p['name'])}</b>\n"
            f"Paid: <b>{fmt_money(price)} {escape(CURRENCY)}</b>\n"
        )
        try:
            await context.application.bot.send_message(chat_id=so_id, text=note, parse_mode=ParseMode.HTML)
        except Exception:
            pass

        # Deliver
        key_text = (p["key_text"] or "").strip()
        tg_link = (p["tg_link"] or "").strip()
        await safe_delete_q_message(update, context)

        msg = (
            f"✅ <b>Purchase Successful</b>\n\n"
            f"Purchased: <b>{escape(p['name'])}</b>\n"
            f"Paid: <b>{fmt_money(price)} {escape(CURRENCY)}</b>\n"
            f"Total Balance: <b>{fmt_money(new_bal)} {escape(CURRENCY)}</b>\n"
        )
        if key_text:
            msg += f"\n🔑 <b>Key:</b>\n<code>{escape(key_text)}</code>\n"
        await update.effective_chat.send_message(msg, parse_mode=ParseMode.HTML)

        if tg_link:
            await update.effective_chat.send_message("📁 Delivery:", reply_markup=kb_get_file(tg_link))
        return

    # Support Draft DONE/CANCEL
    if data in {"SUP_DONE", "SUP_CANCEL"}:
        mode = get_mode(context)
        if mode != "SUPPORT_USER":
            await q.answer("No active message.", show_alert=True)
            return
        if data == "SUP_CANCEL":
            clear_mode(context)
            await safe_delete_q_message(update, context)
            await send_shop_welcome(update, context, get_current_shop(uid))
            return

        # DONE: create ticket
        text = get_draft(context)
        if not text:
            await q.answer("Send a message first.", show_alert=True)
            return
        info = tmp(context)
        shop_owner = int(info.get("shop_owner_id", get_current_shop(uid)))

        # Create/open ticket
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM tickets WHERE shop_owner_id=? AND user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (shop_owner, uid),
        )
        row = cur.fetchone()
        if row:
            ticket_id = int(row["id"])
        else:
            cur.execute(
                "INSERT INTO tickets(shop_owner_id, user_id, status, created_at, updated_at) VALUES(?,?,?,?,?)",
                (shop_owner, uid, "open", now_ts(), now_ts()),
            )
            ticket_id = cur.lastrowid

        cur.execute(
            "INSERT INTO ticket_messages(ticket_id, sender_id, text, created_at) VALUES(?,?,?,?)",
            (ticket_id, uid, text, now_ts()),
        )
        cur.execute("UPDATE tickets SET updated_at=? WHERE id=?", (now_ts(), ticket_id))
        conn.commit()
        conn.close()

        # Notify owner (main -> superadmin; seller -> seller)
        try:
            await context.application.bot.send_message(
                chat_id=shop_owner,
                text=f"🆘 <b>New Support Ticket</b>\nFrom: <b>{escape(get_user_display(uid) or str(uid))}</b>\nTicket: <b>#{ticket_id}</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        clear_mode(context)
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("✅ Sent to support.", reply_markup=kb_back_main())
        return

    # ---------------------------
    # ADMIN PANEL callbacks
    # ---------------------------
    if data == "M_SHOP":
        # must be allowed to use admin panel (seller in own shop) or superadmin in main shop
        current = get_current_shop(uid)
        if is_superadmin(uid):
            current = SUPER_ADMIN_ID
        if not (is_superadmin(uid) or (current == uid and seller_panel_allowed(uid))):
            await q.answer("Not allowed.", show_alert=True)
            return
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("🛒 <b>Manage Shop</b>", parse_mode=ParseMode.HTML, reply_markup=kb_shop_manage())
        return

    if data == "M_DEPOSITS":
        current = get_current_shop(uid)
        if is_superadmin(uid):
            current = SUPER_ADMIN_ID
        # seller only in own shop; superadmin in main shop only
        if not (is_superadmin(uid) or (current == uid and seller_panel_allowed(uid))):
            await q.answer("Not allowed.", show_alert=True)
            return
        await safe_delete_q_message(update, context)

        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM deposit_requests WHERE shop_owner_id=? AND status='pending' ORDER BY id DESC LIMIT 20",
            (current,),
        )
        deps = cur.fetchall()
        conn.close()

        if not deps:
            await update.effective_chat.send_message("No pending deposits.", reply_markup=kb_back_main())
            return

        for d in deps:
            dep_id = int(d["id"])
            duid = int(d["user_id"])
            amount = float(d["amount"])
            uname = get_user_display(duid) or str(duid)
            text = (
                f"💳 <b>Deposit Request</b>\n"
                f"User: <b>{escape(uname)}</b>\n"
                f"Amount: <b>{fmt_money(amount)} {escape(CURRENCY)}</b>\n"
                f"Request ID: <b>#{dep_id}</b>\n"
            )
            # show proof image
            proof_id = d["proof_file_id"]
            try:
                await update.effective_chat.send_photo(photo=proof_id, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb_approve_reject_deposit(dep_id))
            except Exception:
                await update.effective_chat.send_message(text + "\n(Proof unavailable)", parse_mode=ParseMode.HTML, reply_markup=kb_approve_reject_deposit(dep_id))
        return

    if data.startswith("DEP_OK:") or data.startswith("DEP_NO:"):
        # Approve / reject; and DELETE the message after
        dep_id = int(data.split(":")[1])
        action = "approved" if data.startswith("DEP_OK:") else "rejected"

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM deposit_requests WHERE id=?", (dep_id,))
        dep = cur.fetchone()
        if not dep:
            conn.close()
            await q.answer("Not found.", show_alert=True)
            return

        shop_owner = int(dep["shop_owner_id"])
        dep_user = int(dep["user_id"])
        amount = float(dep["amount"])
        status = dep["status"]

        # Permission:
        # - main shop deposits -> only superadmin
        # - seller shop deposits -> only that seller (shop owner)
        if shop_owner == SUPER_ADMIN_ID:
            if not is_superadmin(uid):
                conn.close()
                await q.answer("Not allowed.", show_alert=True)
                return
        else:
            if uid != shop_owner:
                conn.close()
                await q.answer("Not allowed.", show_alert=True)
                return

        if status != "pending":
            conn.close()
            await q.answer("Already handled.", show_alert=True)
            # delete the old message anyway
            await safe_delete_q_message(update, context)
            return

        cur.execute(
            "UPDATE deposit_requests SET status=?, handled_by=?, handled_at=? WHERE id=?",
            (action, uid, now_ts(), dep_id),
        )
        conn.commit()
        conn.close()

        if action == "approved":
            new_bal = add_balance(shop_owner, dep_user, amount)
            log_tx(shop_owner, dep_user, "deposit_approved", amount, "Deposit approved")
            # notify user
            try:
                await context.application.bot.send_message(
                    chat_id=dep_user,
                    text=(
                        f"✅ <b>Deposit Approved</b>\n"
                        f"Deposited: <b>{fmt_money(amount)} {escape(CURRENCY)}</b>\n"
                        f"Total Balance: <b>{fmt_money(new_bal)} {escape(CURRENCY)}</b>"
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        else:
            try:
                await context.application.bot.send_message(
                    chat_id=dep_user,
                    text=(
                        f"❌ <b>Deposit Rejected</b>\n"
                        f"Amount: <b>{fmt_money(amount)} {escape(CURRENCY)}</b>"
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        # delete deposit request message from approver chat
        await safe_delete_q_message(update, context)
        return

    if data == "M_TICKETS":
        current = get_current_shop(uid)
        if is_superadmin(uid):
            current = SUPER_ADMIN_ID
        # seller only for own shop; superadmin for main shop + their own seller shop tickets appear when they are in that shop
        if not (is_superadmin(uid) or (current == uid and seller_panel_allowed(uid))):
            await q.answer("Not allowed.", show_alert=True)
            return

        await safe_delete_q_message(update, context)

        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tickets WHERE shop_owner_id=? AND status='open' ORDER BY updated_at DESC LIMIT 40",
            (current,),
        )
        tickets = cur.fetchall()
        conn.close()

        if not tickets:
            await update.effective_chat.send_message("No open support tickets.", reply_markup=kb_back_main())
            return

        await update.effective_chat.send_message("🆘 <b>Support Inbox</b>", parse_mode=ParseMode.HTML, reply_markup=kb_ticket_list(tickets, "ADMIN_PANEL"))
        return

    if data.startswith("TICKET:"):
        ticket_id = int(data.split(":")[1])

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,))
        t = cur.fetchone()
        if not t:
            conn.close()
            await q.answer("Not found.", show_alert=True)
            return

        shop_owner = int(t["shop_owner_id"])
        ticket_user = int(t["user_id"])

        # only shop owner can read
        if shop_owner == SUPER_ADMIN_ID:
            if not is_superadmin(uid):
                conn.close()
                await q.answer("Not allowed.", show_alert=True)
                return
        else:
            if uid != shop_owner:
                conn.close()
                await q.answer("Not allowed.", show_alert=True)
                return

        cur.execute(
            "SELECT sender_id, text, created_at FROM ticket_messages WHERE ticket_id=? ORDER BY id DESC LIMIT 6",
            (ticket_id,),
        )
        msgs = cur.fetchall()
        conn.close()

        uname = get_user_display(ticket_user) or str(ticket_user)
        lines = [f"🆘 <b>Ticket #{ticket_id}</b>\nUser: <b>{escape(uname)}</b>\n"]
        for m in reversed(msgs):
            sid = int(m["sender_id"])
            who = "User" if sid == ticket_user else "Support"
            lines.append(f"<b>{who}:</b> {escape(m['text'])}")

        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("\n\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb_ticket_actions(ticket_id))
        return

    if data.startswith("TICKET_REPLY:"):
        ticket_id = int(data.split(":")[1])

        # Permission check + set mode to draft reply
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,))
        t = cur.fetchone()
        conn.close()
        if not t:
            await q.answer("Not found.", show_alert=True)
            return
        shop_owner = int(t["shop_owner_id"])

        if shop_owner == SUPER_ADMIN_ID:
            if not is_superadmin(uid):
                await q.answer("Not allowed.", show_alert=True)
                return
        else:
            if uid != shop_owner:
                await q.answer("Not allowed.", show_alert=True)
                return

        clear_mode(context)
        set_mode(context, "SUPPORT_REPLY", {"ticket_id": ticket_id})
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(
            "✍️ <b>Reply</b>\nSend your message(s) now, then press ✅ Done.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_done_cancel("REPLY"),
        )
        return

    if data in {"REPLY_DONE", "REPLY_CANCEL"}:
        mode = get_mode(context)
        if mode != "SUPPORT_REPLY":
            await q.answer("No active reply.", show_alert=True)
            return
        if data == "REPLY_CANCEL":
            clear_mode(context)
            await safe_delete_q_message(update, context)
            await update.effective_chat.send_message("Canceled.", reply_markup=kb_back_main())
            return

        text = get_draft(context)
        if not text:
            await q.answer("Send a message first.", show_alert=True)
            return

        info = tmp(context)
        ticket_id = int(info.get("ticket_id", 0))
        if ticket_id <= 0:
            clear_mode(context)
            await q.answer("Ticket missing.", show_alert=True)
            return

        # get ticket
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,))
        t = cur.fetchone()
        if not t:
            conn.close()
            clear_mode(context)
            await q.answer("Ticket not found.", show_alert=True)
            return

        shop_owner = int(t["shop_owner_id"])
        ticket_user = int(t["user_id"])

        # permission
        if shop_owner == SUPER_ADMIN_ID:
            if not is_superadmin(uid):
                conn.close()
                clear_mode(context)
                await q.answer("Not allowed.", show_alert=True)
                return
        else:
            if uid != shop_owner:
                conn.close()
                clear_mode(context)
                await q.answer("Not allowed.", show_alert=True)
                return

        cur.execute(
            "INSERT INTO ticket_messages(ticket_id, sender_id, text, created_at) VALUES(?,?,?,?)",
            (ticket_id, uid, text, now_ts()),
        )
        cur.execute("UPDATE tickets SET updated_at=? WHERE id=?", (now_ts(), ticket_id))
        conn.commit()
        conn.close()

        # send to user
        try:
            await context.application.bot.send_message(
                chat_id=ticket_user,
                text=f"🆘 <b>Support Reply</b>\n\n{escape(text)}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        clear_mode(context)
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("✅ Reply sent.", reply_markup=kb_back_main())
        return

    if data.startswith("TICKET_CLOSE:"):
        ticket_id = int(data.split(":")[1])

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,))
        t = cur.fetchone()
        if not t:
            conn.close()
            await q.answer("Not found.", show_alert=True)
            return
        shop_owner = int(t["shop_owner_id"])
        ticket_user = int(t["user_id"])

        if shop_owner == SUPER_ADMIN_ID:
            if not is_superadmin(uid):
                conn.close()
                await q.answer("Not allowed.", show_alert=True)
                return
        else:
            if uid != shop_owner:
                conn.close()
                await q.answer("Not allowed.", show_alert=True)
                return

        cur.execute("UPDATE tickets SET status='closed', updated_at=? WHERE id=?", (now_ts(), ticket_id))
        conn.commit()
        conn.close()

        # delete the ticket view message after close (clean inbox UX)
        await safe_delete_q_message(update, context)
        try:
            await context.application.bot.send_message(chat_id=ticket_user, text="✅ Your support ticket has been closed.")
        except Exception:
            pass
        return

    # ---------------------------
    # Admin Panel: Users list & actions (Seller: own shop users; Superadmin: main shop users only)
    # ---------------------------
    if data == "M_USERS":
        current = get_current_shop(uid)
        if is_superadmin(uid):
            current = SUPER_ADMIN_ID
        if not (is_superadmin(uid) or (current == uid and seller_panel_allowed(uid))):
            await q.answer("Not allowed.", show_alert=True)
            return

        await safe_delete_q_message(update, context)

        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id FROM balances WHERE shop_owner_id=? ORDER BY user_id DESC LIMIT 50",
            (current,),
        )
        rows_u = cur.fetchall()
        conn.close()

        if not rows_u:
            await update.effective_chat.send_message("No users yet.", reply_markup=kb_back_main())
            return

        await update.effective_chat.send_message("👥 <b>Users</b>", parse_mode=ParseMode.HTML, reply_markup=kb_userlist_admin(current, rows_u))
        return

    if data.startswith("M_USER:"):
        _, so, target = data.split(":")
        so_id = int(so); target_id = int(target)

        current = get_current_shop(uid)
        if is_superadmin(uid):
            current = SUPER_ADMIN_ID

        if so_id != current:
            await q.answer("Not available.", show_alert=True)
            return

        if not (is_superadmin(uid) or (current == uid and seller_panel_allowed(uid))):
            await q.answer("Not allowed.", show_alert=True)
            return

        bal = get_balance(so_id, target_id)
        uname = get_user_display(target_id) or str(target_id)
        text = (
            f"👤 <b>{escape(uname)}</b>\n"
            f"Telegram ID: <code>{target_id}</code>\n"
            f"Balance: <b>{fmt_money(bal)} {escape(CURRENCY)}</b>"
        )
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=kb_seller_user_actions(so_id, target_id))
        return

    if data.startswith("M_BAL_ADD:"):
        _, so, target, delta = data.split(":")
        so_id = int(so); target_id = int(target); delta_f = float(delta)

        current = get_current_shop(uid)
        if is_superadmin(uid):
            current = SUPER_ADMIN_ID

        if so_id != current:
            await q.answer("Not available.", show_alert=True)
            return
        if not (is_superadmin(uid) or (current == uid and seller_panel_allowed(uid))):
            await q.answer("Not allowed.", show_alert=True)
            return

        newb = add_balance(so_id, target_id, delta_f)
        log_tx(so_id, target_id, "balance_adjust", delta_f, f"Adjusted by {uid}")

        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(
            f"✅ Updated.\nNew balance: {fmt_money(newb)} {CURRENCY}",
            reply_markup=kb_back_main(),
        )
        return

    if data.startswith("M_HIST:"):
        _, so, target = data.split(":")
        so_id = int(so); target_id = int(target)

        current = get_current_shop(uid)
        if is_superadmin(uid):
            current = SUPER_ADMIN_ID

        if so_id != current:
            await q.answer("Not available.", show_alert=True)
            return
        if not (is_superadmin(uid) or (current == uid and seller_panel_allowed(uid))):
            await q.answer("Not allowed.", show_alert=True)
            return

        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM transactions WHERE shop_owner_id=? AND user_id=? ORDER BY id DESC LIMIT 15",
            (so_id, target_id),
        )
        txs = cur.fetchall()
        conn.close()

        bal = get_balance(so_id, target_id)
        uname = get_user_display(target_id) or str(target_id)

        lines = [f"📜 <b>History</b> — <b>{escape(uname)}</b>\nTotal Balance: <b>{fmt_money(bal)} {escape(CURRENCY)}</b>\n"]
        if not txs:
            lines.append("No history.")
        else:
            for t in txs:
                kind = t["kind"]
                amt = float(t["amount"])
                note = (t["note"] or "").strip()
                if kind == "deposit_approved":
                    lines.append(f"Deposited: <b>{fmt_money(amt)} {escape(CURRENCY)}</b>")
                elif kind == "purchase":
                    lines.append(f"Purchased: <b>{escape(note.replace('Purchased: ', ''))}</b>\nPaid: <b>{fmt_money(abs(amt))} {escape(CURRENCY)}</b>")
                elif kind == "balance_adjust":
                    sign = "+" if amt >= 0 else ""
                    lines.append(f"Balance Adjusted: <b>{sign}{fmt_money(amt)} {escape(CURRENCY)}</b>")
                elif kind == "seller_sub":
                    lines.append(f"Seller Subscription: <b>{fmt_money(amt)} {escape(CURRENCY)}</b>")
                else:
                    lines.append(f"{escape(kind)}: <b>{fmt_money(amt)} {escape(CURRENCY)}</b>")

                # add blank line
                lines.append("")

        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("\n".join(lines).strip(), parse_mode=ParseMode.HTML, reply_markup=kb_back_main())
        return

    # ---------------------------
    # Admin Panel: Wallet / Welcome editing (Seller or Superadmin)
    # ---------------------------
    if data == "M_SET_WALLET":
        current = get_current_shop(uid)
        if is_superadmin(uid):
            current = SUPER_ADMIN_ID
        if not (is_superadmin(uid) or (current == uid and seller_panel_allowed(uid))):
            await q.answer("Not allowed.", show_alert=True)
            return

        clear_mode(context)
        set_mode(context, "EDIT_WALLET_ADDR", {"shop_owner_id": current})
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(
            "💳 <b>Set Wallet Address</b>\nSend the wallet address text (any format).",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="EDIT_CANCEL")]]),
        )
        return

    if data == "M_EDIT_WALLETMSG":
        current = get_current_shop(uid)
        if is_superadmin(uid):
            current = SUPER_ADMIN_ID
        if not (is_superadmin(uid) or (current == uid and seller_panel_allowed(uid))):
            await q.answer("Not allowed.", show_alert=True)
            return

        clear_mode(context)
        set_mode(context, "EDIT_WALLET_MSG", {"shop_owner_id": current})
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(
            "📝 <b>Edit Wallet Message</b>\nSend the new wallet message text.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="EDIT_CANCEL")]]),
        )
        return

    if data == "M_EDIT_WELCOME":
        current = get_current_shop(uid)
        if is_superadmin(uid):
            # Super admin edits main shop welcome while in admin panel
            current = SUPER_ADMIN_ID
        if not (is_superadmin(uid) or (current == uid and seller_panel_allowed(uid))):
            await q.answer("Not allowed.", show_alert=True)
            return

        clear_mode(context)
        set_mode(context, "EDIT_WELCOME", {"shop_owner_id": current})
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(
            "🖼 <b>Edit Welcome Message</b>\nSend:\n1) Text message (welcome text)\nOR\n2) Photo/Video with caption (caption becomes welcome text).\n\nThis supports photo/video.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="EDIT_CANCEL")]]),
        )
        return

    if data == "M_SHARE":
        # seller only (own shop)
        current = get_current_shop(uid)
        if current != uid or not is_active_seller(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        link = f"https://t.me/{context.application.bot.username}?start=s_{uid}"
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(
            f"📣 <b>Share My Shop</b>\n\nSend this link to customers:\n{escape(link)}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_back_main(),
        )
        return

    if data == "EDIT_CANCEL":
        clear_mode(context)
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("Canceled.", reply_markup=kb_back_main())
        return

    # ---------------------------
    # Super Admin menus & search
    # ---------------------------
    if data == "SA_SELLERS":
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        await safe_delete_q_message(update, context)

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT seller_id FROM sellers ORDER BY seller_id DESC LIMIT 40")
        rs = cur.fetchall()
        conn.close()

        if not rs:
            await update.effective_chat.send_message("No sellers yet.", reply_markup=kb_sa_main())
            return
        await update.effective_chat.send_message("🏪 <b>Sellers</b>", parse_mode=ParseMode.HTML, reply_markup=kb_sa_sellers_list(rs))
        return

    if data == "SA_USERS":
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        await safe_delete_q_message(update, context)

        # Main shop users only: anyone with balance record in main shop
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM balances WHERE shop_owner_id=? ORDER BY user_id DESC LIMIT 50", (SUPER_ADMIN_ID,))
        rs = cur.fetchall()
        conn.close()

        if not rs:
            await update.effective_chat.send_message("No users yet.", reply_markup=kb_sa_main())
            return
        await update.effective_chat.send_message("👥 <b>Main Shop Users</b>", parse_mode=ParseMode.HTML, reply_markup=kb_sa_users_list(rs))
        return

    if data == "SA_SELLER_SEARCH":
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        clear_mode(context)
        set_mode(context, "SA_SEARCH_SELLER", {})
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("🔎 Send seller username (example: @name or name).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="EDIT_CANCEL")]]))
        return

    if data == "SA_USER_SEARCH":
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        clear_mode(context)
        set_mode(context, "SA_SEARCH_USER", {})
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("🔎 Send user username (example: @name or name).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="EDIT_CANCEL")]]))
        return

    if data.startswith("SA_SELLER:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        seller_id = int(data.split(":")[1])
        await safe_delete_q_message(update, context)

        row = get_seller_row(seller_id)
        if not row:
            await update.effective_chat.send_message("Seller not found.", reply_markup=kb_sa_main())
            return

        uname = get_user_display(seller_id) or str(seller_id)
        sub_until = int(row["sub_until"] or 0)
        banned = int(row["banned"] or 0)
        restricted_until = int(row["restricted_until"] or 0)
        panel_banned = int(row["panel_banned"] or 0)
        bal = float(row["balance"] or 0)

        def left(ts: int) -> str:
            if ts <= now_ts():
                return "0"
            return str((ts - now_ts()) // 86400)

        text = (
            f"🏪 <b>Seller</b> {escape(uname)}\n"
            f"Seller ID: <code>{seller_id}</code>\n"
            f"Seller Balance: <b>{fmt_money(bal)} {escape(CURRENCY)}</b>\n"
            f"Days Left: <b>{left(sub_until)}</b>\n"
            f"Restricted Days Left: <b>{left(restricted_until)}</b>\n"
            f"Banned Shop: <b>{'YES' if banned else 'NO'}</b>\n"
            f"Banned Panel: <b>{'YES' if panel_banned else 'NO'}</b>\n"
        )
        await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=kb_sa_seller_actions(seller_id))
        return

    if data.startswith("SA_USER:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(data.split(":")[1])

        # Must be main shop user only (privacy lock)
        # main shop user = has balances row under SUPER_ADMIN_ID
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM balances WHERE shop_owner_id=? AND user_id=? LIMIT 1", (SUPER_ADMIN_ID, target))
        ok = cur.fetchone() is not None
        conn.close()
        if not ok:
            await q.answer("Not a main shop user.", show_alert=True)
            return

        await safe_delete_q_message(update, context)
        bal = get_balance(SUPER_ADMIN_ID, target)
        uname = get_user_display(target) or str(target)
        text = (
            f"👤 <b>{escape(uname)}</b>\n"
            f"Telegram ID: <code>{target}</code>\n"
            f"Balance: <b>{fmt_money(bal)} {escape(CURRENCY)}</b>"
        )
        await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=kb_sa_user_actions(target))
        return

    # Super admin: ban/unban user (main shop only)
    if data.startswith("SA_BAN_USER:") or data.startswith("SA_UNBAN_USER:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(data.split(":")[1])

        conn = db()
        cur = conn.cursor()
        if data.startswith("SA_BAN_USER:"):
            cur.execute(
                "INSERT INTO user_bans(shop_owner_id, user_id, banned) VALUES(?,?,1) "
                "ON CONFLICT(shop_owner_id, user_id) DO UPDATE SET banned=1",
                (SUPER_ADMIN_ID, target),
            )
            msg = "✅ User banned from main shop."
        else:
            cur.execute(
                "INSERT INTO user_bans(shop_owner_id, user_id, banned) VALUES(?,?,0) "
                "ON CONFLICT(shop_owner_id, user_id) DO UPDATE SET banned=0",
                (SUPER_ADMIN_ID, target),
            )
            msg = "✅ User unbanned from main shop."
        conn.commit()
        conn.close()

        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(msg, reply_markup=kb_back_main())
        return

    # Super admin: edit user balance (main shop only) -> buttons
    if data.startswith("SA_EDIT_BAL:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(data.split(":")[1])

        # must be main shop user
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM balances WHERE shop_owner_id=? AND user_id=? LIMIT 1", (SUPER_ADMIN_ID, target))
        ok = cur.fetchone() is not None
        conn.close()
        if not ok:
            await q.answer("Not a main shop user.", show_alert=True)
            return

        await safe_delete_q_message(update, context)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add +10", callback_data=f"SA_BAL_ADD:{target}:10"),
             InlineKeyboardButton("➕ Add +50", callback_data=f"SA_BAL_ADD:{target}:50")],
            [InlineKeyboardButton("➖ Minus -10", callback_data=f"SA_BAL_ADD:{target}:-10"),
             InlineKeyboardButton("➖ Minus -50", callback_data=f"SA_BAL_ADD:{target}:-50")],
            [InlineKeyboardButton("⬅️ Back", callback_data="SA_USERS")]
        ])
        await update.effective_chat.send_message("💰 Edit Balance (Main Shop)", reply_markup=kb)
        return

    if data.startswith("SA_BAL_ADD:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        _, target, delta = data.split(":")
        target_id = int(target); delta_f = float(delta)

        # must be main shop user
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM balances WHERE shop_owner_id=? AND user_id=? LIMIT 1", (SUPER_ADMIN_ID, target_id))
        ok = cur.fetchone() is not None
        conn.close()
        if not ok:
            await q.answer("Not a main shop user.", show_alert=True)
            return

        nb = add_balance(SUPER_ADMIN_ID, target_id, delta_f)
        log_tx(SUPER_ADMIN_ID, target_id, "balance_adjust", delta_f, "Adjusted by Super Admin")

        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(f"✅ Updated. New balance: {fmt_money(nb)} {CURRENCY}", reply_markup=kb_back_main())
        return

    if data.startswith("SA_HIST:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(data.split(":")[1])

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM balances WHERE shop_owner_id=? AND user_id=? LIMIT 1", (SUPER_ADMIN_ID, target))
        ok = cur.fetchone() is not None
        conn.close()
        if not ok:
            await q.answer("Not a main shop user.", show_alert=True)
            return

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM transactions WHERE shop_owner_id=? AND user_id=? ORDER BY id DESC LIMIT 15", (SUPER_ADMIN_ID, target))
        txs = cur.fetchall()
        conn.close()

        bal = get_balance(SUPER_ADMIN_ID, target)
        uname = get_user_display(target) or str(target)

        lines = [f"📜 <b>History</b> — <b>{escape(uname)}</b>\nTotal Balance: <b>{fmt_money(bal)} {escape(CURRENCY)}</b>\n"]
        if not txs:
            lines.append("No history.")
        else:
            for t in txs:
                kind = t["kind"]
                amt = float(t["amount"])
                note = (t["note"] or "").strip()
                if kind == "deposit_approved":
                    lines.append(f"Deposited: <b>{fmt_money(amt)} {escape(CURRENCY)}</b>")
                elif kind == "purchase":
                    lines.append(f"Purchased: <b>{escape(note.replace('Purchased: ', ''))}</b>\nPaid: <b>{fmt_money(abs(amt))} {escape(CURRENCY)}</b>")
                else:
                    sign = "+" if amt >= 0 else ""
                    lines.append(f"{escape(kind)}: <b>{sign}{fmt_money(amt)} {escape(CURRENCY)}</b>")
                lines.append("")
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("\n".join(lines).strip(), parse_mode=ParseMode.HTML, reply_markup=kb_back_main())
        return

    # Super admin: seller actions
    if data.startswith("SA_ADD_DAYS:") or data.startswith("SA_RESTRICT:") or data.startswith("SA_BAN_SHOP:") or data.startswith("SA_UNBAN_SHOP:") or data.startswith("SA_BAN_PANEL:") or data.startswith("SA_UNBAN_PANEL:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        parts = data.split(":")
        cmd = parts[0]
        seller_id = int(parts[1])

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM sellers WHERE seller_id=?", (seller_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            await q.answer("Seller not found.", show_alert=True)
            return

        if cmd == "SA_ADD_DAYS":
            days = int(parts[2])
            now = now_ts()
            add_secs = days * 86400
            sub_until = int(row["sub_until"] or 0)
            new_until = (sub_until if sub_until > now else now) + add_secs
            cur.execute("UPDATE sellers SET sub_until=? WHERE seller_id=?", (new_until, seller_id))
            msg = f"✅ Added {days} days."
        elif cmd == "SA_RESTRICT":
            days = int(parts[2])
            new_until = now_ts() + days * 86400
            cur.execute("UPDATE sellers SET restricted_until=? WHERE seller_id=?", (new_until, seller_id))
            msg = f"✅ Restricted for {days} days."
        elif cmd == "SA_BAN_SHOP":
            cur.execute("UPDATE sellers SET banned=1 WHERE seller_id=?", (seller_id,))
            msg = "✅ Seller shop banned."
        elif cmd == "SA_UNBAN_SHOP":
            cur.execute("UPDATE sellers SET banned=0 WHERE seller_id=?", (seller_id,))
            msg = "✅ Seller shop unbanned."
        elif cmd == "SA_BAN_PANEL":
            cur.execute("UPDATE sellers SET panel_banned=1 WHERE seller_id=?", (seller_id,))
            msg = "✅ Seller panel banned."
        else:
            cur.execute("UPDATE sellers SET panel_banned=0 WHERE seller_id=?", (seller_id,))
            msg = "✅ Seller panel unbanned."

        conn.commit()
        conn.close()

        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(msg, reply_markup=kb_back_main())
        return

    if data.startswith("SA_SELLER_BAL:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        seller_id = int(data.split(":")[1])
        clear_mode(context)
        set_mode(context, "SA_EDIT_SELLER_BAL", {"seller_id": seller_id})
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message("💰 Send new seller balance amount (number).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="EDIT_CANCEL")]]))
        return

    if data == "SA_EDIT_SELLER_DESC":
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        clear_mode(context)
        set_mode(context, "SA_EDIT_SELLER_DESC", {})
        await safe_delete_q_message(update, context)
        await update.effective_chat.send_message(
            "📝 Send the new <b>Become Seller</b> description text.\n(HTML is supported.)",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="EDIT_CANCEL")]]),
        )
        return

    if data.startswith("SA_USER_TICKETS:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(data.split(":")[1])
        # Only main shop user
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM balances WHERE shop_owner_id=? AND user_id=? LIMIT 1", (SUPER_ADMIN_ID, target))
        ok = cur.fetchone() is not None
        conn.close()
        if not ok:
            await q.answer("Not a main shop user.", show_alert=True)
            return

        # Show open ticket for that user in main shop
        conn = db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tickets WHERE shop_owner_id=? AND user_id=? AND status='open' ORDER BY updated_at DESC LIMIT 1",
            (SUPER_ADMIN_ID, target),
        )
        t = cur.fetchone()
        conn.close()

        if not t:
            await safe_delete_q_message(update, context)
            await update.effective_chat.send_message("No open tickets for this user.", reply_markup=kb_back_main())
            return

        tid = int(t["id"])
        await q.answer()
        # open ticket view
        await safe_delete_q_message(update, context)
        # re-use existing open logic by sending a synthetic callback is not needed; show direct:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT sender_id, text, created_at FROM ticket_messages WHERE ticket_id=? ORDER BY id DESC LIMIT 6", (tid,))
        msgs = cur.fetchall()
        conn.close()

        uname = get_user_display(target) or str(target)
        lines = [f"🆘 <b>Ticket #{tid}</b>\nUser: <b>{escape(uname)}</b>\n"]
        for m in reversed(msgs):
            sid = int(m["sender_id"])
            who = "User" if sid == target else "Support"
            lines.append(f"<b>{who}:</b> {escape(m['text'])}")
        await update.effective_chat.send_message("\n\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb_ticket_actions(tid))
        return

    if data.startswith("SA_SELLER_TICKETS:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        seller_id = int(data.split(":")[1])

        # This shows ONLY tickets for that seller's OWN shop? (you requested you can see your own seller support too,
        # but not other sellers' customer support). So this action only works for SUPER_ADMIN's own seller shop.
        if seller_id != SUPER_ADMIN_ID:
            await q.answer("Privacy lock: cannot access other seller support.", show_alert=True)
            return

        # Show open tickets for your own seller shop (shop_owner_id = SUPER_ADMIN_ID as seller shop)
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM tickets WHERE shop_owner_id=? AND status='open' ORDER BY updated_at DESC LIMIT 40", (SUPER_ADMIN_ID,))
        tickets = cur.fetchall()
        conn.close()

        await safe_delete_q_message(update, context)
        if not tickets:
            await update.effective_chat.send_message("No open tickets.", reply_markup=kb_back_main())
            return
        await update.effective_chat.send_message("🆘 <b>Your Seller Support Inbox</b>", parse_mode=ParseMode.HTML, reply_markup=kb_ticket_list(tickets, "SA_MENU"))
        return

    # If we reach here
    await q.answer("Unknown action.", show_alert=True)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    upsert_user(u)
    uid = u.id
    mode = get_mode(context)

    # Deposit amount entry
    if mode == "DEP_AMOUNT":
        text = (update.message.text or "").strip() if update.message else ""
        if not text:
            return
        try:
            amt = float(text)
            if amt <= 0:
                raise ValueError
        except ValueError:
            await update.effective_chat.send_message("❌ Invalid amount. Send a number like 10.")
            return

        info = tmp(context)
        shop_owner = int(info.get("shop_owner_id", get_current_shop(uid)))

        # Next: require photo proof
        set_mode(context, "DEP_PROOF", {"shop_owner_id": shop_owner, "amount": amt})
        await update.effective_chat.send_message("📸 Now send the <b>photo proof</b>.", parse_mode=ParseMode.HTML)
        return

    # Deposit proof photo
    if mode == "DEP_PROOF":
        if not update.message:
            return
        if not update.message.photo:
            await update.effective_chat.send_message("❌ You must send a <b>photo proof</b>.", parse_mode=ParseMode.HTML)
            return

        info = tmp(context)
        shop_owner = int(info.get("shop_owner_id", get_current_shop(uid)))
        amt = float(info.get("amount", 0))

        # Create deposit request
        photo = update.message.photo[-1]
        proof_id = photo.file_id

        conn = db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO deposit_requests(shop_owner_id, user_id, amount, proof_file_id, status, created_at) VALUES(?,?,?,?,?,?)",
            (shop_owner, uid, amt, proof_id, "pending", now_ts()),
        )
        dep_id = cur.lastrowid
        conn.commit()
        conn.close()

        # Notify approver:
        # Main shop -> super admin
        # Seller shop -> seller only (no super admin)
        approver_chat = shop_owner

        uname = get_user_display(uid) or str(uid)
        caption = (
            f"💳 <b>Deposit Request</b>\n"
            f"User: <b>{escape(uname)}</b>\n"
            f"Amount: <b>{fmt_money(amt)} {escape(CURRENCY)}</b>\n"
            f"Request ID: <b>#{dep_id}</b>"
        )
        try:
            await context.application.bot.send_photo(
                chat_id=approver_chat,
                photo=proof_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_approve_reject_deposit(dep_id),
            )
        except Exception:
            try:
                await context.application.bot.send_message(
                    chat_id=approver_chat,
                    text=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_approve_reject_deposit(dep_id),
                )
            except Exception:
                pass

        clear_mode(context)
        await update.effective_chat.send_message("✅ Deposit request sent. Please wait for approval.", reply_markup=kb_back_main())
        return

    # Support drafting (user)
    if mode == "SUPPORT_USER":
        if not update.message or not update.message.text:
            return
        add_draft(context, update.message.text.strip())
        await update.effective_chat.send_message("✅ Added. You can send more, then press ✅ Done.")
        return

    # Edit wallet address / message
    if mode in {"EDIT_WALLET_ADDR", "EDIT_WALLET_MSG"}:
        if not update.message or not update.message.text:
            return
        val = update.message.text.strip()
        info = tmp(context)
        shop_owner = int(info.get("shop_owner_id", get_current_shop(uid)))

        # permission: superadmin edits main; seller edits their own
        if shop_owner == SUPER_ADMIN_ID:
            if not is_superadmin(uid):
                await update.effective_chat.send_message("Not allowed.")
                clear_mode(context)
                return
        else:
            if uid != shop_owner:
                await update.effective_chat.send_message("Not allowed.")
                clear_mode(context)
                return

        if mode == "EDIT_WALLET_ADDR":
            set_shop_setting(shop_owner, "wallet_address", val)
            msg = "✅ Wallet address updated."
        else:
            set_shop_setting(shop_owner, "wallet_message", val)
            msg = "✅ Wallet message updated."

        clear_mode(context)
        await update.effective_chat.send_message(msg, reply_markup=kb_back_main())
        return

    # Edit welcome (text or photo/video with caption)
    if mode == "EDIT_WELCOME":
        info = tmp(context)
        shop_owner = int(info.get("shop_owner_id", get_current_shop(uid)))

        # permission
        if shop_owner == SUPER_ADMIN_ID:
            if not is_superadmin(uid):
                await update.effective_chat.send_message("Not allowed.")
                clear_mode(context)
                return
        else:
            if uid != shop_owner:
                await update.effective_chat.send_message("Not allowed.")
                clear_mode(context)
                return

        file_id = ""
        file_type = ""
        text = ""

        if update.message:
            if update.message.photo:
                file_id = update.message.photo[-1].file_id
                file_type = "photo"
                text = (update.message.caption or "").strip()
            elif update.message.video:
                file_id = update.message.video.file_id
                file_type = "video"
                text = (update.message.caption or "").strip()
            elif update.message.text:
                text = update.message.text.strip()
            else:
                await update.effective_chat.send_message("Send text or photo/video.")
                return
        else:
            return

        if text:
            set_shop_setting(shop_owner, "welcome_text", text)
        if file_id:
            set_shop_setting(shop_owner, "welcome_file_id", file_id)
            set_shop_setting(shop_owner, "welcome_file_type", file_type)
        else:
            # if text only, keep previous media; user can remove media by sending "REMOVE_MEDIA"
            pass

        clear_mode(context)
        await update.effective_chat.send_message("✅ Welcome message updated.", reply_markup=kb_back_main())
        return

    # Super admin edit seller balance
    if mode == "SA_EDIT_SELLER_BAL":
        if not is_superadmin(uid):
            clear_mode(context)
            return
        if not update.message or not update.message.text:
            return
        try:
            newbal = float(update.message.text.strip())
            if newbal < 0:
                newbal = 0.0
        except ValueError:
            await update.effective_chat.send_message("❌ Send a number.")
            return
        seller_id = int(tmp(context).get("seller_id", 0))
        conn = db()
        cur = conn.cursor()
        cur.execute("UPDATE sellers SET balance=? WHERE seller_id=?", (newbal, seller_id))
        conn.commit()
        conn.close()
        clear_mode(context)
        await update.effective_chat.send_message("✅ Seller balance updated.", reply_markup=kb_back_main())
        return

    # Super admin edit Become Seller description
    if mode == "SA_EDIT_SELLER_DESC":
        if not is_superadmin(uid):
            clear_mode(context)
            return
        if not update.message or not update.message.text:
            return
        set_shop_setting(SUPER_ADMIN_ID, "seller_desc", update.message.text.strip())
        clear_mode(context)
        await update.effective_chat.send_message("✅ Become Seller description updated.", reply_markup=kb_back_main())
        return

    # Super admin seller search
    if mode == "SA_SEARCH_SELLER":
        if not is_superadmin(uid):
            clear_mode(context)
            return
        if not update.message or not update.message.text:
            return
        qtxt = update.message.text.strip().lstrip("@").lower()

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT seller_id FROM sellers")
        all_s = [int(r["seller_id"]) for r in cur.fetchall()]
        conn.close()

        matches = []
        for sid in all_s:
            uname = get_user_display(sid).lower()
            if qtxt in uname.replace("@", "") or qtxt == str(sid):
                matches.append(sid)

        clear_mode(context)
        if not matches:
            await update.effective_chat.send_message("No matches.", reply_markup=kb_sa_main())
            return

        rows = [{"seller_id": sid} for sid in matches]
        await update.effective_chat.send_message("🔎 <b>Matches</b>", parse_mode=ParseMode.HTML, reply_markup=kb_sa_sellers_list(rows))  # type: ignore
        return

    # Super admin user search (main shop users only)
    if mode == "SA_SEARCH_USER":
        if not is_superadmin(uid):
            clear_mode(context)
            return
        if not update.message or not update.message.text:
            return
        qtxt = update.message.text.strip().lstrip("@").lower()

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM balances WHERE shop_owner_id=?", (SUPER_ADMIN_ID,))
        all_u = [int(r["user_id"]) for r in cur.fetchall()]
        conn.close()

        matches = []
        for xid in all_u:
            uname = get_user_display(xid).lower()
            if qtxt in uname.replace("@", "") or qtxt == str(xid):
                matches.append(xid)

        clear_mode(context)
        if not matches:
            await update.effective_chat.send_message("No matches.", reply_markup=kb_sa_main())
            return

        rows = [{"user_id": x} for x in matches]
        await update.effective_chat.send_message("🔎 <b>Matches</b>", parse_mode=ParseMode.HTML, reply_markup=kb_sa_users_list(rows))  # type: ignore
        return

    # If no mode, ignore text
    return


# ---------------------------
# Shop content creation flows (simple button-based setup)
# ---------------------------
# NOTE: For brevity & stability, creation uses text prompts (not IDs),
# but users do NOT need to type any product id; bot uses buttons and step flows.
async def on_admin_create_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # This handler is merged into on_button in a single file approach.
    pass


# ---------------------------
# Bootstrap
# ---------------------------
def main() -> None:
    init_db()
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_button))
    application.add_handler(MessageHandler(filters.ALL, on_message))

    log.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
