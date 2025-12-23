import os
import sqlite3
import time
import logging
from typing import Optional, List, Tuple, Set

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# ============================================================
# CONFIG (Railway ENV)
# ============================================================
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
MAIN_WALLET_ADDRESS = os.getenv("USDT_TRC20", "").strip()

SELLER_SUB_PRICE = float((os.getenv("SELLER_SUB_PRICE", "10") or "10").strip() or "10")
SELLER_SUB_DAYS = int((os.getenv("SELLER_SUB_DAYS", "30") or "30").strip() or "30")

DB_FILE = os.getenv("DB_FILE", "store.db").strip()

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("autopanel")

# ============================================================
# CONSTANTS / CONTEXT
# ============================================================
OWNER_MAIN = 0
WELCOME_PUBLIC = -1
WELCOME_SELLER_MAIN = -2

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

RESERVED_WORDS = [
    "seller", "become seller", "subscription", "subscribe",
    "reseller", "admin", "vip seller", "seller plan", "reseller plan",
]

CTX_SHOP_OWNER = "shop_owner"     # current shop context: 0 main, else seller_id
CTX_FLOW = "flow"

# flows
FLOW_SUPPORT_DRAFT = "flow_support_draft"
FLOW_SUPPORT_REPLY = "flow_support_reply"
FLOW_DEPOSIT = "flow_deposit"
FLOW_EDIT_WELCOME = "flow_edit_welcome"
FLOW_EDIT_WALLETMSG = "flow_edit_walletmsg"
FLOW_SET_WALLET_ADDR = "flow_set_wallet_addr"

FLOW_USERS_SEARCH = "flow_users_search"
FLOW_SELLERS_SEARCH = "flow_sellers_search"

FLOW_CAT_ADD = "flow_cat_add"
FLOW_COCAT_NAME = "flow_cocat_name"
FLOW_PROD_DETAILS = "flow_prod_details"
FLOW_DELIVERY_TEXT = "flow_delivery_text"

# ============================================================
# HELPERS
# ============================================================
def now_ts() -> int:
    return int(time.time())

def must_have_config() -> Optional[str]:
    if not BOT_TOKEN:
        return "Missing BOT_TOKEN"
    if SUPER_ADMIN_ID <= 0:
        return "Missing SUPER_ADMIN_ID"
    if not MAIN_WALLET_ADDRESS:
        return "Missing USDT_TRC20 (main wallet address)"
    return None

def is_superadmin(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID and SUPER_ADMIN_ID > 0

def is_admin(uid: int) -> bool:
    return is_superadmin(uid) or uid in ADMIN_IDS

def fmt_money(x: float) -> str:
    return f"{x:.2f} {CURRENCY}"

def days_left(until_ts: int) -> int:
    if until_ts <= now_ts():
        return 0
    return int((until_ts - now_ts()) / 86400)

def contains_reserved_words(name: str) -> bool:
    t = (name or "").lower()
    return any(w in t for w in RESERVED_WORDS)

async def safe_delete(msg: Optional[Message]):
    if not msg:
        return
    try:
        await msg.delete()
    except Exception:
        pass

def reset_flow(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(CTX_FLOW, None)
    for k in list(context.user_data.keys()):
        if k.startswith("flow_"):
            context.user_data.pop(k, None)

async def send_or_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, kb: Optional[InlineKeyboardMarkup] = None):
    q = update.callback_query
    if q and q.message:
        try:
            await q.edit_message_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await context.bot.send_message(update.effective_user.id, text, reply_markup=kb)

async def send_media_or_text(chat_id: int, context: ContextTypes.DEFAULT_TYPE, media_type: str, file_id: str, caption: str, kb: Optional[InlineKeyboardMarkup] = None):
    media_type = (media_type or "").strip().lower()
    file_id = (file_id or "").strip()
    caption = (caption or "").strip()
    if media_type == "photo" and file_id:
        return await context.bot.send_photo(chat_id, file_id, caption=caption, reply_markup=kb)
    if media_type == "video" and file_id:
        return await context.bot.send_video(chat_id, file_id, caption=caption, reply_markup=kb)
    return await context.bot.send_message(chat_id, caption, reply_markup=kb)

def current_shop_owner(context: ContextTypes.DEFAULT_TYPE) -> int:
    return int(context.user_data.get(CTX_SHOP_OWNER, OWNER_MAIN))

# ============================================================
# DATABASE
# ============================================================
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def db_init():
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA foreign_keys=ON;")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        balance REAL NOT NULL DEFAULT 0,
        created_ts INTEGER NOT NULL,
        last_support_target INTEGER NOT NULL DEFAULT 0
    );
    """)

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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS welcome_messages (
        owner_id INTEGER PRIMARY KEY,
        media_type TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        caption TEXT DEFAULT ''
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS wallet_messages (
        owner_id INTEGER PRIMARY KEY,
        text TEXT NOT NULL DEFAULT ''
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        category_id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
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
        delivery_key TEXT DEFAULT '',
        delivery_link TEXT DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1
    );
    """)

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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposit_requests (
        dep_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        shop_owner_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        proof_file_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_ts INTEGER NOT NULL
    );
    """)

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

# ============================================================
# DB HELPERS
# ============================================================
def ensure_user(uid: int, username: str):
    username = (username or "").lstrip("@").strip()
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

def list_users(limit: int = 60) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT user_id, username, balance, created_ts
        FROM users
        ORDER BY created_ts DESC
        LIMIT ?
    """, (limit,))
    return cur.fetchall()

def list_users_prefix(prefix: str, limit: int = 60) -> List[sqlite3.Row]:
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

def is_active_seller(uid: int) -> bool:
    ok, _ = seller_shop_state(uid)
    return ok

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

def set_seller_wallet_addr(uid: int, addr: str):
    ensure_seller(uid)
    cur.execute("UPDATE sellers SET wallet_address=? WHERE seller_id=?", ((addr or "").strip(), uid))
    conn.commit()

