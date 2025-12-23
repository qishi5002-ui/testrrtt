# ============================================================
# AutoPanel Telegram Bot (FULL SYSTEM) — PART 1 / 3
# Paste this FIRST into main.py (then paste Part 2, then Part 3)
# ============================================================

import os
import sqlite3
import time
import logging
from typing import Optional, Tuple, List, Dict, Any, Set

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ----------------------------
# CONFIG (Railway ENV)
# ----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPER_ADMIN_ID = int((os.getenv("SUPER_ADMIN_ID", "0") or "0").strip() or "0")

_admins_raw = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS: Set[int] = set()
if _admins_raw:
    for x in _admins_raw.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_IDS.add(int(x))

STORE_NAME = os.getenv("STORE_NAME", "AutoPanel").strip()
CURRENCY = os.getenv("CURRENCY", "USDT").strip()

# main shop wallet address (string; NOT forced TRC20 wording)
MAIN_WALLET_ADDRESS = os.getenv("USDT_TRC20", "").strip()

SELLER_SUB_PRICE = float((os.getenv("SELLER_SUB_PRICE", "10") or "10").strip() or "10")
SELLER_SUB_DAYS = int((os.getenv("SELLER_SUB_DAYS", "30") or "30").strip() or "30")

DB_FILE = os.getenv("DB_FILE", "store.db").strip()

# ----------------------------
# LOGGING
# ----------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("autopanel")

# ----------------------------
# CONSTANTS
# ----------------------------
WELCOME_PUBLIC = -1          # public welcome for normal users
WELCOME_SELLER_MAIN = -2     # seller-main welcome (no "created by" line)
OWNER_MAIN_SHOP = 0          # owner_id=0 is main shop in catalog

DEFAULT_PUBLIC_WELCOME = (
    "Welcome to AutoPanel\n"
    "Get your 24/7 Store Panel Here !!\n\n"
    "Bot created by @RekkoOwn"
)
DEFAULT_SELLER_MAIN_WELCOME = (
    "Welcome to AutoPanel\n"
    "Get your 24/7 Store Panel Here !!"
)
DEFAULT_SELLER_SHOP_WELCOME = "Welcome to my shop!"

DEFAULT_MAIN_WALLET_MSG = "Send payment to the address below, then request deposit with photo proof."
DEFAULT_SELLER_WALLET_MSG = "Send payment to my address below, then request deposit with photo proof."

# Sellers are not allowed to sell anything that looks like subscription/admin/seller
RESERVED_WORDS = [
    "seller", "become seller", "subscription", "subscribe",
    "reseller", "admin", "vip seller", "seller plan", "reseller plan",
]

# context keys
CTX_FLOW = "flow"
CTX_LAST_UI_MSG_ID = "last_ui_msg_id"
CTX_SHOP_OWNER = "shop_owner"               # 0 main shop, else seller_id
CTX_SUPPORT_DRAFT = "support_draft"
CTX_TICKET_REPLY_DRAFT = "ticket_reply_draft"
CTX_DEPOSIT = "deposit_flow"
CTX_EDIT_WELCOME = "edit_welcome"
CTX_EDIT_WALLETMSG = "edit_walletmsg"
CTX_USER_SEARCH = "user_search"
CTX_SELLER_SEARCH = "seller_search"
CTX_ADMIN_TARGET_USER = "admin_target_user"
CTX_ADMIN_TARGET_SELLER = "admin_target_seller"

# ----------------------------
# BASIC HELPERS
# ----------------------------
def now_ts() -> int:
    return int(time.time())

def is_superadmin(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID and SUPER_ADMIN_ID > 0

def is_admin(uid: int) -> bool:
    return is_superadmin(uid) or (uid in ADMIN_IDS)

def fmt_money(x: float) -> str:
    return f"{x:.2f} {CURRENCY}"

def days_left(until_ts: int) -> int:
    if until_ts <= now_ts():
        return 0
    return int((until_ts - now_ts()) / 86400)

def contains_reserved_words(name: str) -> bool:
    t = (name or "").lower()
    return any(w in t for w in RESERVED_WORDS)

async def safe_delete_message(msg: Optional[Message]):
    if not msg:
        return
    try:
        await msg.delete()
    except Exception:
        pass

async def safe_edit_or_send(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
):
    """
    Prefer editing callback message; otherwise send a new message.
    Store last UI message id for cleanup if needed.
    """
    q = update.callback_query
    if q and q.message:
        try:
            await q.edit_message_text(text, reply_markup=reply_markup)
            context.user_data[CTX_LAST_UI_MSG_ID] = q.message.message_id
            return
        except Exception:
            pass

    m = await context.bot.send_message(chat_id=update.effective_user.id, text=text, reply_markup=reply_markup)
    context.user_data[CTX_LAST_UI_MSG_ID] = m.message_id

async def send_media_or_text(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    media_type: str,
    file_id: str,
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
):
    media_type = (media_type or "").strip().lower()
    file_id = (file_id or "").strip()
    caption = (caption or "").strip()

    if media_type == "photo" and file_id:
        return await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption, reply_markup=reply_markup)
    if media_type == "video" and file_id:
        return await context.bot.send_video(chat_id=chat_id, video=file_id, caption=caption, reply_markup=reply_markup)
    return await context.bot.send_message(chat_id=chat_id, text=caption, reply_markup=reply_markup)

