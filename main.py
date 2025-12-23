# main.py — AutoPanel (Railway-ready, button-only, full features rebuild)
# Python: 3.11+ | Library: python-telegram-bot 20.x
#
# ENV REQUIRED:
#   BOT_TOKEN
#   SUPER_ADMIN_ID   (your numeric Telegram ID)
#
# ENV OPTIONAL:
#   STORE_NAME=AutoPanel
#   CURRENCY=USDT
#   MAIN_WALLET= (or USDT_TRC20)
#   SELLER_SUB_PRICE=10
#   SELLER_SUB_DAYS=30
#   DB_FILE=data.db
#
# Notes:
# - Main shop = SUPER_ADMIN_ID
# - Seller shop = seller_id
# - Seller users are locked inside seller shop (cannot see main shop)
# - Super admin can search Users/Sellers (with list + search button on top)
# - Deposits require PHOTO proof, approve/reject deletes the approval message
# - Support uses Draft → Done
# - Admin uses buttons only (no /admin)
# - Category > Co-Category > Products
# - Product delivers Key + Get File button (tg link per product)

import os
import re
import time
import sqlite3
import logging
from typing import Optional, List, Dict, Any, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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

# -------------------------
# Config
# -------------------------
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN")

SUPER_ADMIN_ID = int((os.getenv("SUPER_ADMIN_ID") or os.getenv("ADMIN_ID") or "0").strip() or "0")
if SUPER_ADMIN_ID <= 0:
    raise RuntimeError("Missing/invalid SUPER_ADMIN_ID")

STORE_NAME = (os.getenv("STORE_NAME") or "AutoPanel").strip()
CURRENCY = (os.getenv("CURRENCY") or "USDT").strip()

DEFAULT_MAIN_WALLET = (os.getenv("MAIN_WALLET") or os.getenv("USDT_TRC20") or "").strip()

SELLER_SUB_PRICE = float((os.getenv("SELLER_SUB_PRICE") or "10").strip() or "10")
SELLER_SUB_DAYS = int((os.getenv("SELLER_SUB_DAYS") or "30").strip() or "30")

DB_FILE = (os.getenv("DB_FILE") or "data.db").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("autopanel")


# -------------------------
# Small utils
# -------------------------
def ts() -> int:
    return int(time.time())


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def money(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.2f}".rstrip("0").rstrip(".")


def is_super(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID


def is_main_shop(shop_owner_id: int) -> bool:
    return shop_owner_id == SUPER_ADMIN_ID


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# -------------------------
# DB init
# -------------------------
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
    )
    """)

    # browsing context (which shop user is currently in)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
        user_id INTEGER PRIMARY KEY,
        shop_owner_id INTEGER NOT NULL,
        locked INTEGER DEFAULT 0   -- 1 = seller-user locked in seller shop
    )
    """)

    # seller accounts
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

    # per-shop settings
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shop_settings(
        shop_owner_id INTEGER PRIMARY KEY,
        wallet_address TEXT DEFAULT '',
        wallet_message TEXT DEFAULT '',
        welcome_text TEXT DEFAULT '',
        welcome_file_id TEXT DEFAULT '',
        welcome_file_type TEXT DEFAULT '', -- photo/video/''
        seller_desc TEXT DEFAULT ''
    )
    """)

    # balances per shop
    cur.execute("""
    CREATE TABLE IF NOT EXISTS balances(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        balance REAL DEFAULT 0,
        PRIMARY KEY (shop_owner_id, user_id)
    )
    """)

    # bans per shop
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_bans(
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        banned INTEGER DEFAULT 1,
        PRIMARY KEY (shop_owner_id, user_id)
    )
    """)

    # Catalog
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
        key_text TEXT DEFAULT '',
        tg_link TEXT DEFAULT ''
    )
    """)

    # Transactions (for History)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_owner_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL,         -- deposit/purchase/adjust/seller_sub
        amount REAL DEFAULT 0,
        note TEXT DEFAULT '',
        created_at INTEGER NOT NULL
    )
    """)

    # Deposits
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

    # Support
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

    # Ensure settings exist for main shop
    ensure_shop_settings(SUPER_ADMIN_ID)

    # Default main wallet if provided
    if DEFAULT_MAIN_WALLET:
        cur.execute("SELECT wallet_address FROM shop_settings WHERE shop_owner_id=?", (SUPER_ADMIN_ID,))
        r = cur.fetchone()
        if r and not (r["wallet_address"] or "").strip():
            cur.execute("UPDATE shop_settings SET wallet_address=? WHERE shop_owner_id=?", (DEFAULT_MAIN_WALLET, SUPER_ADMIN_ID))
            conn.commit()

    # Default welcome message (main shop includes footer)
    cur.execute("SELECT welcome_text FROM shop_settings WHERE shop_owner_id=?", (SUPER_ADMIN_ID,))
    r = cur.fetchone()
    if r and not (r["welcome_text"] or "").strip():
        default = (
            f"✅ Welcome to <b>{esc(STORE_NAME)}</b>\n"
            f"Get your 24/7 Store Panel Here !!\n\n"
            f"Bot created by @RekkoOwn"
        )
        cur.execute("UPDATE shop_settings SET welcome_text=? WHERE shop_owner_id=?", (default, SUPER_ADMIN_ID))
        conn.commit()

    # Default Become Seller description
    cur.execute("SELECT seller_desc FROM shop_settings WHERE shop_owner_id=?", (SUPER_ADMIN_ID,))
    r = cur.fetchone()
    if r and not (r["seller_desc"] or "").strip():
        desc = (
            "⭐ <b>Become a Seller</b>\n\n"
            "✅ Your own shop\n"
            "✅ Your own products\n"
            "✅ Your own wallet + deposit approvals\n"
            "✅ Your own support inbox\n\n"
            f"Price: <b>{money(SELLER_SUB_PRICE)} {esc(CURRENCY)}</b> / <b>{SELLER_SUB_DAYS} days</b>\n"
            "Renew early to stack days."
        )
        cur.execute("UPDATE shop_settings SET seller_desc=? WHERE shop_owner_id=?", (desc, SUPER_ADMIN_ID))
        conn.commit()

    conn.close()


