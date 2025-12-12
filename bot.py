import os
import json
import random
import logging
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from openai import OpenAI

# ================== ENV ==================
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "1129344598"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DATA_FILE = "data.json"
AMAZON_TAG = "entscheidungshelfer-21"

# ================== LOG ==================
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("Entscheidungshelfer")

# ================== OPENAI ==================
client = OpenAI(api_key=OPENAI_API_KEY)

# ================== STATE ==================
DEFAULT_STATE = {
    "ads": {"enabled": False, "mode": "low"},
    "subscriptions": {"enabled": False, "users": []},
    "limits": {"free_per_day": 1},
    "users": {}
}

def load_state():
    if not os.path.exists(DATA_FILE):
        return DEFAULT_STATE
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(STATE, f, indent=2, ensure_ascii=False)

STATE = load_state()

TZ = timezone(timedelta(hours=1))

def today():
    return datetime.now(TZ).strftime("%Y-%m-%d")

def get_user(uid):
    u = STATE["users"].setdefault(str(uid), {"today": today(), "count": 0})
    if u["today"] != today():
        u["today"] = today()
        u["count"] = 0
    return u

def is_admin(uid): return uid == ADMIN_ID
def is_sub(uid): return STATE["subscriptions"]["enabled"] and uid in STATE["subscriptions"]["users"]

# ================== UI ==================
KEYBOARD = ReplyKeyboardMarkup(
    [
        ["✅ Entscheidung", "🧠 Pro & Contra"],
        ["🎯 Kurz-Tipp", "📊 Status"],
        ["🔄 Neustart", "ℹ️ Hilfe"],
    ],
    resize_keyboard=True
)

START_TEXT = (
    "👋 *Entscheidungshelfer*\n\n"
    "Schreib dein Thema – ich entscheide mit dir.\n\n"
    "Beispiel:\n"
    "Soll ich heute noch arbeiten oder Feierabend machen?"
)

HELP_TEXT = (
    "✅ Entscheidung – Empfehlung\n"
    "🧠 Pro & Contra – Liste\n"
    "🎯 Kurz-Tipp – 2 Sätze\n\n"
    "Ohne Abo: 1 Anfrage / Tag"
)

# ================== AI ==================
SYSTEM_PROMPT = (
    "Du bist ein klarer Entscheidungshelfer.\n"
    "Kurz, strukturiert, immer mit Empfehlung.\n"
)

def ai_answer(text, mode):
    if mode == "quick":
        task = "Gib 1 Satz Empfehlung + 1 Satz nächsten Schritt."
    elif mode == "pro":
        task = "Erstelle Pro/Contra + Mini-Empfehlung."
    else:
        task = (
            "1) Kurzfazit\n"
            "2) Pro\n"
            "3) Contra\n"
            "4) Empfehlung + nächster Schritt"
        )

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.7,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{task}\n\n{text}"}
        ]
    )
    return resp.choices[0].message.content.strip()

# ================== ADS ==================
def show_ad():
    if not STATE["ads"]["enabled"]:
        return False
    mode = STATE["ads"]["mode"]
    return random.random() < {"low": 0.1, "light": 0.25, "normal": 0.5}[mode]

AD_TEXT = (
    f"\n\n🎁 Geschenk gesucht? "
    f"Schreib Anlass + Budget – ich helfe. ({AMAZON_TAG})"
)

# ================== HANDLERS ==================
async def start(update: Update, ctx):
    await update.message.reply_text(
        START_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD
    )

async def help_cmd(update: Update, ctx):
    await update.message.reply_text(HELP_TEXT, reply_markup=KEYBOARD)

async def stats(update: Update, ctx):
    uid = update.effective_user.id
    u = get_user(uid)
    await update.message.reply_text(
        f"📊 Heute: {u['count']}/{STATE['limits']['free_per_day']}\n"
        f"Abo: {'JA' if is_sub(uid) else 'NEIN'}",
        reply_markup=KEYBOARD
    )

async def restart(update: Update, ctx):
    ctx.user_data.clear()
    await update.message.reply_text("🔄 Neustart", reply_markup=KEYBOARD)

async def ads_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id):
        return
    arg = ctx.args[0] if ctx.args else ""
    if arg in ("on", "off"):
        STATE["ads"]["enabled"] = arg == "on"
    elif arg in ("low", "light", "normal"):
        STATE["ads"]["mode"] = arg
    save_state()
    await update.message.reply_text("✅ OK")

async def sub_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id):
        return
    if ctx.args[0] == "on":
        STATE["subscriptions"]["enabled"] = True
    elif ctx.args[0] == "off":
        STATE["subscriptions"]["enabled"] = False
    elif ctx.args[0] == "add":
        STATE["subscriptions"]["users"].append(int(ctx.args[1]))
    elif ctx.args[0] == "del":
        STATE["subscriptions"]["users"].remove(int(ctx.args[1]))
    save_state()
    await update.message.reply_text("✅ OK")

async def handle(update: Update, ctx):
    text = update.message.text
    uid = update.effective_user.id
    u = get_user(uid)

    if not is_sub(uid) and u["count"] >= STATE["limits"]["free_per_day"]:
        await update.message.reply_text("⛔ Tageslimit erreicht")
        return

    mode = "decision"
    if "Kurz" in text:
        mode = "quick"
    elif "Pro" in text:
        mode = "pro"

    u["count"] += 1
    save_state()

    answer = ai_answer(text, mode)
    if show_ad() and not is_sub(uid):
        answer += AD_TEXT

    await update.message.reply_text(answer, reply_markup=KEYBOARD)

# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("ads", ads_cmd))
    app.add_handler(CommandHandler("sub", sub_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    app.run_polling()

if __name__ == "__main__":
    main()
