# ===================== IMPORTS =====================
import os
import sqlite3
import datetime
import hashlib
from typing import Optional, List, Tuple

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
SUPER_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "rekkoshop.db")
CURRENCY = os.getenv("CURRENCY", "USD")

PANEL_PRICE_CENTS = 1000  # $10
PANEL_DAYS = 30

DEFAULT_MAIN_SHOP_NAME = "RekkoShop"
DEFAULT_MAIN_WELCOME = "Welcome To RekkoShop , Receive your keys instantly here"
DEFAULT_BRAND = "Bot created by @RekkoOwn"

# ===================== TIME / MONEY =====================
def now_utc():
    return datetime.datetime.utcnow()

def now_iso():
    return now_utc().isoformat(timespec="seconds")

def parse_iso(s: str):
    return datetime.datetime.fromisoformat(s)

def money(cents: int):
    return f"{cents/100:.2f} {CURRENCY}"

def sha256(s: str):
    return hashlib.sha256(s.encode()).hexdigest()

# ===================== DB =====================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            owner_banned INTEGER DEFAULT 0,
            owner_restrict_until TEXT
        );

        CREATE TABLE IF NOT EXISTS shops(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            shop_name TEXT NOT NULL,
            welcome_text TEXT NOT NULL,
            panel_until TEXT,
            is_suspended INTEGER DEFAULT 0,
            suspended_reason TEXT,
            created_at TEXT NOT NULL
        );
        """)

        # Ensure main shop
        r = conn.execute("SELECT id FROM shops ORDER BY id ASC LIMIT 1").fetchone()
        if not r:
            conn.execute("""
            INSERT INTO shops(owner_id, shop_name, welcome_text, created_at)
            VALUES(?,?,?,?)
            """, (SUPER_ADMIN_ID, DEFAULT_MAIN_SHOP_NAME, DEFAULT_MAIN_WELCOME, now_iso()))

        # Panel offer text
        if not conn.execute("SELECT 1 FROM settings WHERE key='panel_offer'").fetchone():
            conn.execute("""
            INSERT INTO settings(key,value) VALUES(?,?)
            """, (
                "panel_offer",
                "⭐ Get Own Panel ($10/month)\n\n"
                "• Your own shop\n"
                "• Your own owner panel\n"
                "• Your own branding\n\n"
                "Renews automatically."
            ))

# ===================== USERS =====================
def upsert_user(u):
    with db() as conn:
        r = conn.execute("SELECT 1 FROM users WHERE user_id=?", (u.id,)).fetchone()
        if r:
            conn.execute("""
            UPDATE users SET username=?, first_name=?, last_name=?, updated_at=?
            WHERE user_id=?
            """, (u.username, u.first_name, u.last_name, now_iso(), u.id))
        else:
            conn.execute("""
            INSERT INTO users(user_id,username,first_name,last_name,created_at,updated_at)
            VALUES(?,?,?,?,?,?)
            """, (u.id, u.username, u.first_name, u.last_name, now_iso(), now_iso()))

def is_super_admin(uid: int):
    return uid == SUPER_ADMIN_ID

def owner_banned(uid: int):
    with db() as conn:
        r = conn.execute("SELECT owner_banned FROM users WHERE user_id=?", (uid,)).fetchone()
        return bool(r and r["owner_banned"])

def owner_restricted(uid: int):
    with db() as conn:
        r = conn.execute("SELECT owner_restrict_until FROM users WHERE user_id=?", (uid,)).fetchone()
        if not r or not r["owner_restrict_until"]:
            return False
        return parse_iso(r["owner_restrict_until"]) > now_utc()

# ===================== SHOPS =====================
def get_main_shop_id():
    return 1

def get_shop(shop_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()

def create_shop(owner_id: int):
    with db() as conn:
        conn.execute("""
        INSERT INTO shops(owner_id, shop_name, welcome_text, created_at)
        VALUES(?,?,?,?)
        """, (owner_id, f"{owner_id}'s Shop", "Welcome! Customize your store.", now_iso()))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

def is_shop_owner(shop_id: int, uid: int):
    s = get_shop(shop_id)
    return bool(s and s["owner_id"] == uid)

def panel_active(shop_id: int):
    s = get_shop(shop_id)
    if not s or not s["panel_until"]:
        return False
    return parse_iso(s["panel_until"]) > now_utc()

def renew_panel(shop_id: int):
    until = (now_utc() + datetime.timedelta(days=PANEL_DAYS)).isoformat(timespec="seconds")
    with db() as conn:
        conn.execute("UPDATE shops SET panel_until=? WHERE id=?", (until, shop_id))

# ===================== UI =====================
def kb_home(shop_id: int, uid: int):
    rows = [
        [InlineKeyboardButton("⭐ Get Own Panel", callback_data="panel:info")]
    ]

    if is_shop_owner(shop_id, uid) and panel_active(shop_id):
        rows.insert(0, [InlineKeyboardButton("🛠 Owner Panel", callback_data="own:menu")])

    if shop_id != get_main_shop_id():
        rows.append([InlineKeyboardButton("⬅ Back to RekkoShop", callback_data="shop:main")])

    if shop_id == get_main_shop_id() and is_super_admin(uid):
        rows.append([InlineKeyboardButton("🧾 Platform", callback_data="sa:menu")])

    return InlineKeyboardMarkup(rows)

# ===================== IMPORTS =====================
import os
import sqlite3
import datetime
import hashlib
from typing import Optional, List, Tuple

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
SUPER_ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "rekkoshop.db")
CURRENCY = os.getenv("CURRENCY", "USD")

PLATFORM_USDT_TRC20_ADDRESS = os.getenv("USDT_TRC20_ADDRESS", "").strip()

PANEL_PRICE_CENTS = 1000  # $10
PANEL_DAYS = 30
PAGE_SIZE = 8

DEFAULT_MAIN_SHOP_NAME = "RekkoShop"
DEFAULT_MAIN_WELCOME = "Welcome To RekkoShop , Receive your keys instantly here"
DEFAULT_BRAND = "Bot created by @RekkoOwn"


# ===================== TIME / MONEY =====================
def now_utc():
    return datetime.datetime.utcnow()

def now_iso():
    return now_utc().isoformat(timespec="seconds")

def parse_iso(s: str):
    return datetime.datetime.fromisoformat(s)

def money(cents: int) -> str:
    return f"{cents/100:.2f} {CURRENCY}"

def to_cents(s: str) -> Optional[int]:
    try:
        v = float(s.replace(",", "."))
        if v <= 0:
            return None
        return int(round(v * 100))
    except Exception:
        return None

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def days_left(until_iso: str) -> int:
    try:
        until = parse_iso(until_iso)
        secs = (until - now_utc()).total_seconds()
        if secs <= 0:
            return 0
        return int((secs + 86399) // 86400)
    except Exception:
        return 0


# ===================== DB =====================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shops(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            shop_name TEXT NOT NULL,
            welcome_text TEXT NOT NULL,
            panel_until TEXT,
            is_suspended INTEGER DEFAULT 0,
            suspended_reason TEXT,
            created_at TEXT NOT NULL,
            wallet_address TEXT
        );

        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            last_bot_msg_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            owner_banned INTEGER DEFAULT 0,
            owner_restrict_until TEXT,
            owner_block_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS shop_users(
            shop_id INTEGER,
            user_id INTEGER,
            balance_cents INTEGER DEFAULT 0,
            reseller_logged_in INTEGER DEFAULT 0,
            PRIMARY KEY(shop_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS categories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            name TEXT,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS subcategories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            category_id INTEGER,
            name TEXT,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            category_id INTEGER,
            subcategory_id INTEGER,
            name TEXT,
            user_price_cents INTEGER,
            reseller_price_cents INTEGER,
            telegram_link TEXT,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS keys(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            product_id INTEGER,
            key_text TEXT,
            is_used INTEGER DEFAULT 0,
            used_by INTEGER,
            used_at TEXT
        );

        CREATE TABLE IF NOT EXISTS purchases(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            user_id INTEGER,
            product_id INTEGER,
            product_name TEXT,
            price_cents INTEGER,
            key_text TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS deposits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            user_id INTEGER,
            amount_cents INTEGER,
            photo_file_id TEXT,
            caption TEXT,
            status TEXT,
            created_at TEXT,
            reviewed_at TEXT,
            reviewed_by INTEGER
        );

        CREATE TABLE IF NOT EXISTS resellers(
            shop_id INTEGER,
            user_id INTEGER,
            tg_username TEXT,
            login_username TEXT,
            password_hash TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            PRIMARY KEY(shop_id, user_id)
        );
        """)

        # Ensure main shop exists
        r = conn.execute("SELECT id FROM shops WHERE id=1").fetchone()
        if not r:
            conn.execute("""
            INSERT INTO shops
            (id, owner_id, shop_name, welcome_text, created_at, wallet_address)
            VALUES (1,?,?,?,?,?)
            """, (
                SUPER_ADMIN_ID,
                DEFAULT_MAIN_SHOP_NAME,
                DEFAULT_MAIN_WELCOME,
                now_iso(),
                PLATFORM_USDT_TRC20_ADDRESS or None
            ))

        if not conn.execute("SELECT 1 FROM settings WHERE key='panel_offer'").fetchone():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                ("panel_offer",
                 "⭐ Get Own Panel ($10/month)\n\n"
                 "• Your own store\n"
                 "• Your own wallet\n"
                 "• Full owner panel\n\n"
                 "Auto-renews from YOUR shop balance.")
            )


