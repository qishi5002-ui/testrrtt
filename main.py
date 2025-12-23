import os
import sqlite3
import time
import logging
from typing import Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# Railway ENV Variables (NO CODE EDIT)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or "0")

STORE_NAME = os.getenv("STORE_NAME", "RekkoShop").strip()
CURRENCY = os.getenv("CURRENCY", "USDT").strip()
USDT_TRC20 = os.getenv("USDT_TRC20", "").strip()

SELLER_SUB_PRICE = float(os.getenv("SELLER_SUB_PRICE", "10").strip() or "10")
SELLER_SUB_DAYS = int(os.getenv("SELLER_SUB_DAYS", "30").strip() or "30")

DB_FILE = os.getenv("DB_FILE", "store.db").strip()

# =========================
# Logging
# =========================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("storebot")


def now_ts() -> int:
    return int(time.time())


def must_have_config() -> Optional[str]:
    if not BOT_TOKEN:
        return "Missing BOT_TOKEN"
    if ADMIN_ID <= 0:
        return "Missing ADMIN_ID"
    if not USDT_TRC20:
        return "Missing USDT_TRC20"
    return None


# =========================
# Database
# =========================
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
    CREATE TABLE IF NOT EXISTS products (
        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL, -- 0=main store, else seller_id
        category TEXT NOT NULL,
        co_category TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        price REAL NOT NULL,
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
        proof TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending', -- pending/approved/rejected
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


def list_products(owner_id: int):
    cur.execute("""
        SELECT product_id, category, co_category, name, price
        FROM products
        WHERE owner_id=? AND active=1
        ORDER BY category, co_category, name
    """, (owner_id,))
    return cur.fetchall()


def add_product(owner_id: int, category: str, co_category: str, name: str, price: float):
    cur.execute("""
        INSERT INTO products(owner_id, category, co_category, name, price, active)
        VALUES(?,?,?,?,?,1)
    """, (owner_id, category.strip(), co_category.strip(), name.strip(), float(price)))
    conn.commit()


def deactivate_product(owner_id: int, product_id: int):
    cur.execute("UPDATE products SET active=0 WHERE owner_id=? AND product_id=?", (owner_id, product_id))
    conn.commit()


def tx_history(uid: int, limit: int = 10):
    cur.execute("""
        SELECT type, amount, balance_after, note, created_ts
        FROM transactions
        WHERE user_id=?
        ORDER BY tx_id DESC
        LIMIT ?
    """, (uid, limit))
    return cur.fetchall()


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


def get_open_ticket(from_id: int, to_id: int) -> Optional[int]:
    cur.execute(
        "SELECT ticket_id FROM tickets WHERE from_id=? AND to_id=? AND status='open' ORDER BY ticket_id DESC LIMIT 1",
        (from_id, to_id),
    )
    r = cur.fetchone()
    return int(r["ticket_id"]) if r else None


# =========================
# UI Helpers
# =========================
def main_menu_kb():
    rows = [
        [InlineKeyboardButton("🛒 Products", callback_data="U_PRODUCTS")],
        [InlineKeyboardButton("💰 Wallet / Deposit", callback_data="U_WALLET")],
        [InlineKeyboardButton("📜 History", callback_data="U_HISTORY")],
        [InlineKeyboardButton("🆘 Support", callback_data="U_SUPPORT")],
        [InlineKeyboardButton(f"⭐ Become Seller ({SELLER_SUB_PRICE:g}/{SELLER_SUB_DAYS}d)", callback_data="U_BECOME_SELLER")],
        [InlineKeyboardButton("🏪 Seller Panel", callback_data="S_PANEL")],
    ]
    # show admin entry if admin
    return InlineKeyboardMarkup(rows)


def back_to_main_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]])