def list_sellers(limit: int = 60) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT u.user_id AS seller_id, u.username, u.balance, s.sub_until_ts, s.restricted_until_ts, s.banned
        FROM sellers s
        JOIN users u ON u.user_id = s.seller_id
        ORDER BY s.sub_until_ts DESC
        LIMIT ?
    """, (limit,))
    return cur.fetchall()

def list_sellers_prefix(prefix: str, limit: int = 60) -> List[sqlite3.Row]:
    p = (prefix or "").lstrip("@").strip().lower()
    cur.execute("""
        SELECT u.user_id AS seller_id, u.username, u.balance, s.sub_until_ts, s.restricted_until_ts, s.banned
        FROM sellers s
        JOIN users u ON u.user_id = s.seller_id
        WHERE lower(u.username) LIKE ?
        ORDER BY s.sub_until_ts DESC
        LIMIT ?
    """, (p + "%", limit))
    return cur.fetchall()

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

def format_history(uid: int) -> str:
    cur.execute("""
        SELECT type, amount, balance_after, note, created_ts
        FROM transactions
        WHERE user_id=?
        ORDER BY tx_id DESC
        LIMIT 12
    """, (uid,))
    rows = cur.fetchall()
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
        else:
            sign = "+" if amt >= 0 else "-"
            out.append(f"{t}: {sign}{abs(amt):.2f} {CURRENCY}\nTotal Balance: {bal:.2f} {CURRENCY}\n")

    return "\n".join(out).strip()

def get_welcome(owner_id: int) -> sqlite3.Row:
    cur.execute("SELECT * FROM welcome_messages WHERE owner_id=?", (owner_id,))
    r = cur.fetchone()
    if r:
        return r

    if owner_id == WELCOME_PUBLIC:
        cap = DEFAULT_PUBLIC_WELCOME
    elif owner_id == WELCOME_SELLER_MAIN:
        cap = DEFAULT_SELLER_MAIN_WELCOME
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

def get_wallet_message(owner_id: int) -> str:
    cur.execute("SELECT text FROM wallet_messages WHERE owner_id=?", (owner_id,))
    r = cur.fetchone()
    if r and (r["text"] or "").strip():
        return (r["text"] or "").strip()
    return DEFAULT_MAIN_WALLET_MSG if owner_id == OWNER_MAIN else DEFAULT_SELLER_WALLET_MSG

def set_wallet_message(owner_id: int, text: str):
    t = (text or "").strip()
    cur.execute("""
        INSERT INTO wallet_messages(owner_id, text)
        VALUES(?,?)
        ON CONFLICT(owner_id) DO UPDATE SET text=excluded.text
    """, (owner_id, t))
    conn.commit()

# ============================================================
# CATALOG
# ============================================================
def upsert_category(owner_id: int, name: str, description: str = ""):
    name = (name or "").strip()
    if not name:
        raise ValueError("Category name required")
    cur.execute("""
        INSERT INTO categories(owner_id, name, description, active)
        VALUES(?,?,?,1)
        ON CONFLICT(owner_id, name) DO UPDATE SET
            description=excluded.description,
            active=1
    """, (owner_id, name, (description or "").strip()))
    conn.commit()

def upsert_cocategory(owner_id: int, category_name: str, name: str, description: str = ""):
    category_name = (category_name or "").strip()
    name = (name or "").strip()
    if not category_name or not name:
        raise ValueError("Co-category requires category + name")
    cur.execute("""
        INSERT INTO cocategories(owner_id, category_name, name, description, active)
        VALUES(?,?,?,?,1)
        ON CONFLICT(owner_id, category_name, name) DO UPDATE SET
            description=excluded.description,
            active=1
    """, (owner_id, category_name, name, (description or "").strip()))
    conn.commit()

def add_product(owner_id: int, category_name: str, cocategory_name: str, name: str, price: float, description: str = "") -> int:
    category_name = (category_name or "").strip()
    cocategory_name = (cocategory_name or "").strip()
    name = (name or "").strip()
    if not category_name or not cocategory_name or not name:
        raise ValueError("Product requires category, co-category, name")
    if owner_id != OWNER_MAIN and contains_reserved_words(name):
        raise ValueError("This product name is not allowed for sellers.")
    if float(price) <= 0:
        raise ValueError("Price must be > 0")

    upsert_category(owner_id, category_name)
    upsert_cocategory(owner_id, category_name, cocategory_name)

    cur.execute("""
        INSERT INTO products(owner_id, category_name, cocategory_name, name, price, description, active)
        VALUES(?,?,?,?,?,?,1)
    """, (owner_id, category_name, cocategory_name, name, float(price), (description or "").strip()))
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
        SELECT name, description
        FROM categories
        WHERE owner_id=? AND active=1
        ORDER BY name
    """, (owner_id,))
    return cur.fetchall()

def list_cocategories(owner_id: int, category_name: str) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT name, description
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