# ===================== USERS =====================
def upsert_user(u):
    with db() as conn:
        r = conn.execute("SELECT 1 FROM users WHERE user_id=?", (u.id,)).fetchone()
        if r:
            conn.execute("""
            UPDATE users SET username=?, first_name=?, last_name=?, updated_at=?
            WHERE user_id=?
            """, (u.username, u.first_name, u.last_name, now_iso(), u.id))
        else:
            conn.execute("""
            INSERT INTO users(user_id,username,first_name,last_name,created_at,updated_at)
            VALUES(?,?,?,?,?,?)
            """, (u.id, u.username, u.first_name, u.last_name, now_iso(), now_iso()))


# ===================== SHOP USERS / BALANCE =====================
def ensure_shop_user(shop_id: int, uid: int):
    with db() as conn:
        r = conn.execute(
            "SELECT 1 FROM shop_users WHERE shop_id=? AND user_id=?",
            (shop_id, uid)
        ).fetchone()
        if not r:
            conn.execute(
                "INSERT INTO shop_users(shop_id,user_id,balance_cents,reseller_logged_in) VALUES(?,?,0,0)",
                (shop_id, uid)
            )

def get_balance(shop_id: int, uid: int) -> int:
    ensure_shop_user(shop_id, uid)
    with db() as conn:
        r = conn.execute(
            "SELECT balance_cents FROM shop_users WHERE shop_id=? AND user_id=?",
            (shop_id, uid)
        ).fetchone()
        return int(r["balance_cents"])

