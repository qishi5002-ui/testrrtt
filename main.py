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
PAGE_SIZE = 10


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

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

def rows2(btns: List[InlineKeyboardButton], per_row: int = 2):
    return [btns[i:i+per_row] for i in range(0, len(btns), per_row)]


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
            last_bot_msg_id INTEGER,
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
            user_id INTEGER PRIMARY KEY,
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
        r = conn.execute("SELECT 1 FROM users WHERE user_id=?", (u.id,)).fetchone()
        if r:
            conn.execute("""
                UPDATE users SET username=?, first_name=?, last_name=?, updated_at=?
                WHERE user_id=?
            """, (uname, u.first_name, u.last_name, now_iso(), u.id))
        else:
            conn.execute("""
                INSERT INTO users(user_id,username,first_name,last_name,balance_cents,reseller_logged_in,last_bot_msg_id,created_at,updated_at)
                VALUES(?,?,?,?,0,0,NULL,?,?)
            """, (u.id, uname, u.first_name, u.last_name, now_iso(), now_iso()))

def get_user(uid: int):
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

def get_last_bot_msg_id(uid: int) -> Optional[int]:
    with db() as conn:
        r = conn.execute("SELECT last_bot_msg_id FROM users WHERE user_id=?", (uid,)).fetchone()
        return int(r["last_bot_msg_id"]) if r and r["last_bot_msg_id"] else None

def set_last_bot_msg_id(uid: int, msg_id: Optional[int]):
    with db() as conn:
        conn.execute("UPDATE users SET last_bot_msg_id=? WHERE user_id=?", (msg_id, uid))

def get_balance(uid: int) -> int:
    with db() as conn:
        return conn.execute("SELECT balance_cents FROM users WHERE user_id=?", (uid,)).fetchone()["balance_cents"]

def add_balance(uid: int, delta: int):
    with db() as conn:
        conn.execute("UPDATE users SET balance_cents=balance_cents+? WHERE user_id=?", (delta, uid))

def set_balance(uid: int, new_bal: int):
    with db() as conn:
        conn.execute("UPDATE users SET balance_cents=? WHERE user_id=?", (new_bal, uid))

def can_deduct(uid: int, amt: int) -> bool:
    return get_balance(uid) >= amt

def deduct(uid: int, amt: int):
    with db() as conn:
        conn.execute("UPDATE users SET balance_cents=balance_cents-? WHERE user_id=?", (amt, uid))

def set_reseller_logged(uid: int, flag: bool):
    with db() as conn:
        conn.execute("UPDATE users SET reseller_logged_in=? WHERE user_id=?", (1 if flag else 0, uid))

def total_users() -> int:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