def ensure_shop_settings(shop_owner_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    if not cur.fetchone():
        # Seller welcome default WITHOUT footer (seller users must not see RekkoOwn footer)
        welcome = f"✅ Welcome to <b>{esc(STORE_NAME)}</b>\nGet your 24/7 Store Panel Here !!"
        # But for main shop we overwrite later with footer default
        cur.execute(
            "INSERT INTO shop_settings(shop_owner_id, wallet_address, wallet_message, welcome_text, welcome_file_id, welcome_file_type, seller_desc) "
            "VALUES(?,?,?,?,?,?,?)",
            (shop_owner_id, "", "", welcome, "", "", ""),
        )
        conn.commit()
    conn.close()


# -------------------------
# User/session helpers
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


def get_shop_settings(shop_owner_id: int) -> sqlite3.Row:
    ensure_shop_settings(shop_owner_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM shop_settings WHERE shop_owner_id=?", (shop_owner_id,))
    r = cur.fetchone()
    conn.close()
    assert r is not None
    return r


def set_shop_setting(shop_owner_id: int, field: str, value: str) -> None:
    if field not in {"wallet_address", "wallet_message", "welcome_text", "welcome_file_id", "welcome_file_type", "seller_desc"}:
        raise ValueError("bad field")
    ensure_shop_settings(shop_owner_id)
    conn = db()
    cur = conn.cursor()
    cur.execute(f"UPDATE shop_settings SET {field}=? WHERE shop_owner_id=?", (value or "", shop_owner_id))
    conn.commit()
    conn.close()


# -------------------------
# Seller permissions
# -------------------------
def seller_row(seller_id: int) -> Optional[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM sellers WHERE seller_id=?", (seller_id,))
    r = cur.fetchone()
    conn.close()
    return r


def seller_shop_active(seller_id: int) -> bool:
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


def seller_panel_allowed(seller_id: int) -> bool:
    if is_super(seller_id):
        return True
    r = seller_row(seller_id)
    if not r:
        return False
    if int(r["banned_panel"] or 0) == 1:
        return False
    return seller_shop_active(seller_id)


# -------------------------
# Balance / bans / tx
# -------------------------
def ensure_balance_row(shop_owner_id: int, uid: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,0)", (shop_owner_id, uid))
    conn.commit()
    conn.close()


def get_balance(shop_owner_id: int, uid: int) -> float:
    ensure_balance_row(shop_owner_id, uid)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM balances WHERE shop_owner_id=? AND user_id=?", (shop_owner_id, uid))
    r = cur.fetchone()
    conn.close()
    return float(r["balance"]) if r else 0.0


def set_balance(shop_owner_id: int, uid: int, new_bal: float) -> None:
    new_bal = max(0.0, float(new_bal))
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO balances(shop_owner_id, user_id, balance) VALUES(?,?,?) "
        "ON CONFLICT(shop_owner_id, user_id) DO UPDATE SET balance=excluded.balance",
        (shop_owner_id, uid, new_bal),
    )
    conn.commit()
    conn.close()


def add_balance(shop_owner_id: int, uid: int, delta: float) -> float:
    b = get_balance(shop_owner_id, uid)
    nb = max(0.0, b + float(delta))
    set_balance(shop_owner_id, uid, nb)
    return nb


def log_tx(shop_owner_id: int, uid: int, kind: str, amount: float, note: str = "") -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions(shop_owner_id, user_id, kind, amount, note, created_at) VALUES(?,?,?,?,?,?)",
        (shop_owner_id, uid, kind, float(amount), note or "", ts()),
    )
    conn.commit()
    conn.close()


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


# -------------------------
# Catalog queries
# -------------------------
def list_categories(shop_owner_id: int) -> List[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE shop_owner_id=? ORDER BY id DESC", (shop_owner_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def list_cocats(shop_owner_id: int, cat_id: int) -> List[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM cocategories WHERE shop_owner_id=? AND category_id=? ORDER BY id DESC",
        (shop_owner_id, cat_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def list_products(shop_owner_id: int, cat_id: int, cocat_id: int) -> List[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM products WHERE shop_owner_id=? AND category_id=? AND cocategory_id=? ORDER BY id DESC",
        (shop_owner_id, cat_id, cocat_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_product(shop_owner_id: int, pid: int) -> Optional[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE shop_owner_id=? AND id=?", (shop_owner_id, pid))
    r = cur.fetchone()
    conn.close()
    return r


# -------------------------
# Draft/state (button-only)
# -------------------------
def set_mode(ctx: ContextTypes.DEFAULT_TYPE, mode: str, data: Optional[Dict[str, Any]] = None) -> None:
    ctx.user_data["mode"] = mode
    ctx.user_data["data"] = data or {}
    ctx.user_data["draft"] = []


def clear_mode(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data.pop("mode", None)
    ctx.user_data.pop("data", None)
    ctx.user_data.pop("draft", None)


def mode(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    return str(ctx.user_data.get("mode") or "")


def data(ctx: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    return dict(ctx.user_data.get("data") or {})


def draft_add(ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    ctx.user_data.setdefault("draft", [])
    ctx.user_data["draft"].append(text)


def draft_text(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    parts = ctx.user_data.get("draft") or []
    return "\n".join([p for p in parts if p]).strip()


# -------------------------
# Keyboards
# -------------------------
def rows2(btns: List[InlineKeyboardButton], per_row: int = 2) -> List[List[InlineKeyboardButton]]:
    out: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for b in btns:
        row.append(b)
        if len(row) >= per_row:
            out.append(row)
            row = []
    if row:
        out.append(row)
    return out


def kb_main(uid: int) -> InlineKeyboardMarkup:
    shop_owner_id, locked = get_session(uid)

    # Seller-user locked in seller shop: Products, Wallet, Support only
    if locked == 1 and shop_owner_id != SUPER_ADMIN_ID and uid != shop_owner_id and not is_super(uid):
        btns = [
            InlineKeyboardButton("🛒 Products", callback_data="U_PRODUCTS"),
            InlineKeyboardButton("💰 Wallet", callback_data="U_WALLET"),
            InlineKeyboardButton("🆘 Support", callback_data="U_SUPPORT"),
        ]
        return InlineKeyboardMarkup(rows2(btns, 2))

    btns = [
        InlineKeyboardButton("🛒 Products", callback_data="U_PRODUCTS"),
        InlineKeyboardButton("💰 Wallet", callback_data="U_WALLET"),
        InlineKeyboardButton("🆘 Support", callback_data="U_SUPPORT"),
    ]

    # Become Seller only in main shop and only for non-sellers and not super admin
    if is_main_shop(shop_owner_id) and (not seller_shop_active(uid)) and (not is_super(uid)):
        btns.append(InlineKeyboardButton("⭐ Become Seller", callback_data="BECOME_SELLER"))

    # Admin panel: seller in own shop OR super admin in main shop
    if is_super(uid) and is_main_shop(shop_owner_id):
        btns.append(InlineKeyboardButton("🛠 Admin Panel", callback_data="ADMIN_PANEL"))
    elif uid == shop_owner_id and seller_panel_allowed(uid):
        btns.append(InlineKeyboardButton("🛠 Admin Panel", callback_data="ADMIN_PANEL"))

    # Super admin button on main menu (ONLY YOU)
    if is_super(uid):
        btns.append(InlineKeyboardButton("👑 Super Admin", callback_data="SA_MENU"))

    return InlineKeyboardMarkup(rows2(btns, 2))


def kb_back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]])


def kb_deposit() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Deposit", callback_data="DEP_START")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])


def kb_done_cancel(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Done", callback_data=f"{prefix}_DONE"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"{prefix}_CANCEL"),
    ]])


def kb_become_seller() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💳 Pay {money(SELLER_SUB_PRICE)} {CURRENCY}", callback_data="SELLER_PAY")],
        [InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")],
    ])


def kb_categories(shop_owner_id: int, cats: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for c in cats[:50]:
        rows.append([InlineKeyboardButton(f"📂 {c['name']}", callback_data=f"CAT:{c['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)


def kb_cocats(cat_id: int, cocats: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for cc in cocats[:50]:
        rows.append([InlineKeyboardButton(f"📁 {cc['name']}", callback_data=f"COCAT:{cat_id}:{cc['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="U_PRODUCTS")])
    return InlineKeyboardMarkup(rows)


def kb_products(cat_id: int, cocat_id: int, prods: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for p in prods[:60]:
        rows.append([InlineKeyboardButton(f"🛒 {p['name']} — {money(float(p['price']))} {CURRENCY}", callback_data=f"PROD:{p['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"CAT:{cat_id}")])
    return InlineKeyboardMarkup(rows)


def kb_product_view(pid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Buy", callback_data=f"BUY:{pid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="U_PRODUCTS")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])


def kb_get_file(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("📁 Get File", url=url)]])


def kb_admin_panel(uid: int) -> InlineKeyboardMarkup:
    # Seller: in own shop
    # Super: in main shop
    shop_owner_id, _ = get_session(uid)

    rows: List[List[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton("🛒 Manage Shop", callback_data="M_SHOP")])
    rows.append([InlineKeyboardButton("💳 Approve Deposits", callback_data="M_DEPOSITS")])
    rows.append([InlineKeyboardButton("🆘 Support Inbox", callback_data="M_TICKETS")])
    rows.append([InlineKeyboardButton("👥 Users", callback_data="M_USERS")])

    rows.append([InlineKeyboardButton("💳 Set Wallet Address", callback_data="M_SET_WALLET")])
    rows.append([InlineKeyboardButton("📝 Edit Wallet Message", callback_data="M_SET_WALLETMSG")])
    rows.append([InlineKeyboardButton("🖼 Edit Welcome Message", callback_data="M_SET_WELCOME")])

    # Sellers only: share shop + main shop button
    if uid == shop_owner_id and seller_panel_allowed(uid):
        rows.append([InlineKeyboardButton("📣 Share My Shop", callback_data="M_SHARE")])
        rows.append([InlineKeyboardButton("🏬 Main Shop", callback_data="GO_MAIN_SHOP")])

    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)


def kb_manage_shop() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Category", callback_data="ADD_CAT")],
        [InlineKeyboardButton("➕ Add Co-Category", callback_data="ADD_COCAT")],
        [InlineKeyboardButton("➕ Add Product", callback_data="ADD_PROD")],
        [InlineKeyboardButton("🗂 View Categories", callback_data="VIEW_CATS_ADMIN")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ADMIN_PANEL")],
    ])


def kb_skip_media(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip (No Media)", callback_data=f"{prefix}_SKIP_MEDIA")],
        [InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")],
    ])


def kb_yes_no_desc(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Add Description", callback_data=f"{prefix}_DESC_Y"),
            InlineKeyboardButton("⏭ Skip Description", callback_data=f"{prefix}_DESC_N"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")],
    ])


def kb_sa_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏪 Sellers", callback_data="SA_SELLERS")],
        [InlineKeyboardButton("👥 Users", callback_data="SA_USERS")],
        [InlineKeyboardButton("📝 Edit Become Seller Description", callback_data="SA_EDIT_SELLER_DESC")],
        [InlineKeyboardButton("⬅️ Back", callback_data="MAIN_MENU")],
    ])


def kb_sa_sellers_list(sellers: List[int]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton("🔎 Search Seller", callback_data="SA_SEARCH_SELLER")])
    for sid in sellers[:50]:
        rows.append([InlineKeyboardButton(f"🏪 {user_display(sid)}", callback_data=f"SA_SELLER:{sid}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="SA_MENU")])
    return InlineKeyboardMarkup(rows)


def kb_sa_users_list(users: List[int]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton("🔎 Search User", callback_data="SA_SEARCH_USER")])
    for uid in users[:60]:
        rows.append([InlineKeyboardButton(f"👤 {user_display(uid)}", callback_data=f"SA_USER:{uid}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="SA_MENU")])
    return InlineKeyboardMarkup(rows)


def kb_sa_seller_actions(seller_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Edit Seller Balance", callback_data=f"SA_SELLER_BAL:{seller_id}")],
        [InlineKeyboardButton("➕ Add 7 days", callback_data=f"SA_ADD_DAYS:{seller_id}:7"),
         InlineKeyboardButton("➕ Add 14 days", callback_data=f"SA_ADD_DAYS:{seller_id}:14")],
        [InlineKeyboardButton("➕ Add 30 days", callback_data=f"SA_ADD_DAYS:{seller_id}:30")],
        [InlineKeyboardButton("⏳ Restrict 7", callback_data=f"SA_RESTRICT:{seller_id}:7"),
         InlineKeyboardButton("⏳ Restrict 14", callback_data=f"SA_RESTRICT:{seller_id}:14")],
        [InlineKeyboardButton("⏳ Restrict 30", callback_data=f"SA_RESTRICT:{seller_id}:30")],
        [InlineKeyboardButton("🚫 Ban Shop", callback_data=f"SA_BAN_SHOP:{seller_id}"),
         InlineKeyboardButton("✅ Unban Shop", callback_data=f"SA_UNBAN_SHOP:{seller_id}")],
        [InlineKeyboardButton("🚫 Ban Panel", callback_data=f"SA_BAN_PANEL:{seller_id}"),
         InlineKeyboardButton("✅ Unban Panel", callback_data=f"SA_UNBAN_PANEL:{seller_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="SA_SELLERS")],
    ])


def kb_sa_user_actions(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Edit Balance", callback_data=f"SA_EDIT_BAL:{uid}")],
        [InlineKeyboardButton("🆘 Reply Support", callback_data=f"SA_USER_TICKETS:{uid}")],
        [InlineKeyboardButton("🚫 Ban From Main Shop", callback_data=f"SA_BAN_USER:{uid}"),
         InlineKeyboardButton("✅ Unban", callback_data=f"SA_UNBAN_USER:{uid}")],
        [InlineKeyboardButton("📜 History", callback_data=f"SA_HIST:{uid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="SA_USERS")],
    ])


def kb_dep_approve(dep_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"DEP_OK:{dep_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"DEP_NO:{dep_id}"),
    ]])


def kb_users_manage(shop_owner_id: int, users: List[int]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for uid in users[:60]:
        rows.append([InlineKeyboardButton(f"👤 {user_display(uid)}", callback_data=f"M_USER:{uid}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ADMIN_PANEL")])
    return InlineKeyboardMarkup(rows)


def kb_user_balance_buttons(prefix: str, uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ +10", callback_data=f"{prefix}:{uid}:10"),
         InlineKeyboardButton("➕ +50", callback_data=f"{prefix}:{uid}:50")],
        [InlineKeyboardButton("➖ -10", callback_data=f"{prefix}:{uid}:-10"),
         InlineKeyboardButton("➖ -50", callback_data=f"{prefix}:{uid}:-50")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ADMIN_PANEL")],
    ])


# -------------------------
# Safe delete
# -------------------------
async def delete_msg(app: Application, chat_id: int, message_id: int) -> None:
    try:
        await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except BadRequest:
        pass
    except Exception:
        pass


async def delete_callback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.callback_query and update.callback_query.message:
            m = update.callback_query.message
            await delete_msg(context.application, m.chat_id, m.message_id)
    except Exception:
        pass


# -------------------------
# Welcome sender
# -------------------------
async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE, shop_owner_id: int) -> None:
    s = get_shop_settings(shop_owner_id)
    text = (s["welcome_text"] or "").strip() or "Welcome!"
    fid = (s["welcome_file_id"] or "").strip()
    ftype = (s["welcome_file_type"] or "").strip()

    kb = kb_main(update.effective_user.id)

    if fid and ftype == "photo":
        await update.effective_chat.send_photo(photo=fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
    elif fid and ftype == "video":
        await update.effective_chat.send_video(video=fid, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# -------------------------
# /start and deep-link routing
# -------------------------
def parse_start_arg(arg: str) -> Optional[int]:
    # /start s_<sellerid>
    m = re.match(r"^s_(\d+)$", (arg or "").strip())
    if not m:
        return None
    return int(m.group(1))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    upsert_user(u)

    shop_owner_id = SUPER_ADMIN_ID
    locked = 0

    if context.args:
        sid = parse_start_arg(context.args[0])
        if sid and seller_shop_active(sid):
            shop_owner_id = sid
            locked = 1  # seller-user lock in seller shop

    # set session
    set_session(u.id, shop_owner_id, locked)

    # IMPORTANT FIX: user must appear in admin panel after /start
    ensure_balance_row(shop_owner_id, u.id)

    # Also if main shop start, ensure main balance row exists
    if shop_owner_id != SUPER_ADMIN_ID and locked == 1:
        # seller users should still have a balance row for that seller shop
        ensure_balance_row(shop_owner_id, u.id)

    await send_welcome(update, context, shop_owner_id)


# -------------------------
# Support + Deposits + Purchases
# -------------------------
def get_open_ticket(shop_owner_id: int, uid: int) -> Optional[int]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM tickets WHERE shop_owner_id=? AND user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
        (shop_owner_id, uid),
    )
    r = cur.fetchone()
    conn.close()
    return int(r["id"]) if r else None


def create_ticket(shop_owner_id: int, uid: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO tickets(shop_owner_id, user_id, status, created_at, updated_at) VALUES(?,?,?,?,?)",
        (shop_owner_id, uid, "open", ts(), ts()),
    )
    tid = cur.lastrowid
    conn.commit()
    conn.close()
    return int(tid)


def add_ticket_msg(ticket_id: int, sender_id: int, text: str) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticket_messages(ticket_id, sender_id, text, created_at) VALUES(?,?,?,?)",
        (ticket_id, sender_id, text, ts()),
    )
    cur.execute("UPDATE tickets SET updated_at=? WHERE id=?", (ts(), ticket_id))
    conn.commit()
    conn.close()


def list_open_tickets(shop_owner_id: int) -> List[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM tickets WHERE shop_owner_id=? AND status='open' ORDER BY updated_at DESC LIMIT 60",
        (shop_owner_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def ticket_info(ticket_id: int) -> Optional[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    r = cur.fetchone()
    conn.close()
    return r


def ticket_last_msgs(ticket_id: int, limit: int = 8) -> List[sqlite3.Row]:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT sender_id, text, created_at FROM ticket_messages WHERE ticket_id=? ORDER BY id DESC LIMIT ?",
        (ticket_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def close_ticket(ticket_id: int) -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE tickets SET status='closed', updated_at=? WHERE id=?", (ts(), ticket_id))
    conn.commit()
    conn.close()


# -------------------------
# Admin catalog creation flows (button-only)
# -------------------------
def admin_context_shop(uid: int) -> Optional[int]:
    shop_owner_id, locked = get_session(uid)
    # super admin manages main shop only
    if is_super(uid):
        if not is_main_shop(shop_owner_id):
            # even if super admin is inside something, admin panel is for main
            return SUPER_ADMIN_ID
        return SUPER_ADMIN_ID
    # seller manages their own shop only
    if uid == shop_owner_id and seller_panel_allowed(uid):
        return uid
    return None


def can_use_admin_panel(uid: int) -> bool:
    return admin_context_shop(uid) is not None


# -------------------------
# Handler: callbacks
# -------------------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    u = update.effective_user
    upsert_user(u)
    uid = u.id

    shop_owner_id, locked = get_session(uid)
    cb = q.data or ""

    # MAIN_MENU must cancel modes and delete the button-message
    if cb == "MAIN_MENU":
        clear_mode(context)
        await delete_callback_message(update, context)

        # locked seller user stays in seller shop
        shop_owner_id, locked = get_session(uid)
        if locked == 1:
            await send_welcome(update, context, shop_owner_id)
            return

        # super admin always main
        if is_super(uid):
            set_session(uid, SUPER_ADMIN_ID, 0)
            ensure_balance_row(SUPER_ADMIN_ID, uid)
            await send_welcome(update, context, SUPER_ADMIN_ID)
            return

        # seller goes to own shop, normal goes to main
        if seller_shop_active(uid):
            set_session(uid, uid, 0)
            ensure_balance_row(uid, uid)
            await send_welcome(update, context, uid)
        else:
            set_session(uid, SUPER_ADMIN_ID, 0)
            ensure_balance_row(SUPER_ADMIN_ID, uid)
            await send_welcome(update, context, SUPER_ADMIN_ID)
        return

    # hard lock: seller-user cannot go main shop
    if locked == 1 and cb == "GO_MAIN_SHOP":
        await q.answer("Not allowed.", show_alert=True)
        return

    # Super admin menu
    if cb == "SA_MENU":
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        clear_mode(context)
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("👑 <b>Super Admin</b>", parse_mode=ParseMode.HTML, reply_markup=kb_sa_menu())
        return

    # Admin panel
    if cb == "ADMIN_PANEL":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        clear_mode(context)
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("🛠 <b>Admin Panel</b>", parse_mode=ParseMode.HTML, reply_markup=kb_admin_panel(uid))
        return

    # Seller: go main shop (seller only)
    if cb == "GO_MAIN_SHOP":
        if is_super(uid):
            set_session(uid, SUPER_ADMIN_ID, 0)
            ensure_balance_row(SUPER_ADMIN_ID, uid)
            await delete_callback_message(update, context)
            await send_welcome(update, context, SUPER_ADMIN_ID)
            return
        if seller_shop_active(uid):
            # seller can view main shop (their customers cannot)
            set_session(uid, SUPER_ADMIN_ID, 0)
            ensure_balance_row(SUPER_ADMIN_ID, uid)
            await delete_callback_message(update, context)
            await send_welcome(update, context, SUPER_ADMIN_ID)
            return
        await q.answer("Not allowed.", show_alert=True)
        return

    # User buttons
    if cb == "U_PRODUCTS":
        clear_mode(context)
        await delete_callback_message(update, context)

        shop_owner_id, locked = get_session(uid)
        # if seller shop inactive, block seller users
        if locked == 1 and not seller_shop_active(shop_owner_id):
            await update.effective_chat.send_message("⛔ This seller shop is inactive.", reply_markup=kb_back_main())
            return

        cats = list_categories(shop_owner_id)
        if not cats:
            await update.effective_chat.send_message("No categories yet.", reply_markup=kb_back_main())
            return
        await update.effective_chat.send_message("📂 <b>Categories</b>", parse_mode=ParseMode.HTML, reply_markup=kb_categories(shop_owner_id, cats))
        return

    if cb == "U_WALLET":
        clear_mode(context)
        await delete_callback_message(update, context)

        shop_owner_id, _ = get_session(uid)
        if is_banned(shop_owner_id, uid):
            await update.effective_chat.send_message("⛔ You are banned from this shop.", reply_markup=kb_back_main())
            return

        ensure_balance_row(shop_owner_id, uid)
        bal = get_balance(shop_owner_id, uid)
        s = get_shop_settings(shop_owner_id)
        addr = (s["wallet_address"] or "").strip()
        msg = (s["wallet_message"] or "").strip()

        txt = (
            f"💰 <b>Wallet</b>\n"
            f"Balance: <b>{money(bal)} {esc(CURRENCY)}</b>\n"
        )
        if addr:
            txt += f"\n<b>Wallet Address:</b>\n<code>{esc(addr)}</code>\n"
        if msg:
            txt += f"\n<b>Note:</b>\n{esc(msg)}"

        await update.effective_chat.send_message(txt, parse_mode=ParseMode.HTML, reply_markup=kb_deposit())
        return

    if cb == "DEP_START":
        shop_owner_id, _ = get_session(uid)
        if is_banned(shop_owner_id, uid):
            await q.answer("Banned.", show_alert=True)
            return
        clear_mode(context)
        set_mode(context, "DEP_AMOUNT", {"shop_owner_id": shop_owner_id})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message(
            "➕ <b>Deposit</b>\nSend deposit amount (example: 10).",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")]]),
        )
        return

    if cb == "U_SUPPORT":
        shop_owner_id, _ = get_session(uid)
        if is_banned(shop_owner_id, uid):
            await q.answer("Banned.", show_alert=True)
            return
        clear_mode(context)
        set_mode(context, "SUPPORT_DRAFT", {"shop_owner_id": shop_owner_id})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message(
            "🆘 <b>Support</b>\nSend your message(s), then press ✅ Done.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_done_cancel("SUP"),
        )
        return

    if cb in {"SUP_DONE", "SUP_CANCEL"}:
        if mode(context) != "SUPPORT_DRAFT":
            await q.answer("No active support.", show_alert=True)
            return
        if cb == "SUP_CANCEL":
            clear_mode(context)
            await delete_callback_message(update, context)
            await send_welcome(update, context, get_session(uid)[0])
            return

        text = draft_text(context)
        if not text:
            await q.answer("Send a message first.", show_alert=True)
            return

        shop_owner_id = int(data(context).get("shop_owner_id", SUPER_ADMIN_ID))
        tid = get_open_ticket(shop_owner_id, uid) or create_ticket(shop_owner_id, uid)
        add_ticket_msg(tid, uid, text)

        # notify owner only (main -> superadmin, seller -> seller)
        try:
            await context.application.bot.send_message(
                chat_id=shop_owner_id,
                text=f"🆘 <b>New Support</b>\nShop: <b>{'Main Shop' if is_main_shop(shop_owner_id) else 'Seller Shop'}</b>\nFrom: <b>{esc(user_display(uid))}</b>\nTicket: <b>#{tid}</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        clear_mode(context)
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("✅ Sent to support.", reply_markup=kb_back_main())
        return

    # Become seller
    if cb == "BECOME_SELLER":
        if locked == 1:
            await q.answer("Not available.", show_alert=True)
            return
        if not is_main_shop(shop_owner_id) or is_super(uid) or seller_shop_active(uid):
            await q.answer("Not available.", show_alert=True)
            return
        clear_mode(context)
        await delete_callback_message(update, context)
        desc = (get_shop_settings(SUPER_ADMIN_ID)["seller_desc"] or "").strip()
        await update.effective_chat.send_message(desc or "Become Seller", parse_mode=ParseMode.HTML, reply_markup=kb_become_seller())
        return

    if cb == "SELLER_PAY":
        # cannot buy in seller shop
        shop_owner_id, locked = get_session(uid)
        if locked == 1 or not is_main_shop(shop_owner_id) or is_super(uid) or seller_shop_active(uid):
            await q.answer("Not available.", show_alert=True)
            return

        if is_banned(SUPER_ADMIN_ID, uid):
            await q.answer("Banned.", show_alert=True)
            return

        bal = get_balance(SUPER_ADMIN_ID, uid)
        if bal < SELLER_SUB_PRICE:
            await delete_callback_message(update, context)
            await update.effective_chat.send_message(
                f"❌ Not enough balance.\nNeeded: {money(SELLER_SUB_PRICE)} {CURRENCY}\nYour balance: {money(bal)} {CURRENCY}",
                reply_markup=kb_back_main(),
            )
            return

        # deduct
        nb = add_balance(SUPER_ADMIN_ID, uid, -SELLER_SUB_PRICE)
        log_tx(SUPER_ADMIN_ID, uid, "seller_sub", -SELLER_SUB_PRICE, f"Become Seller ({SELLER_SUB_DAYS} days)")

        # create/extend seller record
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM sellers WHERE seller_id=?", (uid,))
        r = cur.fetchone()
        add_sec = SELLER_SUB_DAYS * 86400
        now = ts()
        if not r:
            cur.execute(
                "INSERT INTO sellers(seller_id, sub_until, banned_shop, restricted_until, banned_panel, balance) VALUES(?,?,?,?,?,?)",
                (uid, now + add_sec, 0, 0, 0, 0.0),
            )
        else:
            old = int(r["sub_until"] or 0)
            new_until = (old if old > now else now) + add_sec
            cur.execute("UPDATE sellers SET sub_until=? WHERE seller_id=?", (new_until, uid))
        conn.commit()
        conn.close()

        ensure_shop_settings(uid)

        # seller switches to own shop
        set_session(uid, uid, 0)
        ensure_balance_row(uid, uid)

        await delete_callback_message(update, context)
        await update.effective_chat.send_message(
            f"✅ You are now a <b>Seller</b>.\nBalance: <b>{money(nb)} {esc(CURRENCY)}</b>",
            parse_mode=ParseMode.HTML,
        )
        await send_welcome(update, context, uid)
        return

    # Catalog navigation
    if cb.startswith("CAT:"):
        clear_mode(context)
        await delete_callback_message(update, context)

        cat_id = int(cb.split(":")[1])
        shop_owner_id, _ = get_session(uid)

        cocats = list_cocats(shop_owner_id, cat_id)
        if not cocats:
            await update.effective_chat.send_message("No co-categories yet.", reply_markup=kb_back_main())
            return
        await update.effective_chat.send_message("📁 <b>Co-Categories</b>", parse_mode=ParseMode.HTML, reply_markup=kb_cocats(cat_id, cocats))
        return

    if cb.startswith("COCAT:"):
        clear_mode(context)
        await delete_callback_message(update, context)

        _, cat_id, cocat_id = cb.split(":")
        cat_id = int(cat_id); cocat_id = int(cocat_id)
        shop_owner_id, _ = get_session(uid)

        prods = list_products(shop_owner_id, cat_id, cocat_id)
        if not prods:
            await update.effective_chat.send_message("No products yet.", reply_markup=kb_back_main())
            return
        await update.effective_chat.send_message("🛒 <b>Products</b>", parse_mode=ParseMode.HTML, reply_markup=kb_products(cat_id, cocat_id, prods))
        return

    if cb.startswith("PROD:"):
        clear_mode(context)
        await delete_callback_message(update, context)

        pid = int(cb.split(":")[1])
        shop_owner_id, _ = get_session(uid)
        p = get_product(shop_owner_id, pid)
        if not p:
            await q.answer("Not found.", show_alert=True)
            return

        txt = f"🛒 <b>{esc(p['name'])}</b>\nPrice: <b>{money(float(p['price']))} {esc(CURRENCY)}</b>"
        if (p["description"] or "").strip():
            txt += f"\n\n{esc(p['description'])}"
        await update.effective_chat.send_message(txt, parse_mode=ParseMode.HTML, reply_markup=kb_product_view(pid))
        return

    if cb.startswith("BUY:"):
        pid = int(cb.split(":")[1])
        shop_owner_id, _ = get_session(uid)

        if is_banned(shop_owner_id, uid):
            await q.answer("Banned.", show_alert=True)
            return

        p = get_product(shop_owner_id, pid)
        if not p:
            await q.answer("Not found.", show_alert=True)
            return

        price = float(p["price"])
        bal = get_balance(shop_owner_id, uid)
        if bal < price:
            await delete_callback_message(update, context)
            await update.effective_chat.send_message(
                f"❌ Not enough balance.\nNeeded: {money(price)} {CURRENCY}\nYour balance: {money(bal)} {CURRENCY}",
                reply_markup=kb_back_main(),
            )
            return

        nb = add_balance(shop_owner_id, uid, -price)
        log_tx(shop_owner_id, uid, "purchase", -price, f"Purchased: {p['name']}")

        # Notify shop owner ONLY (main shop -> super admin; seller shop -> that seller)
        try:
            await context.application.bot.send_message(
                chat_id=shop_owner_id,
                text=(
                    f"🛒 <b>New Purchase</b>\n"
                    f"Shop: <b>{'Main Shop' if is_main_shop(shop_owner_id) else 'Seller Shop'}</b>\n"
                    f"Buyer: <b>{esc(user_display(uid))}</b>\n"
                    f"Product: <b>{esc(p['name'])}</b>\n"
                    f"Paid: <b>{money(price)} {esc(CURRENCY)}</b>"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        await delete_callback_message(update, context)

        out = (
            f"✅ <b>Purchase Successful</b>\n\n"
            f"Product: <b>{esc(p['name'])}</b>\n"
            f"Paid: <b>{money(price)} {esc(CURRENCY)}</b>\n"
            f"Total Balance: <b>{money(nb)} {esc(CURRENCY)}</b>\n"
        )
        if (p["key_text"] or "").strip():
            out += f"\n🔑 <b>Key:</b>\n<code>{esc(p['key_text'])}</code>\n"
        await update.effective_chat.send_message(out, parse_mode=ParseMode.HTML)

        link = (p["tg_link"] or "").strip()
        if link:
            await update.effective_chat.send_message("📁 Delivery:", reply_markup=kb_get_file(link))
        return

    # -------------------------
    # Admin Panel: Manage Shop
    # -------------------------
    if cb == "M_SHOP":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        clear_mode(context)
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("🛒 <b>Manage Shop</b>", parse_mode=ParseMode.HTML, reply_markup=kb_manage_shop())
        return

    if cb == "VIEW_CATS_ADMIN":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid)
        assert shop is not None
        cats = list_categories(shop)
        await delete_callback_message(update, context)
        if not cats:
            await update.effective_chat.send_message("No categories yet.", reply_markup=kb_manage_shop())
            return
        # show categories (admin)
        rows = [[InlineKeyboardButton(f"📂 {c['name']}", callback_data=f"ADMIN_CAT:{c['id']}")] for c in cats[:60]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP")])
        await update.effective_chat.send_message("🗂 <b>Categories</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb.startswith("ADMIN_CAT:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid)
        assert shop is not None
        cat_id = int(cb.split(":")[1])
        cocats = list_cocats(shop, cat_id)

        rows: List[List[InlineKeyboardButton]] = []
        rows.append([InlineKeyboardButton("🗑 Delete Category", callback_data=f"DEL_CAT:{cat_id}")])
        rows.append([InlineKeyboardButton("➕ Add Co-Category Here", callback_data=f"ADD_COCAT_IN:{cat_id}")])
        if cocats:
            rows.append([InlineKeyboardButton("📁 View Co-Categories", callback_data=f"ADMIN_COCATS:{cat_id}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="VIEW_CATS_ADMIN")])
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("📂 <b>Category Tools</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb.startswith("ADMIN_COCATS:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid)
        assert shop is not None
        cat_id = int(cb.split(":")[1])
        cocats = list_cocats(shop, cat_id)
        await delete_callback_message(update, context)
        if not cocats:
            await update.effective_chat.send_message("No co-categories.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"ADMIN_CAT:{cat_id}")]]))
            return
        rows = [[InlineKeyboardButton(f"📁 {cc['name']}", callback_data=f"ADMIN_COCAT:{cat_id}:{cc['id']}")] for cc in cocats[:60]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"ADMIN_CAT:{cat_id}")])
        await update.effective_chat.send_message("📁 <b>Co-Categories</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb.startswith("ADMIN_COCAT:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        _, cat_id, cocat_id = cb.split(":")
        cat_id = int(cat_id); cocat_id = int(cocat_id)
        await delete_callback_message(update, context)

        rows = [
            [InlineKeyboardButton("🗑 Delete Co-Category", callback_data=f"DEL_COCAT:{cocat_id}")],
            [InlineKeyboardButton("➕ Add Product Here", callback_data=f"ADD_PROD_IN:{cat_id}:{cocat_id}")],
            [InlineKeyboardButton("📦 View Products", callback_data=f"ADMIN_PRODS:{cat_id}:{cocat_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"ADMIN_COCATS:{cat_id}")],
        ]
        await update.effective_chat.send_message("📁 <b>Co-Category Tools</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb.startswith("ADMIN_PRODS:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid)
        assert shop is not None
        _, cat_id, cocat_id = cb.split(":")
        cat_id = int(cat_id); cocat_id = int(cocat_id)
        prods = list_products(shop, cat_id, cocat_id)
        await delete_callback_message(update, context)
        if not prods:
            await update.effective_chat.send_message("No products yet.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"ADMIN_COCAT:{cat_id}:{cocat_id}")]]))
            return
        rows = [[InlineKeyboardButton(f"🛒 {p['name']}", callback_data=f"ADMIN_PROD:{p['id']}")] for p in prods[:60]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"ADMIN_COCAT:{cat_id}:{cocat_id}")])
        await update.effective_chat.send_message("📦 <b>Products</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb.startswith("ADMIN_PROD:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        pid = int(cb.split(":")[1])
        await delete_callback_message(update, context)
        rows = [
            [InlineKeyboardButton("✏️ Edit Description", callback_data=f"EDIT_PROD_DESC:{pid}")],
            [InlineKeyboardButton("🔑 Edit Key Text", callback_data=f"EDIT_PROD_KEY:{pid}")],
            [InlineKeyboardButton("🔗 Edit Telegram Link", callback_data=f"EDIT_PROD_LINK:{pid}")],
            [InlineKeyboardButton("🗑 Delete Product", callback_data=f"DEL_PROD:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP")],
        ]
        await update.effective_chat.send_message("🛒 <b>Product Tools</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    # Creation flows
    if cb == "ADD_CAT":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        clear_mode(context)
        set_mode(context, "ADD_CAT_NAME", {"shop_owner_id": admin_context_shop(uid)})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("➕ <b>Add Category</b>\nSend category name.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")]]))
        return

    if cb == "ADD_COCAT":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid)
        assert shop is not None
        cats = list_categories(shop)
        if not cats:
            await q.answer("Create a category first.", show_alert=True)
            return
        await delete_callback_message(update, context)
        rows = [[InlineKeyboardButton(f"📂 {c['name']}", callback_data=f"ADD_COCAT_IN:{c['id']}")] for c in cats[:60]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP")])
        await update.effective_chat.send_message("Select category to add Co-Category:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb.startswith("ADD_COCAT_IN:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        cat_id = int(cb.split(":")[1])
        clear_mode(context)
        set_mode(context, "ADD_COCAT_NAME", {"shop_owner_id": admin_context_shop(uid), "category_id": cat_id})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("➕ <b>Add Co-Category</b>\nSend co-category name.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")]]))
        return

    if cb == "ADD_PROD":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid)
        assert shop is not None
        cats = list_categories(shop)
        if not cats:
            await q.answer("Create category first.", show_alert=True)
            return
        await delete_callback_message(update, context)
        rows = [[InlineKeyboardButton(f"📂 {c['name']}", callback_data=f"ADD_PROD_PICKCAT:{c['id']}")] for c in cats[:60]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP")])
        await update.effective_chat.send_message("Select category:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb.startswith("ADD_PROD_PICKCAT:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        cat_id = int(cb.split(":")[1])
        shop = admin_context_shop(uid)
        assert shop is not None
        cocats = list_cocats(shop, cat_id)
        await delete_callback_message(update, context)
        if not cocats:
            await update.effective_chat.send_message("No co-categories. Create one first.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP")]]))
            return
        rows = [[InlineKeyboardButton(f"📁 {cc['name']}", callback_data=f"ADD_PROD_IN:{cat_id}:{cc['id']}")] for cc in cocats[:60]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP")])
        await update.effective_chat.send_message("Select co-category:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb.startswith("ADD_PROD_IN:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        _, cat_id, cocat_id = cb.split(":")
        cat_id = int(cat_id); cocat_id = int(cocat_id)
        clear_mode(context)
        set_mode(context, "ADD_PROD_NAME", {"shop_owner_id": admin_context_shop(uid), "category_id": cat_id, "cocategory_id": cocat_id})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("➕ <b>Add Product</b>\nSend product name.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")]]))
        return

    # Delete actions
    if cb.startswith("DEL_CAT:") and can_use_admin_panel(uid):
        shop = admin_context_shop(uid); assert shop is not None
        cat_id = int(cb.split(":")[1])
        conn = db(); cur = conn.cursor()
        # cascade delete
        cur.execute("DELETE FROM products WHERE shop_owner_id=? AND category_id=?", (shop, cat_id))
        cur.execute("DELETE FROM cocategories WHERE shop_owner_id=? AND category_id=?", (shop, cat_id))
        cur.execute("DELETE FROM categories WHERE shop_owner_id=? AND id=?", (shop, cat_id))
        conn.commit(); conn.close()
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("✅ Category deleted.", reply_markup=kb_manage_shop())
        return

    if cb.startswith("DEL_COCAT:") and can_use_admin_panel(uid):
        shop = admin_context_shop(uid); assert shop is not None
        cocat_id = int(cb.split(":")[1])
        conn = db(); cur = conn.cursor()
        cur.execute("DELETE FROM products WHERE shop_owner_id=? AND cocategory_id=?", (shop, cocat_id))
        cur.execute("DELETE FROM cocategories WHERE shop_owner_id=? AND id=?", (shop, cocat_id))
        conn.commit(); conn.close()
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("✅ Co-Category deleted.", reply_markup=kb_manage_shop())
        return

    if cb.startswith("DEL_PROD:") and can_use_admin_panel(uid):
        shop = admin_context_shop(uid); assert shop is not None
        pid = int(cb.split(":")[1])
        conn = db(); cur = conn.cursor()
        cur.execute("DELETE FROM products WHERE shop_owner_id=? AND id=?", (shop, pid))
        conn.commit(); conn.close()
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("✅ Product deleted.", reply_markup=kb_manage_shop())
        return

    # Edit product fields
    if cb.startswith("EDIT_PROD_DESC:") and can_use_admin_panel(uid):
        pid = int(cb.split(":")[1])
        clear_mode(context)
        set_mode(context, "EDIT_PROD_DESC", {"shop_owner_id": admin_context_shop(uid), "pid": pid})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("✏️ Send new product description (or send '-' to clear).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")]]))
        return

    if cb.startswith("EDIT_PROD_KEY:") and can_use_admin_panel(uid):
        pid = int(cb.split(":")[1])
        clear_mode(context)
        set_mode(context, "EDIT_PROD_KEY", {"shop_owner_id": admin_context_shop(uid), "pid": pid})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("🔑 Send new Key Text (or '-' to clear).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")]]))
        return

    if cb.startswith("EDIT_PROD_LINK:") and can_use_admin_panel(uid):
        pid = int(cb.split(":")[1])
        clear_mode(context)
        set_mode(context, "EDIT_PROD_LINK", {"shop_owner_id": admin_context_shop(uid), "pid": pid})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("🔗 Send new Telegram link (must be a valid https://t.me/... link) (or '-' to clear).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")]]))
        return

    # -------------------------
    # Admin Panel: Deposits / Tickets / Users
    # -------------------------
    if cb == "M_DEPOSITS":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid); assert shop is not None
        await delete_callback_message(update, context)

        conn = db(); cur = conn.cursor()
        cur.execute("SELECT * FROM deposit_requests WHERE shop_owner_id=? AND status='pending' ORDER BY id DESC LIMIT 30", (shop,))
        deps = cur.fetchall()
        conn.close()

        if not deps:
            await update.effective_chat.send_message("No pending deposits.", reply_markup=kb_back_main())
            return

        for d in deps:
            dep_id = int(d["id"])
            duid = int(d["user_id"])
            amt = float(d["amount"])
            cap = (
                f"💳 <b>Deposit Request</b>\n"
                f"User: <b>{esc(user_display(duid))}</b>\n"
                f"Telegram ID: <code>{duid}</code>\n"
                f"Amount: <b>{money(amt)} {esc(CURRENCY)}</b>\n"
                f"Request ID: <b>#{dep_id}</b>"
            )
            try:
                await update.effective_chat.send_photo(photo=d["proof_file_id"], caption=cap, parse_mode=ParseMode.HTML, reply_markup=kb_dep_approve(dep_id))
            except Exception:
                await update.effective_chat.send_message(cap, parse_mode=ParseMode.HTML, reply_markup=kb_dep_approve(dep_id))
        return

    if cb.startswith("DEP_OK:") or cb.startswith("DEP_NO:"):
        dep_id = int(cb.split(":")[1])
        approve = cb.startswith("DEP_OK:")

        conn = db(); cur = conn.cursor()
        cur.execute("SELECT * FROM deposit_requests WHERE id=?", (dep_id,))
        dep = cur.fetchone()
        if not dep:
            conn.close()
            await q.answer("Not found.", show_alert=True)
            await delete_callback_message(update, context)
            return

        shop = int(dep["shop_owner_id"])
        dep_user = int(dep["user_id"])
        amt = float(dep["amount"])
        status = dep["status"]

        # permission: main deposits -> super admin; seller deposits -> that seller
        if shop == SUPER_ADMIN_ID:
            if not is_super(uid):
                conn.close()
                await q.answer("Not allowed.", show_alert=True)
                return
        else:
            if uid != shop:
                conn.close()
                await q.answer("Not allowed.", show_alert=True)
                return

        if status != "pending":
            conn.close()
            await q.answer("Already handled.", show_alert=True)
            await delete_callback_message(update, context)
            return

        new_status = "approved" if approve else "rejected"
        cur.execute("UPDATE deposit_requests SET status=?, handled_by=?, handled_at=? WHERE id=?", (new_status, uid, ts(), dep_id))
        conn.commit()
        conn.close()

        if approve:
            nb = add_balance(shop, dep_user, amt)
            log_tx(shop, dep_user, "deposit", amt, "Deposit approved")
            try:
                await context.application.bot.send_message(
                    chat_id=dep_user,
                    text=(
                        f"✅ <b>Deposit Approved</b>\n"
                        f"Deposited: <b>{money(amt)} {esc(CURRENCY)}</b>\n"
                        f"Total Balance: <b>{money(nb)} {esc(CURRENCY)}</b>"
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        else:
            try:
                await context.application.bot.send_message(
                    chat_id=dep_user,
                    text=f"❌ <b>Deposit Rejected</b>\nAmount: <b>{money(amt)} {esc(CURRENCY)}</b>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        # REQUIRED: delete approval message after action
        await delete_callback_message(update, context)
        return

    if cb == "M_TICKETS":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid); assert shop is not None
        await delete_callback_message(update, context)

        tickets = list_open_tickets(shop)
        if not tickets:
            await update.effective_chat.send_message("No open tickets.", reply_markup=kb_back_main())
            return

        rows: List[List[InlineKeyboardButton]] = []
        for t in tickets[:60]:
            tu = int(t["user_id"])
            rows.append([InlineKeyboardButton(f"🆘 {user_display(tu)} (#{t['id']})", callback_data=f"TICKET:{t['id']}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ADMIN_PANEL")])
        await update.effective_chat.send_message("🆘 <b>Support Inbox</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb.startswith("TICKET:"):
        tid = int(cb.split(":")[1])
        t = ticket_info(tid)
        if not t:
            await q.answer("Not found.", show_alert=True)
            return

        shop = int(t["shop_owner_id"])
        # permission
        if shop == SUPER_ADMIN_ID:
            if not is_super(uid):
                await q.answer("Not allowed.", show_alert=True)
                return
        else:
            if uid != shop:
                await q.answer("Not allowed.", show_alert=True)
                return

        msgs = ticket_last_msgs(tid, 8)
        tu = int(t["user_id"])
        lines = [f"🆘 <b>Ticket #{tid}</b>\nUser: <b>{esc(user_display(tu))}</b>\nTelegram ID: <code>{tu}</code>\n"]
        for m in reversed(msgs):
            who = "User" if int(m["sender_id"]) == tu else "Support"
            lines.append(f"<b>{who}:</b> {esc(m['text'])}")

        await delete_callback_message(update, context)
        await update.effective_chat.send_message(
            "\n\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✍️ Reply", callback_data=f"TICKET_REPLY:{tid}")],
                [InlineKeyboardButton("✅ Close", callback_data=f"TICKET_CLOSE:{tid}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="M_TICKETS")],
            ]),
        )
        return

    if cb.startswith("TICKET_REPLY:"):
        tid = int(cb.split(":")[1])
        t = ticket_info(tid)
        if not t:
            await q.answer("Not found.", show_alert=True)
            return

        shop = int(t["shop_owner_id"])
        if shop == SUPER_ADMIN_ID:
            if not is_super(uid):
                await q.answer("Not allowed.", show_alert=True)
                return
        else:
            if uid != shop:
                await q.answer("Not allowed.", show_alert=True)
                return

        clear_mode(context)
        set_mode(context, "SUPPORT_REPLY_DRAFT", {"ticket_id": tid})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message(
            "✍️ <b>Reply</b>\nSend your message(s), then press ✅ Done.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_done_cancel("REPLY"),
        )
        return

    if cb in {"REPLY_DONE", "REPLY_CANCEL"}:
        if mode(context) != "SUPPORT_REPLY_DRAFT":
            await q.answer("No active reply.", show_alert=True)
            return
        if cb == "REPLY_CANCEL":
            clear_mode(context)
            await delete_callback_message(update, context)
            await update.effective_chat.send_message("Canceled.", reply_markup=kb_back_main())
            return

        text = draft_text(context)
        if not text:
            await q.answer("Send a message first.", show_alert=True)
            return

        tid = int(data(context).get("ticket_id", 0))
        t = ticket_info(tid)
        if not t:
            clear_mode(context)
            await q.answer("Ticket not found.", show_alert=True)
            return

        shop = int(t["shop_owner_id"])
        tu = int(t["user_id"])

        # permission
        if shop == SUPER_ADMIN_ID:
            if not is_super(uid):
                await q.answer("Not allowed.", show_alert=True)
                return
        else:
            if uid != shop:
                await q.answer("Not allowed.", show_alert=True)
                return

        add_ticket_msg(tid, uid, text)

        # send to user
        try:
            await context.application.bot.send_message(
                chat_id=tu,
                text=f"🆘 <b>Support Reply</b>\n\n{esc(text)}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        clear_mode(context)
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("✅ Reply sent.", reply_markup=kb_back_main())
        return

    if cb.startswith("TICKET_CLOSE:"):
        tid = int(cb.split(":")[1])
        t = ticket_info(tid)
        if not t:
            await q.answer("Not found.", show_alert=True)
            return
        shop = int(t["shop_owner_id"])
        tu = int(t["user_id"])

        if shop == SUPER_ADMIN_ID:
            if not is_super(uid):
                await q.answer("Not allowed.", show_alert=True)
                return
        else:
            if uid != shop:
                await q.answer("Not allowed.", show_alert=True)
                return

        close_ticket(tid)
        await delete_callback_message(update, context)
        try:
            await context.application.bot.send_message(chat_id=tu, text="✅ Your support ticket has been closed.")
        except Exception:
            pass
        return

    if cb == "M_USERS":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid); assert shop is not None
        await delete_callback_message(update, context)

        conn = db(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM balances WHERE shop_owner_id=? ORDER BY user_id DESC LIMIT 80", (shop,))
        rows = cur.fetchall()
        conn.close()
        users = [int(r["user_id"]) for r in rows]

        if not users:
            await update.effective_chat.send_message("No users yet.", reply_markup=kb_back_main())
            return

        await update.effective_chat.send_message(
            "👥 <b>Users</b>\n(Click a user to manage)",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_users_manage(shop, users),
        )
        return

    if cb.startswith("M_USER:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid); assert shop is not None
        target = int(cb.split(":")[1])
        bal = get_balance(shop, target)
        await delete_callback_message(update, context)
        await update.effective_chat.send_message(
            f"👤 <b>{esc(user_display(target))}</b>\nTelegram ID: <code>{target}</code>\nBalance: <b>{money(bal)} {esc(CURRENCY)}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Edit Balance", callback_data=f"M_EDIT_BAL:{target}")],
                [InlineKeyboardButton("📜 History", callback_data=f"M_HIST:{target}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="M_USERS")],
            ]),
        )
        return

    if cb.startswith("M_EDIT_BAL:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(cb.split(":")[1])
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("💰 Choose adjustment:", reply_markup=kb_user_balance_buttons("M_BAL_ADD", target))
        return

    if cb.startswith("M_BAL_ADD:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        _, target, delta = cb.split(":")
        target = int(target); delta = float(delta)
        shop = admin_context_shop(uid); assert shop is not None
        nb = add_balance(shop, target, delta)
        log_tx(shop, target, "adjust", delta, f"Adjusted by {uid}")
        await delete_callback_message(update, context)
        await update.effective_chat.send_message(f"✅ Updated.\nNew balance: {money(nb)} {CURRENCY}", reply_markup=kb_back_main())
        return

    if cb.startswith("M_HIST:"):
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(cb.split(":")[1])
        shop = admin_context_shop(uid); assert shop is not None

        conn = db(); cur = conn.cursor()
        cur.execute("SELECT * FROM transactions WHERE shop_owner_id=? AND user_id=? ORDER BY id DESC LIMIT 20", (shop, target))
        txs = cur.fetchall()
        conn.close()

        bal = get_balance(shop, target)
        lines = [f"📜 <b>History</b> — <b>{esc(user_display(target))}</b>\nTotal Balance: <b>{money(bal)} {esc(CURRENCY)}</b>\n"]
        if not txs:
            lines.append("No history.")
        else:
            for t in txs:
                kind = t["kind"]
                amt = float(t["amount"])
                note = (t["note"] or "").strip()
                if kind == "deposit":
                    lines.append(f"Deposited: <b>{money(amt)} {esc(CURRENCY)}</b>")
                elif kind == "purchase":
                    lines.append(f"Purchased: <b>{esc(note.replace('Purchased: ', ''))}</b>\nPaid: <b>{money(abs(amt))} {esc(CURRENCY)}</b>")
                elif kind == "adjust":
                    sign = "+" if amt >= 0 else ""
                    lines.append(f"Balance Adjusted: <b>{sign}{money(amt)} {esc(CURRENCY)}</b>")
                elif kind == "seller_sub":
                    lines.append(f"Seller Subscription: <b>{money(amt)} {esc(CURRENCY)}</b>")
                else:
                    sign = "+" if amt >= 0 else ""
                    lines.append(f"{esc(kind)}: <b>{sign}{money(amt))} {esc(CURRENCY)}</b>")
                lines.append("")
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("\n".join(lines).strip(), parse_mode=ParseMode.HTML, reply_markup=kb_back_main())
        return

    # Admin: wallet & welcome settings
    if cb == "M_SET_WALLET":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid); assert shop is not None
        clear_mode(context)
        set_mode(context, "SET_WALLET_ADDR", {"shop_owner_id": shop})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("💳 Send wallet address text (any format).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")]]))
        return

    if cb == "M_SET_WALLETMSG":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid); assert shop is not None
        clear_mode(context)
        set_mode(context, "SET_WALLET_MSG", {"shop_owner_id": shop})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("📝 Send wallet message text.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")]]))
        return

    if cb == "M_SET_WELCOME":
        if not can_use_admin_panel(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        shop = admin_context_shop(uid); assert shop is not None
        clear_mode(context)
        set_mode(context, "SET_WELCOME", {"shop_owner_id": shop})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message(
            "🖼 Send welcome:\n1) Text message OR\n2) Photo/Video with caption.\n\n(For seller shops: no RekkoOwn footer)\n(For main shop: you can include anything)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")]]),
        )
        return

    if cb == "M_SHARE":
        # seller only
        if not (seller_shop_active(uid) and get_session(uid)[0] == uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        await delete_callback_message(update, context)
        link = f"https://t.me/{context.application.bot.username}?start=s_{uid}"
        await update.effective_chat.send_message(
            f"📣 <b>Share My Shop</b>\n\nSend this link to customers:\n{esc(link)}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_back_main(),
        )
        return

    # -------------------------
    # Super Admin: lists + search + actions
    # -------------------------
    if cb == "SA_SELLERS":
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        await delete_callback_message(update, context)
        conn = db(); cur = conn.cursor()
        cur.execute("SELECT seller_id FROM sellers ORDER BY seller_id DESC LIMIT 80")
        rows = cur.fetchall()
        conn.close()
        sellers = [int(r["seller_id"]) for r in rows]
        if not sellers:
            await update.effective_chat.send_message("No sellers yet.", reply_markup=kb_sa_menu())
            return
        await update.effective_chat.send_message("🏪 <b>Sellers</b>", parse_mode=ParseMode.HTML, reply_markup=kb_sa_sellers_list(sellers))
        return

    if cb == "SA_USERS":
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        await delete_callback_message(update, context)
        conn = db(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM balances WHERE shop_owner_id=? ORDER BY user_id DESC LIMIT 120", (SUPER_ADMIN_ID,))
        rows = cur.fetchall()
        conn.close()
        users = [int(r["user_id"]) for r in rows]
        if not users:
            await update.effective_chat.send_message("No main-shop users yet.", reply_markup=kb_sa_menu())
            return
        await update.effective_chat.send_message("👥 <b>Main Shop Users</b>", parse_mode=ParseMode.HTML, reply_markup=kb_sa_users_list(users))
        return

    if cb == "SA_SEARCH_SELLER":
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        clear_mode(context)
        set_mode(context, "SA_SEARCH_SELLER", {})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("🔎 Send seller username (example: @name).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="SA_MENU")]]))
        return

    if cb == "SA_SEARCH_USER":
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        clear_mode(context)
        set_mode(context, "SA_SEARCH_USER", {})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("🔎 Send user username (example: @name).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="SA_MENU")]]))
        return

    if cb == "SA_EDIT_SELLER_DESC":
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        clear_mode(context)
        set_mode(context, "SA_EDIT_SELLER_DESC", {})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("📝 Send the new Become Seller description (HTML supported).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="SA_MENU")]]))
        return

    if cb.startswith("SA_SELLER:"):
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        sid = int(cb.split(":")[1])
        r = seller_row(sid)
        if not r:
            await q.answer("Seller not found.", show_alert=True)
            return

        days_left = max(0, (int(r["sub_until"] or 0) - ts()) // 86400)
        restr_left = max(0, (int(r["restricted_until"] or 0) - ts()) // 86400)

        txt = (
            f"🏪 <b>{esc(user_display(sid))}</b>\n"
            f"Seller ID: <code>{sid}</code>\n"
            f"Days Left: <b>{days_left}</b>\n"
            f"Restricted Days Left: <b>{restr_left}</b>\n"
            f"Banned Shop: <b>{'YES' if int(r['banned_shop'] or 0) else 'NO'}</b>\n"
            f"Banned Panel: <b>{'YES' if int(r['banned_panel'] or 0) else 'NO'}</b>\n"
        )
        await delete_callback_message(update, context)
        await update.effective_chat.send_message(txt, parse_mode=ParseMode.HTML, reply_markup=kb_sa_seller_actions(sid))
        return

    if cb.startswith("SA_USER:"):
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(cb.split(":")[1])
        # must be main shop user (privacy)
        conn = db(); cur = conn.cursor()
        cur.execute("SELECT 1 FROM balances WHERE shop_owner_id=? AND user_id=? LIMIT 1", (SUPER_ADMIN_ID, target))
        ok = cur.fetchone() is not None
        conn.close()
        if not ok:
            await q.answer("Not a main-shop user.", show_alert=True)
            return

        bal = get_balance(SUPER_ADMIN_ID, target)
        txt = f"👤 <b>{esc(user_display(target))}</b>\nTelegram ID: <code>{target}</code>\nBalance: <b>{money(bal)} {esc(CURRENCY)}</b>"
        await delete_callback_message(update, context)
        await update.effective_chat.send_message(txt, parse_mode=ParseMode.HTML, reply_markup=kb_sa_user_actions(target))
        return

    if cb.startswith("SA_BAN_USER:") or cb.startswith("SA_UNBAN_USER:"):
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(cb.split(":")[1])
        set_ban(SUPER_ADMIN_ID, target, 1 if cb.startswith("SA_BAN_USER:") else 0)
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("✅ Updated.", reply_markup=kb_back_main())
        return

    if cb.startswith("SA_EDIT_BAL:"):
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(cb.split(":")[1])
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("💰 Choose adjustment:", reply_markup=kb_user_balance_buttons("SA_BAL_ADD", target))
        return

    if cb.startswith("SA_BAL_ADD:"):
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        _, target, delta = cb.split(":")
        target = int(target); delta = float(delta)
        # main shop only
        nb = add_balance(SUPER_ADMIN_ID, target, delta)
        log_tx(SUPER_ADMIN_ID, target, "adjust", delta, "Adjusted by Super Admin")
        await delete_callback_message(update, context)
        await update.effective_chat.send_message(f"✅ Updated.\nNew balance: {money(nb)} {CURRENCY}", reply_markup=kb_back_main())
        return

    if cb.startswith("SA_HIST:"):
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(cb.split(":")[1])
        conn = db(); cur = conn.cursor()
        cur.execute("SELECT * FROM transactions WHERE shop_owner_id=? AND user_id=? ORDER BY id DESC LIMIT 20", (SUPER_ADMIN_ID, target))
        txs = cur.fetchall()
        conn.close()

        bal = get_balance(SUPER_ADMIN_ID, target)
        lines = [f"📜 <b>History</b> — <b>{esc(user_display(target))}</b>\nTotal Balance: <b>{money(bal)} {esc(CURRENCY)}</b>\n"]
        if not txs:
            lines.append("No history.")
        else:
            for t in txs:
                kind = t["kind"]
                amt = float(t["amount"])
                note = (t["note"] or "").strip()
                if kind == "deposit":
                    lines.append(f"Deposited: <b>{money(amt)} {esc(CURRENCY)}</b>")
                elif kind == "purchase":
                    lines.append(f"Purchased: <b>{esc(note.replace('Purchased: ', ''))}</b>\nPaid: <b>{money(abs(amt))} {esc(CURRENCY)}</b>")
                elif kind == "adjust":
                    sign = "+" if amt >= 0 else ""
                    lines.append(f"Balance Adjusted: <b>{sign}{money(amt)} {esc(CURRENCY)}</b>")
                elif kind == "seller_sub":
                    lines.append(f"Seller Subscription: <b>{money(amt)} {esc(CURRENCY)}</b>")
                else:
                    sign = "+" if amt >= 0 else ""
                    lines.append(f"{esc(kind)}: <b>{sign}{money(amt)} {esc(CURRENCY)}</b>")
                lines.append("")
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("\n".join(lines).strip(), parse_mode=ParseMode.HTML, reply_markup=kb_back_main())
        return

    if cb.startswith("SA_ADD_DAYS:") or cb.startswith("SA_RESTRICT:") or cb.startswith("SA_BAN_SHOP:") or cb.startswith("SA_UNBAN_SHOP:") or cb.startswith("SA_BAN_PANEL:") or cb.startswith("SA_UNBAN_PANEL:"):
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return

        parts = cb.split(":")
        cmd = parts[0]
        sid = int(parts[1])

        conn = db(); cur = conn.cursor()
        cur.execute("SELECT * FROM sellers WHERE seller_id=?", (sid,))
        r = cur.fetchone()
        if not r:
            conn.close()
            await q.answer("Seller not found.", show_alert=True)
            return

        now = ts()
        if cmd == "SA_ADD_DAYS":
            days = int(parts[2])
            old = int(r["sub_until"] or 0)
            new_until = (old if old > now else now) + days * 86400
            cur.execute("UPDATE sellers SET sub_until=? WHERE seller_id=?", (new_until, sid))
        elif cmd == "SA_RESTRICT":
            days = int(parts[2])
            cur.execute("UPDATE sellers SET restricted_until=? WHERE seller_id=?", (now + days * 86400, sid))
        elif cmd == "SA_BAN_SHOP":
            cur.execute("UPDATE sellers SET banned_shop=1 WHERE seller_id=?", (sid,))
        elif cmd == "SA_UNBAN_SHOP":
            cur.execute("UPDATE sellers SET banned_shop=0 WHERE seller_id=?", (sid,))
        elif cmd == "SA_BAN_PANEL":
            cur.execute("UPDATE sellers SET banned_panel=1 WHERE seller_id=?", (sid,))
        elif cmd == "SA_UNBAN_PANEL":
            cur.execute("UPDATE sellers SET banned_panel=0 WHERE seller_id=?", (sid,))
        conn.commit(); conn.close()

        await delete_callback_message(update, context)
        await update.effective_chat.send_message("✅ Updated.", reply_markup=kb_back_main())
        return

    if cb.startswith("SA_SELLER_BAL:"):
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        sid = int(cb.split(":")[1])
        clear_mode(context)
        set_mode(context, "SA_SET_SELLER_BAL", {"seller_id": sid})
        await delete_callback_message(update, context)
        await update.effective_chat.send_message("💰 Send new seller balance amount (number).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="SA_MENU")]]))
        return

    if cb.startswith("SA_USER_TICKETS:"):
        if not is_super(uid):
            await q.answer("Not allowed.", show_alert=True)
            return
        target = int(cb.split(":")[1])
        # main shop only
        tid = get_open_ticket(SUPER_ADMIN_ID, target)
        await delete_callback_message(update, context)
        if not tid:
            await update.effective_chat.send_message("No open tickets for this user.", reply_markup=kb_back_main())
            return
        # open ticket view
        msgs = ticket_last_msgs(tid, 8)
        lines = [f"🆘 <b>Ticket #{tid}</b>\nUser: <b>{esc(user_display(target))}</b>\nTelegram ID: <code>{target}</code>\n"]
        for m in reversed(msgs):
            who = "User" if int(m["sender_id"]) == target else "Support"
            lines.append(f"<b>{who}:</b> {esc(m['text'])}")
        await update.effective_chat.send_message(
            "\n\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✍️ Reply", callback_data=f"TICKET_REPLY:{tid}")],
                [InlineKeyboardButton("✅ Close", callback_data=f"TICKET_CLOSE:{tid}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="SA_MENU")],
            ]),
        )
        return

    # Unknown
    await q.answer("Unknown action.", show_alert=True)


# -------------------------
# Handler: messages (modes)
# -------------------------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    upsert_user(u)
    uid = u.id
    m = mode(context)

    # Support draft (user)
    if m == "SUPPORT_DRAFT":
        if update.message and update.message.text:
            draft_add(context, update.message.text.strip())
            await update.effective_chat.send_message("✅ Added. You can send more, then press ✅ Done.")
        return

    # Deposit amount
    if m == "DEP_AMOUNT":
        if not (update.message and update.message.text):
            return
        s = update.message.text.strip()
        try:
            amt = float(s)
            if amt <= 0:
                raise ValueError
        except ValueError:
            await update.effective_chat.send_message("❌ Invalid amount. Send a number like 10.")
            return

        shop_owner_id = int(data(context).get("shop_owner_id", SUPER_ADMIN_ID))
        set_mode(context, "DEP_PROOF", {"shop_owner_id": shop_owner_id, "amount": amt})
        await update.effective_chat.send_message("📸 Now send a <b>PHOTO proof</b>.", parse_mode=ParseMode.HTML)
        return

    # Deposit proof
    if m == "DEP_PROOF":
        if not update.message:
            return
        if not update.message.photo:
            await update.effective_chat.send_message("❌ You must send a <b>PHOTO proof</b>.", parse_mode=ParseMode.HTML)
            return
        shop_owner_id = int(data(context).get("shop_owner_id", SUPER_ADMIN_ID))
        amt = float(data(context).get("amount", 0))

        proof_id = update.message.photo[-1].file_id

        conn = db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO deposit_requests(shop_owner_id, user_id, amount, proof_file_id, status, created_at) VALUES(?,?,?,?,?,?)",
            (shop_owner_id, uid, amt, proof_id, "pending", ts()),
        )
        dep_id = int(cur.lastrowid)
        conn.commit(); conn.close()

        # Notify approver only (shop owner)
        try:
            await context.application.bot.send_photo(
                chat_id=shop_owner_id,
                photo=proof_id,
                caption=(
                    f"💳 <b>Deposit Request</b>\n"
                    f"Shop: <b>{'Main Shop' if is_main_shop(shop_owner_id) else 'Seller Shop'}</b>\n"
                    f"User: <b>{esc(user_display(uid))}</b>\n"
                    f"Telegram ID: <code>{uid}</code>\n"
                    f"Amount: <b>{money(amt)} {esc(CURRENCY)}</b>\n"
                    f"Request ID: <b>#{dep_id}</b>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_dep_approve(dep_id),
            )
        except Exception:
            try:
                await context.application.bot.send_message(
                    chat_id=shop_owner_id,
                    text=(
                        f"💳 <b>Deposit Request</b>\n"
                        f"User: <b>{esc(user_display(uid))}</b>\n"
                        f"Telegram ID: <code>{uid}</code>\n"
                        f"Amount: <b>{money(amt)} {esc(CURRENCY)}</b>\n"
                        f"Request ID: <b>#{dep_id}</b>"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_dep_approve(dep_id),
                )
            except Exception:
                pass

        clear_mode(context)
        await update.effective_chat.send_message("✅ Deposit request sent. Please wait for approval.", reply_markup=kb_back_main())
        return

    # Admin settings: wallet/wallet msg
    if m == "SET_WALLET_ADDR":
        if update.message and update.message.text:
            shop = int(data(context).get("shop_owner_id", SUPER_ADMIN_ID))
            set_shop_setting(shop, "wallet_address", update.message.text.strip())
            clear_mode(context)
            await update.effective_chat.send_message("✅ Wallet address updated.", reply_markup=kb_back_main())
        return

    if m == "SET_WALLET_MSG":
        if update.message and update.message.text:
            shop = int(data(context).get("shop_owner_id", SUPER_ADMIN_ID))
            set_shop_setting(shop, "wallet_message", update.message.text.strip())
            clear_mode(context)
            await update.effective_chat.send_message("✅ Wallet message updated.", reply_markup=kb_back_main())
        return

    # Welcome message (text OR photo/video with caption)
    if m == "SET_WELCOME":
        shop = int(data(context).get("shop_owner_id", SUPER_ADMIN_ID))
        text = ""
        fid = ""
        ftype = ""

        if not update.message:
            return

        if update.message.photo:
            fid = update.message.photo[-1].file_id
            ftype = "photo"
            text = (update.message.caption or "").strip()
        elif update.message.video:
            fid = update.message.video.file_id
            ftype = "video"
            text = (update.message.caption or "").strip()
        elif update.message.text:
            text = update.message.text.strip()
        else:
            await update.effective_chat.send_message("Send text or photo/video.")
            return

        if text:
            set_shop_setting(shop, "welcome_text", text)
        if fid:
            set_shop_setting(shop, "welcome_file_id", fid)
            set_shop_setting(shop, "welcome_file_type", ftype)

        clear_mode(context)
        await update.effective_chat.send_message("✅ Welcome updated.", reply_markup=kb_back_main())
        return

    # Super admin edit seller desc
    if m == "SA_EDIT_SELLER_DESC":
        if is_super(uid) and update.message and update.message.text:
            set_shop_setting(SUPER_ADMIN_ID, "seller_desc", update.message.text.strip())
            clear_mode(context)
            await update.effective_chat.send_message("✅ Become Seller description updated.", reply_markup=kb_back_main())
        return

    # Super admin set seller balance
    if m == "SA_SET_SELLER_BAL":
        if not (is_super(uid) and update.message and update.message.text):
            return
        sid = int(data(context).get("seller_id", 0))
        try:
            val = float(update.message.text.strip())
            if val < 0:
                val = 0.0
        except ValueError:
            await update.effective_chat.send_message("❌ Send a number.")
            return
        conn = db(); cur = conn.cursor()
        cur.execute("UPDATE sellers SET balance=? WHERE seller_id=?", (val, sid))
        conn.commit(); conn.close()
        clear_mode(context)
        await update.effective_chat.send_message("✅ Seller balance updated.", reply_markup=kb_back_main())
        return

    # Super admin search seller/user
    if m == "SA_SEARCH_SELLER":
        if not (is_super(uid) and update.message and update.message.text):
            return
        q = update.message.text.strip().lstrip("@").lower()
        conn = db(); cur = conn.cursor()
        cur.execute("SELECT seller_id FROM sellers")
        ids = [int(r["seller_id"]) for r in cur.fetchall()]
        conn.close()
        matches = []
        for sid in ids:
            ud = user_display(sid).lower().lstrip("@")
            if q in ud or q == str(sid):
                matches.append(sid)
        clear_mode(context)
        if not matches:
            await update.effective_chat.send_message("No matches.", reply_markup=kb_sa_menu())
            return
        await update.effective_chat.send_message("🔎 <b>Matches</b>", parse_mode=ParseMode.HTML, reply_markup=kb_sa_sellers_list(matches))
        return

    if m == "SA_SEARCH_USER":
        if not (is_super(uid) and update.message and update.message.text):
            return
        q = update.message.text.strip().lstrip("@").lower()
        conn = db(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM balances WHERE shop_owner_id=?", (SUPER_ADMIN_ID,))
        ids = [int(r["user_id"]) for r in cur.fetchall()]
        conn.close()
        matches = []
        for xid in ids:
            ud = user_display(xid).lower().lstrip("@")
            if q in ud or q == str(xid):
                matches.append(xid)
        clear_mode(context)
        if not matches:
            await update.effective_chat.send_message("No matches.", reply_markup=kb_sa_menu())
            return
        await update.effective_chat.send_message("🔎 <b>Matches</b>", parse_mode=ParseMode.HTML, reply_markup=kb_sa_users_list(matches))
        return

    # Admin: creation/edit flows
    if m == "ADD_CAT_NAME":
        if update.message and update.message.text:
            shop = int(data(context).get("shop_owner_id", SUPER_ADMIN_ID))
            name = update.message.text.strip()
            set_mode(context, "ADD_CAT_DESC_CHOICE", {"shop_owner_id": shop, "name": name})
            await update.effective_chat.send_message("Add description?", reply_markup=kb_yes_no_desc("CAT"))
        return

    if m == "ADD_CAT_DESC":
        if update.message and update.message.text:
            dct = data(context)
            shop = int(dct["shop_owner_id"]); name = dct["name"]
            desc = update.message.text.strip()
            set_mode(context, "ADD_CAT_MEDIA", {"shop_owner_id": shop, "name": name, "description": desc})
            await update.effective_chat.send_message("Send photo/video for category (optional) or press Skip.", reply_markup=kb_skip_media("CAT"))
        return

    if m == "ADD_CAT_MEDIA":
        # expects media OR user might send anything and we treat as skip
        dct = data(context)
        shop = int(dct["shop_owner_id"]); name = dct["name"]; desc = dct.get("description", "") or ""
        fid = ""; ftype = ""

        if update.message:
            if update.message.photo:
                fid = update.message.photo[-1].file_id
                ftype = "photo"
            elif update.message.video:
                fid = update.message.video.file_id
                ftype = "video"

        conn = db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO categories(shop_owner_id, name, description, file_id, file_type) VALUES(?,?,?,?,?)",
            (shop, name, desc, fid, ftype),
        )
        conn.commit(); conn.close()

        clear_mode(context)
        await update.effective_chat.send_message("✅ Category created.", reply_markup=kb_back_main())
        return

    if m == "ADD_COCAT_NAME":
        if update.message and update.message.text:
            dct = data(context)
            shop = int(dct["shop_owner_id"]); cat_id = int(dct["category_id"])
            name = update.message.text.strip()
            set_mode(context, "ADD_COCAT_DESC_CHOICE", {"shop_owner_id": shop, "category_id": cat_id, "name": name})
            await update.effective_chat.send_message("Add description?", reply_markup=kb_yes_no_desc("COCAT"))
        return

    if m == "ADD_COCAT_DESC":
        if update.message and update.message.text:
            dct = data(context)
            shop = int(dct["shop_owner_id"]); cat_id = int(dct["category_id"]); name = dct["name"]
            desc = update.message.text.strip()
            set_mode(context, "ADD_COCAT_MEDIA", {"shop_owner_id": shop, "category_id": cat_id, "name": name, "description": desc})
            await update.effective_chat.send_message("Send photo/video for co-category (optional) or press Skip.", reply_markup=kb_skip_media("COCAT"))
        return

    if m == "ADD_COCAT_MEDIA":
        dct = data(context)
        shop = int(dct["shop_owner_id"]); cat_id = int(dct["category_id"]); name = dct["name"]; desc = dct.get("description", "") or ""
        fid = ""; ftype = ""
        if update.message:
            if update.message.photo:
                fid = update.message.photo[-1].file_id
                ftype = "photo"
            elif update.message.video:
                fid = update.message.video.file_id
                ftype = "video"
        conn = db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO cocategories(shop_owner_id, category_id, name, description, file_id, file_type) VALUES(?,?,?,?,?,?)",
            (shop, cat_id, name, desc, fid, ftype),
        )
        conn.commit(); conn.close()
        clear_mode(context)
        await update.effective_chat.send_message("✅ Co-Category created.", reply_markup=kb_back_main())
        return

    if m == "ADD_PROD_NAME":
        if update.message and update.message.text:
            dct = data(context)
            name = update.message.text.strip()
            dct["name"] = name
            set_mode(context, "ADD_PROD_PRICE", dct)
            await update.effective_chat.send_message("Send product price (example: 10).")
        return

    if m == "ADD_PROD_PRICE":
        if update.message and update.message.text:
            s = update.message.text.strip()
            try:
                price = float(s)
                if price <= 0:
                    raise ValueError
            except ValueError:
                await update.effective_chat.send_message("❌ Invalid price. Send a number like 10.")
                return
            dct = data(context)
            dct["price"] = price
            set_mode(context, "ADD_PROD_DESC_CHOICE", dct)
            await update.effective_chat.send_message("Add description?", reply_markup=kb_yes_no_desc("PROD"))
        return

    if m == "ADD_PROD_DESC":
        if update.message and update.message.text:
            dct = data(context)
            dct["description"] = update.message.text.strip()
            set_mode(context, "ADD_PROD_MEDIA", dct)
            await update.effective_chat.send_message("Send product photo/video (optional) or press Skip.", reply_markup=kb_skip_media("PROD"))
        return

    if m == "ADD_PROD_MEDIA":
        dct = data(context)
        fid = ""; ftype = ""
        if update.message:
            if update.message.photo:
                fid = update.message.photo[-1].file_id
                ftype = "photo"
            elif update.message.video:
                fid = update.message.video.file_id
                ftype = "video"
        dct["file_id"] = fid
        dct["file_type"] = ftype
        set_mode(context, "ADD_PROD_KEY", dct)
        await update.effective_chat.send_message("🔑 Send product Key Text (or '-' for none).")
        return

    if m == "ADD_PROD_KEY":
        if update.message and update.message.text:
            dct = data(context)
            key = update.message.text.strip()
            dct["key_text"] = "" if key == "-" else key
            set_mode(context, "ADD_PROD_LINK", dct)
            await update.effective_chat.send_message("🔗 Send product Telegram link (https://t.me/...) (or '-' for none).")
        return

    if m == "ADD_PROD_LINK":
        if update.message and update.message.text:
            dct = data(context)
            link = update.message.text.strip()
            if link != "-" and not link.startswith("http"):
                await update.effective_chat.send_message("❌ Invalid link. Send a full https://t.me/... link or '-'.")
                return
            dct["tg_link"] = "" if link == "-" else link

            shop = int(dct["shop_owner_id"])
            cat_id = int(dct["category_id"])
            cocat_id = int(dct["cocategory_id"])
            name = dct["name"]
            price = float(dct["price"])
            desc = dct.get("description", "") or ""
            fid = dct.get("file_id", "") or ""
            ftype = dct.get("file_type", "") or ""
            key_text = dct.get("key_text", "") or ""
            tg_link = dct.get("tg_link", "") or ""

            conn = db(); cur = conn.cursor()
            cur.execute(
                "INSERT INTO products(shop_owner_id, category_id, cocategory_id, name, price, description, file_id, file_type, key_text, tg_link) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (shop, cat_id, cocat_id, name, price, desc, fid, ftype, key_text, tg_link),
            )
            conn.commit(); conn.close()

            clear_mode(context)
            await update.effective_chat.send_message("✅ Product created.", reply_markup=kb_back_main())
        return

    # Edit product fields
    if m in {"EDIT_PROD_DESC", "EDIT_PROD_KEY", "EDIT_PROD_LINK"}:
        if not (update.message and update.message.text):
            return
        dct = data(context)
        shop = int(dct.get("shop_owner_id", SUPER_ADMIN_ID))
        pid = int(dct.get("pid", 0))
        val = update.message.text.strip()
        if val == "-":
            val = ""

        if m == "EDIT_PROD_LINK" and val and not val.startswith("http"):
            await update.effective_chat.send_message("❌ Invalid link. Send full https://t.me/... or '-' to clear.")
            return

        field = "description" if m == "EDIT_PROD_DESC" else ("key_text" if m == "EDIT_PROD_KEY" else "tg_link")
        conn = db(); cur = conn.cursor()
        cur.execute(f"UPDATE products SET {field}=? WHERE shop_owner_id=? AND id=?", (val, shop, pid))
        conn.commit(); conn.close()

        clear_mode(context)
        await update.effective_chat.send_message("✅ Updated.", reply_markup=kb_back_main())
        return


# -------------------------
# Special callback: description/media choice for creation
# (Handled via a small extra callback handler inside on_button)
# -------------------------
async def on_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    cb = q.data or ""
    uid = update.effective_user.id

    # Category description choice
    if cb in {"CAT_DESC_Y", "CAT_DESC_N"} and mode(context) == "ADD_CAT_DESC_CHOICE":
        dct = data(context)
        if cb == "CAT_DESC_N":
            set_mode(context, "ADD_CAT_MEDIA", {"shop_owner_id": dct["shop_owner_id"], "name": dct["name"], "description": ""})
            await delete_callback_message(update, context)
            await update.effective_chat.send_message("Send photo/video for category (optional) or press Skip.", reply_markup=kb_skip_media("CAT"))
        else:
            set_mode(context, "ADD_CAT_DESC", dct)
            await delete_callback_message(update, context)
            await update.effective_chat.send_message("Send category description text.")
        return

    if cb == "CAT_SKIP_MEDIA" and mode(context) == "ADD_CAT_MEDIA":
        # finalize without media by simulating empty media
        await on_message(update, context)  # will insert with empty fid/ftype
        return

    # Co-category description choice
    if cb in {"COCAT_DESC_Y", "COCAT_DESC_N"} and mode(context) == "ADD_COCAT_DESC_CHOICE":
        dct = data(context)
        if cb == "COCAT_DESC_N":
            set_mode(context, "ADD_COCAT_MEDIA", {"shop_owner_id": dct["shop_owner_id"], "category_id": dct["category_id"], "name": dct["name"], "description": ""})
            await delete_callback_message(update, context)
            await update.effective_chat.send_message("Send photo/video for co-category (optional) or press Skip.", reply_markup=kb_skip_media("COCAT"))
        else:
            set_mode(context, "ADD_COCAT_DESC", dct)
            await delete_callback_message(update, context)
            await update.effective_chat.send_message("Send co-category description text.")
        return

    if cb == "COCAT_SKIP_MEDIA" and mode(context) == "ADD_COCAT_MEDIA":
        await on_message(update, context)
        return

    # Product description choice
    if cb in {"PROD_DESC_Y", "PROD_DESC_N"} and mode(context) == "ADD_PROD_DESC_CHOICE":
        dct = data(context)
        if cb == "PROD_DESC_N":
            set_mode(context, "ADD_PROD_MEDIA", {**dct, "description": ""})
            await delete_callback_message(update, context)
            await update.effective_chat.send_message("Send product photo/video (optional) or press Skip.", reply_markup=kb_skip_media("PROD"))
        else:
            set_mode(context, "ADD_PROD_DESC", dct)
            await delete_callback_message(update, context)
            await update.effective_chat.send_message("Send product description text.")
        return

    if cb == "PROD_SKIP_MEDIA" and mode(context) == "ADD_PROD_MEDIA":
        # continue without media
        await on_message(update, context)
        return

    # fallback: pass to main handler
    await on_button(update, context)


# -------------------------
# Main
# -------------------------
def main() -> None:
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # IMPORTANT: on_choice covers both choice callbacks + normal callbacks
    app.add_handler(CallbackQueryHandler(on_choice))

    # message handler
    app.add_handler(MessageHandler(filters.ALL, on_message))

    log.info("Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
