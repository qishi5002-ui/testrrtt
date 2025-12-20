# ================== REKKOSHOP FINAL BOT ==================
# Users + Admin + Resellers
# Buttons only | Categories | Wallet | Screenshot Deposit
# ========================================================

import os, sqlite3, datetime, hashlib

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, MessageHandler,
    CommandHandler, ContextTypes, filters
)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
STORE_NAME = os.getenv("STORE_NAME", "RekkoShop")
CURRENCY = os.getenv("CURRENCY", "USD")
USDT_TRC20_ADDRESS = os.getenv("USDT_TRC20_ADDRESS")

DB = "shop.db"

# ---------------- HELPERS ----------------
def now():
    return datetime.datetime.utcnow().isoformat()

def money(c):
    return f"{c/100:.2f} {CURRENCY}"

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def is_admin(u):
    return u.id == ADMIN_ID

# ---------------- DATABASE ----------------
def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            created TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS categories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            active INTEGER DEFAULT 1
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            price INTEGER,
            reseller_price INTEGER,
            category_id INTEGER,
            channel TEXT,
            active INTEGER DEFAULT 1
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS keys(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            key TEXT,
            used INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS deposits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            photo TEXT,
            status TEXT,
            created TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            price INTEGER,
            key TEXT,
            created TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS resellers(
            user_id INTEGER PRIMARY KEY,
            login TEXT,
            pw TEXT,
            active INTEGER DEFAULT 1
        )""")

def ensure_user(uid):
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone():
            c.execute("INSERT INTO users VALUES(?,?,?)", (uid, 0, now()))

# ---------------- MENUS ----------------
def main_menu(admin=False, reseller=False):
    rows = [
        [InlineKeyboardButton("🛍️ Products", callback_data="products")],
        [InlineKeyboardButton("💰 Wallet", callback_data="wallet")],
        [InlineKeyboardButton("💳 Deposit", callback_data="deposit")],
        [InlineKeyboardButton("📩 Support", callback_data="support")],
        [InlineKeyboardButton("🔐 Reseller Login", callback_data="reseller_login")]
    ]
    if reseller:
        rows.insert(0, [InlineKeyboardButton("🧑‍💻 Reseller Mode", callback_data="noop")])
    if admin:
        rows.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="admin")])
    return InlineKeyboardMarkup(rows)

def back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="home")]])

# ---------------- START ----------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    await update.message.reply_text(
        f"Welcome to **{STORE_NAME}**",
        reply_markup=main_menu(is_admin(update.effective_user)),
        parse_mode="Markdown"
    )

# ---------------- CALLBACK HANDLER ----------------
async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    ensure_user(u.id)

    if q.data == "home":
        await q.edit_message_text(
            f"Welcome to **{STORE_NAME}**",
            reply_markup=main_menu(is_admin(u), ctx.user_data.get("reseller")),
            parse_mode="Markdown"
        )

    elif q.data == "wallet":
        bal = db().execute("SELECT balance FROM users WHERE user_id=?", (u.id,)).fetchone()["balance"]
        await q.edit_message_text(
            f"💰 Balance: {money(bal)}\n\nDeposit via USDT TRC20:\n`{USDT_TRC20_ADDRESS}`",
            reply_markup=back(),
            parse_mode="Markdown"
        )

    elif q.data == "deposit":
        ctx.user_data["deposit"] = True
        await q.edit_message_text(
            "Send **payment screenshot** now.",
            reply_markup=back(),
            parse_mode="Markdown"
        )

    elif q.data == "products":
        cats = db().execute("SELECT * FROM categories WHERE active=1").fetchall()
        kb = [[InlineKeyboardButton(c["name"], callback_data=f"cat:{c['id']}")] for c in cats]
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home")])
        await q.edit_message_text("Select category:", reply_markup=InlineKeyboardMarkup(kb))

    elif q.data.startswith("cat:"):
        cid = int(q.data.split(":")[1])
        prods = db().execute("SELECT * FROM products WHERE category_id=? AND active=1", (cid,)).fetchall()
        kb = [[InlineKeyboardButton(p["name"], callback_data=f"prod:{p['id']}")] for p in prods]
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="home")])
        await q.edit_message_text("Select product:", reply_markup=InlineKeyboardMarkup(kb))

    elif q.data.startswith("prod:"):
        pid = int(q.data.split(":")[1])
        p = db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        reseller = ctx.user_data.get("reseller")
        price = p["reseller_price"] if reseller else p["price"]
        await q.edit_message_text(
            f"{p['name']}\nPrice: {money(price)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Buy", callback_data=f"buy:{pid}")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="home")]
            ])
        )

    elif q.data.startswith("buy:"):
        pid = int(q.data.split(":")[1])
        with db() as c:
            p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            reseller = ctx.user_data.get("reseller")
            price = p["reseller_price"] if reseller else p["price"]
            bal = c.execute("SELECT balance FROM users WHERE user_id=?", (u.id,)).fetchone()["balance"]
            if bal < price:
                await q.answer("Not enough balance", show_alert=True)
                return
            key = c.execute("SELECT * FROM keys WHERE product_id=? AND used=0", (pid,)).fetchone()
            if not key:
                await q.answer("Out of stock", show_alert=True)
                return
            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (price, u.id))
            c.execute("UPDATE keys SET used=1 WHERE id=?", (key["id"],))
            c.execute("INSERT INTO orders VALUES(NULL,?,?,?,?,?)", (u.id, pid, price, key["key"], now()))
        await q.edit_message_text(
            f"✅ Purchase successful\n\n🔑 Key:\n`{key['key']}`\n\n🔗 Channel:\n{p['channel']}",
            parse_mode="Markdown",
            reply_markup=back()
        )

# ---------------- PHOTO HANDLER (DEPOSIT) ----------------
async def photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.get("deposit"):
        file_id = update.message.photo[-1].file_id
        with db() as c:
            c.execute("INSERT INTO deposits VALUES(NULL,?,?,?,?,?)",
                      (update.effective_user.id, 0, file_id, "PENDING", now()))
        ctx.user_data.pop("deposit")
        await update.message.reply_text("Deposit submitted. Admin will review.", reply_markup=back())
        await ctx.bot.send_photo(
            ADMIN_ID,
            file_id,
            caption=f"New deposit from {update.effective_user.id}"
        )

# ---------------- ADMIN REPLY ----------------
async def admin_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        txt = update.message.reply_to_message.text or ""
        if "ID:" in txt:
            uid = int(txt.split("ID:")[1].split()[0])
            await ctx.bot.send_message(uid, update.message.text)

# ---------------- MAIN ----------------
async def post_init(app):
    init_db()

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.PHOTO, photo))
    app.add_handler(MessageHandler(filters.TEXT & filters.User(ADMIN_ID), admin_reply))
    app.run_polling()

if __name__ == "__main__":
    main()
