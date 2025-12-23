import os
import sqlite3
import time
import logging
from typing import Optional, Tuple, Set

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
# ✅ Railway ENV (NO CODE EDIT)
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0").strip() or "0")  # YOU
_raw_admins = os.getenv("ADMIN_IDS", "").strip()  # comma-separated admin ids
ADMIN_IDS: Set[int] = set()
if _raw_admins:
    for x in _raw_admins.split(","):
        x = x.strip()
        if x.isdigit():
            ADMIN_IDS.add(int(x))

STORE_NAME = os.getenv("STORE_NAME", "RekkoShop").strip()
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
log = logging.getLogger("storebot")


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


# =========================================================
# Database
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

    # welcome message per owner (0=main store, else seller_id)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS welcome_messages (
        owner_id INTEGER PRIMARY KEY,
        media_type TEXT DEFAULT '',   -- photo/video or ''
        file_id TEXT DEFAULT '',
        caption TEXT DEFAULT ''
    );
    """)

    # categories & co-categories (optional media + description)
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

    # products (optional media + description + delivery)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,                -- 0=main, else seller_id
        category_name TEXT NOT NULL,
        cocategory_name TEXT NOT NULL,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        description TEXT DEFAULT '',
        media_type TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        delivery_key TEXT DEFAULT '',             -- key/code
        delivery_link TEXT DEFAULT '',            -- telegram link / file link
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

    # deposits: require proof photo
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


def seller_can_use(uid: int) -> Tuple[bool, str]:
    s = get_seller(uid)
    if s is None:
        return False, "You are not a seller."
    if int(s["banned"]) == 1:
        return False, "🚫 Your seller store is banned."
    now = now_ts()
    if int(s["restricted_until_ts"]) > now:
        return False, "⏳ Your seller store is restricted right now."
    if int(s["sub_until_ts"]) <= now:
        return False, "❗ Your seller subscription is expired. Pay again in main store."
    return True, "OK"


def tx_history(uid: int, limit: int = 10):
    cur.execute("""
        SELECT type, amount, balance_after, note, created_ts
        FROM transactions
        WHERE user_id=?
        ORDER BY tx_id DESC
        LIMIT ?
    """, (uid, limit))
    return cur.fetchall()


def get_open_ticket(from_id: int, to_id: int) -> Optional[int]:
    cur.execute(
        "SELECT ticket_id FROM tickets WHERE from_id=? AND to_id=? AND status='open' ORDER BY ticket_id DESC LIMIT 1",
        (from_id, to_id),
    )
    r = cur.fetchone()
    return int(r["ticket_id"]) if r else None


def create_ticket(from_id: int, to_id: int) -> int:
    cur.execute(
        "INSERT INTO tickets(from_id, to_id, status, created_ts) VALUES(?,?, 'open', ?)",
        (from_id, to_id, now_ts()),
    )
    tid = cur.lastrowid
    conn.commit()
    return int(tid)


def add_ticket_message(ticket_id: int, sender_id: int, message: str):
    cur.execute(
        "INSERT INTO ticket_messages(ticket_id, sender_id, message, created_ts) VALUES(?,?,?,?)",
        (ticket_id, sender_id, message, now_ts()),
    )
    conn.commit()


# =========================================================
# ✅ Sellers can't sell "seller" / subscriptions
# =========================================================
RESERVED_WORDS = [
    "seller", "become seller", "subscription", "subscribe",
    "reseller plan", "seller plan", "vip seller", "admin",
]

def contains_reserved_words(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in RESERVED_WORDS)


# =========================================================
# Welcome messages (media supported)
# =========================================================
def get_welcome(owner_id: int) -> sqlite3.Row:
    cur.execute("SELECT * FROM welcome_messages WHERE owner_id=?", (owner_id,))
    r = cur.fetchone()
    if r:
        return r
    cur.execute(
        "INSERT OR IGNORE INTO welcome_messages(owner_id, media_type, file_id, caption) VALUES(?,?,?,?)",
        (owner_id, "", "", f"Welcome to {STORE_NAME}!"),
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


# =========================================================
# Category / CoCategory / Product helpers
# =========================================================
def ensure_category(owner_id: int, name: str):
    cur.execute("INSERT OR IGNORE INTO categories(owner_id, name) VALUES(?,?)", (owner_id, name.strip()))
    conn.commit()

def ensure_cocategory(owner_id: int, category_name: str, name: str):
    cur.execute(
        "INSERT OR IGNORE INTO cocategories(owner_id, category_name, name) VALUES(?,?,?)",
        (owner_id, category_name.strip(), name.strip()),
    )
    conn.commit()

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
    delivery_key: str = "",
    delivery_link: str = "",
) -> int:
    ensure_category(owner_id, category_name)
    ensure_cocategory(owner_id, category_name, cocategory_name)
    cur.execute("""
        INSERT INTO products(owner_id, category_name, cocategory_name, name, price, description, media_type, file_id, delivery_key, delivery_link, active)
        VALUES(?,?,?,?,?,?,?,?,?,?,1)
    """, (
        owner_id,
        category_name.strip(),
        cocategory_name.strip(),
        name.strip(),
        float(price),
        description.strip(),
        media_type,
        file_id,
        delivery_key.strip(),
        delivery_link.strip(),
    ))
    conn.commit()
    return int(cur.lastrowid)

def update_product_delivery(owner_id: int, product_id: int, delivery_key: str, delivery_link: str):
    cur.execute("""
        UPDATE products SET delivery_key=?, delivery_link=?
        WHERE owner_id=? AND product_id=?
    """, (delivery_key.strip(), delivery_link.strip(), owner_id, product_id))
    conn.commit()

def update_product_meta(owner_id: int, product_id: int, description: str, media_type: str, file_id: str):
    cur.execute("""
        UPDATE products SET description=?, media_type=?, file_id=?
        WHERE owner_id=? AND product_id=?
    """, (description.strip(), media_type, file_id, owner_id, product_id))
    conn.commit()

def deactivate_product(owner_id: int, product_id: int):
    cur.execute("UPDATE products SET active=0 WHERE owner_id=? AND product_id=?", (owner_id, product_id))
    conn.commit()

def list_categories(owner_id: int):
    cur.execute("""
        SELECT name, description, media_type, file_id
        FROM categories
        WHERE owner_id=? AND active=1
        ORDER BY name
    """, (owner_id,))
    return cur.fetchall()

def list_cocategories(owner_id: int, category_name: str):
    cur.execute("""
        SELECT name, description, media_type, file_id
        FROM cocategories
        WHERE owner_id=? AND category_name=? AND active=1
        ORDER BY name
    """, (owner_id, category_name))
    return cur.fetchall()

def list_products(owner_id: int, category_name: str, cocategory_name: str):
    cur.execute("""
        SELECT product_id, name, price, description, media_type, file_id
        FROM products
        WHERE owner_id=? AND category_name=? AND cocategory_name=? AND active=1
        ORDER BY name
    """, (owner_id, category_name, cocategory_name))
    return cur.fetchall()

def get_product(owner_id: int, product_id: int):
    cur.execute("SELECT * FROM products WHERE owner_id=? AND product_id=? AND active=1", (owner_id, product_id))
    return cur.fetchone()


# =========================================================
# UI helpers
# =========================================================
def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🛒 Products", callback_data="U_PRODUCTS"),
            InlineKeyboardButton("💰 Wallet", callback_data="U_WALLET"),
        ],
        [
            InlineKeyboardButton("📜 History", callback_data="U_HISTORY"),
            InlineKeyboardButton("🆘 Support", callback_data="U_SUPPORT"),
        ],
        [
            InlineKeyboardButton("⭐ Become Seller", callback_data="U_BECOME_SELLER"),
            InlineKeyboardButton("🏪 Seller Panel", callback_data="S_PANEL"),
        ],
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="A_PANEL")])
    if is_superadmin(uid):
        rows.append([InlineKeyboardButton("👑 Super Admin Panel", callback_data="SA_PANEL")])
    return InlineKeyboardMarkup(rows)

def back_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]])

def seller_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Manage Products", callback_data="S_MANAGE_PRODUCTS"),
            InlineKeyboardButton("🏷 Manage Categories", callback_data="S_MANAGE_CATS"),
        ],
        [
            InlineKeyboardButton("💳 Wallet Address", callback_data="S_SET_WALLET"),
            InlineKeyboardButton("👤 Edit User Balance", callback_data="S_EDIT_USER_BAL"),
        ],
        [
            InlineKeyboardButton("🆘 Support Inbox", callback_data="S_TICKETS"),
            InlineKeyboardButton("🖼 Edit Welcome", callback_data="S_EDIT_WELCOME"),
        ],
        [
            InlineKeyboardButton("⭐ Pay Subscription", callback_data="U_BECOME_SELLER"),
            InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN"),
        ],
    ])

def admin_panel_kb(is_sa: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("✅ Approve Deposits", callback_data="A_DEPOSITS"),
            InlineKeyboardButton("💰 Edit User Balance", callback_data="A_EDIT_BAL"),
        ],
        [
            InlineKeyboardButton("🛒 Manage Main Products", callback_data="A_MAIN_PRODUCTS"),
            InlineKeyboardButton("🖼 Edit Main Welcome", callback_data="A_EDIT_WELCOME"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")],
    ]
    if is_sa:
        rows.insert(0, [
            InlineKeyboardButton("⏳ Restrict Seller", callback_data="SA_RESTRICT"),
            InlineKeyboardButton("🚫 Ban/Unban Seller", callback_data="SA_BAN"),
        ])
        rows.insert(1, [
            InlineKeyboardButton("⏱ Adjust Seller Sub", callback_data="SA_SUB_ADJUST"),
            InlineKeyboardButton("💰 Edit Seller Balance", callback_data="SA_EDIT_SELLER_BAL"),
        ])
    return InlineKeyboardMarkup(rows)


# =========================================================
# Welcome sender
# =========================================================
async def send_welcome_to_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE, uid: int, owner_id: int):
    w = get_welcome(owner_id)
    caption = w["caption"] or f"Welcome to {STORE_NAME}!"
    kb = main_menu_kb(uid)

    if w["media_type"] == "photo" and w["file_id"]:
        await context.bot.send_photo(chat_id=chat_id, photo=w["file_id"], caption=caption, reply_markup=kb)
    elif w["media_type"] == "video" and w["file_id"]:
        await context.bot.send_video(chat_id=chat_id, video=w["file_id"], caption=caption, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id=chat_id, text=caption, reply_markup=kb)


# =========================================================
# Commands
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    err = must_have_config()
    if err:
        await update.message.reply_text(
            f"❌ Bot is not configured: {err}\n\n"
            "Set Railway Variables:\n"
            "- BOT_TOKEN\n- SUPER_ADMIN_ID\n- USDT_TRC20\n"
            "Optional: ADMIN_IDS (comma)\n"
        )
        return

    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username or "")
    await send_welcome_to_chat(chat_id=uid, context=context, uid=uid, owner_id=0)

async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username or "")
    await update.message.reply_text("Use /start then open panels from buttons.")

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    await update.message.reply_text("🛠 Admin Panel", reply_markup=admin_panel_kb(is_sa=is_superadmin(uid)))


# =========================================================
# Button handler
# =========================================================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid, q.from_user.username or "")

    data = q.data

    # Back to main (send new welcome message)
    if data == "BACK_MAIN":
        try:
            await q.message.delete()
        except Exception:
            pass
        await send_welcome_to_chat(chat_id=uid, context=context, uid=uid, owner_id=0)
        return

    # -------------------------
    # USER: WALLET + DEPOSIT
    # -------------------------
    if data == "U_WALLET":
        u = get_user(uid)
        txt = (
            f"💰 Wallet\n\n"
            f"Balance: {float(u['balance']):.2f} {CURRENCY}\n\n"
            f"Deposit Address ({CURRENCY} TRC20):\n{USDT_TRC20}\n\n"
            "To request deposit approval, tap below.\n(You must send AMOUNT then PHOTO proof)"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Request Deposit", callback_data="U_DEP_REQ")],
            [InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]
        ])
        await q.edit_message_text(txt, reply_markup=kb)
        return

    if data == "U_DEP_REQ":
        context.user_data["deposit_flow"] = {"step": "amount"}
        await q.edit_message_text("Send deposit amount (numbers only). Example: 10 or 25.5", reply_markup=back_main_kb())
        return

    # -------------------------
    # USER: HISTORY
    # -------------------------
    if data == "U_HISTORY":
        rows = tx_history(uid, 10)
        if not rows:
            await q.edit_message_text("📜 No transactions yet.", reply_markup=back_main_kb())
            return
        text = "📜 Last 10 Transactions\n\n"
        for r in rows:
            amt = float(r["amount"])
            text += f"• {r['type']} | {amt:+g} {CURRENCY} | bal {float(r['balance_after']):.2f}\n"
            if r["note"]:
                text += f"  {r['note']}\n"
        await q.edit_message_text(text, reply_markup=back_main_kb())
        return

    # -------------------------
    # USER: SUPPORT (do not show super admin id)
    # -------------------------
    if data == "U_SUPPORT":
        u = get_user(uid)
        last_sid = int(u["last_support_target"] or 0)

        kb_rows = [[InlineKeyboardButton("👑 Contact Admin", callback_data="SUPPORT_TO:ADMIN")]]
        if last_sid != 0:
            kb_rows.append([InlineKeyboardButton("🏪 Contact Last Seller", callback_data=f"SUPPORT_TO:{last_sid}")])
        kb_rows.append([InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")])

        await q.edit_message_text(
            "🆘 Support\n\nChoose who to contact, then send your message.\nSend: cancel (to stop)",
            reply_markup=InlineKeyboardMarkup(kb_rows),
        )
        return

    if data.startswith("SUPPORT_TO:"):
        target = data.split(":", 1)[1]
        to_id = SUPER_ADMIN_ID if target == "ADMIN" else int(target)
        context.user_data["support_to_id"] = to_id
        await q.edit_message_text(
            "✉️ Send your support message now.\n(Your message will be forwarded.)\nSend: cancel (to stop)",
            reply_markup=back_main_kb(),
        )
        return

    # -------------------------
    # USER: PRODUCTS (choose store)
    # -------------------------
    if data == "U_PRODUCTS":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏬 Main Store", callback_data="SHOP:0")],
            [InlineKeyboardButton("🏪 Enter Seller Shop ID", callback_data="SHOP_ENTER_SELLER")],
            [InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]
        ])
        await q.edit_message_text("Choose a shop:", reply_markup=kb)
        return

    if data == "SHOP_ENTER_SELLER":
        context.user_data["awaiting_open_shop_id"] = True
        await q.edit_message_text("Send seller_id to open their shop (numbers only).", reply_markup=back_main_kb())
        return

    if data.startswith("SHOP:"):
        owner_id = int(data.split(":")[1])
        context.user_data["shop_owner"] = owner_id
        cats = list_categories(owner_id)
        if not cats:
            await q.edit_message_text("No categories yet in this shop.", reply_markup=back_main_kb())
            return
        kb_rows = []
        for c in cats[:30]:
            kb_rows.append([InlineKeyboardButton(c["name"], callback_data=f"CATEGORY:{owner_id}:{c['name']}")])
        kb_rows.append([InlineKeyboardButton("⬅️ Back", callback_data="U_PRODUCTS")])
        await q.edit_message_text("Select Category:", reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if data.startswith("CATEGORY:"):
        _, owner_id_s, cat = data.split(":", 2)
        owner_id = int(owner_id_s)
        cocats = list_cocategories(owner_id, cat)
        if not cocats:
            await q.edit_message_text("No co-categories yet.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"SHOP:{owner_id}")]]))
            return
        kb_rows = []
        for cc in cocats[:30]:
            kb_rows.append([InlineKeyboardButton(cc["name"], callback_data=f"COCAT:{owner_id}:{cat}:{cc['name']}")])
        kb_rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"SHOP:{owner_id}")])
        await q.edit_message_text(f"Category: {cat}\nSelect Co-Category:", reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if data.startswith("COCAT:"):
        _, owner_id_s, cat, cocat = data.split(":", 3)
        owner_id = int(owner_id_s)
        prods = list_products(owner_id, cat, cocat)
        if not prods:
            await q.edit_message_text("No products yet.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"CATEGORY:{owner_id}:{cat}")]]))
            return
        kb_rows = []
        text = f"Category: {cat}\nCo-Category: {cocat}\n\nProducts:\n"
        for p in prods[:30]:
            text += f"• [{p['product_id']}] {p['name']} — {float(p['price']):.2f} {CURRENCY}\n"
            kb_rows.append([InlineKeyboardButton(f"View #{p['product_id']}", callback_data=f"VIEWPROD:{owner_id}:{p['product_id']}")])
        kb_rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"CATEGORY:{owner_id}:{cat}")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if data.startswith("VIEWPROD:"):
        _, owner_id_s, pid_s = data.split(":")
        owner_id = int(owner_id_s)
        pid = int(pid_s)
        p = get_product(owner_id, pid)
        if not p:
            await q.edit_message_text("Product not found.", reply_markup=back_main_kb())
            return

        desc = (p["description"] or "").strip()
        caption = f"*{p['name']}*\nPrice: `{float(p['price']):.2f} {CURRENCY}`"
        if desc:
            caption += f"\n\n{desc}"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Buy", callback_data=f"BUY:{owner_id}:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"COCAT:{owner_id}:{p['category_name']}:{p['cocategory_name']}")]
        ])

        # send media if exists, otherwise edit text
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

    # -------------------------
    # BUY: deliver Key + hidden Telegram Link via "Get File"
    # -------------------------
    if data.startswith("BUY:"):
        _, owner_id_s, pid_s = data.split(":")
        owner_id = int(owner_id_s)
        pid = int(pid_s)
        p = get_product(owner_id, pid)
        if not p:
            await q.edit_message_text("❌ Product not found.", reply_markup=back_main_kb())
            return

        price = float(p["price"])
        u = get_user(uid)
        if float(u["balance"]) < price:
            await q.edit_message_text(
                f"❌ Insufficient balance.\nPrice: {price:.2f} {CURRENCY}\nYour balance: {float(u['balance']):.2f} {CURRENCY}",
                reply_markup=back_main_kb(),
            )
            return

        # Charge buyer
        add_balance(uid, -price, uid, "purchase", f"Bought {p['name']} (shop {owner_id})")

        # Credit seller if shop is seller
        if owner_id != 0:
            add_balance(owner_id, +price, uid, "sale", f"Sold {p['name']} to {uid}")
            cur.execute("UPDATE users SET last_support_target=? WHERE user_id=?", (owner_id, uid))
            conn.commit()

        delivery_key = (p["delivery_key"] or "").strip()
        delivery_link = (p["delivery_link"] or "").strip()

        # Create a purchase session so "Get File" button can reveal link
        purchase_token = f"{owner_id}:{pid}:{uid}:{now_ts()}"
        context.user_data["last_purchase_link"] = {"token": purchase_token, "link": delivery_link}

        text = f"✅ Purchase successful!\n\nProduct: {p['name']}\nPaid: {price:.2f} {CURRENCY}"
        if delivery_key:
            text += f"\n\n🔑 Key:\n{delivery_key}"
        else:
            text += "\n\n🔑 Key:\n(Owner has not set a key yet.)"

        kb_rows = []
        if delivery_link:
            kb_rows.append([InlineKeyboardButton("📁 Get File", callback_data=f"GETFILE:{purchase_token}")])
        kb_rows.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="BACK_MAIN")])

        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if data.startswith("GETFILE:"):
        token = data.split(":", 1)[1]
        lp = context.user_data.get("last_purchase_link") or {}
        if lp.get("token") != token:
            await q.answer("Link expired. Buy again or ask support.", show_alert=True)
            return
        link = (lp.get("link") or "").strip()
        if not link:
            await q.answer("No link set yet. Contact support.", show_alert=True)
            return

        # Hide link until button pressed: send link only now (not shown earlier)
        try:
            await context.bot.send_message(chat_id=uid, text=f"📁 Your File Link:\n{link}")
        except Exception:
            pass
        await q.answer("Sent ✅", show_alert=False)
        return

    # -------------------------
    # Become Seller (button text only; show price on click)
    # -------------------------
    if data == "U_BECOME_SELLER":
        u = get_user(uid)
        txt = (
            "⭐ Become Seller\n\n"
            f"Price: {SELLER_SUB_PRICE:g} {CURRENCY}\n"
            f"Duration: {SELLER_SUB_DAYS} days\n\n"
            f"Your balance: {float(u['balance']):.2f} {CURRENCY}\n\n"
            "Tap Pay to activate/extend your seller subscription."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Pay", callback_data="SELLER_PAY")],
            [InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]
        ])
        await q.edit_message_text(txt, reply_markup=kb)
        return

    if data == "SELLER_PAY":
        u = get_user(uid)
        price = float(SELLER_SUB_PRICE)
        if float(u["balance"]) < price:
            await q.edit_message_text(
                f"❌ Insufficient balance.\nNeed: {price:.2f} {CURRENCY}\nYou have: {float(u['balance']):.2f} {CURRENCY}",
                reply_markup=back_main_kb(),
            )
            return

        add_balance(uid, -price, uid, "sub", f"Seller subscription +{SELLER_SUB_DAYS} days")
        ensure_seller(uid)
        new_until = set_seller_subscription(uid, SELLER_SUB_DAYS)

        await q.edit_message_text(
            f"✅ Seller subscription updated.\n\nValid until (UTC): {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(new_until))}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏪 Seller Panel", callback_data="S_PANEL")],
                [InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]
            ]),
        )
        return

    # -------------------------
    # Seller Panel
    # -------------------------
    if data == "S_PANEL":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(
                msg,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⭐ Pay Subscription", callback_data="U_BECOME_SELLER")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]
                ]),
            )
            return
        await q.edit_message_text("🏪 Seller Panel", reply_markup=seller_panel_kb())
        return

    if data == "S_SET_WALLET":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=back_main_kb())
            return
        context.user_data["awaiting_seller_wallet"] = True
        await q.edit_message_text("Send your seller wallet address now:", reply_markup=back_main_kb())
        return

    if data == "S_EDIT_USER_BAL":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=back_main_kb())
            return
        context.user_data["awaiting_seller_editbal"] = True
        await q.edit_message_text("Send: user_id amount\nExample: 123456789 +10", reply_markup=back_main_kb())
        return

    if data == "S_EDIT_WELCOME":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=back_main_kb())
            return
        context.user_data["welcome_edit"] = {"owner_id": uid, "step": "caption"}
        await q.edit_message_text("Send welcome caption text now (or type: skip to keep text empty):", reply_markup=back_main_kb())
        return

    if data == "S_MANAGE_CATS":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=back_main_kb())
            return
        context.user_data["cat_manage_owner"] = uid
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add/Update Category", callback_data="CAT_ADD")],
            [InlineKeyboardButton("➕ Add/Update Co-Category", callback_data="COCAT_ADD")],
            [InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")],
        ])
        await q.edit_message_text("🏷 Category Management", reply_markup=kb)
        return

    if data == "CAT_ADD":
        context.user_data["awaiting_add_category"] = {"owner_id": uid, "step": "name"}
        await q.edit_message_text(
            "Send Category name.\n(Optional later: description + photo/video)",
            reply_markup=back_main_kb()
        )
        return

    if data == "COCAT_ADD":
        context.user_data["awaiting_add_cocategory"] = {"owner_id": uid, "step": "cat"}
        await q.edit_message_text(
            "Send: Category name for this Co-Category (must match existing or new).",
            reply_markup=back_main_kb()
        )
        return

    if data == "S_MANAGE_PRODUCTS":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=back_main_kb())
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Product", callback_data="PROD_ADD")],
            [InlineKeyboardButton("🗑 Remove Product", callback_data="PROD_DEL")],
            [InlineKeyboardButton("✉️ Set Delivery (Key/Link)", callback_data="PROD_DELIVERY")],
            [InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")],
        ])
        await q.edit_message_text("🛒 Product Management", reply_markup=kb)
        return

    if data == "PROD_ADD":
        context.user_data["awaiting_add_product"] = {"owner_id": uid, "step": "line"}
        await q.edit_message_text(
            "Send product in format:\n"
            "Category | Co-Category | Name | Price\n"
            "Then bot will ask optional description + optional photo/video.\n\n"
            "Example:\nPUBG | UC | 325 UC | 4.50",
            reply_markup=back_main_kb()
        )
        return

    if data == "PROD_DEL":
        context.user_data["awaiting_del_product"] = {"owner_id": uid}
        await q.edit_message_text("Send product_id to remove (disable).", reply_markup=back_main_kb())
        return

    if data == "PROD_DELIVERY":
        context.user_data["awaiting_set_delivery"] = {"owner_id": uid, "step": "pid"}
        await q.edit_message_text(
            "Send product_id to set delivery for.\nThen send:\nKey line (or 'none')\nThen send:\nTelegram Link (or 'none')",
            reply_markup=back_main_kb()
        )
        return

    # -------------------------
    # Admin / SuperAdmin Panel
    # -------------------------
    if data == "A_PANEL":
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=back_main_kb())
            return
        await q.edit_message_text("🛠 Admin Panel", reply_markup=admin_panel_kb(is_sa=is_superadmin(uid)))
        return

    if data == "SA_PANEL":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=back_main_kb())
            return
        await q.edit_message_text("👑 Super Admin Panel", reply_markup=admin_panel_kb(is_sa=True))
        return

    if data == "A_EDIT_WELCOME":
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=back_main_kb())
            return
        # main store welcome
        context.user_data["welcome_edit"] = {"owner_id": 0, "step": "caption"}
        await q.edit_message_text("Send main welcome caption text now (or type: skip):", reply_markup=back_main_kb())
        return

    if data == "A_EDIT_BAL":
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=back_main_kb())
            return
        context.user_data["awaiting_admin_editbal"] = True
        await q.edit_message_text("Send: user_id amount\nExample: 123456789 +50", reply_markup=back_main_kb())
        return

    if data == "A_DEPOSITS":
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=back_main_kb())
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
            await q.edit_message_text("✅ No pending deposits.", reply_markup=back_main_kb())
            return

        text = "✅ Pending Deposits\n\n"
        kb_rows = []
        for r in rows:
            text += f"• Dep #{r['dep_id']} | user {r['user_id']} | {float(r['amount']):g} {CURRENCY}\n"
            kb_rows.append([
                InlineKeyboardButton(f"View Proof #{r['dep_id']}", callback_data=f"DEP_VIEW:{r['dep_id']}"),
            ])
            kb_rows.append([
                InlineKeyboardButton(f"Approve #{r['dep_id']}", callback_data=f"DEP_OK:{r['dep_id']}"),
                InlineKeyboardButton(f"Reject #{r['dep_id']}", callback_data=f"DEP_NO:{r['dep_id']}"),
            ])
        kb_rows.append([InlineKeyboardButton("⬅️ Back", callback_data="A_PANEL")])
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
        try:
            await context.bot.send_photo(chat_id=uid, photo=r["proof_file_id"], caption=f"Proof for Dep #{dep_id}\nUser: {r['user_id']}\nAmount: {float(r['amount']):g} {CURRENCY}")
        except Exception:
            pass
        await q.answer("Sent proof ✅", show_alert=False)
        return

    if data.startswith("DEP_OK:") or data.startswith("DEP_NO:"):
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=back_main_kb())
            return
        dep_id = int(data.split(":")[1])
        cur.execute("SELECT * FROM deposit_requests WHERE dep_id=? AND status='pending'", (dep_id,))
        r = cur.fetchone()
        if not r:
            await q.edit_message_text("Deposit not found or already handled.", reply_markup=back_main_kb())
            return

        if data.startswith("DEP_NO:"):
            cur.execute("UPDATE deposit_requests SET status='rejected' WHERE dep_id=?", (dep_id,))
            conn.commit()
            await q.edit_message_text(f"❌ Rejected deposit #{dep_id}", reply_markup=back_main_kb())
            return

        amount = float(r["amount"])
        user_id = int(r["user_id"])
        cur.execute("UPDATE deposit_requests SET status='approved' WHERE dep_id=?", (dep_id,))
        conn.commit()
        try:
            add_balance(user_id, amount, uid, "deposit_ok", f"Deposit approved #{dep_id}")
        except Exception as e:
            await q.edit_message_text(f"❌ Failed to add balance: {e}", reply_markup=back_main_kb())
            return

        await q.edit_message_text(f"✅ Approved deposit #{dep_id} (+{amount:g} {CURRENCY})", reply_markup=back_main_kb())
        try:
            await context.bot.send_message(chat_id=user_id, text=f"✅ Your deposit #{dep_id} was approved.\nBalance added: {amount:g} {CURRENCY}")
        except Exception:
            pass
        return

    if data == "A_MAIN_PRODUCTS":
        if not is_admin(uid):
            await q.edit_message_text("❌ Admin only.", reply_markup=back_main_kb())
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Product", callback_data="A_PROD_ADD")],
            [InlineKeyboardButton("🗑 Remove Product", callback_data="A_PROD_DEL")],
            [InlineKeyboardButton("✉️ Set Delivery (Key/Link)", callback_data="A_PROD_DELIVERY")],
            [InlineKeyboardButton("⬅️ Back", callback_data="A_PANEL")],
        ])
        await q.edit_message_text("🛒 Main Store Product Management", reply_markup=kb)
        return

    if data == "A_PROD_ADD":
        context.user_data["awaiting_add_product"] = {"owner_id": 0, "step": "line", "is_admin_add": True}
        await q.edit_message_text(
            "Send product in format:\n"
            "Category | Co-Category | Name | Price\n"
            "Then bot will ask optional description + optional photo/video.\n\n"
            "Example:\nMobile Legends | Diamonds | 86 Diamonds | 2.90",
            reply_markup=back_main_kb()
        )
        return

    if data == "A_PROD_DEL":
        context.user_data["awaiting_del_product"] = {"owner_id": 0}
        await q.edit_message_text("Send product_id to remove (disable).", reply_markup=back_main_kb())
        return

    if data == "A_PROD_DELIVERY":
        context.user_data["awaiting_set_delivery"] = {"owner_id": 0, "step": "pid"}
        await q.edit_message_text(
            "Send product_id to set delivery for.\nThen send:\nKey line (or 'none')\nThen send:\nTelegram Link (or 'none')",
            reply_markup=back_main_kb()
        )
        return

    # Super Admin seller control
    if data == "SA_RESTRICT":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=back_main_kb())
            return
        context.user_data["awaiting_sa_restrict"] = True
        await q.edit_message_text("Send: seller_id days\nExample: 123456789 14", reply_markup=back_main_kb())
        return

    if data == "SA_BAN":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=back_main_kb())
            return
        context.user_data["awaiting_sa_ban"] = True
        await q.edit_message_text("Send: seller_id ban OR seller_id unban\nExample: 123456789 ban", reply_markup=back_main_kb())
        return

    if data == "SA_SUB_ADJUST":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=back_main_kb())
            return
        context.user_data["awaiting_sa_sub_adjust"] = True
        await q.edit_message_text("Send: seller_id days_to_add\nExample: 123456789 30 (adds 30 days)", reply_markup=back_main_kb())
        return

    if data == "SA_EDIT_SELLER_BAL":
        if not is_superadmin(uid):
            await q.edit_message_text("❌ Super Admin only.", reply_markup=back_main_kb())
            return
        context.user_data["awaiting_sa_edit_seller_bal"] = True
        await q.edit_message_text("Send: seller_id amount\nExample: 123456789 +100", reply_markup=back_main_kb())
        return

    # Unknown
    await q.edit_message_text("Unknown action.", reply_markup=back_main_kb())


# =========================================================
# Text + Media handler (state machine)
# =========================================================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username or "")

    # Cancel
    if update.message.text and update.message.text.strip().lower() == "cancel":
        context.user_data.clear()
        await update.message.reply_text("✅ Cancelled.")
        return

    # ---- Deposit flow: amount then photo proof
    dep = context.user_data.get("deposit_flow")
    if dep:
        step = dep.get("step")
        if step == "amount":
            if not update.message.text:
                await update.message.reply_text("❌ Send amount as text number. Example: 10")
                return
            try:
                amount = float(update.message.text.strip())
                if amount <= 0:
                    raise ValueError
            except Exception:
                await update.message.reply_text("❌ Invalid amount. Send a number like 10 or 25.5")
                return
            context.user_data["deposit_flow"] = {"step": "photo", "amount": amount}
            await update.message.reply_text("Now send PHOTO proof (screenshot). (Photo required)")
            return

        if step == "photo":
            if not update.message.photo:
                await update.message.reply_text("❌ Photo required. Please send a PHOTO proof.")
                return
            amount = float(dep.get("amount") or 0)
            proof_file_id = update.message.photo[-1].file_id  # highest res
            cur.execute(
                "INSERT INTO deposit_requests(user_id, amount, proof_file_id, status, created_ts) VALUES(?,?,?, 'pending', ?)",
                (uid, amount, proof_file_id, now_ts()),
            )
            conn.commit()
            dep_id = int(cur.lastrowid)
            context.user_data.pop("deposit_flow", None)

            await update.message.reply_text(f"✅ Deposit request created: #{dep_id}\nAdmin will review it soon.")
            # notify super admin silently
            try:
                await context.bot.send_message(
                    chat_id=SUPER_ADMIN_ID,
                    text=f"💳 Pending deposit #{dep_id}\nUser: {uid}\nAmount: {amount:g} {CURRENCY}\n(Use Admin Panel -> Approve Deposits)",
                )
            except Exception:
                pass
            return

    # ---- Open seller shop id
    if context.user_data.pop("awaiting_open_shop_id", False):
        if not update.message.text or not update.message.text.strip().isdigit():
            await update.message.reply_text("❌ Send seller_id numbers only.")
            context.user_data["awaiting_open_shop_id"] = True
            return
        sid = int(update.message.text.strip())
        # check seller exists
        s = get_seller(sid)
        if not s:
            await update.message.reply_text("❌ Seller not found.")
            return
        await update.message.reply_text("Opening shop…")
        # emulate button flow
        fake_update = Update(update.update_id, callback_query=None)
        # send as new message with categories
        cats = list_categories(sid)
        if not cats:
            await update.message.reply_text("No categories yet in this seller shop.")
            return
        kb_rows = []
        for c in cats[:30]:
            kb_rows.append([InlineKeyboardButton(c["name"], callback_data=f"CATEGORY:{sid}:{c['name']}")])
        kb_rows.append([InlineKeyboardButton("⬅️ Back", callback_data="U_PRODUCTS")])
        await update.message.reply_text("Select Category:", reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    # ---- Support message forwarding
    to_id = context.user_data.get("support_to_id")
    if to_id:
        if not update.message.text:
            await update.message.reply_text("❌ Send support message as text.")
            return
        msg = update.message.text.strip()
        tid = get_open_ticket(uid, to_id) or create_ticket(uid, to_id)
        add_ticket_message(tid, uid, msg)

        if to_id != SUPER_ADMIN_ID:
            cur.execute("UPDATE users SET last_support_target=? WHERE user_id=?", (int(to_id), uid))
            conn.commit()

        await update.message.reply_text("✅ Support message sent.")
        try:
            await context.bot.send_message(
                chat_id=to_id,
                text=f"🆘 Ticket #{tid}\nFrom: {uid}\n\n{msg}",
            )
        except Exception:
            pass
        return

    # ---- Seller wallet update
    if context.user_data.pop("awaiting_seller_wallet", False):
        ensure_seller(uid)
        cur.execute("UPDATE sellers SET wallet_address=? WHERE seller_id=?", (update.message.text or "", uid))
        conn.commit()
        await update.message.reply_text("✅ Seller wallet updated.")
        return

    # ---- Seller edit user balance
    if context.user_data.pop("awaiting_seller_editbal", False):
        ok, msg = seller_can_use(uid)
        if not ok:
            await update.message.reply_text(msg)
            return
        parts = (update.message.text or "").split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: user_id amount\nExample: 123456789 +10")
            context.user_data["awaiting_seller_editbal"] = True
            return
        try:
            target = int(parts[0]); amt = float(parts[1])
        except Exception:
            await update.message.reply_text("❌ Invalid values.")
            context.user_data["awaiting_seller_editbal"] = True
            return
        try:
            newb = add_balance(target, amt, uid, "edit", f"Edited by seller {uid}")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}")
            return
        await update.message.reply_text(f"✅ Updated user {target} balance.\nNew balance: {newb:.2f} {CURRENCY}")
        return

    # ---- Admin edit user balance
    if context.user_data.pop("awaiting_admin_editbal", False):
        if not is_admin(uid):
            await update.message.reply_text("❌ Admin only.")
            return
        parts = (update.message.text or "").split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: user_id amount\nExample: 123456789 +50")
            context.user_data["awaiting_admin_editbal"] = True
            return
        try:
            target = int(parts[0]); amt = float(parts[1])
        except Exception:
            await update.message.reply_text("❌ Invalid values.")
            context.user_data["awaiting_admin_editbal"] = True
            return
        try:
            newb = add_balance(target, amt, uid, "admin_edit", "Edited by admin")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}")
            return
        await update.message.reply_text(f"✅ Updated user {target} balance.\nNew balance: {newb:.2f} {CURRENCY}")
        return

    # ---- Super Admin: restrict
    if context.user_data.pop("awaiting_sa_restrict", False):
        if not is_superadmin(uid):
            await update.message.reply_text("❌ Super Admin only.")
            return
        parts = (update.message.text or "").split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: seller_id days\nExample: 123456789 14")
            context.user_data["awaiting_sa_restrict"] = True
            return
        try:
            sid = int(parts[0]); days = int(parts[1])
            if days <= 0:
                raise ValueError
        except Exception:
            await update.message.reply_text("❌ Invalid.")
            context.user_data["awaiting_sa_restrict"] = True
            return
        ensure_seller(sid)
        until = now_ts() + days * 86400
        cur.execute("UPDATE sellers SET restricted_until_ts=? WHERE seller_id=?", (until, sid))
        conn.commit()
        await update.message.reply_text(f"✅ Restricted seller {sid} for {days} days.")
        return

    # ---- Super Admin: ban/unban
    if context.user_data.pop("awaiting_sa_ban", False):
        if not is_superadmin(uid):
            await update.message.reply_text("❌ Super Admin only.")
            return
        parts = (update.message.text or "").split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: seller_id ban|unban")
            context.user_data["awaiting_sa_ban"] = True
            return
        try:
            sid = int(parts[0]); act = parts[1].lower()
            if act not in ("ban", "unban"):
                raise ValueError
        except Exception:
            await update.message.reply_text("❌ Use: 123 ban OR 123 unban")
            context.user_data["awaiting_sa_ban"] = True
            return
        ensure_seller(sid)
        cur.execute("UPDATE sellers SET banned=? WHERE seller_id=?", (1 if act == "ban" else 0, sid))
        conn.commit()
        await update.message.reply_text(f"✅ Seller {sid}: {act.upper()}")
        return

    # ---- Super Admin: adjust seller subscription
    if context.user_data.pop("awaiting_sa_sub_adjust", False):
        if not is_superadmin(uid):
            await update.message.reply_text("❌ Super Admin only.")
            return
        parts = (update.message.text or "").split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: seller_id days_to_add\nExample: 123 30")
            context.user_data["awaiting_sa_sub_adjust"] = True
            return
        try:
            sid = int(parts[0]); add_days = int(parts[1])
        except Exception:
            await update.message.reply_text("❌ Invalid.")
            context.user_data["awaiting_sa_sub_adjust"] = True
            return
        ensure_seller(sid)
        new_until = set_seller_subscription(sid, add_days)
        await update.message.reply_text(f"✅ Seller {sid} subscription extended.\nUntil (UTC): {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(new_until))}")
        return

    # ---- Super Admin: edit seller balance
    if context.user_data.pop("awaiting_sa_edit_seller_bal", False):
        if not is_superadmin(uid):
            await update.message.reply_text("❌ Super Admin only.")
            return
        parts = (update.message.text or "").split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: seller_id amount\nExample: 123 +100")
            context.user_data["awaiting_sa_edit_seller_bal"] = True
            return
        try:
            sid = int(parts[0]); amt = float(parts[1])
        except Exception:
            await update.message.reply_text("❌ Invalid.")
            context.user_data["awaiting_sa_edit_seller_bal"] = True
            return
        try:
            newb = add_balance(sid, amt, uid, "sa_seller_bal", "Edited seller balance by superadmin")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}")
            return
        await update.message.reply_text(f"✅ Updated seller {sid} balance.\nNew balance: {newb:.2f} {CURRENCY}")
        return

    # ---- Welcome edit (caption then optional media)
    we = context.user_data.get("welcome_edit")
    if we:
        owner_id = int(we.get("owner_id", 0))
        step = we.get("step")

        # caption step
        if step == "caption":
            if update.message.text:
                cap = update.message.text.strip()
                if cap.lower() == "skip":
                    cap = ""
                context.user_data["welcome_edit"] = {"owner_id": owner_id, "step": "media", "caption": cap}
                await update.message.reply_text("Now send PHOTO or VIDEO for welcome.\nOr type: skip (no media).")
                return
            else:
                await update.message.reply_text("Send caption text, or type: skip")
                return

        # media step
        if step == "media":
            caption = (we.get("caption") or "").strip()

            if update.message.text and update.message.text.strip().lower() == "skip":
                set_welcome(owner_id, "", "", caption)
                context.user_data.pop("welcome_edit", None)
                await update.message.reply_text("✅ Welcome updated (text only).")
                return

            if update.message.photo:
                file_id = update.message.photo[-1].file_id
                set_welcome(owner_id, "photo", file_id, caption)
                context.user_data.pop("welcome_edit", None)
                await update.message.reply_text("✅ Welcome updated (photo).")
                return

            if update.message.video:
                file_id = update.message.video.file_id
                set_welcome(owner_id, "video", file_id, caption)
                context.user_data.pop("welcome_edit", None)
                await update.message.reply_text("✅ Welcome updated (video).")
                return

            await update.message.reply_text("❌ Send PHOTO or VIDEO, or type: skip")
            return

    # ---- Add/Update Category
    ac = context.user_data.get("awaiting_add_category")
    if ac:
        owner_id = int(ac.get("owner_id"))
        step = ac.get("step")
        if step == "name":
            if not update.message.text:
                await update.message.reply_text("❌ Send category name as text.")
                return
            name = update.message.text.strip()
            if owner_id != 0 and contains_reserved_words(name):
                await update.message.reply_text("❌ Not allowed keyword in category.")
                return
            context.user_data["awaiting_add_category"] = {"owner_id": owner_id, "step": "desc", "name": name}
            await update.message.reply_text("Optional: send description text now, or type: skip")
            return
        if step == "desc":
            desc = ""
            if update.message.text and update.message.text.strip().lower() != "skip":
                desc = update.message.text.strip()
            context.user_data["awaiting_add_category"] = {"owner_id": owner_id, "step": "media", "name": ac["name"], "desc": desc}
            await update.message.reply_text("Optional: send PHOTO or VIDEO now, or type: skip")
            return
        if step == "media":
            name = ac["name"]; desc = ac.get("desc", "")
            media_type = ""; file_id = ""
            if update.message.text and update.message.text.strip().lower() == "skip":
                upsert_category(owner_id, name, desc, "", "")
                context.user_data.pop("awaiting_add_category", None)
                await update.message.reply_text("✅ Category saved.")
                return
            if update.message.photo:
                media_type = "photo"; file_id = update.message.photo[-1].file_id
            elif update.message.video:
                media_type = "video"; file_id = update.message.video.file_id
            else:
                await update.message.reply_text("❌ Send PHOTO/VIDEO or type: skip")
                return
            upsert_category(owner_id, name, desc, media_type, file_id)
            context.user_data.pop("awaiting_add_category", None)
            await update.message.reply_text("✅ Category saved (with media).")
            return

    # ---- Add/Update Co-Category
    acc = context.user_data.get("awaiting_add_cocategory")
    if acc:
        owner_id = int(acc.get("owner_id"))
        step = acc.get("step")
        if step == "cat":
            if not update.message.text:
                await update.message.reply_text("❌ Send parent Category name.")
                return
            cat = update.message.text.strip()
            context.user_data["awaiting_add_cocategory"] = {"owner_id": owner_id, "step": "name", "cat": cat}
            await update.message.reply_text("Now send Co-Category name.")
            return
        if step == "name":
            if not update.message.text:
                await update.message.reply_text("❌ Send co-category name.")
                return
            name = update.message.text.strip()
            if owner_id != 0 and contains_reserved_words(name):
                await update.message.reply_text("❌ Not allowed keyword in co-category.")
                return
            context.user_data["awaiting_add_cocategory"] = {"owner_id": owner_id, "step": "desc", "cat": acc["cat"], "name": name}
            await update.message.reply_text("Optional: send description text now, or type: skip")
            return
        if step == "desc":
            desc = ""
            if update.message.text and update.message.text.strip().lower() != "skip":
                desc = update.message.text.strip()
            context.user_data["awaiting_add_cocategory"] = {"owner_id": owner_id, "step": "media", "cat": acc["cat"], "name": acc["name"], "desc": desc}
            await update.message.reply_text("Optional: send PHOTO or VIDEO now, or type: skip")
            return
        if step == "media":
            cat = acc["cat"]; name = acc["name"]; desc = acc.get("desc", "")
            media_type = ""; file_id = ""
            if update.message.text and update.message.text.strip().lower() == "skip":
                upsert_cocategory(owner_id, cat, name, desc, "", "")
                context.user_data.pop("awaiting_add_cocategory", None)
                await update.message.reply_text("✅ Co-Category saved.")
                return
            if update.message.photo:
                media_type = "photo"; file_id = update.message.photo[-1].file_id
            elif update.message.video:
                media_type = "video"; file_id = update.message.video.file_id
            else:
                await update.message.reply_text("❌ Send PHOTO/VIDEO or type: skip")
                return
            upsert_cocategory(owner_id, cat, name, desc, media_type, file_id)
            context.user_data.pop("awaiting_add_cocategory", None)
            await update.message.reply_text("✅ Co-Category saved (with media).")
            return

    # ---- Add Product (seller or admin)
    ap = context.user_data.get("awaiting_add_product")
    if ap:
        owner_id = int(ap.get("owner_id"))
        step = ap.get("step")

        if step == "line":
            if not update.message.text:
                await update.message.reply_text("❌ Send product line as text.")
                return
            parts = [p.strip() for p in update.message.text.split("|")]
            if len(parts) != 4:
                await update.message.reply_text("❌ Format: Category | Co-Category | Name | Price")
                return
            cat, cocat, name, price_s = parts
            if owner_id != 0:
                if contains_reserved_words(cat) or contains_reserved_words(cocat) or contains_reserved_words(name):
                    await update.message.reply_text("❌ Not allowed keyword (seller/subscription/admin).")
                    return
            try:
                price = float(price_s)
                if price <= 0:
                    raise ValueError
            except Exception:
                await update.message.reply_text("❌ Invalid price.")
                return

            # seller must be active seller (super admin/admin adding main store is allowed)
            if owner_id != 0:
                ok, msg = seller_can_use(owner_id)
                if not ok:
                    await update.message.reply_text(msg)
                    context.user_data.pop("awaiting_add_product", None)
                    return

            pid = add_product(owner_id, cat, cocat, name, price)
            context.user_data["awaiting_add_product"] = {"owner_id": owner_id, "step": "desc", "pid": pid}
            await update.message.reply_text("Optional: send description text now, or type: skip")
            return

        if step == "desc":
            desc = ""
            if update.message.text and update.message.text.strip().lower() != "skip":
                desc = update.message.text.strip()
            context.user_data["awaiting_add_product"] = {"owner_id": owner_id, "step": "media", "pid": ap["pid"], "desc": desc}
            await update.message.reply_text("Optional: send PHOTO or VIDEO now, or type: skip")
            return

        if step == "media":
            pid = int(ap["pid"])
            desc = ap.get("desc", "")
            media_type = ""; file_id = ""

            if update.message.text and update.message.text.strip().lower() == "skip":
                update_product_meta(owner_id, pid, desc, "", "")
                context.user_data.pop("awaiting_add_product", None)
                await update.message.reply_text(f"✅ Product saved. (product_id={pid})\nNow set delivery: use Product Delivery button.")
                return

            if update.message.photo:
                media_type = "photo"; file_id = update.message.photo[-1].file_id
            elif update.message.video:
                media_type = "video"; file_id = update.message.video.file_id
            else:
                await update.message.reply_text("❌ Send PHOTO/VIDEO or type: skip")
                return

            update_product_meta(owner_id, pid, desc, media_type, file_id)
            context.user_data.pop("awaiting_add_product", None)
            await update.message.reply_text(f"✅ Product saved with media. (product_id={pid})\nNow set delivery: use Product Delivery button.")
            return

    # ---- Remove product
    dp = context.user_data.get("awaiting_del_product")
    if dp:
        owner_id = int(dp.get("owner_id"))
        if not update.message.text or not update.message.text.strip().isdigit():
            await update.message.reply_text("❌ Send product_id number only.")
            return
        pid = int(update.message.text.strip())
        # seller restrictions
        if owner_id != 0:
            ok, msg = seller_can_use(owner_id)
            if not ok:
                await update.message.reply_text(msg)
                context.user_data.pop("awaiting_del_product", None)
                return
        deactivate_product(owner_id, pid)
        context.user_data.pop("awaiting_del_product", None)
        await update.message.reply_text("✅ Product removed (disabled).")
        return

    # ---- Set delivery (key/link)
    sd = context.user_data.get("awaiting_set_delivery")
    if sd:
        owner_id = int(sd.get("owner_id"))
        step = sd.get("step")

        if step == "pid":
            if not update.message.text or not update.message.text.strip().isdigit():
                await update.message.reply_text("❌ Send product_id number.")
                return
            pid = int(update.message.text.strip())
            # validate product exists
            p = get_product(owner_id, pid)
            if not p:
                await update.message.reply_text("❌ Product not found.")
                return
            context.user_data["awaiting_set_delivery"] = {"owner_id": owner_id, "step": "key", "pid": pid}
            await update.message.reply_text("Send Key (or type: none)")
            return

        if step == "key":
            key = (update.message.text or "").strip()
            if key.lower() == "none":
                key = ""
            context.user_data["awaiting_set_delivery"] = {"owner_id": owner_id, "step": "link", "pid": sd["pid"], "key": key}
            await update.message.reply_text("Send Telegram Link (or type: none)")
            return

        if step == "link":
            link = (update.message.text or "").strip()
            if link.lower() == "none":
                link = ""
            pid = int(sd["pid"])
            key = sd.get("key", "")
            update_product_delivery(owner_id, pid, key, link)
            context.user_data.pop("awaiting_set_delivery", None)
            await update.message.reply_text("✅ Delivery saved. Buyers will receive Key + 'Get File' button.")
            return

    await update.message.reply_text("Use /start to open the menu.")


# =========================================================
# Main
# =========================================================
def main():
    db_init()

    # seed default main welcome if missing
    get_welcome(0)

    app = Application.builder().token(BOT_TOKEN or "invalid").build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("admin", admin))

    app.add_handler(CallbackQueryHandler(on_button))
    # accept text, photos, videos
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))

    log.info("Bot running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
