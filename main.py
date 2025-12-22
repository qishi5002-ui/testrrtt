import os
import sqlite3
import datetime
import hashlib
from typing import Optional, List, Tuple, Dict, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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

# ============================================================
# ENV
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPER_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # YOU
DB_PATH = os.getenv("DB_PATH", "rekkoshop.db")
CURRENCY = os.getenv("CURRENCY", "USD")

# Platform wallet address (where sellers pay subscription / renew)
PLATFORM_USDT_TRC20_ADDRESS = os.getenv("USDT_TRC20_ADDRESS", "").strip()

# Become Seller subscription
SELLER_PRICE_CENTS = int(os.getenv("SELLER_PRICE_CENTS", "1000"))  # $10 default
SELLER_DAYS = int(os.getenv("SELLER_DAYS", "30"))

PAGE_SIZE = 8

DEFAULT_MAIN_SHOP_NAME = "RekkoShop"
DEFAULT_MAIN_WELCOME = "Welcome To RekkoShop , Receive your keys instantly here"
DEFAULT_BRAND = "Bot created by @RekkoOwn"

DEFAULT_BECOME_SELLER_DESC = (
    "⭐ Become Seller ($10/month)\n\n"
    "You will get:\n"
    "• Your own shop in this bot\n"
    "• Your own wallet address (customers pay you)\n"
    "• Your own categories / products / keys\n"
    "• Your own admin panel\n\n"
    "Renew subscription via this bot (owner approves).\n"
)

# ============================================================
# TIME / MONEY
# ============================================================
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
    return (u.username or "").lower() if u and u.username else None

def rows(btns: List[InlineKeyboardButton], per_row: int = 2):
    return [btns[i:i+per_row] for i in range(0, len(btns), per_row)]

def days_left(until_iso: Optional[str]) -> int:
    if not until_iso:
        return 0
    try:
        until = parse_iso(until_iso)
        secs = (until - now_utc()).total_seconds()
        if secs <= 0:
            return 0
        return int((secs + 86399) // 86400)
    except Exception:
        return 0

# ============================================================
# DB
# ============================================================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return col in cols

def _ensure_col(conn: sqlite3.Connection, table: str, col: str, ddl_fragment: str):
    if not _col_exists(conn, table, col):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl_fragment}")

def init_db():
    with db() as conn:
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
            wallet_address TEXT,
            panel_until TEXT,
            is_suspended INTEGER NOT NULL DEFAULT 0,
            suspended_reason TEXT,
            created_at TEXT NOT NULL
        )
        """)

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
            kind TEXT NOT NULL DEFAULT 'TOPUP',  -- TOPUP / SUB_NEW / SUB_RENEW
            target_shop_id INTEGER,              -- for SUB_* deposits: which seller shop to activate/renew
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

        # migrations (in case older DB existed)
        _ensure_col(conn, "shops", "wallet_address", "TEXT")
        _ensure_col(conn, "shops", "panel_until", "TEXT")
        _ensure_col(conn, "shops", "is_suspended", "INTEGER NOT NULL DEFAULT 0")
        _ensure_col(conn, "shops", "suspended_reason", "TEXT")

        _ensure_col(conn, "deposits", "kind", "TEXT NOT NULL DEFAULT 'TOPUP'")
        _ensure_col(conn, "deposits", "target_shop_id", "INTEGER")
        _ensure_col(conn, "deposits", "reviewed_at", "TEXT")
        _ensure_col(conn, "deposits", "reviewed_by", "INTEGER")

        # Ensure main shop exists with id=1
        r = conn.execute("SELECT id FROM shops ORDER BY id ASC LIMIT 1").fetchone()
        if not r:
            conn.execute("""
            INSERT INTO shops(owner_id, shop_name, welcome_text, wallet_address, panel_until, is_suspended, suspended_reason, created_at)
            VALUES(?,?,?,?,NULL,0,NULL,?)
            """, (SUPER_ADMIN_ID, DEFAULT_MAIN_SHOP_NAME, DEFAULT_MAIN_WELCOME, None, now_iso()))

        # Default settings
        if not conn.execute("SELECT 1 FROM settings WHERE key='become_seller_desc'").fetchone():
            conn.execute("INSERT INTO settings(key,value) VALUES(?,?)", ("become_seller_desc", DEFAULT_BECOME_SELLER_DESC))

        if not conn.execute("SELECT 1 FROM settings WHERE key='platform_wallet'").fetchone():
            conn.execute("INSERT INTO settings(key,value) VALUES(?,?)", ("platform_wallet", PLATFORM_USDT_TRC20_ADDRESS or ""))

# ============================================================
# SETTINGS
# ============================================================
def get_setting(key: str, default: str = "") -> str:
    with db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

def set_setting(key: str, value: str):
    with db() as conn:
        if conn.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone():
            conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
        else:
            conn.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, value))

def platform_wallet() -> str:
    # prefer ENV if set, else DB setting
    env = PLATFORM_USDT_TRC20_ADDRESS.strip()
    if env:
        return env
    return (get_setting("platform_wallet", "") or "").strip()

def become_seller_desc() -> str:
    return get_setting("become_seller_desc", DEFAULT_BECOME_SELLER_DESC)

# ============================================================
# USERS
# ============================================================
def upsert_user(u):
    with db() as conn:
        uid = u.id
        uname = safe_username(u)
        exists = conn.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone()
        if exists:
            conn.execute("""
            UPDATE users SET username=?, first_name=?, last_name=?, updated_at=?
            WHERE user_id=?
            """, (uname, u.first_name, u.last_name, now_iso(), uid))
        else:
            conn.execute("""
            INSERT INTO users(user_id,username,first_name,last_name,last_bot_msg_id,created_at,updated_at)
            VALUES(?,?,?,?,NULL,?,?)
            """, (uid, uname, u.first_name, u.last_name, now_iso(), now_iso()))

def get_last_bot_msg_id(uid: int) -> Optional[int]:
    with db() as conn:
        r = conn.execute("SELECT last_bot_msg_id FROM users WHERE user_id=?", (uid,)).fetchone()
        return int(r["last_bot_msg_id"]) if r and r["last_bot_msg_id"] else None

def set_last_bot_msg_id(uid: int, msg_id: Optional[int]):
    with db() as conn:
        conn.execute("UPDATE users SET last_bot_msg_id=? WHERE user_id=?", (msg_id, uid))

def list_all_user_ids() -> List[int]:
    with db() as conn:
        rows_ = conn.execute("SELECT user_id FROM users").fetchall()
        return [int(r["user_id"]) for r in rows_]

# ============================================================
# SHOP USERS / BALANCE
# ============================================================
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
        conn.execute("UPDATE shop_users SET balance_cents=? WHERE shop_id=? AND user_id=?",
                     (new_bal_cents, shop_id, uid))

def can_deduct(shop_id: int, uid: int, amt: int) -> bool:
    return get_balance(shop_id, uid) >= amt

def deduct(shop_id: int, uid: int, amt: int):
    add_balance_delta(shop_id, uid, -amt)

# ============================================================
# SHOPS
# ============================================================
def get_main_shop_id() -> int:
    return 1

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

def extend_panel(shop_id: int, days: int):
    s = get_shop(shop_id)
    if not s:
        return
    base = now_utc()
    if s["panel_until"]:
        try:
            cur = parse_iso(s["panel_until"])
            if cur > base:
                base = cur
        except Exception:
            pass
    new_until = (base + datetime.timedelta(days=days)).isoformat(timespec="seconds")
    set_shop_panel_until(shop_id, new_until)

def set_shop_suspension(shop_id: int, suspended: bool, reason: Optional[str]):
    with db() as conn:
        conn.execute("""
        UPDATE shops SET is_suspended=?, suspended_reason=?
        WHERE id=?
        """, (1 if suspended else 0, (reason.strip() if reason else None), shop_id))

def shop_is_suspended(shop_id: int) -> Tuple[bool, Optional[str]]:
    s = get_shop(shop_id)
    if not s:
        return True, "Shop not found"
    return (bool(int(s["is_suspended"]) == 1), s["suspended_reason"])

def list_shops(limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
        SELECT id, owner_id, shop_name, panel_until, is_suspended
        FROM shops
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

def get_shop_by_owner(owner_id: int) -> Optional[int]:
    with db() as conn:
        r = conn.execute("SELECT id FROM shops WHERE owner_id=? ORDER BY id DESC LIMIT 1", (owner_id,)).fetchone()
        return int(r["id"]) if r else None

def create_shop_for_owner(owner_id: int) -> int:
    with db() as conn:
        r = conn.execute("SELECT id FROM shops WHERE owner_id=? ORDER BY id DESC LIMIT 1", (owner_id,)).fetchone()
        if r:
            return int(r["id"])
        conn.execute("""
        INSERT INTO shops(owner_id, shop_name, welcome_text, wallet_address, panel_until, is_suspended, suspended_reason, created_at)
        VALUES(?,?,?,?,NULL,0,NULL,?)
        """, (owner_id, f"{owner_id}'s Shop", "Welcome! Edit your store in Admin Panel.", None, now_iso()))
        return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

# ============================================================
# CATALOG
# ============================================================
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
        r = conn.execute("SELECT is_active FROM subcategories WHERE shop_id=? AND id=?", (shop_id, sub_id)).fetchone()
        if not r:
            return
        conn.execute("UPDATE subcategories SET is_active=? WHERE shop_id=? AND id=?",
                     (0 if int(r["is_active"]) == 1 else 1, shop_id, sub_id))

def add_product(shop_id: int, cat_id: int, sub_id: int, name: str, user_price_cents: int):
    with db() as conn:
        conn.execute("""
        INSERT INTO products(shop_id,category_id,subcategory_id,name,user_price_cents,telegram_link,is_active)
        VALUES(?,?,?,?,?,NULL,1)
        """, (shop_id, cat_id, sub_id, name.strip(), user_price_cents))

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

def update_product_price(shop_id: int, pid: int, user_price_cents: int):
    with db() as conn:
        conn.execute("UPDATE products SET user_price_cents=? WHERE shop_id=? AND id=?",
                     (user_price_cents, shop_id, pid))

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

# ============================================================
# PURCHASES / HISTORY
# ============================================================
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

# ============================================================
# DEPOSITS
# ============================================================
def create_deposit(shop_id: int, uid: int, amt: int, file_id: str, caption: str, kind: str = "TOPUP", target_shop_id: Optional[int] = None) -> int:
    with db() as conn:
        conn.execute("""
        INSERT INTO deposits(shop_id,user_id,amount_cents,photo_file_id,caption,status,kind,target_shop_id,created_at)
        VALUES(?,?,?,?,?,'PENDING',?,?,?)
        """, (shop_id, uid, amt, file_id, caption, kind, target_shop_id, now_iso()))
        return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

def get_deposit(shop_id: int, dep_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM deposits WHERE shop_id=? AND id=?", (shop_id, dep_id)).fetchone()

def list_pending_deposits(shop_id: int, limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
        SELECT * FROM deposits
        WHERE shop_id=? AND status='PENDING'
        ORDER BY id DESC LIMIT ? OFFSET ?
        """, (shop_id, limit, offset)).fetchall()

