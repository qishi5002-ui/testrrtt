# Telegram Store Bot (Railway Ready)

## ✅ Deploy on Railway (NO CODE EDIT)
1. Upload these 4 files to GitHub:
   - main.py
   - requirements.txt
   - runtime.txt
   - README.md

2. Railway:
   - New Project -> Deploy from GitHub Repo

3. Railway Variables (Project -> Variables):
   - BOT_TOKEN = (your bot token from BotFather)
   - ADMIN_ID = (your Telegram numeric ID)
   - USDT_TRC20 = (your USDT TRC20 address)
   - STORE_NAME = RekkoShop (optional)
   - CURRENCY = USDT (optional)
   - SELLER_SUB_PRICE = 10 (optional)
   - SELLER_SUB_DAYS = 30 (optional)
   - DB_FILE = store.db (optional)

4. Railway Start Command:
   - `python main.py`

## Commands
- /start -> main menu
- /panel -> seller panel shortcut
- /admin -> super admin panel shortcut

## Notes
- Seller subscription stacks: buying again adds +30 days.
- Admin can approve deposits in Admin Panel.
