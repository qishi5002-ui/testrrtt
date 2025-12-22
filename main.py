import os
import sqlite3
import datetime
import hashlib
from typing import Optional, List

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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # YOU
DB_PATH = os.getenv("DB_PATH", "rekkoshop.db")
CURRENCY = os.getenv("CURRENCY", "USD")

USDT_TRC20_ADDRESS = os.getenv("USDT_TRC20_ADDRESS", "").strip()

PAGE_SIZE = 8

SHOP_NAME = os.getenv("SHOP_NAME", "RekkoShop").strip()
WELCOME_TEXT = os.getenv("WELCOME_TEXT", "Welcome To RekkoShop , Receive your keys instantly here").strip()
DEFAULT_BRAND = os.getenv("BRAND_TEXT", "Bot created by @RekkoOwn").strip()


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

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

def safe_username(u) -> Optional[str]:
    return (u.username or "").lower() if u.username else None

def rows(btns: List[InlineKeyboardButton], per_row: int = 2):
    return [btns[i:i+per_row] for i in range(0, len(btns), per_row)]


# ===================== DB =====================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
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
        CREATE TABLE IF NOT EXISTS wallets(
            key TEXT PRIMARY KEY,
            address TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS shop_users(
            user_id INTEGER PRIMARY KEY,
            balance_cents INTEGER NOT NULL DEFAULT 0
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS categories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS subcategories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            user_id INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL,
            photo_file_id TEXT NOT NULL,
            caption TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by INTEGER
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS support_msgs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        # default wallet address
        if not conn.execute("SELECT 1 FROM wallets WHERE key='usdt_trc20'").fetchone():
            conn.execute("INSERT INTO wallets(key,address) VALUES('usdt_trc20',?)", (USDT_TRC20_ADDRESS or None,))

        if USDT_TRC20_ADDRESS:
            conn.execute("UPDATE wallets SET address=? WHERE key='usdt_trc20'", (USDT_TRC20_ADDRESS,))


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

def ensure_shop_user(uid: int):
    with db() as conn:
        r = conn.execute("SELECT 1 FROM shop_users WHERE user_id=?", (uid,)).fetchone()
        if not r:
            conn.execute("INSERT INTO shop_users(user_id,balance_cents) VALUES(?,0)", (uid,))

def get_balance(uid: int) -> int:
    ensure_shop_user(uid)
    with db() as conn:
        r = conn.execute("SELECT balance_cents FROM shop_users WHERE user_id=?", (uid,)).fetchone()
        return int(r["balance_cents"]) if r else 0

def add_balance_delta(uid: int, delta_cents: int):
    ensure_shop_user(uid)
    with db() as conn:
        conn.execute("UPDATE shop_users SET balance_cents=balance_cents+? WHERE user_id=?", (delta_cents, uid))
        conn.execute("UPDATE shop_users SET balance_cents=0 WHERE user_id=? AND balance_cents<0", (uid,))

def set_balance_absolute(uid: int, new_bal_cents: int):
    if new_bal_cents < 0:
        new_bal_cents = 0
    ensure_shop_user(uid)
    with db() as conn:
        conn.execute("UPDATE shop_users SET balance_cents=? WHERE user_id=?", (new_bal_cents, uid))

def can_deduct(uid: int, amt: int) -> bool:
    return get_balance(uid) >= amt

def deduct(uid: int, amt: int):
    add_balance_delta(uid, -amt)


# ===================== WALLET =====================
def get_wallet_address() -> Optional[str]:
    with db() as conn:
        r = conn.execute("SELECT address FROM wallets WHERE key='usdt_trc20'").fetchone()
        if not r:
            return None
        v = r["address"]
        return v.strip() if v else None

def set_wallet_address(addr: Optional[str]):
    with db() as conn:
        conn.execute("UPDATE wallets SET address=? WHERE key='usdt_trc20'", (addr.strip() if addr else None,))


# ===================== CATALOG =====================
def list_categories(active_only=True):
    with db() as conn:
        if active_only:
            return conn.execute("SELECT * FROM categories WHERE is_active=1 ORDER BY id ASC").fetchall()
        return conn.execute("SELECT * FROM categories ORDER BY id ASC").fetchall()

def add_category(name: str):
    name = name.strip()
    if not name:
        return
    with db() as conn:
        conn.execute("INSERT INTO categories(name,is_active) VALUES(?,1)", (name,))

def toggle_category(cat_id: int):
    with db() as conn:
        r = conn.execute("SELECT is_active FROM categories WHERE id=?", (cat_id,)).fetchone()
        if not r:
            return
        conn.execute("UPDATE categories SET is_active=? WHERE id=?", (0 if r["is_active"] else 1, cat_id))

def list_subcategories(cat_id: int, active_only=True):
    with db() as conn:
        if active_only:
            return conn.execute("""
                SELECT * FROM subcategories
                WHERE category_id=? AND is_active=1
                ORDER BY id ASC
            """, (cat_id,)).fetchall()
        return conn.execute("""
            SELECT * FROM subcategories
            WHERE category_id=?
            ORDER BY id ASC
        """, (cat_id,)).fetchall()

def add_subcategory(cat_id: int, name: str):
    name = name.strip()
    if not name:
        return
    with db() as conn:
        conn.execute("INSERT INTO subcategories(category_id,name,is_active) VALUES(?,?,1)", (cat_id, name))

def toggle_subcategory(sub_id: int):
    with db() as conn:
        r = conn.execute("SELECT is_active, category_id FROM subcategories WHERE id=?", (sub_id,)).fetchone()
        if not r:
            return None
        conn.execute("UPDATE subcategories SET is_active=? WHERE id=?", (0 if r["is_active"] else 1, sub_id))
        return int(r["category_id"])

def add_product(cat_id: int, sub_id: int, name: str, up: int):
    with db() as conn:
        conn.execute("""
        INSERT INTO products(category_id,subcategory_id,name,user_price_cents,telegram_link,is_active)
        VALUES(?,?,?,?,NULL,1)
        """, (cat_id, sub_id, name.strip(), up))

def list_products_by_subcat(sub_id: int, active_only=True):
    with db() as conn:
        if active_only:
            return conn.execute("""
            SELECT p.*,
              (SELECT COUNT(*) FROM keys k WHERE k.product_id=p.id AND k.is_used=0) AS stock
            FROM products p
            WHERE p.subcategory_id=? AND p.is_active=1
            ORDER BY p.id ASC
            """, (sub_id,)).fetchall()
        return conn.execute("""
            SELECT p.*,
              (SELECT COUNT(*) FROM keys k WHERE k.product_id=p.id AND k.is_used=0) AS stock
            FROM products p
            WHERE p.subcategory_id=?
            ORDER BY p.id ASC
        """, (sub_id,)).fetchall()

def get_product(pid: int):
    with db() as conn:
        return conn.execute("""
        SELECT p.*,
          (SELECT COUNT(*) FROM keys k WHERE k.product_id=p.id AND k.is_used=0) AS stock
        FROM products p
        WHERE p.id=?
        """, (pid,)).fetchone()

def toggle_product(pid: int):
    with db() as conn:
        r = conn.execute("SELECT is_active FROM products WHERE id=?", (pid,)).fetchone()
        if not r:
            return
        conn.execute("UPDATE products SET is_active=? WHERE id=?", (0 if r["is_active"] else 1, pid))

def update_product_link(pid: int, link: Optional[str]):
    with db() as conn:
        conn.execute("UPDATE products SET telegram_link=? WHERE id=?", ((link.strip() if link else None), pid))

def update_product_price(pid: int, up: int):
    with db() as conn:
        conn.execute("UPDATE products SET user_price_cents=? WHERE id=?", (up, pid))

def add_keys(pid: int, keys: List[str]) -> int:
    keys = [k.strip() for k in keys if k.strip()]
    if not keys:
        return 0
    with db() as conn:
        conn.executemany(
            "INSERT INTO keys(product_id,key_text,is_used) VALUES(?,?,0)",
            [(pid, k) for k in keys]
        )
    return len(keys)

def take_key(pid: int, buyer: int) -> Optional[str]:
    with db() as conn:
        r = conn.execute("""
            SELECT id, key_text FROM keys
            WHERE product_id=? AND is_used=0
            ORDER BY id ASC LIMIT 1
        """, (pid,)).fetchone()
        if not r:
            return None
        conn.execute("""
            UPDATE keys SET is_used=1, used_by=?, used_at=?
            WHERE id=?
        """, (buyer, now_iso(), r["id"]))
        return r["key_text"]

# ===================== PURCHASES =====================
def add_purchase(uid: int, pid: int, pname: str, price_cents: int, key_text: str):
    with db() as conn:
        conn.execute("""
        INSERT INTO purchases(user_id,product_id,product_name,price_cents,key_text,created_at)
        VALUES(?,?,?,?,?,?)
        """, (uid, pid, pname, price_cents, key_text, now_iso()))

def list_purchases(uid: int, limit: int = 10):
    with db() as conn:
        return conn.execute("""
        SELECT id, product_id, product_name, price_cents, key_text, created_at
        FROM purchases
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """, (uid, limit)).fetchall()

def get_purchase(uid: int, purchase_id: int):
    with db() as conn:
        return conn.execute("""
        SELECT * FROM purchases
        WHERE user_id=? AND id=?
        """, (uid, purchase_id)).fetchone()


# ===================== DEPOSITS =====================
def create_deposit(uid: int, amt: int, file_id: str, caption: str) -> int:
    with db() as conn:
        conn.execute("""
        INSERT INTO deposits(user_id,amount_cents,photo_file_id,caption,status,created_at)
        VALUES(?,?,?,?, 'PENDING', ?)
        """, (uid, amt, file_id, caption, now_iso()))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

def get_deposit(dep_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()

def set_deposit_status(dep_id: int, status: str, reviewer: int):
    with db() as conn:
        conn.execute("""
        UPDATE deposits
        SET status=?, reviewed_at=?, reviewed_by=?
        WHERE id=? AND status='PENDING'
        """, (status, now_iso(), reviewer, dep_id))

def list_pending_deposits(limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
        SELECT * FROM deposits
        WHERE status='PENDING'
        ORDER BY id DESC LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()


# ===================== SUPPORT =====================
def add_support_msg(uid: int, text: str):
    with db() as conn:
        conn.execute("""
        INSERT INTO support_msgs(user_id,text,created_at)
        VALUES(?,?,?)
        """, (uid, text.strip(), now_iso()))


# ===================== ADMIN / USERS =====================
def count_users() -> int:
    with db() as conn:
        r = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return int(r["c"]) if r else 0

def list_users(limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
        SELECT u.user_id, u.username, u.first_name, u.last_name,
               COALESCE(su.balance_cents,0) AS balance_cents
        FROM users u
        LEFT JOIN shop_users su ON su.user_id = u.user_id
        ORDER BY u.user_id ASC
        LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

def all_user_ids() -> List[int]:
    with db() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
        return [int(r["user_id"]) for r in rows]


# ===================== UI HELPERS =====================
def kb_back_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]])

def kb_home(uid: int) -> InlineKeyboardMarkup:
    grid = [
        [InlineKeyboardButton("🛍️ Products", callback_data="home:products"),
         InlineKeyboardButton("💰 Wallet", callback_data="home:wallet")],
        [InlineKeyboardButton("📜 History", callback_data="home:history"),
         InlineKeyboardButton("📩 Support", callback_data="home:support")],
    ]
    if is_admin(uid):
        grid.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="adm:menu")])
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