def set_deposit_status(shop_id: int, dep_id: int, status: str, reviewer: int):
    with db() as conn:
        conn.execute("""
        UPDATE deposits SET status=?, reviewed_at=?, reviewed_by=?
        WHERE shop_id=? AND id=? AND status='PENDING'
        """, (status, now_iso(), reviewer, shop_id, dep_id))

def update_deposit_amount(shop_id: int, dep_id: int, new_amount_cents: int):
    with db() as conn:
        conn.execute("""
        UPDATE deposits
        SET amount_cents=?
        WHERE shop_id=? AND id=? AND status='PENDING'
        """, (new_amount_cents, shop_id, dep_id))

def update_deposit_caption(shop_id: int, dep_id: int, caption: str):
    with db() as conn:
        conn.execute("""
        UPDATE deposits
        SET caption=?
        WHERE shop_id=? AND id=? AND status='PENDING'
        """, (caption.strip(), shop_id, dep_id))

# ============================================================
# SUPPORT / USERS LIST
# ============================================================
def add_support_msg(shop_id: int, uid: int, text: str):
    with db() as conn:
        conn.execute("""
        INSERT INTO support_msgs(shop_id,user_id,text,created_at)
        VALUES(?,?,?,?)
        """, (shop_id, uid, text.strip(), now_iso()))

def count_shop_users(shop_id: int) -> int:
    with db() as conn:
        r = conn.execute("SELECT COUNT(*) AS c FROM shop_users WHERE shop_id=?", (shop_id,)).fetchone()
        return int(r["c"])

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

def list_shop_user_ids(shop_id: int) -> List[int]:
    with db() as conn:
        rows_ = conn.execute("SELECT user_id FROM shop_users WHERE shop_id=?", (shop_id,)).fetchall()
        return [int(r["user_id"]) for r in rows_]

# ============================================================
# CONTEXT (active shop)
# ============================================================
def get_active_shop_id(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    sid = ctx.user_data.get("active_shop_id")
    return int(sid) if sid else get_main_shop_id()

def set_active_shop_id(ctx: ContextTypes.DEFAULT_TYPE, shop_id: int):
    ctx.user_data["active_shop_id"] = int(shop_id)

# ============================================================
# HOME TEXT (wallet address NOT shown here)
# ============================================================
def shop_home_text(shop_id: int, uid: int) -> str:
    suspended, reason = shop_is_suspended(shop_id)
    if suspended:
        return "⛔ This shop is suspended.\n\n" + (f"Reason: {reason}" if reason else "") + f"\n\n{DEFAULT_BRAND}"

    s = get_shop(shop_id)
    if not s:
        return DEFAULT_MAIN_WELCOME + "\n\n" + DEFAULT_BRAND

    # If seller shop: show subscription days left (no wallet address here)
    if shop_id != get_main_shop_id():
        left = days_left(s["panel_until"])
        if is_shop_owner(shop_id, uid):
            if left > 0:
                return f"{s['welcome_text']}\n\n🗓 Subscription: {left} day(s) left\n\n— {s['shop_name']}"
            else:
                return f"{s['welcome_text']}\n\n🔒 Subscription expired.\nRenew via Main Shop.\n\n— {s['shop_name']}"
        return f"{s['welcome_text']}\n\n— {s['shop_name']}"

    # Main shop
    return f"{s['welcome_text']}\n\n— {s['shop_name']}"

# ============================================================
# CLEAN SEND
# ============================================================
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

# ============================================================
# UI HELPERS
# ============================================================
def kb_back_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]])

def kb_home(shop_id: int, uid: int) -> InlineKeyboardMarkup:
    ensure_shop_user(shop_id, uid)

    grid = [
        [InlineKeyboardButton("🛍️ Products", callback_data="home:products"),
         InlineKeyboardButton("💰 Wallet", callback_data="home:wallet")],
        [InlineKeyboardButton("📜 History", callback_data="home:history"),
         InlineKeyboardButton("📩 Support", callback_data="home:support")],
    ]

    # Seller shop owner: Admin panel
    if is_shop_owner(shop_id, uid):
        if shop_id == get_main_shop_id() and is_super_admin(uid):
            grid.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="own:menu")])
        elif shop_id != get_main_shop_id():
            if is_panel_active(shop_id):
                grid.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="own:menu")])
            else:
                grid.append([InlineKeyboardButton("🔒 Admin Panel (Renew Subscription)", callback_data="seller:renew")])

    # Main shop: Become Seller only for normal users (and also ok for you)
    if shop_id == get_main_shop_id():
        grid.append([InlineKeyboardButton("⭐ Become Seller", callback_data="seller:info")])

    # Switch shop shortcut
    if shop_id != get_main_shop_id():
        grid.append([InlineKeyboardButton("⬅️ Back to Main Shop", callback_data="shop:switch:main")])
    else:
        sid = get_shop_by_owner(uid)
        if sid and sid != get_main_shop_id():
            grid.append([InlineKeyboardButton("🏪 My Seller Shop", callback_data=f"shop:switch:{sid}")])

    # Super admin platform controls
    if shop_id == get_main_shop_id() and is_super_admin(uid):
        grid.append([InlineKeyboardButton("🧾 Platform", callback_data="sa:menu")])

    return InlineKeyboardMarkup(grid)