def add_balance_delta(shop_id: int, uid: int, delta: int):
    ensure_shop_user(shop_id, uid)
    with db() as conn:
        conn.execute(
            "UPDATE shop_users SET balance_cents=balance_cents+? WHERE shop_id=? AND user_id=?",
            (delta, shop_id, uid)
        )


# ===================== SHOPS =====================
def get_shop(shop_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()

def is_shop_owner(shop_id: int, uid: int) -> bool:
    s = get_shop(shop_id)
    return bool(s and int(s["owner_id"]) == uid)

def is_panel_active(shop_id: int) -> bool:
    s = get_shop(shop_id)
    if not s or not s["panel_until"]:
        return False
    try:
        return parse_iso(s["panel_until"]) > now_utc()
    except Exception:
        return False

# ===================== UI HELPERS =====================
def kb_back_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_wallet():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Deposit", callback_data="wallet:deposit")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_products_root():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Categories", callback_data="prod:cats")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
    ])

def kb_home(shop_id: int, uid: int):
    ensure_shop_user(shop_id, uid)

    grid = [
        [
            InlineKeyboardButton("🛍️ Products", callback_data="home:products"),
            InlineKeyboardButton("💰 Wallet", callback_data="home:wallet"),
        ],
        [
            InlineKeyboardButton("📜 History", callback_data="home:history"),
            InlineKeyboardButton("📩 Support", callback_data="home:support"),
        ],
        [
            InlineKeyboardButton("⭐ Get Own Panel", callback_data="panel:info"),
        ]
    ]

    if is_shop_owner(shop_id, uid):
        grid.append([InlineKeyboardButton("🛠️ Owner Panel", callback_data="own:menu")])

    return InlineKeyboardMarkup(grid)