def kb_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="adm:users:0"),
         InlineKeyboardButton("💳 Deposits", callback_data="adm:deps:0")],
        [InlineKeyboardButton("📂 Categories", callback_data="adm:cats"),
         InlineKeyboardButton("🧩 Co-Categories", callback_data="adm:subs")],
        [InlineKeyboardButton("📦 Products", callback_data="adm:products"),
         InlineKeyboardButton("🔑 Keys", callback_data="adm:keys")],
        [InlineKeyboardButton("💳 Wallet Address", callback_data="adm:walletaddr"),
         InlineKeyboardButton("📣 Broadcast", callback_data="adm:broadcast")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_deposit_inline_admin(dep_id: int) -> InlineKeyboardMarkup:
    # Buttons directly on the admin deposit screenshot message (no need to open deposits list)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"adm:depok:{dep_id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"adm:depnok:{dep_id}")],
        [InlineKeyboardButton("🗂 Open Deposits List", callback_data="adm:deps:0")]
    ])

def kb_product_view_admin(pid: int, sub_id: int, cat_id: int) -> InlineKeyboardMarkup:
    # Used so after edits, we refresh and stay on the same product screen
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Toggle Active", callback_data=f"adm:prod_toggle:{pid}:{sub_id}:{cat_id}"),
         InlineKeyboardButton("🔗 Edit Link", callback_data=f"adm:prod_link:{pid}:{sub_id}:{cat_id}")],
        [InlineKeyboardButton("💲 Edit Price", callback_data=f"adm:prod_price:{pid}:{sub_id}:{cat_id}"),
         InlineKeyboardButton("🔑 Add Keys", callback_data=f"adm:keys_for:{pid}:{sub_id}:{cat_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"adm:prod_sub:{sub_id}:{cat_id}"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def shop_home_text() -> str:
    # IMPORTANT: no wallet address shown here (your request)
    return f"{WELCOME_TEXT}\n\n— {SHOP_NAME}\n\n{DEFAULT_BRAND}"


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


# ===================== START =====================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    ensure_shop_user(uid)

    # Reset flow
    ctx.user_data["flow"] = None
    for k in ["dep_amount", "pid", "cat_id", "sub_id", "selected_user", "selected_user_page",
              "adm_dep_page", "adm_dep_id", "broadcast_text",
              "edit_pid", "edit_sub", "edit_cat"]:
        ctx.user_data.pop(k, None)

    await send_clean(update, ctx, shop_home_text(), reply_markup=kb_home(uid))


# ===================== CALLBACKS =====================
async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    upsert_user(q.from_user)

    uid = q.from_user.id
    ensure_shop_user(uid)
    data = q.data or ""

    # ---------- HOME ----------
    if data == "home:menu":
        ctx.user_data["flow"] = None
        return await q.edit_message_text(shop_home_text(), reply_markup=kb_home(uid))

    if data == "home:wallet":
        bal = get_balance(uid)
        addr = get_wallet_address()
        addr_txt = addr if addr else "⚠️ Wallet address not set yet (admin must set it)"
        txt = f"💰 Wallet\n\nBalance: {money(bal)}\n\nUSDT (TRC-20) Address:\n{addr_txt}"
        return await q.edit_message_text(txt, reply_markup=kb_wallet())

    if data == "wallet:deposit":
        addr = get_wallet_address()
        if not addr:
            return await q.edit_message_text(
                "⚠️ Deposit unavailable.\n\nAdmin has not set a wallet address yet.",
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

    if data == "home:products":
        return await q.edit_message_text("🛍️ Products", reply_markup=kb_products_root())

    if data == "prod:cats":
        cats = list_categories(active_only=True)
        if not cats:
            return await q.edit_message_text("No categories yet.", reply_markup=kb_back_home())
        btns = [InlineKeyboardButton(c["name"], callback_data=f"prod:cat:{c['id']}") for c in cats]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📂 Choose a category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("prod:cat:"):
        cat_id = int(data.split(":")[-1])
        subs = list_subcategories(cat_id, active_only=True)
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
        prods = list_products_by_subcat(sub_id, active_only=True)
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
        p = get_product(pid)
        if not p or int(p["is_active"]) != 1:
            return await q.answer("Product not available", show_alert=True)

        price = int(p["user_price_cents"])
        stock = int(p["stock"]) if p["stock"] is not None else 0
        bal = get_balance(uid)

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

        p = get_product(pid)
        if not p or int(p["is_active"]) != 1:
            return await q.answer("Product not available", show_alert=True)

        stock = int(p["stock"]) if p["stock"] is not None else 0
        if stock <= 0:
            return await q.answer("Out of stock.", show_alert=True)

        price = int(p["user_price_cents"])
        if not can_deduct(uid, price):
            return await q.answer("Not enough balance. Top up wallet.", show_alert=True)

        key_text = take_key(pid, uid)
        if not key_text:
            return await q.answer("Out of stock.", show_alert=True)

        deduct(uid, price)
        add_purchase(uid, pid, p["name"], price, key_text)

        link = (p["telegram_link"] or "").strip()
        txt = f"✅ Purchase successful!\n\n🔑 Key:\n`{key_text}`"
        if link:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Get Files", url=link),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ])
            return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")
        return await q.edit_message_text(txt + "\n\n⚠️ No file link set yet.", reply_markup=kb_back_home(), parse_mode="Markdown")

    if data == "home:history":
        purchases = list_purchases(uid, limit=10)
        if not purchases:
            return await q.edit_message_text("📜 No purchases yet.", reply_markup=kb_back_home())
        btns = [InlineKeyboardButton(f"#{r['id']} • {r['product_name']}", callback_data=f"hist:view:{r['id']}") for r in purchases]
        kb = rows(btns, 1)
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📜 Your purchases:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("hist:view:"):
        hid = int(data.split(":")[-1])
        r = get_purchase(uid, hid)
        if not r:
            return await q.answer("Not found", show_alert=True)
        txt = (
            f"🧾 Purchase #{r['id']}\n\n"
            f"Product: {r['product_name']}\n"
            f"Paid: {money(int(r['price_cents']))}\n"
            f"Date: {r['created_at']}\n\n"
            f"🔑 Key:\n`{r['key_text']}`"
        )
        p = get_product(int(r["product_id"]))
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

    if data == "home:support":
        ctx.user_data["flow"] = "support_send"
        return await q.edit_message_text("📩 Support\n\nType your message:", reply_markup=kb_back_home())

    # ---------- ADMIN ----------
    if data == "adm:menu":
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = None
        return await q.edit_message_text("🛠️ Admin Panel", reply_markup=kb_admin_menu())

    if data == "adm:broadcast":
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = "adm_broadcast_text"
        return await q.edit_message_text("📣 Broadcast\n\nSend the message you want to send to ALL users:", reply_markup=kb_admin_menu())

    if data == "adm:walletaddr":
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        addr = get_wallet_address()
        ctx.user_data["flow"] = "adm_wallet_edit"
        return await q.edit_message_text(
            "💳 Wallet Address\n\n"
            f"Current:\n{addr or 'Not set'}\n\n"
            "Send new wallet address (or send - to clear):",
            reply_markup=kb_admin_menu()
        )

    # ----------------- ADMIN: DEPOSITS LIST (optional) -----------------
    if data.startswith("adm:deps:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        deps = list_pending_deposits(PAGE_SIZE, page * PAGE_SIZE)
        if not deps:
            return await q.edit_message_text("💳 No pending deposits.", reply_markup=kb_admin_menu())

        btns = [InlineKeyboardButton(f"#{d['id']} • {d['user_id']} • {money(int(d['amount_cents']))}",
                                     callback_data=f"adm:dep:{d['id']}:{page}") for d in deps]
        kb = rows(btns, 1)
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"adm:deps:{page-1}"))
        if len(deps) == PAGE_SIZE:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"adm:deps:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:menu")])
        return await q.edit_message_text("💳 Pending deposits:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("adm:dep:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        dep_id = int(parts[2])
        page = int(parts[3])
        d = get_deposit(dep_id)
        if not d:
            return await q.answer("Not found", show_alert=True)

        caption = (
            f"💳 Deposit #{dep_id}\n"
            f"User: {d['user_id']}\n"
            f"Amount: {money(int(d['amount_cents']))}\n"
            f"Note: {d['caption'] or '-'}\n"
            f"Status: {d['status']}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"adm:depok:{dep_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"adm:depnok:{dep_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"adm:deps:{page}")]
        ])
        # show photo again with buttons
        await send_clean_text(q.message.chat_id, ctx, uid, caption, reply_markup=kb)
        try:
            await ctx.bot.send_photo(chat_id=q.message.chat_id, photo=d["photo_file_id"])
        except Exception:
            pass
        return

    # ----------------- ADMIN: DEPOSIT APPROVE/REJECT (INLINE) -----------------
    if data.startswith("adm:depok:") or data.startswith("adm:depnok:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)

        dep_id = int(data.split(":")[2])
        d = get_deposit(dep_id)
        if not d:
            return await q.answer("Deposit not found.", show_alert=True)
        if d["status"] != "PENDING":
            return await q.answer("Already processed.", show_alert=True)

        if data.startswith("adm:depok:"):
            set_deposit_status(dep_id, "APPROVED", uid)
            add_balance_delta(int(d["user_id"]), int(d["amount_cents"]))
            # Update the SAME admin message (no need to go deposits)
            try:
                await q.edit_message_caption(
                    caption=(q.message.caption or "") + "\n\n✅ APPROVED",
                    reply_markup=None
                )
            except Exception:
                try:
                    await q.edit_message_text("✅ Deposit approved.")
                except Exception:
                    pass
            # Notify user
            try:
                await ctx.bot.send_message(
                    chat_id=int(d["user_id"]),
                    text=f"✅ Your deposit #{dep_id} was approved.\nBalance added: {money(int(d['amount_cents']))}"
                )
            except Exception:
                pass
            return

        else:
            set_deposit_status(dep_id, "REJECTED", uid)
            try:
                await q.edit_message_caption(
                    caption=(q.message.caption or "") + "\n\n❌ REJECTED",
                    reply_markup=None
                )
            except Exception:
                try:
                    await q.edit_message_text("❌ Deposit rejected.")
                except Exception:
                    pass
            try:
                await ctx.bot.send_message(
                    chat_id=int(d["user_id"]),
                    text=f"❌ Your deposit #{dep_id} was rejected.\nIf this is a mistake, message Support."
                )
            except Exception:
                pass
            return

    # ----------------- ADMIN: CATEGORIES -----------------
    if data == "adm:cats":
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        cats = list_categories(active_only=False)
        btns = [InlineKeyboardButton(("✅ " if c["is_active"] else "🚫 ") + c["name"],
                                     callback_data=f"adm:cat_toggle:{c['id']}") for c in cats]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("➕ Add Category", callback_data="adm:cat_add"),
                   InlineKeyboardButton("⬅️ Back", callback_data="adm:menu")])
        return await q.edit_message_text("📂 Categories (tap to enable/disable):", reply_markup=InlineKeyboardMarkup(kb))

    if data == "adm:cat_add":
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = "adm_cat_add"
        return await q.edit_message_text("➕ Send category name:", reply_markup=kb_admin_menu())

    if data.startswith("adm:cat_toggle:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        cat_id = int(data.split(":")[-1])
        toggle_category(cat_id)
        return await q.edit_message_text("✅ Updated.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data="adm:cats"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # ----------------- ADMIN: SUBCATEGORIES -----------------
    if data == "adm:subs":
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        cats = list_categories(active_only=False)
        if not cats:
            return await q.edit_message_text("No categories yet. Add category first.", reply_markup=kb_admin_menu())
        btns = [InlineKeyboardButton(c["name"], callback_data=f"adm:subs_in:{c['id']}") for c in cats]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:menu")])
        return await q.edit_message_text("🧩 Choose category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("adm:subs_in:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        cat_id = int(data.split(":")[-1])
        subs = list_subcategories(cat_id, active_only=False)
        btns = [InlineKeyboardButton(("✅ " if s["is_active"] else "🚫 ") + s["name"],
                                     callback_data=f"adm:sub_toggle:{s['id']}:{cat_id}") for s in subs]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("➕ Add Co-Category", callback_data=f"adm:sub_add:{cat_id}"),
                   InlineKeyboardButton("⬅️ Back", callback_data="adm:subs")])
        return await q.edit_message_text("🧩 Co-categories (tap to enable/disable):", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("adm:sub_add:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        cat_id = int(data.split(":")[-1])
        ctx.user_data["flow"] = "adm_sub_add"
        ctx.user_data["cat_id"] = cat_id
        return await q.edit_message_text("➕ Send co-category name:", reply_markup=kb_admin_menu())

    if data.startswith("adm:sub_toggle:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        sub_id = int(parts[2])
        cat_id = int(parts[3])
        toggle_subcategory(sub_id)
        return await q.edit_message_text("✅ Updated.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"adm:subs_in:{cat_id}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ]))

    # ----------------- ADMIN: PRODUCTS -----------------
    if data == "adm:products":
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        cats = list_categories(active_only=False)
        if not cats:
            return await q.edit_message_text("Add a category first.", reply_markup=kb_admin_menu())
        btns = [InlineKeyboardButton(c["name"], callback_data=f"adm:prod_cat:{c['id']}") for c in cats]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:menu")])
        return await q.edit_message_text("📦 Choose category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("adm:prod_cat:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        cat_id = int(data.split(":")[-1])
        subs = list_subcategories(cat_id, active_only=False)
        if not subs:
            return await q.edit_message_text("Add a co-category first.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="adm:products"),
                 InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ]))
        btns = [InlineKeyboardButton(s["name"], callback_data=f"adm:prod_sub:{s['id']}:{cat_id}") for s in subs]
        kb = rows(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:products"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📦 Choose co-category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("adm:prod_sub:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        sub_id = int(parts[2])
        cat_id = int(parts[3])
        prods = list_products_by_subcat(sub_id, active_only=False)
        btns = [InlineKeyboardButton((("✅ " if int(p["is_active"]) == 1 else "🚫 ") + p["name"] + f" (ID {p['id']})"),
                                    callback_data=f"adm:prod_view:{p['id']}:{sub_id}:{cat_id}") for p in prods]
        kb = rows(btns, 1)
        kb.append([InlineKeyboardButton("➕ Add Product", callback_data=f"adm:prod_add:{sub_id}:{cat_id}"),
                   InlineKeyboardButton("⬅️ Back", callback_data=f"adm:prod_cat:{cat_id}")])
        return await q.edit_message_text("📦 Products:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("adm:prod_add:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        sub_id = int(parts[2])
        cat_id = int(parts[3])
        ctx.user_data["flow"] = "adm_prod_add"
        ctx.user_data["sub_id"] = sub_id
        ctx.user_data["cat_id"] = cat_id
        return await q.edit_message_text(
            "➕ Add Product\n\nSend format:\nName | user_price\n\nExample:\nPUBG Key | 10",
            reply_markup=kb_admin_menu()
        )

    if data.startswith("adm:prod_view:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        pid = int(parts[2])
        sub_id = int(parts[3])
        cat_id = int(parts[4])
        p = get_product(pid)
        if not p:
            return await q.answer("Not found", show_alert=True)

        txt = (
            f"📦 {p['name']} (ID {p['id']})\n\n"
            f"Price: {money(int(p['user_price_cents']))}\n"
            f"Stock: {int(p['stock'])}\n"
            f"Active: {'YES' if int(p['is_active'])==1 else 'NO'}\n"
            f"Link: {(p['telegram_link'] or '-').strip()}"
        )
        # store "return target" so edits can refresh SAME view
        ctx.user_data["edit_pid"] = pid
        ctx.user_data["edit_sub"] = sub_id
        ctx.user_data["edit_cat"] = cat_id
        return await q.edit_message_text(txt, reply_markup=kb_product_view_admin(pid, sub_id, cat_id))

    if data.startswith("adm:prod_toggle:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        pid = int(parts[2]); sub_id = int(parts[3]); cat_id = int(parts[4])
        toggle_product(pid)
        # stay on product view
        p = get_product(pid)
        txt = (
            f"📦 {p['name']} (ID {p['id']})\n\n"
            f"Price: {money(int(p['user_price_cents']))}\n"
            f"Stock: {int(p['stock'])}\n"
            f"Active: {'YES' if int(p['is_active'])==1 else 'NO'}\n"
            f"Link: {(p['telegram_link'] or '-').strip()}"
        )
        return await q.edit_message_text(txt, reply_markup=kb_product_view_admin(pid, sub_id, cat_id))

    if data.startswith("adm:prod_link:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        pid = int(parts[2]); sub_id = int(parts[3]); cat_id = int(parts[4])
        ctx.user_data["flow"] = "adm_prod_link"
        ctx.user_data["pid"] = pid
        ctx.user_data["sub_id"] = sub_id
        ctx.user_data["cat_id"] = cat_id
        return await q.edit_message_text("🔗 Send Telegram link (or send - to clear):", reply_markup=kb_admin_menu())

    if data.startswith("adm:prod_price:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        pid = int(parts[2]); sub_id = int(parts[3]); cat_id = int(parts[4])
        ctx.user_data["flow"] = "adm_prod_price"
        ctx.user_data["pid"] = pid
        ctx.user_data["sub_id"] = sub_id
        ctx.user_data["cat_id"] = cat_id
        return await q.edit_message_text("💲 Send new price (example 10 or 10.5):", reply_markup=kb_admin_menu())

    # Admin: Keys (button first)
    if data == "adm:keys":
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data["flow"] = None
        return await q.edit_message_text("🔑 Keys\n\nOpen a product → tap “🔑 Add Keys”.", reply_markup=kb_admin_menu())

    if data.startswith("adm:keys_for:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        pid = int(parts[2]); sub_id = int(parts[3]); cat_id = int(parts[4])
        p = get_product(pid)
        if not p:
            return await q.answer("Product not found", show_alert=True)
        ctx.user_data["flow"] = "adm_keys_add"
        ctx.user_data["pid"] = pid
        ctx.user_data["sub_id"] = sub_id
        ctx.user_data["cat_id"] = cat_id
        return await q.edit_message_text(
            f"🔑 Add Keys for: {p['name']} (ID {pid})\n\nSend keys (one per line):",
            reply_markup=kb_admin_menu()
        )

    # ----------------- ADMIN: USERS (for viewing only) -----------------
    if data.startswith("adm:users:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        total = count_users()
        rowsu = list_users(PAGE_SIZE, page * PAGE_SIZE)
        if not rowsu:
            return await q.edit_message_text("No users yet.", reply_markup=kb_admin_menu())

        btns = []
        for r in rowsu:
            uname = ("@" + r["username"]) if r["username"] else ""
            btns.append(InlineKeyboardButton(f"{r['user_id']} {uname} • {money(int(r['balance_cents']))}",
                                             callback_data=f"adm:user:{r['user_id']}:{page}"))
        kb = rows(btns, 1)

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"adm:users:{page-1}"))
        if (page + 1) * PAGE_SIZE < total:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"adm:users:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:menu")])
        return await q.edit_message_text(f"👥 Users (Total {total})", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("adm:user:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        parts = data.split(":")
        target_uid = int(parts[2])
        page = int(parts[3])

        ensure_shop_user(target_uid)
        bal = get_balance(target_uid)

        txt = f"👤 User {target_uid}\n\nBalance: {money(bal)}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"adm:users:{page}"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    return


# ===================== TEXT HANDLER =====================
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    ensure_shop_user(uid)

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
        add_support_msg(uid, text)
        try:
            await ctx.bot.send_message(chat_id=ADMIN_ID, text=f"📩 Support\nFrom: {uid}\n\n{text}")
        except Exception:
            pass
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Sent.", reply_markup=kb_home(uid))

    # Admin wallet edit
    if flow == "adm_wallet_edit":
        if not is_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(uid))
        if text == "-":
            set_wallet_address(None)
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "✅ Wallet address cleared.", reply_markup=kb_admin_menu())
        if len(text) < 10:
            return await send_clean(update, ctx, "Invalid wallet address.", reply_markup=kb_admin_menu())
        set_wallet_address(text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Wallet address updated.", reply_markup=kb_admin_menu())

    # Admin broadcast
    if flow == "adm_broadcast_text":
        if not is_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(uid))

        user_ids = all_user_ids()
        sent = 0
        failed = 0
        for tuid in user_ids:
            try:
                await ctx.bot.send_message(chat_id=tuid, text=text)
                sent += 1
            except Exception:
                failed += 1

        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, f"📣 Broadcast done.\nSent: {sent}\nFailed: {failed}", reply_markup=kb_admin_menu())

    # Admin add category
    if flow == "adm_cat_add":
        if not is_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(uid))
        add_category(text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Category added.", reply_markup=kb_admin_menu())

    # Admin add subcategory
    if flow == "adm_sub_add":
        if not is_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(uid))
        cat_id = int(ctx.user_data.get("cat_id", 0))
        if cat_id <= 0:
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Category missing.", reply_markup=kb_admin_menu())
        add_subcategory(cat_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Co-category added.", reply_markup=kb_admin_menu())

    # Admin add product
    if flow == "adm_prod_add":
        if not is_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(uid))
        if "|" not in text:
            return await send_clean(update, ctx, "Format: Name | user_price", reply_markup=kb_admin_menu())
        name, price_s = [x.strip() for x in text.split("|", 1)]
        up = to_cents(price_s)
        if not name or up is None:
            return await send_clean(update, ctx, "Invalid values.", reply_markup=kb_admin_menu())
        sub_id = int(ctx.user_data.get("sub_id", 0))
        cat_id = int(ctx.user_data.get("cat_id", 0))
        add_product(cat_id, sub_id, name, up)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Product added.", reply_markup=kb_admin_menu())

    # Admin edit product link (STAY ON PRODUCT VIEW)
    if flow == "adm_prod_link":
        if not is_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(uid))
        pid = int(ctx.user_data.get("pid", 0))
        sub_id = int(ctx.user_data.get("sub_id", 0))
        cat_id = int(ctx.user_data.get("cat_id", 0))
        if text == "-":
            update_product_link(pid, None)
        else:
            update_product_link(pid, text)
        ctx.user_data["flow"] = None

        # refresh SAME product view
        p = get_product(pid)
        txt = (
            f"📦 {p['name']} (ID {p['id']})\n\n"
            f"Price: {money(int(p['user_price_cents']))}\n"
            f"Stock: {int(p['stock'])}\n"
            f"Active: {'YES' if int(p['is_active'])==1 else 'NO'}\n"
            f"Link: {(p['telegram_link'] or '-').strip()}"
        )
        return await send_clean(update, ctx, txt, reply_markup=kb_product_view_admin(pid, sub_id, cat_id))

    # Admin edit product price (STAY ON PRODUCT VIEW)
    if flow == "adm_prod_price":
        if not is_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(uid))
        pid = int(ctx.user_data.get("pid", 0))
        sub_id = int(ctx.user_data.get("sub_id", 0))
        cat_id = int(ctx.user_data.get("cat_id", 0))
        up = to_cents(text)
        if up is None:
            return await send_clean(update, ctx, "Send amount like 10 or 10.5", reply_markup=kb_admin_menu())
        update_product_price(pid, up)
        ctx.user_data["flow"] = None

        # refresh SAME product view
        p = get_product(pid)
        txt = (
            f"📦 {p['name']} (ID {p['id']})\n\n"
            f"Price: {money(int(p['user_price_cents']))}\n"
            f"Stock: {int(p['stock'])}\n"
            f"Active: {'YES' if int(p['is_active'])==1 else 'NO'}\n"
            f"Link: {(p['telegram_link'] or '-').strip()}"
        )
        return await send_clean(update, ctx, txt, reply_markup=kb_product_view_admin(pid, sub_id, cat_id))

    # Admin add keys (STAY ON PRODUCT VIEW)
    if flow == "adm_keys_add":
        if not is_admin(uid):
            ctx.user_data["flow"] = None
            return await send_clean(update, ctx, "Not authorized.", reply_markup=kb_home(uid))
        pid = int(ctx.user_data.get("pid", 0))
        sub_id = int(ctx.user_data.get("sub_id", 0))
        cat_id = int(ctx.user_data.get("cat_id", 0))
        keys = text.splitlines()
        n = add_keys(pid, keys)
        ctx.user_data["flow"] = None

        p = get_product(pid)
        txt = (
            f"✅ Added {n} keys.\n\n"
            f"📦 {p['name']} (ID {p['id']})\n\n"
            f"Price: {money(int(p['user_price_cents']))}\n"
            f"Stock: {int(p['stock'])}\n"
            f"Active: {'YES' if int(p['is_active'])==1 else 'NO'}\n"
            f"Link: {(p['telegram_link'] or '-').strip()}"
        )
        return await send_clean(update, ctx, txt, reply_markup=kb_product_view_admin(pid, sub_id, cat_id))

    # Default ignore
    return


