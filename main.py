# ===================== IMPORTS =====================
import os
import asyncio
import hashlib
import datetime
from typing import Optional, List

import psycopg2
import psycopg2.extras

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ===================== ENV =====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")
if ADMIN_ID == 0:
    raise RuntimeError("ADMIN_ID missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL missing")

CURRENCY = "USD"

# ===================== TIME / MONEY =====================
def now_utc():
    return datetime.datetime.utcnow()

def now_iso():
    return now_utc().isoformat(timespec="seconds")

def money(cents: int) -> str:
    return f"{cents/100:.2f} {CURRENCY}"

def to_cents(txt: str) -> Optional[int]:
    try:
        v = float(txt.strip())
        if v <= 0:
            return None
        return int(round(v * 100))
    except Exception:
        return None

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

# ===================== DATABASE =====================
def db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor,
        sslmode="require",
    )

def init_db():
    with db() as conn:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            telegram_link TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL,
            key_text TEXT NOT NULL,
            is_used INTEGER NOT NULL DEFAULT 0,
            used_by BIGINT,
            used_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            key_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS wallet (
            user_id BIGINT PRIMARY KEY,
            balance_cents INTEGER NOT NULL DEFAULT 0
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS deposits (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            amount_cents INTEGER NOT NULL,
            photo_file_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        conn.commit()

# ===================== USERS =====================
def upsert_user(u):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO users (user_id, username, first_name, created_at)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (user_id)
        DO UPDATE SET username=EXCLUDED.username
        """, (
            u.id,
            u.username,
            u.first_name,
            now_iso(),
        ))
        cur.execute("""
        INSERT INTO wallet (user_id, balance_cents)
        VALUES (%s,0)
        ON CONFLICT (user_id) DO NOTHING
        """, (u.id,))
        conn.commit()

def get_balance(uid: int) -> int:
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT balance_cents FROM wallet WHERE user_id=%s", (uid,))
        r = cur.fetchone()
        return int(r["balance_cents"]) if r else 0

def add_balance(uid: int, delta: int):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
        UPDATE wallet
        SET balance_cents = balance_cents + %s
        WHERE user_id=%s
        """, (delta, uid))
        conn.commit()

# ===================== PRODUCTS =====================
def list_products(active_only=True):
    with db() as conn:
        cur = conn.cursor()
        if active_only:
            cur.execute("""
            SELECT p.*,
            (SELECT COUNT(*) FROM keys k WHERE k.product_id=p.id AND k.is_used=0) AS stock
            FROM products p
            WHERE is_active=1
            ORDER BY id ASC
            """)
        else:
            cur.execute("""
            SELECT p.*,
            (SELECT COUNT(*) FROM keys k WHERE k.product_id=p.id AND k.is_used=0) AS stock
            FROM products p
            ORDER BY id ASC
            """)
        return cur.fetchall()

def get_product(pid: int):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT p.*,
        (SELECT COUNT(*) FROM keys k WHERE k.product_id=p.id AND k.is_used=0) AS stock
        FROM products p WHERE id=%s
        """, (pid,))
        return cur.fetchone()

def take_key(pid: int, uid: int) -> Optional[str]:
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT id, key_text FROM keys
        WHERE product_id=%s AND is_used=0
        ORDER BY id ASC LIMIT 1
        """, (pid,))
        r = cur.fetchone()
        if not r:
            return None

        cur.execute("""
        UPDATE keys SET is_used=1, used_by=%s, used_at=%s
        WHERE id=%s
        """, (uid, now_iso(), r["id"]))
        conn.commit()
        return r["key_text"]

# ===================== UI =====================
def kb_home(uid: int):
    buttons = [
        [
            InlineKeyboardButton("🛍 Products", callback_data="home:products"),
            InlineKeyboardButton("💰 Wallet", callback_data="home:wallet"),
        ],
        [
            InlineKeyboardButton("📜 History", callback_data="home:history"),
        ]
    ]
    if uid == ADMIN_ID:
        buttons.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin:menu")])
    return InlineKeyboardMarkup(buttons)

def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data="home")]])

# ===================== START =====================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    ctx.user_data.clear()
    await update.message.reply_text(
        "👋 Welcome!\n\nChoose an option:",
        reply_markup=kb_home(uid)
    )