# ===================== SHOP HOME TEXT =====================
def shop_home_text(shop_id: int) -> str:
    s = get_shop(shop_id)
    if not s:
        return DEFAULT_MAIN_WELCOME + "\n\n" + DEFAULT_BRAND

    if s["panel_until"] and is_panel_active(shop_id):
        left = days_left(s["panel_until"])
        return (
            f"{s['welcome_text']}\n\n"
            f"🗓 Subscription: {left} day(s) left\n\n"
            f"— {s['shop_name']}"
        )

    return f"{s['welcome_text']}\n\n— {s['shop_name']}"


# ===================== CLEAN SEND =====================
async def send_clean(update: Update, ctx, text, reply_markup=None):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    with db() as conn:
        r = conn.execute(
            "SELECT last_bot_msg_id FROM users WHERE user_id=?",
            (uid,)
        ).fetchone()
        if r and r["last_bot_msg_id"]:
            try:
                await ctx.bot.delete_message(chat_id, r["last_bot_msg_id"])
            except Exception:
                pass

    msg = await ctx.bot.send_message(chat_id, text, reply_markup=reply_markup)

    with db() as conn:
        conn.execute(
            "UPDATE users SET last_bot_msg_id=? WHERE user_id=?",
            (msg.message_id, uid)
        )


# ===================== /START =====================
async def cmd_start(update: Update, ctx):
    upsert_user(update.effective_user)
    uid = update.effective_user.id

    ctx.user_data.clear()
    ctx.user_data["active_shop_id"] = 1

    ensure_shop_user(1, uid)

    await send_clean(
        update,
        ctx,
        shop_home_text(1),
        reply_markup=kb_home(1, uid)
    )