def kb_wallet(shop_id: int, uid: int) -> InlineKeyboardMarkup:
    # wallet address only shown in wallet screen
    buttons = [
        [InlineKeyboardButton("💳 Deposit", callback_data="wallet:deposit"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ]
    if is_shop_owner(shop_id, uid):
        buttons.insert(0, [InlineKeyboardButton("💰 Edit User Balance", callback_data="own:users:0")])
    return InlineKeyboardMarkup(buttons)

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

def kb_owner_menu(shop_id: int) -> InlineKeyboardMarkup:
    # For seller shop owner OR super admin in main shop
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="own:users:0"),
         InlineKeyboardButton("💳 Deposits", callback_data="own:deps:0")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="own:broadcast"),
         InlineKeyboardButton("✏️ Edit Store", callback_data="own:editstore")],
        [InlineKeyboardButton("📂 Categories", callback_data="own:cats"),
         InlineKeyboardButton("🧩 Co-Categories", callback_data="own:subs")],
        [InlineKeyboardButton("📦 Products", callback_data="own:products"),
         InlineKeyboardButton("🔑 Keys", callback_data="own:keys")],
        [InlineKeyboardButton("💳 Wallet Address", callback_data="own:walletaddr"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")],
    ])

def kb_sa_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏪 Shops", callback_data="sa:shops:0"),
         InlineKeyboardButton("✏️ Become Seller Desc", callback_data="sa:become_desc")],
        [InlineKeyboardButton("📢 Global Broadcast", callback_data="sa:broadcast"),
         InlineKeyboardButton("💳 Platform Wallet", callback_data="sa:platform_wallet")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

# ============================================================
# START
# ============================================================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id

    ctx.user_data.setdefault("active_shop_id", get_main_shop_id())
    ctx.user_data["flow"] = None

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

    await send_clean(update, ctx, shop_home_text(shop_id, uid), reply_markup=kb_home(shop_id, uid))

# ============================================================
# NOTE:
# Part 2 will include:
# - FULL CALLBACK HANDLER (buttons, admin panel, deposits inline approve/reject, become seller, renew)
# - Products flow, history flow, support flow
# ============================================================

# ===================== BOOT =====================
async def post_init(app):
    init_db()

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    if SUPER_ADMIN_ID == 0:
        raise RuntimeError("ADMIN_ID missing")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))  # defined in Part 2
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_photo))  # defined in Part 3
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_text))   # defined in Part 3

    app.run_polling()

if __name__ == "__main__":
    main()

# ============================================================
# CALLBACKS (Part 2/3)
# ============================================================

def _admin_guard(shop_id: int, uid: int) -> Tuple[bool, str]:
    # super admin has admin in main shop
    if shop_id == get_main_shop_id() and is_super_admin(uid):
        return True, ""
    # seller shop: only owner + active subscription
    if not is_shop_owner(shop_id, uid):
        return False, "Not authorized"
    if not is_panel_active(shop_id):
        return False, "Subscription expired. Renew via Main Shop."
    return True, ""

def _deposit_inline_kb(dep_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"dep:approve:{dep_id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"dep:reject:{dep_id}")],
        [InlineKeyboardButton("✏️ Edit Amount", callback_data=f"dep:edit_amt:{dep_id}"),
         InlineKeyboardButton("📝 Edit Note", callback_data=f"dep:edit_note:{dep_id}")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def _deposit_caption(d) -> str:
    kind = (d["kind"] or "TOPUP")
    base = (
        f"💳 Deposit #{d['id']}\n"
        f"Shop: #{d['shop_id']}\n"
        f"User: {d['user_id']}\n"
        f"Amount: {money(int(d['amount_cents']))}\n"
        f"Kind: {kind}\n"
    )
    if kind in ("SUB_NEW", "SUB_RENEW"):
        base += f"Target Shop: #{int(d['target_shop_id'] or 0)}\n"
    base += f"Note: {d['caption'] or '-'}\nStatus: {d['status']}"
    return base

def _reset_flow(ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["flow"] = None
    for k in [
        "dep_amount", "target_deposit",
        "selected_user", "selected_user_page",
        "pid", "cat_id", "sub_id",
        "broadcast_scope", "broadcast_shop_id",
        "seller_target_shop",
        "edit_return_cb",
    ]:
        ctx.user_data.pop(k, None)

def _owner_can_manage_shop(shop_id: int, uid: int) -> bool:
    if shop_id == get_main_shop_id():
        return is_super_admin(uid)
    return is_shop_owner(shop_id, uid) and is_panel_active(shop_id)

def _shop_switch_allowed(shop_id: int, uid: int) -> bool:
    if shop_id == get_main_shop_id():
        return True
    # users can browse seller shops; owners may have expired admin but still browse shop
    return True

async def _stay_or_back_to(ctx: ContextTypes.DEFAULT_TYPE, q, text: str, stay_cb: Optional[str], fallback_cb: str, reply_markup: Optional[InlineKeyboardMarkup] = None, parse_mode=None):
    # helper so "after edit product price/link" we can stay on product view instead of home
    if reply_markup is not None:
        return await q.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    if stay_cb:
        return await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=stay_cb)]]), parse_mode=parse_mode)
    return await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=fallback_cb)]]), parse_mode=parse_mode)