# ===================== CALLBACK HANDLER =====================
async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    upsert_user(q.from_user)

    data = q.data

    # HOME
    if data == "home":
        ctx.user_data.clear()
        return await q.edit_message_text(
            "🏠 Main Menu",
            reply_markup=kb_home(uid)
        )

    # ================= PRODUCTS =================
    if data == "home:products":
        prods = list_products(active_only=True)
        if not prods:
            return await q.edit_message_text("No products available.", reply_markup=kb_back())

        btns = []
        for p in prods:
            btns.append([
                InlineKeyboardButton(
                    f"{p['name']} • {money(p['price_cents'])} • Stock {p['stock']}",
                    callback_data=f"prod:{p['id']}"
                )
            ])
        btns.append([InlineKeyboardButton("⬅ Back", callback_data="home")])
        return await q.edit_message_text("🛍 Products:", reply_markup=InlineKeyboardMarkup(btns))

    if data.startswith("prod:"):
        pid = int(data.split(":")[1])
        p = get_product(pid)
        if not p or p["is_active"] != 1:
            return await q.answer("Product not available", show_alert=True)

        bal = get_balance(uid)
        txt = (
            f"📦 {p['name']}\n\n"
            f"Price: {money(p['price_cents'])}\n"
            f"Stock: {p['stock']}\n"
            f"Your balance: {money(bal)}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Buy", callback_data=f"buy:{pid}")],
            [InlineKeyboardButton("⬅ Back", callback_data="home:products")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    # ================= BUY =================
    if data.startswith("buy:"):
        pid = int(data.split(":")[1])
        p = get_product(pid)
        if not p:
            return await q.answer("Not found", show_alert=True)

        if p["stock"] <= 0:
            return await q.answer("Out of stock", show_alert=True)

        price = int(p["price_cents"])
        if get_balance(uid) < price:
            return await q.answer("Insufficient balance", show_alert=True)

        key = take_key(pid, uid)
        if not key:
            return await q.answer("Out of stock", show_alert=True)

        add_balance(uid, -price)

        with db() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO purchases (user_id, product_id, product_name, price_cents, key_text, created_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            """, (uid, pid, p["name"], price, key, now_iso()))
            conn.commit()

        txt = f"✅ Purchase successful!\n\n🔑 Key:\n`{key}`"
        kb = []
        if p["telegram_link"]:
            kb.append([InlineKeyboardButton("📥 Get Files", url=p["telegram_link"])])
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home")])
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    # ================= WALLET =================
    if data == "home:wallet":
        bal = get_balance(uid)
        txt = f"💰 Wallet\n\nBalance: {money(bal)}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Deposit", callback_data="wallet:deposit")],
            [InlineKeyboardButton("⬅ Back", callback_data="home")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data == "wallet:deposit":
        ctx.user_data["flow"] = "deposit_amount"
        return await q.edit_message_text(
            "💳 Deposit\n\nSend amount (example: 10 or 10.5):",
            reply_markup=kb_back()
        )

    # ================= HISTORY =================
    if data == "home:history":
        with db() as conn:
            cur = conn.cursor()
            cur.execute("""
            SELECT * FROM purchases
            WHERE user_id=%s
            ORDER BY id DESC LIMIT 10
            """, (uid,))
            rows = cur.fetchall()

        if not rows:
            return await q.edit_message_text("No purchases yet.", reply_markup=kb_back())

        txt = "📜 Purchase History\n\n"
        for r in rows:
            txt += f"#{r['id']} • {r['product_name']} • {money(r['price_cents'])}\n"
        return await q.edit_message_text(txt, reply_markup=kb_back())

# ===================== TEXT HANDLER =====================
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    upsert_user(update.effective_user)

    flow = ctx.user_data.get("flow")
    text = (update.message.text or "").strip()

    if flow == "deposit_amount":
        amt = to_cents(text)
        if amt is None:
            return await update.message.reply_text("Invalid amount. Try again.")

        ctx.user_data.clear()

        with db() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO deposits (user_id, amount_cents, photo_file_id, status, created_at)
            VALUES (%s,%s,'TEXT', 'PENDING', %s)
            """, (uid, amt, now_iso()))
            conn.commit()

        return await update.message.reply_text(
            f"✅ Deposit request submitted: {money(amt)}\n\nAdmin will approve.",
            reply_markup=kb_home(uid)
        )

# ===================== BOOT =====================
async def post_init(app):
    init_db()

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_text))
    app.run_polling()

if __name__ == "__main__":
    main()


# ===================== ADMIN UI =====================
def kb_admin():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 Products", callback_data="admin:products"),
            InlineKeyboardButton("💳 Deposits", callback_data="admin:deposits"),
        ],
        [
            InlineKeyboardButton("👥 Users", callback_data="admin:users"),
        ],
        [
            InlineKeyboardButton("⬅ Back", callback_data="home"),
        ]
    ])

def kb_admin_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅ Back", callback_data="admin:menu")]
    ])