# ===================== CALLBACK HANDLER =====================
async def on_cb(update: Update, ctx):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    upsert_user(q.from_user)

    shop_id = ctx.user_data.get("active_shop_id", 1)
    ensure_shop_user(shop_id, uid)

    data = q.data

    # ================= HOME =================
    if data == "home:menu":
        ctx.user_data.clear()
        ctx.user_data["active_shop_id"] = shop_id
        return await q.edit_message_text(
            shop_home_text(shop_id),
            reply_markup=kb_home(shop_id, uid)
        )

    if data == "home:wallet":
        bal = get_balance(shop_id, uid)
        s = get_shop(shop_id)
        addr = s["wallet_address"] if s else None
        txt = (
            f"💰 Wallet\n\n"
            f"Balance: {money(bal)}\n\n"
            f"USDT Address:\n{addr or 'Not set yet'}"
        )
        return await q.edit_message_text(txt, reply_markup=kb_wallet())

    if data == "home:products":
        return await q.edit_message_text("🛍️ Products", reply_markup=kb_products_root())

    # ================= GET FILES (FIXED) =================
    if data.startswith("getfiles:"):
        pid = int(data.split(":")[1])

        p = get_product(shop_id, pid)
        if not p:
            return await q.answer("Product not found", show_alert=True)

        link = (p["telegram_link"] or "").strip()
        if not link:
            return await q.answer("No Telegram link set", show_alert=True)

        # delete previous message
        try:
            await ctx.bot.delete_message(
                chat_id=q.message.chat_id,
                message_id=q.message.message_id
            )
        except Exception:
            pass

        # send JOIN button (no text link exposed)
        await ctx.bot.send_message(
            chat_id=q.message.chat_id,
            text="📥 Access your files:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➡️ Join Channel", url=link)],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ])
        )
        return

    # ================= PANEL INFO =================
    if data == "panel:info":
        with db() as conn:
            offer = conn.execute(
                "SELECT value FROM settings WHERE key='panel_offer'"
            ).fetchone()["value"]

        bal = get_balance(1, uid)
        txt = (
            f"{offer}\n\n"
            f"Price: {money(PANEL_PRICE_CENTS)} / month\n"
            f"Your balance: {money(bal)}"
        )

        return await q.edit_message_text(
            txt,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Buy", callback_data="panel:buy")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="home:menu")]
            ])
        )

    if data == "panel:buy":
        if get_balance(1, uid) < PANEL_PRICE_CENTS:
            return await q.answer("Not enough balance", show_alert=True)

        add_balance_delta(1, uid, -PANEL_PRICE_CENTS)

        with db() as conn:
            conn.execute("""
            INSERT INTO shops(owner_id, shop_name, welcome_text, panel_until, created_at)
            VALUES(?,?,?,?,?)
            """, (
                uid,
                f"{uid}'s Shop",
                "Welcome to your shop!",
                (now_utc() + datetime.timedelta(days=PANEL_DAYS)).isoformat(),
                now_iso()
            ))
            sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        ctx.user_data["active_shop_id"] = sid
        ensure_shop_user(sid, uid)

        return await q.edit_message_text(
            f"✅ Panel activated!\n\nShop ID: {sid}",
            reply_markup=kb_home(sid, uid)
        )

    # ================= FALLBACK =================
    return await q.answer("Unknown action", show_alert=True)

