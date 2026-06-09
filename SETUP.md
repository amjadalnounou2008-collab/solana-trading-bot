# Solana Meme Coin Bot — Complete Beginner Setup

**Read this top to bottom. Do not skip steps.**

This is the same bot your friend uses. You get your **own** wallet, Telegram, and Railway.  
**Never share your private key with anyone.**

Repo: https://github.com/az1234567aa/solana-trading-bot

---

## BEFORE YOU START — What you need

- A laptop (Mac or Windows)
- ~$30–50 to put in your Phantom wallet (SOL for trading)
- About 1–2 hours for first-time setup
- WiFi

---

## STEP 1 — Install 3 programs on your laptop

### A) Cursor (code editor)
1. Go to https://cursor.com
2. Click **Download**
3. Install it like any normal app
4. Open Cursor

### B) Python
1. Go to https://www.python.org/downloads/
2. Download **Python 3.11** or newer
3. Install it
4. **Windows only:** check the box **"Add Python to PATH"** during install

### C) Git
1. Go to https://git-scm.com/downloads
2. Download and install (click Next on everything — defaults are fine)

---

## STEP 2 — Create your Phantom wallet (your money lives here)

1. Go to https://phantom.app
2. Install Phantom browser extension (or phone app)
3. Click **Create New Wallet**
4. Write down your **Secret Recovery Phrase** on paper — never share it
5. Set a password
6. You now have a Solana wallet — this is **yours only**

**Fund it later (Step 8):** you need ~0.1 SOL (~$15–20) to trade

---

## STEP 3 — Create Telegram bot (alerts go here)

1. Open Telegram on your phone
2. Search **@BotFather**
3. Send: `/newbot`
4. Pick a name (e.g. `My Meme Bot`)
5. Pick a username ending in `bot` (e.g. `mymeme_alert_bot`)
6. BotFather gives you a **token** — looks like `7123456789:AAH...`  
   **Copy and save it** — this is `TELEGRAM_BOT_TOKEN`

**Get your Chat ID:**
1. Search **@userinfobot** on Telegram
2. Press **Start**
3. It shows your **Id:** number (e.g. `123456789`)  
   **Save it** — this is `TELEGRAM_CHAT_ID`

**Start your bot:**
1. Search your new bot username in Telegram
2. Press **Start**

---

## STEP 4 — Get Helius API key (free — bot reads blockchain)

1. Go to https://helius.dev
2. Sign up (free)
3. Create a new project
4. Copy your **API Key**  
   Save it — this is `HELIUS_API_KEY`

---

## STEP 5 — Download the bot code to your laptop

1. Open **Cursor**
2. Menu → **Terminal** → **New Terminal** (a black box opens at bottom)

**Mac — paste this and press Enter:**
```bash
cd ~/Desktop
git clone https://github.com/az1234567aa/solana-trading-bot.git
cd solana-trading-bot
```

**Windows — paste this and press Enter:**
```bash
cd %USERPROFILE%\Desktop
git clone https://github.com/az1234567aa/solana-trading-bot.git
cd solana-trading-bot
```

3. In Cursor: **File → Open Folder** → open the `solana-trading-bot` folder on your Desktop

---

## STEP 6 — Install bot dependencies

In the same Terminal, paste **one line at a time** (wait for each to finish):

**Mac:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

If you see errors, tell your friend who gave you this guide.

---

## STEP 7 — Create your `.env` file (secret keys)

1. In Cursor, in the `solana-trading-bot` folder, create a new file named exactly: `.env`
2. Paste this and **replace** the placeholder text with YOUR keys:

```
WALLET_PRIVATE_KEY=paste_your_phantom_private_key_here
HELIUS_API_KEY=paste_your_helius_key_here
TELEGRAM_BOT_TOKEN=paste_your_botfather_token_here
TELEGRAM_CHAT_ID=paste_your_telegram_id_here
PAPER_TRADE=false
```

**How to get Phantom private key:**
1. Open Phantom extension
2. Settings (gear icon) → **Security & Privacy**
3. **Export Private Key** → enter password
4. Copy the long string → paste as `WALLET_PRIVATE_KEY`

⚠️ **NEVER send this key to anyone. Not Discord, not screenshots, not your friend.**

3. Save the file (`Cmd+S` or `Ctrl+S`)

---

## STEP 8 — Put money in your wallet

