import os
import sqlite3
import time
import logging
from typing import Optional, Tuple, Set, List

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# ENV (Railway)
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0").strip() or "0")
_raw_admins = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS: Set[int] = set()
if _raw_admins:
    for x in _raw_admins.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_IDS.add(int(x))

STORE_NAME = os.getenv("STORE_NAME", "AutoPanel").strip()
CURRENCY = os.getenv("CURRENCY", "USDT").strip()
USDT_TRC20 = os.getenv("USDT_TRC20", "").strip()

SELLER_SUB_PRICE = float(os.getenv("SELLER_SUB_PRICE", "10").strip() or "10")
SELLER_SUB_DAYS = int(os.getenv("SELLER_SUB_DAYS", "30").strip() or "30")

DB_FILE = os.getenv("DB_FILE", "store.db").strip()

# =========================================================
# Logging
# =========================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("autopanel")


# =========================================================
# Constants
# =========================================================
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

DEFAULT_SELLER_SHOP_WELCOME = "Welcome to your shop!"

RESERVED_WORDS = [
    "seller", "become seller", "subscription", "subscribe",
    "reseller plan", "seller plan", "vip seller", "admin",
]


# =========================================================
# Helpers
# =========================================================
def now_ts() -> int:
    return int(time.time())


def is_superadmin(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID and SUPER_ADMIN_ID > 0


def is_admin(uid: int) -> bool:
    return is_superadmin(uid) or (uid in ADMIN_IDS)


def must_have_config() -> Optional[str]:
    if not BOT_TOKEN:
        return "Missing BOT_TOKEN"
    if SUPER_ADMIN_ID <= 0:
        return "Missing SUPER_ADMIN_ID"
    if not USDT_TRC20:
        return "Missing USDT_TRC20"
    return None


def contains_reserved_words(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in RESERVED_WORDS)


def seller_credit_block(text: str) -> bool:
    return "@rekkoown" in (text or "").lower()


# =========================================================
# DB
# =========================================================
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
        media_type TEXT DEFAULT '',   -- photo/video or ''
        file_id TEXT DEFAULT '',
        caption TEXT DEFAULT ''
    );
    """)

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


def ensure_user(uid: int, username: str):
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO users(user_id, username, balance, created_ts) VALUES(?,?,?,?)",
            (uid, username or "", 0.0, now_ts()),
        )
        conn.commit()
    else:
        cur.execute("UPDATE users SET username=? WHERE user_id=?", (username or "", uid))
        conn.commit()


def get_user(uid: int):
    cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    return cur.fetchone()


def ensure_seller(uid: int):
    cur.execute("INSERT OR IGNORE INTO sellers(seller_id) VALUES(?)", (uid,))
    conn.commit()


def get_seller(uid: int):
    cur.execute("SELECT * FROM sellers WHERE seller_id=?", (uid,))
    return cur.fetchone()


def is_active_seller(uid: int) -> bool:
    s = get_seller(uid)
    if not s:
        return False
    return int(s["sub_until_ts"]) > now_ts()


def seller_can_use(uid: int) -> Tuple[bool, str]:
    s = get_seller(uid)
    if s is None:
        return False, "You are not a seller."
    if int(s["banned"]) == 1:
        return False, "🚫 Your seller store is banned."
    if int(s["restricted_until_ts"]) > now_ts():
        return False, "⏳ Your seller store is restricted right now."
    if int(s["sub_until_ts"]) <= now_ts():
        return False, "❗ Your seller subscription is expired. Pay again in main store."
    return True, "OK"


def add_balance(uid: int, delta: float, actor_id: int, tx_type: str, note: str = "") -> float:
    u = get_user(uid)
    if u is None:
        raise ValueError("User not found")
    new_bal = float(u["balance"]) + float(delta)
    if new_bal < 0:
        raise ValueError("Insufficient balance")
    cur.execute("UPDATE users SET balance=? WHERE user_id=?", (new_bal, uid))
    cur.execute(
        "INSERT INTO transactions(user_id, actor_id, type, amount, balance_after, note, created_ts) VALUES(?,?,?,?,?,?,?)",
        (uid, actor_id, tx_type, float(delta), new_bal, note, now_ts()),
    )
    conn.commit()
    return new_bal


def tx_history(uid: int, limit: int = 10):
    cur.execute("""
        SELECT type, amount, balance_after, note, created_ts
        FROM transactions
        WHERE user_id=?
        ORDER BY tx_id DESC
        LIMIT ?
    """, (uid, limit))
    return cur.fetchall()


def set_seller_subscription(uid: int, add_days: int) -> int:
    ensure_seller(uid)
    s = get_seller(uid)
    now = now_ts()
    current = int(s["sub_until_ts"])
    base = current if current > now else now
    new_until = base + add_days * 86400
    cur.execute("UPDATE sellers SET sub_until_ts=? WHERE seller_id=?", (new_until, uid))
    conn.commit()
    return new_until


# ================= Welcome =================
def get_welcome(owner_id: int) -> sqlite3.Row:
    cur.execute("SELECT * FROM welcome_messages WHERE owner_id=?", (owner_id,))
    r = cur.fetchone()
    if r:
        return r

    if owner_id == WELCOME_PUBLIC:
        caption = DEFAULT_PUBLIC_WELCOME
    elif owner_id == WELCOME_SELLER_MAIN:
        caption = DEFAULT_SELLER_MAIN_WELCOME
    elif owner_id == 0:
        caption = DEFAULT_PUBLIC_WELCOME
    else:
        caption = DEFAULT_SELLER_SHOP_WELCOME

    cur.execute(
        "INSERT OR IGNORE INTO welcome_messages(owner_id, media_type, file_id, caption) VALUES(?,?,?,?)",
        (owner_id, "", "", caption),
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
    """, (owner_id, media_type, file_id, caption))
    conn.commit()


def welcome_profile_for_user(uid: int) -> int:
    return WELCOME_SELLER_MAIN if is_active_seller(uid) else WELCOME_PUBLIC


# ================= Catalog =================
def upsert_category(owner_id: int, name: str, description: str = "", media_type: str = "", file_id: str = ""):
    cur.execute("""
        INSERT INTO categories(owner_id, name, description, media_type, file_id, active)
        VALUES(?,?,?,?,?,1)
        ON CONFLICT(owner_id, name) DO UPDATE SET
            description=excluded.description,
            media_type=excluded.media_type,
            file_id=excluded.file_id,
            active=1
    """, (owner_id, name.strip(), description.strip(), media_type, file_id))
    conn.commit()


def upsert_cocategory(owner_id: int, category_name: str, name: str, description: str = "", media_type: str = "", file_id: str = ""):
    cur.execute("""
        INSERT INTO cocategories(owner_id, category_name, name, description, media_type, file_id, active)
        VALUES(?,?,?,?,?,?,1)
        ON CONFLICT(owner_id, category_name, name) DO UPDATE SET
            description=excluded.description,
            media_type=excluded.media_type,
            file_id=excluded.file_id,
            active=1
    """, (owner_id, category_name.strip(), name.strip(), description.strip(), media_type, file_id))
    conn.commit()


def add_product(
    owner_id: int,
    category_name: str,
    cocategory_name: str,
    name: str,
    price: float,
    description: str = "",
    media_type: str = "",
    file_id: str = "",
) -> int:
    upsert_category(owner_id, category_name)
    upsert_cocategory(owner_id, category_name, cocategory_name)
    cur.execute("""
        INSERT INTO products(owner_id, category_name, cocategory_name, name, price, description, media_type, file_id, active)
        VALUES(?,?,?,?,?,?,?,?,1)
    """, (owner_id, category_name.strip(), cocategory_name.strip(), name.strip(), float(price),
          description.strip(), media_type, file_id))
    conn.commit()
    return int(cur.lastrowid)


