import os
import sqlite3
import datetime
import hashlib
from typing import Optional, List, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ===================== ENV =====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SUPER_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # you
DB_PATH = os.getenv("DB_PATH", "rekkoshop.db")
CURRENCY = os.getenv("CURRENCY", "USD")
USDT_TRC20_ADDRESS = os.getenv("USDT_TRC20_ADDRESS", "")

# Get Own Panel subscription
PANEL_PRICE_CENTS = 1000  # $10
PANEL_DAYS = 30

PAGE_SIZE = 10

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

def rows2(btns: List[InlineKeyboardButton], per_row: int = 2):
    return [btns[i:i+per_row] for i in range(0, len(btns), per_row)]


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

        # --- owner controls (ban permanent / restrict temporary) ---
        if not _col_exists(conn, "users", "owner_banned"):
            conn.execute("ALTER TABLE users ADD COLUMN owner_banned INTEGER NOT NULL DEFAULT 0")
        if not _col_exists(conn, "users", "owner_restrict_until"):
            conn.execute("ALTER TABLE users ADD COLUMN owner_restrict_until TEXT")
        if not _col_exists(conn, "users", "owner_block_reason"):
            conn.execute("ALTER TABLE users ADD COLUMN owner_block_reason TEXT")

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
            status TEXT NOT NULL,     -- PENDING/APPROVED/REJECTED
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

        # Ensure main shop exists as id=1
        r = conn.execute("SELECT id FROM shops ORDER BY id ASC LIMIT 1").fetchone()
        if not r:
            conn.execute("""
            INSERT INTO shops(owner_id, shop_name, welcome_text, panel_until, is_suspended, suspended_reason, created_at)
            VALUES(?,?,?,?,0,NULL,?)
            """, (SUPER_ADMIN_ID, DEFAULT_MAIN_SHOP_NAME, DEFAULT_MAIN_WELCOME, None, now_iso()))

        # Default Get Own Panel offer text (editable)
        if not conn.execute("SELECT 1 FROM settings WHERE key='panel_offer'").fetchone():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                ("panel_offer",
                 "⭐ Get Own Panel ($10/month)\n\n"
                 "• Your own store\n"
                 "• Your own wallet\n"
                 "• Your own admin panel\n"
                 "• Your own categories / products / keys / resellers\n\n"
                 "Renews monthly automatically from your shop wallet. If not enough, it will be revoked.")
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


# ===================== SHOP USERS / WALLET =====================
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

def add_balance(shop_id: int, uid: int, delta: int):
    ensure_shop_user(shop_id, uid)
    with db() as conn:
        conn.execute("UPDATE shop_users SET balance_cents=balance_cents+? WHERE shop_id=? AND user_id=?", (delta, shop_id, uid))

def set_balance(shop_id: int, uid: int, new_bal: int):
    ensure_shop_user(shop_id, uid)
    with db() as conn:
        conn.execute("UPDATE shop_users SET balance_cents=? WHERE shop_id=? AND user_id=?", (new_bal, shop_id, uid))

def can_deduct(shop_id: int, uid: int, amt: int) -> bool:
    return get_balance(shop_id, uid) >= amt

def deduct(shop_id: int, uid: int, amt: int):
    add_balance(shop_id, uid, -amt)

def set_reseller_logged(shop_id: int, uid: int, flag: bool):
    ensure_shop_user(shop_id, uid)
    with db() as conn:
        conn.execute("UPDATE shop_users SET reseller_logged_in=? WHERE shop_id=? AND user_id=?", (1 if flag else 0, shop_id, uid))


# ===================== SHOPS =====================
def get_main_shop_id() -> int:
    return 1

