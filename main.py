import os
import sqlite3
import datetime
import hashlib
from typing import Optional, List, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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

# Wallet address shown for RekkoShop (Shop #1) by default
PLATFORM_USDT_TRC20_ADDRESS = os.getenv("USDT_TRC20_ADDRESS", "").strip()

# "Get Own Panel" subscription
PANEL_PRICE_CENTS = 1000  # $10.00
PANEL_DAYS = 30

PAGE_SIZE = 8  # list paging

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
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return col in cols

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
            panel_until TEXT,
            is_suspended INTEGER NOT NULL DEFAULT 0,
            suspended_reason TEXT,
            created_at TEXT NOT NULL,
            wallet_address TEXT
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
            updated_at TEXT NOT NULL,
            owner_banned INTEGER NOT NULL DEFAULT 0,
            owner_restrict_until TEXT,
            owner_block_reason TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS shop_users(
            shop_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            reseller_logged_in INTEGER NOT NULL DEFAULT 0,
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
        CREATE TABLE IF NOT EXISTS resellers(
            shop_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            tg_username TEXT,
            login_username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            PRIMARY KEY(shop_id, user_id)
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
            INSERT INTO shops(owner_id, shop_name, welcome_text, panel_until, is_suspended, suspended_reason, created_at, wallet_address)
            VALUES(?,?,?,?,0,NULL,?,?)
            """, (SUPER_ADMIN_ID, DEFAULT_MAIN_SHOP_NAME, DEFAULT_MAIN_WELCOME, None, now_iso(),
                  PLATFORM_USDT_TRC20_ADDRESS or None))

        # Ensure RekkoShop wallet set if env provided
        if PLATFORM_USDT_TRC20_ADDRESS:
            conn.execute("UPDATE shops SET wallet_address=COALESCE(wallet_address, ?) WHERE id=1",
                         (PLATFORM_USDT_TRC20_ADDRESS,))

        if not conn.execute("SELECT 1 FROM settings WHERE key='panel_offer'").fetchone():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                ("panel_offer",
                 "⭐ Get Own Panel ($10/month)\n\n"
                 "• Your own store\n"
                 "• Your own wallet address\n"
                 "• Your own owner panel\n"
                 "• Your own categories / products / keys / resellers\n\n"
                 "Renews monthly automatically from YOUR SHOP wallet.\n"
                 "If not enough, Owner Panel will be revoked.")
            )


# ===================== USERS =====================
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
            INSERT INTO users(user_id,username,first_name,last_name,last_bot_msg_id,created_at,updated_at,owner_banned,owner_restrict_until,owner_block_reason)
            VALUES(?,?,?,?,NULL,?,?,0,NULL,NULL)
            """, (uid, uname, u.first_name, u.last_name, now_iso(), now_iso()))

def get_last_bot_msg_id(uid: int) -> Optional[int]:
    with db() as conn:
        r = conn.execute("SELECT last_bot_msg_id FROM users WHERE user_id=?", (uid,)).fetchone()
        return int(r["last_bot_msg_id"]) if r and r["last_bot_msg_id"] else None

def set_last_bot_msg_id(uid: int, msg_id: Optional[int]):
    with db() as conn:
        conn.execute("UPDATE users SET last_bot_msg_id=? WHERE user_id=?", (msg_id, uid))

def owner_is_banned(uid: int) -> bool:
    with db() as conn:
        r = conn.execute("SELECT owner_banned FROM users WHERE user_id=?", (uid,)).fetchone()
        return bool(r and int(r["owner_banned"]) == 1)

def owner_restrict_until(uid: int) -> Optional[str]:
    with db() as conn:
        r = conn.execute("SELECT owner_restrict_until FROM users WHERE user_id=?", (uid,)).fetchone()
        return r["owner_restrict_until"] if r else None

def owner_is_restricted(uid: int) -> bool:
    until = owner_restrict_until(uid)
    if not until:
        return False
    try:
        return parse_iso(until) > now_utc()
    except Exception:
        return False

def can_be_owner(uid: int) -> Tuple[bool, str]:
    if owner_is_banned(uid):
        return False, "You are permanently banned from owning a panel."
    if owner_is_restricted(uid):
        until = owner_restrict_until(uid)
        return False, f"You are temporarily restricted from owning a panel until {until}."
    return True, ""

def set_owner_ban(uid: int, banned: bool, reason: Optional[str]):
    with db() as conn:
        conn.execute("""
        UPDATE users
        SET owner_banned=?, owner_block_reason=?
        WHERE user_id=?
        """, (1 if banned else 0, (reason.strip() if reason else None), uid))

def set_owner_restrict(uid: int, until_iso: Optional[str], reason: Optional[str]):
    with db() as conn:
        conn.execute("""
        UPDATE users
        SET owner_restrict_until=?, owner_block_reason=?
        WHERE user_id=?
        """, ((until_iso.strip() if until_iso else None), (reason.strip() if reason else None), uid))


# ===================== SHOP USERS / BALANCE =====================
def ensure_shop_user(shop_id: int, uid: int):
    with db() as conn:
        r = conn.execute("SELECT 1 FROM shop_users WHERE shop_id=? AND user_id=?", (shop_id, uid)).fetchone()
        if not r:
            conn.execute("INSERT INTO shop_users(shop_id,user_id,balance_cents,reseller_logged_in) VALUES(?,?,0,0)", (shop_id, uid))

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

def set_reseller_logged(shop_id: int, uid: int, flag: bool):
    ensure_shop_user(shop_id, uid)
    with db() as conn:
        conn.execute("UPDATE shop_users SET reseller_logged_in=? WHERE shop_id=? AND user_id=?",
                     (1 if flag else 0, shop_id, uid))

def reseller_logged_in(shop_id: int, uid: int) -> bool:
    r = get_shop_user(shop_id, uid)
    return bool(r and int(r["reseller_logged_in"]) == 1)


# ===================== SHOPS =====================
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

def set_shop_panel_until(shop_id: int, until_iso: Optional[str]):
    with db() as conn:
        conn.execute("UPDATE shops SET panel_until=? WHERE id=?", (until_iso, shop_id))

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
    return (bool(s["is_suspended"]), s["suspended_reason"])

def get_shop_wallet(shop_id: int) -> Optional[str]:
    with db() as conn:
        r = conn.execute("SELECT wallet_address FROM shops WHERE id=?", (shop_id,)).fetchone()
        if not r:
            return None
        v = r["wallet_address"]
        return v.strip() if v else None

def set_shop_wallet(shop_id: int, address: Optional[str]):
    addr = address.strip() if address else None
    with db() as conn:
        conn.execute("UPDATE shops SET wallet_address=? WHERE id=?", (addr, shop_id))

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
        INSERT INTO shops(owner_id, shop_name, welcome_text, panel_until, is_suspended, suspended_reason, created_at, wallet_address)
        VALUES(?,?,?,?,0,NULL,?,NULL)
        """, (owner_id, f"{owner_id}'s Shop", "Welcome! Customize your store in the Owner Panel.", None, now_iso()))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

def is_panel_active(shop_id: int) -> bool:
    s = get_shop(shop_id)
    if not s or not s["panel_until"]:
        return False
    try:
        return parse_iso(s["panel_until"]) > now_utc()
    except Exception:
        return False

def renew_panel_if_needed(shop_id: int):
    s = get_shop(shop_id)
    if not s or not s["panel_until"]:
        return
    try:
        until = parse_iso(s["panel_until"])
    except Exception:
        set_shop_panel_until(shop_id, None)
        return
    if until > now_utc():
        return

    owner_id = int(s["owner_id"])
    ensure_shop_user(shop_id, owner_id)
    if can_deduct(shop_id, owner_id, PANEL_PRICE_CENTS):
        deduct(shop_id, owner_id, PANEL_PRICE_CENTS)
        new_until = (now_utc() + datetime.timedelta(days=PANEL_DAYS)).isoformat(timespec="seconds")
        set_shop_panel_until(shop_id, new_until)
    else:
        set_shop_panel_until(shop_id, None)

def delete_shop_hard(shop_id: int):
    with db() as conn:
        conn.execute("DELETE FROM categories WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM subcategories WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM products WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM keys WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM purchases WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM deposits WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM resellers WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM shop_users WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM support_msgs WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM shops WHERE id=?", (shop_id,))

def list_shops(limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
        SELECT id, owner_id, shop_name, panel_until, is_suspended
        FROM shops
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()


# ===================== SETTINGS =====================
def panel_offer_text() -> str:
    with db() as conn:
        return conn.execute("SELECT value FROM settings WHERE key='panel_offer'").fetchone()["value"]

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
                     (0 if r["is_active"] else 1, shop_id, cat_id))

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
                     (0 if r["is_active"] else 1, shop_id, sub_id))

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
                     (0 if r["is_active"] else 1, shop_id, pid))

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
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

def list_pending_deposits(shop_id: int, limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
        SELECT * FROM deposits
        WHERE shop_id=? AND status='PENDING'
        ORDER BY id DESC LIMIT ? OFFSET ?
        """, (shop_id, limit, offset)).fetchall()