def update_product_meta(owner_id: int, product_id: int, description: str, media_type: str, file_id: str):
    cur.execute("""
        UPDATE products SET description=?, media_type=?, file_id=?
        WHERE owner_id=? AND product_id=?
    """, (description.strip(), media_type, file_id, owner_id, product_id))
    conn.commit()


def update_product_delivery(owner_id: int, product_id: int, delivery_key: str, delivery_link: str):
    cur.execute("""
        UPDATE products SET delivery_key=?, delivery_link=?
        WHERE owner_id=? AND product_id=?
    """, (delivery_key.strip(), delivery_link.strip(), owner_id, product_id))
    conn.commit()


def deactivate_product(owner_id: int, product_id: int):
    cur.execute("UPDATE products SET active=0 WHERE owner_id=? AND product_id=?", (owner_id, product_id))
    conn.commit()


def list_categories(owner_id: int):
    cur.execute("SELECT name FROM categories WHERE owner_id=? AND active=1 ORDER BY name", (owner_id,))
    return cur.fetchall()


def list_cocategories(owner_id: int, category_name: str):
    cur.execute("""
        SELECT name FROM cocategories
        WHERE owner_id=? AND category_name=? AND active=1
        ORDER BY name
    """, (owner_id, category_name))
    return cur.fetchall()


def list_products(owner_id: int, category_name: str, cocategory_name: str):
    cur.execute("""
        SELECT product_id, name, price
        FROM products
        WHERE owner_id=? AND category_name=? AND cocategory_name=? AND active=1
        ORDER BY name
    """, (owner_id, category_name, cocategory_name))
    return cur.fetchall()


def get_product(owner_id: int, product_id: int):
    cur.execute("""
        SELECT * FROM products
        WHERE owner_id=? AND product_id=? AND active=1
    """, (owner_id, product_id))
    return cur.fetchone()


# ================= Support =================
def get_open_ticket(from_id: int, to_id: int) -> Optional[int]:
    cur.execute("""
        SELECT ticket_id FROM tickets
        WHERE from_id=? AND to_id=? AND status='open'
        ORDER BY ticket_id DESC LIMIT 1
    """, (from_id, to_id))
    r = cur.fetchone()
    return int(r["ticket_id"]) if r else None


def create_ticket(from_id: int, to_id: int) -> int:
    cur.execute("INSERT INTO tickets(from_id, to_id, status, created_ts) VALUES(?,?, 'open', ?)",
                (from_id, to_id, now_ts()))
    conn.commit()
    return int(cur.lastrowid)


def add_ticket_message(ticket_id: int, sender_id: int, message: str):
    cur.execute(
        "INSERT INTO ticket_messages(ticket_id, sender_id, message, created_ts) VALUES(?,?,?,?)",
        (ticket_id, sender_id, message, now_ts()),
    )
    conn.commit()


def list_open_tickets_for(to_id: int, limit: int = 20):
    cur.execute("""
        SELECT ticket_id, from_id, created_ts
        FROM tickets
        WHERE to_id=? AND status='open'
        ORDER BY ticket_id DESC
        LIMIT ?
    """, (to_id, limit))
    return cur.fetchall()


def close_ticket(ticket_id: int):
    cur.execute("UPDATE tickets SET status='closed' WHERE ticket_id=?", (ticket_id,))
    conn.commit()


# =========================================================
# UI
# =========================================================
def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
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


def kb_menu_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")]])


def seller_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Manage Products", callback_data="S_MANAGE_PRODUCTS"),
         InlineKeyboardButton("🏷 Manage Categories", callback_data="S_MANAGE_CATS")],
        [InlineKeyboardButton("💳 Wallet Address", callback_data="S_SET_WALLET"),
         InlineKeyboardButton("👤 Edit User Balance", callback_data="S_EDIT_USER_BAL")],
        [InlineKeyboardButton("🖼 Edit Shop Welcome", callback_data="S_EDIT_SHOP_WELCOME"),
         InlineKeyboardButton("🆘 Support Inbox", callback_data="S_TICKETS")],
        [InlineKeyboardButton("⭐ Pay Subscription", callback_data="U_BECOME_SELLER"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])


def admin_panel_kb(sa: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("✅ Approve Deposits", callback_data="A_DEPOSITS"),
         InlineKeyboardButton("💰 Edit User Balance", callback_data="A_EDIT_BAL")],
        [InlineKeyboardButton("🛒 Manage Main Store", callback_data="A_MAIN_STORE"),
         InlineKeyboardButton("🖼 Edit Public Welcome", callback_data="EDIT_WELCOME_PUBLIC")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ]
    if sa:
        rows.insert(2, [InlineKeyboardButton("🖼 Edit Seller Main Welcome", callback_data="EDIT_WELCOME_SELLERMAIN"),
                        InlineKeyboardButton("🖼 Edit Any Seller Shop Welcome", callback_data="EDIT_WELCOME_ANY_SELLERSHOP")])
        rows.insert(3, [InlineKeyboardButton("⏳ Restrict Seller", callback_data="SA_RESTRICT_PICK"),
                        InlineKeyboardButton("⏱ Extend Seller Sub", callback_data="SA_SUB_PICK")])
        rows.insert(4, [InlineKeyboardButton("🚫 Ban/Unban Seller", callback_data="SA_BAN_PICK"),
                        InlineKeyboardButton("💰 Edit Seller Balance", callback_data="SA_EDIT_SELLER_BAL")])
        rows.insert(5, [InlineKeyboardButton("📊 Totals", callback_data="SA_TOTALS"),
                        InlineKeyboardButton("🆘 Admin Support Inbox", callback_data="A_TICKETS")])
    else:
        rows.insert(3, [InlineKeyboardButton("🆘 Admin Support Inbox", callback_data="A_TICKETS")])
    return InlineKeyboardMarkup(rows)


def manage_store_kb(prefix: str) -> InlineKeyboardMarkup:
    # prefix "MAIN" or "SELLER:<id>"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏷 Add/Update Category", callback_data=f"{prefix}:CAT_ADD"),
         InlineKeyboardButton("🏷 Add/Update Co-Category", callback_data=f"{prefix}:COCAT_ADD")],
        [InlineKeyboardButton("🛒 Add Product", callback_data=f"{prefix}:PROD_ADD"),
         InlineKeyboardButton("🗑 Remove Product", callback_data=f"{prefix}:PROD_DEL")],
        [InlineKeyboardButton("✉️ Set Delivery (Key/Link)", callback_data=f"{prefix}:PROD_DELIVERY")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
    ])


# =========================================================
# UX: cancel flow + delete message + menu
# =========================================================
async def hard_reset_to_menu(uid: int, context: ContextTypes.DEFAULT_TYPE, msg_to_delete=None):
    context.user_data.clear()
    if msg_to_delete is not None:
        try:
            await msg_to_delete.delete()
        except Exception:
            pass
    await send_welcome_to_user(uid, context)


# =========================================================
# Welcome sender
# =========================================================
async def send_welcome_to_user(uid: int, context: ContextTypes.DEFAULT_TYPE):
    owner_id = welcome_profile_for_user(uid)
    w = get_welcome(owner_id)
    caption = (w["caption"] or "").strip()
    if not caption:
        caption = DEFAULT_SELLER_MAIN_WELCOME if owner_id == WELCOME_SELLER_MAIN else DEFAULT_PUBLIC_WELCOME

    kb = main_menu_kb(uid)

    if w["media_type"] == "photo" and w["file_id"]:
        await context.bot.send_photo(chat_id=uid, photo=w["file_id"], caption=caption, reply_markup=kb)
    elif w["media_type"] == "video" and w["file_id"]:
        await context.bot.send_video(chat_id=uid, video=w["file_id"], caption=caption, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id=uid, text=caption, reply_markup=kb)