# ===================== PHOTO HANDLER (deposit screenshot) =====================
async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    ensure_shop_user(uid)

    if ctx.user_data.get("flow") != "dep_wait_photo":
        return

    addr = get_wallet_address()
    if not addr:
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "Deposit unavailable (wallet not set).", reply_markup=kb_home(uid))

    amt = int(ctx.user_data.get("dep_amount", 0))
    if amt <= 0:
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "Deposit amount missing. Wallet → Deposit again.", reply_markup=kb_home(uid))

    file_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "").strip()

    dep_id = create_deposit(uid, amt, file_id, caption)
    ctx.user_data["flow"] = None

    await send_clean(update, ctx, f"✅ Deposit submitted (ID #{dep_id}). Admin will review.", reply_markup=kb_home(uid))

    # ADMIN RECEIVES SCREENSHOT WITH INLINE APPROVE/REJECT (your request)
    try:
        await ctx.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=(
                f"💳 NEW DEPOSIT\n"
                f"Deposit #{dep_id}\n"
                f"User: {uid}\n"
                f"Amount: {money(amt)}\n"
                f"Note: {caption or '-'}\n"
                f"Status: PENDING"
            ),
            reply_markup=kb_deposit_inline_admin(dep_id)
        )
    except Exception:
        pass


# ===================== BOOT =====================
async def post_init(app):
    init_db()

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    if ADMIN_ID == 0:
        raise RuntimeError("ADMIN_ID missing")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_text))

    app.run_polling()

if __name__ == "__main__":
    main()