def get_deposit(shop_id: int, dep_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM deposits WHERE shop_id=? AND id=?", (shop_id, dep_id)).fetchone()

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


# ===================== RESELLERS =====================
def reseller_by_login(shop_id: int, login: str):
    with db() as conn:
        return conn.execute("""
        SELECT * FROM resellers
        WHERE shop_id=? AND login_username=?
        """, (shop_id, login.lower().strip())).fetchone()

def reseller_by_uid(shop_id: int, uid: int):
    with db() as conn:
        return conn.execute("SELECT * FROM resellers WHERE shop_id=? AND user_id=?", (shop_id, uid)).fetchone()

def add_reseller_by_tg_username(shop_id: int, tg_username: str, login: str, password: str) -> Tuple[bool, str]:
    tg = tg_username.strip().lstrip("@").lower()
    login = login.strip().lower()
    with db() as conn:
        u = conn.execute("SELECT user_id FROM users WHERE username=?", (tg,)).fetchone()
        if not u:
            return False, "User must press /start once first."
        uid = int(u["user_id"])
        if conn.execute("SELECT 1 FROM resellers WHERE shop_id=? AND user_id=?", (shop_id, uid)).fetchone():
            return False, "Already a reseller."
        conn.execute("""
        INSERT INTO resellers(shop_id,user_id,tg_username,login_username,password_hash,is_active,created_at)
        VALUES(?,?,?,?,?,1,?)
        """, (shop_id, uid, tg, login, sha256(password), now_iso()))
    return True, "Reseller added."

def toggle_reseller(shop_id: int, uid: int):
    with db() as conn:
        r = conn.execute("SELECT is_active FROM resellers WHERE shop_id=? AND user_id=?", (shop_id, uid)).fetchone()
        if not r:
            return
        conn.execute("UPDATE resellers SET is_active=? WHERE shop_id=? AND user_id=?",
                     (0 if r["is_active"] else 1, shop_id, uid))

def list_resellers(shop_id: int, limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
        SELECT * FROM resellers
        WHERE shop_id=?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """, (shop_id, limit, offset)).fetchall()

def set_reseller_password(shop_id: int, uid: int, pw: str):
    with db() as conn:
        conn.execute("UPDATE resellers SET password_hash=? WHERE shop_id=? AND user_id=?",
                     (sha256(pw), shop_id, uid))


# ===================== SUPPORT =====================
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
        SELECT su.user_id, su.balance_cents, su.reseller_logged_in, u.username, u.first_name, u.last_name
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

def shop_home_text(shop_id: int) -> str:
    suspended, reason = shop_is_suspended(shop_id)
    if suspended:
        return "⛔ This shop is suspended.\n\n" + (f"Reason: {reason}" if reason else "") + f"\n\n{DEFAULT_BRAND}"

    s = get_shop(shop_id)
    if not s:
        return DEFAULT_MAIN_WELCOME + "\n\n" + DEFAULT_BRAND

    renew_panel_if_needed(shop_id)
    s = get_shop(shop_id)

    if s["panel_until"] and is_panel_active(shop_id):
        left = days_left(s["panel_until"])
        return f"{s['welcome_text']}\n\n🗓 Subscription: {left} day(s) left\n\n— {s['shop_name']}"

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


# ===================== UI HELPERS =====================
def kb_back_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]])

def kb_home(shop_id: int, uid: int) -> InlineKeyboardMarkup:
    ensure_shop_user(shop_id, uid)
    res_on = reseller_logged_in(shop_id, uid)

def kb_open_files(link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Get Files", url=link)],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

    grid = [
        [InlineKeyboardButton("🛍️ Products", callback_data="home:products"),
         InlineKeyboardButton("💰 Wallet", callback_data="home:wallet")],
        [InlineKeyboardButton("📜 History", callback_data="home:history"),
         InlineKeyboardButton("📩 Support", callback_data="home:support")],
        [InlineKeyboardButton("🔐 Reseller Login", callback_data="res:login"),
         InlineKeyboardButton("⭐ Get Own Panel", callback_data="panel:info")],
    ]

    if res_on:
        grid.insert(0, [InlineKeyboardButton("🧑‍💻 Reseller: ON (Logout)", callback_data="res:logout")])

    # Owner panel
    if is_shop_owner(shop_id, uid):
        if shop_id == get_main_shop_id() and is_super_admin(uid):
            grid.append([InlineKeyboardButton("🛠️ Owner Panel", callback_data="own:menu")])
        else:
            renew_panel_if_needed(shop_id)
            if is_panel_active(shop_id):
                grid.append([InlineKeyboardButton("🛠️ Owner Panel", callback_data="own:menu")])
            else:
                grid.append([InlineKeyboardButton("🔒 Owner Panel (Get Own Panel Required)", callback_data="panel:info")])

    # Switch shop
    if shop_id != get_main_shop_id():
        grid.append([InlineKeyboardButton("⬅️ Back to RekkoShop", callback_data="shop:switch:main")])
    else:
        sid = get_shop_by_owner(uid)
        if sid and sid != get_main_shop_id():
            grid.append([InlineKeyboardButton("🏪 My Shop", callback_data=f"shop:switch:{sid}")])

    if shop_id == get_main_shop_id() and is_super_admin(uid):
        grid.append([InlineKeyboardButton("🧾 Platform", callback_data="sa:menu")])

    return InlineKeyboardMarkup(grid)

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

def kb_owner_menu(shop_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="own:users:0"),
         InlineKeyboardButton("💳 Deposits", callback_data="own:deps:0")],
        [InlineKeyboardButton("📂 Categories", callback_data="own:cats"),
         InlineKeyboardButton("🧩 Co-Categories", callback_data="own:subs")],
        [InlineKeyboardButton("📦 Products", callback_data="own:products"),
         InlineKeyboardButton("🔑 Keys", callback_data="own:keys")],
        [InlineKeyboardButton("🧑‍💼 Resellers", callback_data="own:resellers:0"),
         InlineKeyboardButton("💳 Wallet Address", callback_data="own:walletaddr")],
        [InlineKeyboardButton("✏️ Edit Store", callback_data="own:editstore"),
         InlineKeyboardButton("⚠️ Danger Zone", callback_data="own:danger")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_sa_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏪 Shops", callback_data="sa:shops:0"),
         InlineKeyboardButton("✏️ Panel Offer Text", callback_data="sa:offer")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])


# ===================== START =====================
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

    await send_clean(update, ctx, shop_home_text(shop_id), reply_markup=kb_home(shop_id, uid))


# ===================== CALLBACKS =====================
async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    upsert_user(q.from_user)

    uid = q.from_user.id
    shop_id = get_active_shop_id(ctx)
    ensure_shop_user(shop_id, uid)
    data = q.data or ""

    # Suspension guard (allow switching/home)
    suspended, reason = shop_is_suspended(shop_id)
    if suspended and not data.startswith("shop:switch:") and data != "home:menu":
        return await q.edit_message_text(
            "⛔ This shop is suspended.\n\n" + (f"Reason: {reason}" if reason else "") + f"\n\n{DEFAULT_BRAND}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to RekkoShop", callback_data="shop:switch:main")]])
        )

    # Switch shop
if data.startswith("getfiles:"):
    pid = int(data.split(":")[1])
    p = get_product(shop_id, pid)
    if not p:
        return await q.answer("Not found", show_alert=True)

    link = (p["telegram_link"] or "").strip()
    if not link:
        return await q.answer("No link set.", show_alert=True)

    # delete old bot message
    try:
        await ctx.bot.delete_message(chat_id=q.message.chat_id, message_id=q.message.message_id)
    except Exception:
        pass

    # send button (no link text)
    await ctx.bot.send_message(
        chat_id=q.message.chat_id,
        text="📥 Tap below to open the files:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Get Files", url=link)],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
    )
    return

    # Home menu
if data == "home:menu":
    ctx.user_data["flow"] = None

    # HARD RESET – cancel EVERYTHING
    for k in [
        "dep_amount",
        "selected_user", "selected_user_page",
        "res_login_user", "res_tg", "res_login",
        "pid", "cat_id", "sub_id",
        "target_deposit",
        "res_uid",
        "sa_sel_shop", "sa_sel_user", "sa_sel_page",
        "sid",
    ]:
        ctx.user_data.pop(k, None)

    return await q.edit_message_text(
        shop_home_text(shop_id),
        reply_markup=kb_home(shop_id, uid)
    )

    # Wallet screen
    if data == "home:wallet":
        bal = get_balance(shop_id, uid)
        addr = get_shop_wallet(shop_id)
        addr_txt = addr if addr else "⚠️ Wallet address not set yet (owner must set it)"
        txt = f"💰 Wallet\n\nBalance: {money(bal)}\n\nUSDT (TRC-20) Address:\n{addr_txt}"
        return await q.edit_message_text(txt, reply_markup=kb_wallet())

    # Deposit
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
            parse_mode="Markdown"
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

        is_res = reseller_logged_in(shop_id, uid)
        price = int(p["reseller_price_cents"]) if is_res else int(p["user_price_cents"])
        stock = int(p["stock"]) if p["stock"] is not None else 0
        bal = get_balance(shop_id, uid)

        txt = (
            f"📦 {p['name']}\n\n"
            f"Price: {money(price)} {'(Reseller)' if is_res else ''}\n"
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

        is_res = reseller_logged_in(shop_id, uid)
        price = int(p["reseller_price_cents"]) if is_res else int(p["user_price_cents"])

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
                [InlineKeyboardButton("📥 Get Files", callback_data=f"getfiles:{pid}") ,
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ])
            return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")
        return await q.edit_message_text(txt + "\n\n⚠️ No file link set yet.", reply_markup=kb_back_home(), parse_mode="Markdown")

    if data.startswith("getfiles:"):
        pid = int(data.split(":")[1])
        p = get_product(shop_id, pid)
        if not p:
            return await q.answer("Not found", show_alert=True)
        link = (p["telegram_link"] or "").strip()
        if not link:
            return await q.answer("No link set.", show_alert=True)
        # delete the old message after click (your requirement)
        try:
            await ctx.bot.delete_message(chat_id=q.message.chat_id, message_id=q.message.message_id)
        except Exception:
            pass
        await ctx.bot.send_message(chat_id=q.message.chat_id, text=f"📥 Telegram Link:\n{link}")
        return

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
        return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")

    # Support
    if data == "home:support":
        ctx.user_data["flow"] = "support_send"
        return await q.edit_message_text("📩 Support\n\nType your message to the shop owner:", reply_markup=kb_back_home())

    # Reseller login/logout
    if data == "res:logout":
        set_reseller_logged(shop_id, uid, False)
        return await q.edit_message_text("✅ Reseller logged out.", reply_markup=kb_home(shop_id, uid))

    if data == "res:login":
        ctx.user_data["flow"] = "res_login_user"
        return await q.edit_message_text("🔐 Reseller Login\n\nSend your reseller username:", reply_markup=kb_back_home())

    # Get Own Panel
    if data == "panel:info":
        offer = panel_offer_text()
        main_id = get_main_shop_id()
        ensure_shop_user(main_id, uid)
        bal = get_balance(main_id, uid)
        ok, msg = can_be_owner(uid)
        warn = "" if ok else f"\n\n⚠️ {msg}"
        txt = offer + f"\n\nPrice: {money(PANEL_PRICE_CENTS)} / month\nYour RekkoShop balance: {money(bal)}{warn}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Buy Get Own Panel", callback_data="panel:buy"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data == "panel:buy":
        ok, msg = can_be_owner(uid)
        if not ok:
            return await q.answer(msg, show_alert=True)

        main_id = get_main_shop_id()
        ensure_shop_user(main_id, uid)
        if not can_deduct(main_id, uid, PANEL_PRICE_CENTS):
            return await q.answer("Not enough RekkoShop balance. Top up first.", show_alert=True)

        deduct(main_id, uid, PANEL_PRICE_CENTS)

        sid = create_shop_for_owner(uid)
        new_until = (now_utc() + datetime.timedelta(days=PANEL_DAYS)).isoformat(timespec="seconds")
        set_shop_panel_until(sid, new_until)
        ensure_shop_user(sid, uid)

        set_active_shop_id(ctx, sid)

        bot_username = (await ctx.bot.get_me()).username
        deeplink = f"https://t.me/{bot_username}?start=shop_{sid}"

        txt = (
            "✅ Get Own Panel activated!\n\n"
            f"Your Shop ID: {sid}\n"
            f"Share your shop link:\n{deeplink}\n\n"
            "Next: Owner Panel → 💳 Wallet Address (set your wallet)"
        )
        return await q.edit_message_text(txt, reply_markup=kb_home(sid, uid))

    # Owner Panel
    if data == "own:menu":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        if shop_id != get_main_shop_id():
            ok, msg = can_be_owner(uid)
            if not ok:
                return await q.answer(msg, show_alert=True)
            renew_panel_if_needed(shop_id)
            if not is_panel_active(shop_id):
                return await q.answer("Get Own Panel expired. Top up YOUR SHOP wallet to auto-renew.", show_alert=True)
        ctx.user_data["flow"] = None
        return await q.edit_message_text("🛠️ Owner Panel", reply_markup=kb_owner_menu(shop_id))

    # Owner: Edit store
    if data == "own:editstore":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = "own_edit_store"
        return await q.edit_message_text(
            "✏️ Edit Store\n\nSend this format:\n\nName | Welcome text\n\nExample:\nMyShop | Welcome to my shop!",
            reply_markup=kb_owner_menu(shop_id)
        )

    # Owner: Wallet address
    if data == "own:walletaddr":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        if shop_id != get_main_shop_id():
            renew_panel_if_needed(shop_id)
            if not is_panel_active(shop_id):
                return await q.answer("Owner panel expired.", show_alert=True)

        addr = get_shop_wallet(shop_id)
        ctx.user_data["flow"] = "own_wallet_edit"
        return await q.edit_message_text(
            "💳 Wallet Address\n\n"
            f"Current:\n{addr or 'Not set'}\n\n"
            "Send new wallet address (or send - to clear):",
            reply_markup=kb_owner_menu(shop_id)
        )

    # Owner: Categories
    if data == "own:cats":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        cats = list_categories(shop_id, active_only=False)
        btns = [InlineKeyboardButton(("✅ " if c["is_active"] else "🚫 ") + c["name"], callback_data=f"own:cat_toggle:{c['id']}") for c in cats]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("➕ Add Category", callback_data="own:cat_add"),
                   InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("📂 Categories (tap to enable/disable):", reply_markup=InlineKeyboardMarkup(kb))

    if data == "own:cat_add":
        ctx.user_data["flow"] = "own_cat_add"
        return await q.edit_message_text("➕ Send category name:", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:cat_toggle:"):
        cat_id = int(data.split(":")[-1])
        toggle_category(shop_id, cat_id)
        return await q.edit_message_text("✅ Updated.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data="own:cats"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # Owner: Co-Categories
    if data == "own:subs":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        cats = list_categories(shop_id, active_only=False)
        if not cats:
            return await q.edit_message_text("No categories yet. Add category first.", reply_markup=kb_owner_menu(shop_id))
        btns = [InlineKeyboardButton(c["name"], callback_data=f"own:subs_in:{c['id']}") for c in cats]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("🧩 Choose category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:subs_in:"):
        cat_id = int(data.split(":")[-1])
        subs = list_subcategories(shop_id, cat_id, active_only=False)
        btns = [InlineKeyboardButton(("✅ " if s["is_active"] else "🚫 ") + s["name"], callback_data=f"own:sub_toggle:{s['id']}:{cat_id}") for s in subs]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("➕ Add Co-Category", callback_data=f"own:sub_add:{cat_id}"),
                   InlineKeyboardButton("⬅️ Back", callback_data="own:subs")])
        return await q.edit_message_text("🧩 Co-categories (tap to enable/disable):", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:sub_add:"):
        cat_id = int(data.split(":")[-1])
        ctx.user_data["flow"] = "own_sub_add"
        ctx.user_data["cat_id"] = cat_id
        return await q.edit_message_text("➕ Send co-category name:", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:sub_toggle:"):
        parts = data.split(":")
        sub_id = int(parts[2])
        cat_id = int(parts[3])
        toggle_subcategory(shop_id, sub_id)
        return await q.edit_message_text("✅ Updated.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:subs_in:{cat_id}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # Owner: Products
    if data == "own:products":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        cats = list_categories(shop_id, active_only=False)
        if not cats:
            return await q.edit_message_text("Add a category first.", reply_markup=kb_owner_menu(shop_id))
        btns = [InlineKeyboardButton(c["name"], callback_data=f"own:prod_cat:{c['id']}") for c in cats]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("📦 Choose category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:prod_cat:"):
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
        parts = data.split(":")
        sub_id = int(parts[2])
        cat_id = int(parts[3])
        ctx.user_data["flow"] = "own_prod_add"
        ctx.user_data["sub_id"] = sub_id
        ctx.user_data["cat_id"] = cat_id
        return await q.edit_message_text(
            "➕ Add Product\n\nSend format:\nName | user_price | reseller_price\n\nExample:\nPUBG Key | 10 | 8",
            reply_markup=kb_owner_menu(shop_id)
        )

    if data.startswith("own:prod_view:"):
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
            f"Reseller price: {money(int(p['reseller_price_cents']))}\n"
            f"Stock: {int(p['stock'])}\n"
            f"Active: {'YES' if int(p['is_active'])==1 else 'NO'}\n"
            f"Link: {(p['telegram_link'] or '-').strip()}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Toggle Active", callback_data=f"own:prod_toggle:{pid}:{sub_id}:{cat_id}"),
             InlineKeyboardButton("🔗 Edit Link", callback_data=f"own:prod_link:{pid}")],
            [InlineKeyboardButton("💲 Edit Prices", callback_data=f"own:prod_price:{pid}"),
             InlineKeyboardButton("🔑 Add Keys", callback_data=f"own:keys_for:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:prod_sub:{sub_id}:{cat_id}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("own:prod_toggle:"):
        parts = data.split(":")
        pid = int(parts[2])
        sub_id = int(parts[3])
        cat_id = int(parts[4])
        toggle_product(shop_id, pid)
        return await q.edit_message_text("✅ Updated.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:prod_view:{pid}:{sub_id}:{cat_id}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    if data.startswith("own:prod_link:"):
        pid = int(data.split(":")[-1])
        ctx.user_data["flow"] = "own_prod_link"
        ctx.user_data["pid"] = pid
        return await q.edit_message_text("🔗 Send Telegram link (or send - to clear):", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:prod_price:"):
        pid = int(data.split(":")[-1])
        ctx.user_data["flow"] = "own_prod_price"
        ctx.user_data["pid"] = pid
        return await q.edit_message_text("💲 Send format: user_price | reseller_price\nExample: 10 | 8", reply_markup=kb_owner_menu(shop_id))

    # Owner: Keys (button first)
    if data == "own:keys":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = None
        return await q.edit_message_text("🔑 Keys\n\nOpen a product → tap “🔑 Add Keys”.", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:keys_for:"):
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        pid = int(data.split(":")[-1])
        p = get_product(shop_id, pid)
        if not p:
            return await q.answer("Product not found", show_alert=True)
        ctx.user_data["flow"] = "own_keys_add"
        ctx.user_data["pid"] = pid
        return await q.edit_message_text(
            f"🔑 Add Keys for: {p['name']} (ID {pid})\n\nSend keys (one per line):",
            reply_markup=kb_owner_menu(shop_id)
        )

    # Owner: Deposits
    if data.startswith("own:deps:"):
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        deps = list_pending_deposits(shop_id, PAGE_SIZE, page * PAGE_SIZE)
        if not deps:
            return await q.edit_message_text("💳 No pending deposits.", reply_markup=kb_owner_menu(shop_id))
        btns = [InlineKeyboardButton(f"#{d['id']} • {d['user_id']} • {money(int(d['amount_cents']))}",
                                     callback_data=f"own:dep:{d['id']}:{page}") for d in deps]
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
        parts = data.split(":")
        dep_id = int(parts[2])
        page = int(parts[3])
        d = get_deposit(shop_id, dep_id)
        if not d:
            return await q.answer("Not found", show_alert=True)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"own:depok:{dep_id}:{page}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"own:depnok:{dep_id}:{page}")],
            [InlineKeyboardButton("✏️ Edit Amount", callback_data=f"own:depedit_amt:{dep_id}:{page}"),
             InlineKeyboardButton("📝 Edit Note", callback_data=f"own:depedit_note:{dep_id}:{page}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:deps:{page}")]
        ])

        caption = (
            f"🏪 Shop #{shop_id}\n💳 Deposit #{dep_id}\n"
            f"User: {d['user_id']}\nAmount: {money(int(d['amount_cents']))}\n"
            f"Note: {d['caption'] or '-'}\nStatus: {d['status']}"
        )
        await send_clean_text(q.message.chat_id, ctx, uid, caption, reply_markup=kb)
        await ctx.bot.send_photo(chat_id=q.message.chat_id, photo=d["photo_file_id"])
        return

    if data.startswith("own:depedit_amt:"):
        parts = data.split(":")
        dep_id = int(parts[2])
        page = int(parts[3])
        d = get_deposit(shop_id, dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Only PENDING deposits can be edited.", show_alert=True)
        ctx.user_data["flow"] = "own_dep_edit_amount"
        ctx.user_data["target_deposit"] = dep_id
        return await q.edit_message_text(
            f"✏️ Edit Deposit Amount\n\nCurrent: {money(int(d['amount_cents']))}\n\nSend new amount:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"own:dep:{dep_id}:{page}")]])
        )

    if data.startswith("own:depedit_note:"):
        parts = data.split(":")
        dep_id = int(parts[2])
        page = int(parts[3])
        d = get_deposit(shop_id, dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Only PENDING deposits can be edited.", show_alert=True)
        ctx.user_data["flow"] = "own_dep_edit_note"
        ctx.user_data["target_deposit"] = dep_id
        return await q.edit_message_text(
            f"📝 Edit Deposit Note\n\nCurrent:\n{d['caption'] or '-'}\n\nSend new note:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"own:dep:{dep_id}:{page}")]])
        )

    if data.startswith("own:depok:"):
        parts = data.split(":")
        dep_id = int(parts[2])
        page = int(parts[3])
        d = get_deposit(shop_id, dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Already processed.", show_alert=True)
        set_deposit_status(shop_id, dep_id, "APPROVED", uid)
        add_balance_delta(shop_id, int(d["user_id"]), int(d["amount_cents"]))
        return await q.edit_message_text("✅ Deposit approved and balance added.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:deps:{page}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    if data.startswith("own:depnok:"):
        parts = data.split(":")
        dep_id = int(parts[2])
        page = int(parts[3])
        d = get_deposit(shop_id, dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Already processed.", show_alert=True)
        set_deposit_status(shop_id, dep_id, "REJECTED", uid)
        return await q.edit_message_text("❌ Deposit rejected.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:deps:{page}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # Owner: Users list + reply + balance edit
    if data.startswith("own:users:"):
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
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
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
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
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        target_uid = int(ctx.user_data.get("selected_user", 0))
        if not target_uid:
            return await q.answer("Select a user first.", show_alert=True)
        ctx.user_data["flow"] = "own_reply_user"
        return await q.edit_message_text(f"💬 Reply to {target_uid}\n\nType your message:", reply_markup=kb_owner_menu(shop_id))

    if data == "own:balmenu":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
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
        return await q.edit_message_text(f"💰 Edit Balance for {target_uid}\n\nCurrent: {money(bal)}\n\nChoose:", reply_markup=kb)

    if data in ("own:bal_add", "own:bal_sub", "own:bal_set"):
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        target_uid = int(ctx.user_data.get("selected_user", 0))
        if not target_uid:
            return await q.answer("Select a user first.", show_alert=True)
        ctx.user_data["flow"] = data  # reuse as flow name
        hint = "Send amount to ADD:" if data == "own:bal_add" else ("Send amount to SUBTRACT:" if data == "own:bal_sub" else "Send new exact balance:")
        return await q.edit_message_text(f"{hint}\n(example 10 or 10.5)", reply_markup=kb_owner_menu(shop_id))

    # Owner: Resellers
    if data.startswith("own:resellers:"):
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        rs = list_resellers(shop_id, PAGE_SIZE, page * PAGE_SIZE)
        btns = []
        for r in rs:
            tag = "✅" if int(r["is_active"]) == 1 else "🚫"
            btns.append(InlineKeyboardButton(f"{tag} {r['login_username']} (uid {r['user_id']})",
                                             callback_data=f"own:res_view:{r['user_id']}:{page}"))
        kb = rows(btns, 1)
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"own:resellers:{page-1}"))
        if len(rs) == PAGE_SIZE:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"own:resellers:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("➕ Add Reseller", callback_data="own:res_add"),
                   InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("🧑‍💼 Resellers:", reply_markup=InlineKeyboardMarkup(kb))

    if data == "own:res_add":
        ctx.user_data["flow"] = "own_res_add_tg"
        return await q.edit_message_text(
            "➕ Add Reseller\n\nSend reseller Telegram username (example: @username)\n\n(They must press /start once first)",
            reply_markup=kb_owner_menu(shop_id)
        )

    if data.startswith("own:res_view:"):
        parts = data.split(":")
        res_uid = int(parts[2])
        page = int(parts[3])
        r = reseller_by_uid(shop_id, res_uid)
        if not r:
            return await q.answer("Not found", show_alert=True)
        txt = (
            f"🧑‍💼 Reseller\n\n"
            f"User ID: {r['user_id']}\n"
            f"TG: @{r['tg_username'] if r['tg_username'] else '-'}\n"
            f"Login: {r['login_username']}\n"
            f"Active: {'YES' if int(r['is_active'])==1 else 'NO'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Toggle Active", callback_data=f"own:res_toggle:{res_uid}:{page}"),
             InlineKeyboardButton("🔑 Change Password", callback_data=f"own:res_pw:{res_uid}:{page}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:resellers:{page}")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("own:res_toggle:"):
        parts = data.split(":")
        res_uid = int(parts[2])
        page = int(parts[3])
        toggle_reseller(shop_id, res_uid)
        return await q.edit_message_text("✅ Updated.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:resellers:{page}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    if data.startswith("own:res_pw:"):
        parts = data.split(":")
        res_uid = int(parts[2])
        page = int(parts[3])
        ctx.user_data["flow"] = "own_res_pw"
        ctx.user_data["res_uid"] = res_uid
        return await q.edit_message_text("🔑 Send new password:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"own:res_view:{res_uid}:{page}")]
        ]))

    # Danger Zone delete own shop (not main)
    if data == "own:danger":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        if shop_id == get_main_shop_id():
            return await q.answer("Main shop cannot be deleted.", show_alert=True)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ Delete My Shop", callback_data="own:delete_confirm"),
             InlineKeyboardButton("⬅️ Back", callback_data="own:menu")]
        ])
        return await q.edit_message_text("⚠️ Danger Zone\n\nDelete will remove everything in this shop.\n\nProceed?", reply_markup=kb)

    if data == "own:delete_confirm":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        if shop_id == get_main_shop_id():
            return await q.answer("Main shop cannot be deleted.", show_alert=True)
        delete_shop_hard(shop_id)
        set_active_shop_id(ctx, get_main_shop_id())
        return await q.edit_message_text("🗑️ Shop deleted.", reply_markup=kb_home(get_main_shop_id(), uid))

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
            f"Reason: {s['suspended_reason'] or '-'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Users", callback_data=f"sa:users:{sid}:0"),
             InlineKeyboardButton("💰 Edit Balance", callback_data=f"sa:balpick:{sid}:0")],
            [InlineKeyboardButton("⛔ Suspend", callback_data=f"sa:suspend:{sid}:{page}"),
             InlineKeyboardButton("✅ Unsuspend", callback_data=f"sa:unsuspend:{sid}:{page}")],
            [InlineKeyboardButton("🚫 Ban Owner", callback_data=f"sa:ban:{s['owner_id']}:{page}"),
             InlineKeyboardButton("⏳ Restrict 7d", callback_data=f"sa:restrict7:{s['owner_id']}:{page}")],
            [InlineKeyboardButton("🗑️ Delete Shop", callback_data=f"sa:delshop:{sid}:{page}"),
             InlineKeyboardButton("⬅️ Back", callback_data=f"sa:shops:{page}")],
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    # Super Admin: list users of a shop
    if data.startswith("sa:users:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        sid = int(parts[2])
        page = int(parts[3])
        total = count_shop_users(sid)
        rowsu = list_shop_users(sid, PAGE_SIZE, page * PAGE_SIZE)
        if not rowsu:
            return await q.edit_message_text("No users in this shop yet.", reply_markup=kb_sa_menu())

        btns = []
        for r in rowsu:
            uname = ("@" + r["username"]) if r["username"] else ""
            btns.append(InlineKeyboardButton(f"{r['user_id']} {uname} • {money(int(r['balance_cents']))}",
                                             callback_data=f"sa:user:{sid}:{r['user_id']}:{page}"))
        kb = rows(btns, 1)
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"sa:users:{sid}:{page-1}"))
        if (page + 1) * PAGE_SIZE < total:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"sa:users:{sid}:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data=f"sa:shop:{sid}:0")])
        return await q.edit_message_text(f"👥 Shop #{sid} Users (Total {total})", reply_markup=InlineKeyboardMarkup(kb))

    # Super Admin: user detail + balance edit
    if data.startswith("sa:user:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        sid = int(parts[2])
        target_uid = int(parts[3])
        page = int(parts[4])
        ensure_shop_user(sid, target_uid)
        bal = get_balance(sid, target_uid)

        ctx.user_data["sa_sel_shop"] = sid
        ctx.user_data["sa_sel_user"] = target_uid
        ctx.user_data["sa_sel_page"] = page

        txt = f"🧾 Super Admin\n\nShop #{sid}\nUser {target_uid}\nBalance: {money(bal)}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add", callback_data="sa:bal_add"),
             InlineKeyboardButton("➖ Subtract", callback_data="sa:bal_sub")],
            [InlineKeyboardButton("🧾 Set Exact", callback_data="sa:bal_set"),
             InlineKeyboardButton("⬅️ Back", callback_data=f"sa:users:{sid}:{page}")],
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data in ("sa:bal_add", "sa:bal_sub", "sa:bal_set"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = data
        hint = "Send amount to ADD:" if data == "sa:bal_add" else ("Send amount to SUBTRACT:" if data == "sa:bal_sub" else "Send new exact balance:")
        return await q.edit_message_text(f"{hint}\n(example 10 or 10.5)", reply_markup=kb_sa_menu())

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

    if data.startswith("sa:ban:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        owner_id = int(data.split(":")[2])
        set_owner_ban(owner_id, True, "Banned by Super Admin")
        return await q.edit_message_text("🚫 Owner banned (permanent).", reply_markup=kb_sa_menu())

    if data.startswith("sa:restrict7:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        owner_id = int(data.split(":")[2])
        until = (now_utc() + datetime.timedelta(days=7)).isoformat(timespec="seconds")
        set_owner_restrict(owner_id, until, "Restricted 7 days by Super Admin")
        return await q.edit_message_text("⏳ Owner restricted for 7 days.", reply_markup=kb_sa_menu())

    if data.startswith("sa:delshop:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        sid = int(data.split(":")[2])
        if sid == get_main_shop_id():
            return await q.answer("Cannot delete main shop.", show_alert=True)
        delete_shop_hard(sid)
        return await q.edit_message_text("🗑️ Shop deleted.", reply_markup=kb_sa_menu())

    if data == "sa:offer":
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = "sa_offer_edit"
        return await q.edit_message_text("✏️ Send new Get Own Panel description text:", reply_markup=kb_sa_menu())

    return


# ===================== TEXT HANDLER =====================
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    shop_id = get_active_shop_id(ctx)
    ensure_shop_user(shop_id, uid)

    text = (update.message.text or "").strip()
    flow = ctx.user_data.get("flow")

    # Deposit custom amount
    if flow == "dep_custom":
        amt = to_cents(text)
        if amt is None:
            return await send_clean(update, ctx, "Send amount like 10 or 10.5", reply_markup=kb_back_home())
        ctx.user_data["flow"] = "dep_wait_photo"
        ctx.user_data["dep_amount"] = amt
        return await send_clean(update, ctx, f"✅ Amount set: {money(amt)}\nNow send screenshot (photo).", reply_markup=kb_back_home())

    # Support
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

    # Reseller login
    if flow == "res_login_user":
        ctx.user_data["res_login_user"] = text.strip().lower()
        ctx.user_data["flow"] = "res_login_pw"
        return await send_clean(update, ctx, "🔐 Send password:", reply_markup=kb_back_home())

    if flow == "res_login_pw":
        login = (ctx.user_data.get("res_login_user") or "").strip().lower()
        pw = text.strip()
        r = reseller_by_login(shop_id, login)
        if not r or int(r["is_active"]) != 1 or r["password_hash"] != sha256(pw) or int(r["user_id"]) != uid:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "❌ Invalid reseller login.", reply_markup=kb_home(shop_id, uid))
        set_reseller_logged(shop_id, uid, True)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Reseller login success.", reply_markup=kb_home(shop_id, uid))

    # Owner edit store
    if flow == "own_edit_store":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        if "|" not in text:
            return await send_clean(update, ctx, "Format: Name | Welcome text", reply_markup=kb_owner_menu(shop_id))
        name, welcome = [x.strip() for x in text.split("|", 1)]
        if not name or not welcome:
            return await send_clean(update, ctx, "Format: Name | Welcome text", reply_markup=kb_owner_menu(shop_id))
        set_shop_profile(shop_id, name, welcome)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Store updated.", reply_markup=kb_owner_menu(shop_id))

    # Owner wallet edit
    if flow == "own_wallet_edit":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        if shop_id != get_main_shop_id():
            renew_panel_if_needed(shop_id)
            if not is_panel_active(shop_id):
                ctx.user_data["flow"] = None
                return await send_clean(update, ctx, "Owner panel expired.", reply_markup=kb_home(shop_id, uid))

        if text == "-":
            set_shop_wallet(shop_id, None)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "✅ Wallet address cleared.", reply_markup=kb_owner_menu(shop_id))

        if len(text) < 10:
            return await send_clean(update, ctx, "Invalid wallet address.", reply_markup=kb_owner_menu(shop_id))

        set_shop_wallet(shop_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Wallet address updated.", reply_markup=kb_owner_menu(shop_id))

    # Owner add category
    if flow == "own_cat_add":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        add_category(shop_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Category added.", reply_markup=kb_owner_menu(shop_id))

    # Owner add subcategory
    if flow == "own_sub_add":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        cat_id = int(ctx.user_data.get("cat_id", 0))
        if cat_id <= 0:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Category missing.", reply_markup=kb_owner_menu(shop_id))
        add_subcategory(shop_id, cat_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Co-category added.", reply_markup=kb_owner_menu(shop_id))

    # Owner add product
    if flow == "own_prod_add":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        if "|" not in text:
            return await send_clean(update, ctx, "Format: Name | user_price | reseller_price", reply_markup=kb_owner_menu(shop_id))
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 3:
            return await send_clean(update, ctx, "Format: Name | user_price | reseller_price", reply_markup=kb_owner_menu(shop_id))
        name = parts[0]
        up = to_cents(parts[1])
        rp = to_cents(parts[2])
        if not name or up is None or rp is None:
            return await send_clean(update, ctx, "Invalid values.", reply_markup=kb_owner_menu(shop_id))
        sub_id = int(ctx.user_data.get("sub_id", 0))
        cat_id = int(ctx.user_data.get("cat_id", 0))
        add_product(shop_id, cat_id, sub_id, name, up, rp)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Product added.", reply_markup=kb_owner_menu(shop_id))

    # Owner edit product link
    if flow == "own_prod_link":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        pid = int(ctx.user_data.get("pid", 0))
        if text == "-":
            update_product_link(shop_id, pid, None)
        else:
            update_product_link(shop_id, pid, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Link updated.", reply_markup=kb_owner_menu(shop_id))

    # Owner edit product prices
    if flow == "own_prod_price":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        if "|" not in text:
            return await send_clean(update, ctx, "Format: user_price | reseller_price", reply_markup=kb_owner_menu(shop_id))
        a, b = [x.strip() for x in text.split("|", 1)]
        up = to_cents(a)
        rp = to_cents(b)
        if up is None or rp is None:
            return await send_clean(update, ctx, "Invalid prices.", reply_markup=kb_owner_menu(shop_id))
        pid = int(ctx.user_data.get("pid", 0))
        update_product_prices(shop_id, pid, up, rp)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Prices updated.", reply_markup=kb_owner_menu(shop_id))

    # Owner add keys
    if flow == "own_keys_add":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        pid = int(ctx.user_data.get("pid", 0))
        keys = text.splitlines()
        n = add_keys(shop_id, pid, keys)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, f"✅ Added {n} keys.", reply_markup=kb_owner_menu(shop_id))

    # Owner reply user
    if flow == "own_reply_user":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
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

    # Owner balance edit flows
    if flow in ("own:bal_add", "own:bal_sub", "own:bal_set"):
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
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

    # Owner add reseller flow
    if flow == "own_res_add_tg":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        ctx.user_data["res_tg"] = text.strip()
        ctx.user_data["flow"] = "own_res_add_login"
        return await send_clean(update, ctx, "Send reseller login username:", reply_markup=kb_owner_menu(shop_id))

    if flow == "own_res_add_login":
        ctx.user_data["res_login"] = text.strip()
        ctx.user_data["flow"] = "own_res_add_pw"
        return await send_clean(update, ctx, "Send reseller password:", reply_markup=kb_owner_menu(shop_id))

    if flow == "own_res_add_pw":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        tg = ctx.user_data.get("res_tg", "")
        login = ctx.user_data.get("res_login", "")
        pw = text.strip()
        ok, msg = add_reseller_by_tg_username(shop_id, tg, login, pw)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, ("✅ " if ok else "❌ ") + msg, reply_markup=kb_owner_menu(shop_id))

    if flow == "own_res_pw":
        if not is_shop_owner(shop_id, uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(shop_id, uid))
        res_uid = int(ctx.user_data.get("res_uid", 0))
        set_reseller_password(shop_id, res_uid, text.strip())
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Password updated.", reply_markup=kb_owner_menu(shop_id))

    # Owner edit deposit amount/note
    if flow == "own_dep_edit_amount":
        dep_id = int(ctx.user_data.get("target_deposit", 0))
        amt = to_cents(text)
        if amt is None:
            return await send_clean(update, ctx, "Send amount like 10 or 10.5", reply_markup=kb_owner_menu(shop_id))
        d = get_deposit(shop_id, dep_id)
        if not d or d["status"] != "PENDING":
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Deposit not found or not pending.", reply_markup=kb_owner_menu(shop_id))
        update_deposit_amount(shop_id, dep_id, amt)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, f"✅ Deposit #{dep_id} amount updated to {money(amt)}.", reply_markup=kb_owner_menu(shop_id))

    if flow == "own_dep_edit_note":
        dep_id = int(ctx.user_data.get("target_deposit", 0))
        d = get_deposit(shop_id, dep_id)
        if not d or d["status"] != "PENDING":
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Deposit not found or not pending.", reply_markup=kb_owner_menu(shop_id))
        update_deposit_caption(shop_id, dep_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Deposit note updated.", reply_markup=kb_owner_menu(shop_id))

    # Super Admin flows
    if flow == "sa_suspend_reason":
        sid = int(ctx.user_data.get("sid", 0))
        set_shop_suspension(sid, True, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "⛔ Shop suspended.", reply_markup=kb_sa_menu())

    if flow == "sa_offer_edit":
        set_panel_offer_text(text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Updated panel offer text.", reply_markup=kb_sa_menu())

    if flow in ("sa:bal_add", "sa:bal_sub", "sa:bal_set"):
        if not is_super_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_sa_menu())

        sid = int(ctx.user_data.get("sa_sel_shop", 0))
        target_uid = int(ctx.user_data.get("sa_sel_user", 0))
        amt = to_cents(text)
        if amt is None:
            return await send_clean(update, ctx, "Send amount like 10 or 10.5", reply_markup=kb_sa_menu())

        if flow == "sa:bal_add":
            add_balance_delta(sid, target_uid, amt)
        elif flow == "sa:bal_sub":
            add_balance_delta(sid, target_uid, -amt)
        else:
            set_balance_absolute(sid, target_uid, amt)

        ctx.user_data["flow"] = None
        newb = get_balance(sid, target_uid)
        return await send_clean(update, ctx, f"✅ Updated.\nShop #{sid} User {target_uid} = {money(newb)}", reply_markup=kb_sa_menu())

    # Default ignore
    return


# ===================== PHOTO HANDLER (deposit screenshot) =====================
async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    shop_id = get_active_shop_id(ctx)
    ensure_shop_user(shop_id, uid)

    if ctx.user_data.get("flow") != "dep_wait_photo":
        return

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

    dep_id = create_deposit(shop_id, uid, amt, file_id, caption)
    ctx.user_data["flow"] = None

    await send_clean(update, ctx, f"✅ Deposit submitted (ID #{dep_id}). Owner will review.", reply_markup=kb_home(shop_id, uid))

    owner_id = int(get_shop(shop_id)["owner_id"])
    try:
        await ctx.bot.send_photo(
            chat_id=owner_id,
            photo=file_id,
            caption=f"🏪 Shop #{shop_id}\n💳 Deposit #{dep_id}\nUser: {uid}\nAmount: {money(amt)}\nNote: {caption or '-'}"
        )
    except Exception:
        pass


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
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_text))

    app.run_polling()

if __name__ == "__main__":
    main()