def list_all_products(owner_id: int, limit: int = 60) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT product_id, name, price, category_name, cocategory_name
        FROM products
        WHERE owner_id=? AND active=1
        ORDER BY product_id DESC
        LIMIT ?
    """, (owner_id, limit))
    return cur.fetchall()

def get_product(owner_id: int, product_id: int) -> Optional[sqlite3.Row]:
    cur.execute("""
        SELECT *
        FROM products
        WHERE owner_id=? AND product_id=? AND active=1
    """, (owner_id, product_id))
    return cur.fetchone()

# ============================================================
# SUPPORT / TICKETS
# ============================================================
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

def list_tickets_for(receiver_id: int) -> List[sqlite3.Row]:
    cur.execute("""
        SELECT t.ticket_id, t.from_id, u.username, t.created_ts
        FROM tickets t
        JOIN users u ON u.user_id=t.from_id
        WHERE t.to_id=? AND t.status='open'
        ORDER BY t.created_ts DESC
        LIMIT 25
    """, (receiver_id,))
    return cur.fetchall()

def get_ticket(ticket_id: int) -> Optional[sqlite3.Row]:
    cur.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,))
    return cur.fetchone()

def close_ticket(ticket_id: int):
    cur.execute("UPDATE tickets SET status='closed' WHERE ticket_id=?", (ticket_id,))
    conn.commit()

# ============================================================
# DEPOSITS
# ============================================================
def create_deposit_request(user_id: int, shop_owner_id: int, amount: float, proof_file_id: str) -> int:
    cur.execute("""
        INSERT INTO deposit_requests(user_id, shop_owner_id, amount, proof_file_id, status, created_ts)
        VALUES(?,?,?,?, 'pending', ?)
    """, (user_id, shop_owner_id, float(amount), proof_file_id, now_ts()))
    conn.commit()
    return int(cur.lastrowid)

def approve_deposit(dep_id: int, actor_id: int) -> Optional[int]:
    cur.execute("SELECT * FROM deposit_requests WHERE dep_id=? AND status='pending'", (dep_id,))
    d = cur.fetchone()
    if not d:
        return None
    add_balance(int(d["user_id"]), float(d["amount"]), actor_id, "deposit_ok", "Deposit approved")
    cur.execute("UPDATE deposit_requests SET status='approved' WHERE dep_id=?", (dep_id,))
    conn.commit()
    return int(d["user_id"])

def reject_deposit(dep_id: int, actor_id: int, reason: str = "Rejected") -> Optional[int]:
    cur.execute("SELECT * FROM deposit_requests WHERE dep_id=? AND status='pending'", (dep_id,))
    d = cur.fetchone()
    if not d:
        return None
    add_balance(int(d["user_id"]), 0.0, actor_id, "deposit_reject", reason)
    cur.execute("UPDATE deposit_requests SET status='rejected' WHERE dep_id=?", (dep_id,))
    conn.commit()
    return int(d["user_id"])

# ============================================================
# NOTIFICATIONS
# ============================================================
async def notify_superadmin(context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        await context.bot.send_message(SUPER_ADMIN_ID, text)
    except Exception:
        pass

async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str):
    for a in ADMIN_IDS:
        try:
            await context.bot.send_message(a, text)
        except Exception:
            pass

async def notify_owner_and_superadmin(context: ContextTypes.DEFAULT_TYPE, owner_id: int, text: str):
    if owner_id != OWNER_MAIN:
        try:
            await context.bot.send_message(owner_id, text)
        except Exception:
            pass
    await notify_superadmin(context, text)

# ============================================================
# UI KEYBOARDS
# ============================================================
def kb_main_home(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🛒 Products", callback_data="U_PRODUCTS"),
         InlineKeyboardButton("💰 Wallet", callback_data="U_WALLET")],
        [InlineKeyboardButton("📜 History", callback_data="U_HISTORY"),
         InlineKeyboardButton("🆘 Support", callback_data="U_SUPPORT")],
        [InlineKeyboardButton("⭐ Become Seller", callback_data="U_BECOME_SELLER")],
    ]
    if is_admin(uid) or is_active_seller(uid):
        rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="ADMIN_PANEL")])
    return InlineKeyboardMarkup(rows)

def kb_seller_shop_home(shop_owner: int, uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📂 Categories", callback_data=f"SHOP_CATS:{shop_owner}"),
         InlineKeyboardButton("💰 Wallet", callback_data="U_WALLET")],
        [InlineKeyboardButton("📜 History", callback_data="U_HISTORY"),
         InlineKeyboardButton("🆘 Support", callback_data="U_SUPPORT")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ]
    if uid == shop_owner or is_superadmin(uid):
        rows.insert(2, [InlineKeyboardButton("🏬 Main Shop", callback_data="GO_MAIN_SHOP")])
    return InlineKeyboardMarkup(rows)

def kb_shop_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏬 Main Shop", callback_data="OPEN_SHOP:0"),
         InlineKeyboardButton("🏪 Seller Shops", callback_data="SELLER_SHOPS")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

def kb_categories(owner_id: int, cats: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for c in cats[:25]:
        rows.append([InlineKeyboardButton(c["name"], callback_data=f"CATEGORY:{owner_id}:{c['name']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"OPEN_SHOP:{owner_id}")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)

def kb_cocategories(owner_id: int, category_name: str, cocats: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for cc in cocats[:25]:
        rows.append([InlineKeyboardButton(cc["name"], callback_data=f"COCAT:{owner_id}:{category_name}:{cc['name']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"SHOP_CATS:{owner_id}")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)

def kb_products(owner_id: int, category_name: str, cocategory_name: str, products: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for p in products[:25]:
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

def kb_support_draft() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done", callback_data="SUPPORT_DONE"),
         InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")],
    ])

def kb_become_seller() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Pay {SELLER_SUB_PRICE:.2f} {CURRENCY} / {SELLER_SUB_DAYS} days", callback_data="SUB_PAY")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

def kb_admin_panel(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🛒 Manage Shop", callback_data="M_SHOP")],
        [InlineKeyboardButton("💳 Approve Deposits", callback_data="M_DEPOSITS")],
        [InlineKeyboardButton("🆘 Support Inbox", callback_data="M_TICKETS")],
        [InlineKeyboardButton("👥 Users", callback_data="M_USERS")],
    ]
    if is_superadmin(uid):
        rows.append([InlineKeyboardButton("🏪 Sellers", callback_data="M_SELLERS")])
    rows += [
        [InlineKeyboardButton("💳 Set Wallet Address", callback_data="M_SET_WALLET")],
        [InlineKeyboardButton("📝 Edit Wallet Message", callback_data="M_EDIT_WALLETMSG")],
        [InlineKeyboardButton("🖼 Edit Welcome Message", callback_data="M_EDIT_WELCOME")],
        [InlineKeyboardButton("📣 Share My Shop", callback_data="M_SHARE")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ]
    return InlineKeyboardMarkup(rows)

def kb_shop_manage(owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Category", callback_data=f"CAT_ADD:{owner_id}")],
        [InlineKeyboardButton("➕ Add Co-Category", callback_data=f"COCAT_ADD:{owner_id}")],
        [InlineKeyboardButton("➕ Add Product", callback_data=f"PROD_ADD:{owner_id}")],
        [InlineKeyboardButton("✉️ Set Delivery (Key/Link)", callback_data=f"DELIVERY_SET:{owner_id}")],
        [InlineKeyboardButton("🗑 Remove Product", callback_data=f"PROD_DEL:{owner_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ADMIN_PANEL"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

def kb_user_list(users: List[sqlite3.Row], is_seller_scope: bool) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for u in users[:30]:
        uname = (u["username"] or "").strip()
        label = f"@{uname}" if uname else f"NoUsername — {u['user_id']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"USER_VIEW:{u['user_id']}")])
    rows.append([InlineKeyboardButton("🔎 Search", callback_data=("USER_SEARCH_SELLER" if is_seller_scope else "USER_SEARCH_ALL"))])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ADMIN_PANEL"),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)

def kb_user_actions(target_uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ +10", callback_data=f"BAL:{target_uid}:10"),
         InlineKeyboardButton("➖ -10", callback_data=f"BAL:{target_uid}:-10")],
        [InlineKeyboardButton("➕ +50", callback_data=f"BAL:{target_uid}:50"),
         InlineKeyboardButton("➖ -50", callback_data=f"BAL:{target_uid}:-50")],
        [InlineKeyboardButton("📜 View History", callback_data=f"USER_HIST:{target_uid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="M_USERS"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

def kb_seller_list(sellers: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for s in sellers[:30]:
        uname = (s["username"] or "").strip()
        label = f"@{uname}" if uname else f"NoUsername — {s['seller_id']}"
        d = days_left(int(s["sub_until_ts"]))
        rows.append([InlineKeyboardButton(f"{label} (days {d})", callback_data=f"SELLER_VIEW:{s['seller_id']}")])
    rows.append([InlineKeyboardButton("🔎 Search", callback_data="SELLER_SEARCH")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ADMIN_PANEL"),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)

def kb_seller_actions(seller_id: int, banned: int) -> InlineKeyboardMarkup:
    ban_label = "✅ Unban" if banned else "🚫 Ban"
    ban_cb = f"SA_UNBAN:{seller_id}" if banned else f"SA_BAN:{seller_id}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add 7 days", callback_data=f"SA_ADD_SUB:{seller_id}:7"),
         InlineKeyboardButton("➕ Add 14 days", callback_data=f"SA_ADD_SUB:{seller_id}:14"),
         InlineKeyboardButton("➕ Add 30 days", callback_data=f"SA_ADD_SUB:{seller_id}:30")],
        [InlineKeyboardButton(ban_label, callback_data=ban_cb),
         InlineKeyboardButton("🏪 Open Shop", callback_data=f"OPEN_SHOP:{seller_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="M_SELLERS"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

def kb_deposit_actions(dep_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"DEP_OK:{dep_id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"DEP_NO:{dep_id}")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

def kb_ticket_list(tickets: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for t in tickets[:25]:
        uname = (t["username"] or "").strip()
        label = f"@{uname}" if uname else f"NoUsername — {t['from_id']}"
        rows.append([InlineKeyboardButton(f"#{t['ticket_id']} from {label}", callback_data=f"TICKET_OPEN:{t['ticket_id']}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ADMIN_PANEL"),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
    return InlineKeyboardMarkup(rows)

def kb_ticket_view(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Reply (draft)", callback_data=f"TICKET_REPLY:{ticket_id}"),
         InlineKeyboardButton("✅ Close Ticket", callback_data=f"TICKET_CLOSE:{ticket_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="M_TICKETS"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])

def kb_ticket_reply() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done", callback_data="TICKET_REPLY_DONE"),
         InlineKeyboardButton("❌ Cancel", callback_data="MAIN_MENU")],
    ])

# ============================================================
# SHOP CONTEXT / ROUTING
# ============================================================
def set_support_target(uid: int, target: int):
    cur.execute("UPDATE users SET last_support_target=? WHERE user_id=?", (target, uid))
    conn.commit()

def resolve_wallet_for_context(uid: int, context: ContextTypes.DEFAULT_TYPE) -> Tuple[int, str, str]:
    shop_owner = current_shop_owner(context)
    if shop_owner != OWNER_MAIN:
        s = get_seller(shop_owner)
        addr = (s["wallet_address"] or "").strip() if s else ""
        if addr:
            return shop_owner, addr, get_wallet_message(shop_owner)
        return OWNER_MAIN, MAIN_WALLET_ADDRESS, get_wallet_message(OWNER_MAIN)
    return OWNER_MAIN, MAIN_WALLET_ADDRESS, get_wallet_message(OWNER_MAIN)

async def open_shop(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int):
    uid = update.effective_user.id

    if owner_id != OWNER_MAIN:
        ok, msg = seller_shop_state(owner_id)
        if not ok:
            await send_or_edit(update, context, msg, InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return

    context.user_data[CTX_SHOP_OWNER] = owner_id
    set_support_target(uid, owner_id if owner_id != OWNER_MAIN else SUPER_ADMIN_ID)

    if owner_id == OWNER_MAIN:
        w = get_welcome(WELCOME_SELLER_MAIN if is_active_seller(uid) else WELCOME_PUBLIC)
        await send_media_or_text(uid, context, w["media_type"], w["file_id"], w["caption"], kb_main_home(uid))
    else:
        w = get_welcome(owner_id)
        await send_media_or_text(uid, context, w["media_type"], w["file_id"], w["caption"], kb_seller_shop_home(owner_id, uid))

# ============================================================
# BUY / DELIVERY
# ============================================================
async def do_buy(uid: int, context: ContextTypes.DEFAULT_TYPE, owner_id: int, product_id: int):
    p = get_product(owner_id, product_id)
    if not p:
        await context.bot.send_message(uid, "❌ Product not found.")
        return

    price = float(p["price"])
    u = get_user(uid)
    if float(u["balance"]) < price:
        await context.bot.send_message(uid, f"❌ Insufficient balance.\nPrice: {fmt_money(price)}\nYour balance: {fmt_money(float(u['balance']))}")
        return

    add_balance(uid, -price, uid, "purchase", p["name"])

    if owner_id != OWNER_MAIN:
        add_balance(owner_id, +price, uid, "sale", f"{p['name']}")

    buyer = f"@{u['username']}" if (u["username"] or "").strip() else str(uid)
    await notify_owner_and_superadmin(context, owner_id, f"🛒 Purchase\nShop: {owner_id}\nBuyer: {buyer}\nProduct: {p['name']}\nAmount: {fmt_money(price)}")

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
        await context.bot.send_message(uid, "❌ Product not found.")
        return
    link = (p["delivery_link"] or "").strip()
    if not link:
        await context.bot.send_message(uid, "❌ No file link set by owner.")
        return
    await context.bot.send_message(uid, f"📁 File Link:\n{link}")

# ============================================================
# SUPPORT SEND
# ============================================================
async def support_send(uid: int, context: ContextTypes.DEFAULT_TYPE, text: str):
    u = get_user(uid)
    target = int(u["last_support_target"]) if u else SUPER_ADMIN_ID
    if target == 0:
        target = SUPER_ADMIN_ID

    tid = get_open_ticket(uid, target)
    if not tid:
        tid = create_ticket(uid, target)
    add_ticket_message(tid, uid, text)

    sender = f"@{u['username']}" if (u and (u["username"] or "").strip()) else str(uid)
    await notify_owner_and_superadmin(context, target if target != SUPER_ADMIN_ID else OWNER_MAIN,
                                      f"🆘 Support Ticket #{tid}\nFrom: {sender}\nTo: {target}\n\n{text[:1200]}")

# ============================================================
# SELLER SHARE LINK
# ============================================================
async def send_share_my_shop(uid: int, context: ContextTypes.DEFAULT_TYPE):
    me = await context.bot.get_me()
    if not me.username:
        await context.bot.send_message(uid, "❌ Bot username missing. Set a username in BotFather.")
        return
    link = f"https://t.me/{me.username}?start=shop_{uid}"
    msg = f"📣 Share My Shop\n\nSend this link to your users:\n{link}"
    await context.bot.send_message(uid, msg)

# ============================================================
# START
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    err = must_have_config()
    if err:
        if update.message:
            await update.message.reply_text(f"❌ Bot config error: {err}")
        return

    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username or "")

    if context.args and context.args[0].startswith("shop_"):
        try:
            sid = int(context.args[0].split("_", 1)[1])
            reset_flow(context)
            await open_shop(update, context, sid)
            return
        except Exception:
            pass

    reset_flow(context)
    context.user_data[CTX_SHOP_OWNER] = OWNER_MAIN
    w = get_welcome(WELCOME_SELLER_MAIN if is_active_seller(uid) else WELCOME_PUBLIC)
    await send_media_or_text(uid, context, w["media_type"], w["file_id"], w["caption"], kb_main_home(uid))

# ============================================================
# CALLBACKS
# ============================================================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid, q.from_user.username or "")
    data = q.data

    # MAIN_MENU behavior:
    # seller-shop users stay inside seller shop
    if data == "MAIN_MENU":
        reset_flow(context)
        shop_owner = current_shop_owner(context)
        if shop_owner != OWNER_MAIN and uid != shop_owner and not is_superadmin(uid):
            await safe_delete(q.message)
            await open_shop(update, context, shop_owner)
            return
        await safe_delete(q.message)
        await start(update, context)
        return

    if data == "GO_MAIN_SHOP":
        reset_flow(context)
        await safe_delete(q.message)
        await open_shop(update, context, OWNER_MAIN)
        return

    if data == "U_PRODUCTS":
        reset_flow(context)
        await send_or_edit(update, context, "Choose a shop:", kb_shop_picker())
        return

    if data.startswith("OPEN_SHOP:"):
        reset_flow(context)
        owner_id = int(data.split(":", 1)[1])
        await safe_delete(q.message)
        await open_shop(update, context, owner_id)
        return

    if data == "SELLER_SHOPS":
        reset_flow(context)
        sellers = list_sellers(30)
        rows: List[List[InlineKeyboardButton]] = []
        for s in sellers:
            uname = (s["username"] or "").strip()
            label = f"@{uname}" if uname else f"NoUsername — {s['seller_id']}"
            rows.append([InlineKeyboardButton(label, callback_data=f"OPEN_SHOP:{s['seller_id']}")])
        rows.append([InlineKeyboardButton("🔎 Search Seller", callback_data="SELLER_SEARCH")])
        rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
        await send_or_edit(update, context, "🏪 Seller Shops", InlineKeyboardMarkup(rows))
        return

    if data == "SELLER_SEARCH":
        reset_flow(context)
        context.user_data[CTX_FLOW] = FLOW_SELLERS_SEARCH
        await send_or_edit(update, context, "Send seller username to search (no @).",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data == "U_WALLET":
        reset_flow(context)
        _, addr, msg = resolve_wallet_for_context(uid, context)
        u = get_user(uid)
        text = (
            f"💰 Wallet\n\n"
            f"Balance: {fmt_money(float(u['balance']))}\n\n"
            f"Deposit Address:\n{addr}\n\n"
            f"{msg}"
        )
        await send_or_edit(update, context, text, InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Request Deposit", callback_data="DEP_START")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]
        ]))
        return

    if data == "DEP_START":
        reset_flow(context)
        shop_owner = current_shop_owner(context)
        context.user_data[CTX_FLOW] = FLOW_DEPOSIT
        context.user_data[FLOW_DEPOSIT] = {"step": "amount", "shop_owner_id": shop_owner}
        await send_or_edit(update, context, "Enter deposit amount (numbers only):",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data == "U_HISTORY":
        reset_flow(context)
        await send_or_edit(update, context, format_history(uid),
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data == "U_SUPPORT":
        reset_flow(context)
        context.user_data[CTX_FLOW] = FLOW_SUPPORT_DRAFT
        context.user_data[FLOW_SUPPORT_DRAFT] = {"draft": ""}
        await send_or_edit(update, context, "✉️ Type your message (send multiple texts). Press DONE to send.",
                           kb_support_draft())
        return

    if data == "SUPPORT_DONE":
        if context.user_data.get(CTX_FLOW) != FLOW_SUPPORT_DRAFT:
            await q.answer("No draft", show_alert=True)
            return
        draft = (context.user_data.get(FLOW_SUPPORT_DRAFT, {}).get("draft") or "").strip()
        if not draft:
            await q.answer("Empty message", show_alert=True)
            return
        await support_send(uid, context, draft)
        reset_flow(context)
        await send_or_edit(update, context, "✅ Support message sent.",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data == "U_BECOME_SELLER":
        reset_flow(context)
        await send_or_edit(update, context, "⭐ Become Seller", kb_become_seller())
        return

    if data == "SUB_PAY":
        u = get_user(uid)
        if float(u["balance"]) < SELLER_SUB_PRICE:
            await send_or_edit(update, context,
                               f"❌ Not enough balance.\nPrice: {fmt_money(SELLER_SUB_PRICE)}\nYour balance: {fmt_money(float(u['balance']))}",
                               InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return
        add_balance(uid, -SELLER_SUB_PRICE, uid, "sub", f"{SELLER_SUB_DAYS} days")
        new_until = add_seller_subscription(uid, SELLER_SUB_DAYS)
        await send_or_edit(update, context, f"✅ Seller activated!\nDays left: {days_left(new_until)}", kb_main_home(uid))
        return

    if data.startswith("SHOP_CATS:"):
        owner_id = int(data.split(":", 1)[1])
        cats = list_categories(owner_id)
        await send_or_edit(update, context, "📂 Categories", kb_categories(owner_id, cats))
        return

    if data.startswith("CATEGORY:"):
        _, owner_id, cat = data.split(":", 2)
        owner_id = int(owner_id)
        cocats = list_cocategories(owner_id, cat)
        await send_or_edit(update, context, f"📁 {cat}", kb_cocategories(owner_id, cat, cocats))
        return

    if data.startswith("COCAT:"):
        _, owner_id, cat, cocat = data.split(":", 3)
        owner_id = int(owner_id)
        prods = list_products(owner_id, cat, cocat)
        await send_or_edit(update, context, f"🛒 {cocat}", kb_products(owner_id, cat, cocat, prods))
        return

    if data.startswith("VIEWPROD:"):
        _, owner_id, pid = data.split(":", 2)
        owner_id, pid = int(owner_id), int(pid)
        p = get_product(owner_id, pid)
        if not p:
            await send_or_edit(update, context, "❌ Product not found.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return
        text = (
            f"🛒 {p['name']}\n\n"
            f"Price: {fmt_money(float(p['price']))}\n\n"
            f"{(p['description'] or '').strip()}"
        ).strip()
        await send_or_edit(update, context, text, kb_product_view(owner_id, p["category_name"], p["cocategory_name"], pid))
        return

    if data.startswith("BUY:"):
        _, owner_id, pid = data.split(":", 2)
        await do_buy(uid, context, int(owner_id), int(pid))
        return

    if data.startswith("GETFILE:"):
        _, owner_id, pid = data.split(":", 2)
        await do_getfile(uid, context, int(owner_id), int(pid))
        return

    if data == "ADMIN_PANEL":
        reset_flow(context)
        if not (is_admin(uid) or is_active_seller(uid)):
            await q.answer("Not allowed", show_alert=True)
            return
        await send_or_edit(update, context, "🛠 Admin Panel", kb_admin_panel(uid))
        return

    if data == "M_SHOP":
        reset_flow(context)
        owner = uid if is_active_seller(uid) else OWNER_MAIN
        if is_superadmin(uid):
            owner = OWNER_MAIN
        await send_or_edit(update, context, "🛒 Manage Shop", kb_shop_manage(owner))
        return

    if data == "M_DEPOSITS":
        reset_flow(context)
        owner = uid if is_active_seller(uid) else OWNER_MAIN
        if is_superadmin(uid) or is_admin(uid):
            owner = OWNER_MAIN

        cur.execute("""
            SELECT d.dep_id, d.user_id, u.username, d.amount, d.proof_file_id
            FROM deposit_requests d
            JOIN users u ON u.user_id=d.user_id
            WHERE d.status='pending' AND d.shop_owner_id=?
            ORDER BY d.created_ts ASC
            LIMIT 25
        """, (owner,))
        deps = cur.fetchall()
        if not deps:
            await send_or_edit(update, context, "No pending deposits.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return

        for d in deps:
            uname = (d["username"] or "").strip()
            label = f"@{uname}" if uname else str(d["user_id"])
            caption = f"💳 Deposit Request\nUser: {label}\nAmount: {fmt_money(float(d['amount']))}\nDepID: {d['dep_id']}"
            await context.bot.send_photo(uid, d["proof_file_id"], caption=caption, reply_markup=kb_deposit_actions(int(d["dep_id"])))

        await send_or_edit(update, context, "⬆️ Sent pending deposits above.",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data.startswith("DEP_OK:"):
        dep_id = int(data.split(":", 1)[1])
        user_id = approve_deposit(dep_id, uid)
        if user_id:
            await q.answer("Approved")
            try:
                await context.bot.send_message(user_id, "✅ Your deposit was approved and added to your balance.")
            except Exception:
                pass
        else:
            await q.answer("Not found", show_alert=True)
        return

    if data.startswith("DEP_NO:"):
        dep_id = int(data.split(":", 1)[1])
        user_id = reject_deposit(dep_id, uid)
        if user_id:
            await q.answer("Rejected")
            try:
                await context.bot.send_message(user_id, "❌ Your deposit was rejected. Contact support if needed.")
            except Exception:
                pass
        else:
            await q.answer("Not found", show_alert=True)
        return

    if data == "M_TICKETS":
        reset_flow(context)
        receiver = uid if is_active_seller(uid) else SUPER_ADMIN_ID
        if is_admin(uid) or is_superadmin(uid):
            receiver = SUPER_ADMIN_ID
        tickets = list_tickets_for(receiver)
        if not tickets:
            await send_or_edit(update, context, "No open tickets.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return
        await send_or_edit(update, context, "🆘 Tickets:", kb_ticket_list(tickets))
        return

    if data.startswith("TICKET_OPEN:"):
        tid = int(data.split(":", 1)[1])
        t = get_ticket(tid)
        if not t:
            await send_or_edit(update, context, "Ticket not found.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return

        if int(t["to_id"]) != uid and not is_admin(uid) and not is_superadmin(uid):
            await q.answer("Not allowed", show_alert=True)
            return

        cur.execute("""
            SELECT sender_id, message
            FROM ticket_messages
            WHERE ticket_id=?
            ORDER BY msg_id DESC
            LIMIT 8
        """, (tid,))
        msgs = cur.fetchall()
        lines = [f"🆘 Ticket #{tid}"]
        for m in reversed(msgs):
            sender = "User" if int(m["sender_id"]) == int(t["from_id"]) else "Support"
            lines.append(f"{sender}: {m['message']}")
        await send_or_edit(update, context, "\n\n".join(lines), kb_ticket_view(tid))
        return

    if data.startswith("TICKET_CLOSE:"):
        tid = int(data.split(":", 1)[1])
        close_ticket(tid)
        await send_or_edit(update, context, f"✅ Closed ticket #{tid}.",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data.startswith("TICKET_REPLY:"):
        tid = int(data.split(":", 1)[1])
        t = get_ticket(tid)
        if not t:
            await q.answer("Not found", show_alert=True)
            return
        reset_flow(context)
        context.user_data[CTX_FLOW] = FLOW_SUPPORT_REPLY
        context.user_data[FLOW_SUPPORT_REPLY] = {"ticket_id": tid, "draft": ""}
        await send_or_edit(update, context, "Type your reply (multiple messages). Press DONE to send.", kb_ticket_reply())
        return

    if data == "TICKET_REPLY_DONE":
        if context.user_data.get(CTX_FLOW) != FLOW_SUPPORT_REPLY:
            await q.answer("No draft", show_alert=True)
            return
        payload = context.user_data.get(FLOW_SUPPORT_REPLY, {})
        tid = int(payload.get("ticket_id", 0))
        draft = (payload.get("draft") or "").strip()
        if not tid or not draft:
            await q.answer("Empty", show_alert=True)
            return
        t = get_ticket(tid)
        if not t:
            await q.answer("Not found", show_alert=True)
            return
        add_ticket_message(tid, uid, draft)
        to_user = int(t["from_id"])
        try:
            await context.bot.send_message(to_user, f"✅ Reply from support:\n\n{draft}")
        except Exception:
            pass
        reset_flow(context)
        await send_or_edit(update, context, "✅ Reply sent.",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data == "M_USERS":
        reset_flow(context)
        if is_superadmin(uid):
            users = list_users(30)
            await send_or_edit(update, context, "👥 Users (ALL)", kb_user_list(users, is_seller_scope=False))
        else:
            cur.execute("""
                SELECT DISTINCT u.user_id, u.username, u.balance, u.created_ts
                FROM users u
                LEFT JOIN deposit_requests d ON d.user_id=u.user_id AND d.shop_owner_id=?
                WHERE u.last_support_target=? OR d.user_id IS NOT NULL
                ORDER BY u.created_ts DESC
                LIMIT 30
            """, (uid, uid))
            users = cur.fetchall()
            await send_or_edit(update, context, "👥 Users (Your Shop)", kb_user_list(users, is_seller_scope=True))
        return

    if data in ("USER_SEARCH_SELLER", "USER_SEARCH_ALL"):
        reset_flow(context)
        context.user_data[CTX_FLOW] = FLOW_USERS_SEARCH
        context.user_data[FLOW_USERS_SEARCH] = {"mode": data}
        await send_or_edit(update, context, "Send username to search (no @).",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data.startswith("USER_VIEW:"):
        target_uid = int(data.split(":", 1)[1])
        tu = get_user(target_uid)
        if not tu:
            await send_or_edit(update, context, "User not found.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return

        if not is_superadmin(uid) and is_active_seller(uid):
            cur.execute("""
                SELECT 1
                FROM users u
                LEFT JOIN deposit_requests d ON d.user_id=u.user_id AND d.shop_owner_id=?
                WHERE u.user_id=? AND (u.last_support_target=? OR d.user_id IS NOT NULL)
                LIMIT 1
            """, (uid, target_uid, uid))
            if not cur.fetchone():
                await q.answer("Not allowed", show_alert=True)
                return
        elif not is_superadmin(uid) and not is_admin(uid):
            await q.answer("Not allowed", show_alert=True)
            return

        uname = (tu["username"] or "").strip()
        label = f"@{uname}" if uname else "NoUsername"
        extra = ""
        s = get_seller(target_uid)
        if s:
            extra = f"\n\nSeller: YES\nDays left: {days_left(int(s['sub_until_ts']))}\nBanned: {int(s['banned'])}"
        text = f"👤 User: {label}\nTelegram ID: {tu['user_id']}\nBalance: {fmt_money(float(tu['balance']))}{extra}"
        await send_or_edit(update, context, text, kb_user_actions(target_uid))
        return

    if data.startswith("BAL:"):
        _, target_uid, delta = data.split(":", 2)
        target_uid = int(target_uid)
        delta = float(delta)

        if not (is_superadmin(uid) or is_admin(uid) or is_active_seller(uid)):
            await q.answer("Not allowed", show_alert=True)
            return

        if is_active_seller(uid) and not (is_superadmin(uid) or is_admin(uid)):
            cur.execute("""
                SELECT 1
                FROM users u
                LEFT JOIN deposit_requests d ON d.user_id=u.user_id AND d.shop_owner_id=?
                WHERE u.user_id=? AND (u.last_support_target=? OR d.user_id IS NOT NULL)
                LIMIT 1
            """, (uid, target_uid, uid))
            if not cur.fetchone():
                await q.answer("Not allowed", show_alert=True)
                return

        try:
            add_balance(target_uid, delta, uid, "admin_edit", "Balance update")
        except Exception as e:
            await q.answer(str(e), show_alert=True)
            return

        tu = get_user(target_uid)
        uname = (tu["username"] or "").strip()
        label = f"@{uname}" if uname else str(target_uid)
        await send_or_edit(update, context, f"✅ Updated {label}\nNew balance: {fmt_money(float(tu['balance']))}",
                           kb_user_actions(target_uid))
        return

    if data.startswith("USER_HIST:"):
        target_uid = int(data.split(":", 1)[1])
        if not (is_superadmin(uid) or is_admin(uid) or is_active_seller(uid)):
            await q.answer("Not allowed", show_alert=True)
            return
        await send_or_edit(update, context, format_history(target_uid),
                           InlineKeyboardMarkup([
                               [InlineKeyboardButton("⬅️ Back", callback_data=f"USER_VIEW:{target_uid}")],
                               [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]
                           ]))
        return

    if data == "M_SELLERS":
        reset_flow(context)
        if not is_superadmin(uid):
            await q.answer("Not allowed", show_alert=True)
            return
        sellers = list_sellers(30)
        await send_or_edit(update, context, "🏪 Sellers", kb_seller_list(sellers))
        return

    if data.startswith("SELLER_VIEW:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed", show_alert=True)
            return
        sid = int(data.split(":", 1)[1])
        su = get_user(sid)
        ss = get_seller(sid)
        if not su or not ss:
            await send_or_edit(update, context, "Seller not found.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return
        uname = (su["username"] or "").strip()
        label = f"@{uname}" if uname else "NoUsername"
        text = (
            f"🏪 Seller: {label}\nTelegram ID: {sid}\n"
            f"Balance: {fmt_money(float(su['balance']))}\n"
            f"Days left: {days_left(int(ss['sub_until_ts']))}\n"
            f"Banned: {int(ss['banned'])}"
        )
        await send_or_edit(update, context, text, kb_seller_actions(sid, int(ss["banned"])))
        return

    if data.startswith("SA_ADD_SUB:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed", show_alert=True)
            return
        _, sid, dd = data.split(":")
        sid, dd = int(sid), int(dd)
        new_until = add_seller_subscription(sid, dd)
        await q.answer("Added")
        await send_or_edit(update, context, f"✅ Updated seller {sid}\nDays left: {days_left(new_until)}",
                           kb_seller_actions(sid, int(get_seller(sid)["banned"])))
        return

    if data.startswith("SA_BAN:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed", show_alert=True)
            return
        sid = int(data.split(":", 1)[1])
        ensure_seller(sid)
        cur.execute("UPDATE sellers SET banned=1 WHERE seller_id=?", (sid,))
        conn.commit()
        await q.answer("Banned")
        await send_or_edit(update, context, f"🚫 Banned seller {sid}", kb_seller_actions(sid, 1))
        return

    if data.startswith("SA_UNBAN:"):
        if not is_superadmin(uid):
            await q.answer("Not allowed", show_alert=True)
            return
        sid = int(data.split(":", 1)[1])
        ensure_seller(sid)
        cur.execute("UPDATE sellers SET banned=0 WHERE seller_id=?", (sid,))
        conn.commit()
        await q.answer("Unbanned")
        await send_or_edit(update, context, f"✅ Unbanned seller {sid}", kb_seller_actions(sid, 0))
        return

    if data == "M_SET_WALLET":
        reset_flow(context)
        if is_active_seller(uid):
            context.user_data[CTX_FLOW] = FLOW_SET_WALLET_ADDR
            await send_or_edit(update, context, "Send your wallet address (any format).",
                               InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        else:
            await send_or_edit(update, context, "Main shop wallet address is set by ENV (USDT_TRC20).",
                               InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data == "M_EDIT_WALLETMSG":
        reset_flow(context)
        owner_id = uid if is_active_seller(uid) else OWNER_MAIN
        if is_superadmin(uid):
            owner_id = OWNER_MAIN
        context.user_data[CTX_FLOW] = FLOW_EDIT_WALLETMSG
        context.user_data[FLOW_EDIT_WALLETMSG] = {"owner_id": owner_id}
        await send_or_edit(update, context, "Send the new wallet message (text).",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data == "M_EDIT_WELCOME":
        reset_flow(context)
        owner_id = uid if is_active_seller(uid) else WELCOME_PUBLIC
        if is_superadmin(uid):
            owner_id = WELCOME_PUBLIC
        context.user_data[CTX_FLOW] = FLOW_EDIT_WELCOME
        context.user_data[FLOW_EDIT_WELCOME] = {"owner_id": owner_id}
        await send_or_edit(update, context, "Send welcome as:\n- Text OR\n- Photo/Video with caption\n\n(Next message will be saved.)",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data == "M_SHARE":
        reset_flow(context)
        if not is_active_seller(uid) and not is_superadmin(uid):
            await q.answer("Not allowed", show_alert=True)
            return
        await send_share_my_shop(uid, context)
        return

    if data.startswith("CAT_ADD:"):
        reset_flow(context)
        owner = int(data.split(":", 1)[1])
        if owner != OWNER_MAIN and owner != uid and not is_superadmin(uid):
            await q.answer("Not allowed", show_alert=True)
            return
        context.user_data[CTX_FLOW] = FLOW_CAT_ADD
        context.user_data[FLOW_CAT_ADD] = {"owner_id": owner}
        await send_or_edit(update, context, "Send category as:\nName | optional description",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data.startswith("COCAT_ADD:"):
        reset_flow(context)
        owner = int(data.split(":", 1)[1])
        cats = list_categories(owner)
        if not cats:
            await send_or_edit(update, context, "No categories yet. Add a category first.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP")],
                                                     [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"COCAT_PICK:{owner}:{c['name']}")] for c in cats[:25]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP"),
                     InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
        await send_or_edit(update, context, "Pick a category:", InlineKeyboardMarkup(rows))
        return

    if data.startswith("COCAT_PICK:"):
        _, owner, cat = data.split(":", 2)
        owner = int(owner)
        reset_flow(context)
        context.user_data[CTX_FLOW] = FLOW_COCAT_NAME
        context.user_data[FLOW_COCAT_NAME] = {"owner_id": owner, "category": cat}
        await send_or_edit(update, context, f"Send co-category name for:\n{cat}\n\nFormat:\nName | optional description",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data.startswith("PROD_ADD:"):
        reset_flow(context)
        owner = int(data.split(":", 1)[1])
        cats = list_categories(owner)
        if not cats:
            await send_or_edit(update, context, "No categories yet. Add a category first.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP")],
                                                     [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"PROD_PICK_CAT:{owner}:{c['name']}")] for c in cats[:25]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP"),
                     InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
        await send_or_edit(update, context, "Pick a category:", InlineKeyboardMarkup(rows))
        return

    if data.startswith("PROD_PICK_CAT:"):
        _, owner, cat = data.split(":", 2)
        owner = int(owner)
        cocats = list_cocategories(owner, cat)
        if not cocats:
            await send_or_edit(update, context, "No co-categories yet. Add one first.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"PROD_ADD:{owner}")],
                                                     [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return
        rows = [[InlineKeyboardButton(cc["name"], callback_data=f"PROD_PICK_COCAT:{owner}:{cat}:{cc['name']}")] for cc in cocats[:25]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"PROD_ADD:{owner}"),
                     InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
        await send_or_edit(update, context, f"Pick a co-category for:\n{cat}", InlineKeyboardMarkup(rows))
        return

    if data.startswith("PROD_PICK_COCAT:"):
        _, owner, cat, cocat = data.split(":", 3)
        owner = int(owner)
        reset_flow(context)
        context.user_data[CTX_FLOW] = FLOW_PROD_DETAILS
        context.user_data[FLOW_PROD_DETAILS] = {"owner_id": owner, "category": cat, "cocat": cocat}
        await send_or_edit(update, context, "Send product details:\nName | Price | optional description",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data.startswith("DELIVERY_SET:"):
        reset_flow(context)
        owner = int(data.split(":", 1)[1])
        prods = list_all_products(owner, 30)
        if not prods:
            await send_or_edit(update, context, "No products yet.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP")],
                                                     [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return
        rows = [[InlineKeyboardButton(f"{p['name']} (#{p['product_id']})", callback_data=f"DEL_PICK:{owner}:{p['product_id']}")] for p in prods[:30]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP"),
                     InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
        await send_or_edit(update, context, "Pick a product to set delivery:", InlineKeyboardMarkup(rows))
        return

    if data.startswith("DEL_PICK:"):
        _, owner, pid = data.split(":", 2)
        owner = int(owner); pid = int(pid)
        reset_flow(context)
        context.user_data[CTX_FLOW] = FLOW_DELIVERY_TEXT
        context.user_data[FLOW_DELIVERY_TEXT] = {"owner_id": owner, "product_id": pid}
        await send_or_edit(update, context, "Send delivery as:\nKey text | Telegram link",
                           InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    if data.startswith("PROD_DEL:"):
        reset_flow(context)
        owner = int(data.split(":", 1)[1])
        prods = list_all_products(owner, 30)
        if not prods:
            await send_or_edit(update, context, "No products to remove.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP")],
                                                     [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return
        rows = [[InlineKeyboardButton(f"🗑 {p['name']} (#{p['product_id']})", callback_data=f"PROD_DEL_DO:{owner}:{p['product_id']}")] for p in prods[:30]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP"),
                     InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
        await send_or_edit(update, context, "Pick product to remove:", InlineKeyboardMarkup(rows))
        return

    if data.startswith("PROD_DEL_DO:"):
        _, owner, pid = data.split(":", 2)
        owner = int(owner); pid = int(pid)
        deactivate_product(owner, pid)
        await send_or_edit(update, context, "✅ Product removed.",
                           InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="M_SHOP")],
                                                 [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
        return

    await q.answer("Unknown button", show_alert=True)

# ============================================================
# MESSAGES
# ============================================================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username or "")
    flow = context.user_data.get(CTX_FLOW)

    if flow == FLOW_SUPPORT_DRAFT:
        if update.message.text:
            payload = context.user_data.get(FLOW_SUPPORT_DRAFT, {"draft": ""})
            payload["draft"] = (payload.get("draft") or "") + update.message.text.strip() + "\n"
            context.user_data[FLOW_SUPPORT_DRAFT] = payload
        return

    if flow == FLOW_SUPPORT_REPLY:
        if update.message.text:
            payload = context.user_data.get(FLOW_SUPPORT_REPLY, {"ticket_id": 0, "draft": ""})
            payload["draft"] = (payload.get("draft") or "") + update.message.text.strip() + "\n"
            context.user_data[FLOW_SUPPORT_REPLY] = payload
        return

    if flow == FLOW_DEPOSIT:
        payload = context.user_data.get(FLOW_DEPOSIT, {})
        step = payload.get("step", "amount")

        if step == "amount":
            if not update.message.text:
                await update.message.reply_text("Enter amount as text (numbers only).")
                return
            try:
                amount = float(update.message.text.strip())
                if amount <= 0:
                    raise ValueError("bad")
            except Exception:
                await update.message.reply_text("Enter a valid amount (example: 10).")
                return
            payload["amount"] = amount
            payload["step"] = "photo"
            context.user_data[FLOW_DEPOSIT] = payload
            await update.message.reply_text("Now send PHOTO proof.")
            return

        if step == "photo":
            if not update.message.photo:
                await update.message.reply_text("Send a PHOTO proof (not text).")
                return
            file_id = update.message.photo[-1].file_id
            shop_owner_id = int(payload.get("shop_owner_id", OWNER_MAIN))
            dep_id = create_deposit_request(uid, shop_owner_id, float(payload["amount"]), file_id)

            u = get_user(uid)
            buyer = f"@{u['username']}" if (u and (u["username"] or "").strip()) else str(uid)
            text = f"💳 Deposit Request\nDepID: {dep_id}\nShopOwner: {shop_owner_id}\nUser: {buyer}\nAmount: {fmt_money(float(payload['amount']))}"

            if shop_owner_id != OWNER_MAIN:
                try:
                    await context.bot.send_photo(shop_owner_id, file_id, caption=text, reply_markup=kb_deposit_actions(dep_id))
                except Exception:
                    pass

            for a in ADMIN_IDS:
                try:
                    await context.bot.send_photo(a, file_id, caption=text, reply_markup=kb_deposit_actions(dep_id))
                except Exception:
                    pass

            try:
                await context.bot.send_photo(SUPER_ADMIN_ID, file_id, caption=text, reply_markup=kb_deposit_actions(dep_id))
            except Exception:
                pass

            reset_flow(context)
            await update.message.reply_text("✅ Deposit request sent.",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]]))
            return

    if flow == FLOW_EDIT_WALLETMSG:
        payload = context.user_data.get(FLOW_EDIT_WALLETMSG, {})
        owner_id = int(payload.get("owner_id", OWNER_MAIN))
        if update.message.text:
            set_wallet_message(owner_id, update.message.text.strip())
            reset_flow(context)
            await update.message.reply_text("✅ Wallet message saved.")
        else:
            await update.message.reply_text("Send the wallet message as text.")
        return

    if flow == FLOW_SET_WALLET_ADDR:
        if update.message.text:
            set_seller_wallet_addr(uid, update.message.text.strip())
            reset_flow(context)
            await update.message.reply_text("✅ Wallet address saved.")
        else:
            await update.message.reply_text("Send wallet address as text.")
        return

    if flow == FLOW_EDIT_WELCOME:
        payload = context.user_data.get(FLOW_EDIT_WELCOME, {})
        owner_id = int(payload.get("owner_id", WELCOME_PUBLIC))

        media_type = ""
        file_id = ""
        caption = ""

        if update.message.photo:
            media_type = "photo"
            file_id = update.message.photo[-1].file_id
            caption = (update.message.caption or "").strip()
        elif update.message.video:
            media_type = "video"
            file_id = update.message.video.file_id
            caption = (update.message.caption or "").strip()
        elif update.message.text:
            caption = update.message.text.strip()
        else:
            await update.message.reply_text("Send text OR photo/video with caption.")
            return

        if not caption:
            caption = DEFAULT_PUBLIC_WELCOME if owner_id == WELCOME_PUBLIC else DEFAULT_SELLER_SHOP_WELCOME

        set_welcome(owner_id, media_type, file_id, caption)
        reset_flow(context)
        await update.message.reply_text("✅ Welcome saved.")
        return

    if flow == FLOW_USERS_SEARCH:
        payload = context.user_data.get(FLOW_USERS_SEARCH, {})
        mode = payload.get("mode", "USER_SEARCH_ALL")
        qtxt = (update.message.text or "").strip()
        if not qtxt:
            await update.message.reply_text("Send username to search.")
            return
        if mode == "USER_SEARCH_ALL":
            users = list_users_prefix(qtxt, 30)
            await update.message.reply_text("Users:", reply_markup=kb_user_list(users, is_seller_scope=False))
        else:
            cur.execute("""
                SELECT DISTINCT u.user_id, u.username, u.balance, u.created_ts
                FROM users u
                LEFT JOIN deposit_requests d ON d.user_id=u.user_id AND d.shop_owner_id=?
                WHERE (u.last_support_target=? OR d.user_id IS NOT NULL)
                AND lower(u.username) LIKE ?
                ORDER BY u.created_ts DESC
                LIMIT 30
            """, (uid, uid, qtxt.lower() + "%"))
            users = cur.fetchall()
            await update.message.reply_text("Users (Your Shop):", reply_markup=kb_user_list(users, is_seller_scope=True))
        reset_flow(context)
        return

    if flow == FLOW_SELLERS_SEARCH:
        qtxt = (update.message.text or "").strip()
        if not qtxt:
            await update.message.reply_text("Send seller username.")
            return
        sellers = list_sellers_prefix(qtxt, 30)
        rows = []
        for s in sellers:
            uname = (s["username"] or "").strip()
            label = f"@{uname}" if uname else f"NoUsername — {s['seller_id']}"
            rows.append([InlineKeyboardButton(label, callback_data=f"OPEN_SHOP:{s['seller_id']}")])
        rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
        await update.message.reply_text("Seller Shops:", reply_markup=InlineKeyboardMarkup(rows))
        reset_flow(context)
        return

    if flow == FLOW_CAT_ADD:
        payload = context.user_data.get(FLOW_CAT_ADD, {})
        owner = int(payload.get("owner_id", uid))
        if not update.message.text:
            await update.message.reply_text("Send: Name | optional description")
            return
        parts = [p.strip() for p in update.message.text.split("|")]
        name = parts[0] if parts else ""
        desc = parts[1] if len(parts) > 1 else ""
        try:
            upsert_category(owner, name, desc)
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")
            return
        reset_flow(context)
        await update.message.reply_text("✅ Category added.")
        return

    if flow == FLOW_COCAT_NAME:
        payload = context.user_data.get(FLOW_COCAT_NAME, {})
        owner = int(payload.get("owner_id", uid))
        cat = payload.get("category", "")
        if not update.message.text:
            await update.message.reply_text("Send: Name | optional description")
            return
        parts = [p.strip() for p in update.message.text.split("|")]
        name = parts[0] if parts else ""
        desc = parts[1] if len(parts) > 1 else ""
        try:
            upsert_cocategory(owner, cat, name, desc)
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")
            return
        reset_flow(context)
        await update.message.reply_text("✅ Co-category added.")
        return

    if flow == FLOW_PROD_DETAILS:
        payload = context.user_data.get(FLOW_PROD_DETAILS, {})
        owner = int(payload.get("owner_id", uid))
        cat = payload.get("category", "")
        cocat = payload.get("cocat", "")
        if not update.message.text:
            await update.message.reply_text("Send: Name | Price | optional description")
            return
        parts = [p.strip() for p in update.message.text.split("|")]
        if len(parts) < 2:
            await update.message.reply_text("Send: Name | Price | optional description")
            return
        name = parts[0]
        price_s = parts[1]
        desc = parts[2] if len(parts) > 2 else ""
        try:
            pid = add_product(owner, cat, cocat, name, float(price_s), desc)
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")
            return
        reset_flow(context)
        await update.message.reply_text(f"✅ Product added. ProductID: {pid}")
        return

    if flow == FLOW_DELIVERY_TEXT:
        payload = context.user_data.get(FLOW_DELIVERY_TEXT, {})
        owner = int(payload.get("owner_id", uid))
        pid = int(payload.get("product_id", 0))
        if not update.message.text:
            await update.message.reply_text("Send: Key text | Telegram link")
            return
        parts = [p.strip() for p in update.message.text.split("|")]
        if len(parts) < 2:
            await update.message.reply_text("Send: Key text | Telegram link")
            return
        key = parts[0]
        link = parts[1]
        set_product_delivery(owner, pid, key, link)
        reset_flow(context)
        await update.message.reply_text("✅ Delivery updated.")
        return

# ============================================================
# MAIN
# ============================================================
def main():
    db_init()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))

    log.info("AutoPanel bot running")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