# ===================== ADMIN CALLBACKS =====================
async def admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    if uid != ADMIN_ID:
        return await q.answer("Not authorized", show_alert=True)

    data = q.data

    # ADMIN MENU
    if data == "admin:menu":
        return await q.edit_message_text(
            "🛠 Admin Panel",
            reply_markup=kb_admin()
        )

    # ================= PRODUCTS =================
    if data == "admin:products":
        prods = list_products(active_only=False)
        btns = []
        for p in prods:
            state = "✅" if p["is_active"] else "🚫"
            btns.append([
                InlineKeyboardButton(
                    f"{state} {p['name']} (ID {p['id']})",
                    callback_data=f"admin:prod:{p['id']}"
                )
            ])
        btns.append([InlineKeyboardButton("➕ Add Product", callback_data="admin:add_product")])
        btns.append([InlineKeyboardButton("⬅ Back", callback_data="admin:menu")])
        return await q.edit_message_text(
            "📦 Products",
            reply_markup=InlineKeyboardMarkup(btns)
        )

    if data.startswith("admin:prod:"):
        pid = int(data.split(":")[2])
        p = get_product(pid)
        txt = (
            f"📦 {p['name']}\n\n"
            f"Price: {money(p['price_cents'])}\n"
            f"Stock: {p['stock']}\n"
            f"Active: {'YES' if p['is_active'] else 'NO'}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Toggle Active", callback_data=f"admin:toggle:{pid}")],
            [InlineKeyboardButton("🔑 Add Keys", callback_data=f"admin:addkeys:{pid}")],
            [InlineKeyboardButton("⬅ Back", callback_data="admin:products")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("admin:toggle:"):
        pid = int(data.split(":")[2])
        toggle_product(pid)
        return await q.answer("Updated")

    if data == "admin:add_product":
        ctx.user_data["flow"] = "admin_add_product"
        return await q.edit_message_text(
            "➕ Add Product\n\nSend format:\nName | Price | Telegram Link",
            reply_markup=kb_admin_back()
        )

    # ================= KEYS =================
    if data.startswith("admin:addkeys:"):
        pid = int(data.split(":")[2])
        ctx.user_data["flow"] = "admin_add_keys"
        ctx.user_data["pid"] = pid
        return await q.edit_message_text(
            "🔑 Send keys (one per line):",
            reply_markup=kb_admin_back()
        )

    # ================= DEPOSITS =================
    if data == "admin:deposits":
        with db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM deposits WHERE status='PENDING'")
            deps = cur.fetchall()

        if not deps:
            return await q.edit_message_text("No pending deposits.", reply_markup=kb_admin())

        btns = []
        for d in deps:
            btns.append([
                InlineKeyboardButton(
                    f"#{d['id']} • User {d['user_id']} • {money(d['amount_cents'])}",
                    callback_data=f"admin:dep:{d['id']}"
                )
            ])
        btns.append([InlineKeyboardButton("⬅ Back", callback_data="admin:menu")])
        return await q.edit_message_text(
            "💳 Pending Deposits",
            reply_markup=InlineKeyboardMarkup(btns)
        )

    if data.startswith("admin:dep:"):
        dep_id = int(data.split(":")[2])
        with db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM deposits WHERE id=%s", (dep_id,))
            d = cur.fetchone()

        txt = (
            f"💳 Deposit #{d['id']}\n\n"
            f"User: {d['user_id']}\n"
            f"Amount: {money(d['amount_cents'])}\n"
            f"Status: {d['status']}"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"admin:depok:{dep_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"admin:depnok:{dep_id}")
            ],
            [InlineKeyboardButton("⬅ Back", callback_data="admin:deposits")]
        ])
        return await q.edit_message_text(txt, reply_markup=kb)

    if data.startswith("admin:depok:"):
        dep_id = int(data.split(":")[2])
        with db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM deposits WHERE id=%s", (dep_id,))
            d = cur.fetchone()
            cur.execute("UPDATE deposits SET status='APPROVED' WHERE id=%s", (dep_id,))
            add_balance(d["user_id"], d["amount_cents"])
            conn.commit()
        return await q.edit_message_text("✅ Deposit approved.", reply_markup=kb_admin())

    if data.startswith("admin:depnok:"):
        dep_id = int(data.split(":")[2])
        with db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE deposits SET status='REJECTED' WHERE id=%s", (dep_id,))
            conn.commit()
        return await q.edit_message_text("❌ Deposit rejected.", reply_markup=kb_admin())

# ===================== ADMIN TEXT FLOWS =====================
async def admin_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return

    flow = ctx.user_data.get("flow")
    text = (update.message.text or "").strip()

    # ADD PRODUCT
    if flow == "admin_add_product":
        if "|" not in text:
            return await update.message.reply_text("Format: Name | Price | Telegram Link")

        name, price, link = [x.strip() for x in text.split("|", 2)]
        cents = to_cents(price)
        if cents is None:
            return await update.message.reply_text("Invalid price")

        with db() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO products (name, price_cents, telegram_link, is_active)
            VALUES (%s,%s,%s,1)
            """, (name, cents, link))
            conn.commit()

        ctx.user_data.clear()
        return await update.message.reply_text("✅ Product added.", reply_markup=kb_admin())

    # ADD KEYS
    if flow == "admin_add_keys":
        pid = ctx.user_data.get("pid")
        keys = text.splitlines()
        with db() as conn:
            cur = conn.cursor()
            for k in keys:
                cur.execute(
                    "INSERT INTO keys (product_id, key_text, is_used) VALUES (%s,%s,0)",
                    (pid, k.strip())
                )
            conn.commit()
        ctx.user_data.clear()
        return await update.message.reply_text("✅ Keys added.", reply_markup=kb_admin())

# ===================== REGISTER ADMIN HANDLERS =====================
app.add_handler(CallbackQueryHandler(admin_cb, pattern="^admin"))
app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, admin_text))