# =========================
# Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    err = must_have_config()
    if err:
        await update.message.reply_text(
            f"❌ Bot is not configured: {err}\n\n"
            "Set Railway Variables:\n"
            "- BOT_TOKEN\n- ADMIN_ID\n- USDT_TRC20\n"
        )
        return

    u = update.effective_user
    ensure_user(u.id, u.username or "")

    kb = main_menu_kb()
    # add admin button only for admin
    if u.id == ADMIN_ID:
        kb = InlineKeyboardMarkup(kb.inline_keyboard + [[InlineKeyboardButton("👑 Admin Panel", callback_data="A_PANEL")]])

    await update.message.reply_text(
        f"Welcome to *{STORE_NAME}*",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username or "")
    if uid == ADMIN_ID:
        await update.message.reply_text(
            "👑 Super Admin Panel",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Open Admin Panel", callback_data="A_PANEL")]]),
        )
    else:
        await update.message.reply_text(
            "🏪 Seller Panel",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Open Seller Panel", callback_data="S_PANEL")]]),
        )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
    await update.message.reply_text(
        "👑 Super Admin Panel",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Open Admin Panel", callback_data="A_PANEL")]]),
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid, q.from_user.username or "")

    data = q.data

    # Back
    if data == "BACK_MAIN":
        kb = main_menu_kb()
        if uid == ADMIN_ID:
            kb = InlineKeyboardMarkup(kb.inline_keyboard + [[InlineKeyboardButton("👑 Admin Panel", callback_data="A_PANEL")]])
        await q.edit_message_text(f"Welcome to *{STORE_NAME}*", parse_mode="Markdown", reply_markup=kb)
        return

    # ===== USER =====
    if data == "U_WALLET":
        u = get_user(uid)
        txt = (
            f"💰 *Wallet*\n\n"
            f"Balance: `{float(u['balance']):.2f} {CURRENCY}`\n\n"
            f"Deposit Address ({CURRENCY} TRC20):\n`{USDT_TRC20}`\n\n"
            "To request deposit approval, tap below."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Request Deposit Approval", callback_data="U_DEP_REQ")],
            [InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]
        ])
        await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)
        return

    if data == "U_DEP_REQ":
        context.user_data["awaiting_deposit_amount"] = True
        await q.edit_message_text(
            "➕ Send the deposit amount (numbers only).\nExample: `10` or `25.5`",
            parse_mode="Markdown",
            reply_markup=back_to_main_kb(),
        )
        return

    if data == "U_HISTORY":
        rows = tx_history(uid, 10)
        if not rows:
            await q.edit_message_text("📜 No transactions yet.", reply_markup=back_to_main_kb())
            return
        text = "📜 *Last 10 Transactions*\n\n"
        for r in rows:
            amt = float(r["amount"])
            text += f"• {r['type']} | {amt:+g} {CURRENCY} | bal {float(r['balance_after']):.2f}\n"
            if r["note"]:
                text += f"  _{r['note']}_\n"
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_to_main_kb())
        return

    if data == "U_PRODUCTS":
        items = list_products(0)
        if not items:
            await q.edit_message_text("🛒 No products in main store yet.", reply_markup=back_to_main_kb())
            return

        text = "🛒 *Main Store Products*\n\n"
        kb_rows = []
        for p in items[:30]:
            text += f"• [{p['product_id']}] {p['name']} — {float(p['price']):.2f} {CURRENCY}\n"
            kb_rows.append([InlineKeyboardButton(f"Buy #{p['product_id']}", callback_data=f"BUY:0:{p['product_id']}")])
        kb_rows.append([InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if data.startswith("BUY:"):
        try:
            _, sid, pid = data.split(":")
            sid = int(sid)
            pid = int(pid)
        except:
            await q.edit_message_text("❌ Invalid buy request.", reply_markup=back_to_main_kb())
            return

        cur.execute("SELECT * FROM products WHERE owner_id=? AND product_id=? AND active=1", (sid, pid))
        p = cur.fetchone()
        if not p:
            await q.edit_message_text("❌ Product not found.", reply_markup=back_to_main_kb())
            return

        price = float(p["price"])
        u = get_user(uid)
        if float(u["balance"]) < price:
            await q.edit_message_text(
                f"❌ Insufficient balance.\n\nPrice: {price:.2f} {CURRENCY}\nYour balance: {float(u['balance']):.2f} {CURRENCY}",
                reply_markup=back_to_main_kb(),
            )
            return

        # deduct buyer
        add_balance(uid, -price, uid, "purchase", f"Bought {p['name']} (seller {sid})")
        # credit seller if needed
        if sid != 0:
            add_balance(sid, +price, uid, "sale", f"Sold {p['name']} to {uid}")
            # remember last seller for support
            cur.execute("UPDATE users SET last_support_target=? WHERE user_id=?", (sid, uid))
            conn.commit()

        await q.edit_message_text(
            f"✅ Purchase successful!\n\nYou bought: *{p['name']}*\nPaid: `{price:.2f} {CURRENCY}`",
            parse_mode="Markdown",
            reply_markup=back_to_main_kb(),
        )
        return

    if data == "U_SUPPORT":
        u = get_user(uid)
        last_sid = int(u["last_support_target"] or 0)

        kb = [[InlineKeyboardButton("👑 Contact Admin", callback_data="SUPPORT_TO:ADMIN")]]
        if last_sid != 0:
            kb.append([InlineKeyboardButton(f"🏪 Contact Seller ({last_sid})", callback_data=f"SUPPORT_TO:{last_sid}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")])

        await q.edit_message_text(
            "🆘 *Support*\n\nChoose who to contact, then send your message.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if data.startswith("SUPPORT_TO:"):
        target = data.split(":", 1)[1]
        to_id = ADMIN_ID if target == "ADMIN" else int(target)
        context.user_data["support_to_id"] = to_id

        await q.edit_message_text(
            f"✉️ Now send your support message.\n(It will go to `{to_id}`)\n\nSend `cancel` to stop.",
            parse_mode="Markdown",
            reply_markup=back_to_main_kb(),
        )
        return

    if data == "U_BECOME_SELLER":
        u = get_user(uid)
        price = float(SELLER_SUB_PRICE)
        if float(u["balance"]) < price:
            await q.edit_message_text(
                f"⭐ *Become Seller*\n\n"
                f"Price: `{price:.2f} {CURRENCY}` for `{SELLER_SUB_DAYS}` days\n\n"
                f"Your balance: `{float(u['balance']):.2f} {CURRENCY}`\n\n"
                "Deposit first in Wallet.",
                parse_mode="Markdown",
                reply_markup=back_to_main_kb(),
            )
            return

        add_balance(uid, -price, uid, "sub", f"Seller subscription +{SELLER_SUB_DAYS} days")
        ensure_seller(uid)
        new_until = set_seller_subscription(uid, SELLER_SUB_DAYS)

        await q.edit_message_text(
            f"✅ You are now a seller!\n\n"
            f"Subscription valid until (UTC): `{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(new_until))}`\n\n"
            "Open Seller Panel.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏪 Seller Panel", callback_data="S_PANEL")],
                [InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]
            ]),
        )
        return

    # ===== SELLER =====
    if data == "S_PANEL":
        ok, msg = seller_can_use(uid)
        if not ok:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐ Pay Subscription (Main Store)", callback_data="U_BECOME_SELLER")],
                [InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]
            ])
            await q.edit_message_text(msg, reply_markup=kb)
            return

        s = get_seller(uid)
        wallet = s["wallet_address"] or "(not set)"
        sub_until = int(s["sub_until_ts"])
        txt = (
            "🏪 *Seller Panel*\n\n"
            f"Wallet: `{wallet}`\n"
            f"Subscription until (UTC): `{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(sub_until))}`\n\n"
            "Choose an option:"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 My Shop (Products)", callback_data="S_SHOP")],
            [InlineKeyboardButton("💳 Edit My Wallet Address", callback_data="S_SET_WALLET")],
            [InlineKeyboardButton("➕ Add Product", callback_data="S_ADD_PROD")],
            [InlineKeyboardButton("🗑️ Remove Product", callback_data="S_DEL_PROD")],
            [InlineKeyboardButton("👤 Edit User Balance", callback_data="S_EDIT_USER_BAL")],
            [InlineKeyboardButton("🆘 Seller Support Inbox", callback_data="S_TICKETS")],
            [InlineKeyboardButton("⭐ Pay Subscription (Main Store)", callback_data="U_BECOME_SELLER")],
            [InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]
        ])
        await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)
        return

    if data == "S_SHOP":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=back_to_main_kb())
            return
        items = list_products(uid)
        text = "🛒 *My Shop Products*\n\n"
        if not items:
            text += "_No products yet._\n"
        else:
            for p in items[:50]:
                cc = f" / {p['co_category']}" if p["co_category"] else ""
                text += f"• [{p['product_id']}] {p['category']}{cc} — {p['name']} — {float(p['price']):.2f} {CURRENCY}\n"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Open My Shop (Buyer View)", callback_data="S_BUYER_VIEW")],
            [InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]
        ])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    if data == "S_BUYER_VIEW":
        items = list_products(uid)
        if not items:
            await q.edit_message_text("🛒 Your shop has no products.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]]))
            return
        text = f"🏪 *Seller Shop ({uid})*\n\n"
        kb_rows = []
        for p in items[:30]:
            text += f"• [{p['product_id']}] {p['name']} — {float(p['price']):.2f} {CURRENCY}\n"
            kb_rows.append([InlineKeyboardButton(f"Buy #{p['product_id']}", callback_data=f"BUY:{uid}:{p['product_id']}")])
        kb_rows.append([InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if data == "S_SET_WALLET":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=back_to_main_kb())
            return
        context.user_data["awaiting_seller_wallet"] = True
        await q.edit_message_text("💳 Send your new wallet address now.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]]))
        return

    if data == "S_ADD_PROD":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=back_to_main_kb())
            return
        context.user_data["awaiting_add_product"] = True
        await q.edit_message_text(
            "➕ Send product in this format:\n\n"
            "`category | co-category | name | price`\n\n"
            "Example:\n`PUBG | UC | 325 UC | 4.50`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]])
        )
        return

    if data == "S_DEL_PROD":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=back_to_main_kb())
            return
        items = list_products(uid)
        if not items:
            await q.edit_message_text("No products to remove.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]]))
            return
        text = "🗑️ Send the product_id to remove.\n\nYour products:\n"
        for p in items[:50]:
            text += f"• {p['product_id']} — {p['name']}\n"
        context.user_data["awaiting_del_product"] = True
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]]))
        return

    if data == "S_EDIT_USER_BAL":
        ok, msg = seller_can_use(uid)
        if not ok:
            await q.edit_message_text(msg, reply_markup=back_to_main_kb())
            return
        context.user_data["awaiting_seller_editbal"] = True
        await q.edit_message_text(
            "👤 Send in format:\n`user_id amount`\n\nExample: `123456789 +10`\n(Use + or -)",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]])
        )
        return

    if data == "S_TICKETS":
        cur.execute("""
            SELECT ticket_id, from_id
            FROM tickets
            WHERE to_id=? AND status='open'
            ORDER BY ticket_id DESC
            LIMIT 10
        """, (uid,))
        rows = cur.fetchall()
        if not rows:
            await q.edit_message_text("🆘 No open support tickets.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]]))
            return

        text = "🆘 *Open Tickets*\n\n"
        kb = []
        for r in rows:
            text += f"• Ticket #{r['ticket_id']} from `{r['from_id']}`\n"
            kb.append([InlineKeyboardButton(f"Open Ticket #{r['ticket_id']}", callback_data=f"TICKET_OPEN:{r['ticket_id']}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("TICKET_OPEN:"):
        tid = int(data.split(":")[1])
        cur.execute("SELECT * FROM tickets WHERE ticket_id=?", (tid,))
        t = cur.fetchone()
        if not t:
            await q.edit_message_text("Ticket not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]]))
            return

        cur.execute("""
            SELECT sender_id, message
            FROM ticket_messages
            WHERE ticket_id=?
            ORDER BY msg_id ASC
            LIMIT 20
        """, (tid,))
        msgs = cur.fetchall()

        text = f"🆘 *Ticket #{tid}*\nFrom: `{t['from_id']}`\n\n"
        if not msgs:
            text += "_No messages yet._\n"
        else:
            for m in msgs:
                who = "User" if int(m["sender_id"]) == int(t["from_id"]) else "Seller/Admin"
                text += f"{who}: {m['message']}\n"

        context.user_data["reply_ticket_id"] = tid
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✉️ Reply (send message now)", callback_data="TICKET_REPLY")],
            [InlineKeyboardButton("✅ Close Ticket", callback_data=f"TICKET_CLOSE:{tid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="S_TICKETS")]
        ])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    if data == "TICKET_REPLY":
        if not context.user_data.get("reply_ticket_id"):
            await q.edit_message_text("No ticket selected.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]]))
            return
        context.user_data["awaiting_ticket_reply"] = True
        await q.edit_message_text("Send your reply message now.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]]))
        return

    if data.startswith("TICKET_CLOSE:"):
        tid = int(data.split(":")[1])
        cur.execute("UPDATE tickets SET status='closed' WHERE ticket_id=?", (tid,))
        conn.commit()
        context.user_data.pop("reply_ticket_id", None)
        await q.edit_message_text("✅ Ticket closed.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="S_PANEL")]]))
        return

    # ===== ADMIN =====
    if data == "A_PANEL":
        if uid != ADMIN_ID:
            await q.edit_message_text("❌ Admin only.", reply_markup=back_to_main_kb())
            return

        cur.execute("SELECT COUNT(*) AS c FROM users")
        total_users = int(cur.fetchone()["c"])
        cur.execute("SELECT COUNT(*) AS c FROM sellers")
        total_sellers = int(cur.fetchone()["c"])

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Stats", callback_data="A_STATS")],
            [InlineKeyboardButton("💰 Edit Balance", callback_data="A_EDIT_BAL")],
            [InlineKeyboardButton("⏳ Restrict Seller", callback_data="A_RESTRICT")],
            [InlineKeyboardButton("🚫 Ban/Unban Seller", callback_data="A_BAN")],
            [InlineKeyboardButton("✅ Approve Deposits", callback_data="A_DEPOSITS")],
            [InlineKeyboardButton("🛒 Main Store Products", callback_data="A_MAIN_PRODUCTS")],
            [InlineKeyboardButton("⬅️ Back", callback_data="BACK_MAIN")]
        ])
        await q.edit_message_text(
            f"👑 *Super Admin Panel*\n\nUsers: `{total_users}`\nSellers: `{total_sellers}`",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return

    if data == "A_STATS":
        if uid != ADMIN_ID:
            await q.edit_message_text("❌ Admin only.", reply_markup=back_to_main_kb())
            return
        cur.execute("SELECT COUNT(*) AS c FROM users")
        total_users = int(cur.fetchone()["c"])
        cur.execute("SELECT COUNT(*) AS c FROM sellers")
        total_sellers = int(cur.fetchone()["c"])
        await q.edit_message_text(
            f"📊 *Stats*\n\nTotal Users: `{total_users}`\nTotal Sellers: `{total_sellers}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A_PANEL")]])
        )
        return

    if data == "A_EDIT_BAL":
        if uid != ADMIN_ID:
            await q.edit_message_text("❌ Admin only.", reply_markup=back_to_main_kb())
            return
        context.user_data["awaiting_admin_editbal"] = True
        await q.edit_message_text(
            "💰 Send:\n`user_id amount`\nExample: `123456789 +50` or `123456789 -10`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A_PANEL")]])
        )
        return

    if data == "A_RESTRICT":
        if uid != ADMIN_ID:
            await q.edit_message_text("❌ Admin only.", reply_markup=back_to_main_kb())
            return
        context.user_data["awaiting_admin_restrict"] = True
        await q.edit_message_text(
            "⏳ Send:\n`seller_id days`\nExample: `123456789 14` (restrict 14 days)",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A_PANEL")]])
        )
        return

    if data == "A_BAN":
        if uid != ADMIN_ID:
            await q.edit_message_text("❌ Admin only.", reply_markup=back_to_main_kb())
            return
        context.user_data["awaiting_admin_ban"] = True
        await q.edit_message_text(
            "🚫 Send:\n`seller_id ban`\nExamples:\n`123456789 ban`\n`123456789 unban`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A_PANEL")]])
        )
        return

    if data == "A_DEPOSITS":
        if uid != ADMIN_ID:
            await q.edit_message_text("❌ Admin only.", reply_markup=back_to_main_kb())
            return

        cur.execute("""
            SELECT dep_id, user_id, amount, proof
            FROM deposit_requests
            WHERE status='pending'
            ORDER BY dep_id ASC
            LIMIT 10
        """)
        rows = cur.fetchall()
        if not rows:
            await q.edit_message_text("✅ No pending deposits.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A_PANEL")]]))
            return

        text = "✅ *Pending Deposits*\n\n"
        kb = []
        for r in rows:
            text += f"• Dep #{r['dep_id']} | user `{r['user_id']}` | `{float(r['amount']):g}` {CURRENCY}\n"
            if r["proof"]:
                text += f"  proof: {r['proof']}\n"
            kb.append([
                InlineKeyboardButton(f"Approve #{r['dep_id']}", callback_data=f"DEP_OK:{r['dep_id']}"),
                InlineKeyboardButton(f"Reject #{r['dep_id']}", callback_data=f"DEP_NO:{r['dep_id']}")
            ])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="A_PANEL")])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("DEP_OK:") or data.startswith("DEP_NO:"):
        if uid != ADMIN_ID:
            await q.edit_message_text("❌ Admin only.", reply_markup=back_to_main_kb())
            return

        dep_id = int(data.split(":")[1])
        cur.execute("SELECT * FROM deposit_requests WHERE dep_id=? AND status='pending'", (dep_id,))
        r = cur.fetchone()
        if not r:
            await q.edit_message_text("Deposit not found or already handled.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A_DEPOSITS")]]))
            return

        if data.startswith("DEP_NO:"):
            cur.execute("UPDATE deposit_requests SET status='rejected' WHERE dep_id=?", (dep_id,))
            conn.commit()
            await q.edit_message_text(f"❌ Rejected deposit #{dep_id}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A_DEPOSITS")]]))
            return

        amount = float(r["amount"])
        user_id = int(r["user_id"])
        cur.execute("UPDATE deposit_requests SET status='approved' WHERE dep_id=?", (dep_id,))
        conn.commit()
        try:
            add_balance(user_id, amount, ADMIN_ID, "deposit_ok", f"Deposit approved #{dep_id}")
        except Exception as e:
            await q.edit_message_text(f"❌ Failed to add balance: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A_DEPOSITS")]]))
            return

        await q.edit_message_text(
            f"✅ Approved deposit #{dep_id} and added {amount:g} {CURRENCY}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A_DEPOSITS")]])
        )
        return

    if data == "A_MAIN_PRODUCTS":
        if uid != ADMIN_ID:
            await q.edit_message_text("❌ Admin only.", reply_markup=back_to_main_kb())
            return
        items = list_products(0)
        text = "🛒 *Main Store Products*\n\n"
        if not items:
            text += "_No products yet._\n"
        else:
            for p in items[:50]:
                cc = f" / {p['co_category']}" if p["co_category"] else ""
                text += f"• [{p['product_id']}] {p['category']}{cc} — {p['name']} — {float(p['price']):.2f} {CURRENCY}\n"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Main Product", callback_data="A_ADD_MAIN_PROD")],
            [InlineKeyboardButton("🗑️ Remove Main Product", callback_data="A_DEL_MAIN_PROD")],
            [InlineKeyboardButton("⬅️ Back", callback_data="A_PANEL")]
        ])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    if data == "A_ADD_MAIN_PROD":
        if uid != ADMIN_ID:
            await q.edit_message_text("❌ Admin only.", reply_markup=back_to_main_kb())
            return
        context.user_data["awaiting_admin_add_main_product"] = True
        await q.edit_message_text(
            "➕ Send main product:\n\n`category | co-category | name | price`\n\nExample:\n`Mobile Legends | Diamonds | 86 Diamonds | 2.90`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A_MAIN_PRODUCTS")]])
        )
        return

    if data == "A_DEL_MAIN_PROD":
        if uid != ADMIN_ID:
            await q.edit_message_text("❌ Admin only.", reply_markup=back_to_main_kb())
            return
        context.user_data["awaiting_admin_del_main_product"] = True
        await q.edit_message_text(
            "🗑️ Send the main product_id to remove.\nExample: `12`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="A_MAIN_PRODUCTS")]])
        )
        return

    await q.edit_message_text("Unknown action.", reply_markup=back_to_main_kb())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username or "")
    text = (update.message.text or "").strip()

    if text.lower() == "cancel":
        context.user_data.pop("support_to_id", None)
        context.user_data.pop("awaiting_deposit_amount", None)
        context.user_data.pop("awaiting_seller_wallet", None)
        context.user_data.pop("awaiting_add_product", None)
        context.user_data.pop("awaiting_del_product", None)
        context.user_data.pop("awaiting_seller_editbal", None)
        context.user_data.pop("awaiting_admin_editbal", None)
        context.user_data.pop("awaiting_admin_restrict", None)
        context.user_data.pop("awaiting_admin_ban", None)
        context.user_data.pop("awaiting_admin_add_main_product", None)
        context.user_data.pop("awaiting_admin_del_main_product", None)
        context.user_data.pop("awaiting_ticket_reply", None)
        await update.message.reply_text("✅ Cancelled.")
        return

    # Deposit request amount
    if context.user_data.pop("awaiting_deposit_amount", False):
        try:
            amount = float(text)
            if amount <= 0:
                raise ValueError("amount")
        except:
            await update.message.reply_text("❌ Invalid amount. Send a number like `10` or `25.5`.", parse_mode="Markdown")
            context.user_data["awaiting_deposit_amount"] = True
            return

        cur.execute(
            "INSERT INTO deposit_requests(user_id, amount, status, created_ts) VALUES(?,?, 'pending', ?)",
            (uid, amount, now_ts())
        )
        conn.commit()
        dep_id = cur.lastrowid

        await update.message.reply_text(f"✅ Deposit request created: #{dep_id}\nAdmin will approve it soon.")

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"💳 Pending deposit #{dep_id}\nUser: {uid}\nAmount: {amount:g} {CURRENCY}\n\nOpen /admin -> Approve Deposits."
            )
        except Exception:
            pass
        return

    # Seller wallet update
    if context.user_data.pop("awaiting_seller_wallet", False):
        ensure_seller(uid)
        cur.execute("UPDATE sellers SET wallet_address=? WHERE seller_id=?", (text, uid))
        conn.commit()
        await update.message.reply_text("✅ Seller wallet updated. Use /panel.")
        return

    # Add product (seller)
    if context.user_data.pop("awaiting_add_product", False):
        ok, msg = seller_can_use(uid)
        if not ok:
            await update.message.reply_text(msg)
            return

        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 4:
            await update.message.reply_text("❌ Wrong format. Use: `category | co-category | name | price`", parse_mode="Markdown")
            context.user_data["awaiting_add_product"] = True
            return

        cat, co, name, price_s = parts
        try:
            price = float(price_s)
            if price <= 0:
                raise ValueError("price")
        except:
            await update.message.reply_text("❌ Invalid price. Example: `4.50`", parse_mode="Markdown")
            context.user_data["awaiting_add_product"] = True
            return

        add_product(uid, cat, co, name, price)
        await update.message.reply_text("✅ Product added. Use /panel.")
        return

    # Remove product (seller)
    if context.user_data.pop("awaiting_del_product", False):
        ok, msg = seller_can_use(uid)
        if not ok:
            await update.message.reply_text(msg)
            return
        try:
            pid = int(text)
        except:
            await update.message.reply_text("❌ Send only product_id number.")
            context.user_data["awaiting_del_product"] = True
            return

        deactivate_product(uid, pid)
        await update.message.reply_text("✅ Product removed (hidden).")
        return

    # Seller edit user balance
    if context.user_data.pop("awaiting_seller_editbal", False):
        ok, msg = seller_can_use(uid)
        if not ok:
            await update.message.reply_text(msg)
            return

        m = text.split()
        if len(m) != 2:
            await update.message.reply_text("❌ Format: `user_id amount` example: `123 +10`", parse_mode="Markdown")
            context.user_data["awaiting_seller_editbal"] = True
            return
        try:
            target = int(m[0])
            amt = float(m[1])
        except:
            await update.message.reply_text("❌ Invalid values. Example: `123 +10`", parse_mode="Markdown")
            context.user_data["awaiting_seller_editbal"] = True
            return

        try:
            newb = add_balance(target, amt, uid, "edit", f"Edited by seller {uid}")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}")
            return

        await update.message.reply_text(f"✅ Updated user {target} balance. New balance: {newb:.2f} {CURRENCY}")
        return

    # Admin edit balance
    if context.user_data.pop("awaiting_admin_editbal", False):
        if uid != ADMIN_ID:
            await update.message.reply_text("❌ Admin only.")
            return
        m = text.split()
        if len(m) != 2:
            await update.message.reply_text("❌ Format: `user_id amount` example: `123 +50`", parse_mode="Markdown")
            context.user_data["awaiting_admin_editbal"] = True
            return
        try:
            target = int(m[0])
            amt = float(m[1])
        except:
            await update.message.reply_text("❌ Invalid values.")
            context.user_data["awaiting_admin_editbal"] = True
            return
        try:
            newb = add_balance(target, amt, ADMIN_ID, "admin_edit", "Edited by superadmin")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}")
            return
        await update.message.reply_text(f"✅ Updated user {target} balance. New balance: {newb:.2f} {CURRENCY}")
        return

    # Admin restrict seller
    if context.user_data.pop("awaiting_admin_restrict", False):
        if uid != ADMIN_ID:
            await update.message.reply_text("❌ Admin only.")
            return
        m = text.split()
        if len(m) != 2:
            await update.message.reply_text("❌ Format: `seller_id days` example: `123 14`", parse_mode="Markdown")
            context.user_data["awaiting_admin_restrict"] = True
            return
        try:
            sid = int(m[0])
            days = int(m[1])
            if days <= 0:
                raise ValueError("days")
        except:
            await update.message.reply_text("❌ Invalid. Example: `123 14`")
            context.user_data["awaiting_admin_restrict"] = True
            return
        ensure_seller(sid)
        until = now_ts() + days * 86400
        cur.execute("UPDATE sellers SET restricted_until_ts=? WHERE seller_id=?", (until, sid))
        conn.commit()
        await update.message.reply_text(f"✅ Restricted seller {sid} for {days} days.")
        return

    # Admin ban/unban
    if context.user_data.pop("awaiting_admin_ban", False):
        if uid != ADMIN_ID:
            await update.message.reply_text("❌ Admin only.")
            return
        m = text.split()
        if len(m) != 2:
            await update.message.reply_text("❌ Format: `seller_id ban` or `seller_id unban`", parse_mode="Markdown")
            context.user_data["awaiting_admin_ban"] = True
            return
        try:
            sid = int(m[0])
            action = m[1].lower()
            if action not in ("ban", "unban"):
                raise ValueError("action")
        except:
            await update.message.reply_text("❌ Use: `123 ban` or `123 unban`", parse_mode="Markdown")
            context.user_data["awaiting_admin_ban"] = True
            return
        ensure_seller(sid)
        banned = 1 if action == "ban" else 0
        cur.execute("UPDATE sellers SET banned=? WHERE seller_id=?", (banned, sid))
        conn.commit()
        await update.message.reply_text(f"✅ Seller {sid} set to: {action.upper()}.")
        return

    # Admin add main product
    if context.user_data.pop("awaiting_admin_add_main_product", False):
        if uid != ADMIN_ID:
            await update.message.reply_text("❌ Admin only.")
            return
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 4:
            await update.message.reply_text("❌ Use: `category | co-category | name | price`", parse_mode="Markdown")
            context.user_data["awaiting_admin_add_main_product"] = True
            return
        cat, co, name, price_s = parts
        try:
            price = float(price_s)
            if price <= 0:
                raise ValueError("price")
        except:
            await update.message.reply_text("❌ Invalid price.", parse_mode="Markdown")
            context.user_data["awaiting_admin_add_main_product"] = True
            return
        add_product(0, cat, co, name, price)
        await update.message.reply_text("✅ Main store product added.")
        return

    # Admin del main product
    if context.user_data.pop("awaiting_admin_del_main_product", False):
        if uid != ADMIN_ID:
            await update.message.reply_text("❌ Admin only.")
            return
        try:
            pid = int(text)
        except:
            await update.message.reply_text("❌ Send only product_id number.")
            context.user_data["awaiting_admin_del_main_product"] = True
            return
        deactivate_product(0, pid)
        await update.message.reply_text("✅ Main store product removed (hidden).")
        return

    # Support message sending
    to_id = context.user_data.get("support_to_id")
    if to_id:
        tid = get_open_ticket(uid, to_id) or create_ticket(uid, to_id)
        add_ticket_message(tid, uid, text)

        if to_id != ADMIN_ID:
            cur.execute("UPDATE users SET last_support_target=? WHERE user_id=?", (int(to_id), uid))
            conn.commit()

        await update.message.reply_text("✅ Support message sent.")
        try:
            await context.bot.send_message(
                chat_id=to_id,
                text=f"🆘 Support Ticket #{tid}\nFrom: {uid}\n\nMessage:\n{text}\n\n(Open Seller Panel -> Support Inbox)"
            )
        except Exception:
            pass
        return

    # Ticket reply (seller/admin)
    if context.user_data.pop("awaiting_ticket_reply", False):
        tid = context.user_data.get("reply_ticket_id")
        if not tid:
            await update.message.reply_text("No ticket selected.")
            return
        cur.execute("SELECT * FROM tickets WHERE ticket_id=?", (tid,))
        t = cur.fetchone()
        if not t:
            await update.message.reply_text("Ticket not found.")
            return
        to_user = int(t["from_id"])
        add_ticket_message(tid, uid, text)
        await update.message.reply_text("✅ Reply sent to user.")
        try:
            await context.bot.send_message(chat_id=to_user, text=f"✅ Reply on Ticket #{tid}:\n{text}")
        except Exception:
            pass
        return

    await update.message.reply_text("Use /start to open the menu.")


def main():
    db_init()

    # Seed demo product if no main products
    cur.execute("SELECT COUNT(*) AS c FROM products WHERE owner_id=0")
    if int(cur.fetchone()["c"]) == 0:
        add_product(0, "Demo", "Demo", "Sample Product", 1.00)

    app = Application.builder().token(BOT_TOKEN or "invalid").build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("admin", admin_cmd))

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
