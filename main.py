# main.py
# RekkoShop Multi-Shop Telegram Store Bot (SQLite) — FULL WORKING CODE
# Features:
# - Platform shop (RekkoShop) + "Become Seller" subscription ($10 / 30 days) paid inside platform bot
# - Each seller gets their own shop: own admin panel, own wallet address, categories/subcategories/products/keys
# - Wallet + deposits (photo proof) -> owner gets APPROVE/REJECT buttons instantly
# - Purchases + History (keys delivered instantly)
# - Support chat (user -> shop owner) + owner reply
# - Users list + user count + edit user balance (super admin and sellers)
# - Broadcast (super admin to all users; seller to their shop users)
# - Shop share link button for sellers
# - Welcome media (photo/video) per shop + welcome text
# - Category/Subcategory disable/enable
# - Super admin: total users/sellers, suspend shop for custom days, ban shop permanently, edit Become Seller description
#
# Requires: python-telegram-bot==20.*
# Railway: set env BOT_TOKEN, ADMIN_ID (your telegram user id), optional DB_PATH, CURRENCY, USDT_TRC20_ADDRESS

import os
import sqlite3
import datetime
import hashlib
import traceback
from typing import Optional, List, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ===================== ENV =====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPER_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # YOU
DB_PATH = os.getenv("DB_PATH", "rekkoshop.db")
CURRENCY = os.getenv("CURRENCY", "USD")
PLATFORM_USDT_TRC20_ADDRESS = os.getenv("USDT_TRC20_ADDRESS", "").strip()

# Subscription / seller system
PANEL_PRICE_CENTS = 1000  # $10.00
PANEL_DAYS = 30

PAGE_SIZE = 8

DEFAULT_MAIN_SHOP_NAME = "RekkoShop"
DEFAULT_MAIN_WELCOME = "Welcome To RekkoShop , Receive your keys instantly here"
DEFAULT_BRAND = "Bot created by @RekkoOwn"


# ===================== TIME / MONEY =====================
def now_utc() -> datetime.datetime:
    return datetime.datetime.utcnow()

def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")

def parse_iso(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s)

def money(cents: int) -> str:
    return f"{cents/100:.2f} {CURRENCY}"