def must_have_config() -> Optional[str]:
    if not BOT_TOKEN:
        return "Missing BOT_TOKEN"
    if SUPER_ADMIN_ID <= 0:
        return "Missing SUPER_ADMIN_ID"
    if not MAIN_WALLET_ADDRESS:
        return "Missing USDT_TRC20 (Main wallet address)"
    return None

# ----------------------------
# DATABASE
# ----------------------------
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def db_init():
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA foreign_keys=ON;")

    # users
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        balance REAL NOT NULL DEFAULT 0,
        created_ts INTEGER NOT NULL,
        last_support_target INTEGER NOT NULL DEFAULT 0
    );
    """)

    # sellers
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sellers (
        seller_id INTEGER PRIMARY KEY,
        wallet_address TEXT DEFAULT '',
        sub_until_ts INTEGER NOT NULL DEFAULT 0,
        restricted_until_ts INTEGER NOT NULL DEFAULT 0,
        banned INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(seller_id) REFERENCES users(user_id) ON DELETE CASCADE
    );
    """)

    # welcome messages (public, seller-main, seller-shop)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS welcome_messages (
        owner_id INTEGER PRIMARY KEY,
        media_type TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        caption TEXT DEFAULT ''
    );
    """)

    # wallet messages (editable message text, not chain-specific)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS wallet_messages (
        owner_id INTEGER PRIMARY KEY,
        text TEXT NOT NULL DEFAULT ''
    );
    """)

    # catalog (category -> cocategory -> products)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        category_id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        media_type TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        UNIQUE(owner_id, name)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cocategories (
        cocategory_id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        category_name TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        media_type TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        UNIQUE(owner_id, category_name, name)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        category_name TEXT NOT NULL,
        cocategory_name TEXT NOT NULL,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        description TEXT DEFAULT '',
        media_type TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        delivery_key TEXT DEFAULT '',
        delivery_link TEXT DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1
    );
    """)

    # transactions (history)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        actor_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        amount REAL NOT NULL,
        balance_after REAL NOT NULL,
        note TEXT DEFAULT '',
        created_ts INTEGER NOT NULL
    );
    """)

    # deposit requests (with photo proof)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposit_requests (
        dep_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        proof_file_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_ts INTEGER NOT NULL
    );
    """)

    # support tickets
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_id INTEGER NOT NULL,
        to_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        created_ts INTEGER NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ticket_messages (
        msg_id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_ts INTEGER NOT NULL,
        FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
    );
    """)

    conn.commit()

# ----------------------------
# DB: users / sellers
# ----------------------------
def ensure_user(uid: int, username: str):
    username = (username or "").strip()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO users(user_id, username, balance, created_ts, last_support_target) VALUES(?,?,?,?,?)",
            (uid, username, 0.0, now_ts(), 0),
        )
        conn.commit()
    else:
        cur.execute("UPDATE users SET username=? WHERE user_id=?", (username, uid))
        conn.commit()

def get_user(uid: int) -> Optional[sqlite3.Row]:
    cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    return cur.fetchone()

def list_users_recent(limit: int = 25) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT user_id, username, balance, created_ts
        FROM users
        ORDER BY created_ts DESC
        LIMIT ?
    """, (limit,))
    return cur.fetchall()

def search_users_by_username(prefix: str, limit: int = 25) -> List[sqlite3.Row]:
    p = (prefix or "").lstrip("@").strip().lower()
    cur.execute("""
        SELECT user_id, username, balance, created_ts
        FROM users
        WHERE lower(username) LIKE ?
        ORDER BY created_ts DESC
        LIMIT ?
    """, (p + "%", limit))
    return cur.fetchall()

def ensure_seller(uid: int):
    cur.execute("INSERT OR IGNORE INTO sellers(seller_id) VALUES(?)", (uid,))
    conn.commit()

def get_seller(uid: int) -> Optional[sqlite3.Row]:
    cur.execute("SELECT * FROM sellers WHERE seller_id=?", (uid,))
    return cur.fetchone()

def set_seller_wallet(uid: int, wallet_address: str):
    ensure_seller(uid)
    cur.execute("UPDATE sellers SET wallet_address=? WHERE seller_id=?", ((wallet_address or "").strip(), uid))
    conn.commit()

def is_active_seller(uid: int) -> bool:
    s = get_seller(uid)
    return bool(s and int(s["sub_until_ts"]) > now_ts() and int(s["banned"]) == 0 and int(s["restricted_until_ts"]) <= now_ts())

def seller_shop_state(seller_id: int) -> Tuple[bool, str]:
    s = get_seller(seller_id)
    if not s:
        return False, "Seller not found."
    if int(s["banned"]) == 1:
        return False, "🚫 This seller shop is banned."
    if int(s["restricted_until_ts"]) > now_ts():
        return False, "⏳ This seller shop is restricted right now."
    if int(s["sub_until_ts"]) <= now_ts():
        return False, "❗ This seller subscription is expired."
    return True, "OK"

def add_seller_subscription(uid: int, add_days: int) -> int:
    ensure_seller(uid)
    s = get_seller(uid)
    now = now_ts()
    current = int(s["sub_until_ts"])
    base = current if current > now else now
    new_until = base + int(add_days) * 86400
    cur.execute("UPDATE sellers SET sub_until_ts=? WHERE seller_id=?", (new_until, uid))
    conn.commit()
    return new_until

def set_seller_restrict(uid: int, add_days: int) -> int:
    ensure_seller(uid)
    s = get_seller(uid)
    now = now_ts()
    current = int(s["restricted_until_ts"])
    base = current if current > now else now
    new_until = base + int(add_days) * 86400
    cur.execute("UPDATE sellers SET restricted_until_ts=? WHERE seller_id=?", (new_until, uid))
    conn.commit()
    return new_until

def set_seller_ban(uid: int, banned: bool):
    ensure_seller(uid)
    cur.execute("UPDATE sellers SET banned=? WHERE seller_id=?", (1 if banned else 0, uid))
    conn.commit()

def list_active_sellers_recent(limit: int = 25) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT u.user_id, u.username, u.balance, s.sub_until_ts, s.restricted_until_ts, s.banned
        FROM sellers s
        JOIN users u ON u.user_id = s.seller_id
        WHERE s.sub_until_ts > ?
        ORDER BY s.sub_until_ts DESC
        LIMIT ?
    """, (now_ts(), limit))
    return cur.fetchall()