def get_shop(shop_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()

def set_shop_profile(shop_id: int, name: str, welcome: str):
    with db() as conn:
        conn.execute("UPDATE shops SET shop_name=?, welcome_text=? WHERE id=?", (name.strip(), welcome.strip(), shop_id))

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
        INSERT INTO shops(owner_id, shop_name, welcome_text, panel_until, is_suspended, suspended_reason, created_at)
        VALUES(?,?,?,?,0,NULL,?)
        """, (owner_id, f"{owner_id}'s Shop", "Welcome! Customize your store in the Owner Panel.", None, now_iso()))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

def is_shop_owner(shop_id: int, uid: int) -> bool:
    s = get_shop(shop_id)
    return bool(s) and int(s["owner_id"]) == uid

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
    # charge $10 from OWNER balance in THEIR SHOP wallet
    if can_deduct(shop_id, owner_id, PANEL_PRICE_CENTS):
        deduct(shop_id, owner_id, PANEL_PRICE_CENTS)
        new_until = (now_utc() + datetime.timedelta(days=PANEL_DAYS)).isoformat(timespec="seconds")
        set_shop_panel_until(shop_id, new_until)
    else:
        set_shop_panel_until(shop_id, None)

def delete_shop_hard(shop_id: int):
    # hard delete everything for that shop
    with db() as conn:
        conn.execute("DELETE FROM categories WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM subcategories WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM products WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM keys WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM purchases WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM deposits WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM resellers WHERE shop_id=?", (shop_id,))
        conn.execute("DELETE FROM shop_users WHERE shop_id=?", (shop_id,))
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
        if not r: return
        conn.execute("UPDATE categories SET is_active=? WHERE shop_id=? AND id=?", (0 if r["is_active"] else 1, shop_id, cat_id))

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
        conn.execute("INSERT INTO subcategories(shop_id,category_id,name,is_active) VALUES(?,?,?,1)", (shop_id, cat_id, name))

def toggle_subcategory(shop_id: int, sub_id: int):
    with db() as conn:
        r = conn.execute("SELECT is_active FROM subcategories WHERE shop_id=? AND id=?", (shop_id, sub_id)).fetchone()
        if not r: return
        conn.execute("UPDATE subcategories SET is_active=? WHERE shop_id=? AND id=?", (0 if r["is_active"] else 1, shop_id, sub_id))

def add_product(shop_id: int, cat_id: int, sub_id: int, name: str, up: int, rp: int):
    with db() as conn:
        conn.execute("""
        INSERT INTO products(shop_id,category_id,subcategory_id,name,user_price_cents,reseller_price_cents,telegram_link,is_active)
        VALUES(?,?,?,?,?,?,NULL,1)
        """, (shop_id, cat_id, sub_id, name.strip(), up, rp))

def list_products_by_subcat(shop_id: int, sub_id: int):
    with db() as conn:
        return conn.execute("""
        SELECT p.*,
          (SELECT COUNT(*) FROM keys k WHERE k.shop_id=p.shop_id AND k.product_id=p.id AND k.is_used=0) AS stock
        FROM products p
        WHERE p.shop_id=? AND p.subcategory_id=? AND p.is_active=1
        ORDER BY p.id ASC
        """, (shop_id, sub_id)).fetchall()

def list_products_all(shop_id: int):
    with db() as conn:
        return conn.execute("""
        SELECT p.*,
          (SELECT COUNT(*) FROM keys k WHERE k.shop_id=p.shop_id AND k.product_id=p.id AND k.is_used=0) AS stock
        FROM products p
        WHERE p.shop_id=?
        ORDER BY p.id DESC
        """, (shop_id,)).fetchall()

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
        if not r: return
        conn.execute("UPDATE products SET is_active=? WHERE shop_id=? AND id=?", (0 if r["is_active"] else 1, shop_id, pid))

def update_product_link(shop_id: int, pid: int, link: Optional[str]):
    with db() as conn:
        conn.execute("UPDATE products SET telegram_link=? WHERE shop_id=? AND id=?", ((link.strip() if link else None), shop_id, pid))

def update_product_prices(shop_id: int, pid: int, up: int, rp: int):
    with db() as conn:
        conn.execute("UPDATE products SET user_price_cents=?, reseller_price_cents=? WHERE shop_id=? AND id=?", (up, rp, shop_id, pid))

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


# ===================== PURCHASES (HISTORY) =====================
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

def list_resellers(shop_id: int, limit=20, offset=0):
    with db() as conn:
        return conn.execute("""
        SELECT user_id, tg_username, login_username, is_active
        FROM resellers
        WHERE shop_id=?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """, (shop_id, limit, offset)).fetchall()

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
        if not r: return
        conn.execute("UPDATE resellers SET is_active=? WHERE shop_id=? AND user_id=?", (0 if r["is_active"] else 1, shop_id, uid))

def set_reseller_password(shop_id: int, uid: int, pw: str):
    with db() as conn:
        conn.execute("UPDATE resellers SET password_hash=? WHERE shop_id=? AND user_id=?", (sha256(pw), shop_id, uid))


# ===================== SUPPORT FORWARD =====================
def support_header(u) -> str:
    uname = f"@{u.username}" if u.username else "(no username)"
    name = f"{u.first_name or ''} {u.last_name or ''}".strip()
    return f"👤 {name} {uname}\n🆔 Chat ID: {u.id}\n\n"

async def forward_support(update: Update, ctx: ContextTypes.DEFAULT_TYPE, shop_id: int):
    uid = update.effective_user.id
    if is_shop_owner(shop_id, uid):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    owner_id = int(get_shop(shop_id)["owner_id"])
    await ctx.bot.send_message(chat_id=owner_id, text=f"🏪 Shop #{shop_id}\n" + support_header(update.effective_user) + "💬 Message:\n" + text)

async def owner_reply_by_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return
    original = update.message.reply_to_message.text or ""
    if "🆔 Chat ID:" not in original:
        return
    try:
        target = int(original.split("🆔 Chat ID:")[1].split("\n")[0].strip())
    except Exception:
        return
    await ctx.bot.send_message(chat_id=target, text=update.message.text)


# ===================== CLEAN SEND =====================
async def send_clean(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    last_id = get_last_bot_msg_id(uid)
    if last_id:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass
    msg = await update.message.reply_text(text, reply_markup=reply_markup)
    set_last_bot_msg_id(uid, msg.message_id)


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

    # Panel active => no RekkoOwn branding in that shop
    if s["panel_until"] and is_panel_active(shop_id):
        return f"{s['welcome_text']}\n\n— {s['shop_name']}"
    # Not active => show branding
    return f"{s['welcome_text']}\n\n{DEFAULT_BRAND}"


# ===================== UI =====================
def kb_mainmenu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]])

def kb_home(shop_id: int, uid: int) -> InlineKeyboardMarkup:
    ensure_shop_user(shop_id, uid)
    su = get_shop_user(shop_id, uid)
    reseller_logged = bool(su and su["reseller_logged_in"])

    buttons = [
        [InlineKeyboardButton("🛍️ Products", callback_data="home:products"),
         InlineKeyboardButton("💰 Wallet", callback_data="home:wallet")],
        [InlineKeyboardButton("📜 History", callback_data="home:history"),
         InlineKeyboardButton("📩 Support", callback_data="home:support")],
        [InlineKeyboardButton("🔐 Reseller Login", callback_data="res:login"),
         InlineKeyboardButton("⭐ Get Own Panel", callback_data="panel:info")],
    ]

    if reseller_logged:
        buttons.insert(0, [InlineKeyboardButton("🧑‍💻 Reseller: ON (Logout)", callback_data="res:logout")])

    # Owner entry
    if is_shop_owner(shop_id, uid):
        if shop_id == get_main_shop_id() and is_super_admin(uid):
            buttons.append([InlineKeyboardButton("🛠️ Owner Panel", callback_data="own:menu")])
        else:
            renew_panel_if_needed(shop_id)
            if is_panel_active(shop_id):
                buttons.append([InlineKeyboardButton("🛠️ Owner Panel", callback_data="own:menu")])
            else:
                buttons.append([InlineKeyboardButton("🔒 Owner Panel (Get Own Panel Required)", callback_data="panel:info")])

    # Switch shop shortcuts
    if shop_id != get_main_shop_id():
        buttons.append([InlineKeyboardButton("⬅️ Back to RekkoShop", callback_data="shop:switch:main")])
    else:
        sid = get_shop_by_owner(uid)
        if sid and sid != get_main_shop_id():
            buttons.append([InlineKeyboardButton("🏪 My Shop", callback_data=f"shop:switch:{sid}")])

    # Super Admin platform tools (only in main)
    if shop_id == get_main_shop_id() and is_super_admin(uid):
        buttons.append([InlineKeyboardButton("🧾 Platform (Ban/Restrict/Manage Shops)", callback_data="sa:menu")])

    return InlineKeyboardMarkup(buttons)

def kb_wallet(shop_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Deposit", callback_data="wallet:deposit"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_deposit_amounts() -> InlineKeyboardMarkup:
    presets = [500, 1000, 2000, 5000]
    btns = [InlineKeyboardButton(f"💵 {money(a)}", callback_data=f"dep:amt:{a}") for a in presets]
    kb = rows2(btns, 2)
    kb.append([InlineKeyboardButton("✍️ Custom Amount", callback_data="dep:custom"),
               InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
    return InlineKeyboardMarkup(kb)

def kb_owner_menu(shop_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="own:users:0"),
         InlineKeyboardButton("💳 Deposits", callback_data="own:deps:0")],
        [InlineKeyboardButton("📂 Categories", callback_data="own:cats"),
         InlineKeyboardButton("🧩 Co-Categories", callback_data="own:subs")],
        [InlineKeyboardButton("📦 Products", callback_data="own:products"),
         InlineKeyboardButton("🔑 Keys", callback_data="own:keys")],
        [InlineKeyboardButton("🧑‍💼 Resellers", callback_data="own:resellers:0"),
         InlineKeyboardButton("⚠️ Danger Zone", callback_data="own:danger")],
        [InlineKeyboardButton("✏️ Edit Store", callback_data="own:editstore"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_sa_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Manage Owners (Ban/Restrict)", callback_data="sa:owners:0"),
         InlineKeyboardButton("🏪 Manage Shops", callback_data="sa:shops:0")],
        [InlineKeyboardButton("✏️ Edit Get Own Panel Text", callback_data="sa:paneltext"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])


# ===================== START =====================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    ctx.user_data.clear()

    # deep link: /start shop_12
    args = ctx.args or []
    if args and args[0].startswith("shop_"):
        try:
            sid = int(args[0].split("_", 1)[1])
            if get_shop(sid):
                set_active_shop_id(ctx, sid)
        except Exception:
            set_active_shop_id(ctx, get_main_shop_id())
    else:
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

    # Block access if shop suspended (still allow going back main)
    suspended, reason = shop_is_suspended(shop_id)
    if suspended and data not in ("shop:switch:main", "home:menu"):
        txt = "⛔ This shop is suspended.\n\n" + (f"Reason: {reason}" if reason else "")
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back to RekkoShop", callback_data="shop:switch:main")]
        ]))

    # ===== shop switch =====
    if data.startswith("shop:switch:"):
        target = data.split(":")[-1]
        if target == "main":
            set_active_shop_id(ctx, get_main_shop_id())
        else:
            try:
                sid = int(target)
                if get_shop(sid):
                    set_active_shop_id(ctx, sid)
            except Exception:
                pass
        shop_id = get_active_shop_id(ctx)
        ensure_shop_user(shop_id, uid)
        return await q.edit_message_text(shop_home_text(shop_id), reply_markup=kb_home(shop_id, uid))

    # ===== home =====
    if data == "home:menu":
        ctx.user_data["flow"] = None
        return await q.edit_message_text(shop_home_text(shop_id), reply_markup=kb_home(shop_id, uid))

    if data == "home:wallet":
        bal = get_balance(shop_id, uid)
        txt = f"💰 Wallet\n\nBalance: {money(bal)}\n\nUSDT (TRC-20) Address:\n{USDT_TRC20_ADDRESS}"
        return await q.edit_message_text(txt, reply_markup=kb_wallet(shop_id))

    if data == "wallet:deposit":
        ctx.user_data["flow"] = "dep_choose"
        return await q.edit_message_text("💳 Deposit\n\nChoose amount:", reply_markup=kb_deposit_amounts())

    if data == "home:support":
        ctx.user_data["flow"] = None
        return await q.edit_message_text("📩 Support\n\nType your message. Shop owner will reply.", reply_markup=kb_mainmenu())

    if data == "home:history":
        rows = list_purchases(shop_id, uid, limit=10)
        if not rows:
            return await q.edit_message_text("📜 History\n\nNo purchases yet.", reply_markup=kb_mainmenu())
        lines = [f"📜 History (Shop #{shop_id}) — last 10\n"]
        btns = []
        for r in rows:
            lines.append(f"• #{r['id']} — {r['product_name']} — {money(r['price_cents'])}")
            btns.append(InlineKeyboardButton("🔒 Get Files", callback_data=f"hist:get:{r['id']}"))
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("hist:get:"):
        purchase_id = int(data.split(":")[-1])
        pur = get_purchase(shop_id, uid, purchase_id)
        if not pur:
            return await q.answer("Purchase not found.", show_alert=True)

        p = get_product(shop_id, int(pur["product_id"]))
        if not p:
            return await q.answer("Product not found.", show_alert=True)

        link = p["telegram_link"]
        if not link or not str(link).startswith("http"):
            return await q.answer("Telegram link not set for this product. (Owner: set Telegram Link)", show_alert=True)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔒 Get Files", url=link)],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        await ctx.bot.send_message(
            chat_id=uid,
            text=f"✅ Access for order #{purchase_id}\n\n🔑 Key:\n{pur['key_text']}\n\nTap the button:",
            reply_markup=kb
        )
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    # ===== products browse =====
    if data == "home:products":
        cats = list_categories(shop_id, True)
        if not cats:
            return await q.edit_message_text("No categories yet.", reply_markup=kb_mainmenu())
        btns = [InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}") for c in cats]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📂 Choose Category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("shop:cat:"):
        cat_id = int(data.split(":")[-1])
        subs = list_subcategories(shop_id, cat_id, True)
        if not subs:
            return await q.edit_message_text("No co-categories yet.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="home:products"),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ]))
        btns = [InlineKeyboardButton(s["name"], callback_data=f"shop:sub:{s['id']}") for s in subs]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="home:products"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("🧩 Choose Co-Category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("shop:sub:"):
        sub_id = int(data.split(":")[-1])
        prods = list_products_by_subcat(shop_id, sub_id)
        if not prods:
            return await q.edit_message_text("No products here yet.", reply_markup=kb_mainmenu())
        btns = [InlineKeyboardButton(f"{p['name']} (Stock:{p['stock']})", callback_data=f"shop:prod:{p['id']}") for p in prods]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="home:products"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📦 Product List:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("shop:prod:"):
        pid = int(data.split(":")[-1])
        p = get_product(shop_id, pid)
        if not p or p["is_active"] != 1:
            return await q.answer("Not available", show_alert=True)

        su = get_shop_user(shop_id, uid)
        reseller_ok = bool(su and su["reseller_logged_in"]) and reseller_by_uid(shop_id, uid) is not None
        price = int(p["reseller_price_cents"] if reseller_ok else p["user_price_cents"])

        txt = f"📌 {p['name']}\nPrice: {money(price)}\nStock: {p['stock']}\n\nBuy using wallet balance."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Buy", callback_data=f"buy:{pid}"),
             InlineKeyboardButton("⬅️ Back", callback_data="home:products")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("buy:"):
        pid = int(data.split(":")[-1])
        p = get_product(shop_id, pid)
        if not p or p["is_active"] != 1 or p["stock"] <= 0:
            return await q.answer("Out of stock", show_alert=True)

        su = get_shop_user(shop_id, uid)
        reseller_ok = bool(su and su["reseller_logged_in"]) and reseller_by_uid(shop_id, uid) is not None
        price = int(p["reseller_price_cents"] if reseller_ok else p["user_price_cents"])

        if not can_deduct(shop_id, uid, price):
            return await q.answer("Not enough balance", show_alert=True)

        key = take_key(shop_id, pid, uid)
        if not key:
            return await q.answer("Out of stock", show_alert=True)

        deduct(shop_id, uid, price)
        add_purchase(shop_id, uid, pid, p["name"], price, key)

        return await q.edit_message_text(
            "✅ Purchase Successful!\n\n"
            f"🔑 Key:\n{key}\n\n"
            "📜 Go to History → Get Files to open the Telegram link.",
            reply_markup=kb_mainmenu()
        )

    # ===== deposit choose amount =====
    if data.startswith("dep:amt:"):
        amt = int(data.split(":")[-1])
        ctx.user_data["flow"] = "dep_wait_photo"
        ctx.user_data["dep_amount"] = amt
        return await q.edit_message_text(
            f"✅ Amount set: {money(amt)}\n\nNow send payment screenshot (photo).",
            reply_markup=kb_mainmenu()
        )

    if data == "dep:custom":
        ctx.user_data["flow"] = "dep_custom"
        return await q.edit_message_text("✍️ Send amount (example 10 or 10.5):", reply_markup=kb_mainmenu())

    # ===== reseller =====
    if data == "res:login":
        ctx.user_data["flow"] = "res_login_user"
        return await q.edit_message_text("🔐 Reseller Login\n\nSend login username:", reply_markup=kb_mainmenu())

    if data == "res:logout":
        set_reseller_logged(shop_id, uid, False)
        ctx.user_data["flow"] = None
        return await q.edit_message_text("✅ Logged out.", reply_markup=kb_home(shop_id, uid))

    # ===== Get Own Panel =====
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
            "Go to Owner Panel → Edit Store to customize."
        )
        return await q.edit_message_text(txt, reply_markup=kb_home(sid, uid))

    # ===== Owner Panel =====
    if data == "own:menu":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)

        # for non-main shops: must be active + not restricted/banned
        if shop_id != get_main_shop_id():
            ok, msg = can_be_owner(uid)
            if not ok:
                return await q.answer(msg, show_alert=True)

            renew_panel_if_needed(shop_id)
            if not is_panel_active(shop_id):
                return await q.answer("Get Own Panel expired. Top up your SHOP wallet to auto-renew.", show_alert=True)

        ctx.user_data["flow"] = None
        return await q.edit_message_text("🛠️ Owner Panel", reply_markup=kb_owner_menu(shop_id))

    if data == "own:danger":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        if shop_id == get_main_shop_id():
            return await q.answer("Main shop cannot be deleted.", show_alert=True)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ Delete My Shop", callback_data="own:delete_confirm")],
            [InlineKeyboardButton("⬅️ Back", callback_data="own:menu"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text("⚠️ Danger Zone\n\nDelete shop will remove all products/keys/users/deposits/history.", reply_markup=kb)

    if data == "own:delete_confirm":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ YES, DELETE", callback_data="own:delete_do"),
             InlineKeyboardButton("❌ Cancel", callback_data="own:menu")]
        ])
        return await q.edit_message_text("Are you sure? This cannot be undone.", reply_markup=kb)

    if data == "own:delete_do":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        if shop_id == get_main_shop_id():
            return await q.answer("Main shop cannot be deleted.", show_alert=True)
        delete_shop_hard(shop_id)
        set_active_shop_id(ctx, get_main_shop_id())
        main_id = get_active_shop_id(ctx)
        return await q.edit_message_text("✅ Shop deleted. Back to RekkoShop.", reply_markup=kb_home(main_id, uid))

    # Owner: edit store
    if data == "own:editstore":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = "own_edit_name"
        return await q.edit_message_text("✏️ Send your Shop Name:", reply_markup=kb_owner_menu(shop_id))

    # Owner: categories
    if data == "own:cats":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        cats = list_categories(shop_id, False)
        kb = [[InlineKeyboardButton("➕ Add Category", callback_data="own:catadd")]]
        for c in cats:
            state = "✅" if c["is_active"] else "❌"
            kb.append([InlineKeyboardButton(f"{state} {c['name']}", callback_data=f"own:cattog:{c['id']}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📂 Categories:", reply_markup=InlineKeyboardMarkup(kb))

    if data == "own:catadd":
        ctx.user_data["flow"] = "own_add_cat"
        return await q.edit_message_text("Type category name:", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:cattog:"):
        toggle_category(shop_id, int(data.split(":")[-1]))
        return await q.edit_message_text("✅ Updated.", reply_markup=kb_owner_menu(shop_id))

    # Owner: subcategories
    if data == "own:subs":
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        cats = list_categories(shop_id, True)
        if not cats:
            return await q.edit_message_text("Add a category first.", reply_markup=kb_owner_menu(shop_id))
        btns = [InlineKeyboardButton(c["name"], callback_data=f"own:subcatpick:{c['id']}") for c in cats]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("Pick Category to manage Co-Categories:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:subcatpick:"):
        cat_id = int(data.split(":")[-1])
        subs = list_subcategories(shop_id, cat_id, False)
        kb = [[InlineKeyboardButton("➕ Add Co-Category", callback_data=f"own:subadd:{cat_id}")]]
        for s in subs:
            state = "✅" if s["is_active"] else "❌"
            kb.append([InlineKeyboardButton(f"{state} {s['name']}", callback_data=f"own:subtog:{s['id']}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:subs")])
        return await q.edit_message_text("🧩 Co-Categories:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:subadd:"):
        ctx.user_data["flow"] = "own_add_sub"
        ctx.user_data["tmp_cat_id"] = int(data.split(":")[-1])
        return await q.edit_message_text("Type Co-Category name:", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:subtog:"):
        toggle_subcategory(shop_id, int(data.split(":")[-1]))
        return await q.edit_message_text("✅ Updated.", reply_markup=kb_owner_menu(shop_id))

    # Owner: products
    if data == "own:products":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Product", callback_data="own:prodadd"),
             InlineKeyboardButton("📋 List Products", callback_data="own:prodlist")],
            [InlineKeyboardButton("⬅️ Back", callback_data="own:menu"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text("📦 Products:", reply_markup=kb)

    if data == "own:prodadd":
        cats = list_categories(shop_id, True)
        if not cats:
            return await q.edit_message_text("Add categories first.", reply_markup=kb_owner_menu(shop_id))
        ctx.user_data["flow"] = "own_prod_pick_cat"
        btns = [InlineKeyboardButton(c["name"], callback_data=f"own:pickcat:{c['id']}") for c in cats]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:products")])
        return await q.edit_message_text("Pick Category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:pickcat:"):
        if ctx.user_data.get("flow") != "own_prod_pick_cat":
            return
        cat_id = int(data.split(":")[-1])
        subs = list_subcategories(shop_id, cat_id, True)
        if not subs:
            return await q.answer("Add Co-Categories for this category first.", show_alert=True)
        ctx.user_data["flow"] = "own_prod_pick_sub"
        ctx.user_data["new_prod"] = {"cat_id": cat_id}
        btns = [InlineKeyboardButton(s["name"], callback_data=f"own:picksu:{s['id']}") for s in subs]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:products")])
        return await q.edit_message_text("Pick Co-Category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:picksu:"):
        if ctx.user_data.get("flow") != "own_prod_pick_sub":
            return
        sub_id = int(data.split(":")[-1])
        ctx.user_data["new_prod"]["sub_id"] = sub_id
        ctx.user_data["flow"] = "own_prod_name"
        return await q.edit_message_text("Type Product Name:", reply_markup=kb_owner_menu(shop_id))

    if data == "own:prodlist":
        prods = list_products_all(shop_id)
        if not prods:
            return await q.edit_message_text("No products yet.", reply_markup=kb_owner_menu(shop_id))
        btns = [InlineKeyboardButton(f"#{p['id']} {p['name']}", callback_data=f"own:prod:{p['id']}") for p in prods[:60]]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:products")])
        return await q.edit_message_text("Tap product:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:prod:"):
        pid = int(data.split(":")[-1])
        p = get_product(shop_id, pid)
        if not p:
            return await q.answer("Not found", show_alert=True)
        txt = (
            f"📦 Product #{pid}\n{p['name']}\n"
            f"User:{money(p['user_price_cents'])}  Reseller:{money(p['reseller_price_cents'])}\n"
            f"Stock:{p['stock']}\n"
            f"Telegram Link:{p['telegram_link'] or '-'}\n"
            f"Active:{'YES' if p['is_active'] else 'NO'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅/❌ Toggle", callback_data=f"own:prodtog:{pid}"),
             InlineKeyboardButton("💲 Prices", callback_data=f"own:prodprice:{pid}")],
            [InlineKeyboardButton("🔗 Set Telegram Link", callback_data=f"own:prodlink:{pid}"),
             InlineKeyboardButton("⬅️ Back", callback_data="own:prodlist")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("own:prodtog:"):
        toggle_product(shop_id, int(data.split(":")[-1]))
        return await q.edit_message_text("✅ Updated.", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:prodlink:"):
        ctx.user_data["flow"] = "own_set_link"
        ctx.user_data["target_product"] = int(data.split(":")[-1])
        return await q.edit_message_text("Paste Telegram invite link (or - to clear):", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:prodprice:"):
        ctx.user_data["flow"] = "own_set_prices"
        ctx.user_data["target_product"] = int(data.split(":")[-1])
        return await q.edit_message_text("Type: USER_PRICE,RESELLER_PRICE (example 10,7):", reply_markup=kb_owner_menu(shop_id))

    # Owner: keys
    if data == "own:keys":
        prods = list_products_all(shop_id)
        if not prods:
            return await q.edit_message_text("Add products first.", reply_markup=kb_owner_menu(shop_id))
        btns = [InlineKeyboardButton(f"#{p['id']} {p['name']}", callback_data=f"own:keysprod:{p['id']}") for p in prods[:60]]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("Choose product:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:keysprod:"):
        ctx.user_data["flow"] = "own_keys_paste"
        ctx.user_data["target_product"] = int(data.split(":")[-1])
        return await q.edit_message_text("Paste keys (one per line):", reply_markup=kb_owner_menu(shop_id))

    # Owner: deposits
    if data.startswith("own:deps:"):
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        rows = list_pending_deposits(shop_id, PAGE_SIZE, page * PAGE_SIZE)
        if not rows:
            return await q.edit_message_text("No pending deposits.", reply_markup=kb_owner_menu(shop_id))
        btns = [InlineKeyboardButton(f"#{d['id']} {money(d['amount_cents'])}", callback_data=f"own:dep:{d['id']}") for d in rows]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("💳 Pending Deposits:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:dep:"):
        dep_id = int(data.split(":")[-1])
        d = get_deposit(shop_id, dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Not found", show_alert=True)

        await ctx.bot.send_photo(
            chat_id=uid,
            photo=d["photo_file_id"],
            caption=f"Deposit #{dep_id}\nUser:{d['user_id']}\nAmount:{money(d['amount_cents'])}\nCaption:{d['caption'] or '-'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"own:depok:{dep_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"own:depnok:{dep_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="own:deps:0")]
        ])
        return await q.edit_message_text("Choose action:", reply_markup=kb)

    if data.startswith("own:depok:"):
        dep_id = int(data.split(":")[-1])
        d = get_deposit(shop_id, dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Not found", show_alert=True)
        set_deposit_status(shop_id, dep_id, "APPROVED", uid)
        add_balance(shop_id, int(d["user_id"]), int(d["amount_cents"]))
        await ctx.bot.send_message(chat_id=int(d["user_id"]), text=f"✅ Deposit approved: {money(d['amount_cents'])}")
        return await q.edit_message_text("✅ Approved.", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:depnok:"):
        dep_id = int(data.split(":")[-1])
        d = get_deposit(shop_id, dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Not found", show_alert=True)
        set_deposit_status(shop_id, dep_id, "REJECTED", uid)
        await ctx.bot.send_message(chat_id=int(d["user_id"]), text="❌ Deposit rejected.")
        return await q.edit_message_text("❌ Rejected.", reply_markup=kb_owner_menu(shop_id))

    # Owner: users + reply + balance edit
    if data.startswith("own:users:"):
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        with db() as conn:
            rows = conn.execute("""
                SELECT su.user_id, su.balance_cents, u.username, u.first_name, u.last_name
                FROM shop_users su
                LEFT JOIN users u ON u.user_id = su.user_id
                WHERE su.shop_id=?
                ORDER BY su.user_id DESC
                LIMIT ? OFFSET ?
            """, (shop_id, PAGE_SIZE, page * PAGE_SIZE)).fetchall()
        if not rows:
            return await q.edit_message_text("No users yet.", reply_markup=kb_owner_menu(shop_id))
        btns = []
        for r in rows:
            title = f"@{r['username']}" if r["username"] else (r["first_name"] or "User")
            btns.append(InlineKeyboardButton(title, callback_data=f"own:user:{r['user_id']}"))
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("👥 Users (tap one):", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("own:user:"):
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        target = int(data.split(":")[-1])
        su = get_shop_user(shop_id, target)
        txt = f"👤 User ID: {target}\nBalance: {money(int(su['balance_cents']))}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📨 Reply", callback_data=f"own:reply:{target}"),
             InlineKeyboardButton("✍️ Set Balance", callback_data=f"own:balset:{target}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="own:users:0")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("own:reply:"):
        ctx.user_data["flow"] = "own_reply"
        ctx.user_data["target_user"] = int(data.split(":")[-1])
        return await q.edit_message_text("📨 Type your reply message:", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:balset:"):
        ctx.user_data["flow"] = "own_bal_set"
        ctx.user_data["target_user"] = int(data.split(":")[-1])
        return await q.edit_message_text("✍️ Set new balance amount:", reply_markup=kb_owner_menu(shop_id))

    # Owner: resellers
    if data.startswith("own:resellers:"):
        if not is_shop_owner(shop_id, uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        rows = list_resellers(shop_id, PAGE_SIZE, page * PAGE_SIZE)
        kb = [[InlineKeyboardButton("➕ Add Reseller", callback_data="own:resadd")]]
        if rows:
            btns = []
            for r in rows:
                state = "✅" if r["is_active"] else "❌"
                btns.append(InlineKeyboardButton(f"{state} {r['login_username']}", callback_data=f"own:res:{r['user_id']}"))
            kb += rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="own:menu")])
        return await q.edit_message_text("🧑‍💼 Resellers:", reply_markup=InlineKeyboardMarkup(kb))

    if data == "own:resadd":
        ctx.user_data["flow"] = "own_res_add"
        return await q.edit_message_text("Type: @telegramusername, loginusername, password", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:res:"):
        rid = int(data.split(":")[-1])
        r = reseller_by_uid(shop_id, rid)
        if not r:
            return await q.answer("Not found", show_alert=True)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅/❌ Toggle", callback_data=f"own:restog:{rid}"),
             InlineKeyboardButton("🔑 Reset PW", callback_data=f"own:respw:{rid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="own:resellers:0")]
        ])
        return await q.edit_message_text(
            f"Reseller @{r['tg_username']}\nLogin: {r['login_username']}\nActive: {'YES' if r['is_active'] else 'NO'}",
            reply_markup=kb
        )

    if data.startswith("own:restog:"):
        toggle_reseller(shop_id, int(data.split(":")[-1]))
        return await q.edit_message_text("✅ Updated.", reply_markup=kb_owner_menu(shop_id))

    if data.startswith("own:respw:"):
        ctx.user_data["flow"] = "own_res_pw"
        ctx.user_data["target_reseller"] = int(data.split(":")[-1])
        return await q.edit_message_text("Type new password:", reply_markup=kb_owner_menu(shop_id))

    # ===== Super Admin Platform =====
    if data == "sa:menu":
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = None
        return await q.edit_message_text("🧾 Platform Tools", reply_markup=kb_sa_menu())

    if data == "sa:paneltext":
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = "sa_edit_panel_text"
        return await q.edit_message_text("Send new Get Own Panel description text:", reply_markup=kb_sa_menu())

    if data.startswith("sa:shops:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        rows = list_shops(PAGE_SIZE, page * PAGE_SIZE)
        if not rows:
            return await q.edit_message_text("No shops.", reply_markup=kb_sa_menu())
        btns = []
        for r in rows:
            sflag = "⛔" if r["is_suspended"] else "✅"
            btns.append(InlineKeyboardButton(f"{sflag} #{r['id']} {r['shop_name']}", callback_data=f"sa:shop:{r['id']}"))
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="sa:menu")])
        return await q.edit_message_text("🏪 Manage Shops:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("sa:shop:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        sid = int(data.split(":")[-1])
        s = get_shop(sid)
        if not s:
            return await q.answer("Not found", show_alert=True)
        txt = (
            f"🏪 Shop #{sid}\n"
            f"Owner: {s['owner_id']}\n"
            f"Name: {s['shop_name']}\n"
            f"Suspended: {'YES' if s['is_suspended'] else 'NO'}\n"
            f"Reason: {s['suspended_reason'] or '-'}\n"
            f"Panel Until: {s['panel_until'] or '-'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⛔ Suspend", callback_data=f"sa:suspend:{sid}"),
             InlineKeyboardButton("✅ Unsuspend", callback_data=f"sa:unsuspend:{sid}")],
            [InlineKeyboardButton("🗑️ Delete Shop", callback_data=f"sa:shopdel_confirm:{sid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="sa:shops:0")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("sa:suspend:"):
        sid = int(data.split(":")[-1])
        ctx.user_data["flow"] = "sa_suspend_reason"
        ctx.user_data["target_shop"] = sid
        return await q.edit_message_text("Type suspension reason:", reply_markup=kb_sa_menu())

    if data.startswith("sa:unsuspend:"):
        sid = int(data.split(":")[-1])
        set_shop_suspension(sid, False, None)
        return await q.edit_message_text("✅ Unsuspended.", reply_markup=kb_sa_menu())

    if data.startswith("sa:shopdel_confirm:"):
        sid = int(data.split(":")[-1])
        if sid == get_main_shop_id():
            return await q.answer("Cannot delete main shop.", show_alert=True)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ YES DELETE", callback_data=f"sa:shopdel_do:{sid}"),
             InlineKeyboardButton("❌ Cancel", callback_data="sa:menu")]
        ])
        return await q.edit_message_text("Confirm delete shop? This removes everything.", reply_markup=kb)

    if data.startswith("sa:shopdel_do:"):
        sid = int(data.split(":")[-1])
        if sid == get_main_shop_id():
            return await q.answer("Cannot delete main shop.", show_alert=True)
        delete_shop_hard(sid)
        return await q.edit_message_text("✅ Shop deleted.", reply_markup=kb_sa_menu())

    # Owners list (ban/restrict)
    if data.startswith("sa:owners:"):
        if not (shop_id == get_main_shop_id() and is_super_admin(uid)):
            return await q.answer("Not authorized", show_alert=True)
        # show owners that have a shop OR have ban/restrict flags
        page = int(data.split(":")[-1])
        with db() as conn:
            rows = conn.execute("""
            SELECT u.user_id, u.username, u.first_name, u.owner_banned, u.owner_restrict_until
            FROM users u
            WHERE u.user_id IN (SELECT owner_id FROM shops)
               OR u.owner_banned=1
               OR u.owner_restrict_until IS NOT NULL
            ORDER BY u.user_id DESC
            LIMIT ? OFFSET ?
            """, (PAGE_SIZE, page * PAGE_SIZE)).fetchall()
        if not rows:
            return await q.edit_message_text("No owners found.", reply_markup=kb_sa_menu())

        btns = []
        for r in rows:
            name = f"@{r['username']}" if r["username"] else (r["first_name"] or str(r["user_id"]))
            tag = "🚫" if r["owner_banned"] else ("🔒" if (r["owner_restrict_until"] and owner_is_restricted(int(r["user_id"]))) else "✅")
            btns.append(InlineKeyboardButton(f"{tag} {name}", callback_data=f"sa:owner:{r['user_id']}"))
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="sa:menu")])
        return await q.edit_message_text("👤 Manage Owners:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("sa:owner:"):
        target = int(data.split(":")[-1])
        with db() as conn:
            u = conn.execute("SELECT * FROM users WHERE user_id=?", (target,)).fetchone()
        if not u:
            return await q.answer("User not found", show_alert=True)

        sid = get_shop_by_owner(target)
        banned = owner_is_banned(target)
        restricted = owner_is_restricted(target)
        until = owner_restrict_until(target)

        txt = (
            f"👤 Owner/User: {target}\n"
            f"Username: @{u['username']}" if u["username"] else f"👤 Owner/User: {target}\nUsername: -"
        )
        txt += (
            f"\nShop: {sid or '-'}"
            f"\nBanned (perm): {'YES' if banned else 'NO'}"
            f"\nRestricted (temp): {'YES' if restricted else 'NO'}"
            f"\nRestrict until: {until or '-'}"
            f"\nReason: {u['owner_block_reason'] or '-'}"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 Ban Permanent", callback_data=f"sa:ban:{target}"),
             InlineKeyboardButton("🔒 Restrict Temp", callback_data=f"sa:restrict:{target}")],
            [InlineKeyboardButton("✅ Unban", callback_data=f"sa:unban:{target}"),
             InlineKeyboardButton("✅ Unrestrict", callback_data=f"sa:unrestrict:{target}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="sa:owners:0")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("sa:ban:"):
        target = int(data.split(":")[-1])
        ctx.user_data["flow"] = "sa_ban_reason"
        ctx.user_data["target_owner"] = target
        return await q.edit_message_text("Type BAN reason (permanent):", reply_markup=kb_sa_menu())

    if data.startswith("sa:restrict:"):
        target = int(data.split(":")[-1])
        ctx.user_data["target_owner"] = target
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("7 days", callback_data="sa:restrict_days:7"),
             InlineKeyboardButton("30 days", callback_data="sa:restrict_days:30")],
            [InlineKeyboardButton("90 days", callback_data="sa:restrict_days:90"),
             InlineKeyboardButton("Custom (type days)", callback_data="sa:restrict_custom")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"sa:owner:{target}")]
        ])
        return await q.edit_message_text("Choose restriction duration:", reply_markup=kb)

    if data.startswith("sa:restrict_days:"):
        days = int(data.split(":")[-1])
        target = int(ctx.user_data.get("target_owner", 0))
        until = (now_utc() + datetime.timedelta(days=days)).isoformat(timespec="seconds")
        ctx.user_data["flow"] = "sa_restrict_reason"
        ctx.user_data["restrict_until"] = until
        return await q.edit_message_text(f"Type RESTRICTION reason (until {until}):", reply_markup=kb_sa_menu())

    if data == "sa:restrict_custom":
        ctx.user_data["flow"] = "sa_restrict_custom_days"
        return await q.edit_message_text("Type number of days to restrict (example 14):", reply_markup=kb_sa_menu())

    if data.startswith("sa:unban:"):
        target = int(data.split(":")[-1])
        set_owner_ban(target, False, None)
        return await q.edit_message_text("✅ Unbanned.", reply_markup=kb_sa_menu())

    if data.startswith("sa:unrestrict:"):
        target = int(data.split(":")[-1])
        set_owner_restrict(target, None, None)
        return await q.edit_message_text("✅ Unrestricted.", reply_markup=kb_sa_menu())

    return


# ===================== TEXT HANDLER (typing) =====================
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    shop_id = get_active_shop_id(ctx)
    ensure_shop_user(shop_id, uid)

    text = (update.message.text or "").strip()
    flow = ctx.user_data.get("flow")

    # deposit custom amount
    if flow == "dep_custom":
        amt = to_cents(text)
        if amt is None:
            return await send_clean(update, ctx, "Send number like 10 or 10.5", reply_markup=kb_mainmenu())
        ctx.user_data["flow"] = "dep_wait_photo"
        ctx.user_data["dep_amount"] = amt
        return await send_clean(update, ctx, f"✅ Amount set: {money(amt)}\nNow send screenshot (photo).", reply_markup=kb_mainmenu())

    # reseller login
    if flow == "res_login_user":
        ctx.user_data["flow"] = "res_login_pw"
        ctx.user_data["res_login_user"] = text.lower()
        return await send_clean(update, ctx, "Type password:", reply_markup=kb_mainmenu())

    if flow == "res_login_pw":
        login = ctx.user_data.get("res_login_user", "")
        ctx.user_data["flow"] = None
        rec = reseller_by_login(shop_id, login)
        if not rec or rec["is_active"] != 1 or int(rec["user_id"]) != uid:
            set_reseller_logged(shop_id, uid, False)
            return await send_clean(update, ctx, "❌ Login failed.", reply_markup=kb_home(shop_id, uid))
        if sha256(text) != rec["password_hash"]:
            set_reseller_logged(shop_id, uid, False)
            return await send_clean(update, ctx, "❌ Wrong password.", reply_markup=kb_home(shop_id, uid))
        set_reseller_logged(shop_id, uid, True)
        return await send_clean(update, ctx, "✅ Reseller login success.", reply_markup=kb_home(shop_id, uid))

    # Owner panel typed flows
    if flow and is_shop_owner(shop_id, uid):
        if shop_id != get_main_shop_id():
            ok, msg = can_be_owner(uid)
            if not ok:
                ctx.user_data["flow"] = None
                return await send_clean(update, ctx, msg, reply_markup=kb_home(shop_id, uid))
            renew_panel_if_needed(shop_id)
            if not is_panel_active(shop_id):
                ctx.user_data["flow"] = None
                return await send_clean(update, ctx, "Get Own Panel expired. Top up SHOP wallet to renew.", reply_markup=kb_home(shop_id, uid))

        if flow == "own_edit_name":
            ctx.user_data["tmp_shop_name"] = text
            ctx.user_data["flow"] = "own_edit_welcome"
            return await send_clean(update, ctx, "Now send Welcome text:", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_edit_welcome":
            name = ctx.user_data.get("tmp_shop_name", "").strip() or "My Shop"
            set_shop_profile(shop_id, name, text)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "✅ Store updated.", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_add_cat":
            add_category(shop_id, text)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "✅ Category added.", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_add_sub":
            cat_id = int(ctx.user_data.get("tmp_cat_id", 0))
            add_subcategory(shop_id, cat_id, text)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "✅ Co-Category added.", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_prod_name":
            ctx.user_data["new_prod"]["name"] = text
            ctx.user_data["flow"] = "own_prod_user_price"
            return await send_clean(update, ctx, "Send USER price (example 10 or 10.5):", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_prod_user_price":
            up = to_cents(text)
            if up is None:
                return await send_clean(update, ctx, "Send a valid number.", reply_markup=kb_owner_menu(shop_id))
            ctx.user_data["new_prod"]["up"] = up
            ctx.user_data["flow"] = "own_prod_res_price"
            return await send_clean(update, ctx, "Send RESELLER price:", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_prod_res_price":
            rp = to_cents(text)
            if rp is None:
                return await send_clean(update, ctx, "Send a valid number.", reply_markup=kb_owner_menu(shop_id))
            info = ctx.user_data.get("new_prod", {})
            add_product(shop_id, int(info["cat_id"]), int(info["sub_id"]), info["name"], int(info["up"]), int(rp))
            ctx.user_data["flow"] = None
            ctx.user_data.pop("new_prod", None)
            return await send_clean(update, ctx, "✅ Product added.", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_set_link":
            pid = int(ctx.user_data.get("target_product", 0))
            link = text.strip()
            update_product_link(shop_id, pid, None if link == "-" else link)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "✅ Telegram link saved.", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_set_prices":
            pid = int(ctx.user_data.get("target_product", 0))
            if "," not in text:
                return await send_clean(update, ctx, "Use: USER_PRICE,RESELLER_PRICE (example 10,7)", reply_markup=kb_owner_menu(shop_id))
            a, b = [x.strip() for x in text.split(",", 1)]
            up = to_cents(a); rp = to_cents(b)
            if up is None or rp is None:
                return await send_clean(update, ctx, "Invalid prices.", reply_markup=kb_owner_menu(shop_id))
            update_product_prices(shop_id, pid, up, rp)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "✅ Prices updated.", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_keys_paste":
            pid = int(ctx.user_data.get("target_product", 0))
            n = add_keys(shop_id, pid, text.splitlines())
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, f"✅ Added {n} keys.", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_res_add":
            parts = [p.strip() for p in text.split(",")]
            if len(parts) != 3:
                return await send_clean(update, ctx, "Format: @telegramusername, loginusername, password", reply_markup=kb_owner_menu(shop_id))
            ok, msg = add_reseller_by_tg_username(shop_id, parts[0], parts[1], parts[2])
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, ("✅ " if ok else "❌ ") + msg, reply_markup=kb_owner_menu(shop_id))

        if flow == "own_res_pw":
            rid = int(ctx.user_data.get("target_reseller", 0))
            set_reseller_password(shop_id, rid, text)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "✅ Password reset.", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_reply":
            target = int(ctx.user_data.get("target_user", 0))
            ctx.user_data["flow"] = None
            await ctx.bot.send_message(chat_id=target, text=text)
            return await send_clean(update, ctx, "✅ Sent.", reply_markup=kb_owner_menu(shop_id))

        if flow == "own_bal_set":
            target = int(ctx.user_data.get("target_user", 0))
            amt = to_cents(text)
            if amt is None:
                return await send_clean(update, ctx, "Send amount like 10 or 10.5", reply_markup=kb_owner_menu(shop_id))
            set_balance(shop_id, target, amt)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "✅ Updated.", reply_markup=kb_owner_menu(shop_id))

    # Super Admin typed flows
    if shop_id == get_main_shop_id() and is_super_admin(uid):
        if flow == "sa_edit_panel_text":
            set_panel_offer_text(text)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "✅ Updated Get Own Panel text.", reply_markup=kb_sa_menu())

        if flow == "sa_suspend_reason":
            sid = int(ctx.user_data.get("target_shop", 0))
            set_shop_suspension(sid, True, text)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, f"⛔ Shop #{sid} suspended.", reply_markup=kb_sa_menu())

        if flow == "sa_ban_reason":
            target = int(ctx.user_data.get("target_owner", 0))
            # delete their shop (if any)
            sid = get_shop_by_owner(target)
            if sid and sid != get_main_shop_id():
                delete_shop_hard(sid)
            set_owner_ban(target, True, text)
            set_owner_restrict(target, None, None)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, f"🚫 Owner {target} banned permanently and shop deleted.", reply_markup=kb_sa_menu())

        if flow == "sa_restrict_custom_days":
            days = int(text.strip()) if text.strip().isdigit() else 0
            if days <= 0:
                return await send_clean(update, ctx, "Type a valid number of days (example 14).", reply_markup=kb_sa_menu())
            until = (now_utc() + datetime.timedelta(days=days)).isoformat(timespec="seconds")
            ctx.user_data["flow"] = "sa_restrict_reason"
            ctx.user_data["restrict_until"] = until
            return await send_clean(update, ctx, f"Type RESTRICTION reason (until {until}):", reply_markup=kb_sa_menu())

        if flow == "sa_restrict_reason":
            target = int(ctx.user_data.get("target_owner", 0))
            until = ctx.user_data.get("restrict_until")
            # delete their shop (if any)
            sid = get_shop_by_owner(target)
            if sid and sid != get_main_shop_id():
                delete_shop_hard(sid)
            set_owner_restrict(target, until, text)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, f"🔒 Owner {target} restricted until {until} and shop deleted.", reply_markup=kb_sa_menu())

    # Normal text => support
    return await forward_support(update, ctx, shop_id)


# ===================== PHOTO HANDLER (deposit) =====================
async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    shop_id = get_active_shop_id(ctx)
    ensure_shop_user(shop_id, uid)

    if ctx.user_data.get("flow") != "dep_wait_photo":
        return

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
    await ctx.bot.send_photo(
        chat_id=owner_id,
        photo=file_id,
        caption=f"🏪 Shop #{shop_id}\n💳 Deposit #{dep_id}\nUser: {uid}\nAmount: {money(amt)}\nCaption: {caption or '-'}"
    )


# ===================== BOOT =====================
async def post_init(app):
    init_db()

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    if SUPER_ADMIN_ID == 0:
        raise RuntimeError("ADMIN_ID missing")
    if not USDT_TRC20_ADDRESS:
        raise RuntimeError("USDT_TRC20_ADDRESS missing")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & filters.REPLY, owner_reply_by_reply))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_text))

    app.run_polling()

if __name__ == "__main__":
    main()
