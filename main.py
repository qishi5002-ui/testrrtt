import os
import sqlite3
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # your Telegram numeric ID
DB_PATH = os.getenv("DB_PATH", "rekkoshop.db")

USDT_TRC20_ADDRESS = os.getenv("USDT_TRC20_ADDRESS", "TEwKbkNdXqvNUG951nYTCRF6UjLGBn7pqc")
CURRENCY = os.getenv("CURRENCY", "USD")
STORE_NAME = os.getenv("STORE_NAME", "RekkoShop")

# ---------------- DB ----------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            key_text TEXT NOT NULL,
            is_used INTEGER NOT NULL DEFAULT 0,
            used_at TEXT DEFAULT NULL,
            used_by INTEGER DEFAULT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL,
            proof TEXT NOT NULL,
            status TEXT NOT NULL, -- PENDING / APPROVED / REJECTED
            created_at TEXT NOT NULL,
            reviewed_at TEXT DEFAULT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            price_cents INTEGER NOT NULL,
            delivered_key TEXT DEFAULT NULL,
            status TEXT NOT NULL, -- PAID_DELIVERED / FAILED_NO_KEY
            created_at TEXT NOT NULL
        )
        """)

def now_iso():
    return datetime.datetime.utcnow().isoformat()

def cents(n: float) -> int:
    return int(round(n * 100))

def money(cents_val: int) -> str:
    return f"{cents_val/100:.2f} {CURRENCY}"

def is_admin(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == ADMIN_ID

def ensure_user(user_id: int):
    with db() as conn:
        row = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users(user_id, balance_cents, created_at) VALUES(?,?,?)",
                (user_id, 0, now_iso())
            )

def get_setting(k: str) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        return row["v"] if row else None

def set_setting(k: str, v: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v)
        )

# ------------- Admin Inbox (reply through bot) -------------
def format_user_header(update: Update) -> str:
    u = update.effective_user
    chat_id = update.effective_chat.id
    uname = f"@{u.username}" if u and u.username else "(no username)"
    name = f"{u.first_name or ''} {u.last_name or ''}".strip() if u else ""
    return f"👤 {name} {uname}\n🆔 Chat ID: {chat_id}\n\n"

async def forward_user_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    if is_admin(update):
        return
    text = update.message.text or ""
    header = format_user_header(update)
    await context.bot.send_message(chat_id=ADMIN_ID, text=header + "💬 Message:\n" + text)

async def admin_reply_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_admin(update):
        return
    if not update.message.reply_to_message:
        return
    original = update.message.reply_to_message.text or ""
    if "Chat ID:" not in original:
        return
    try:
        chat_id_str = original.split("Chat ID:")[1].split("\n")[0].strip()
        target_chat_id = int(chat_id_str)
    except Exception:
        return
    await context.bot.send_message(chat_id=target_chat_id, text=update.message.text)

# ---------------- UI ----------------
def main_kb(is_admin_user: bool):
    rows = [
        [InlineKeyboardButton("🛍️ Products", callback_data="shop:list")],
        [InlineKeyboardButton("💰 Wallet", callback_data="wallet:menu")],
    ]
    if is_admin_user:
        rows.append([InlineKeyboardButton("🛠️ Admin", callback_data="admin:menu")])
    return InlineKeyboardMarkup(rows)

def admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Product", callback_data="admin:addproduct")],
        [InlineKeyboardButton("📦 List Products", callback_data="admin:products")],
        [InlineKeyboardButton("🔑 Add Keys", callback_data="admin:addkeys")],
        [InlineKeyboardButton("💳 Deposit Requests", callback_data="admin:deposits")],
        [InlineKeyboardButton("🔗 Set Private Channel", callback_data="admin:setchannel")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back:main")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    text = (
        f"Welcome to {STORE_NAME} ✅\n\n"
        "Use the buttons below.\n"
        "Need help? Just type here and admin will reply."
    )
    await update.message.reply_text(text, reply_markup=main_kb(is_admin(update)))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start\n"
        "/wallet\n"
        "/deposit <amount> <proof/txid>\n\n"
        "Admin:\n"
        "/setchannel <@channel OR invite link>\n"
        "/addproduct <name> | <price>\n"
        "/addkeys <product_id>\n"
        "/deposits"
    )

# ---------------- Wallet & Deposits ----------------
async def wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_wallet(update, context, from_callback=False)

async def show_wallet(update_or_q, context, from_callback: bool, q=None):
    user_id = (q.from_user.id if q else update_or_q.effective_user.id)
    ensure_user(user_id)
    with db() as conn:
        bal = conn.execute("SELECT balance_cents FROM users WHERE user_id=?", (user_id,)).fetchone()["balance_cents"]
    text = (
        f"💰 Wallet\n"
        f"Balance: {money(bal)}\n\n"
        f"To deposit USDT (TRC-20):\n{USDT_TRC20_ADDRESS}\n\n"
        f"Send: /deposit <amount> <txid/screenshot note>\n"
        f"Example: /deposit 10 paid txid:xxxxx"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Products", callback_data="shop:list")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back:main")],
    ])
    if from_callback and q:
        return await q.edit_message_text(text, reply_markup=kb)
    return await update_or_q.message.reply_text(text, reply_markup=kb) if hasattr(update_or_q, "message") else await update_or_q.reply_text(text, reply_markup=kb)

async def deposit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    parts = (update.message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        return await update.message.reply_text("Usage: /deposit <amount> <proof/txid note>\nExample: /deposit 10 txid:xxxx")
    try:
        amt = float(parts[1])
        if amt <= 0:
            raise ValueError()
    except Exception:
        return await update.message.reply_text("Amount must be a number like 10 or 5.5")

    proof = parts[2].strip()
    amount_cents = cents(amt)

    with db() as conn:
        conn.execute(
            "INSERT INTO deposits(user_id, amount_cents, proof, status, created_at) VALUES(?,?,?,?,?)",
            (update.effective_user.id, amount_cents, proof, "PENDING", now_iso())
        )
        dep_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    await update.message.reply_text(f"✅ Deposit request submitted. ID #{dep_id}\nAdmin will approve after checking payment.")
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(f"💳 Deposit Request #{dep_id}\n"
              f"User ID: {update.effective_user.id}\n"
              f"Amount: {money(amount_cents)}\n"
              f"Proof: {proof}")
    )

# ---------------- Products & Purchase ----------------
async def list_products(q, context):
    with db() as conn:
        rows = conn.execute("SELECT id,name,price_cents FROM products WHERE is_active=1 ORDER BY id DESC").fetchall()
    if not rows:
        return await q.edit_message_text("No products yet.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back:main")]]))
    kb = [[InlineKeyboardButton(f"{r['name']} — {money(r['price_cents'])}", callback_data=f"shop:view:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton("⬅️ Back", callback_data="back:main")])
    await q.edit_message_text("🛍️ Products:", reply_markup=InlineKeyboardMarkup(kb))

async def view_product(q, context, pid: int):
    with db() as conn:
        p = conn.execute("SELECT id,name,price_cents,is_active FROM products WHERE id=?", (pid,)).fetchone()
        if not p or p["is_active"] != 1:
            return await q.edit_message_text("Product not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="shop:list")]]))
        remaining = conn.execute("SELECT COUNT(*) AS c FROM keys WHERE product_id=? AND is_used=0", (pid,)).fetchone()["c"]
    text = f"📌 {p['name']}\nPrice: {money(p['price_cents'])}\nKeys left: {remaining}\n\nBuy using wallet balance."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Buy Now", callback_data=f"shop:buy:{pid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="shop:list")]
    ])
    await q.edit_message_text(text, reply_markup=kb)

async def buy_product(q, context, pid: int):
    user_id = q.from_user.id
    ensure_user(user_id)

    with db() as conn:
        p = conn.execute("SELECT id,name,price_cents,is_active FROM products WHERE id=?", (pid,)).fetchone()
        if not p or p["is_active"] != 1:
            return await q.answer("Product not available.", show_alert=True)

        bal = conn.execute("SELECT balance_cents FROM users WHERE user_id=?", (user_id,)).fetchone()["balance_cents"]
        price = p["price_cents"]
        if bal < price:
            return await q.answer(f"Not enough balance. Need {money(price)}", show_alert=True)

        # get 1 unused key
        k = conn.execute(
            "SELECT id,key_text FROM keys WHERE product_id=? AND is_used=0 ORDER BY id ASC LIMIT 1",
            (pid,)
        ).fetchone()
        if not k:
            # still deduct? NO. Keep user safe.
            return await q.answer("Out of stock (no keys).", show_alert=True)

        # deduct balance
        conn.execute("UPDATE users SET balance_cents = balance_cents - ? WHERE user_id=?", (price, user_id))

        # mark key used
        conn.execute(
            "UPDATE keys SET is_used=1, used_at=?, used_by=? WHERE id=?",
            (now_iso(), user_id, k["id"])
        )

        # record order
        conn.execute(
            "INSERT INTO orders(user_id, product_id, price_cents, delivered_key, status, created_at) VALUES(?,?,?,?,?,?)",
            (user_id, pid, price, k["key_text"], "PAID_DELIVERED", now_iso())
        )
        order_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    # Channel behavior (admin sets this)
    channel_value = get_setting("private_channel")  # can be @username OR invite link
    channel_text = ""
    if channel_value:
        # If admin put @channelusername, bot will try to generate an invite link (bot must be admin in that channel).
        if channel_value.startswith("@"):
            try:
                invite = await context.bot.create_chat_invite_link(chat_id=channel_value, creates_join_request=False)
                channel_text = f"\n\n🔗 Private Channel Invite:\n{invite.invite_link}"
            except Exception:
                # fallback: tell admin to paste an invite link instead
                channel_text = f"\n\n🔗 Private Channel:\n{channel_value}\n(If you want a clickable invite link, admin can /setchannel with an invite link.)"
        else:
            channel_text = f"\n\n🔗 Private Channel Link:\n{channel_value}"
    else:
        channel_text = "\n\n🔗 Private Channel Link: (not set yet)"

    await q.edit_message_text(
        f"✅ Purchase successful!\nOrder: #{order_id}\nProduct: {p['name']}\n\n"
        f"🔑 Your Key:\n{k['key_text']}"
        f"{channel_text}"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"🧾 New sale #{order_id}\nUser: {user_id}\nProduct: {p['name']}\nPrice: {money(p['price_cents'])}")

# ---------------- Admin Commands ----------------
async def setchannel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    text = (update.message.text or "").split(maxsplit=1)
    if len(text) < 2:
        return await update.message.reply_text("Usage: /setchannel <@channelusername OR invite link>")
    val = text[1].strip()
    set_setting("private_channel", val)
    await update.message.reply_text(f"✅ Private channel set to: {val}")

async def addproduct_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    raw = (update.message.text or "").split(maxsplit=1)
    if len(raw) < 2 or "|" not in raw[1]:
        return await update.message.reply_text("Usage: /addproduct <name> | <price>\nExample: /addproduct VIP Key 7 Days | 5")
    name, price_str = [x.strip() for x in raw[1].split("|", 1)]
    try:
        price = float(price_str)
        if price <= 0:
            raise ValueError()
    except Exception:
        return await update.message.reply_text("Price must be a number like 5 or 9.99")

    with db() as conn:
        conn.execute("INSERT INTO products(name, price_cents, is_active) VALUES(?,?,1)", (name, cents(price)))
        pid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    await update.message.reply_text(f"✅ Product added. ID #{pid}\nNow add keys: /addkeys {pid}")

async def addkeys_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await update.message.reply_text("Usage: /addkeys <product_id>\nThen send keys (one per line). End with /done")
    try:
        pid = int(parts[1].strip())
    except Exception:
        return await update.message.reply_text("Product ID must be a number.")
    context.user_data["addkeys_pid"] = pid
    context.user_data["addkeys_mode"] = True
    await update.message.reply_text("Send keys now (one per line). When finished, send /done")

async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if context.user_data.get("addkeys_mode"):
        context.user_data["addkeys_mode"] = False
        context.user_data.pop("addkeys_pid", None)
        return await update.message.reply_text("✅ Finished adding keys.")

async def admin_text_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Used ONLY for add-keys mode
    if not is_admin(update):
        return
    if not context.user_data.get("addkeys_mode"):
        return
    pid = context.user_data.get("addkeys_pid")
    key_text = (update.message.text or "").strip()
    if not key_text:
        return
    with db() as conn:
        conn.execute("INSERT INTO keys(product_id, key_text, is_used) VALUES(?,?,0)", (pid, key_text))
    await update.message.reply_text("✅ Key added.")

async def deposits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    with db() as conn:
        rows = conn.execute("SELECT id,user_id,amount_cents,proof,created_at FROM deposits WHERE status='PENDING' ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        return await update.message.reply_text("No pending deposits.")
    lines = ["Pending deposits:\n"]
    for r in rows:
        lines.append(f"#{r['id']} user:{r['user_id']} amount:{money(r['amount_cents'])} proof:{r['proof']}")
    lines.append("\nApprove: /approve <deposit_id>\nReject: /reject <deposit_id>")
    await update.message.reply_text("\n".join(lines))

async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await update.message.reply_text("Usage: /approve <deposit_id>")
    dep_id = int(parts[1].strip())

    with db() as conn:
        d = conn.execute("SELECT * FROM deposits WHERE id=? AND status='PENDING'", (dep_id,)).fetchone()
        if not d:
            return await update.message.reply_text("Deposit not found or already reviewed.")
        conn.execute("UPDATE deposits SET status='APPROVED', reviewed_at=? WHERE id=?", (now_iso(), dep_id))
        conn.execute("UPDATE users SET balance_cents = balance_cents + ? WHERE user_id=?", (d["amount_cents"], d["user_id"]))

    await update.message.reply_text(f"✅ Approved deposit #{dep_id}")
    await context.bot.send_message(chat_id=d["user_id"], text=f"✅ Deposit approved. Wallet credited: {money(d['amount_cents'])}")

async def reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await update.message.reply_text("Usage: /reject <deposit_id>")
    dep_id = int(parts[1].strip())
    with db() as conn:
        d = conn.execute("SELECT * FROM deposits WHERE id=? AND status='PENDING'", (dep_id,)).fetchone()
        if not d:
            return await update.message.reply_text("Deposit not found or already reviewed.")
        conn.execute("UPDATE deposits SET status='REJECTED', reviewed_at=? WHERE id=?", (now_iso(), dep_id))
    await update.message.reply_text(f"✅ Rejected deposit #{dep_id}")
    await context.bot.send_message(chat_id=d["user_id"], text=f"❌ Deposit rejected. If you think this is a mistake, message support.")

# ---------------- Callbacks ----------------
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "back:main":
        return await q.edit_message_text("Menu:", reply_markup=main_kb(is_admin(update)))

    if data == "shop:list":
        return await list_products(q, context)

    if data.startswith("shop:view:"):
        pid = int(data.split(":")[-1])
        return await view_product(q, context, pid)

    if data.startswith("shop:buy:"):
        pid = int(data.split(":")[-1])
        return await buy_product(q, context, pid)

    if data == "wallet:menu":
        return await show_wallet(update, context, from_callback=True, q=q)

    if data == "admin:menu":
        if not is_admin(update):
            return await q.edit_message_text("Not authorized.")
        return await q.edit_message_text("Admin menu:", reply_markup=admin_kb())

    if data == "admin:setchannel":
        if not is_admin(update):
            return
        return await q.edit_message_text("Send:\n/setchannel <@channel OR invite link>\n\nExample:\n/setchannel @RekkoVIP\nor\n/setchannel https://t.me/+xxxx")

    if data == "admin:addproduct":
        if not is_admin(update):
            return
        return await q.edit_message_text("Send:\n/addproduct <name> | <price>\nExample:\n/addproduct VIP Key 7 Days | 5")

    if data == "admin:addkeys":
        if not is_admin(update):
            return
        return await q.edit_message_text("Send:\n/addkeys <product_id>\nThen send keys (one per line) and finish with /done")

    if data == "admin:products":
        if not is_admin(update):
            return
        with db() as conn:
            rows = conn.execute("SELECT id,name,price_cents,is_active FROM products ORDER BY id DESC LIMIT 30").fetchall()
        if not rows:
            return await q.edit_message_text("No products.", reply_markup=admin_kb())
        lines = ["Products:\n"]
        for r in rows:
            lines.append(f"#{r['id']} {r['name']} — {money(r['price_cents'])} — {'ON' if r['is_active'] else 'OFF'}")
        return await q.edit_message_text("\n".join(lines), reply_markup=admin_kb())

    if data == "admin:deposits":
        if not is_admin(update):
            return
        return await q.edit_message_text("Use:\n/deposits\n/approve <id>\n/reject <id>", reply_markup=admin_kb())

# ---------------- Main ----------------
async def post_init(app):
    init_db()

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing. Set Railway variable BOT_TOKEN.")
    if ADMIN_ID == 0:
        raise RuntimeError("ADMIN_ID missing. Set Railway variable ADMIN_ID.")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("wallet", wallet_cmd))
    app.add_handler(CommandHandler("deposit", deposit_cmd))

    # Admin
    app.add_handler(CommandHandler("setchannel", setchannel_cmd))
    app.add_handler(CommandHandler("addproduct", addproduct_cmd))
    app.add_handler(CommandHandler("addkeys", addkeys_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CommandHandler("deposits", deposits_cmd))
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("reject", reject_cmd))

    app.add_handler(CallbackQueryHandler(callbacks))

    # Admin reply as bot (support inbox)
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & filters.User(ADMIN_ID), admin_reply_forward))

    # Admin add-keys mode
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & filters.User(ADMIN_ID), admin_text_receiver))

    # Forward user messages to admin (keep last)
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.User(ADMIN_ID), forward_user_to_admin))

    app.run_polling()

if __name__ == "__main__":
    main()
