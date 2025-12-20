import os
import sqlite3
import datetime
import hashlib
from typing import Optional, List

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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
STORE_NAME = os.getenv("STORE_NAME", "RekkoShop")
CURRENCY = os.getenv("CURRENCY", "USD")
USDT_TRC20_ADDRESS = os.getenv("USDT_TRC20_ADDRESS", "")
DB_PATH = os.getenv("DB_PATH", "rekkoshop.db")

HOME_TEXT = (
    "Welcome To RekkoShop , Receive your keys instantly here\n\n"
    "Bot created by @RekkoOwn"
)

DEPOSIT_PRESETS = [500, 1000, 2000, 5000]  # cents

# ===================== HELPERS =====================
def now_iso() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds")

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

def is_admin_id(uid: int) -> bool:
    return uid == ADMIN_ID

# ===================== DB =====================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            reseller_logged_in INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            user_price_cents INTEGER NOT NULL,
            reseller_price_cents INTEGER NOT NULL,
            channel_link TEXT,
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
        CREATE TABLE IF NOT EXISTS deposits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL,
            photo_file_id TEXT NOT NULL,
            caption TEXT,
            status TEXT NOT NULL,            -- PENDING / APPROVED / REJECTED
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by INTEGER
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS resellers(
            user_id INTEGER PRIMARY KEY,      -- must exist in users
            tg_username TEXT,
            login_username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """)

def upsert_user(u):
    uname = (u.username or "").lower() if u.username else None
    with db() as conn:
        row = conn.execute("SELECT user_id FROM users WHERE user_id=?", (u.id,)).fetchone()
        if row:
            conn.execute("""
                UPDATE users
                SET username=?, first_name=?, last_name=?, updated_at=?
                WHERE user_id=?
            """, (uname, u.first_name, u.last_name, now_iso(), u.id))
        else:
            conn.execute("""
                INSERT INTO users(user_id, username, first_name, last_name, balance_cents, reseller_logged_in, created_at, updated_at)
                VALUES(?,?,?,?,0,0,?,?)
            """, (u.id, uname, u.first_name, u.last_name, now_iso(), now_iso()))

def get_user(uid: int):
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

def set_logged_in(uid: int, flag: bool):
    with db() as conn:
        conn.execute("UPDATE users SET reseller_logged_in=? WHERE user_id=?", (1 if flag else 0, uid))

def add_balance(uid: int, delta: int):
    with db() as conn:
        conn.execute("UPDATE users SET balance_cents = balance_cents + ? WHERE user_id=?", (delta, uid))

def set_balance(uid: int, new_bal: int):
    with db() as conn:
        conn.execute("UPDATE users SET balance_cents=? WHERE user_id=?", (new_bal, uid))

def can_deduct(uid: int, amount: int) -> bool:
    with db() as conn:
        bal = conn.execute("SELECT balance_cents FROM users WHERE user_id=?", (uid,)).fetchone()["balance_cents"]
        return bal >= amount

def deduct(uid: int, amount: int):
    with db() as conn:
        conn.execute("UPDATE users SET balance_cents = balance_cents - ? WHERE user_id=?", (amount, uid))

def total_users() -> int:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

def list_users(limit=10, offset=0):
    with db() as conn:
        return conn.execute("""
            SELECT user_id, username, first_name, last_name, balance_cents
            FROM users
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

def list_cats(active_only=True):
    with db() as conn:
        if active_only:
            return conn.execute("SELECT * FROM categories WHERE is_active=1 ORDER BY id ASC").fetchall()
        return conn.execute("SELECT * FROM categories ORDER BY id ASC").fetchall()

def add_cat(name: str):
    with db() as conn:
        conn.execute("INSERT INTO categories(name,is_active) VALUES(?,1)", (name.strip(),))

def toggle_cat(cat_id: int):
    with db() as conn:
        row = conn.execute("SELECT is_active FROM categories WHERE id=?", (cat_id,)).fetchone()
        if not row: return
        conn.execute("UPDATE categories SET is_active=? WHERE id=?", (0 if row["is_active"] else 1, cat_id))

def add_product(cat_id: int, name: str, user_price: int, reseller_price: int, channel_link: str):
    with db() as conn:
        conn.execute("""
            INSERT INTO products(category_id,name,user_price_cents,reseller_price_cents,channel_link,is_active)
            VALUES(?,?,?,?,?,1)
        """, (cat_id, name.strip(), user_price, reseller_price, channel_link.strip() if channel_link else None))

def list_products_by_cat(cat_id: int):
    with db() as conn:
        return conn.execute("""
            SELECT p.*,
              (SELECT COUNT(*) FROM keys k WHERE k.product_id=p.id AND k.is_used=0) AS stock
            FROM products p
            WHERE p.category_id=? AND p.is_active=1
            ORDER BY p.id ASC
        """, (cat_id,)).fetchall()

def list_products_all():
    with db() as conn:
        return conn.execute("""
            SELECT p.*,
              (SELECT COUNT(*) FROM keys k WHERE k.product_id=p.id AND k.is_used=0) AS stock
            FROM products p
            ORDER BY p.id DESC
        """).fetchall()

def get_product(pid: int):
    with db() as conn:
        return conn.execute("""
            SELECT p.*,
              (SELECT COUNT(*) FROM keys k WHERE k.product_id=p.id AND k.is_used=0) AS stock
            FROM products p WHERE p.id=?
        """, (pid,)).fetchone()

def toggle_product(pid: int):
    with db() as conn:
        row = conn.execute("SELECT is_active FROM products WHERE id=?", (pid,)).fetchone()
        if not row: return
        conn.execute("UPDATE products SET is_active=? WHERE id=?", (0 if row["is_active"] else 1, pid))

def update_product_channel(pid: int, link: str):
    with db() as conn:
        conn.execute("UPDATE products SET channel_link=? WHERE id=?", (link.strip() if link else None, pid))

def update_product_prices(pid: int, up: int, rp: int):
    with db() as conn:
        conn.execute("UPDATE products SET user_price_cents=?, reseller_price_cents=? WHERE id=?", (up, rp, pid))

def add_keys(pid: int, keys: List[str]) -> int:
    keys = [k.strip() for k in keys if k.strip()]
    if not keys:
        return 0
    with db() as conn:
        conn.executemany("INSERT INTO keys(product_id,key_text,is_used) VALUES(?,?,0)", [(pid, k) for k in keys])
    return len(keys)

def take_key(pid: int, buyer_id: int) -> Optional[str]:
    with db() as conn:
        row = conn.execute("""
            SELECT id, key_text FROM keys
            WHERE product_id=? AND is_used=0
            ORDER BY id ASC LIMIT 1
        """, (pid,)).fetchone()
        if not row:
            return None
        conn.execute("""
            UPDATE keys SET is_used=1, used_by=?, used_at=? WHERE id=?
        """, (buyer_id, now_iso(), row["id"]))
        return row["key_text"]

def create_deposit(uid: int, amount: int, file_id: str, caption: str) -> int:
    with db() as conn:
        conn.execute("""
            INSERT INTO deposits(user_id,amount_cents,photo_file_id,caption,status,created_at)
            VALUES(?,?,?,?, 'PENDING', ?)
        """, (uid, amount, file_id, caption, now_iso()))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

def pending_deposits(limit=10, offset=0):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM deposits
            WHERE status='PENDING'
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

def get_deposit(dep_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()

def set_deposit_status(dep_id: int, status: str, reviewer: int):
    with db() as conn:
        conn.execute("""
            UPDATE deposits SET status=?, reviewed_at=?, reviewed_by=?
            WHERE id=? AND status='PENDING'
        """, (status, now_iso(), reviewer, dep_id))

def reseller_by_login(login: str):
    with db() as conn:
        return conn.execute("SELECT * FROM resellers WHERE login_username=?", (login.lower().strip(),)).fetchone()

def reseller_by_uid(uid: int):
    with db() as conn:
        return conn.execute("SELECT * FROM resellers WHERE user_id=?", (uid,)).fetchone()

def add_reseller_by_tg_username(tg_username: str, login: str, password: str) -> (bool, str):
    tg = tg_username.strip().lstrip("@").lower()
    login = login.strip().lower()
    with db() as conn:
        u = conn.execute("SELECT user_id FROM users WHERE username=?", (tg,)).fetchone()
        if not u:
            return False, "User must press /start once first."
        uid = int(u["user_id"])
        if conn.execute("SELECT 1 FROM resellers WHERE user_id=?", (uid,)).fetchone():
            return False, "Already a reseller."
        conn.execute("""
            INSERT INTO resellers(user_id,tg_username,login_username,password_hash,is_active,created_at)
            VALUES(?,?,?,?,1,?)
        """, (uid, tg, login, sha256(password), now_iso()))
    return True, "Reseller added."

def toggle_reseller(uid: int):
    with db() as conn:
        row = conn.execute("SELECT is_active FROM resellers WHERE user_id=?", (uid,)).fetchone()
        if not row: return
        conn.execute("UPDATE resellers SET is_active=? WHERE user_id=?", (0 if row["is_active"] else 1, uid))

def set_reseller_password(uid: int, pw: str):
    with db() as conn:
        conn.execute("UPDATE resellers SET password_hash=? WHERE user_id=?", (sha256(pw), uid))

def list_resellers(limit=10, offset=0):
    with db() as conn:
        return conn.execute("""
            SELECT r.user_id, r.tg_username, r.login_username, r.is_active,
                   u.balance_cents, u.username, u.first_name, u.last_name
            FROM resellers r
            LEFT JOIN users u ON u.user_id=r.user_id
            ORDER BY r.created_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

# ===================== UI =====================
def kb_home(uid: int) -> InlineKeyboardMarkup:
    u = get_user(uid)
    reseller_logged = bool(u and u["reseller_logged_in"])
    rows = [
        [InlineKeyboardButton("🛍️ Products", callback_data="home:products")],
        [InlineKeyboardButton("💰 Wallet", callback_data="home:wallet")],
        [InlineKeyboardButton("📩 Support", callback_data="home:support")],
        [InlineKeyboardButton("🔐 Reseller Login", callback_data="res:login")],
    ]
    if reseller_logged:
        rows.insert(0, [InlineKeyboardButton("🧑‍💻 Reseller Mode: ON (Logout)", callback_data="res:logout")])
    if is_admin_id(uid):
        rows.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="admin:menu")])
    return InlineKeyboardMarkup(rows)

def kb_mainmenu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]])

def kb_wallet(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Deposit", callback_data="wallet:deposit")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_deposit_amounts() -> InlineKeyboardMarkup:
    rows = []
    r = []
    for a in DEPOSIT_PRESETS:
        r.append(InlineKeyboardButton(f"💵 {money(a)}", callback_data=f"dep:amt:{a}"))
        if len(r) == 2:
            rows.append(r); r = []
    if r: rows.append(r)
    rows.append([InlineKeyboardButton("✍️ Custom Amount", callback_data="dep:custom")])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
    return InlineKeyboardMarkup(rows)

def kb_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="admin:users:0")],
        [InlineKeyboardButton("💳 Deposits", callback_data="admin:deps:0")],
        [InlineKeyboardButton("📂 Categories", callback_data="admin:cats")],
        [InlineKeyboardButton("📦 Products", callback_data="admin:products")],
        [InlineKeyboardButton("🔑 Keys", callback_data="admin:keys")],
        [InlineKeyboardButton("🧑‍💼 Resellers", callback_data="admin:resellers:0")],
        [InlineKeyboardButton("📊 Stats", callback_data="admin:stats")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")],
    ])

# ===================== SUPPORT FORWARD + REPLY =====================
def user_header(u, chat_id: int) -> str:
    uname = f"@{u.username}" if u.username else "(no username)"
    name = f"{u.first_name or ''} {u.last_name or ''}".strip()
    return f"👤 {name} {uname}\n🆔 Chat ID: {chat_id}\n\n"

async def forward_user_to_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    if update.effective_user.id == ADMIN_ID:
        return
    # don't forward if user is in a flow (deposit/login/admin input)
    if ctx.user_data.get("flow"):
        return
    text = update.message.text or ""
    header = user_header(update.effective_user, update.effective_chat.id)
    await ctx.bot.send_message(chat_id=ADMIN_ID, text=header + "💬 Message:\n" + text)

async def admin_reply_by_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not update.message.reply_to_message:
        return
    original = update.message.reply_to_message.text or ""
    if "Chat ID:" not in original:
        return
    try:
        chat_id_str = original.split("Chat ID:")[1].split("\n")[0].strip()
        target = int(chat_id_str)
    except Exception:
        return
    await ctx.bot.send_message(chat_id=target, text=update.message.text)

# ===================== MAIN HANDLERS =====================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    ctx.user_data.clear()
    await update.message.reply_text(HOME_TEXT, reply_markup=kb_home(update.effective_user.id))

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    upsert_user(q.from_user)

    data = q.data or ""

    # HOME
    if data == "home:menu":
        ctx.user_data.clear()
        return await q.edit_message_text(HOME_TEXT, reply_markup=kb_home(uid))

    if data == "home:wallet":
        u = get_user(uid)
        text = (
            f"💰 Wallet\n\n"
            f"Balance: {money(u['balance_cents'])}\n\n"
            f"USDT (TRC-20) Address:\n{USDT_TRC20_ADDRESS}"
        )
        return await q.edit_message_text(text, reply_markup=kb_wallet(uid))

    if data == "wallet:deposit":
        # deposit is inside wallet
        ctx.user_data["flow"] = "dep_choose_amount"
        return await q.edit_message_text(
            "💳 Deposit\n\nChoose an amount:",
            reply_markup=kb_deposit_amounts()
        )

    if data == "home:support":
        ctx.user_data.clear()
        return await q.edit_message_text(
            "📩 Support\n\nType your message here. Admin will reply.",
            reply_markup=kb_mainmenu()
        )

    # PRODUCTS
    if data == "home:products":
        cats = list_cats(active_only=True)
        if not cats:
            return await q.edit_message_text("No categories yet.", reply_markup=kb_mainmenu())
        kb = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📂 Choose a category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("shop:cat:"):
        cat_id = int(data.split(":")[-1])
        prods = list_products_by_cat(cat_id)
        if not prods:
            return await q.edit_message_text("No products in this category yet.", reply_markup=kb_mainmenu())
        kb = []
        for p in prods:
            kb.append([InlineKeyboardButton(f"{p['name']} (Stock: {p['stock']})", callback_data=f"shop:prod:{p['id']}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="home:products")])
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("🛍️ Choose product:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("shop:prod:"):
        pid = int(data.split(":")[-1])
        p = get_product(pid)
        if not p or p["is_active"] != 1:
            return await q.edit_message_text("Product not available.", reply_markup=kb_mainmenu())

        u = get_user(uid)
        reseller = bool(u["reseller_logged_in"]) and (reseller_by_uid(uid) is not None)
        price = p["reseller_price_cents"] if reseller else p["user_price_cents"]

        text = (
            f"📌 {p['name']}\n"
            f"Price: {money(price)}\n"
            f"Stock: {p['stock']}\n\n"
            f"Buy using your wallet balance."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Buy", callback_data=f"buy:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="home:products")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")],
        ])
        return await q.edit_message_text(text, reply_markup=kb)

    if data.startswith("buy:"):
        pid = int(data.split(":")[-1])
        p = get_product(pid)
        if not p or p["is_active"] != 1:
            return await q.answer("Not available", show_alert=True)

        u = get_user(uid)
        reseller = bool(u["reseller_logged_in"]) and (reseller_by_uid(uid) is not None)
        price = p["reseller_price_cents"] if reseller else p["user_price_cents"]

        if p["stock"] <= 0:
            return await q.answer("Out of stock", show_alert=True)
        if not can_deduct(uid, price):
            return await q.answer("Not enough balance", show_alert=True)

        key = take_key(pid, uid)
        if not key:
            return await q.answer("Out of stock", show_alert=True)

        deduct(uid, price)
        channel = p["channel_link"] or "(No channel link set)"

        msg = f"✅ Purchase Successful!\n\n🔑 Key:\n{key}\n\n🔗 Channel:\n{channel}"
        return await q.edit_message_text(msg, reply_markup=kb_mainmenu())

    # DEPOSIT (buttons)
    if data.startswith("dep:amt:"):
        amt = int(data.split(":")[-1])
        ctx.user_data["flow"] = "dep_wait_photo"
        ctx.user_data["dep_amount"] = amt
        return await q.edit_message_text(
            f"✅ Amount set: {money(amt)}\n\nNow send your payment screenshot (photo).",
            reply_markup=kb_mainmenu()
        )

    if data == "dep:custom":
        ctx.user_data["flow"] = "dep_custom_amount"
        return await q.edit_message_text(
            "✍️ Send the amount you paid (example: 10 or 10.5).",
            reply_markup=kb_mainmenu()
        )

    # RESELLER
    if data == "res:login":
        ctx.user_data["flow"] = "res_login_username"
        return await q.edit_message_text("🔐 Reseller Login\n\nSend your login username:", reply_markup=kb_mainmenu())

    if data == "res:logout":
        set_logged_in(uid, False)
        ctx.user_data.clear()
        return await q.edit_message_text("✅ Logged out.", reply_markup=kb_home(uid))

    # ADMIN
    if data == "admin:menu":
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data.clear()
        return await q.edit_message_text("🛠️ Admin Panel", reply_markup=kb_admin_menu())

    if data == "admin:stats":
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        return await q.edit_message_text(f"📊 Stats\n\nTotal users: {total_users()}", reply_markup=kb_admin_menu())

    # Admin: Users list
    if data.startswith("admin:users:"):
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        limit = 8
        offset = page * limit
        rows = list_users(limit=limit, offset=offset)
        if not rows:
            return await q.edit_message_text("No users.", reply_markup=kb_admin_menu())

        kb = []
        for r in rows:
            name = (r["username"] or "").strip()
            title = f"@{name}" if name else f"{(r['first_name'] or '')}".strip() or "User"
            kb.append([InlineKeyboardButton(f"{title} — {money(r['balance_cents'])}", callback_data=f"admin:user:{r['user_id']}")])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:users:{page-1}"))
        if len(rows) == limit:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin:users:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:menu")])
        return await q.edit_message_text("👥 Users (tap one):", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("admin:user:"):
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        target = int(data.split(":")[-1])
        tu = get_user(target)
        if not tu:
            return await q.answer("User not found", show_alert=True)

        uname = f"@{tu['username']}" if tu["username"] else "(no username)"
        text = f"👤 User\nID: {tu['user_id']}\nUsername: {uname}\nBalance: {money(tu['balance_cents'])}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📨 Reply", callback_data=f"admin:reply:{target}")],
            [InlineKeyboardButton("➕ Add Balance", callback_data=f"admin:baladd:{target}")],
            [InlineKeyboardButton("➖ Deduct Balance", callback_data=f"admin:baldec:{target}")],
            [InlineKeyboardButton("✍️ Set Balance", callback_data=f"admin:balset:{target}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:users:0")],
        ])
        return await q.edit_message_text(text, reply_markup=kb)

    if data.startswith("admin:reply:"):
        target = int(data.split(":")[-1])
        ctx.user_data["flow"] = "admin_reply_user"
        ctx.user_data["target_user"] = target
        return await q.edit_message_text(f"📨 Reply\n\nSend message to user ID {target}:", reply_markup=kb_admin_menu())

    if data.startswith("admin:baladd:"):
        target = int(data.split(":")[-1])
        ctx.user_data["flow"] = "admin_bal_add"
        ctx.user_data["target_user"] = target
        return await q.edit_message_text("➕ Add Balance\n\nSend amount (example 10 or 10.5):", reply_markup=kb_admin_menu())

    if data.startswith("admin:baldec:"):
        target = int(data.split(":")[-1])
        ctx.user_data["flow"] = "admin_bal_dec"
        ctx.user_data["target_user"] = target
        return await q.edit_message_text("➖ Deduct Balance\n\nSend amount:", reply_markup=kb_admin_menu())

    if data.startswith("admin:balset:"):
        target = int(data.split(":")[-1])
        ctx.user_data["flow"] = "admin_bal_set"
        ctx.user_data["target_user"] = target
        return await q.edit_message_text("✍️ Set Balance\n\nSend new balance amount:", reply_markup=kb_admin_menu())

    # Admin: Deposits
    if data.startswith("admin:deps:"):
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        limit = 6
        offset = page * limit
        rows = pending_deposits(limit=limit, offset=offset)
        if not rows:
            return await q.edit_message_text("No pending deposits.", reply_markup=kb_admin_menu())
        kb = []
        for d in rows:
            kb.append([InlineKeyboardButton(f"#{d['id']} User:{d['user_id']} {money(d['amount_cents'])}", callback_data=f"admin:dep:{d['id']}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:deps:{page-1}"))
        if len(rows) == limit:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin:deps:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:menu")])
        return await q.edit_message_text("💳 Pending Deposits (tap one):", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("admin:dep:"):
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        dep_id = int(data.split(":")[-1])
        d = get_deposit(dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Not found", show_alert=True)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"admin:depok:{dep_id}")],
            [InlineKeyboardButton("❌ Reject", callback_data=f"admin:depnok:{dep_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:deps:0")],
        ])
        # show photo to admin
        await ctx.bot.send_photo(
            chat_id=uid,
            photo=d["photo_file_id"],
            caption=f"Deposit #{dep_id}\nUser: {d['user_id']}\nAmount: {money(d['amount_cents'])}\nCaption: {d['caption'] or '-'}"
        )
        return await q.edit_message_text("Choose action:", reply_markup=kb)

    if data.startswith("admin:depok:"):
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        dep_id = int(data.split(":")[-1])
        d = get_deposit(dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Not found", show_alert=True)
        set_deposit_status(dep_id, "APPROVED", uid)
        add_balance(d["user_id"], d["amount_cents"])
        await ctx.bot.send_message(chat_id=d["user_id"], text=f"✅ Deposit approved: {money(d['amount_cents'])}")
        return await q.edit_message_text("✅ Approved.", reply_markup=kb_admin_menu())

    if data.startswith("admin:depnok:"):
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        dep_id = int(data.split(":")[-1])
        d = get_deposit(dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Not found", show_alert=True)
        set_deposit_status(dep_id, "REJECTED", uid)
        await ctx.bot.send_message(chat_id=d["user_id"], text="❌ Deposit rejected. Contact support if this is a mistake.")
        return await q.edit_message_text("❌ Rejected.", reply_markup=kb_admin_menu())

    # Admin: Categories
    if data == "admin:cats":
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        cats = list_cats(active_only=False)
        kb = [[InlineKeyboardButton("➕ Add Category", callback_data="admin:catadd")]]
        for c in cats:
            state = "✅" if c["is_active"] else "❌"
            kb.append([InlineKeyboardButton(f"{state} {c['name']} (toggle)", callback_data=f"admin:cattog:{c['id']}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:menu")])
        return await q.edit_message_text("📂 Categories:", reply_markup=InlineKeyboardMarkup(kb))

    if data == "admin:catadd":
        ctx.user_data["flow"] = "admin_add_cat"
        return await q.edit_message_text("➕ Add Category\n\nSend category name:", reply_markup=kb_admin_menu())

    if data.startswith("admin:cattog:"):
        cat_id = int(data.split(":")[-1])
        toggle_cat(cat_id)
        return await q.edit_message_text("Updated. Open Categories again.", reply_markup=kb_admin_menu())

    # Admin: Products
    if data == "admin:products":
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Product", callback_data="admin:prodadd")],
            [InlineKeyboardButton("📋 List Products", callback_data="admin:prodlist")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:menu")],
        ])
        return await q.edit_message_text("📦 Products:", reply_markup=kb)

    if data == "admin:prodlist":
        rows = list_products_all()
        if not rows:
            return await q.edit_message_text("No products yet.", reply_markup=kb_admin_menu())
        kb = []
        for p in rows[:20]:
            state = "✅" if p["is_active"] else "❌"
            kb.append([InlineKeyboardButton(f"{state} #{p['id']} {p['name']} (Stock:{p['stock']})", callback_data=f"admin:prod:{p['id']}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:products")])
        return await q.edit_message_text("Tap a product to manage:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("admin:prod:"):
        pid = int(data.split(":")[-1])
        p = get_product(pid)
        if not p:
            return await q.answer("Not found", show_alert=True)
        text = (
            f"📦 Product #{pid}\n{p['name']}\n"
            f"User price: {money(p['user_price_cents'])}\n"
            f"Reseller price: {money(p['reseller_price_cents'])}\n"
            f"Stock: {p['stock']}\n"
            f"Channel: {p['channel_link'] or '-'}\n"
            f"Active: {'YES' if p['is_active'] else 'NO'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅/❌ Toggle Active", callback_data=f"admin:prodtog:{pid}")],
            [InlineKeyboardButton("💲 Change Prices", callback_data=f"admin:prodprice:{pid}")],
            [InlineKeyboardButton("🔗 Set Channel Link", callback_data=f"admin:prodch:{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:prodlist")],
        ])
        return await q.edit_message_text(text, reply_markup=kb)

    if data.startswith("admin:prodtog:"):
        pid = int(data.split(":")[-1])
        toggle_product(pid)
        return await q.edit_message_text("Updated. Open product again.", reply_markup=kb_admin_menu())

    if data.startswith("admin:prodch:"):
        pid = int(data.split(":")[-1])
        ctx.user_data["flow"] = "admin_set_channel"
        ctx.user_data["target_product"] = pid
        return await q.edit_message_text("🔗 Send channel link for this product:", reply_markup=kb_admin_menu())

    if data.startswith("admin:prodprice:"):
        pid = int(data.split(":")[-1])
        ctx.user_data["flow"] = "admin_set_prices"
        ctx.user_data["target_product"] = pid
        return await q.edit_message_text(
            "💲 Send prices like this:\n\nUSER_PRICE,RESELLER_PRICE\nExample: 10,7",
            reply_markup=kb_admin_menu()
        )

    if data == "admin:prodadd":
        ctx.user_data["flow"] = "admin_add_prod_name"
        ctx.user_data["new_prod"] = {}
        return await q.edit_message_text("➕ Add Product\n\nSend product name:", reply_markup=kb_admin_menu())

    # Admin: Keys
    if data == "admin:keys":
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        rows = list_products_all()
        if not rows:
            return await q.edit_message_text("Add products first.", reply_markup=kb_admin_menu())
        kb = [[InlineKeyboardButton(f"#{p['id']} {p['name']}", callback_data=f"admin:keysprod:{p['id']}")] for p in rows[:20]]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:menu")])
        return await q.edit_message_text("🔑 Choose product to add keys:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("admin:keysprod:"):
        pid = int(data.split(":")[-1])
        ctx.user_data["flow"] = "admin_add_keys"
        ctx.user_data["target_product"] = pid
        return await q.edit_message_text(
            "Paste keys now (one per line).",
            reply_markup=kb_admin_menu()
        )

    # Admin: Resellers
    if data.startswith("admin:resellers:"):
        if not is_admin_id(uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        limit = 8
        offset = page * limit
        rows = list_resellers(limit=limit, offset=offset)
        kb = [[InlineKeyboardButton("➕ Add Reseller", callback_data="admin:resadd")]]
        for r in rows:
            state = "✅" if r["is_active"] else "❌"
            kb.append([InlineKeyboardButton(f"{state} {r['login_username']} (@{r['tg_username']})", callback_data=f"admin:res:{r['user_id']}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:resellers:{page-1}"))
        if len(rows) == limit:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin:resellers:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:menu")])
        return await q.edit_message_text("🧑‍💼 Resellers:", reply_markup=InlineKeyboardMarkup(kb))

    if data == "admin:resadd":
        ctx.user_data["flow"] = "admin_add_reseller"
        return await q.edit_message_text(
            "➕ Add Reseller\n\nSend like this:\n@telegramusername, loginusername, password",
            reply_markup=kb_admin_menu()
        )

    if data.startswith("admin:res:"):
        rid = int(data.split(":")[-1])
        r = reseller_by_uid(rid)
        urow = get_user(rid)
        if not r:
            return await q.answer("Not found", show_alert=True)
        text = (
            f"🧑‍💼 Reseller\n"
            f"UserID: {rid}\n"
            f"TG: @{r['tg_username']}\n"
            f"Login: {r['login_username']}\n"
            f"Active: {'YES' if r['is_active'] else 'NO'}\n"
            f"Balance: {money(urow['balance_cents']) if urow else '-'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅/❌ Toggle", callback_data=f"admin:restog:{rid}")],
            [InlineKeyboardButton("🔑 Reset Password", callback_data=f"admin:respw:{rid}")],
            [InlineKeyboardButton("➕ Add Balance", callback_data=f"admin:baladd:{rid}")],
            [InlineKeyboardButton("➖ Deduct Balance", callback_data=f"admin:baldec:{rid}")],
            [InlineKeyboardButton("✍️ Set Balance", callback_data=f"admin:balset:{rid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:resellers:0")],
        ])
        return await q.edit_message_text(text, reply_markup=kb)

    if data.startswith("admin:restog:"):
        rid = int(data.split(":")[-1])
        toggle_reseller(rid)
        return await q.edit_message_text("Updated.", reply_markup=kb_admin_menu())

    if data.startswith("admin:respw:"):
        rid = int(data.split(":")[-1])
        ctx.user_data["flow"] = "admin_reset_res_pw"
        ctx.user_data["target_reseller"] = rid
        return await q.edit_message_text("Send new password:", reply_markup=kb_admin_menu())

    # default
    return

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    text = (update.message.text or "").strip()

    flow = ctx.user_data.get("flow")

    # Reseller login flow
    if flow == "res_login_username":
        ctx.user_data["flow"] = "res_login_password"
        ctx.user_data["res_login_user"] = text.lower()
        return await update.message.reply_text("Send password:", reply_markup=kb_mainmenu())

    if flow == "res_login_password":
        login = ctx.user_data.get("res_login_user", "")
        rec = reseller_by_login(login)
        ctx.user_data.pop("flow", None)
        ctx.user_data.pop("res_login_user", None)

        if not rec or rec["is_active"] != 1:
            set_logged_in(uid, False)
            return await update.message.reply_text("❌ Reseller login failed.", reply_markup=kb_home(uid))

        if sha256(text) != rec["password_hash"]:
            set_logged_in(uid, False)
            return await update.message.reply_text("❌ Wrong password.", reply_markup=kb_home(uid))

        if rec["user_id"] != uid:
            set_logged_in(uid, False)
            return await update.message.reply_text("❌ This login is not for your Telegram account.", reply_markup=kb_home(uid))

        set_logged_in(uid, True)
        return await update.message.reply_text("✅ Reseller login success.", reply_markup=kb_home(uid))

    # Deposit custom amount
    if flow == "dep_custom_amount":
        amt = to_cents(text)
        if amt is None:
            return await update.message.reply_text("Send a number like 10 or 10.5", reply_markup=kb_mainmenu())
        ctx.user_data["flow"] = "dep_wait_photo"
        ctx.user_data["dep_amount"] = amt
        return await update.message.reply_text(f"✅ Amount set: {money(amt)}\nNow send screenshot (photo).", reply_markup=kb_mainmenu())

    # Admin: reply user
    if flow == "admin_reply_user" and is_admin_id(uid):
        target = int(ctx.user_data.get("target_user", 0))
        ctx.user_data.clear()
        await ctx.bot.send_message(chat_id=target, text=text)
        return await update.message.reply_text("✅ Sent.", reply_markup=kb_admin_menu())

    # Admin: balance edits
    if flow in ("admin_bal_add", "admin_bal_dec", "admin_bal_set") and is_admin_id(uid):
        target = int(ctx.user_data.get("target_user", 0))
        amt = to_cents(text)
        if amt is None:
            return await update.message.reply_text("Send amount like 10 or 10.5", reply_markup=kb_admin_menu())

        if flow == "admin_bal_add":
            add_balance(target, amt)
            await ctx.bot.send_message(chat_id=target, text=f"✅ Balance credited: {money(amt)}")
        elif flow == "admin_bal_dec":
            add_balance(target, -amt)
            await ctx.bot.send_message(chat_id=target, text=f"⚠️ Balance deducted: {money(amt)}")
        else:
            set_balance(target, amt)
            await ctx.bot.send_message(chat_id=target, text=f"✅ Balance set to: {money(amt)}")

        ctx.user_data.clear()
        return await update.message.reply_text("✅ Updated.", reply_markup=kb_admin_menu())

    # Admin: add category
    if flow == "admin_add_cat" and is_admin_id(uid):
        add_cat(text)
        ctx.user_data.clear()
        return await update.message.reply_text("✅ Category added.", reply_markup=kb_admin_menu())

    # Admin: add product flow
    if flow == "admin_add_prod_name" and is_admin_id(uid):
        ctx.user_data["new_prod"] = {"name": text}
        ctx.user_data["flow"] = "admin_add_prod_userprice"
        return await update.message.reply_text("Send USER price (e.g., 10 or 10.5):", reply_markup=kb_admin_menu())

    if flow == "admin_add_prod_userprice" and is_admin_id(uid):
        amt = to_cents(text)
        if amt is None:
            return await update.message.reply_text("Send a number price.", reply_markup=kb_admin_menu())
        ctx.user_data["new_prod"]["user_price"] = amt
        ctx.user_data["flow"] = "admin_add_prod_resprice"
        return await update.message.reply_text("Send RESELLER price (e.g., 7):", reply_markup=kb_admin_menu())

    if flow == "admin_add_prod_resprice" and is_admin_id(uid):
        amt = to_cents(text)
        if amt is None:
            return await update.message.reply_text("Send a number price.", reply_markup=kb_admin_menu())
        ctx.user_data["new_prod"]["res_price"] = amt
        ctx.user_data["flow"] = "admin_add_prod_pickcat"

        cats = list_cats(active_only=True)
        if not cats:
            ctx.user_data.clear()
            return await update.message.reply_text("Add a category first.", reply_markup=kb_admin_menu())

        kb = [[InlineKeyboardButton(c["name"], callback_data=f"admin:newprodcat:{c['id']}")] for c in cats]
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await update.message.reply_text("Pick category:", reply_markup=InlineKeyboardMarkup(kb))

    # Admin: add reseller
    if flow == "admin_add_reseller" and is_admin_id(uid):
        # format: @tg, login, password
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 3:
            return await update.message.reply_text("Format: @telegramusername, loginusername, password", reply_markup=kb_admin_menu())
        ok, msg = add_reseller_by_tg_username(parts[0], parts[1], parts[2])
        ctx.user_data.clear()
        return await update.message.reply_text(("✅ " if ok else "❌ ") + msg, reply_markup=kb_admin_menu())

    # Admin: reset reseller password
    if flow == "admin_reset_res_pw" and is_admin_id(uid):
        rid = int(ctx.user_data.get("target_reseller", 0))
        set_reseller_password(rid, text)
        ctx.user_data.clear()
        return await update.message.reply_text("✅ Password reset.", reply_markup=kb_admin_menu())

    # Admin: set channel
    if flow == "admin_set_channel" and is_admin_id(uid):
        pid = int(ctx.user_data.get("target_product", 0))
        update_product_channel(pid, text)
        ctx.user_data.clear()
        return await update.message.reply_text("✅ Channel updated.", reply_markup=kb_admin_menu())

    # Admin: set prices
    if flow == "admin_set_prices" and is_admin_id(uid):
        pid = int(ctx.user_data.get("target_product", 0))
        if "," not in text:
            return await update.message.reply_text("Use format: USER_PRICE,RESELLER_PRICE", reply_markup=kb_admin_menu())
        a, b = [x.strip() for x in text.split(",", 1)]
        up = to_cents(a)
        rp = to_cents(b)
        if up is None or rp is None:
            return await update.message.reply_text("Invalid prices.", reply_markup=kb_admin_menu())
        update_product_prices(pid, up, rp)
        ctx.user_data.clear()
        return await update.message.reply_text("✅ Prices updated.", reply_markup=kb_admin_menu())

    # Admin: add keys
    if flow == "admin_add_keys" and is_admin_id(uid):
        pid = int(ctx.user_data.get("target_product", 0))
        keys = text.splitlines()
        n = add_keys(pid, keys)
        ctx.user_data.clear()
        return await update.message.reply_text(f"✅ Added {n} keys.", reply_markup=kb_admin_menu())

    # Default: forward user support message
    return await forward_user_to_admin(update, ctx)

async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    flow = ctx.user_data.get("flow")

    if flow != "dep_wait_photo":
        return

    amt = int(ctx.user_data.get("dep_amount", 0))
    if amt <= 0:
        ctx.user_data.clear()
        return await update.message.reply_text("Deposit amount missing. Go Wallet → Deposit again.", reply_markup=kb_home(uid))

    photo = update.message.photo[-1].file_id
    caption = (update.message.caption or "").strip()

    dep_id = create_deposit(uid, amt, photo, caption)
    ctx.user_data.clear()

    await update.message.reply_text(f"✅ Deposit submitted (ID #{dep_id}). Admin will review.", reply_markup=kb_home(uid))

    # notify admin with photo
    await ctx.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=photo,
        caption=f"💳 Deposit #{dep_id}\nUser: {uid}\nAmount: {money(amt)}\nCaption: {caption or '-'}"
    )

async def on_admin_newprod_cat_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_admin_id(uid):
        return

    data = q.data or ""
    if not data.startswith("admin:newprodcat:"):
        return
    cat_id = int(data.split(":")[-1])

    ctx.user_data["new_prod"]["cat_id"] = cat_id
    ctx.user_data["flow"] = "admin_add_prod_channel"
    await q.edit_message_text("Send channel link for this product (or type - to skip):", reply_markup=kb_admin_menu())

async def on_admin_finish_add_product_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # This is called from on_text when flow == admin_add_prod_channel
    pass

async def on_text_extra(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # handle final step add product channel after category picked
    uid = update.effective_user.id
    if not is_admin_id(uid):
        return
    if ctx.user_data.get("flow") != "admin_add_prod_channel":
        return

    link = (update.message.text or "").strip()
    if link == "-":
        link = ""
    info = ctx.user_data.get("new_prod", {})
    cat_id = int(info.get("cat_id", 0))
    name = info.get("name", "")
    up = int(info.get("user_price", 0))
    rp = int(info.get("res_price", 0))
    if not cat_id or not name or up <= 0 or rp <= 0:
        ctx.user_data.clear()
        return await update.message.reply_text("❌ Add product flow failed. Try again.", reply_markup=kb_admin_menu())

    add_product(cat_id, name, up, rp, link)
    ctx.user_data.clear()
    return await update.message.reply_text("✅ Product added.", reply_markup=kb_admin_menu())

async def post_init(app):
    init_db()

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing (Railway Variables).")
    if ADMIN_ID == 0:
        raise RuntimeError("ADMIN_ID is missing (Railway Variables).")
    if not USDT_TRC20_ADDRESS:
        raise RuntimeError("USDT_TRC20_ADDRESS is missing (Railway Variables).")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_admin_newprod_cat_callback, pattern=r"^admin:newprodcat:"))
    app.add_handler(CallbackQueryHandler(on_callback))

    # Photos (deposit proof)
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_photo))

    # Text flows + support forwarding
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & filters.User(ADMIN_ID), admin_reply_by_reply))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_text_extra))  # final step add product
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_text))

    app.run_polling()

if __name__ == "__main__":
    main()