def search_active_sellers_by_username(prefix: str, limit: int = 25) -> List[sqlite3.Row]:
    p = (prefix or "").lstrip("@").strip().lower()
    cur.execute("""
        SELECT u.user_id, u.username, u.balance, s.sub_until_ts, s.restricted_until_ts, s.banned
        FROM sellers s
        JOIN users u ON u.user_id = s.seller_id
        WHERE s.sub_until_ts > ? AND lower(u.username) LIKE ?
        ORDER BY s.sub_until_ts DESC
        LIMIT ?
    """, (now_ts(), p + "%", limit))
    return cur.fetchall()

# ----------------------------
# DB: balance / history
# ----------------------------
def add_balance(uid: int, delta: float, actor_id: int, tx_type: str, note: str = "") -> float:
    u = get_user(uid)
    if not u:
        raise ValueError("User not found")
    new_bal = float(u["balance"]) + float(delta)
    if new_bal < 0:
        raise ValueError("Insufficient balance")
    cur.execute("UPDATE users SET balance=? WHERE user_id=?", (new_bal, uid))
    cur.execute(
        "INSERT INTO transactions(user_id, actor_id, type, amount, balance_after, note, created_ts) VALUES(?,?,?,?,?,?,?)",
        (uid, actor_id, tx_type, float(delta), new_bal, (note or "").strip(), now_ts()),
    )
    conn.commit()
    return new_bal