def list_users(limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
            SELECT user_id, username, first_name, last_name, balance_cents
            FROM users
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

# ---- categories/products/keys ----
def list_categories(active_only=True):
    with db() as conn:
        if active_only:
            return conn.execute("SELECT * FROM categories WHERE is_active=1 ORDER BY id ASC").fetchall()
        return conn.execute("SELECT * FROM categories ORDER BY id ASC").fetchall()

def add_category(name: str):
    name = name.strip()
    if not name: return
    with db() as conn:
        conn.execute("INSERT INTO categories(name,is_active) VALUES(?,1)", (name,))

def toggle_category(cat_id: int):
    with db() as conn:
        r = conn.execute("SELECT is_active FROM categories WHERE id=?", (cat_id,)).fetchone()
        if not r: return
        conn.execute("UPDATE categories SET is_active=? WHERE id=?", (0 if r["is_active"] else 1, cat_id))

def add_product(cat_id: int, name: str, up: int, rp: int, channel: str):
    with db() as conn:
        conn.execute("""
            INSERT INTO products(category_id,name,user_price_cents,reseller_price_cents,channel_link,is_active)
            VALUES(?,?,?,?,?,1)
        """, (cat_id, name.strip(), up, rp, channel.strip() if channel and channel != "-" else None))

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
        r = conn.execute("SELECT is_active FROM products WHERE id=?", (pid,)).fetchone()
        if not r: return
        conn.execute("UPDATE products SET is_active=? WHERE id=?", (0 if r["is_active"] else 1, pid))

def update_product_channel(pid: int, link: str):
    with db() as conn:
        conn.execute("UPDATE products SET channel_link=? WHERE id=?", (link.strip() if link and link != "-" else None, pid))

def update_product_prices(pid: int, up: int, rp: int):
    with db() as conn:
        conn.execute("UPDATE products SET user_price_cents=?, reseller_price_cents=? WHERE id=?", (up, rp, pid))

def add_keys(pid: int, keys: List[str]) -> int:
    keys = [k.strip() for k in keys if k.strip()]
    if not keys: return 0
    with db() as conn:
        conn.executemany("INSERT INTO keys(product_id,key_text,is_used) VALUES(?,?,0)", [(pid, k) for k in keys])
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
        conn.execute("UPDATE keys SET is_used=1, used_by=?, used_at=? WHERE id=?", (buyer, now_iso(), r["id"]))
        return r["key_text"]

# ---- deposits ----
def create_deposit(uid: int, amt: int, file_id: str, caption: str) -> int:
    with db() as conn:
        conn.execute("""
            INSERT INTO deposits(user_id,amount_cents,photo_file_id,caption,status,created_at)
            VALUES(?,?,?,?, 'PENDING', ?)
        """, (uid, amt, file_id, caption, now_iso()))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

def list_pending_deposits(limit: int, offset: int):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM deposits WHERE status='PENDING'
            ORDER BY id DESC LIMIT ? OFFSET ?
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

# ---- resellers ----
def reseller_by_login(login: str):
    with db() as conn:
        return conn.execute("SELECT * FROM resellers WHERE login_username=?", (login.lower().strip(),)).fetchone()

def reseller_by_uid(uid: int):
    with db() as conn:
        return conn.execute("SELECT * FROM resellers WHERE user_id=?", (uid,)).fetchone()

def list_resellers(limit=20, offset=0):
    with db() as conn:
        return conn.execute("""
            SELECT r.user_id, r.tg_username, r.login_username, r.is_active
            FROM resellers r
            ORDER BY r.created_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

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
        r = conn.execute("SELECT is_active FROM resellers WHERE user_id=?", (uid,)).fetchone()
        if not r: return
        conn.execute("UPDATE resellers SET is_active=? WHERE user_id=?", (0 if r["is_active"] else 1, uid))

def set_reseller_password(uid: int, pw: str):
    with db() as conn:
        conn.execute("UPDATE resellers SET password_hash=? WHERE user_id=?", (sha256(pw), uid))


# ===================== CLEAN SEND (delete last bot msg) =====================
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


# ===================== UI =====================
def kb_home(uid: int) -> InlineKeyboardMarkup:
    u = get_user(uid)
    reseller_logged = bool(u and u["reseller_logged_in"])

    rows = [
        [InlineKeyboardButton("🛍️ Products", callback_data="home:products"),
         InlineKeyboardButton("💰 Wallet", callback_data="home:wallet")],
        [InlineKeyboardButton("📩 Support", callback_data="home:support"),
         InlineKeyboardButton("🔐 Reseller Login", callback_data="res:login")],
    ]
    if reseller_logged:
        rows.insert(0, [InlineKeyboardButton("🧑‍💻 Reseller: ON (Logout)", callback_data="res:logout")])
    if is_admin(uid):
        rows.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="admin:menu")])
    return InlineKeyboardMarkup(rows)

def kb_mainmenu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]])

def kb_wallet() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Deposit", callback_data="wallet:deposit"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_deposit_amounts() -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(f"💵 {money(a)}", callback_data=f"dep:amt:{a}") for a in DEPOSIT_PRESETS]
    kb = rows2(btns, 2)
    kb.append([InlineKeyboardButton("✍️ Custom Amount", callback_data="dep:custom"),
               InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
    return InlineKeyboardMarkup(kb)

def kb_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="admin:users:0"),
         InlineKeyboardButton("💳 Deposits", callback_data="admin:deps:0")],
        [InlineKeyboardButton("📂 Categories", callback_data="admin:cats"),
         InlineKeyboardButton("📦 Products", callback_data="admin:products")],
        [InlineKeyboardButton("🔑 Keys", callback_data="admin:keys"),
         InlineKeyboardButton("🧑‍💼 Resellers", callback_data="admin:resellers:0")],
        [InlineKeyboardButton("➕ Add Reseller", callback_data="admin:resadd"),
         InlineKeyboardButton("📊 Stats", callback_data="admin:stats")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])


# ===================== SUPPORT + ADMIN REPLY =====================
def support_header(u) -> str:
    uname = f"@{u.username}" if u.username else "(no username)"
    name = f"{u.first_name or ''} {u.last_name or ''}".strip()
    return f"👤 {name} {uname}\n🆔 Chat ID: {u.id}\n\n"

async def forward_support(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        return
    if ctx.user_data.get("flow"):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    await ctx.bot.send_message(chat_id=ADMIN_ID, text=support_header(update.effective_user) + "💬 Message:\n" + text)

async def admin_reply_by_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
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


# ===================== START =====================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    ctx.user_data.clear()
    await send_clean(update, ctx, HOME_TEXT, reply_markup=kb_home(update.effective_user.id))


# ===================== CALLBACKS =====================
async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    upsert_user(q.from_user)
    uid = q.from_user.id
    data = q.data or ""

    # HOME
    if data == "home:menu":
        ctx.user_data.clear()
        return await q.edit_message_text(HOME_TEXT, reply_markup=kb_home(uid))

    if data == "home:wallet":
        bal = get_balance(uid)
        text = f"💰 Wallet\n\nBalance: {money(bal)}\n\nUSDT (TRC-20) Address:\n{USDT_TRC20_ADDRESS}"
        return await q.edit_message_text(text, reply_markup=kb_wallet())

    if data == "wallet:deposit":
        ctx.user_data["flow"] = "dep_choose"
        return await q.edit_message_text("💳 Deposit\n\nChoose amount:", reply_markup=kb_deposit_amounts())

    if data == "home:support":
        ctx.user_data.clear()
        return await q.edit_message_text("📩 Support\n\nType your message. Admin will reply.", reply_markup=kb_mainmenu())

    if data == "home:products":
        cats = list_categories(True)
        if not cats:
            return await q.edit_message_text("No categories yet.", reply_markup=kb_mainmenu())
        btns = [InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}") for c in cats]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📂 Choose category:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("shop:cat:"):
        cat_id = int(data.split(":")[-1])
        prods = list_products_by_cat(cat_id)
        if not prods:
            return await q.edit_message_text("No products in this category.", reply_markup=kb_mainmenu())
        btns = [InlineKeyboardButton(f"{p['name']} (Stock:{p['stock']})", callback_data=f"shop:prod:{p['id']}") for p in prods]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="home:products"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("🛍️ Choose product:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("shop:prod:"):
        pid = int(data.split(":")[-1])
        p = get_product(pid)
        if not p or p["is_active"] != 1:
            return await q.answer("Not available", show_alert=True)

        u = get_user(uid)
        reseller_ok = bool(u and u["reseller_logged_in"]) and reseller_by_uid(uid) is not None
        price = p["reseller_price_cents"] if reseller_ok else p["user_price_cents"]

        text = f"📌 {p['name']}\nPrice: {money(price)}\nStock: {p['stock']}\n\nBuy using wallet balance."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Buy", callback_data=f"buy:{pid}"),
             InlineKeyboardButton("⬅️ Back", callback_data="home:products")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text(text, reply_markup=kb)

    if data.startswith("buy:"):
        pid = int(data.split(":")[-1])
        p = get_product(pid)
        if not p or p["is_active"] != 1 or p["stock"] <= 0:
            return await q.answer("Out of stock", show_alert=True)

        u = get_user(uid)
        reseller_ok = bool(u and u["reseller_logged_in"]) and reseller_by_uid(uid) is not None
        price = p["reseller_price_cents"] if reseller_ok else p["user_price_cents"]

        if not can_deduct(uid, price):
            return await q.answer("Not enough balance", show_alert=True)

        key = take_key(pid, uid)
        if not key:
            return await q.answer("Out of stock", show_alert=True)

        deduct(uid, price)
        channel = p["channel_link"] or "(No channel link set)"
        return await q.edit_message_text(
            f"✅ Purchase Successful!\n\n🔑 Key:\n{key}\n\n🔗 Channel:\n{channel}",
            reply_markup=kb_mainmenu()
        )

    # DEPOSIT
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

    # RESELLER
    if data == "res:login":
        ctx.user_data["flow"] = "res_login_user"
        return await q.edit_message_text("🔐 Reseller Login\n\nSend login username:", reply_markup=kb_mainmenu())

    if data == "res:logout":
        set_reseller_logged(uid, False)
        ctx.user_data.clear()
        return await q.edit_message_text("✅ Logged out.", reply_markup=kb_home(uid))

    # ADMIN
    if data == "admin:menu":
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        ctx.user_data.clear()
        return await q.edit_message_text("🛠️ Admin Panel", reply_markup=kb_admin_menu())

    if data == "admin:stats":
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        return await q.edit_message_text(f"📊 Stats\n\nTotal users: {total_users()}", reply_markup=kb_admin_menu())

    if data.startswith("admin:users:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        offset = page * PAGE_SIZE
        rows = list_users(PAGE_SIZE, offset)
        if not rows:
            return await q.edit_message_text("No users found.", reply_markup=kb_admin_menu())

        btns = []
        for r in rows:
            title = f"@{r['username']}" if r["username"] else (r["first_name"] or "User")
            btns.append(InlineKeyboardButton(title, callback_data=f"admin:user:{r['user_id']}"))

        kb = rows2(btns, 2)
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:users:{page-1}"))
        if len(rows) == PAGE_SIZE:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin:users:{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:menu"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("👥 Users (tap one):", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("admin:user:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        target = int(data.split(":")[-1])
        tu = get_user(target)
        if not tu:
            return await q.answer("User not found", show_alert=True)

        uname = f"@{tu['username']}" if tu["username"] else "(no username)"
        text = f"👤 User\nID: {target}\nUsername: {uname}\nBalance: {money(tu['balance_cents'])}"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📨 Reply", callback_data=f"admin:reply:{target}"),
             InlineKeyboardButton("➕ Add", callback_data=f"admin:baladd:{target}")],
            [InlineKeyboardButton("➖ Deduct", callback_data=f"admin:baldec:{target}"),
             InlineKeyboardButton("✍️ Set", callback_data=f"admin:balset:{target}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:users:0"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text(text, reply_markup=kb)

    if data.startswith("admin:reply:"):
        ctx.user_data["flow"] = "admin_reply"
        ctx.user_data["target_user"] = int(data.split(":")[-1])
        return await q.edit_message_text("📨 Type your reply message:", reply_markup=kb_admin_menu())

    if data.startswith("admin:baladd:"):
        ctx.user_data["flow"] = "admin_bal_add"
        ctx.user_data["target_user"] = int(data.split(":")[-1])
        return await q.edit_message_text("➕ Add balance amount (example 10 or 10.5):", reply_markup=kb_admin_menu())

    if data.startswith("admin:baldec:"):
        ctx.user_data["flow"] = "admin_bal_dec"
        ctx.user_data["target_user"] = int(data.split(":")[-1])
        return await q.edit_message_text("➖ Deduct balance amount:", reply_markup=kb_admin_menu())

    if data.startswith("admin:balset:"):
        ctx.user_data["flow"] = "admin_bal_set"
        ctx.user_data["target_user"] = int(data.split(":")[-1])
        return await q.edit_message_text("✍️ Set new balance amount:", reply_markup=kb_admin_menu())

    if data.startswith("admin:deps:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        page = int(data.split(":")[-1])
        offset = page * PAGE_SIZE
        rows = list_pending_deposits(PAGE_SIZE, offset)
        if not rows:
            return await q.edit_message_text("No pending deposits.", reply_markup=kb_admin_menu())

        btns = [InlineKeyboardButton(f"#{d['id']} {money(d['amount_cents'])}", callback_data=f"admin:dep:{d['id']}") for d in rows]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:menu"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("💳 Pending Deposits:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("admin:dep:"):
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        dep_id = int(data.split(":")[-1])
        d = get_deposit(dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Not found", show_alert=True)

        await ctx.bot.send_photo(
            chat_id=uid,
            photo=d["photo_file_id"],
            caption=f"Deposit #{dep_id}\nUser:{d['user_id']}\nAmount:{money(d['amount_cents'])}\nCaption:{d['caption'] or '-'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"admin:depok:{dep_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"admin:depnok:{dep_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:deps:0"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text("Choose action:", reply_markup=kb)

    if data.startswith("admin:depok:"):
        dep_id = int(data.split(":")[-1])
        d = get_deposit(dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Not found", show_alert=True)
        set_deposit_status(dep_id, "APPROVED", uid)
        add_balance(d["user_id"], d["amount_cents"])
        await ctx.bot.send_message(chat_id=d["user_id"], text=f"✅ Deposit approved: {money(d['amount_cents'])}")
        return await q.edit_message_text("✅ Approved.", reply_markup=kb_admin_menu())

    if data.startswith("admin:depnok:"):
        dep_id = int(data.split(":")[-1])
        d = get_deposit(dep_id)
        if not d or d["status"] != "PENDING":
            return await q.answer("Not found", show_alert=True)
        set_deposit_status(dep_id, "REJECTED", uid)
        await ctx.bot.send_message(chat_id=d["user_id"], text="❌ Deposit rejected.")
        return await q.edit_message_text("❌ Rejected.", reply_markup=kb_admin_menu())

    if data == "admin:cats":
        if not is_admin(uid):
            return await q.answer("Not authorized", show_alert=True)
        cats = list_categories(False)
        kb = [[InlineKeyboardButton("➕ Add Category", callback_data="admin:catadd")]]
        for c in cats:
            state = "✅" if c["is_active"] else "❌"
            kb.append([InlineKeyboardButton(f"{state} {c['name']}", callback_data=f"admin:cattog:{c['id']}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:menu"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("📂 Categories:", reply_markup=InlineKeyboardMarkup(kb))

    if data == "admin:catadd":
        ctx.user_data["flow"] = "admin_add_cat"
        return await q.edit_message_text("Type category name:", reply_markup=kb_admin_menu())

    if data.startswith("admin:cattog:"):
        toggle_category(int(data.split(":")[-1]))
        return await q.edit_message_text("✅ Updated.", reply_markup=kb_admin_menu())

    if data == "admin:products":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Product", callback_data="admin:prodadd"),
             InlineKeyboardButton("📋 List Products", callback_data="admin:prodlist")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:menu"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
        ])
        return await q.edit_message_text("📦 Products:", reply_markup=kb)

    if data == "admin:prodadd":
        ctx.user_data["flow"] = "prod_name"
        ctx.user_data["new_prod"] = {}
        return await q.edit_message_text("Type product name:", reply_markup=kb_admin_menu())

    if data == "admin:prodlist":
        prods = list_products_all()
        if not prods:
            return await q.edit_message_text("No products yet.", reply_markup=kb_admin_menu())
        btns = [InlineKeyboardButton(f"#{p['id']} {p['name']}", callback_data=f"admin:prod:{p['id']}") for p in prods[:40]]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:products"),
                   InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
        return await q.edit_message_text("Tap product:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("admin:prod:"):
        pid = int(data.split(":")[-1])
        p = get_product(pid)
        if not p:
            return await q.answer("Not found", show_alert=True)
        text = (
            f"📦 Product #{pid}\n{p['name']}\n"
            f"User:{money(p['user_price_cents'])}  Reseller:{money(p['reseller_price_cents'])}\n"
            f"Stock:{p['stock']}\n"
            f"Channel:{p['channel_link'] or '-'}\n"
            f"Active:{'YES' if p['is_active'] else 'NO'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅/❌ Toggle", callback_data=f"admin:prodtog:{pid}"),
             InlineKeyboardButton("💲 Prices", callback_data=f"admin:prodprice:{pid}")],
            [InlineKeyboardButton("🔗 Channel", callback_data=f"admin:prodch:{pid}"),
             InlineKeyboardButton("⬅️ Back", callback_data="admin:prodlist")],
        ])
        return await q.edit_message_text(text, reply_markup=kb)

    if data.startswith("admin:prodtog:"):
        toggle_product(int(data.split(":")[-1]))
        return await q.edit_message_text("✅ Updated.", reply_markup=kb_admin_menu())

    if data.startswith("admin:prodch:"):
        ctx.user_data["flow"] = "prod_set_channel"
        ctx.user_data["target_product"] = int(data.split(":")[-1])
        return await q.edit_message_text("Type channel link (or - to clear):", reply_markup=kb_admin_menu())

    if data.startswith("admin:prodprice:"):
        ctx.user_data["flow"] = "prod_set_prices"
        ctx.user_data["target_product"] = int(data.split(":")[-1])
        return await q.edit_message_text("Type: USER_PRICE,RESELLER_PRICE (example 10,7):", reply_markup=kb_admin_menu())

    if data == "admin:keys":
        prods = list_products_all()
        if not prods:
            return await q.edit_message_text("Add products first.", reply_markup=kb_admin_menu())
        btns = [InlineKeyboardButton(f"#{p['id']} {p['name']}", callback_data=f"admin:keysprod:{p['id']}") for p in prods[:40]]
        kb = rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:menu")])
        return await q.edit_message_text("Choose product:", reply_markup=InlineKeyboardMarkup(kb))

    if data.startswith("admin:keysprod:"):
        ctx.user_data["flow"] = "keys_paste"
        ctx.user_data["target_product"] = int(data.split(":")[-1])
        return await q.edit_message_text("Paste keys (one per line):", reply_markup=kb_admin_menu())

    if data.startswith("admin:resellers:"):
        page = int(data.split(":")[-1])
        offset = page * PAGE_SIZE
        rows = list_resellers(PAGE_SIZE, offset)

        kb = [[InlineKeyboardButton("➕ Add Reseller", callback_data="admin:resadd")]]
        if rows:
            btns = []
            for r in rows:
                state = "✅" if r["is_active"] else "❌"
                btns.append(InlineKeyboardButton(f"{state} {r['login_username']}", callback_data=f"admin:res:{r['user_id']}"))
            kb += rows2(btns, 2)
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:menu")])
        return await q.edit_message_text("🧑‍💼 Resellers:", reply_markup=InlineKeyboardMarkup(kb))

    if data == "admin:resadd":
        ctx.user_data["flow"] = "res_add"
        return await q.edit_message_text("Type: @telegramusername, loginusername, password", reply_markup=kb_admin_menu())

    if data.startswith("admin:res:"):
        rid = int(data.split(":")[-1])
        r = reseller_by_uid(rid)
        if not r:
            return await q.answer("Not found", show_alert=True)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅/❌ Toggle", callback_data=f"admin:restog:{rid}"),
             InlineKeyboardButton("🔑 Reset PW", callback_data=f"admin:respw:{rid}")]
        ])
        return await q.edit_message_text(
            f"Reseller @{r['tg_username']}\nLogin: {r['login_username']}\nActive: {'YES' if r['is_active'] else 'NO'}",
            reply_markup=kb
        )

    if data.startswith("admin:restog:"):
        toggle_reseller(int(data.split(":")[-1]))
        return await q.edit_message_text("✅ Updated.", reply_markup=kb_admin_menu())

    if data.startswith("admin:respw:"):
        ctx.user_data["flow"] = "res_pw"
        ctx.user_data["target_reseller"] = int(data.split(":")[-1])
        return await q.edit_message_text("Type new password:", reply_markup=kb_admin_menu())

    return


# ===================== TEXT (THE ONLY TYPING HANDLER) =====================
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    flow = ctx.user_data.get("flow")

    # Reseller login
    if flow == "res_login_user":
        ctx.user_data["flow"] = "res_login_pw"
        ctx.user_data["res_login_user"] = text.lower()
        return await send_clean(update, ctx, "Type password:", reply_markup=kb_mainmenu())

    if flow == "res_login_pw":
        login = ctx.user_data.get("res_login_user", "")
        ctx.user_data.clear()
        rec = reseller_by_login(login)
        if not rec or rec["is_active"] != 1 or rec["user_id"] != uid:
            set_reseller_logged(uid, False)
            return await send_clean(update, ctx, "❌ Login failed.", reply_markup=kb_home(uid))
        if sha256(text) != rec["password_hash"]:
            set_reseller_logged(uid, False)
            return await send_clean(update, ctx, "❌ Wrong password.", reply_markup=kb_home(uid))
        set_reseller_logged(uid, True)
        return await send_clean(update, ctx, "✅ Reseller login success.", reply_markup=kb_home(uid))

    # Deposit custom
    if flow == "dep_custom":
        amt = to_cents(text)
        if amt is None:
            return await send_clean(update, ctx, "Send number like 10 or 10.5", reply_markup=kb_mainmenu())
        ctx.user_data["flow"] = "dep_wait_photo"
        ctx.user_data["dep_amount"] = amt
        return await send_clean(update, ctx, f"✅ Amount set: {money(amt)}\nNow send screenshot (photo).", reply_markup=kb_mainmenu())

    # ADMIN FLOWS (typing always handled here)
    if is_admin(uid) and flow:
        if flow == "admin_add_cat":
            add_category(text)
            ctx.user_data.clear()
            return await send_clean(update, ctx, "✅ Category added.", reply_markup=kb_admin_menu())

        if flow == "prod_name":
            ctx.user_data["new_prod"] = {"name": text}
            ctx.user_data["flow"] = "prod_user_price"
            return await send_clean(update, ctx, "Send USER price (example 10 or 10.5):", reply_markup=kb_admin_menu())

        if flow == "prod_user_price":
            up = to_cents(text)
            if up is None:
                return await send_clean(update, ctx, "Send a number price.", reply_markup=kb_admin_menu())
            ctx.user_data["new_prod"]["up"] = up
            ctx.user_data["flow"] = "prod_res_price"
            return await send_clean(update, ctx, "Send RESELLER price:", reply_markup=kb_admin_menu())

        if flow == "prod_res_price":
            rp = to_cents(text)
            if rp is None:
                return await send_clean(update, ctx, "Send a number price.", reply_markup=kb_admin_menu())
            ctx.user_data["new_prod"]["rp"] = rp

            cats = list_categories(True)
            if not cats:
                ctx.user_data.clear()
                return await send_clean(update, ctx, "Add a category first.", reply_markup=kb_admin_menu())

            ctx.user_data["flow"] = "prod_pick_cat"
            btns = [InlineKeyboardButton(c["name"], callback_data=f"admin:pickcat:{c['id']}") for c in cats]
            kb = rows2(btns, 2)
            kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")])
            await update.message.reply_text("Pick category:", reply_markup=InlineKeyboardMarkup(kb))
            return

        if flow == "prod_channel":
            link = text
            if link == "-":
                link = ""

            info = ctx.user_data.get("new_prod", {})
            cat_id = int(info.get("cat_id", 0))
            name = info.get("name", "")
            up = int(info.get("up", 0))
            rp = int(info.get("rp", 0))

            if not cat_id or not name or up <= 0 or rp <= 0:
                ctx.user_data.clear()
                return await send_clean(update, ctx, "❌ Add product failed. Try again.", reply_markup=kb_admin_menu())

            add_product(cat_id, name, up, rp, link)
            ctx.user_data.clear()
            return await send_clean(update, ctx, "✅ Product added.", reply_markup=kb_admin_menu())

        if flow == "prod_set_channel":
            pid = int(ctx.user_data.get("target_product", 0))
            update_product_channel(pid, text)
            ctx.user_data.clear()
            return await send_clean(update, ctx, "✅ Channel updated.", reply_markup=kb_admin_menu())

        if flow == "prod_set_prices":
            pid = int(ctx.user_data.get("target_product", 0))
            if "," not in text:
                return await send_clean(update, ctx, "Use: USER_PRICE,RESELLER_PRICE (example 10,7)", reply_markup=kb_admin_menu())
            a, b = [x.strip() for x in text.split(",", 1)]
            up = to_cents(a); rp = to_cents(b)
            if up is None or rp is None:
                return await send_clean(update, ctx, "Invalid prices.", reply_markup=kb_admin_menu())
            update_product_prices(pid, up, rp)
            ctx.user_data.clear()
            return await send_clean(update, ctx, "✅ Prices updated.", reply_markup=kb_admin_menu())

        if flow == "keys_paste":
            pid = int(ctx.user_data.get("target_product", 0))
            n = add_keys(pid, text.splitlines())
            ctx.user_data.clear()
            return await send_clean(update, ctx, f"✅ Added {n} keys.", reply_markup=kb_admin_menu())

        if flow == "res_add":
            parts = [p.strip() for p in text.split(",")]
            if len(parts) != 3:
                return await send_clean(update, ctx, "Format: @telegramusername, loginusername, password", reply_markup=kb_admin_menu())
            ok, msg = add_reseller_by_tg_username(parts[0], parts[1], parts[2])
            ctx.user_data.clear()
            return await send_clean(update, ctx, ("✅ " if ok else "❌ ") + msg, reply_markup=kb_admin_menu())

        if flow == "res_pw":
            rid = int(ctx.user_data.get("target_reseller", 0))
            set_reseller_password(rid, text)
            ctx.user_data.clear()
            return await send_clean(update, ctx, "✅ Password reset.", reply_markup=kb_admin_menu())

        if flow == "admin_reply":
            target = int(ctx.user_data.get("target_user", 0))
            ctx.user_data.clear()
            await ctx.bot.send_message(chat_id=target, text=text)
            return await send_clean(update, ctx, "✅ Sent.", reply_markup=kb_admin_menu())

        if flow in ("admin_bal_add", "admin_bal_dec", "admin_bal_set"):
            target = int(ctx.user_data.get("target_user", 0))
            amt = to_cents(text)
            if amt is None:
                return await send_clean(update, ctx, "Send amount like 10 or 10.5", reply_markup=kb_admin_menu())
            if flow == "admin_bal_add":
                add_balance(target, amt)
            elif flow == "admin_bal_dec":
                add_balance(target, -amt)
            else:
                set_balance(target, amt)
            ctx.user_data.clear()
            return await send_clean(update, ctx, "✅ Updated.", reply_markup=kb_admin_menu())

    # Normal user text => support
    return await forward_support(update, ctx)


# ===================== PICK CATEGORY CALLBACK =====================
async def on_pick_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    if ctx.user_data.get("flow") != "prod_pick_cat":
        return
    cat_id = int((q.data or "").split(":")[-1])
    ctx.user_data["new_prod"]["cat_id"] = cat_id
    ctx.user_data["flow"] = "prod_channel"
    return await q.edit_message_text("Type channel link (or - to skip):", reply_markup=kb_admin_menu())


# ===================== PHOTO (DEPOSIT) =====================
async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id

    if ctx.user_data.get("flow") != "dep_wait_photo":
        return

    amt = int(ctx.user_data.get("dep_amount", 0))
    if amt <= 0:
        ctx.user_data.clear()
        return await send_clean(update, ctx, "Deposit amount missing. Wallet → Deposit again.", reply_markup=kb_home(uid))

    file_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "").strip()

    dep_id = create_deposit(uid, amt, file_id, caption)
    ctx.user_data.clear()

    await send_clean(update, ctx, f"✅ Deposit submitted (ID #{dep_id}). Admin will review.", reply_markup=kb_home(uid))

    await ctx.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=file_id,
        caption=f"💳 Deposit #{dep_id}\nUser: {uid}\nAmount: {money(amt)}\nCaption: {caption or '-'}"
    )


# ===================== BOOT =====================
async def post_init(app):
    init_db()

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    if ADMIN_ID == 0:
        raise RuntimeError("ADMIN_ID missing")
    if not USDT_TRC20_ADDRESS:
        raise RuntimeError("USDT_TRC20_ADDRESS missing")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))

    # callbacks
    app.add_handler(CallbackQueryHandler(on_pick_cat, pattern=r"^admin:pickcat:"))
    app.add_handler(CallbackQueryHandler(on_cb))

    # deposit screenshot
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_photo))

    # admin reply only when replying to forwarded support message
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & filters.User(ADMIN_ID) & filters.REPLY, admin_reply_by_reply))

    # ✅ THE ONLY typing handler (this is why it works)
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_text))

    app.run_polling()

if __name__ == "__main__":
    main()