def to_cents(s: str) -> Optional[int]:
    try:
        v = float(s.strip().replace(",", "."))
        if v <= 0:
            return None
        return int(round(v * 100))
    except Exception:
        return None

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def is_super_admin(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID

def safe_username(u) -> Optional[str]:
    return (u.username or "").lower() if u.username else None

def rows(btns: List[InlineKeyboardButton], per_row: int = 2):
    return [btns[i:i+per_row] for i in range(0, len(btns), per_row)]

def days_left(until_iso: str) -> int:
    try:
        until = parse_iso(until_iso)
        secs = (until - now_utc()).total_seconds()
        if secs <= 0:
            return 0
        return int((secs + 86399) // 86400)
    except Exception:
        return 0


# ===================== DB =====================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        return col in cols
    except Exception:
        return False

def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    r = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(r)

def init_db():
    with db() as conn:
        # Core tables
        conn.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS shops(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            shop_name TEXT NOT NULL,
            welcome_text TEXT NOT NULL,
            welcome_media_file_id TEXT,
            welcome_media_type TEXT, -- 'photo'/'video'
            panel_until TEXT,
            is_suspended INTEGER NOT NULL DEFAULT 0,
            suspended_reason TEXT,
            suspended_until TEXT,
            created_at TEXT NOT NULL,
            wallet_address TEXT
        )
        """)

        # Users table (MIGRATION SAFE)
        if not _table_exists(conn, "users"):
            conn.execute("""
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                last_bot_msg_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
        else:
            # Add missing columns if older db
            if not _col_exists(conn, "users", "first_name"):
                conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
            if not _col_exists(conn, "users", "last_name"):
                conn.execute("ALTER TABLE users ADD COLUMN last_name TEXT")
            if not _col_exists(conn, "users", "last_bot_msg_id"):
                conn.execute("ALTER TABLE users ADD COLUMN last_bot_msg_id INTEGER")
            if not _col_exists(conn, "users", "created_at"):
                conn.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
            if not _col_exists(conn, "users", "updated_at"):
                conn.execute("ALTER TABLE users ADD COLUMN updated_at TEXT")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS shop_users(
            shop_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(shop_id, user_id)
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS categories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS subcategories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            subcategory_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            user_price_cents INTEGER NOT NULL,
            reseller_price_cents INTEGER NOT NULL,
            telegram_link TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS keys(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            key_text TEXT NOT NULL,
            is_used INTEGER NOT NULL DEFAULT 0,
            used_by INTEGER,
            used_at TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS purchases(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            key_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS deposits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL,
            photo_file_id TEXT NOT NULL,
            caption TEXT,
            status TEXT NOT NULL,            -- PENDING/APPROVED/REJECTED
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by INTEGER
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS support_msgs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        # Ensure main shop exists with id=1
        r = conn.execute("SELECT id FROM shops ORDER BY id ASC LIMIT 1").fetchone()
        if not r:
            conn.execute("""
            INSERT INTO shops(
              owner_id, shop_name, welcome_text, welcome_media_file_id, welcome_media_type,
              panel_until, is_suspended, suspended_reason, suspended_until, created_at, wallet_address
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (
                SUPER_ADMIN_ID,
                DEFAULT_MAIN_SHOP_NAME,
                DEFAULT_MAIN_WELCOME,
                None, None,
                None,
                0, None, None,
                now_iso(),
                PLATFORM_USDT_TRC20_ADDRESS or None
            ))

        # Ensure main shop owner always = SUPER_ADMIN_ID (so you never lose platform admin)
        conn.execute("UPDATE shops SET owner_id=? WHERE id=1", (SUPER_ADMIN_ID,))

        # Ensure RekkoShop wallet set if env provided
        if PLATFORM_USDT_TRC20_ADDRESS:
            conn.execute("UPDATE shops SET wallet_address=? WHERE id=1", (PLATFORM_USDT_TRC20_ADDRESS,))

        # Default Become Seller description editable
        if not conn.execute("SELECT 1 FROM settings WHERE key='panel_offer'").fetchone():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                ("panel_offer",
                 "⭐ Become a Seller ($10/month)\n\n"
                 "• Your own store inside this bot\n"
                 "• Your own wallet address\n"
                 "• Your own admin panel\n"
                 "• Your own categories / products / keys\n"
                 "• Broadcast to your own users\n\n"
                 "Renewals are paid in RekkoShop (platform) wallet.\n"
                 "If expired, your admin panel will be locked until renewed.")
            )


# ===================== USERS =====================
def upsert_user(u):
    uid = u.id
    uname = safe_username(u)
    fn = getattr(u, "first_name", None)
    ln = getattr(u, "last_name", None)
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone()
        if exists:
            conn.execute("""
            UPDATE users
            SET username=?, first_name=?, last_name=?, updated_at=?
            WHERE user_id=?
            """, (uname, fn, ln, now_iso(), uid))
        else:
            conn.execute("""
            INSERT INTO users(user_id,username,first_name,last_name,last_bot_msg_id,created_at,updated_at)
            VALUES(?,?,?,?,NULL,?,?)
            """, (uid, uname, fn, ln, now_iso(), now_iso()))

def get_last_bot_msg_id(uid: int) -> Optional[int]:
    with db() as conn:
        r = conn.execute("SELECT last_bot_msg_id FROM users WHERE user_id=?", (uid,)).fetchone()
        return int(r["last_bot_msg_id"]) if r and r["last_bot_msg_id"] else None

def set_last_bot_msg_id(uid: int, msg_id: Optional[int]):
    with db() as conn:
        conn.execute("UPDATE users SET last_bot_msg_id=? WHERE user_id=?", (msg_id, uid))

def total_users() -> int:
    with db() as conn:
        r = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return int(r["c"]) if r else 0


# ===================== SHOP USERS / BALANCE =====================
def ensure_shop_user(shop_id: int, uid: int):
    with db() as conn:
        r = conn.execute("SELECT 1 FROM shop_users WHERE shop_id=? AND user_id=?", (shop_id, uid)).fetchone()
        if not r:
            conn.execute("INSERT INTO shop_users(shop_id,user_id,balance_cents) VALUES(?,?,0)", (shop_id, uid))

def get_shop_user(shop_id: int, uid: int):
    ensure_shop_user(shop_id, uid)
    with db() as conn:
        return conn.execute("SELECT * FROM shop_users WHERE shop_id=? AND user_id=?", (shop_id, uid)).fetchone()

def get_balance(shop_id: int, uid: int) -> int:
    r = get_shop_user(shop_id, uid)
    return int(r["balance_cents"])

def add_balance_delta(shop_id: int, uid: int, delta_cents: int):
    ensure_shop_user(shop_id, uid)
    with db() as conn:
        conn.execute(
            "UPDATE shop_users SET balance_cents=balance_cents+? WHERE shop_id=? AND user_id=?",
            (delta_cents, shop_id, uid)
        )
        conn.execute(
            "UPDATE shop_users SET balance_cents=0 WHERE shop_id=? AND user_id=? AND balance_cents<0",
            (shop_id, uid)
        )

def set_balance_absolute(shop_id: int, uid: int, new_bal_cents: int):
    if new_bal_cents < 0:
        new_bal_cents = 0
    ensure_shop_user(shop_id, uid)
    with db() as conn:
        conn.execute(
            "UPDATE shop_users SET balance_cents=? WHERE shop_id=? AND user_id=?",
            (new_bal_cents, shop_id, uid)
        )

def can_deduct(shop_id: int, uid: int, amt: int) -> bool:
    return get_balance(shop_id, uid) >= amt

def deduct(shop_id: int, uid: int, amt: int):
    add_balance_delta(shop_id, uid, -amt)


# ===================== SHOPS =====================
def get_main_shop_id() -> int:
    return 1

def get_shop(shop_id: int):
    with db() as conn:
        # ===================== SHOPS (CONTINUED) =====================
def get_shop(shop_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()

def is_shop_owner(shop_id: int, uid: int) -> bool:
    s = get_shop(shop_id)
    return bool(s) and int(s["owner_id"]) == uid

def set_shop_profile(shop_id: int, name: str, welcome: str):
    with db() as conn:
        conn.execute("UPDATE shops SET shop_name=?, welcome_text=? WHERE id=?",
                     (name.strip(), welcome.strip(), shop_id))

def set_shop_welcome_media(shop_id: int, file_id: Optional[str], media_type: Optional[str]):
    with db() as conn:
        conn.execute("UPDATE shops SET welcome_media_file_id=?, welcome_media_type=? WHERE id=?",
                     (file_id, media_type, shop_id))

def set_shop_wallet(shop_id: int, address: Optional[str]):
    addr = address.strip() if address else None
    with db() as conn:
        conn.execute("UPDATE shops SET wallet_address=? WHERE id=?", (addr, shop_id))

def get_shop_wallet(shop_id: int) -> Optional[str]:
    with db() as conn:
        r = conn.execute("SELECT wallet_address FROM shops WHERE id=?", (shop_id,)).fetchone()
        if not r:
            return None
        v = r["wallet_address"]
        return v.strip() if v else None

def set_shop_panel_until(shop_id: int, until_iso: Optional[str]):
    with db() as conn:
        conn.execute("UPDATE shops SET panel_until=? WHERE id=?", (until_iso, shop_id))

def is_panel_active(shop_id: int) -> bool:
    s = get_shop(shop_id)
    if not s or not s["panel_until"]:
        return False
    try:
        return parse_iso(s["panel_until"]) > now_utc()
    except Exception:
        return False

def extend_panel_30_days(shop_id: int):
    """If active -> add 30 days on top; else -> start from now."""
    s = get_shop(shop_id)
    base = now_utc()
    if s and s["panel_until"]:
        try:
            cur = parse_iso(s["panel_until"])
            if cur > base:
                base = cur
        except Exception:
            pass
    new_until = (base + datetime.timedelta(days=PANEL_DAYS)).isoformat(timespec="seconds")
    set_shop_panel_until(shop_id, new_until)

def set_shop_suspension(shop_id: int, suspended: bool, reason: Optional[str], until_iso: Optional[str]):
    with db() as conn:
        conn.execute("""
        UPDATE shops
        SET is_suspended=?, suspended_reason=?, suspended_until=?
        WHERE id=?
        """, (1 if suspended else 0, (reason.strip() if reason else None), until_iso, shop_id))

def shop_is_suspended(shop_id: int) -> Tuple[bool, Optional[str]]:
    s = get_shop(shop_id)
    if not s:
        return True, "Shop not found"
    if int(s["is_suspended"]) == 1:
        return True, s["suspended_reason"]
    until = s["suspended_until"]
    if until:
        try:
            if parse_iso(until) > now_utc():
                return True, (s["suspended_reason"] or f"Suspended until {until}")
        except Exception:
            pass
    return False, None

def create_shop_for_owner(owner_id: int) -> int:
    with db() as conn:
        r = conn.execute("SELECT id FROM shops WHERE owner_id=? ORDER BY id DESC LIMIT 1", (owner_id,)).fetchone()
        if r:
            return int(r["id"])
        conn.execute("""
        INSERT INTO shops(
          owner_id, shop_name, welcome_text, welcome_media_file_id, welcome_media_type,
          panel_until, is_suspended, suspended_reason, suspended_until, created_at, wallet_address
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (
            owner_id,
            f"{owner_id}'s Shop",
            "Welcome! Customize your store in the Admin Panel.",
            None, None,
            None,
            0, None, None,
            now_iso(),
            None
        ))
        return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

def list_shops(limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
        SELECT id, owner_id, shop_name, panel_until, is_suspended, suspended_until
        FROM shops
        WHERE id != 1
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

def total_sellers() -> int:
    with db() as conn:
        r = conn.execute("SELECT COUNT(*) AS c FROM shops WHERE id != 1").fetchone()
        return int(r["c"]) if r else 0


# ===================== SETTINGS =====================
def panel_offer_text() -> str:
    with db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key='panel_offer'").fetchone()
        return r["value"] if r else ""

def set_panel_offer_text(text: str):
    with db() as conn:
        conn.execute("UPDATE settings SET value=? WHERE key='panel_offer'", (text.strip(),))


# ===================== CATALOG =====================
def list_categories(shop_id: int, active_only=True):
    with db() as conn:
        if active_only:
            return conn.execute("SELECT * FROM categories WHERE shop_id=? AND is_active=1 ORDER BY id ASC", (shop_id,)).fetchall()
        return conn.execute("SELECT * FROM categories WHERE shop_id=? ORDER BY id ASC", (shop_id,)).fetchall()

def add_category(shop_id: int, name: str):
    name = name.strip()
    if not name:
        return
    with db() as conn:
        conn.execute("INSERT INTO categories(shop_id,name,is_active) VALUES(?,?,1)", (shop_id, name))

def toggle_category(shop_id: int, cat_id: int):
    with db() as conn:
        r = conn.execute("SELECT is_active FROM categories WHERE shop_id=? AND id=?", (shop_id, cat_id)).fetchone()
        if not r:
            return
        conn.execute("UPDATE categories SET is_active=? WHERE shop_id=? AND id=?",
                     (0 if int(r["is_active"]) == 1 else 1, shop_id, cat_id))

def list_subcategories(shop_id: int, cat_id: int, active_only=True):
    with db() as conn:
        if active_only:
            return conn.execute("""
                SELECT * FROM subcategories
                WHERE shop_id=? AND category_id=? AND is_active=1
                ORDER BY id ASC
            """, (shop_id, cat_id)).fetchall()
        return conn.execute("""
            SELECT * FROM subcategories
            WHERE shop_id=? AND category_id=?
            ORDER BY id ASC
        """, (shop_id, cat_id)).fetchall()

def add_subcategory(shop_id: int, cat_id: int, name: str):
    name = name.strip()
    if not name:
        return
    with db() as conn:
        conn.execute("INSERT INTO subcategories(shop_id,category_id,name,is_active) VALUES(?,?,?,1)",
                     (shop_id, cat_id, name))

def toggle_subcategory(shop_id: int, sub_id: int):
    with db() as conn:
        r = conn.execute("SELECT is_active, category_id FROM subcategories WHERE shop_id=? AND id=?", (shop_id, sub_id)).fetchone()
        if not r:
            return
        conn.execute("UPDATE subcategories SET is_active=? WHERE shop_id=? AND id=?",
                     (0 if int(r["is_active"]) == 1 else 1, shop_id, sub_id))

def add_product(shop_id: int, cat_id: int, sub_id: int, name: str, up: int, rp: int):
    with db() as conn:
        conn.execute("""
        INSERT INTO products(shop_id,category_id,subcategory_id,name,user_price_cents,reseller_price_cents,telegram_link,is_active)
        VALUES(?,?,?,?,?,?,NULL,1)
        """, (shop_id, cat_id, sub_id, name.strip(), up, rp))

def list_products_by_subcat(shop_id: int, sub_id: int, active_only=True):
    with db() as conn:
        if active_only:
            return conn.execute("""
            SELECT p.*,
              (SELECT COUNT(*) FROM keys k WHERE k.shop_id=p.shop_id AND k.product_id=p.id AND k.is_used=0) AS stock
            FROM products p
            WHERE p.shop_id=? AND p.subcategory_id=? AND p.is_active=1
            ORDER BY p.id ASC
            """, (shop_id, sub_id)).fetchall()
        return conn.execute("""
            SELECT p.*,
              (SELECT COUNT(*) FROM keys k WHERE k.shop_id=p.shop_id AND k.product_id=p.id AND k.is_used=0) AS stock
            FROM products p
            WHERE p.shop_id=? AND p.subcategory_id=?
            ORDER BY p.id ASC
        """, (shop_id, sub_id)).fetchall()

def get_product(shop_id: int, pid: int):
    with db() as conn:
        return conn.execute("""
        SELECT p.*,
          (SELECT COUNT(*) FROM keys k WHERE k.shop_id=p.shop_id AND k.product_id=p.id AND k.is_used=0) AS stock
        FROM products p
        WHERE p.shop_id=? AND p.id=?
        """, (shop_id, pid)).fetchone()

def toggle_product(shop_id: int, pid: int):
    with db() as conn:
        r = conn.execute("SELECT is_active FROM products WHERE shop_id=? AND id=?", (shop_id, pid)).fetchone()
        if not r:
            return
        conn.execute("UPDATE products SET is_active=? WHERE shop_id=? AND id=?",
                     (0 if int(r["is_active"]) == 1 else 1, shop_id, pid))

def update_product_link(shop_id: int, pid: int, link: Optional[str]):
    with db() as conn:
        conn.execute("UPDATE products SET telegram_link=? WHERE shop_id=? AND id=?",
                     ((link.strip() if link else None), shop_id, pid))

def update_product_prices(shop_id: int, pid: int, up: int, rp: int):
    with db() as conn:
        conn.execute("UPDATE products SET user_price_cents=?, reseller_price_cents=? WHERE shop_id=? AND id=?",
                     (up, rp, shop_id, pid))

def add_keys(shop_id: int, pid: int, keys: List[str]) -> int:
    keys = [k.strip() for k in keys if k.strip()]
    if not keys:
        return 0
    with db() as conn:
        conn.executemany(
            "INSERT INTO keys(shop_id,product_id,key_text,is_used) VALUES(?,?,?,0)",
            [(shop_id, pid, k) for k in keys]
        )
    return len(keys)

def take_key(shop_id: int, pid: int, buyer: int) -> Optional[str]:
    with db() as conn:
        r = conn.execute("""
            SELECT id, key_text FROM keys
            WHERE shop_id=? AND product_id=? AND is_used=0
            ORDER BY id ASC LIMIT 1
        """, (shop_id, pid)).fetchone()
        if not r:
            return None
        conn.execute("""
            UPDATE keys SET is_used=1, used_by=?, used_at=?
            WHERE shop_id=? AND id=?
        """, (buyer, now_iso(), shop_id, r["id"]))
        return r["key_text"]


# ===================== PURCHASES =====================
def add_purchase(shop_id: int, uid: int, pid: int, pname: str, price_cents: int, key_text: str):
    with db() as conn:
        conn.execute("""
        INSERT INTO purchases(shop_id,user_id,product_id,product_name,price_cents,key_text,created_at)
        VALUES(?,?,?,?,?,?,?)
        """, (shop_id, uid, pid, pname, price_cents, key_text, now_iso()))

def list_purchases(shop_id: int, uid: int, limit: int = 10):
    with db() as conn:
        return conn.execute("""
        SELECT id, product_id, product_name, price_cents, key_text, created_at
        FROM purchases
        WHERE shop_id=? AND user_id=?
        ORDER BY id DESC
        LIMIT ?
        """, (shop_id, uid, limit)).fetchall()

def get_purchase(shop_id: int, uid: int, purchase_id: int):
    with db() as conn:
        return conn.execute("""
        SELECT * FROM purchases
        WHERE shop_id=? AND user_id=? AND id=?
        """, (shop_id, uid, purchase_id)).fetchone()


# ===================== DEPOSITS =====================
def create_deposit(shop_id: int, uid: int, amt: int, file_id: str, caption: str) -> int:
    with db() as conn:
        conn.execute("""
        INSERT INTO deposits(shop_id,user_id,amount_cents,photo_file_id,caption,status,created_at)
        VALUES(?,?,?,?,?,'PENDING',?)
        """, (shop_id, uid, amt, file_id, caption, now_iso()))
        return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

def get_deposit(shop_id: int, dep_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM deposits WHERE shop_id=? AND id=?", (shop_id, dep_id)).fetchone()

def set_deposit_status(shop_id: int, dep_id: int, status: str, reviewer: int):
    with db() as conn:
        conn.execute("""
        UPDATE deposits SET status=?, reviewed_at=?, reviewed_by=?
        WHERE shop_id=? AND id=? AND status='PENDING'
        """, (status, now_iso(), reviewer, shop_id, dep_id))

def list_pending_deposits(shop_id: int, limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
        SELECT * FROM deposits
        WHERE shop_id=? AND status='PENDING'
        ORDER BY id DESC LIMIT ? OFFSET ?
        """, (shop_id, limit, offset)).fetchall()


# ===================== SUPPORT =====================
def add_support_msg(shop_id: int, uid: int, text: str):
    with db() as conn:
        conn.execute("""
        INSERT INTO support_msgs(shop_id,user_id,text,created_at)
        VALUES(?,?,?,?)
        """, (shop_id, uid, text.strip(), now_iso()))


# ===================== USERS LISTING =====================
def count_shop_users(shop_id: int) -> int:
    with db() as conn:
        r = conn.execute("SELECT COUNT(*) AS c FROM shop_users WHERE shop_id=?", (shop_id,)).fetchone()
        return int(r["c"]) if r else 0

def list_shop_users(shop_id: int, limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
        SELECT su.user_id, su.balance_cents, u.username, u.first_name, u.last_name
        FROM shop_users su
        LEFT JOIN users u ON u.user_id = su.user_id
        WHERE su.shop_id=?
        ORDER BY su.user_id ASC
        LIMIT ? OFFSET ?
        """, (shop_id, limit, offset)).fetchall()


# ===================== SHOP CONTEXT =====================
def get_active_shop_id(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    sid = ctx.user_data.get("active_shop_id")
    return int(sid) if sid else get_main_shop_id()

def set_active_shop_id(ctx: ContextTypes.DEFAULT_TYPE, shop_id: int):
    ctx.user_data["active_shop_id"] = int(shop_id)

def shop_home_text(shop_id: int, uid: int) -> str:
    suspended, reason = shop_is_suspended(shop_id)
    if suspended:
        return "⛔ This shop is suspended.\n\n" + (f"Reason: {reason}" if reason else "") + f"\n\n{DEFAULT_BRAND}"

    s = get_shop(shop_id)
    if not s:
        return DEFAULT_MAIN_WELCOME + "\n\n" + DEFAULT_BRAND

    lines = [s["welcome_text"], "", f"— {s['shop_name']}"]

    # If seller shop, show subscription days left (no wallet here)
    if shop_id != get_main_shop_id():
        if s["panel_until"]:
            left = days_left(s["panel_until"])
            lines.insert(1, f"🗓 Subscription: {left} day(s) left")

    return "\n".join(lines)


# ===================== CLEAN SEND =====================
async def send_clean_text(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, uid: int, text: str, reply_markup=None, parse_mode=None):
    last_id = get_last_bot_msg_id(uid)
    if last_id:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass
    msg = await ctx.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    set_last_bot_msg_id(uid, msg.message_id)

async def send_clean(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None, parse_mode=None):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    await send_clean_text(chat_id, ctx, uid, text, reply_markup=reply_markup, parse_mode=parse_mode)

async def send_welcome_media_if_any(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, shop_id: int):
    s = get_shop(shop_id)
    if not s:
        return
    fid = s["welcome_media_file_id"]
    mtype = (s["welcome_media_type"] or "").strip().lower()
    if not fid or mtype not in ("photo", "video"):
        return
    try:
        if mtype == "photo":
            await ctx.bot.send_photo(chat_id=chat_id, photo=fid)
        else:
            await ctx.bot.send_video(chat_id=chat_id, video=fid)
    except Exception:
        pass


# ===================== UI HELPERS =====================
def kb_back_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]])

def kb_wallet() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Deposit", callback_data="wallet:deposit"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_deposit_amounts() -> InlineKeyboardMarkup:
    presets = [500, 1000, 2000, 5000]
    btns = [InlineKeyboardButton(f"💵 {money(a)}", callback_data=f"dep:amt:{a}") for a in presets]
    kb = rows(btns, 2)
    kb.append([InlineKeyboardButton("✍️ Custom Amount", callback_data="dep:custom"),
               InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
    return InlineKeyboardMarkup(kb)

def kb_products_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Categories", callback_data="prod:cats"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_owner_menu(shop_id: int, uid: int) -> InlineKeyboardMarkup:
    # seller admin OR super admin inside shop
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="own:users:0"),
         InlineKeyboardButton("💳 Deposits", callback_data="own:deps:0")],
        [InlineKeyboardButton("📂 Categories", callback_data="own:cats"),
         InlineKeyboardButton("🧩 Co-Categories", callback_data="own:subs")],
        [InlineKeyboardButton("📦 Products", callback_data="own:products"),
         InlineKeyboardButton("🔑 Keys", callback_data="own:keys")],
        [InlineKeyboardButton("💳 Wallet Address", callback_data="own:walletaddr"),
         InlineKeyboardButton("🖼️ Welcome Media", callback_data="own:welcomemedia")],
        [InlineKeyboardButton("✏️ Edit Store", callback_data="own:editstore"),
         InlineKeyboardButton("📣 Broadcast", callback_data="own:broadcast")],
        [InlineKeyboardButton("🔗 Share Shop", callback_data="own:share"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_sa_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏪 Sellers", callback_data="sa:shops:0"),
         InlineKeyboardButton("📊 Totals", callback_data="sa:totals")],
        [InlineKeyboardButton("✏️ Become Seller Text", callback_data="sa:offer"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_home(shop_id: int, uid: int) -> InlineKeyboardMarkup:
    ensure_shop_user(shop_id, uid)

    grid = [
        [InlineKeyboardButton("🛍️ Products", callback_data="home:products"),
         InlineKeyboardButton("💰 Wallet", callback_data="home:wallet")],
        [InlineKeyboardButton("📜 History", callback_data="home:history"),
         InlineKeyboardButton("📩 Support", callback_data="home:support")],
    ]

    # Become Seller only visible in platform shop (id=1) for normal users
    if shop_id == get_main_shop_id():
        grid.append([InlineKeyboardButton("⭐ Become a Seller", callback_data="panel:info")])

    # Admin panel
    if is_shop_owner(shop_id, uid) or (shop_id == get_main_shop_id() and is_super_admin(uid)):
        if shop_id == get_main_shop_id() and is_super_admin(uid):
            grid.append([InlineKeyboardButton("🧾 Platform Admin", callback_data="sa:menu")])
            grid.append([InlineKeyboardButton("🛠️ Admin Panel (RekkoShop)", callback_data="own:menu")])
        else:
            # seller: require active subscription
            if is_panel_active(shop_id):
                grid.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="own:menu")])
            else:
                grid.append([InlineKeyboardButton("🔒 Admin Panel (Subscription Required)", callback_data="panel:info")])

    # Switch shop
    if shop_id != get_main_shop_id():
        grid.append([InlineKeyboardButton("⬅️ Back to RekkoShop", callback_data="shop:switch:main")])
    else:
        # if user owns a shop, show My Shop
        with db() as conn:
            r = conn.execute("SELECT id FROM shops WHERE owner_id=? AND id != 1 ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
            if r:
                grid.append([InlineKeyboardButton("🏪 My Shop", callback_data=f"shop:switch:{int(r['id'])}")])

    return InlineKeyboardMarkup(grid)


# ===================== START =====================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id

    ctx.user_data.setdefault("active_shop_id", get_main_shop_id())
    ctx.user_data["flow"] = None

    # deep link: ?start=shop_{sid}
    args = ctx.args or []
    if args and args[0].startswith("shop_"):
        try:
            sid = int(args[0].split("_", 1)[1])
            if get_shop(sid):
                set_active_shop_id(ctx, sid)
        except Exception:
            set_active_shop_id(ctx, get_main_shop_id())

    shop_id = get_active_shop_id(ctx)
    ensure_shop_user(shop_id, uid)

    # welcome media (photo/video) then text
    await send_welcome_media_if_any(update.effective_chat.id, ctx, shop_id)
    await send_clean(update, ctx, shop_home_text(shop_id, uid), reply_markup=kb_home(shop_id, uid))


# ===================== CALLBACKS (PART 2 CONTINUES IN PART 3) =====================
async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    upsert_user(q.from_user)

    uid = q.from_user.id
    shop_id = get_active_shop_id(ctx)
    ensure_shop_user(shop_id, uid)
    data = q.data or ""

    # Global suspension guard (allow switching/home)
    suspended, reason = shop_is_suspended(shop_id)
    if suspended and not data.startswith("shop:switch:") and data != "home:menu":
        return await q.edit_message_text(
            "⛔ This shop is suspended.\n\n" + (f"Reason: {reason}" if reason else "") + f"\n\n{DEFAULT_BRAND}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to RekkoShop", callback_data="shop:switch:main")]])
        )

    # Shop switch
    if data.startswith("shop:switch:"):
        try:
            arg = data.split(":")[-1]
            if arg == "main":
                set_active_shop_id(ctx, get_main_shop_id())
            else:
                sid = int(arg)
                if get_shop(sid):
                    set_active_shop_id(ctx, sid)
        except Exception:
            set_active_shop_id(ctx, get_main_shop_id())

        shop_id = get_active_shop_id(ctx)
        ensure_shop_user(shop_id, uid)
        await send_welcome_media_if_any(q.message.chat_id, ctx, shop_id)
        return await q.edit_message_text(shop_home_text(shop_id, uid), reply_markup=kb_home(shop_id, uid))

    # Home
    if data == "home:menu":
        # reset flows
        ctx.user_data["flow"] = None
        for k in [
            "dep_amount",
            "selected_user", "selected_user_page",
            "pid", "cat_id", "sub_id",
            "target_deposit",
            "sa_sel_shop", "sa_sel_user", "sa_sel_page",
            "sid",
            "own_view_pid", "own_view_sub", "own_view_cat",
            "reply_shop_id", "reply_target_uid",
        ]:
            ctx.user_data.pop(k, None)

        await send_welcome_media_if_any(q.message.chat_id, ctx, shop_id)
        return await q.edit_message_text(shop_home_text(shop_id, uid), reply_markup=kb_home(shop_id, uid))

    # Wallet screen (ONLY here shows address)
    if data == "home:wallet":
        bal = get_balance(shop_id, uid)
        addr = get_shop_wallet(shop_id)
        addr_txt = addr if addr else "⚠️ Wallet address not set yet (owner must set it)"
        txt = f"💰 Wallet\n\nBalance: {money(bal)}\n\nUSDT (TRC-20) Address:\n{addr_txt}"
        return await q.edit_message_text(txt, reply_markup=kb_wallet())

    # Deposit choose
    if data == "wallet:deposit":
        addr = get_shop_wallet(shop_id)
        if not addr:
            return await q.edit_message_text(
                "⚠️ Deposit unavailable.\n\nShop owner has not set a wallet address yet.",
                reply_markup=kb_back_home()
            )
        ctx.user_data["flow"] = "dep_choose"
        return await q.edit_message_text(
            f"💳 Deposit\n\nSend payment to:\n`{addr}`\n\nChoose amount:",
            reply_markup=kb_deposit_amounts(),
            parse_mode=ParseMode.MARKDOWN
        )

    if data.startswith("dep:amt:"):
        amt = int(data.split(":")[-1])
        ctx.user_data["flow"] = "dep_wait_photo"
        ctx.user_data["dep_amount"] = amt
        return await q.edit_message_text(
            f"✅ Amount set: {money(amt)}\n\nNow send payment screenshot (photo).",
            reply_markup=kb_back_home()
        )

    if data == "dep:custom":
        ctx.user_data["flow"] = "dep_custom"
        return await q.edit_message_text("✍️ Send amount (example 10 or 10.5):", reply_markup=kb_back_home())

    # Products root
    if data == "home:products":
        return await q.edit_message_text("🛍️ Products", reply_markup=kb_products_root())

    # Product browsing
    if data == "prod:cats":
        cats = list_categories(shop_id, active_only=True)
        if not cats:
            return await q.edit_message_text("No categories yet.", reply_markup=kb_back_home())
        btns = [InlineKeyboardButton(c["name"], callback_data=f"prod:cat:{c['id']}") for c in cats]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📂 Choose a category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("prod:cat:"):
        cat_id = int(data.split(":")[-1])
        subs = list_subcategories(shop_id, cat_id, active_only=True)
        if not subs:
            return await q.edit_message_text("No co-categories here yet.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="prod:cats"),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ]))
        btns = [InlineKeyboardButton(s["name"], callback_data=f"prod:sub:{s['id']}:{cat_id}") for s in subs]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="prod:cats"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("🧩 Choose a co-category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("prod:sub:"):
        parts = data.split(":")
        sub_id = int(parts[2])
        cat_id = int(parts[3])
        prods = list_products_by_subcat(shop_id, sub_id, active_only=True)
        if not prods:
            return await q.edit_message_text("No products in this co-category.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data=f"prod:cat:{cat_id}"),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ]))
        btns = [InlineKeyboardButton(p["name"], callback_data=f"prod:item:{p['id']}:{sub_id}:{cat_id}") for p in prods]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data=f"prod:cat:{cat_id}"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📦 Choose a product:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("prod:item:"):
        parts = data.split(":")
        pid = int(parts[2])
        sub_id = int(parts[3])
        cat_id = int(parts[4])
        p = get_product(shop_id, pid)
        if not p or int(p["is_active"]) != 1:
            return await q.answer("Product not available", show_alert=True)

        price = int(p["user_price_cents"])
        stock = int(p["stock"]) if p["stock"] is not None else 0
        bal = get_balance(shop_id, uid)

        txt = (
            f"📦 {p['name']}\n\n"
            f"Price: {money(price)}\n"
            f"Stock: {stock}\n\n"
            f"Your balance: {money(bal)}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Buy", callback_data=f"buy:{pid}:{sub_id}:{cat_id}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"prod:sub:{sub_id}:{cat_id}")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("buy:"):
        parts = data.split(":")
        pid = int(parts[1])
        sub_id = int(parts[2])
        cat_id = int(parts[3])

        p = get_product(shop_id, pid)
        if not p or int(p["is_active"]) != 1:
            return await q.answer("Product not available", show_alert=True)

        stock = int(p["stock"]) if p["stock"] is not None else 0
        if stock <= 0:
            return await q.answer("Out of stock.", show_alert=True)

        price = int(p["user_price_cents"])
        if not can_deduct(shop_id, uid, price):
            return await q.answer("Not enough balance. Top up wallet.", show_alert=True)

        key_text = take_key(shop_id, pid, uid)
        if not key_text:
            return await q.answer("Out of stock.", show_alert=True)

        deduct(shop_id, uid, price)
        add_purchase(shop_id, uid, pid, p["name"], price, key_text)

        link = (p["telegram_link"] or "").strip()
        txt = f"✅ Purchase successful!\n\n🔑 Key:\n`{key_text}`"
        if link:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Get Files", url=link),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ])
            return await q.edit_message_text(txt, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return await q.edit_message_text(txt + "\n\n⚠️ No file link set yet.", reply_markup=kb_back_home(), parse_mode=ParseMode.MARKDOWN)

    # History
    if data == "home:history":
        purchases = list_purchases(shop_id, uid, limit=10)
        if not purchases:
            return await q.edit_message_text("📜 No purchases yet.", reply_markup=kb_back_home())
        btns = [InlineKeyboardButton(f"#{r['id']} • {r['product_name']}", callback_data=f"hist:view:{r['id']}") for r in purchases]
        kb = rows(btns, 1)
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📜 Your purchases:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("hist:view:"):
        hid = int(data.split(":")[-1])
        r = get_purchase(shop_id, uid, hid)
        if not r:
            return await q.answer("Not found", show_alert=True)
        txt = (
            f"🧾 Purchase #{r['id']}\n\n"
            f"Product: {r['product_name']}\n"
            f"Paid: {money(int(r['price_cents']))}\n"
            f"Date: {r['created_at']}\n\n"
            f"🔑 Key:\n`{r['key_text']}`"
        )
        p = get_product(shop_id, int(r["product_id"]))
        link = (p["telegram_link"] or "").strip() if p else ""
        if link:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Get Files", url=link),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")],
                [InlineKeyboardButton("⬅️ Back", callback_data="home:history")]
            ])
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="home:history"),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ])
        return await q.edit_message_text(txt, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    # Support
    if data == "home:support":
        ctx.user_data["flow"] = "support_send"
        return await q.edit_message_text("📩 Support\n\nType your message to the shop owner:", reply_markup=kb_back_home())

    # Become Seller info/buy (platform only)
    if data == "panel:info":
        if shop_id != get_main_shop_id():
            # in seller shop, hide become seller
            return await q.answer("Not available here.", show_alert=True)

        offer = panel_offer_text()
        bal = get_balance(get_main_shop_id(), uid)
        txt = offer + f"\n\nPrice: {money(PANEL_PRICE_CENTS)} / {PANEL_DAYS} days\nYour RekkoShop balance: {money(bal)}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Buy / Extend Seller Subscription", callback_data="panel:buy")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data == "panel:buy":
        if shop_id != get_main_shop_id():
            return await q.answer("Not available here.", show_alert=True)

        main_id = get_main_shop_id()
        ensure_shop_user(main_id, uid)
        if not can_deduct(main_id, uid, PANEL_PRICE_CENTS):
            return await q.answer("Not enough RekkoShop balance. Top up first.", show_alert=True)

        deduct(main_id, uid, PANEL_PRICE_CENTS)

        sid = create_shop_for_owner(uid)
        extend_panel_30_days(sid)  # IMPORTANT: adds 30 days if already active
        ensure_shop_user(sid, uid)

        set_active_shop_id(ctx, sid)

        bot_username = (await ctx.bot.get_me()).username
        deeplink = f"https://t.me/{bot_username}?start=shop_{sid}"

        txt = (
            "✅ Seller subscription activated/extended!\n\n"
            f"Your Shop ID: {sid}\n"
            f"Share your shop link:\n{deeplink}\n\n"
            "Next: Admin Panel → 💳 Wallet Address (set your wallet)"
        )
        return await q.edit_message_text(txt, reply_markup=kb_home(sid, uid))

    # Admin Panel entry
    if data == "own:menu":
        if not (is_shop_owner(shop_id, uid) or (shop_id == get_main_shop_id() and is_super_admin(uid))):
            return await q.answer("Not authorized", show_alert=True)

        if shop_id != get_main_shop_id() and not is_panel_active(shop_id):
            return await q.answer("Subscription expired. Renew in RekkoShop → Become a Seller.", show_alert=True)

        ctx.user_data["flow"] = None
        return await q.edit_message_text("🛠️ Admin Panel", reply_markup=kb_owner_menu(shop_id, uid))

    # Platform Admin
    if data == "sa:menu":
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        return await q.edit_message_text("🧾 Platform Admin", reply_markup=kb_sa_menu())

    # Everything else continues in PART 3
    # (keep this function name the same: on_cb)

# ===================== CALLBACKS (PART 3) =====================
# Paste this ENTIRE Part 3 DIRECTLY AFTER the end of Part 2 code block.

async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    upsert_user(q.from_user)

    uid = q.from_user.id
    shop_id = get_active_shop_id(ctx)
    ensure_shop_user(shop_id, uid)
    data = q.data or ""

    # Global suspension guard (allow switching/home)
    suspended, reason = shop_is_suspended(shop_id)
    if suspended and not data.startswith("shop:switch:") and data != "home:menu":
        return await q.edit_message_text(
            "⛔ This shop is suspended.\n\n" + (f"Reason: {reason}" if reason else "") + f"\n\n{DEFAULT_BRAND}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to RekkoShop", callback_data="shop:switch:main")]])
        )

    # --- Re-handle the early routes too (safe if Part 2 already handled) ---
    if data.startswith("shop:switch:"):
        try:
            arg = data.split(":")[-1]
            if arg == "main":
                set_active_shop_id(ctx, get_main_shop_id())
            else:
                sid = int(arg)
                if get_shop(sid):
                    set_active_shop_id(ctx, sid)
        except Exception:
            set_active_shop_id(ctx, get_main_shop_id())

        shop_id = get_active_shop_id(ctx)
        ensure_shop_user(shop_id, uid)
        await send_welcome_media_if_any(q.message.chat_id, ctx, shop_id)
        return await q.edit_message_text(shop_home_text(shop_id, uid), reply_markup=kb_home(shop_id, uid))

    if data == "home:menu":
        ctx.user_data["flow"] = None
        for k in [
            "dep_amount",
            "selected_user", "selected_user_page",
            "pid", "cat_id", "sub_id",
            "target_deposit",
            "sa_sel_shop", "sa_sel_user", "sa_sel_page",
            "sid",
            "own_view_pid", "own_view_sub", "own_view_cat",
            "reply_shop_id", "reply_target_uid",
        ]:
            ctx.user_data.pop(k, None)

        await send_welcome_media_if_any(q.message.chat_id, ctx, shop_id)
        return await q.edit_message_text(shop_home_text(shop_id, uid), reply_markup=kb_home(shop_id, uid))

    # ===================== ADMIN PANEL / OWNER =====================
    def _owner_allowed() -> bool:
        if shop_id == get_main_shop_id() and is_super_admin(uid):
            return True
        if is_shop_owner(shop_id, uid):
            if shop_id != get_main_shop_id() and not is_panel_active(shop_id):
                return False
            return True
        return False

    if data == "own:menu":
        if not _owner_allowed():
            return await q.answer("Not authorized / Subscription expired", show_alert=True)
        ctx.user_data["flow"] = None
        return await q.edit_message_text("🛠️ Admin Panel", reply_markup=kb_owner_menu(shop_id, uid))

    # --- Share Shop ---
    if data == "own:share":
        if not _owner_allowed():
            return await q.answer("Not authorized", show_alert=True)
        me = await ctx.bot.get_me()
        bot_username = me.username
        link = f"https://t.me/{bot_username}?start=shop_{shop_id}"
        txt = f"🔗 Share your shop link:\n{link}"
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data="own:menu"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # --- Edit Store (name + welcome text) ---
    if data == "own:editstore":
        if not _owner_allowed():
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = "own_editstore"
        return await q.edit_message_text(
            "✏️ Edit Store\n\nSend in ONE message like:\n\n"
            "Shop Name: My Store\n"
            "Welcome: Welcome to my shop!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="own:menu")]])
        )

    # --- Welcome Media set/remove ---
    if data == "own:welcomemedia":
        if not _owner_allowed():
            return await q.answer("Not authorized", show_alert=True)
        s = get_shop(shop_id)
        cur = "None"
        if s and s["welcome_media_type"] and s["welcome_media_file_id"]:
            cur = f"{s['welcome_media_type']}"
        txt = (
            "🖼️ Welcome Media\n\n"
            f"Current: {cur}\n\n"
            "Send a PHOTO or VIDEO now to set.\n"
            "Or press Remove to clear."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Remove", callback_data="own:welcomemedia:rm")],
            [InlineKeyboardButton("⬅️ Back", callback_data="own:menu"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        ctx.user_data["flow"] = "own_set_welcome_media"
        return await q.edit_message_text(txt, reply_markup=kb)

    if data == "own:welcomemedia:rm":
        if not _owner_allowed():
            return await q.answer("Not authorized", show_alert=True)
        set_shop_welcome_media(shop_id, None, None)
        ctx.user_data["flow"] = None
        return await q.edit_message_text("✅ Welcome media removed.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data="own:welcomemedia"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # --- Wallet address set ---
    if data == "own:walletaddr":
        if not _owner_allowed():
            return await q.answer("Not authorized", show_alert=True)
        cur = get_shop_wallet(shop_id) or "Not set"
        ctx.user_data["flow"] = "own_set_wallet"
        return await q.edit_message_text(
            f"💳 Wallet Address\n\nCurrent:\n{cur}\n\nSend new wallet address (text).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="own:menu")]])
        )

    # --- Broadcast ---
    if data == "own:broadcast":
        if not _owner_allowed():
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = "own_broadcast"
        return await q.edit_message_text(
            "📣 Broadcast\n\nSend the message to broadcast to ALL users in this shop.\n"
            "You can send text only.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="own:menu")]])
        )

    # --- Users list (includes edit balance) ---
    if data.startswith("own:users:"):
        if not _owner_allowed():
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        per = 10
        offset = page * per
        rows_u = list_shop_users(shop_id, per, offset)
        total = count_shop_users(shop_id)
        if not rows_u:
            return await q.edit_message_text("👥 Users\n\nNo users yet.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="own:menu"),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ]))
        lines = [f"👥 Users ({total})", ""]
        btns = []
        for r in rows_u:
            name = (r["first_name"] or "") + (" " + (r["last_name"] or "") if r["last_name"] else "")
            uname = f"@{r['username']}" if r["username"] else ""
            bal = money(int(r["balance_cents"]))
            lines.append(f"• {r['user_id']} {uname} {name.strip()} — {bal}")
            btns.append(InlineKeyboardButton(f"Edit {r['user_id']}", callback_data=f"own:user:{r['user_id']}:{page}"))
        kb = rows(btns, 2)
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"own:users:{page-1}"))
        if offset + per < total:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"own:users:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:user:"):
        if not _owner_allowed():
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        target_uid = int(parts[2])
        page = int(parts[3])
        ctx.user_data["flow"] = "own_edit_balance"
        ctx.user_data["selected_user"] = target_uid
        ctx.user_data["selected_user_page"] = page
        bal = get_balance(shop_id, target_uid)
        return await q.edit_message_text(
            f"💰 Edit Balance\n\nUser: {target_uid}\nCurrent balance: {money(bal)}\n\n"
            "Send NEW balance amount (example 0, 10, 10.5).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data=f"own:users:{page}")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ])
        )

    # --- Deposits list ---
    if data.startswith("own:deps:"):
        if not _owner_allowed():
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        per = 10
        offset = page * per
        deps = list_pending_deposits(shop_id, per, offset)
        if not deps:
            return await q.edit_message_text("💳 Deposits\n\nNo pending deposits.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="own:menu"),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ]))
        lines = ["💳 Pending Deposits", ""]
        btns = []
        for d in deps:
            lines.append(f"• #{d['id']} — User {d['user_id']} — {money(int(d['amount_cents']))}")
            btns.append(InlineKeyboardButton(f"View #{d['id']}", callback_data=f"own:depview:{d['id']}:{page}"))
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:depview:"):
        if not _owner_allowed():
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        dep_id = int(parts[2])
        page = int(parts[3])
        d = get_deposit(shop_id, dep_id)
        if not d:
            return await q.answer("Not found", show_alert=True)

        txt = (
            f"🧾 Deposit #{d['id']}\n\n"
            f"User: {d['user_id']}\n"
            f"Amount: {money(int(d['amount_cents']))}\n"
            f"Status: {d['status']}\n"
            f"Time: {d['created_at']}\n\n"
            f"Caption:\n{d['caption'] or '-'}\n\n"
            "Use buttons below to Approve/Reject."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"own:dep:ok:{dep_id}:{page}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"own:dep:no:{dep_id}:{page}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:deps:{page}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])

        # show screenshot as photo (with caption) then show control message
        try:
            await ctx.bot.send_photo(chat_id=q.message.chat_id, photo=d["photo_file_id"], caption=f"Deposit #{d['id']} Screenshot")
        except Exception:
            pass

        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("own:dep:ok:") or data.startswith("own:dep:no:"):
        if not _owner_allowed():
            return await q.answer("Not authorized", show_alert=True)
        parts = data

        