# =========================================================
# /start (only command)
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    err = must_have_config()
    if err:
        await update.message.reply_text(
            f"❌ Bot is not configured: {err}\n\n"
            "Railway Variables:\n"
            "- BOT_TOKEN\n- SUPER_ADMIN_ID\n- USDT_TRC20\n"
            "Optional:\n- ADMIN_IDS (comma)\n- SELLER_SUB_PRICE\n- SELLER_SUB_DAYS\n- STORE_NAME\n- CURRENCY\n"
        )
        return

    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username or "")
    await send_welcome_to_user(uid, context)


# =========================================================
# Buttons
# =========================================================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid, q.from_user.username or "")

    data = q.data

    # main/back hard reset
    if data in ("MAIN_MENU", "BACK"):
        await hard_reset_to_menu(uid, context, q.message)
        return

    # ---------------- USERS ----------------
    if data == "U_WALLET":
        u = get_user(uid)
        txt = (
            f"💰 Wallet\n\n"
            f"Balance: {float(u['balance']):.2f} {CURRENCY}\n\n"
            f"Deposit Address ({CURRENCY} TRC20):\n{USDT_TRC20}\n\n"
            "Deposit request requires:\n"
            "1) amount\n"
            "2) photo proof\n"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Request Deposit", callback_data="U_DEP_REQ")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
        ])
        await q.edit_message_text(txt, reply_markup=kb)
        return

    if data == "U_DEP_REQ":
        context.user_data.clear()
        context.user_data["deposit_flow"] = {"step": "amount"}
        await q.edit_message_text("Send deposit amount (example: 10 or 25.5).", reply_markup=kb_menu_only())
        return

    if data == "U_HISTORY":
        rows = tx_history(uid, 10)
        if not rows:
            await q.edit_message_text("📜 No transactions yet.", reply_markup=kb_menu_only())
            return
        text = "📜 Last 10 Transactions\n\n"
        for r in rows:
            text += f"• {r['type']} | {float(r['amount']):+g} {CURRENCY} | bal {float(r['balance_after']):.2f}\n"
        await q.edit_message_text(text, reply_markup=kb_menu_only())
        return

    if data == "U_SUPPORT":
        u = get_user(uid)
        last_sid = int(u["last_support_target"] or 0)
        rows = [[InlineKeyboardButton("👑 Contact Admin", callback_data="SUPPORT_TO:ADMIN")]]
        if last_sid:
            rows.append([InlineKeyboardButton("🏪 Contact Last Seller", callback_data=f"SUPPORT_TO:{last_sid}")])
        rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
        await q.edit_message_text(
            "🆘 Support\n\nChoose who to contact, then send your message.\nSend: cancel (to stop)",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("SUPPORT_TO:"):
        target = data.split(":", 1)[1]
        to_id = SUPER_ADMIN_ID if target == "ADMIN" else int(target)
        context.user_data.clear()
        context.user_data["support_to_id"] = to_id
        await q.edit_message_text("✉️ Send your support message now.\nSend: cancel (to stop)", reply_markup=kb_menu_only())
        return

    if data == "U_BECOME_SELLER":
        u = get_user(uid)
        txt = (
            "⭐ Become Seller\n\n"
            f"Price: {SELLER_SUB_PRICE:g} {CURRENCY}\n"
            f"Duration: {SELLER_SUB_DAYS} days\n\n"
            f"Your balance: {float(u['balance']):.2f} {CURRENCY}\n\n"
            "Tap Pay to activate/extend your subscription.\n(Buy again adds days.)"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Pay", callback_data="SELLER_PAY")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
        ])
        await q.edit_message_text(txt, reply_markup=kb)
        return

    if data == "SELLER_PAY":
        u = get_user(uid)
        price = float(SELLER_SUB_PRICE)
        if float(u["balance"]) < price:
            await q.edit_message_text(
                f"❌ Insufficient balance.\nNeed: {price:.2f} {CURRENCY}\nYou have: {float(u['balance']):.2f} {CURRENCY}",
                reply_markup=kb_menu_only(),
            )
            return
        add_balance(uid, -price, uid, "sub", f"Seller subscription +{SELLER_SUB_DAYS} days")
        ensure_seller(uid)
        new_until = set_seller_subscription(uid, SELLER_SUB_DAYS)

        try:
            await q.message.delete()
        except Exception:
            pass
        context.user_data.clear()
        await context.bot.send_message(
            chat_id=uid,
            text=f"✅ Seller subscription updated.\nValid until (UTC): {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(new_until))}",
        )
        await send_welcome_to_user(uid, context)
        return

    if data == "U_PRODUCTS":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏬 Main Store", callback_data="SHOP:0"),
             InlineKeyboardButton("🏪 Open Seller Shop", callback_data="SHOP_OPEN_SELLER")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
        ])
        await q.edit_message_text("Choose a shop:", reply_markup=kb)
        return

    if data == "SHOP_OPEN_SELLER":
        context.user_data.clear()
        context.user_data["awaiting_open_shop_id"] = True
        await q.edit_message_text("Send seller_id (numbers only).", reply_markup=kb_menu_only())
        return

    if data.startswith("SHOP:"):
        owner_id = int(data.split(":")[1])
        cats = list_categories(owner_id)
        if not cats:
            await q.edit_message_text("No categories yet in this shop.", reply_markup=kb_menu_only())
            return
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"CATEGORY:{owner_id}:{c['name']}")] for c in cats[:30]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="U_PRODUCTS")])
        await q.edit_message_text("Select Category:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("CATEGORY:"):
        _, owner_id_s, cat = data.split(":", 2)
        owner_id = int(owner_id_s)
        cocats = list_cocategories(owner_id, cat)
        if not cocats:
            await q.edit_message_text("No co-categories yet.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data=f"SHOP:{owner_id}")]
            ]))
            return
        rows = [[InlineKeyboardButton(cc["name"], callback_data=f"COCAT:{owner_id}:{cat}:{cc['name']}")] for cc in cocats[:30]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"SHOP:{owner_id}")])
        await q.edit_message_text(f"Category: {cat}\nSelect Co-Category:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("COCAT:"):
        _, owner_id_s, cat, cocat = data.split(":", 3)
        owner_id = int(owner_id_s)
        prods = list_products(owner_id, cat, cocat)
        if not prods:
            await q.edit_message_text("No products yet.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data=f"CATEGORY:{owner_id}:{cat}")]
            ]))
            return
        text = f"Category: {cat}\nCo-Category: {cocat}\n\n"
        rows = []
        for p in prods[:30]:
            text += f"• [{p['product_id']}] {p['name']} — {float(p['price']):.2f} {CURRENCY}\n"
            rows.append([InlineKeyboardButton(f"View #{p['product_id']}", callback_data=f"VIEWPROD:{owner_id}:{p['product_id']}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"CATEGORY:{owner_id}:{cat}")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("VIEWPROD:"):
        _, owner_id_s, pid_s = data.split(":")
        owner_id = int(owner_id_s)
        pid = int(pid_s)
        p = get_product(owner_id, pid)
        if not p:
            await q.edit_message_text("Product not found.", reply_markup=kb_menu_only())
            return

        desc = (p["description"] or "").strip()
        caption = f"*{p['name']}*\nPrice: `{float(p['price']):.2f} {CURRENCY}`"
        if desc:
            caption += f"\n\n{desc}"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Buy", callback_data=f"BUY:{owner_id}:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"COCAT:{owner_id}:{p['category_name']}:{p['cocategory_name']}")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
        ])

        try:
            await q.message.delete()
        except Exception:
            pass

        if p["media_type"] == "photo" and p["file_id"]:
            await context.bot.send_photo(chat_id=uid, photo=p["file_id"], caption=caption, parse_mode="Markdown", reply_markup=kb)
        elif p["media_type"] == "video" and p["file_id"]:
            await context.bot.send_video(chat_id=uid, video=p["file_id"], caption=caption, parse_mode="Markdown", reply_markup=kb)
        else:
            await context.bot.send_message(chat_id=uid, text=caption, parse_mode="Markdown", reply_markup=kb)
        return

    if data.startswith("BUY:"):
        _, owner_id_s, pid_s = data.split(":")
        owner_id = int(owner_id_s)
        pid = int(pid_s)
        p = get_product(owner_id, pid)
        if not p:
            await q.edit_message_text("❌ Product not found.", reply_markup=kb_menu_only())
            return

        price = float(p["price"])
        u = get_user(uid)
        if float(u["balance"]) < price:
            await q.edit_message_text(
                f"❌ Insufficient balance.\nPrice: {price:.2f} {CURRENCY}\nYour balance: {float(u['balance']):.2f} {CURRENCY}",
                reply_markup=kb_menu_only(),
            )
            return

        add_balance(uid, -price, uid, "purchase", f"Bought {p['name']} (shop {owner_id})")

        if owner_id != 0:
            add_balance(owner_id, +price, uid, "sale", f"Sold {p['name']} to {uid}")
            cur.execute("UPDATE users SET last_support_target=? WHERE user_id=?", (owner_id, uid))
            conn.commit()

        delivery_key = (p["delivery_key"] or "").strip()
        delivery_link = (p["delivery_link"] or "").strip()

        text = f"✅ Purchase successful!\n\nProduct: {p['name']}\nPaid: {price:.2f} {CURRENCY}\n\n"
        text += "🔑 Key:\n" + (delivery_key if delivery_key else "(Owner has not set a key yet.)")

        rows = []
        if delivery_link:
            rows.append([InlineKeyboardButton("📁 Get File", callback_data=f"GETFILE:{owner_id}:{pid}")])
        rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])

        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("GETFILE:"):
        _, owner_id_s, pid_s = data.split(":")
        owner_id = int(owner_id_s)
        pid = int(pid_s)
        p = get_product(owner_id, pid)
        if not p or not (p["delivery_link"] or "").strip():
            await q.answer("No link set yet. Contact support.", show_alert=True)
            return
        await context.bot.send_message(chat_id=uid, text=f"📁 Your File Link:\n{p['delivery_link']}")
        await q.answer("Sent ✅", show_alert=False)
        return

    # ---------------- SELLERS ----------------
    if data == "S_PANEL":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐ Pay Subscription", callback_data="U_BECOME_SELLER")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
            ]))
            return
        await q.edit_message_text("🏪 Seller Panel", reply_markup=seller_panel_kb())
        return

    if data == "S_SET_WALLET":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        context.user_data["awaiting_seller_wallet"] = True
        await q.edit_message_text("Send your seller wallet address now:", reply_markup=kb_menu_only())
        return

    if data == "S_EDIT_USER_BAL":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        context.user_data["awaiting_seller_editbal"] = True
        await q.edit_message_text("Send: user_id amount\nExample: 123456789 +10", reply_markup=kb_menu_only())
        return

    if data == "S_EDIT_SHOP_WELCOME":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        context.user_data["welcome_edit"] = {"owner_id": uid, "who": "seller_shop", "step": "caption"}
        await q.edit_message_text("Send shop welcome caption (or type: skip):", reply_markup=kb_menu_only())
        return

    if data == "S_TICKETS":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=kb_menu_only())
            return
        tickets = list_open_tickets_for(uid, 20)
        if not tickets:
            await q.edit_message_text("🆘 No open tickets.", reply_markup=kb_menu_only())
            return
        rows = []
        text = "🆘 Open Tickets\n\n"
        for t in tickets[:20]:
            text += f"• Ticket #{t['ticket_id']} from user {t['from_id']}\n"
            rows.append([InlineKeyboardButton(f"Open #{t['ticket_id']}", callback_data=f"TICKET_OPEN:{t['ticket_id']}")])
        rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("TICKET_OPEN:"):
        ticket_id = int(data.split(":")[1])
        cur.execute("SELECT * FROM tickets WHERE ticket_id=? AND status='open'", (ticket_id,))
        t = cur.fetchone()
        if not t:
            await q.edit_message_text("Ticket not found or closed.", reply_markup=kb_menu_only())
            return
        if not (is_admin(uid) or int(t["to_id"]) == uid):
            await q.edit_message_text("❌ Not allowed.", reply_markup=kb_menu_only())
            return

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✉️ Reply", callback_data=f"TICKET_REPLY:{ticket_id}"),
             InlineKeyboardButton("✅ Close", callback_data=f"TICKET_CLOSE:{ticket_id}")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
        ])
        await q.edit_message_text(f"🆘 Ticket #{ticket_id}\nFrom user: {t['from_id']}\n\nPress Reply to send message.", reply_markup=kb)
        return

    if data.startswith("TICKET_REPLY:"):
        ticket_id = int(data.split(":")[1])
        context.user_data.clear()
        context.user_data["ticket_reply"] = {"ticket_id": ticket_id}
        await q.edit_message_text("Send your reply now (text).", reply_markup=kb_menu_only())
        return

    if data.startswith("TICKET_CLOSE:"):
        ticket_id = int(data.split(":")[1])
        close_ticket(ticket_id)
        await q.edit_message_text(f"✅ Ticket #{ticket_id} closed.", reply_markup=kb_menu_only())
        return

    if data == "S_MANAGE_CATS":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=kb_menu_only())
            return
        await q.edit_message_text("🏷 Seller Category Management", reply_markup=manage_store_kb(prefix=f"SELLER:{uid}"))
        return

    if data == "S_MANAGE_PRODUCTS":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=kb_menu_only())
            return
        await q.edit_message_text("🛒 Seller Product Management", reply_markup=manage_store_kb(prefix=f"SELLER:{uid}"))
        return

    # ---------------- ADMIN / SUPER ADMIN ----------------
    if data == "A_PANEL":
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=kb_menu_only())
            return
        await q.edit_message_text("🛠 Admin Panel", reply_markup=admin_panel_kb(sa=False))
        return

    if data == "SA_PANEL":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        await q.edit_message_text("👑 Super Admin Panel", reply_markup=admin_panel_kb(sa=True))
        return

    if data == "A_TICKETS":
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=kb_menu_only())
            return
        tickets = list_open_tickets_for(SUPER_ADMIN_ID, 20)  # admin inbox goes to superadmin
        if not tickets:
            await q.edit_message_text("🆘 No open tickets.", reply_markup=kb_menu_only())
            return
        rows = []
        text = "🆘 Admin Open Tickets\n\n"
        for t in tickets[:20]:
            text += f"• Ticket #{t['ticket_id']} from user {t['from_id']}\n"
            rows.append([InlineKeyboardButton(f"Open #{t['ticket_id']}", callback_data=f"TICKET_OPEN:{t['ticket_id']}")])
        rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "A_DEPOSITS":
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=kb_menu_only())
            return
        cur.execute("""
            SELECT dep_id, user_id, amount
            FROM deposit_requests
            WHERE status='pending'
            ORDER BY dep_id ASC
            LIMIT 10
        """)
        rows = cur.fetchall()
        if not rows:
            await q.edit_message_text("✅ No pending deposits.", reply_markup=kb_menu_only())
            return
        text = "✅ Pending Deposits\n\n"
        kb_rows = []
        for r in rows:
            text += f"• Dep #{r['dep_id']} | user {r['user_id']} | {float(r['amount']):g} {CURRENCY}\n"
            kb_rows.append([InlineKeyboardButton(f"View Proof #{r['dep_id']}", callback_data=f"DEP_VIEW:{r['dep_id']}")])
            kb_rows.append([
                InlineKeyboardButton(f"Approve #{r['dep_id']}", callback_data=f"DEP_OK:{r['dep_id']}"),
                InlineKeyboardButton(f"Reject #{r['dep_id']}", callback_data=f"DEP_NO:{r['dep_id']}"),
            ])
        kb_rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if data.startswith("DEP_VIEW:"):
        if not is_admin(uid):
            await q.answer("Admin only.", show_alert=True)
            return
        dep_id = int(data.split(":")[1])
        cur.execute("SELECT * FROM deposit_requests WHERE dep_id=? AND status='pending'", (dep_id,))
        r = cur.fetchone()
        if not r:
            await q.answer("Not found.", show_alert=True)
            return
        await context.bot.send_photo(
            chat_id=uid,
            photo=r["proof_file_id"],
            caption=f"Proof for Dep #{dep_id}\nUser: {r['user_id']}\nAmount: {float(r['amount']):g} {CURRENCY}",
        )
        await q.answer("Sent ✅", show_alert=False)
        return

    if data.startswith("DEP_OK:") or data.startswith("DEP_NO:"):
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=kb_menu_only())
            return
        dep_id = int(data.split(":")[1])
        cur.execute("SELECT * FROM deposit_requests WHERE dep_id=? AND status='pending'", (dep_id,))
        r = cur.fetchone()
        if not r:
            await q.edit_message_text("Deposit not found or already handled.", reply_markup=kb_menu_only())
            return

        if data.startswith("DEP_NO:"):
            cur.execute("UPDATE deposit_requests SET status='rejected' WHERE dep_id=?", (dep_id,))
            conn.commit()
            await q.edit_message_text(f"❌ Rejected deposit #{dep_id}", reply_markup=kb_menu_only())
            return

        amount = float(r["amount"])
        user_id = int(r["user_id"])
        cur.execute("UPDATE deposit_requests SET status='approved' WHERE dep_id=?", (dep_id,))
        conn.commit()
        add_balance(user_id, amount, uid, "deposit_ok", f"Deposit approved #{dep_id}")

        await q.edit_message_text(f"✅ Approved deposit #{dep_id} (+{amount:g} {CURRENCY})", reply_markup=kb_menu_only())
        try:
            await context.bot.send_message(chat_id=user_id, text=f"✅ Your deposit #{dep_id} was approved.\nBalance added: {amount:g} {CURRENCY}")
        except Exception:
            pass
        return

    if data == "A_EDIT_BAL":
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        context.user_data["awaiting_admin_editbal"] = True
        await q.edit_message_text("Send: user_id amount\nExample: 123456789 +50", reply_markup=kb_menu_only())
        return

    if data == "A_MAIN_STORE":
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=kb_menu_only())
            return
        await q.edit_message_text("🛒 Main Store Management", reply_markup=manage_store_kb(prefix="MAIN"))
        return

    # Welcome edits (admin/superadmin)
    if data == "EDIT_WELCOME_PUBLIC":
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        context.user_data["welcome_edit"] = {"owner_id": WELCOME_PUBLIC, "who": "public", "step": "caption"}
        await q.edit_message_text("Send PUBLIC welcome caption (or type: skip):", reply_markup=kb_menu_only())
        return

    if data == "EDIT_WELCOME_SELLERMAIN":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        context.user_data["welcome_edit"] = {"owner_id": WELCOME_SELLER_MAIN, "who": "seller_main", "step": "caption"}
        await q.edit_message_text("Send SELLER-MAIN welcome caption (or type: skip):", reply_markup=kb_menu_only())
        return

    if data == "EDIT_WELCOME_ANY_SELLERSHOP":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        context.user_data["awaiting_sa_shop_welcome_sellerid"] = True
        await q.edit_message_text("Send seller_id to edit THAT seller shop welcome:", reply_markup=kb_menu_only())
        return

    if data == "SA_TOTALS":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        cur.execute("SELECT COUNT(*) AS c FROM users")
        total_users = int(cur.fetchone()["c"])
        cur.execute("SELECT COUNT(*) AS c FROM sellers")
        total_sellers = int(cur.fetchone()["c"])
        cur.execute("SELECT COUNT(*) AS c FROM deposit_requests WHERE status='pending'")
        pending_dep = int(cur.fetchone()["c"])
        await q.edit_message_text(
            f"📊 Totals\n\nUsers: {total_users}\nSellers: {total_sellers}\nPending Deposits: {pending_dep}",
            reply_markup=kb_menu_only(),
        )
        return

    # Super Admin seller controls (pick seller id, then buttons)
    if data == "SA_RESTRICT_PICK":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        context.user_data["sa_pick_seller_for"] = "restrict"
        await q.edit_message_text("Send seller_id:", reply_markup=kb_menu_only())
        return

    if data == "SA_SUB_PICK":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        context.user_data["sa_pick_seller_for"] = "sub"
        await q.edit_message_text("Send seller_id:", reply_markup=kb_menu_only())
        return

    if data == "SA_BAN_PICK":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        context.user_data["sa_pick_seller_for"] = "ban"
        await q.edit_message_text("Send seller_id:", reply_markup=kb_menu_only())
        return

    if data == "SA_EDIT_SELLER_BAL":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        context.user_data["awaiting_sa_edit_seller_bal"] = True
        await q.edit_message_text("Send: seller_id amount\nExample: 123456789 +100", reply_markup=kb_menu_only())
        return

    if data.startswith("SA_RESTRICT_DO:"):
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        _, sid_s, days_s = data.split(":")
        sid = int(sid_s)
        days = int(days_s)
        ensure_seller(sid)
        until = 0 if days == 0 else (now_ts() + days * 86400)
        cur.execute("UPDATE sellers SET restricted_until_ts=? WHERE seller_id=?", (until, sid))
        conn.commit()
        await q.edit_message_text(f"✅ Seller {sid} restriction updated.", reply_markup=kb_menu_only())
        return

    if data.startswith("SA_SUB_DO:"):
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        _, sid_s, days_s = data.split(":")
        sid = int(sid_s)
        days = int(days_s)
        ensure_seller(sid)
        new_until = set_seller_subscription(sid, days)
        await q.edit_message_text(
            f"✅ Seller {sid} subscription extended.\nUntil (UTC): {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(new_until))}",
            reply_markup=kb_menu_only(),
        )
        return

    if data.startswith("SA_BAN_DO:"):
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        _, sid_s, mode = data.split(":")
        sid = int(sid_s)
        ensure_seller(sid)
        cur.execute("UPDATE sellers SET banned=? WHERE seller_id=?", (1 if mode == "ban" else 0, sid))
        conn.commit()
        await q.edit_message_text(f"✅ Seller {sid}: {mode.upper()}", reply_markup=kb_menu_only())
        return

    # Store management (Seller + Main) buttons
    # callback patterns:
    # MAIN:CAT_ADD, MAIN:COCAT_ADD, MAIN:PROD_ADD, MAIN:PROD_DEL, MAIN:PROD_DELIVERY
    # SELLER:<id>:CAT_ADD ...
    if ":CAT_ADD" in data or ":COCAT_ADD" in data or ":PROD_ADD" in data or ":PROD_DEL" in data or ":PROD_DELIVERY" in data:
        parts = data.split(":")
        if parts[0] == "MAIN":
            if not is_admin(uid):
                await q.edit_message_text("❌ Admin only.", reply_markup=kb_menu_only())
                return
            owner_id = 0
            actor_role = "admin_main"
        elif parts[0] == "SELLER":
            owner_id = int(parts[1])
            if uid != owner_id:
                await q.edit_message_text("❌ Not allowed.", reply_markup=kb_menu_only())
                return
            ok, msg = seller_can_use(uid)
            if not ok:
                await q.edit_message_text(msg, reply_markup=kb_menu_only())
                return
            actor_role = "seller"
        else:
            await q.edit_message_text("Unknown action.", reply_markup=kb_menu_only())
            return

        action = parts[-1]  # CAT_ADD etc
        context.user_data.clear()

        if action == "CAT_ADD":
            context.user_data["cat_add"] = {"owner_id": owner_id, "step": "name", "actor_role": actor_role}
            await q.edit_message_text("Send Category name:", reply_markup=kb_menu_only())
            return

        if action == "COCAT_ADD":
            context.user_data["cocat_add"] = {"owner_id": owner_id, "step": "cat", "actor_role": actor_role}
            await q.edit_message_text("Send parent Category name:", reply_markup=kb_menu_only())
            return

        if action == "PROD_ADD":
            context.user_data["prod_add"] = {"owner_id": owner_id, "step": "line", "actor_role": actor_role}
            await q.edit_message_text(
                "Send:\nCategory | Co-Category | Name | Price\nExample:\nPUBG | UC | 325 UC | 4.50",
                reply_markup=kb_menu_only(),
            )
            return

        if action == "PROD_DEL":
            context.user_data["prod_del"] = {"owner_id": owner_id, "actor_role": actor_role}
            await q.edit_message_text("Send product_id to remove (disable).", reply_markup=kb_menu_only())
            return

        if action == "PROD_DELIVERY":
            context.user_data["prod_delivery"] = {"owner_id": owner_id, "step": "pid", "actor_role": actor_role}
            await q.edit_message_text("Send product_id. Then send Key, then Link.", reply_markup=kb_menu_only())
            return

    # unknown fallback
    await q.edit_message_text("Unknown action.", reply_markup=kb_menu_only())