def get_history(uid: int, limit: int = 10) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT type, amount, balance_after, note, created_ts
        FROM transactions
        WHERE user_id=?
        ORDER BY tx_id DESC
        LIMIT ?
    """, (uid, limit))
    return cur.fetchall()

# ----------------------------
# DB: welcome messages
# ----------------------------
def get_welcome(owner_id: int) -> sqlite3.Row:
    cur.execute("SELECT * FROM welcome_messages WHERE owner_id=?", (owner_id,))
    r = cur.fetchone()
    if r:
        return r

    if owner_id == WELCOME_PUBLIC:
        cap = DEFAULT_PUBLIC_WELCOME
    elif owner_id == WELCOME_SELLER_MAIN:
        cap = DEFAULT_SELLER_MAIN_WELCOME
    elif owner_id == OWNER_MAIN_SHOP:
        cap = DEFAULT_PUBLIC_WELCOME
    else:
        cap = DEFAULT_SELLER_SHOP_WELCOME

    cur.execute(
        "INSERT OR IGNORE INTO welcome_messages(owner_id, media_type, file_id, caption) VALUES(?,?,?,?)",
        (owner_id, "", "", cap),
    )
    conn.commit()
    cur.execute("SELECT * FROM welcome_messages WHERE owner_id=?", (owner_id,))
    return cur.fetchone()

def set_welcome(owner_id: int, media_type: str, file_id: str, caption: str):
    cur.execute("""
        INSERT INTO welcome_messages(owner_id, media_type, file_id, caption)
        VALUES(?,?,?,?)
        ON CONFLICT(owner_id) DO UPDATE SET
            media_type=excluded.media_type,
            file_id=excluded.file_id,
            caption=excluded.caption
    """, (owner_id, (media_type or "").strip(), (file_id or "").strip(), (caption or "").strip()))
    conn.commit()

# ----------------------------
# DB: wallet messages
# ----------------------------
def get_wallet_message(owner_id: int) -> str:
    cur.execute("SELECT text FROM wallet_messages WHERE owner_id=?", (owner_id,))
    r = cur.fetchone()
    if r and (r["text"] or "").strip():
        return (r["text"] or "").strip()
    return DEFAULT_MAIN_WALLET_MSG if owner_id == OWNER_MAIN_SHOP else DEFAULT_SELLER_WALLET_MSG

def set_wallet_message(owner_id: int, text: str):
    t = (text or "").strip()
    cur.execute("""
        INSERT INTO wallet_messages(owner_id, text)
        VALUES(?,?)
        ON CONFLICT(owner_id) DO UPDATE SET
            text=excluded.text
    """, (owner_id, t))
    conn.commit()

# ----------------------------
# Menu Keyboards (base; panels added in Part 3)
# ----------------------------
def kb_main_menu(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🛒 Products", callback_data="U_PRODUCTS"),
         InlineKeyboardButton("💰 Wallet", callback_data="U_WALLET")],
        [InlineKeyboardButton("📜 History", callback_data="U_HISTORY"),
         InlineKeyboardButton("🆘 Support", callback_data="U_SUPPORT")],
        [InlineKeyboardButton("⭐ Become Seller", callback_data="U_BECOME_SELLER"),
         InlineKeyboardButton("🏪 Seller Panel", callback_data="S_PANEL")],
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="A_PANEL")])
    if is_superadmin(uid):
        rows.append([InlineKeyboardButton("👑 Super Admin Panel", callback_data="SA_PANEL")])
    return InlineKeyboardMarkup(rows)

def kb_only_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]])


# ============================================================
# AutoPanel Telegram Bot (FULL SYSTEM) — PART 2 / 3
# Paste this DIRECTLY under Part 1 in the SAME main.py
# ============================================================

# ----------------------------
# DB: catalog helpers
# ----------------------------
def upsert_category(owner_id: int, name: str, description: str = "", media_type: str = "", file_id: str = ""):
    name = (name or "").strip()
    if not name:
        raise ValueError("Category name required")
    cur.execute("""
        INSERT INTO categories(owner_id, name, description, media_type, file_id, active)
        VALUES(?,?,?,?,?,1)
        ON CONFLICT(owner_id, name) DO UPDATE SET
            description=excluded.description,
            media_type=excluded.media_type,
            file_id=excluded.file_id,
            active=1
    """, (owner_id, name, (description or "").strip(), (media_type or "").strip(), (file_id or "").strip()))
    conn.commit()

def upsert_cocategory(owner_id: int, category_name: str, name: str, description: str = "", media_type: str = "", file_id: str = ""):
    category_name = (category_name or "").strip()
    name = (name or "").strip()
    if not category_name or not name:
        raise ValueError("Co-category requires category + name")
    cur.execute("""
        INSERT INTO cocategories(owner_id, category_name, name, description, media_type, file_id, active)
        VALUES(?,?,?,?,?,?,1)
        ON CONFLICT(owner_id, category_name, name) DO UPDATE SET
            description=excluded.description,
            media_type=excluded.media_type,
            file_id=excluded.file_id,
            active=1
    """, (owner_id, category_name, name, (description or "").strip(), (media_type or "").strip(), (file_id or "").strip()))
    conn.commit()

def add_product(owner_id: int, category_name: str, cocategory_name: str, name: str, price: float,
                description: str = "", media_type: str = "", file_id: str = "") -> int:
    category_name = (category_name or "").strip()
    cocategory_name = (cocategory_name or "").strip()
    name = (name or "").strip()
    if not category_name or not cocategory_name or not name:
        raise ValueError("Product requires category, co-category, name")
    if owner_id != OWNER_MAIN_SHOP and contains_reserved_words(name):
        raise ValueError("This product name is not allowed for sellers.")
    if float(price) <= 0:
        raise ValueError("Price must be > 0")

    # ensure chain exists
    upsert_category(owner_id, category_name)
    upsert_cocategory(owner_id, category_name, cocategory_name)

    cur.execute("""
        INSERT INTO products(owner_id, category_name, cocategory_name, name, price,
                             description, media_type, file_id, delivery_key, delivery_link, active)
        VALUES(?,?,?,?,?,?,?,?,?,?,1)
    """, (owner_id, category_name, cocategory_name, name, float(price),
          (description or "").strip(), (media_type or "").strip(), (file_id or "").strip(),
          "", ""))
    conn.commit()
    return int(cur.lastrowid)

def set_product_delivery(owner_id: int, product_id: int, delivery_key: str, delivery_link: str):
    cur.execute("""
        UPDATE products
        SET delivery_key=?, delivery_link=?
        WHERE owner_id=? AND product_id=?
    """, ((delivery_key or "").strip(), (delivery_link or "").strip(), owner_id, product_id))
    conn.commit()

def deactivate_product(owner_id: int, product_id: int):
    cur.execute("UPDATE products SET active=0 WHERE owner_id=? AND product_id=?", (owner_id, product_id))
    conn.commit()

def list_categories(owner_id: int) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT name, description, media_type, file_id
        FROM categories
        WHERE owner_id=? AND active=1
        ORDER BY name
    """, (owner_id,))
    return cur.fetchall()

def list_cocategories(owner_id: int, category_name: str) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT name, description, media_type, file_id
        FROM cocategories
        WHERE owner_id=? AND category_name=? AND active=1
        ORDER BY name
    """, (owner_id, category_name))
    return cur.fetchall()

def list_products(owner_id: int, category_name: str, cocategory_name: str) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT product_id, name, price
        FROM products
        WHERE owner_id=? AND category_name=? AND cocategory_name=? AND active=1
        ORDER BY name
    """, (owner_id, category_name, cocategory_name))
    return cur.fetchall()

def get_product(owner_id: int, product_id: int) -> Optional[sqlite3.Row]:
    cur.execute("""
        SELECT *
        FROM products
        WHERE owner_id=? AND product_id=? AND active=1
    """, (owner_id, product_id))
    return cur.fetchone()

