# Solana Meme Coin Bot — Setup Guide

Same bot for you and your friend. Each person needs their **own** wallet, Telegram bot, and Railway account.

**Latest stable commit:** `fd0eff5` or newer

---

## Part 1 — Accounts to create (friend does this first)

1. **Phantom wallet** — [phantom.app](https://phantom.app)
2. **Helius API key** (free) — [helius.dev](https://helius.dev)
3. **Telegram bot** — message `@BotFather` → `/newbot` → save token + chat ID
4. **GitHub account** — [github.com](https://github.com)
5. **Railway account** — [railway.app](https://railway.app) (free tier works)
6. **Cursor** — [cursor.com](https://cursor.com) (code editor)

---

## Part 2 — Laptop setup (Cursor)

### Install
- Python 3.11+ from [python.org](https://python.org)
- Git from [git-scm.com](https://git-scm.com)
- Cursor

### Clone the bot
Open Terminal in Cursor:

```bash
git clone https://github.com/az1234567aa/solana-trading-bot.git
cd solana-trading-bot
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Create `.env` file (in project folder)
```
WALLET_PRIVATE_KEY=your_phantom_exported_key
HELIUS_API_KEY=your_helius_key
TELEGRAM_BOT_TOKEN=your_botfather_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
PAPER_TRADE=false
```

**Get Phantom private key:** Phantom → Settings → Security → Export Private Key

**Get Telegram chat ID:** message `@userinfobot` on Telegram

### Test locally (optional)
```bash
python main.py
```
You should see:
```
Solana Memecoin Trading Bot starting
Copy trading ON — 7 wallets
Market scanner started
```
Press `Ctrl+C` to stop. For 24/7, use Railway (Part 3).

---

## Part 3 — Railway (runs 24/7)

1. Go to [railway.app](https://railway.app) → **New Project**
2. **Deploy from GitHub** → connect GitHub → select `solana-trading-bot`
   - Friend can **fork** the repo to their own GitHub first, then deploy their fork
3. Open the service → **Variables** → add same keys as `.env`:
   - `WALLET_PRIVATE_KEY`
   - `HELIUS_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `PAPER_TRADE=false`
4. **Settings** → start command should be: `python main.py` (from Procfile)
5. Wait for **Active** status
6. Check **Logs** for `Solana Memecoin Trading Bot starting`

---

## Part 4 — Fund the wallet

- Keep **~0.1 SOL** for trading + gas
- Bot sells coins → **USDC**
- If only USDC in wallet: swap some to SOL in Phantom

---

## What the bot does

- Scans DexScreener + copies 7 vetted GMGN traders
- Max **3 buys/day**, max **2 open positions**
- Stops buying after **-$5** daily loss
- ~**$2 max** per trade
- Auto sells to **USDC** (stop loss, take profit, time stop)
- **Telegram** on every buy/sell with running PnL

---

## Important rules

- **Never share** private keys in Discord/chat
- **Don't run** same wallet on two machines at once (laptop + Railway = pick one, or use Railway only)
- Each friend = own wallet, own Telegram, own Railway
- Trust **Phantom balance** over individual trade messages for total profit

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Crashed on deploy | Check logs; pull latest `git pull` |
| No trades | Add SOL to wallet (need ~0.1 SOL) |
| No Telegram | Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` |
| `wallet too low` | Swap USDC → SOL in Phantom |