# =========================================================
# Messages (flows)
# =========================================================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username or "")

    # cancel
    if update.message.text and update.message.text.strip().lower() == "cancel":
        context.user_data.clear()
        await update.message.reply_text("✅ Cancelled.", reply_markup=kb_menu_only())
        return

    # deposit flow
    dep = context.user_data.get("deposit_flow")
    if dep:
        step = dep.get("step")
        if step == "amount":
            if not update.message.text:
                await update.message.reply_text("❌ Send amount as text. Example: 10")
                return
            try:
                amount = float(update.message.text.strip())
                if amount <= 0:
                    raise ValueError
            except Exception:
                await update.message.reply_text("❌ Invalid amount. Send a number like 10 or 25.5")
                return
            context.user_data["deposit_flow"] = {"step": "photo", "amount": amount}
            await update.message.reply_text("Now send PHOTO proof (required).", reply_markup=kb_menu_only())
            return

        if step == "photo":
            if not update.message.photo:
                await update.message.reply_text("❌ Photo required. Please send PHOTO proof.", reply_markup=kb_menu_only())
                return
            amount = float(dep.get("amount") or 0)
            proof_file_id = update.message.photo[-1].file_id
            cur.execute(
                "INSERT INTO deposit_requests(user_id, amount, proof_file_id, status, created_ts) VALUES(?,?,?, 'pending', ?)",
                (uid, amount, proof_file_id, now_ts()),
            )
            conn.commit()
            dep_id = int(cur.lastrowid)
            context.user_data.clear()

            await update.message.reply_text(f"✅ Deposit request created: #{dep_id}\nAdmin will review it soon.", reply_markup=kb_menu_only())
            try:
                await context.bot.send_message(
                    chat_id=SUPER_ADMIN_ID,
                    text=f"💳 Pending deposit #{dep_id}\nUser: {uid}\nAmount: {amount:g} {CURRENCY}\n(Admin Panel -> Approve Deposits)",
                )
            except Exception:
                pass
            return

    # support send
    to_id = context.user_data.get("support_to_id")
    if to_id:
        if not update.message.text:
            await update.message.reply_text("❌ Send support message as text.", reply_markup=kb_menu_only())
            return
        msg = update.message.text.strip()
        tid = get_open_ticket(uid, to_id) or create_ticket(uid, to_id)
        add_ticket_message(tid, uid, msg)
        await update.message.reply_text("✅ Support message sent.", reply_markup=kb_menu_only())
        try:
            await context.bot.send_message(chat_id=to_id, text=f"🆘 Ticket #{tid}\nFrom: {uid}\n\n{msg}")
        except Exception:
            pass
        return

    # reply to ticket
    tr = context.user_data.get("ticket_reply")
    if tr:
        ticket_id = int(tr["ticket_id"])
        if not update.message.text:
            await update.message.reply_text("❌ Reply must be text.", reply_markup=kb_menu_only())
            return
        msg = update.message.text.strip()
        cur.execute("SELECT * FROM tickets WHERE ticket_id=? AND status='open'", (ticket_id,))
        t = cur.fetchone()
        if not t:
            context.user_data.clear()
            await update.message.reply_text("Ticket closed.", reply_markup=kb_menu_only())
            return
        if not (is_admin(uid) or int(t["to_id"]) == uid):
            context.user_data.clear()
            await update.message.reply_text("❌ Not allowed.", reply_markup=kb_menu_only())
            return

        add_ticket_message(ticket_id, uid, msg)
        context.user_data.clear()
        await update.message.reply_text("✅ Reply sent.", reply_markup=kb_menu_only())
        try:
            await context.bot.send_message(chat_id=int(t["from_id"]), text=f"💬 Reply on Ticket #{ticket_id}\n\n{msg}")
        except Exception:
            pass
        return

    # open seller shop id
    if context.user_data.get("awaiting_open_shop_id"):
        if not update.message.text or not update.message.text.strip().isdigit():
            await update.message.reply_text("❌ Send seller_id numbers only.", reply_markup=kb_menu_only())
            return
        sid = int(update.message.text.strip())
        s = get_seller(sid)
        if not s:
            await update.message.reply_text("❌ Seller not found.", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        cats = list_categories(sid)
        if not cats:
            await update.message.reply_text("No categories yet in this seller shop.", reply_markup=kb_menu_only())
            return
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"CATEGORY:{sid}:{c['name']}")] for c in cats[:30]]
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="U_PRODUCTS")])
        await update.message.reply_text("Select Category:", reply_markup=InlineKeyboardMarkup(rows))
        return

    # seller wallet
    if context.user_data.get("awaiting_seller_wallet"):
        ensure_seller(uid)
        cur.execute("UPDATE sellers SET wallet_address=? WHERE seller_id=?", (update.message.text or "", uid))
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ Seller wallet updated.", reply_markup=kb_menu_only())
        return

    # seller edit user balance
    if context.user_data.get("awaiting_seller_editbal"):
        ok, msg = seller_can_use(uid)
        if not ok:
            context.user_data.clear()
            await update.message.reply_text(msg, reply_markup=kb_menu_only())
            return
        parts = (update.message.text or "").split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: user_id amount\nExample: 123456789 +10", reply_markup=kb_menu_only())
            return
        try:
            target = int(parts[0]); amt = float(parts[1])
        except Exception:
            await update.message.reply_text("❌ Invalid values.", reply_markup=kb_menu_only())
            return
        try:
            newb = add_balance(target, amt, uid, "seller_edit", f"Edited by seller {uid}")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        await update.message.reply_text(f"✅ Updated user {target} balance.\nNew balance: {newb:.2f} {CURRENCY}", reply_markup=kb_menu_only())
        return

    # admin edit user balance
    if context.user_data.get("awaiting_admin_editbal"):
        if not is_admin(uid):
            context.user_data.clear()
            await update.message.reply_text("❌ Admin only.", reply_markup=kb_menu_only())
            return
        parts = (update.message.text or "").split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: user_id amount\nExample: 123456789 +50", reply_markup=kb_menu_only())
            return
        try:
            target = int(parts[0]); amt = float(parts[1])
        except Exception:
            await update.message.reply_text("❌ Invalid values.", reply_markup=kb_menu_only())
            return
        try:
            newb = add_balance(target, amt, uid, "admin_edit", "Edited by admin")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        await update.message.reply_text(f"✅ Updated user {target} balance.\nNew balance: {newb:.2f} {CURRENCY}", reply_markup=kb_menu_only())
        return

    # super admin edit seller balance
    if context.user_data.get("awaiting_sa_edit_seller_bal"):
        if not is_superadmin(uid):
            context.user_data.clear()
            await update.message.reply_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        parts = (update.message.text or "").split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: seller_id amount\nExample: 123456789 +100", reply_markup=kb_menu_only())
            return
        try:
            sid = int(parts[0]); amt = float(parts[1])
        except Exception:
            await update.message.reply_text("❌ Invalid values.", reply_markup=kb_menu_only())
            return
        ensure_seller(sid)
        try:
            newb = add_balance(sid, amt, uid, "sa_seller_bal", "Edited by superadmin")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}", reply_markup=kb_menu_only())
            return
        context.user_data.clear()
        await update.message.reply_text(f"✅ Updated seller {sid} balance.\nNew balance: {newb:.2f} {CURRENCY}", reply_markup=kb_menu_only())
        return

    # super admin pick seller for restrict/sub/ban (then show buttons)
    pick = context.user_data.get("sa_pick_seller_for")
    if pick:
        if not is_superadmin(uid):
            context.user_data.clear()
            await update.message.reply_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        if not update.message.text or not update.message.text.strip().isdigit():
            await update.message.reply_text("❌ Send seller_id number only.", reply_markup=kb_menu_only())
            return
        sid = int(update.message.text.strip())
        ensure_seller(sid)
        context.user_data.clear()

        if pick == "restrict":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Restrict 7 days", callback_data=f"SA_RESTRICT_DO:{sid}:7"),
                 InlineKeyboardButton("Restrict 14 days", callback_data=f"SA_RESTRICT_DO:{sid}:14")],
                [InlineKeyboardButton("Restrict 30 days", callback_data=f"SA_RESTRICT_DO:{sid}:30"),
                 InlineKeyboardButton("Remove Restriction", callback_data=f"SA_RESTRICT_DO:{sid}:0")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
            ])
            await update.message.reply_text(f"⏳ Restrict Seller {sid}", reply_markup=kb)
            return

        if pick == "sub":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("+7 days", callback_data=f"SA_SUB_DO:{sid}:7"),
                 InlineKeyboardButton("+14 days", callback_data=f"SA_SUB_DO:{sid}:14")],
                [InlineKeyboardButton("+30 days", callback_data=f"SA_SUB_DO:{sid}:30")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
            ])
            await update.message.reply_text(f"⏱ Extend Seller Subscription {sid}", reply_markup=kb)
            return

        if pick == "ban":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 Ban", callback_data=f"SA_BAN_DO:{sid}:ban"),
                 InlineKeyboardButton("✅ Unban", callback_data=f"SA_BAN_DO:{sid}:unban")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="MAIN_MENU")],
            ])
            await update.message.reply_text(f"🚫 Ban/Unban Seller {sid}", reply_markup=kb)
            return

    # welcome edits
    if context.user_data.get("awaiting_sa_shop_welcome_sellerid"):
        if not is_superadmin(uid):
            context.user_data.clear()
            await update.message.reply_text("❌ Super Admin only.", reply_markup=kb_menu_only())
            return
        if not update.message.text or not update.message.text.strip().isdigit():
            await update.message.reply_text("❌ Send seller_id number only.", reply_markup=kb_menu_only())
            return
        sid = int(update.message.text.strip())
        ensure_seller(sid)
        context.user_data.clear()
        context.user_data["welcome_edit"] = {"owner_id": sid, "who": "seller_shop", "step": "caption"}
        await update.message.reply_text("Send THAT seller shop welcome caption (or type: skip):", reply_markup=kb_menu_only())
        return

    we = context.user_data.get("welcome_edit")
    if we:
        owner_id = int(we["owner_id"])
        who = we.get("who", "")
        step = we.get("step", "")

        if step == "caption":
            cap = ""
            if update.message.text:
                cap = update.message.text.strip()
                if cap.lower() == "skip":
                    cap = ""
            else:
                await update.message.reply_text("Send caption text, or type: skip", reply_markup=kb_menu_only())
                return

            # sellers cannot add credit line to their shop welcome
            if who == "seller_shop" and seller_credit_block(cap):
                await update.message.reply_text("❌ Not allowed to add: Bot created by @RekkoOwn", reply_markup=kb_menu_only())
                return

            context.user_data["welcome_edit"] = {"owner_id": owner_id, "who": who, "step": "media", "caption": cap}
            await update.message.reply_text("Now send PHOTO or VIDEO, or type: skip (no media).", reply_markup=kb_menu_only())
            return

        if step == "media":
            caption = (we.get("caption") or "").strip()

            if update.message.text and update.message.text.strip().lower() == "skip":
                set_welcome(owner_id, "", "", caption)
                context.user_data.clear()
                await update.message.reply_text("✅ Welcome updated (text only).", reply_markup=kb_menu_only())
                return

            if update.message.photo:
                set_welcome(owner_id, "photo", update.message.photo[-1].file_id, caption)
                context.user_data.clear()
                await update.message.reply_text("✅ Welcome updated (photo).", reply_markup=kb_menu_only())
                return

            if update.message.video:
                set_welcome(owner_id, "video", update.message.video.file_id, caption)
                context.user_data.clear()
                await update.message.reply_text("✅ Welcome updated (video).", reply_markup=kb_menu_only())
                return

            await update.message.reply_text("❌ Send PHOTO/VIDEO or type: skip", reply_markup=kb_menu_only())
            return

    # ---------------- Store management flows (categories, cocategories, products, delivery) ----------------
    ca = context.user_data.get("cat_add")
    if ca:
        owner_id = int(ca["owner_id"])
        actor_role = ca.get("actor_role", "")
        step = ca.get("step", "name")

        if step == "name":
            if not update.message.text:
                await update.message.reply_text("❌ Send category name as text.", reply_markup=kb_menu_only())
                return
            name = update.message.text.strip()
            if owner_id != 0 and contains_reserved_words(name):
                await update.message.reply_text("❌ Not allowed keyword in category (seller/subscription/admin).", reply_markup=kb_menu_only())
                return
            context.user_data["cat_add"] = {"owner_id": owner_id, "actor_role": actor_role, "step": "desc", "name": name}
            await update.message.reply_text("Optional: send description text now, or type: skip", reply_markup=kb_menu_only())
            return

        if step == "desc":
            desc = ""
            if update.message.text and update.message.text.strip().lower() != "skip":
                desc = update.message.text.strip()
            context.user_data["cat_add"] = {"owner_id": owner_id, "actor_role": actor_role, "step": "media", "name": ca["name"], "desc": desc}
            await update.message.reply_text("Optional: send PHOTO/VIDEO now, or type: skip", reply_markup=kb_menu_only())
            return

        if step == "media":
            name = ca["name"]
            desc = ca.get("desc", "")
            if update.message.text and update.message.text.strip().lower() == "skip":
                upsert_category(owner_id, name, desc, "", "")
                context.user_data.clear()
                await update.message.reply_text("✅ Category saved.", reply_markup=kb_menu_only())
                return
            if update.message.photo:
                upsert_category(owner_id, name, desc, "photo", update.message.photo[-1].file_id)
                context.user_data.clear()
                await update.message.reply_text("✅ Category saved (with photo).", reply_markup=kb_menu_only())
                return
            if update.message.video:
                upsert_category(owner_id, name, desc, "video", update.message.video.file_id)
                context.user_data.clear()
                await update.message.reply_text("✅ Category saved (with video).", reply_markup=kb_menu_only())
                return
            await update.message.reply_text("❌ Send PHOTO/VIDEO or type: skip", reply_markup=kb_menu_only())
            return

    cca = context.user_data.get("cocat_add")
    if cca:
        owner_id = int(cca["owner_id"])
        step = cca.get("step", "cat")

        if step == "cat":
            if not update.message.text:
                await update.message.reply_text("❌ Send parent Category name.", reply_markup=kb_menu_only())
                return
            cat = update.message.text.strip()
            context.user_data["cocat_add"] = {"owner_id": owner_id, "step": "name", "cat": cat}
            await update.message.reply_text("Now send Co-Category name.", reply_markup=kb_menu_only())
            return

        if step == "name":
            if not update.message.text:
                await update.message.reply_text("❌ Send Co-Category name.", reply_markup=kb_menu_only())
                return
            name = update.message.text.strip()
            if owner_id != 0 and contains_reserved_words(name):
                await update.message.reply_text("❌ Not allowed keyword in co-category.", reply_markup=kb_menu_only())
                return
            context.user_data["cocat_add"] = {"owner_id": owner_id, "step": "desc", "cat": cca["cat"], "name": name}
            await update.message.reply_text("Optional: send description text now, or type: skip", reply_markup=kb_menu_only())
            return

        if step == "desc":
            desc = ""
            if update.message.text and update.message.text.strip().lower() != "skip":
                desc = update.message.text.strip()
            context.user_data["cocat_add"] = {"owner_id": owner_id, "step": "media", "cat": cca["cat"], "name": cca["name"], "desc": desc}
            await update.message.reply_text("Optional: send PHOTO/VIDEO now, or type: skip", reply_markup=kb_menu_only())
            return

        if step == "media":
            cat = cca["cat"]
            name = cca["name"]
            desc = cca.get("desc", "")
            if update.message.text and update.message.text.strip().lower() == "skip":
                upsert_cocategory(owner_id, cat, name, desc, "", "")
                context.user_data.clear()
                await update.message.reply_text("✅ Co-Category saved.", reply_markup=kb_menu_only())
                return
            if update.message.photo:
                upsert_cocategory(owner_id, cat, name, desc, "photo", update.message.photo[-1].file_id)
                context.user_data.clear()
                await update.message.reply_text("✅ Co-Category saved (with photo).", reply_markup=kb_menu_only())
                return
            if update.message.video:
                upsert_cocategory(owner_id, cat, name, desc, "video", update.message.video.file_id)
                context.user_data.clear()
                await update.message.reply_text("✅ Co-Category saved (with video).", reply_markup=kb_menu_only())
                return
            await update.message.reply_text("❌ Send PHOTO/VIDEO or type: skip", reply_markup=kb_menu_only())
            return

    pa = context.user_data.get("prod_add")
    if pa:
        owner_id = int(pa["owner_id"])
        step = pa.get("step", "line")

        if step == "line":
            if not update.message.text:
                await update.message.reply_text("❌ Send product line as text.", reply_markup=kb_menu_only())
                return
            parts = [p.strip() for p in update.message.text.split("|")]
            if len(parts) != 4:
                await update.message.reply_text("❌ Format: Category | Co-Category | Name | Price", reply_markup=kb_menu_only())
                return
            cat, cocat, name, price_s = parts
            if owner_id != 0 and (contains_reserved_words(cat) or contains_reserved_words(cocat) or contains_reserved_words(name)):
                await update.message.reply_text("❌ Not allowed keyword (seller/subscription/admin).", reply_markup=kb_menu_only())
                return
            try:
                price = float(price_s)
                if price <= 0:
                    raise ValueError
            except Exception:
                await update.message.reply_text("❌ Invalid price.", reply_markup=kb_menu_only())
                return

            pid = add_product(owner_id, cat, cocat, name, price)
            context.user_data["prod_add"] = {"owner_id": owner_id, "step": "desc", "pid": pid}
            await update.message.reply_text("Optional: send description now, or type: skip", reply_markup=kb_menu_only())
            return

        if step == "desc":
            desc = ""
            if update.message.text and update.message.text.strip().lower() != "skip":
                desc = update.message.text.strip()
            context.user_data["prod_add"] = {"owner_id": owner_id, "step": "media", "pid": pa["pid"], "desc": desc}
            await update.message.reply_text("Optional: send PHOTO/VIDEO now, or type: skip", reply_markup=kb_menu_only())
            return

        if step == "media":
            pid = int(pa["pid"])
            desc = pa.get("desc", "")
            if update.message.text and update.message.text.strip().lower() == "skip":
                update_product_meta(owner_id, pid, desc, "", "")
                context.user_data.clear()
                await update.message.reply_text(f"✅ Product saved. product_id={pid}\nNow set delivery in panel.", reply_markup=kb_menu_only())
                return
            if update.message.photo:
                update_product_meta(owner_id, pid, desc, "photo", update.message.photo[-1].file_id)
                context.user_data.clear()
                await update.message.reply_text(f"✅ Product saved (photo). product_id={pid}\nNow set delivery in panel.", reply_markup=kb_menu_only())
                return
            if update.message.video:
                update_product_meta(owner_id, pid, desc, "video", update.message.video.file_id)
                context.user_data.clear()
                await update.message.reply_text(f"✅ Product saved (video). product_id={pid}\nNow set delivery in panel.", reply_markup=kb_menu_only())
                return
            await update.message.reply_text("❌ Send PHOTO/VIDEO or type: skip", reply_markup=kb_menu_only())
            return

    pd = context.user_data.get("prod_del")
    if pd:
        owner_id = int(pd["owner_id"])
        if not update.message.text or not update.message.text.strip().isdigit():
            await update.message.reply_text("❌ Send product_id number only.", reply_markup=kb_menu_only())
            return
        pid = int(update.message.text.strip())
        deactivate_product(owner_id, pid)
        context.user_data.clear()
        await update.message.reply_text("✅ Product removed (disabled).", reply_markup=kb_menu_only())
        return

    dl = context.user_data.get("prod_delivery")
    if dl:
        owner_id = int(dl["owner_id"])
        step = dl.get("step", "pid")

        if step == "pid":
            if not update.message.text or not update.message.text.strip().isdigit():
                await update.message.reply_text("❌ Send product_id number.", reply_markup=kb_menu_only())
                return
            pid = int(update.message.text.strip())
            p = get_product(owner_id, pid)
            if not p:
                await update.message.reply_text("❌ Product not found.", reply_markup=kb_menu_only())
                return
            context.user_data["prod_delivery"] = {"owner_id": owner_id, "step": "key", "pid": pid}
            await update.message.reply_text("Send Key (or type: none)", reply_markup=kb_menu_only())
            return

        if step == "key":
            key = (update.message.text or "").strip()
            if key.lower() == "none":
                key = ""
            context.user_data["prod_delivery"] = {"owner_id": owner_id, "step": "link", "pid": dl["pid"], "key": key}
            await update.message.reply_text("Send Telegram Link (or type: none)", reply_markup=kb_menu_only())
            return

        if step == "link":
            link = (update.message.text or "").strip()
            if link.lower() == "none":
                link = ""
            pid = int(dl["pid"])
            key = dl.get("key", "")
            update_product_delivery(owner_id, pid, key, link)
            context.user_data.clear()
            await update.message.reply_text("✅ Delivery saved. Buyers receive Key + Get File button.", reply_markup=kb_menu_only())
            return

    # fallback
    await update.message.reply_text("Use /start to open the menu.", reply_markup=kb_menu_only())


# =========================================================
# Main
# =========================================================
def main():
    db_init()
    # seed welcome defaults
    get_welcome(WELCOME_PUBLIC)
    get_welcome(WELCOME_SELLER_MAIN)

    app = Application.builder().token(BOT_TOKEN or "invalid").build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))

    log.info("Bot running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