# ----------------------------
# History formatting (nice)
# ----------------------------
def format_history(uid: int) -> str:
    rows = get_history(uid, 12)
    if not rows:
        return "📜 History\n\nNo transactions yet."
    out = ["📜 History (latest 12)\n"]
    for r in rows:
        t = (r["type"] or "").strip()
        amt = float(r["amount"])
        bal = float(r["balance_after"])
        note = (r["note"] or "").strip()

        if t == "deposit_ok":
            out.append(f"✅ Deposited: +{amt:.2f} {CURRENCY} to Balance\nTotal Balance: {bal:.2f} {CURRENCY}\n")
        elif t == "deposit_reject":
            out.append(f"❌ Deposit Rejected: {note}\nTotal Balance: {bal:.2f} {CURRENCY}\n")
        elif t == "purchase":
            out.append(f"🛒 Purchased: {note}\nPaid: {abs(amt):.2f} {CURRENCY}\nTotal Balance: {bal:.2f} {CURRENCY}\n")
        elif t == "sale":
            out.append(f"💸 Sold: {note}\nReceived: +{amt:.2f} {CURRENCY}\nTotal Balance: {bal:.2f} {CURRENCY}\n")
        elif t == "sub":
            out.append(f"⭐ Seller Subscription\nPaid: {abs(amt):.2f} {CURRENCY}\nTotal Balance: {bal:.2f} {CURRENCY}\n")
        elif t in ("admin_edit", "seller_edit", "sa_edit"):
            sign = "+" if amt >= 0 else "-"
            out.append(f"🧾 Balance Update: {sign}{abs(amt):.2f} {CURRENCY}\nTotal Balance: {bal:.2f} {CURRENCY}\n")
        else:
            sign = "+" if amt >= 0 else "-"
            out.append(f"{t}: {sign}{abs(amt):.2f} {CURRENCY}\nTotal Balance: {bal:.2f} {CURRENCY}\n")

    return "\n".join(out).strip()