# ===================== TEXT HANDLER =====================
async def on_text(update: Update, ctx):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    shop_id = ctx.user_data.get("active_shop_id", 1)
    ensure_shop_user(shop_id, uid)

    text = (update.message.text or "").strip()
    flow = ctx.user_data.get("flow")

    # ---------------- Deposit custom amount ----------------
    if flow == "dep_custom":
        amt = to_cents(text)
        if amt is None:
            return await send_clean(update, ctx, "Send amount like 10 or 10.5", kb_back_home())
        ctx.user_data["dep_amount"] = amt
        ctx.user_data["flow"] = "dep_wait_photo"
        return await send_clean(update, ctx, f"✅ Amount set: {money(amt)}\nSend payment screenshot.", kb_back_home())

    # ---------------- Support message ----------------
    if flow == "support_send":
        add_support_msg(shop_id, uid, text)
        owner_id = get_shop(shop_id)["owner_id"]
        try:
            await ctx.bot.send_message(owner_id, f"📩 Support from {uid}:\n\n{text}")
        except Exception:
            pass
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Message sent.", kb_home(shop_id, uid))

    # ---------------- Owner edit store ----------------
    if flow == "own_edit_store":
        if "|" not in text:
            return await send_clean(update, ctx, "Format: Name | Welcome text", kb_home(shop_id, uid))
        name, welcome = [x.strip() for x in text.split("|", 1)]
        set_shop_profile(shop_id, name, welcome)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Store updated.", kb_home(shop_id, uid))

    # ---------------- Owner wallet address ----------------
    if flow == "own_wallet_edit":
        if text == "-":
            set_shop_wallet(shop_id, None)
        else:
            set_shop_wallet(shop_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Wallet updated.", kb_home(shop_id, uid))

    # ---------------- Owner add category ----------------
    if flow == "own_cat_add":
        add_category(shop_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Category added.", kb_home(shop_id, uid))

    # ---------------- Owner add subcategory ----------------
    if flow == "own_sub_add":
        cat_id = ctx.user_data.get("cat_id")
        add_subcategory(shop_id, cat_id, text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Co-category added.", kb_home(shop_id, uid))

    # ---------------- Owner add product ----------------
    if flow == "own_prod_add":
        if "|" not in text:
            return await send_clean(update, ctx, "Format: Name | user_price | reseller_price", kb_home(shop_id, uid))
        name, up, rp = [x.strip() for x in text.split("|")]
        upc = to_cents(up)
        rpc = to_cents(rp)
        add_product(shop_id, ctx.user_data["cat_id"], ctx.user_data["sub_id"], name, upc, rpc)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Product added.", kb_home(shop_id, uid))

    # ---------------- Owner add keys ----------------
    if flow == "own_keys_add":
        pid = ctx.user_data.get("pid")
        count = add_keys(shop_id, pid, text.splitlines())
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, f"✅ Added {count} keys.", kb_home(shop_id, uid))

    # ---------------- Owner reply to user ----------------
    if flow == "own_reply_user":
        target_uid = ctx.user_data.get("selected_user")
        try:
            await ctx.bot.send_message(target_uid, f"📩 Reply from owner:\n\n{text}")
        except Exception:
            pass
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Reply sent.", kb_home(shop_id, uid))

    # ---------------- Balance edit (owner/admin) ----------------
    if flow in ("own:bal_add", "own:bal_sub", "own:bal_set"):
        target_uid = ctx.user_data.get("selected_user")
        amt = to_cents(text)
        if amt is None:
            return await send_clean(update, ctx, "Invalid amount.", kb_home(shop_id, uid))

        if flow == "own:bal_add":
            add_balance_delta(shop_id, target_uid, amt)
        elif flow == "own:bal_sub":
            add_balance_delta(shop_id, target_uid, -amt)
        else:
            set_balance_absolute(shop_id, target_uid, amt)

        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Balance updated.", kb_home(shop_id, uid))

    # ---------------- Super admin panel offer ----------------
    if flow == "sa_offer_edit":
        set_panel_offer_text(text)
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "✅ Offer text updated.", kb_home(shop_id, uid))

    return


# ===================== PHOTO HANDLER =====================
async def on_photo(update: Update, ctx):
    upsert_user(update.effective_user)
    uid = update.effective_user.id
    shop_id = ctx.user_data.get("active_shop_id", 1)

    if ctx.user_data.get("flow") != "dep_wait_photo":
        return

    amt = ctx.user_data.get("dep_amount")
    if not amt:
        ctx.user_data["flow"] = None
        return await send_clean(update, ctx, "Amount missing.", kb_home(shop_id, uid))

    file_id = update.message.photo[-1].file_id
    caption = update.message.caption or ""

    dep_id = create_deposit(shop_id, uid, amt, file_id, caption)
    ctx.user_data["flow"] = None

    owner_id = get_shop(shop_id)["owner_id"]
    try:
        await ctx.bot.send_photo(
            owner_id,
            file_id,
            caption=f"💳 Deposit #{dep_id}\nUser: {uid}\nAmount: {money(amt)}"
        )
    except Exception:
        pass

    return await send_clean(update, ctx, f"✅ Deposit submitted (ID #{dep_id})", kb_home(shop_id, uid))


# ===================== BOOT =====================
async def post_init(app):
    init_db()

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    if SUPER_ADMIN_ID == 0:
        raise RuntimeError("ADMIN_ID missing")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, on_text))

    app.run_polling()

if __name__ == "__main__":
    main()