1. Buy SOL on an exchange (Coinbase, Binance, etc.) or receive from a friend
2. Send **~0.15 SOL** to your Phantom wallet address
3. Bot uses SOL to buy meme coins
4. When it sells, money comes back as **USDC** in Phantom

**You need SOL to trade.** USDC alone is not enough until you swap some to SOL.

---

## STEP 9 — Test on laptop (5 minutes)

Terminal (with venv activated — you should see `(venv)` at the start of the line):

**Mac:**
```bash
cd ~/Desktop/solana-trading-bot
source venv/bin/activate
python main.py
```

**Windows:**
```bash
cd %USERPROFILE%\Desktop\solana-trading-bot
venv\Scripts\activate
python main.py
```

**Good — you should see:**
```
Solana Memecoin Trading Bot starting
Copy trading ON — 7 wallets
Market scanner started
```

Then Telegram message: **"Solana Bot started"**

Press **Ctrl+C** to stop (laptop test only).

---

## STEP 10 — Railway (bot runs 24/7 without laptop)

### A) Create Railway account
1. Go to https://railway.app
2. Sign up with **GitHub** (create GitHub account at github.com if needed)

### B) Put code on GitHub (your own copy)
1. Go to https://github.com/az1234567aa/solana-trading-bot
2. Click **Fork** (top right) — now it's on YOUR GitHub

### C) Deploy on Railway
1. Railway → **New Project**
2. **Deploy from GitHub repo**
3. Select your forked `solana-trading-bot`
4. Wait ~2 minutes for deploy

### D) Add your secret keys on Railway
1. Click your service (usually named after the repo)
2. Click **Variables** tab
3. Add each one (click **+ New Variable**):

| Variable name | Value |
|---------------|-------|
| `WALLET_PRIVATE_KEY` | your Phantom private key |
| `HELIUS_API_KEY` | your Helius key |
| `TELEGRAM_BOT_TOKEN` | your BotFather token |
| `TELEGRAM_CHAT_ID` | your Telegram ID |
| `PAPER_TRADE` | `false` |

4. Railway will redeploy automatically

### E) Check it's working
1. Click **Deployments** → status should be **Active** (green)
2. Click **Logs** — look for:
   ```
   Solana Memecoin Trading Bot starting
   ```
3. Check Telegram — you should get **"Solana Bot started"**

**You can close your laptop.** Railway keeps running.

---

## WHAT THE BOT DOES (same as your friend's)

- Scans meme coins on Solana
- Copies 7 vetted traders
- Max **3 buys per day**
- Max **~$2 per trade**
- Stops if you lose **$5 in one day**
- Sells everything to **USDC**
- Sends you Telegram on every buy and sell with **real profit/loss**

---

## TELEGRAM MESSAGES YOU'LL SEE

**Buy:**
```
🟢 BOUGHT — COINNAME
Cost: 0.015 SOL ($2.10)
Market cap, liquidity, scores...
Buy #1/3 today
```

**Sell:**
```
✅ CLOSED → USDC — COINNAME
Spent: $2.10
Got back: $3.50 USDC
PnL: +$1.40
Today: +$0.50
All-time: -$2.00
```

**Trust your Phantom balance for total money.** Telegram shows each trade.

---

## RULES

1. **Your wallet only** — never use someone else's keys
2. **Don't run laptop + Railway at the same time** with the same wallet (pick Railway)
3. **Start small** — ~$30–50 max while learning
4. **Meme coins are risky** — you can lose money
5. If bot says `wallet too low` → swap USDC to SOL in Phantom

---

## IF SOMETHING BREAKS

| Problem | What to do |
|---------|------------|
| Deploy **Crashed** on Railway | Click **Restart**, check **Logs**, message your friend |
| No Telegram messages | Check Variables spelling on Railway |
| No trades happening | Add more SOL to Phantom |
| `BUY skip — max 3 buys/day` | Normal — bot protecting you |
| `Trading paused for today` | Lost $5 today — resets tomorrow |

---

## CHECKLIST — done when all checked

- [ ] Phantom wallet created + funded with ~0.15 SOL
- [ ] Telegram bot created + Chat ID saved
- [ ] Helius API key saved
- [ ] `.env` file on laptop with all 5 values
- [ ] `python main.py` showed "Bot starting"
- [ ] Railway **Active** + logs show "Bot starting"
- [ ] Got Telegram "Solana Bot started" message

**You're done. Bot runs 24/7 on Railway.**