# ----------------------------
# Shop UI
# ----------------------------
def kb_shop_home(owner_id: int) -> InlineKeyboardMarkup:
    # owner_id: 0 main shop, else seller shop
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Categories", callback_data=f"SHOP_CATS:{owner_id}")],
        [InlineKeyboardButton("🆘 Support", callback_data="U_SUPPORT")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

def kb_shop_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏬 Main Shop", callback_data="OPEN_SHOP:0"),
         InlineKeyboardButton("🏪 Seller Shops", callback_data="SELLER_SHOPS")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

def kb_seller_shops_list(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    rows.append([InlineKeyboardButton("🔎 Search Seller Username", callback_data="SELLER_SEARCH")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="U_PRODUCTS")])
    return InlineKeyboardMarkup(rows)

async def open_shop(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int):
    uid = update.effective_user.id
    context.user_data[CTX_SHOP_OWNER] = owner_id

    if owner_id != OWNER_MAIN_SHOP:
        ok, msg = seller_shop_state(owner_id)
        if not ok:
            await safe_edit_or_send(update, context, msg, reply_markup=kb_only_menu())
            return

    # set support target
    cur.execute("UPDATE users SET last_support_target=? WHERE user_id=?", (owner_id, uid))
    conn.commit()

    # choose welcome message source:
    # - main shop uses public welcome (WELCOME_PUBLIC) OR seller-main for active sellers when they are in main menu
    # - seller shop uses seller_id welcome
    if owner_id == OWNER_MAIN_SHOP:
        w = get_welcome(WELCOME_PUBLIC)
    else:
        w = get_welcome(owner_id)

    msg = await send_media_or_text(
        chat_id=uid,
        context=context,
        media_type=w["media_type"],
        file_id=w["file_id"],
        caption=w["caption"] or (DEFAULT_PUBLIC_WELCOME if owner_id == 0 else DEFAULT_SELLER_SHOP_WELCOME),
        reply_markup=kb_shop_home(owner_id),
    )
    context.user_data[CTX_LAST_UI_MSG_ID] = msg.message_id

# ----------------------------
# Product browsing UI
# ----------------------------
def kb_categories(owner_id: int, cats: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for c in cats[:30]:
        rows.append([InlineKeyboardButton(c["name"], callback_data=f"CATEGORY:{owner_id}:{c['name']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"OPEN_SHOP:{owner_id}")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)

def kb_cocategories(owner_id: int, category_name: str, cocats: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for cc in cocats[:30]:
        rows.append([InlineKeyboardButton(cc["name"], callback_data=f"COCAT:{owner_id}:{category_name}:{cc['name']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"SHOP_CATS:{owner_id}")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)

def kb_products(owner_id: int, category_name: str, cocategory_name: str, products: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for p in products[:30]:
        label = f"{p['name']} — {fmt_money(float(p['price']))}"
        rows.append([InlineKeyboardButton(label, callback_data=f"VIEWPROD:{owner_id}:{p['product_id']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"CATEGORY:{owner_id}:{category_name}")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)

def kb_product_view(owner_id: int, category_name: str, cocategory_name: str, product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Buy", callback_data=f"BUY:{owner_id}:{product_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"COCAT:{owner_id}:{category_name}:{cocategory_name}")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

def kb_delivery_buttons(owner_id: int, product_id: int, has_link: bool) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if has_link:
        rows.append([InlineKeyboardButton("📁 Get File", callback_data=f"GETFILE:{owner_id}:{product_id}")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)

# ----------------------------
# Buying logic
# ----------------------------
async def do_buy(uid: int, context: ContextTypes.DEFAULT_TYPE, owner_id: int, product_id: int):
    p = get_product(owner_id, product_id)
    if not p:
        await context.bot.send_message(uid, "❌ Product not found.", reply_markup=kb_only_menu())
        return

    price = float(p["price"])
    u = get_user(uid)
    if float(u["balance"]) < price:
        await context.bot.send_message(
            uid,
            f"❌ Insufficient balance.\nPrice: {fmt_money(price)}\nYour balance: {fmt_money(float(u['balance']))}",
            reply_markup=kb_only_menu(),
        )
        return

    # deduct buyer
    add_balance(uid, -price, uid, "purchase", p["name"])

    # credit seller if seller shop
    if owner_id != OWNER_MAIN_SHOP:
        add_balance(owner_id, +price, uid, "sale", f"{p['name']} (buyer @{u['username'] or uid})")
        # keep support target to seller
        cur.execute("UPDATE users SET last_support_target=? WHERE user_id=?", (owner_id, uid))
        conn.commit()

    delivery_key = (p["delivery_key"] or "").strip()
    delivery_link = (p["delivery_link"] or "").strip()

    text = (
        f"✅ Purchase Successful!\n\n"
        f"Product: {p['name']}\n"
        f"Paid: {fmt_money(price)}\n\n"
        f"🔑 Key:\n{delivery_key if delivery_key else '(Owner has not set a key yet.)'}"
    )
    await context.bot.send_message(uid, text, reply_markup=kb_delivery_buttons(owner_id, product_id, bool(delivery_link)))

async def do_getfile(uid: int, context: ContextTypes.DEFAULT_TYPE, owner_id: int, product_id: int):
    p = get_product(owner_id, product_id)
    if not p:
        await context.bot.send_message(uid, "❌ Product not found.", reply_markup=kb_only_menu())
        return
    link = (p["delivery_link"] or "").strip()
    if not link:
        await context.bot.send_message(uid, "❌ No file link set by owner.", reply_markup=kb_only_menu())
        return
    # Hide link behind button; this message reveals it only on click
    await context.bot.send_message(uid, f"📁 File Link:\n{link}", reply_markup=kb_only_menu())

# ----------------------------
# Seller deep link share
# ----------------------------
async def send_share_my_shop(uid: int, context: ContextTypes.DEFAULT_TYPE):
    me = await context.bot.get_me()
    if not me.username:
        await context.bot.send_message(uid, "❌ Bot username missing. Set a username in BotFather.", reply_markup=kb_only_menu())
        return
    link = f"https://t.me/{me.username}?start=shop_{uid}"
    msg = (
        "📣 Share My Shop\n\n"
        "Send this link to your users:\n"
        f"{link}\n\n"
        "🏪 24/7 Automated Store"
    )
    await context.bot.send_message(uid, msg, reply_markup=kb_only_menu())

# ----------------------------
# Seller Management Keyboards (used in Part 3 handlers)
# ----------------------------
def kb_seller_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Manage Products", callback_data="S_MGMT"),
         InlineKeyboardButton("🏷 Manage Categories", callback_data="S_CATS")],
        [InlineKeyboardButton("💳 Set Wallet Address", callback_data="S_SET_WALLET"),
         InlineKeyboardButton("📝 Edit Wallet Message", callback_data="S_EDIT_WALLETMSG")],
        [InlineKeyboardButton("🖼 Edit Shop Welcome", callback_data="S_EDIT_WELCOME"),
         InlineKeyboardButton("📣 Share My Shop", callback_data="S_SHARE")],
        [InlineKeyboardButton("🆘 Support Inbox", callback_data="S_TICKETS"),
         InlineKeyboardButton("🏬 Go to Main Shop", callback_data="OPEN_SHOP:0")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

def kb_seller_manage_store(owner_id: int) -> InlineKeyboardMarkup:
    # owner_id is seller_id here
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Category", callback_data=f"CAT_ADD:{owner_id}"),
         InlineKeyboardButton("➕ Add Co-Category", callback_data=f"COCAT_ADD:{owner_id}")],
        [InlineKeyboardButton("➕ Add Product", callback_data=f"PROD_ADD:{owner_id}"),
         InlineKeyboardButton("🗑 Remove Product", callback_data=f"PROD_DEL:{owner_id}")],
        [InlineKeyboardButton("✉️ Set Delivery (Key/Link)", callback_data=f"PROD_DELIVERY:{owner_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

# ----------------------------
# Wallet screen helpers (used in Part 3 handler)
# ----------------------------
def resolve_deposit_context(uid: int) -> Tuple[int, str, str, bool]:
    """
    Returns:
      wallet_owner_id, wallet_address, wallet_message, show_return_to_main
    Rules:
      - active seller: show seller wallet address if set, else main wallet.
      - seller should have Return to Main Shop button.
      - non-seller: show main wallet.
    """
    if is_active_seller(uid):
        s = get_seller(uid)
        w = (s["wallet_address"] or "").strip() if s else ""
        if w:
            return uid, w, get_wallet_message(uid), True
        return OWNER_MAIN_SHOP, MAIN_WALLET_ADDRESS, get_wallet_message(OWNER_MAIN_SHOP), True
    return OWNER_MAIN_SHOP, MAIN_WALLET_ADDRESS, get_wallet_message(OWNER_MAIN_SHOP), False

def kb_wallet(show_return: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("➕ Request Deposit", callback_data="DEP_START")],
    ]
    if show_return:
        rows.append([InlineKeyboardButton("🏬 Return to Main Shop", callback_data="RETURN_MAIN_SHOP")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)


# ============================================================
# AutoPanel Telegram Bot (FULL SYSTEM) — PART 3 / 3
# Handlers, Support, Deposits, Admin & Super Admin Panels
# ============================================================

# ----------------------------
# Support system
# ----------------------------
def get_open_ticket(from_id: int, to_id: int) -> Optional[int]:
    cur.execute("""
        SELECT ticket_id FROM tickets
        WHERE from_id=? AND to_id=? AND status='open'
        ORDER BY ticket_id DESC LIMIT 1
    """, (from_id, to_id))
    r = cur.fetchone()
    return int(r["ticket_id"]) if r else None

def create_ticket(from_id: int, to_id: int) -> int:
    cur.execute(
        "INSERT INTO tickets(from_id,to_id,status,created_ts) VALUES(?,?,?,?)",
        (from_id, to_id, "open", now_ts())
    )
    conn.commit()
    return int(cur.lastrowid)

def add_ticket_message(ticket_id: int, sender_id: int, message: str):
    cur.execute(
        "INSERT INTO ticket_messages(ticket_id,sender_id,message,created_ts) VALUES(?,?,?,?)",
        (ticket_id, sender_id, (message or "").strip(), now_ts())
    )
    conn.commit()

def list_tickets_for(uid: int) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT t.ticket_id, t.from_id, u.username, t.created_ts
        FROM tickets t
        JOIN users u ON u.user_id=t.from_id
        WHERE t.to_id=? AND t.status='open'
        ORDER BY t.created_ts DESC
    """, (uid,))
    return cur.fetchall()

# ----------------------------
# Deposit approval (admin)
# ----------------------------
def list_pending_deposits() -> List[sqlite3.Row]:
    cur.execute("""
        SELECT d.dep_id, d.user_id, u.username, d.amount, d.proof_file_id, d.created_ts
        FROM deposit_requests d
        JOIN users u ON u.user_id=d.user_id
        WHERE d.status='pending'
        ORDER BY d.created_ts
    """)
    return cur.fetchall()

def approve_deposit(dep_id: int, admin_id: int):
    cur.execute("SELECT * FROM deposit_requests WHERE dep_id=? AND status='pending'", (dep_id,))
    d = cur.fetchone()
    if not d:
        return False
    add_balance(d["user_id"], d["amount"], admin_id, "deposit_ok", "Deposit approved")
    cur.execute("UPDATE deposit_requests SET status='approved' WHERE dep_id=?", (dep_id,))
    conn.commit()
    return True

def reject_deposit(dep_id: int, admin_id: int, reason: str = "Rejected"):
    cur.execute("SELECT * FROM deposit_requests WHERE dep_id=? AND status='pending'", (dep_id,))
    d = cur.fetchone()
    if not d:
        return False
    add_balance(d["user_id"], 0, admin_id, "deposit_reject", reason)
    cur.execute("UPDATE deposit_requests SET status='rejected' WHERE dep_id=?", (dep_id,))
    conn.commit()
    return True

# ----------------------------
# START command
# ----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    err = must_have_config()
    if err:
        await update.message.reply_text(f"❌ Bot config error: {err}")
        return

    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username or "")

    # deep link to seller shop
    if context.args and context.args[0].startswith("shop_"):
        try:
            sid = int(context.args[0].split("_", 1)[1])
            await open_shop(update, context, sid)
            return
        except Exception:
            pass

    owner = WELCOME_SELLER_MAIN if is_active_seller(uid) else WELCOME_PUBLIC
    w = get_welcome(owner)
    msg = await send_media_or_text(
        chat_id=uid,
        context=context,
        media_type=w["media_type"],
        file_id=w["file_id"],
        caption=w["caption"],
        reply_markup=kb_main_menu(uid),
    )
    context.user_data[CTX_LAST_UI_MSG_ID] = msg.message_id

# ----------------------------
# BUTTON HANDLER
# ----------------------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid, q.from_user.username or "")
    data = q.data

    # ----- Global reset -----
    if data == "MAIN_MENU":
        context.user_data.clear()
        await safe_delete_message(q.message)
        await start(update, context)
        return

    # ----- Products -----
    if data == "U_PRODUCTS":
        await safe_edit_or_send(update, context, "Choose a shop:", kb_shop_picker())
        return

    if data.startswith("OPEN_SHOP:"):
        owner_id = int(data.split(":", 1)[1])
        await safe_delete_message(q.message)
        await open_shop(update, context, owner_id)
        return

    if data.startswith("SHOP_CATS:"):
        owner_id = int(data.split(":", 1)[1])
        cats = list_categories(owner_id)
        await safe_edit_or_send(update, context, "📂 Categories", kb_categories(owner_id, cats))
        return

    if data.startswith("CATEGORY:"):
        _, owner_id, cat = data.split(":", 2)
        owner_id = int(owner_id)
        cocats = list_cocategories(owner_id, cat)
        await safe_edit_or_send(update, context, f"📁 {cat}", kb_cocategories(owner_id, cat, cocats))
        return

    if data.startswith("COCAT:"):
        _, owner_id, cat, cocat = data.split(":", 3)
        owner_id = int(owner_id)
        prods = list_products(owner_id, cat, cocat)
        await safe_edit_or_send(update, context, f"🛒 {cocat}", kb_products(owner_id, cat, cocat, prods))
        return

    if data.startswith("VIEWPROD:"):
        _, owner_id, pid = data.split(":", 2)
        owner_id, pid = int(owner_id), int(pid)
        p = get_product(owner_id, pid)
        if not p:
            await safe_edit_or_send(update, context, "❌ Product not found.", kb_only_menu())
            return
        text = (
            f"🛒 {p['name']}\n\n"
            f"Price: {fmt_money(float(p['price']))}\n\n"
            f"{p['description'] or ''}"
        )
        await safe_edit_or_send(update, context, text,
                                kb_product_view(owner_id, p["category_name"], p["cocategory_name"], pid))
        return

    if data.startswith("BUY:"):
        _, owner_id, pid = data.split(":", 2)
        await do_buy(uid, context, int(owner_id), int(pid))
        return

    if data.startswith("GETFILE:"):
        _, owner_id, pid = data.split(":", 2)
        await do_getfile(uid, context, int(owner_id), int(pid))
        return

    # ----- Wallet -----
    if data == "U_WALLET":
        wallet_owner, wallet_addr, wallet_msg, show_return = resolve_deposit_context(uid)
        u = get_user(uid)
        text = (
            f"💰 Wallet\n\n"
            f"Balance: {fmt_money(float(u['balance']))}\n\n"
            f"Deposit Address:\n{wallet_addr}\n\n"
            f"{wallet_msg}"
        )
        await safe_edit_or_send(update, context, text, kb_wallet(show_return))
        return

    if data == "RETURN_MAIN_SHOP":
        await safe_delete_message(q.message)
        await open_shop(update, context, OWNER_MAIN_SHOP)
        return

    # ----- Deposit flow -----
    if data == "DEP_START":
        context.user_data[CTX_DEPOSIT] = {}
        await safe_edit_or_send(update, context, "Enter deposit amount:", kb_only_menu())
        return

    # ----- History -----
    if data == "U_HISTORY":
        await safe_edit_or_send(update, context, format_history(uid), kb_only_menu())
        return

    # ----- Support -----
    if data == "U_SUPPORT":
        context.user_data[CTX_SUPPORT_DRAFT] = ""
        await safe_edit_or_send(
            update,
            context,
            "✉️ Send your message.\nPress DONE when finished.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done", callback_data="SUPPORT_DONE")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
            ]),
        )
        return

    if data == "SUPPORT_DONE":
        text = (context.user_data.get(CTX_SUPPORT_DRAFT) or "").strip()
        if not text:
            await q.answer("Message is empty", show_alert=True)
            return

        target = get_user(uid)["last_support_target"]
        if target == 0:
            target = SUPER_ADMIN_ID

        tid = get_open_ticket(uid, target)
        if not tid:
            tid = create_ticket(uid, target)
        add_ticket_message(tid, uid, text)

        context.user_data.clear()
        await safe_edit_or_send(update, context, "✅ Support message sent.", kb_main_menu(uid))
        return

    # ----- Seller Panel -----
    if data == "S_PANEL":
        ok = is_active_seller(uid)
        if not ok:
            await safe_edit_or_send(update, context, "❗ You are not an active seller.", kb_main_menu(uid))
            return
        await safe_edit_or_send(update, context, "🏪 Seller Panel", kb_seller_panel())
        return

    if data == "S_SHARE":
        await send_share_my_shop(uid, context)
        return

    if data == "S_EDIT_WALLETMSG":
        context.user_data[CTX_EDIT_WALLETMSG] = uid
        await safe_edit_or_send(update, context, "Send new wallet message for your shop:", kb_only_menu())
        return

    # ----- Admin Panel -----
    if data == "A_PANEL":
        if not is_admin(uid):
            await q.answer("Not allowed", show_alert=True)
            return
        rows = [
            [InlineKeyboardButton("💳 Approve Deposits", callback_data="A_DEPOSITS")],
            [InlineKeyboardButton("👥 Users", callback_data="A_USERS")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
        ]
        await safe_edit_or_send(update, context, "🛠 Admin Panel", InlineKeyboardMarkup(rows))
        return

    if data == "A_DEPOSITS":
        deps = list_pending_deposits()
        if not deps:
            await safe_edit_or_send(update, context, "No pending deposits.", kb_only_menu())
            return
        for d in deps[:10]:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"DEP_OK:{d['dep_id']}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"DEP_NO:{d['dep_id']}")],
            ])
            await context.bot.send_photo(
                uid,
                d["proof_file_id"],
                caption=f"User: @{d['username']}\nAmount: {fmt_money(float(d['amount']))}",
                reply_markup=kb,
            )
        return

    if data.startswith("DEP_OK:"):
        approve_deposit(int(data.split(":", 1)[1]), uid)
        await q.answer("Approved")
        return

    if data.startswith("DEP_NO:"):
        reject_deposit(int(data.split(":", 1)[1]), uid)
        await q.answer("Rejected")
        return

# ----------------------------
# MESSAGE HANDLER
# ----------------------------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username or "")
    text = (update.message.text or "").strip()

    # Support draft
    if CTX_SUPPORT_DRAFT in context.user_data:
        context.user_data[CTX_SUPPORT_DRAFT] += text + "\n"
        return

    # Wallet message edit
    if CTX_EDIT_WALLETMSG in context.user_data:
        owner = context.user_data.pop(CTX_EDIT_WALLETMSG)
        set_wallet_message(owner, text)
        await update.message.reply_text("✅ Wallet message updated.", reply_markup=kb_main_menu(uid))
        return

    # Deposit flow
    if CTX_DEPOSIT in context.user_data:
        dep = context.user_data[CTX_DEPOSIT]
        if "amount" not in dep:
            try:
                dep["amount"] = float(text)
                await update.message.reply_text("Now send photo proof.")
            except Exception:
                await update.message.reply_text("Enter a valid amount.")
            return
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            create_deposit(uid, dep["amount"], file_id)
            context.user_data.pop(CTX_DEPOSIT)
            await update.message.reply_text("✅ Deposit request sent.", reply_markup=kb_main_menu(uid))
            return

# ----------------------------
# MAIN
# ----------------------------
def main():
    db_init()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))

    log.info("AutoPanel FULL SYSTEM started")
    app.run_polling()

if __name__ == "__main__":
    main()