# ============================================================
# MAIN CALLBACK HANDLER
# ============================================================
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    upsert_user(q.from_user)

    uid = q.from_user.id
    shop_id = get_active_shop_id(ctx)
    ensure_shop_user(shop_id, uid)

    data = (q.data or "").strip()

    # Suspension guard (allow switching/home)
    suspended, reason = shop_is_suspended(shop_id)
    if suspended and not data.startswith("shop:switch:") and data != "home:menu":
        return await q.edit_message_text(
            "⛔ This shop is suspended.\n\n" + (f"Reason: {reason}" if reason else "") + f"\n\n{DEFAULT_BRAND}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Shop", callback_data="shop:switch:main")]])
        )

    # ===================== HOME RESET =====================
    if data == "home:menu":
        _reset_flow(ctx)
        shop_id = get_active_shop_id(ctx)
        return await q.edit_message_text(shop_home_text(shop_id, uid), reply_markup=kb_home(shop_id, uid))

    # ===================== SWITCH SHOP =====================
    if data.startswith("shop:switch:"):
        _, _, sid_s = data.split(":", 2)
        if sid_s == "main":
            set_active_shop_id(ctx, get_main_shop_id())
        else:
            try:
                sid = int(sid_s)
                if get_shop(sid):
                    set_active_shop_id(ctx, sid)
            except Exception:
                set_active_shop_id(ctx, get_main_shop_id())

        _reset_flow(ctx)
        shop_id = get_active_shop_id(ctx)
        return await q.edit_message_text(shop_home_text(shop_id, uid), reply_markup=kb_home(shop_id, uid))

    # ===================== WALLET SCREEN (address ONLY here) =====================
    if data == "home:wallet":
        bal = get_balance(shop_id, uid)
        addr = get_shop_wallet(shop_id)
        addr_txt = addr if addr else "⚠️ Wallet address not set (owner must set it)"
        txt = f"💰 Wallet\n\nBalance: {money(bal)}\n\nUSDT (TRC-20) Address:\n`{addr_txt}`"
        return await q.edit_message_text(txt, reply_markup=kb_wallet(shop_id, uid), parse_mode=ParseMode.MARKDOWN)

    # Wallet deposit
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

    # ===================== PRODUCTS ROOT =====================
    if data == "home:products":
        return await q.edit_message_text("🛍️ Products", reply_markup=kb_products_root())

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

    # ===================== HISTORY =====================
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

    # ===================== SUPPORT =====================
    if data == "home:support":
        ctx.user_data["flow"] = "support_send"
        return await q.edit_message_text("📩 Support\n\nType your message to the shop owner:", reply_markup=kb_back_home())

    # ===================== BECOME SELLER =====================
    if data == "seller:info":
        # only on main shop
        if shop_id != get_main_shop_id():
            return await q.answer("Open this in Main Shop.", show_alert=True)

        desc = become_seller_desc()
        sid = get_shop_by_owner(uid)
        has_shop = bool(sid and sid != get_main_shop_id())

        txt = (
            f"{desc}\n"
            f"Price: {money(SELLER_PRICE_CENTS)} / {SELLER_DAYS} days\n\n"
            f"Platform wallet (pay subscription here):\n`{platform_wallet() or 'NOT SET'}`"
        )

        kb_rows = []
        if has_shop:
            s = get_shop(sid)
            left = days_left(s["panel_until"])
            kb_rows.append([InlineKeyboardButton("🏪 Go To My Seller Shop", callback_data=f"shop:switch:{sid}")])
            kb_rows.append([InlineKeyboardButton("🔁 Renew Subscription", callback_data="seller:renew")])
            txt += f"\n\nYour Seller Shop: #{sid}\nDays left: {left}"
        else:
            kb_rows.append([InlineKeyboardButton("✅ Buy Become Seller", callback_data="seller:buy")])

        kb_rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

    if data == "seller:buy":
        if shop_id != get_main_shop_id():
            return await q.answer("Open this in Main Shop.", show_alert=True)

        # create shop first, then user deposits screenshot to platform wallet for approval
        sid = create_shop_for_owner(uid)
        ctx.user_data["seller_target_shop"] = sid
        ctx.user_data["flow"] = "seller_sub_new_wait_photo"

        txt = (
            "✅ Become Seller started.\n\n"
            f"Step 1: Pay subscription to platform wallet:\n`{platform_wallet() or 'NOT SET'}`\n\n"
            f"Amount: {money(SELLER_PRICE_CENTS)}\n\n"
            "Step 2: Send payment screenshot (photo) now."
        )
        return await q.edit_message_text(txt, reply_markup=kb_back_home(), parse_mode=ParseMode.MARKDOWN)

    if data == "seller:renew":
        # must be in main shop
        if shop_id != get_main_shop_id():
            return await q.answer("Renewal is done via Main Shop.", show_alert=True)

        sid = get_shop_by_owner(uid)
        if not sid or sid == get_main_shop_id():
            return await q.answer("You do not have a seller shop yet. Buy Become Seller first.", show_alert=True)

        ctx.user_data["seller_target_shop"] = sid
        ctx.user_data["flow"] = "seller_sub_renew_wait_photo"

        s = get_shop(sid)
        left = days_left(s["panel_until"])
        txt = (
            "🔁 Renew Subscription\n\n"
            f"Your Seller Shop: #{sid}\n"
            f"Current days left: {left}\n\n"
            f"Pay to platform wallet:\n`{platform_wallet() or 'NOT SET'}`\n"
            f"Amount: {money(SELLER_PRICE_CENTS)}\n\n"
            "Now send payment screenshot (photo)."
        )
        return await q.edit_message_text(txt, reply_markup=kb_back_home(), parse_mode=ParseMode.MARKDOWN)

    # ===================== ADMIN PANEL =====================
    if data == "own:menu":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        _reset_flow(ctx)
        return await q.edit_message_text("🛠️ Admin Panel", reply_markup=kb_owner_menu(shop_id))

    if data == "own:editstore":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        ctx.user_data["flow"] = "own_edit_store"
        return await q.edit_message_text(
            "✏️ Edit Store\n\nSend this format:\n\nName | Welcome text\n\nExample:\nMyShop | Welcome to my shop!",
            reply_markup=kb_owner_menu(shop_id)
        )

    if data == "own:walletaddr":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        addr = get_shop_wallet(shop_id)
        ctx.user_data["flow"] = "own_wallet_edit"
        return await q.edit_message_text(
            "💳 Wallet Address\n\n"
            f"Current:\n{addr or 'Not set'}\n\n"
            "Send new wallet address (or send - to clear):",
            reply_markup=kb_owner_menu(shop_id)
        )

    # ===================== ADMIN: BROADCAST =====================
    if data == "own:broadcast":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        ctx.user_data["flow"] = "broadcast_send"
        ctx.user_data["broadcast_scope"] = "SHOP"
        ctx.user_data["broadcast_shop_id"] = shop_id
        return await q.edit_message_text(
            "📢 Broadcast\n\nSend message to ALL users in THIS shop.\n\nType your broadcast message now:",
            reply_markup=kb_owner_menu(shop_id)
        )

    # ===================== ADMIN: USERS =====================
    if data.startswith("own:users:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        page = int(data.split(":")[-1])
        total = count_shop_users(shop_id)
        rowsu = list_shop_users(shop_id, PAGE_SIZE, page * PAGE_SIZE)
        if not rowsu:
            return await q.edit_message_text("No users yet.", reply_markup=kb_owner_menu(shop_id))

        btns = []
        for r in rowsu:
            uname = ("@" + r["username"]) if r["username"] else ""
            btns.append(InlineKeyboardButton(f"{r['user_id']} {uname} • {money(int(r['balance_cents']))}",
                                             callback_data=f"own:user:{r['user_id']}:{page}"))
        kb = rows(btns, 1)

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"own:users:{page-1}"))
        if (page + 1) * PAGE_SIZE < total:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"own:users:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text(f"👥 Users (Total {total})", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:user:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)

        parts = data.split(":")
        target_uid = int(parts[2])
        page = int(parts[3])

        ensure_shop_user(shop_id, target_uid)
        bal = get_balance(shop_id, target_uid)

        ctx.user_data["selected_user"] = target_uid
        ctx.user_data["selected_user_page"] = page

        txt = f"👤 User {target_uid}\n\nBalance: {money(bal)}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Reply", callback_data="own:reply"),
             InlineKeyboardButton("💰 Edit Balance", callback_data="own:balmenu")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:users:{page}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data == "own:reply":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        target_uid = int(ctx.user_data.get("selected_user", 0))
        if not target_uid:
            return await q.answer("Select a user first.", show_alert=True)
        ctx.user_data["flow"] = "own_reply_user"
        return await q.edit_message_text(
            f"💬 Reply to {target_uid}\n\nType your message:",
            reply_markup=kb_owner_menu(shop_id)
        )

    if data == "own:balmenu":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)

        target_uid = int(ctx.user_data.get("selected_user", 0))
        if not target_uid:
            return await q.answer("Select a user first.", show_alert=True)

        bal = get_balance(shop_id, target_uid)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add", callback_data="own:bal_add"),
             InlineKeyboardButton("➖ Subtract", callback_data="own:bal_sub")],
            [InlineKeyboardButton("🧾 Set Exact", callback_data="own:bal_set"),
             InlineKeyboardButton("⬅️ Back", callback_data=f"own:user:{target_uid}:{int(ctx.user_data.get('selected_user_page',0))}")],
        ])
        return await q.edit_message_text(
            f"💰 Edit Balance for {target_uid}\n\nCurrent: {money(bal)}\n\nChoose:",
            reply_markup=kb
        )

    if data in ("own:bal_add", "own:bal_sub", "own:bal_set"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        target_uid = int(ctx.user_data.get("selected_user", 0))
        if not target_uid:
            return await q.answer("Select a user first.", show_alert=True)

        ctx.user_data["flow"] = data
        hint = "Send amount to ADD:" if data == "own:bal_add" else ("Send amount to SUBTRACT:" if data == "own:bal_sub" else "Send new exact balance:")
        return await q.edit_message_text(f"{hint}\n(example 10 or 10.5)", reply_markup=kb_owner_menu(shop_id))

    # ===================== ADMIN: DEPOSITS LIST =====================
    if data.startswith("own:deps:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)

        page = int(data.split(":")[-1])
        deps = list_pending_deposits(shop_id, PAGE_SIZE, page * PAGE_SIZE)
        if not deps:
            return await q.edit_message_text("💳 No pending deposits.", reply_markup=kb_owner_menu(shop_id))

        btns = []
        for d in deps:
            kind = d["kind"]
            tag = "⭐" if kind in ("SUB_NEW", "SUB_RENEW") else "💳"
            btns.append(InlineKeyboardButton(f"{tag} #{d['id']} • {d['user_id']} • {money(int(d['amount_cents']))}",
                                             callback_data=f"own:dep:{d['id']}:{page}"))
        kb = rows(btns, 1)
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"own:deps:{page-1}"))
        if len(deps) == PAGE_SIZE:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"own:deps:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("💳 Pending deposits:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:dep:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)

        parts = data.split(":")
        dep_id = int(parts[2])
        page = int(parts[3])
        d = get_deposit(shop_id, dep_id)
        if not d:
            return await q.answer("Not found", show_alert=True)

        # show deposit details (inline approve/reject also exists in admin incoming message)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"dep:approve:{dep_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"dep:reject:{dep_id}")],
            [InlineKeyboardButton("✏️ Edit Amount", callback_data=f"dep:edit_amt:{dep_id}"),
             InlineKeyboardButton("📝 Edit Note", callback_data=f"dep:edit_note:{dep_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:deps:{page}")]
        ])

        await q.edit_message_text(_deposit_caption(d), reply_markup=kb)
        try:
            await ctx.bot.send_photo(chat_id=q.message.chat_id, photo=d["photo_file_id"])
        except Exception:
            pass
        return

    # ===================== INLINE APPROVE/REJECT (works from admin incoming deposit message too) =====================
    if data.startswith("dep:approve:") or data.startswith("dep:reject:") or data.startswith("dep:edit_amt:") or data.startswith("dep:edit_note:"):
        dep_id = int(data.split(":")[-1])

        # Find deposit - NOTE: deposits are stored under shop_id where user deposited (TOPUP uses that shop)
        # Subscription deposits are stored in MAIN SHOP (shop_id=1)
        # We must locate deposit by trying active shop then main shop.
        d = get_deposit(shop_id, dep_id)
        if not d:
            d = get_deposit(get_main_shop_id(), dep_id)

        if not d:
            return await q.answer("Deposit not found.", show_alert=True)

        dep_shop_id = int(d["shop_id"])

        # Only shop owner of that deposit shop can approve
        # For MAIN SHOP deposits (incl subscription), only super admin can approve (you)
        if dep_shop_id == get_main_shop_id():
            if not is_super_admin(uid):
                return await q.answer("Not authorized.", show_alert=True)
        else:
            if not _owner_can_manage_shop(dep_shop_id, uid):
                return await q.answer("Not authorized.", show_alert=True)

        if d["status"] != "PENDING":
            return await q.answer("Already processed.", show_alert=True)

        if data.startswith("dep:edit_amt:"):
            ctx.user_data["flow"] = "dep_edit_amount_admin"
            ctx.user_data["target_deposit"] = int(d["id"])
            # remember where to return (stay on same deposit view)
            ctx.user_data["edit_return_cb"] = f"own:dep:{int(d['id'])}:0"
            return await q.edit_message_text(
                f"✏️ Edit Deposit Amount\n\nCurrent: {money(int(d['amount_cents']))}\n\nSend new amount:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home:menu")]])
            )

        if data.startswith("dep:edit_note:"):
            ctx.user_data["flow"] = "dep_edit_note_admin"
            ctx.user_data["target_deposit"] = int(d["id"])
            ctx.user_data["edit_return_cb"] = f"own:dep:{int(d['id'])}:0"
            return await q.edit_message_text(
                f"📝 Edit Deposit Note\n\nCurrent:\n{d['caption'] or '-'}\n\nSend new note:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home:menu")]])
            )

        # Approve / Reject
        if data.startswith("dep:approve:"):
            set_deposit_status(dep_shop_id, dep_id, "APPROVED", uid)

            kind = (d["kind"] or "TOPUP")
            if kind == "TOPUP":
                add_balance_delta(dep_shop_id, int(d["user_id"]), int(d["amount_cents"]))
                # notify user
                try:
                    await ctx.bot.send_message(
                        chat_id=int(d["user_id"]),
                        text=f"✅ Deposit approved!\nShop #{dep_shop_id}\nAmount: {money(int(d['amount_cents']))}"
                    )
                except Exception:
                    pass
                return await q.edit_message_text("✅ Deposit approved and balance added.", reply_markup=kb_back_home())

            # SUB_NEW or SUB_RENEW: activate/extend seller panel for target_shop_id
            target_shop = int(d["target_shop_id"] or 0)
            if target_shop <= 0:
                return await q.edit_message_text("⚠️ Missing target shop. Cannot activate subscription.", reply_markup=kb_back_home())

            extend_panel(target_shop, SELLER_DAYS)

            # notify seller
            try:
                s = get_shop(target_shop)
                left = days_left(s["panel_until"]) if s else 0
                await ctx.bot.send_message(
                    chat_id=int(d["user_id"]),
                    text=f"✅ Subscription activated!\nYour Seller Shop: #{target_shop}\nDays left: {left}\n\nGo to your shop from Main Menu → My Seller Shop."
                )
            except Exception:
                pass

            return await q.edit_message_text("✅ Subscription approved and activated.", reply_markup=kb_back_home())

        if data.startswith("dep:reject:"):
            set_deposit_status(dep_shop_id, dep_id, "REJECTED", uid)
            try:
                await ctx.bot.send_message(
                    chat_id=int(d["user_id"]),
                    text=f"❌ Deposit rejected.\nShop #{dep_shop_id}\nDeposit #{dep_id}\n\nIf this is a mistake, contact support."
                )
            except Exception:
                pass
            return await q.edit_message_text("❌ Deposit rejected.", reply_markup=kb_back_home())

    # ===================== ADMIN: CATEGORIES =====================
    if data == "own:cats":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        cats = list_categories(shop_id, active_only=False)
        btns = [InlineKeyboardButton(("✅ " if int(c["is_active"]) == 1 else "🚫 ") + c["name"], callback_data=f"own:cat_toggle:{c['id']}") for c in cats]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("➕ Add Category", callback_data="own:cat_add"),
                   InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("📂 Categories (tap to enable/disable):", reply_markup=InlineKeyboardMarkup(kb))

    if data == "own:cat_add":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        ctx.user_data["flow"] = "own_cat_add"
        return await q.edit_message_text("➕ Send category name:", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:cat_toggle:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        cat_id = int(data.split(":")[-1])
        toggle_category(shop_id, cat_id)
        return await q.edit_message_text("✅ Updated.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data="own:cats"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # ===================== ADMIN: CO-CATEGORIES =====================
    if data == "own:subs":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        cats = list_categories(shop_id, active_only=False)
        if not cats:
            return await q.edit_message_text("No categories yet. Add category first.", reply_markup=kb_owner_menu(shop_id))
        btns = [InlineKeyboardButton(c["name"], callback_data=f"own:subs_in:{c['id']}") for c in cats]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("🧩 Choose category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:subs_in:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        cat_id = int(data.split(":")[-1])
        subs = list_subcategories(shop_id, cat_id, active_only=False)
        btns = [InlineKeyboardButton(("✅ " if int(s["is_active"]) == 1 else "🚫 ") + s["name"], callback_data=f"own:sub_toggle:{s['id']}:{cat_id}") for s in subs]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("➕ Add Co-Category", callback_data=f"own:sub_add:{cat_id}"),
                   InlineKeyboardButton("⬅️ Back", callback_data="own:subs")])
        return await q.edit_message_text("🧩 Co-categories (tap to enable/disable):", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:sub_add:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        cat_id = int(data.split(":")[-1])
        ctx.user_data["flow"] = "own_sub_add"
        ctx.user_data["cat_id"] = cat_id
        return await q.edit_message_text("➕ Send co-category name:", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:sub_toggle:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        parts = data.split(":")
        sub_id = int(parts[2])
        cat_id = int(parts[3])
        toggle_subcategory(shop_id, sub_id)
        return await q.edit_message_text("✅ Updated.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:subs_in:{cat_id}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # ===================== ADMIN: PRODUCTS =====================
    if data == "own:products":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        cats = list_categories(shop_id, active_only=False)
        if not cats:
            return await q.edit_message_text("Add a category first.", reply_markup=kb_owner_menu(shop_id))
        btns = [InlineKeyboardButton(c["name"], callback_data=f"own:prod_cat:{c['id']}") for c in cats]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("📦 Choose category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:prod_cat:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        cat_id = int(data.split(":")[-1])
        subs = list_subcategories(shop_id, cat_id, active_only=False)
        if not subs:
            return await q.edit_message_text("Add a co-category first.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="own:products"),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ]))
        btns = [InlineKeyboardButton(s["name"], callback_data=f"own:prod_sub:{s['id']}:{cat_id}") for s in subs]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:products"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📦 Choose co-category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:prod_sub:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        parts = data.split(":")
        sub_id = int(parts[2])
        cat_id = int(parts[3])
        prods = list_products_by_subcat(shop_id, sub_id, active_only=False)
        btns = [InlineKeyboardButton((("✅ " if int(p["is_active"])==1 else "🚫 ") + p["name"] + f" (ID {p['id']})"),
                                    callback_data=f"own:prod_view:{p['id']}:{sub_id}:{cat_id}") for p in prods]
        kb = rows(btns, 1)
        kb.append([InlineKeyboardButton("➕ Add Product", callback_data=f"own:prod_add:{sub_id}:{cat_id}"),
                   InlineKeyboardButton("⬅️ Back", callback_data=f"own:prod_cat:{cat_id}")])
        return await q.edit_message_text("📦 Products:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:prod_add:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        parts = data.split(":")
        sub_id = int(parts[2])
        cat_id = int(parts[3])
        ctx.user_data["flow"] = "own_prod_add"
        ctx.user_data["sub_id"] = sub_id
        ctx.user_data["cat_id"] = cat_id
        return await q.edit_message_text(
            "➕ Add Product\n\nSend format:\nName | user_price\n\nExample:\nPUBG Key | 10",
            reply_markup=kb_owner_menu(shop_id)
        )

    if data.startswith("own:prod_view:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        parts = data.split(":")
        pid = int(parts[2])
        sub_id = int(parts[3])
        cat_id = int(parts[4])
        p = get_product(shop_id, pid)
        if not p:
            return await q.answer("Not found", show_alert=True)

        txt = (
            f"📦 {p['name']} (ID {p['id']})\n\n"
            f"User price: {money(int(p['user_price_cents']))}\n"
            f"Stock: {int(p['stock'])}\n"
            f"Active: {'YES' if int(p['is_active'])==1 else 'NO'}\n"
            f"Link: {(p['telegram_link'] or '-').strip()}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Toggle Active", callback_data=f"own:prod_toggle:{pid}:{sub_id}:{cat_id}"),
             InlineKeyboardButton("🔗 Edit Link", callback_data=f"own:prod_link:{pid}:{sub_id}:{cat_id}")],
            [InlineKeyboardButton("💲 Edit Price", callback_data=f"own:prod_price:{pid}:{sub_id}:{cat_id}"),
             InlineKeyboardButton("🔑 Add Keys", callback_data=f"own:keys_for:{pid}:{sub_id}:{cat_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:prod_sub:{sub_id}:{cat_id}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("own:prod_toggle:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        parts = data.split(":")
        pid = int(parts[2])
        sub_id = int(parts[3])
        cat_id = int(parts[4])
        toggle_product(shop_id, pid)
        # stay on product view
        return await q.edit_message_text("✅ Updated.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:prod_view:{pid}:{sub_id}:{cat_id}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    if data.startswith("own:prod_link:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        parts = data.split(":")
        pid = int(parts[2])
        sub_id = int(parts[3])
        cat_id = int(parts[4])
        ctx.user_data["flow"] = "own_prod_link"
        ctx.user_data["pid"] = pid
        ctx.user_data["edit_return_cb"] = f"own:prod_view:{pid}:{sub_id}:{cat_id}"
        return await q.edit_message_text("🔗 Send Telegram link (or send - to clear):", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:prod_price:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        parts = data.split(":")
        pid = int(parts[2])
        sub_id = int(parts[3])
        cat_id = int(parts[4])
        ctx.user_data["flow"] = "own_prod_price"
        ctx.user_data["pid"] = pid
        ctx.user_data["edit_return_cb"] = f"own:prod_view:{pid}:{sub_id}:{cat_id}"
        return await q.edit_message_text("💲 Send new user price (example 10 or 10.5):", reply_markup=kb_owner_menu(shop_id))

    # ===================== ADMIN: KEYS =====================
    if data == "own:keys":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        ctx.user_data["flow"] = None
        return await q.edit_message_text("🔑 Keys\n\nOpen a product → tap “🔑 Add Keys”.", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:keys_for:"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            return await q.answer(msg, show_alert=True)
        parts = data.split(":")
        pid = int(parts[2])
        sub_id = int(parts[3])
        cat_id = int(parts[4])
        p = get_product(shop_id, pid)
        if not p:
            return await q.answer("Product not found", show_alert=True)
        ctx.user_data["flow"] = "own_keys_add"
        ctx.user_data["pid"] = pid
        ctx.user_data["edit_return_cb"] = f"own:prod_view:{pid}:{sub_id}:{cat_id}"
        return await q.edit_message_text(
            f"🔑 Add Keys for: {p['name']} (ID {pid})\n\nSend keys (one per line):",
            reply_markup=kb_owner_menu(shop_id)
        )

    # ===================== SUPER ADMIN PLATFORM =====================
    if data == "sa:menu":
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        return await q.edit_message_text("🧾 Platform", reply_markup=kb_sa_menu())

    if data.startswith("sa:shops:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        rows_s = list_shops(PAGE_SIZE, page * PAGE_SIZE)
        if not rows_s:
            return await q.edit_message_text("No shops.", reply_markup=kb_sa_menu())
        btns = []
        for r in rows_s:
            tag = "⛔" if int(r["is_suspended"]) == 1 else "✅"
            btns.append(InlineKeyboardButton(f"{tag} #{r['id']} • owner {r['owner_id']} • {r['shop_name']}",
                                             callback_data=f"sa:shop:{r['id']}:{page}"))
        kb = rows(btns, 1)
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"sa:shops:{page-1}"))
        if len(rows_s) == PAGE_SIZE:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"sa:shops:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="sa:menu")])
        return await q.edit_message_text("🏪 Shops:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("sa:shop:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        sid = int(parts[2])
        page = int(parts[3])
        s = get_shop(sid)
        if not s:
            return await q.answer("Not found", show_alert=True)

        txt = (
            f"🏪 Shop #{sid}\n\n"
            f"Owner: {s['owner_id']}\n"
            f"Name: {s['shop_name']}\n"
            f"Panel until: {s['panel_until'] or '-'}\n"
            f"Suspended: {'YES' if int(s['is_suspended'])==1 else 'NO'}\n"
            f"Reason: {s['suspended_reason'] or '-'}\n"
            f"Wallet: {(s['wallet_address'] or '-')}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⛔ Suspend", callback_data=f"sa:suspend:{sid}:{page}"),
             InlineKeyboardButton("✅ Unsuspend", callback_data=f"sa:unsuspend:{sid}:{page}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"sa:shops:{page}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")],
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("sa:suspend:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        sid = int(parts[2])
        ctx.user_data["flow"] = "sa_suspend_reason"
        ctx.user_data["sid"] = sid
        return await q.edit_message_text("⛔ Send suspension reason:", reply_markup=kb_sa_menu())

    if data.startswith("sa:unsuspend:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        sid = int(data.split(":")[2])
        set_shop_suspension(sid, False, None)
        return await q.edit_message_text("✅ Unsuspended.", reply_markup=kb_sa_menu())

    if data == "sa:become_desc":
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = "sa_become_desc_edit"
        return await q.edit_message_text("✏️ Send new Become Seller description text:", reply_markup=kb_sa_menu())

    if data == "sa:platform_wallet":
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = "sa_platform_wallet_edit"
        return await q.edit_message_text("💳 Send new PLATFORM wallet address (or - to clear):", reply_markup=kb_sa_menu())

    if data == "sa:broadcast":
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = "broadcast_send"
        ctx.user_data["broadcast_scope"] = "GLOBAL"
        return await q.edit_message_text(
            "📢 Global Broadcast\n\nSend message to ALL users of the bot.\n\nType your broadcast message now:",
            reply_markup=kb_sa_menu()
        )

    return

# SAFETY ALIAS (prevents NameError)
on_callback = on_cb

# ============================================================
# TEXT HANDLER + PHOTO HANDLER + BOOT (Part 3/3)
# ============================================================

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id

    shop_id = get_active_shop_id(ctx)
    ensure_shop_user(shop_id, uid)

    text = (update.message.text or "").strip()
    flow = ctx.user_data.get("flow")

    # -------------------- Deposit custom amount --------------------
    if flow == "dep_custom":
        amt = to_cents(text)
        if amt is None:
            return await send_clean(update, ctx, "Send amount like 10 or 10.5", reply_markup=kb_back_home())
        ctx.user_data["flow"] = "dep_wait_photo"
        ctx.user_data["dep_amount"] = amt
        return await send_clean(update, ctx, f"✅ Amount set: {money(amt)}\nNow send screenshot (photo).", reply_markup=kb_back_home())

    # -------------------- Support --------------------
    if flow == "support_send":
        add_support_msg(shop_id, uid, text)
        owner_id = int(get_shop(shop_id)["owner_id"])
        try:
            await ctx.bot.send_message(
                chat_id=owner_id,
                text=f"📩 Support (Shop #{shop_id})\nFrom: {uid}\n\n{text}"
            )
        except Exception:
            pass
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Sent to owner.", reply_markup=kb_home(shop_id, uid))

    # -------------------- Admin: Edit store --------------------
    if flow == "own_edit_store":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, msg, reply_markup=kb_home(shop_id, uid))
        if "|" not in text:
            return await send_clean(update, ctx, "Format: Name | Welcome text", reply_markup=kb_owner_menu(shop_id))
        name, welcome = [x.strip() for x in text.split("|", 1)]
        if not name or not welcome:
            return await send_clean(update, ctx, "Format: Name | Welcome text", reply_markup=kb_owner_menu(shop_id))
        set_shop_profile(shop_id, name, welcome)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Store updated.", reply_markup=kb_owner_menu(shop_id))

    # -------------------- Admin: Wallet edit --------------------
    if flow == "own_wallet_edit":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, msg, reply_markup=kb_home(shop_id, uid))

        if text == "-":
            set_shop_wallet(shop_id, None)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "✅ Wallet address cleared.", reply_markup=kb_owner_menu(shop_id))

        if len(text) < 10:
            return await send_clean(update, ctx, "Invalid wallet address.", reply_markup=kb_owner_menu(shop_id))

        set_shop_wallet(shop_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Wallet address updated.", reply_markup=kb_owner_menu(shop_id))

    # -------------------- Admin: Category add --------------------
    if flow == "own_cat_add":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, msg, reply_markup=kb_home(shop_id, uid))
        add_category(shop_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Category added.", reply_markup=kb_owner_menu(shop_id))

    # -------------------- Admin: Co-category add --------------------
    if flow == "own_sub_add":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, msg, reply_markup=kb_home(shop_id, uid))
        cat_id = int(ctx.user_data.get("cat_id", 0))
        if cat_id <= 0:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Category missing.", reply_markup=kb_owner_menu(shop_id))
        add_subcategory(shop_id, cat_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Co-category added.", reply_markup=kb_owner_menu(shop_id))

    # -------------------- Admin: Product add --------------------
    if flow == "own_prod_add":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, msg, reply_markup=kb_home(shop_id, uid))
        if "|" not in text:
            return await send_clean(update, ctx, "Format: Name | user_price", reply_markup=kb_owner_menu(shop_id))
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 2:
            return await send_clean(update, ctx, "Format: Name | user_price", reply_markup=kb_owner_menu(shop_id))
        name = parts[0]
        up = to_cents(parts[1])
        if not name or up is None:
            return await send_clean(update, ctx, "Invalid values.", reply_markup=kb_owner_menu(shop_id))
        sub_id = int(ctx.user_data.get("sub_id", 0))
        cat_id = int(ctx.user_data.get("cat_id", 0))
        add_product(shop_id, cat_id, sub_id, name, up, up)  # reseller_price kept same in this version
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Product added.", reply_markup=kb_owner_menu(shop_id))

    # -------------------- Admin: Edit product link (STAY THERE) --------------------
    if flow == "own_prod_link":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, msg, reply_markup=kb_home(shop_id, uid))

        pid = int(ctx.user_data.get("pid", 0))
        if text == "-":
            update_product_link(shop_id, pid, None)
        else:
            update_product_link(shop_id, pid, text)

        stay_cb = ctx.user_data.get("edit_return_cb")
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Link updated.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=stay_cb or "own:products")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # -------------------- Admin: Edit product price (STAY THERE) --------------------
    if flow == "own_prod_price":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, msg, reply_markup=kb_home(shop_id, uid))

        up = to_cents(text)
        if up is None:
            return await send_clean(update, ctx, "Send amount like 10 or 10.5", reply_markup=kb_owner_menu(shop_id))

        pid = int(ctx.user_data.get("pid", 0))
        p = get_product(shop_id, pid)
        if not p:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Product not found.", reply_markup=kb_owner_menu(shop_id))

        update_product_prices(shop_id, pid, up, up)  # reseller_price kept same
        stay_cb = ctx.user_data.get("edit_return_cb")
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Price updated.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=stay_cb or "own:products")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # -------------------- Admin: Add keys (STAY THERE) --------------------
    if flow == "own_keys_add":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, msg, reply_markup=kb_home(shop_id, uid))

        pid = int(ctx.user_data.get("pid", 0))
        keys = text.splitlines()
        n = add_keys(shop_id, pid, keys)
        stay_cb = ctx.user_data.get("edit_return_cb")
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, f"✅ Added {n} keys.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=stay_cb or "own:products")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # -------------------- Admin: Reply user --------------------
    if flow == "own_reply_user":
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, msg, reply_markup=kb_home(shop_id, uid))
        target_uid = int(ctx.user_data.get("selected_user", 0))
        if not target_uid:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Select a user first.", reply_markup=kb_owner_menu(shop_id))
        try:
            await ctx.bot.send_message(chat_id=target_uid, text=f"📩 Reply from shop owner:\n\n{text}")
        except Exception:
            pass
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Sent.", reply_markup=kb_owner_menu(shop_id))

    # -------------------- Admin: Balance edit --------------------
    if flow in ("own:bal_add", "own:bal_sub", "own:bal_set"):
        ok, msg = _admin_guard(shop_id, uid)
        if not ok:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, msg, reply_markup=kb_home(shop_id, uid))

        target_uid = int(ctx.user_data.get("selected_user", 0))
        amt = to_cents(text)
        if amt is None:
            return await send_clean(update, ctx, "Send amount like 10 or 10.5", reply_markup=kb_owner_menu(shop_id))

        if flow == "own:bal_add":
            add_balance_delta(shop_id, target_uid, amt)
        elif flow == "own:bal_sub":
            add_balance_delta(shop_id, target_uid, -amt)
        else:
            set_balance_absolute(shop_id, target_uid, amt)

        ctx.user_data["flow"] = None
        newb = get_balance(shop_id, target_uid)
        return await send_clean(update, ctx, f"✅ Balance updated.\nUser {target_uid}: {money(newb)}", reply_markup=kb_owner_menu(shop_id))

    # -------------------- Deposit admin edit amount/note (inline flow) --------------------
    if flow == "dep_edit_amount_admin":
        dep_id = int(ctx.user_data.get("target_deposit", 0))
        amt = to_cents(text)
        if amt is None:
            return await send_clean(update, ctx, "Send amount like 10 or 10.5", reply_markup=kb_back_home())
        # try current shop then main shop
        d = get_deposit(shop_id, dep_id) or get_deposit(get_main_shop_id(), dep_id)
        if not d or d["status"] != "PENDING":
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Deposit not found or not pending.", reply_markup=kb_back_home())
        update_deposit_amount(int(d["shop_id"]), dep_id, amt)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, f"✅ Deposit #{dep_id} amount updated to {money(amt)}.", reply_markup=kb_back_home())

    if flow == "dep_edit_note_admin":
        dep_id = int(ctx.user_data.get("target_deposit", 0))
        d = get_deposit(shop_id, dep_id) or get_deposit(get_main_shop_id(), dep_id)
        if not d or d["status"] != "PENDING":
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Deposit not found or not pending.", reply_markup=kb_back_home())
        update_deposit_caption(int(d["shop_id"]), dep_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Deposit note updated.", reply_markup=kb_back_home())

    # -------------------- Broadcast (SHOP / GLOBAL) --------------------
    if flow == "broadcast_send":
        scope = (ctx.user_data.get("broadcast_scope") or "SHOP").upper()
        sent = 0
        failed = 0

        if scope == "GLOBAL":
            ids = all_user_ids_global()
        else:
            sid = int(ctx.user_data.get("broadcast_shop_id", shop_id))
            ids = all_user_ids_in_shop(sid)

        for tuid in ids:
            try:
                await ctx.bot.send_message(chat_id=int(tuid), text=f"📢 Broadcast\n\n{text}")
                sent += 1
            except Exception:
                failed += 1

        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, f"✅ Broadcast done.\nSent: {sent}\nFailed: {failed}", reply_markup=kb_home(shop_id, uid))

    # -------------------- Super Admin: suspend reason --------------------
    if flow == "sa_suspend_reason":
        if not is_super_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        sid = int(ctx.user_data.get("sid", 0))
        set_shop_suspension(sid, True, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "⛔ Shop suspended.", reply_markup=kb_sa_menu())

    # -------------------- Super Admin: edit Become Seller desc --------------------
    if flow == "sa_become_desc_edit":
        if not is_super_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        set_become_seller_desc(text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Become Seller description updated.", reply_markup=kb_sa_menu())

    # -------------------- Super Admin: edit PLATFORM wallet --------------------
    if flow == "sa_platform_wallet_edit":
        if not is_super_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        if text == "-":
            set_platform_wallet("")
        else:
            set_platform_wallet(text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Platform wallet updated.", reply_markup=kb_sa_menu())

    # Default ignore
    return


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id

    shop_id = get_active_shop_id(ctx)
    ensure_shop_user(shop_id, uid)

    flow = ctx.user_data.get("flow")

    # --------- Normal TOPUP deposits (to that shop wallet) ----------
    if flow == "dep_wait_photo":
        addr = get_shop_wallet(shop_id)
        if not addr:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Deposit unavailable (wallet not set).", reply_markup=kb_home(shop_id, uid))

        amt = int(ctx.user_data.get("dep_amount", 0))
        if amt <= 0:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Deposit amount missing. Wallet → Deposit again.", reply_markup=kb_home(shop_id, uid))

        file_id = update.message.photo[-1].file_id
        caption = (update.message.caption or "").strip()

        dep_id = create_deposit(shop_id, uid, amt, file_id, caption, kind="TOPUP", target_shop_id=None)
        ctx.user_data["flow"] = None

        await send_clean(update, ctx, f"✅ Deposit submitted (ID #{dep_id}). Owner will review.", reply_markup=kb_home(shop_id, uid))

        # Send to owner with inline approve/reject buttons (NO need to open deposits list)
        owner_id = int(get_shop(shop_id)["owner_id"])
        try:
            await ctx.bot.send_photo(
                chat_id=owner_id,
                photo=file_id,
                caption=_deposit_caption(get_deposit(shop_id, dep_id)),
                reply_markup=_deposit_inline_kb(dep_id)
            )
        except Exception:
            pass
        return

    # --------- Seller subscription NEW (must deposit to platform wallet in MAIN shop) ----------
    if flow == "seller_sub_new_wait_photo":
        # Only allowed from main shop
        if shop_id != get_main_shop_id():
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Please do this from Main Shop.", reply_markup=kb_home(shop_id, uid))

        sid = int(ctx.user_data.get("seller_target_shop", 0))
        if sid <= 0:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Missing target seller shop.", reply_markup=kb_home(shop_id, uid))

        file_id = update.message.photo[-1].file_id
        caption = (update.message.caption or "").strip()

        dep_id = create_deposit(get_main_shop_id(), uid, SELLER_PRICE_CENTS, file_id, caption, kind="SUB_NEW", target_shop_id=sid)
        ctx.user_data["flow"] = None

        await send_clean(update, ctx, f"✅ Subscription payment submitted (ID #{dep_id}). Admin will approve soon.", reply_markup=kb_home(shop_id, uid))

        # Send to SUPER ADMIN (you) with inline approve/reject
        try:
            await ctx.bot.send_photo(
                chat_id=SUPER_ADMIN_ID,
                photo=file_id,
                caption=_deposit_caption(get_deposit(get_main_shop_id(), dep_id)),
                reply_markup=_deposit_inline_kb(dep_id)
            )
        except Exception:
            pass
        return

    # --------- Seller subscription RENEW (also deposit to main shop) ----------
    if flow == "seller_sub_renew_wait_photo":
        if shop_id != get_main_shop_id():
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Please do this from Main Shop.", reply_markup=kb_home(shop_id, uid))

        sid = int(ctx.user_data.get("seller_target_shop", 0))
        if sid <= 0:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Missing target seller shop.", reply_markup=kb_home(shop_id, uid))

        file_id = update.message.photo[-1].file_id
        caption = (update.message.caption or "").strip()

        dep_id = create_deposit(get_main_shop_id(), uid, SELLER_PRICE_CENTS, file_id, caption, kind="SUB_RENEW", target_shop_id=sid)
        ctx.user_data["flow"] = None

        await send_clean(update, ctx, f"✅ Renewal payment submitted (ID #{dep_id}). Admin will approve soon.", reply_markup=kb_home(shop_id, uid))

        try:
            await ctx.bot.send_photo(
                chat_id=SUPER_ADMIN_ID,
                photo=file_id,
                caption=_deposit_caption(get_deposit(get_main_shop_id(), dep_id)),
                reply_markup=_deposit_inline_kb(dep_id)
            )
        except Exception:
            pass
        return

    # Not expected
    return


# ===================== BOOT =====================
async def post_init(app):
    init_db()

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    if SUPER_ADMIN_ID == 0:
        raise RuntimeError("ADMIN_ID missing")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_text))

    app.run_polling()

if __name__ == "__main__":
    main()
    
