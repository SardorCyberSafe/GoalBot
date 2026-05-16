import json
import io
import csv
import random
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import openai

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

CONFIG_PATH = Path("config.json")
KEY_PATH = Path("bot.key")
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)

BOT_TOKEN = CONFIG["bot_token"]
ALLOWED_USERS = CONFIG.get("allowed_users", None)


def _decrypt_section(key_name: str, default: dict) -> dict:
    raw = CONFIG.get(key_name, "")
    if not raw or Fernet is None:
        return default
    try:
        key = KEY_PATH.read_bytes()
        cipher = Fernet(key)
        return json.loads(cipher.decrypt(raw.encode()).decode())
    except Exception:
        return default


AI_CONFIG = _decrypt_section("ai_encrypted", CONFIG.get("ai", {}))
AI_ENABLED = bool(AI_CONFIG.get("api_key") and AI_CONFIG.get("base_url"))

AI_CLIENT = None
if AI_ENABLED:
    AI_CLIENT = openai.OpenAI(
        api_key=AI_CONFIG["api_key"],
        base_url=AI_CONFIG["base_url"],
    )

AKADEMIK_CONFIG = _decrypt_section(
    "ai_akademik_encrypted", CONFIG.get("ai_akademik", {})
)
AKADEMIK_ENABLED = bool(
    AKADEMIK_CONFIG.get("api_key") and AKADEMIK_CONFIG.get("base_url")
)
AKADEMIK_CLIENT = None
AKADEMIK_MODELS = AKADEMIK_CONFIG.get(
    "models", ["qwen3.6-plus-preview-free", "gpt-5.5-free"]
)

if AKADEMIK_ENABLED:
    AKADEMIK_CLIENT = openai.OpenAI(
        api_key=AKADEMIK_CONFIG["api_key"],
        base_url=AKADEMIK_CONFIG["base_url"],
    )

USER_SESSION: dict[int, str] = {}
USER_CACHE: dict[int, dict] = {}

INIT_META = {
    "streak": 0,
    "best_streak": 0,
    "last_active_date": None,
    "completed_dates": [],
    "badges": [],
    "total_completed": 0,
    "pomodoro_total": 0,
}

QUOTES = [
    "Kichik qadamlar katta natijalarga olib boradi.",
    "Bugun qilgan ishing ertangi kunning poydevori.",
    "Muvaffaqiyat — bu odat, tasodif emas.",
    "Eng yaxshi vaqt — hozir. Ikkinchi eng yaxshi vaqt — ertaga.",
    "Maqsadsiz kuch — qayiqsiz suzishga o'xshaydi.",
    "TO'XTAMA! Davom et. Har bir kunning o'z g'alabasi bor.",
    "Faqat harakat natija keltiradi, orzu emas.",
    "Disciplina — bu maqsad va orzu o'rtasidagi ko'prik.",
    "21 kun — odat shakllanishi uchun eng kam vaqt.",
    "Sen qila olasan! Boshqalar qilganini sen ham qila olasan.",
]


# ─── DATA LAYER ───────────────────────────────────────────

def _user_path(user_id: int) -> Path:
    return DATA_DIR / f"{user_id}.json"


def load_user(user_id: int) -> dict:
    if user_id in USER_CACHE:
        return USER_CACHE[user_id]
    path = _user_path(user_id)
    if path.exists():
        with open(path) as f:
            data = json.load(f)
    else:
        data = {"goals": [], "meta": dict(INIT_META)}
    USER_CACHE[user_id] = data
    return data


def save_user(user_id: int):
    path = _user_path(user_id)
    data = USER_CACHE.get(user_id, {"goals": [], "meta": dict(INIT_META)})
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_goals(user_id: int) -> list:
    return load_user(user_id)["goals"]


def get_meta(user_id: int) -> dict:
    return load_user(user_id)["meta"]


def clean_old_dates(user_id: int):
    data = load_user(user_id)
    changed = False
    for g in data["goals"]:
        if g.get("deadline") and g["status"] == "active":
            try:
                dl = datetime.strptime(g["deadline"], "%Y-%m-%d").date()
                if dl < date.today():
                    g["status"] = "missed"
                    changed = True
            except ValueError:
                pass
    if changed:
        save_user(user_id)


def next_goal_id(user_id: int) -> int:
    goals = get_goals(user_id)
    return max([g["id"] for g in goals], default=0) + 1


def find_goal(user_id: int, goal_id: int):
    for g in get_goals(user_id):
        if g["id"] == goal_id:
            return g
    return None


def is_authorized(user_id: int) -> bool:
    if ALLOWED_USERS is None:
        return True
    return user_id in ALLOWED_USERS


def check_and_award_badges(user_id: int, meta: dict):
    """Check and award badges. Returns list of new badges."""
    new_badges = []
    total = meta["total_completed"]
    streak = meta["streak"]

    badge_rules = [
        (1, "🥉 Birinchi qadam", "Birinchi maqsad bajarildi"),
        (5, "🥈 Beshlik", "5 ta maqsad bajarildi"),
        (10, "🥇 O'nlik", "10 ta maqsad bajarildi"),
        (25, "🏆 Lider", "25 ta maqsad bajarildi"),
        (50, "👑 Usta", "50 ta maqsad bajarildi"),
        (100, "💎 Legend", "100 ta maqsad bajarildi"),
    ]

    for threshold, badge_name, desc in badge_rules:
        if total >= threshold:
            key = f"done_{threshold}"
            if key not in [b["key"] for b in meta["badges"]]:
                meta["badges"].append({"key": key, "name": badge_name, "desc": desc})
                new_badges.append((badge_name, desc))

    streak_rules = [
        (3, "🔥 3 kunlik seriya"),
        (7, "🔥🔥 Haftalik seriya"),
        (14, "🔥🔥🔥 Ikki hafta"),
        (30, "⭐ Oylik seriya"),
        (60, "⭐ Ikki oylik"),
        (100, "👑 100 kun"),
    ]

    for threshold, badge_name in streak_rules:
        if streak >= threshold:
            key = f"streak_{threshold}"
            if key not in [b["key"] for b in meta["badges"]]:
                meta["badges"].append({"key": key, "name": badge_name, "desc": f"{threshold} kun ketma-ket"})
                new_badges.append((badge_name, f"{threshold} kun ketma-ket"))

    save_user(user_id)
    return new_badges


def format_goal(g: dict) -> str:
    icons = {"active": "⏳", "done": "✅", "missed": "❌"}
    p_icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    icon = icons.get(g["status"], "⏳")
    picon = p_icons.get(g["priority"], "⚪")
    deadline = f"📅 {g['deadline']}" if g.get("deadline") else ""
    cat = f"📂 {g['category']}" if g.get("category") and g["category"] != "general" else ""
    repeat = f"🔁 {g['repeat']}" if g.get("repeat") else ""
    parts = [f"{icon} `[{g['id']}]` {g['name']}"]
    parts.append(f"   {picon} {g['priority']} | ⏱ {g['hours']}h {' '.join(filter(None, [deadline, cat, repeat]))}")
    return "\n".join(parts)


# ─── REMINDER CHECKER ────────────────────────────────────

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    today_str = date.today().isoformat()

    for user_id, data in list(USER_CACHE.items()):
        meta = data["meta"]
        for g in data["goals"]:
            if g["status"] != "active":
                continue
            reminder = g.get("reminder")
            if not reminder:
                continue

            sent_key = f"_remind_sent_{g['id']}"
            if meta.get(sent_key) == today_str:
                continue

            should_send = False
            if reminder == "daily":
                should_send = True
            elif reminder == "weekly":
                if now.weekday() == 0:
                    should_send = True
            elif reminder.count(":") == 1:
                try:
                    h, m = map(int, reminder.split(":"))
                    if now.hour == h and now.minute == m:
                        should_send = True
                except ValueError:
                    pass

            if should_send:
                meta[sent_key] = today_str
                save_user(user_id)
                try:
                    await context.bot.send_message(
                        user_id,
                        f"⏰ *Eslatma:* `{g['name']}`\n"
                        f"⏱ {g['hours']}h | {g['priority']} priority\n"
                        f"👉 /goal done {g['id']} — bajarildi deb belgilang",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass


# ─── MODE UI ──────────────────────────────────────────────

def get_mode_keyboard(current_mode: str) -> InlineKeyboardMarkup:
    modes = [
        ("mode1", "📋 Maqsadlar"),
        ("mode2", "📸 AI Akademik"),
    ]
    buttons = []
    for key, label in modes:
        label = f"✅ {label}" if key == current_mode else label
        buttons.append(InlineKeyboardButton(label, callback_data=f"mode_{key}"))
    return InlineKeyboardMarkup([buttons])


async def show_mode_info(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    mode = USER_SESSION.get(user_id, "mode1")
    texts = {
        "mode1": (
            "📋 *Mode 1: Maqsadlar Boshqaruvi*\n\n"
            "Asosiy:\n"
            "`/goal add <nomi> <soat>` — maqsad qo'shish\n"
            "`/goal list` — barcha maqsadlar\n"
            "`/goal done <id>` — bajarildi\n"
            "`/goal delete <id>` — o'chirish\n\n"
            "Rejalashtirish:\n"
            "`/goal priority <id> <high|medium|low>`\n"
            "`/goal deadline <id> YYYY-MM-DD`\n"
            "`/goal category <id> <nom>`\n"
            "`/goal repeat <id> <weekly|monthly|none>`\n"
            "`/goal remind <id> <daily|weekly|HH:MM|off>`\n\n"
            "Kuzatish:\n"
            "`/goal stats` — umumiy statistika\n"
            "`/goal today` — bugungi maqsadlar\n"
            "`/goal weekly` — haftalik hisobot\n"
            "`/goal streak` — seriyalar\n"
            "`/goal badges` — yutuqlar\n"
            "`/goal graph <id>` — progress grafigi\n\n"
            "Ish samaradorligi:\n"
            "`/goal pomodoro <id> <start|stop|stats>`\n"
            "`/goal plan` — kunlik reja\n"
            "`/goal advice` — AI maslahat\n"
            "`/goal ask <savol>` — AI ga savol berish\n"
            "`/goal export <csv|json>` — eksport"
        ),
        "mode2": (
            "📸 *AI Akademik — OCR + AI Tahlil*\n\n"
            "Rasmdagi qo'lda yozilgan matn, matematika, "
            "geometriya va algebra masalalarini tahlil qiladi.\n\n"
            "Ishlatish:\n"
            "1. Menga rasm yuboring (qo'lda yozilgan misol)\n"
            "2. AI matnni chiqarib, 2 xil AI ga tekshirtiradi\n"
            "3. Natijalarni Markdown/LaTeX da ko'rasiz\n\n"
            "Modellar:\n"
            "• 🤖 Qwen 3.6 Plus — 1-tekshiruv\n"
            "• 🤖 GPT 5.5 — 2-tekshiruv"
        ),
    }
    await update.message.reply_text(
        texts.get(mode, texts["mode1"]),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_mode_keyboard(mode),
    )


# ═══════════════════════════════════════════════════════════
# MODE 1 — GOAL MANAGER (ALL FEATURES)
# ═══════════════════════════════════════════════════════════

async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return
    if USER_SESSION.get(user_id) != "mode1":
        await update.message.reply_text("Avval Mode 1 ga o'ting: /mode1")
        return
    if not context.args:
        await update.message.reply_text("Yordam: /modeinfo")
        return

    sub = context.args[0].lower()
    load_user(user_id)

    handlers = {
        "add": lambda: goal_add(update, context, user_id) if len(context.args) >= 3 else invalid(),
        "list": lambda: goal_list(update, context, user_id),
        "done": lambda: goal_done(update, context, user_id) if len(context.args) >= 2 else invalid(),
        "delete": lambda: goal_delete(update, context, user_id) if len(context.args) >= 2 else invalid(),
        "priority": lambda: goal_priority(update, context, user_id) if len(context.args) >= 3 else invalid(),
        "deadline": lambda: goal_deadline(update, context, user_id) if len(context.args) >= 3 else invalid(),
        "category": lambda: goal_category(update, context, user_id) if len(context.args) >= 3 else invalid(),
        "repeat": lambda: goal_repeat(update, context, user_id) if len(context.args) >= 3 else invalid(),
        "remind": lambda: goal_remind(update, context, user_id) if len(context.args) >= 3 else invalid(),
        "stats": lambda: goal_stats(update, context, user_id),
        "today": lambda: goal_today(update, context, user_id),
        "weekly": lambda: goal_weekly(update, context, user_id),
        "streak": lambda: goal_streak(update, context, user_id),
        "badges": lambda: goal_badges(update, context, user_id),
        "graph": lambda: goal_graph(update, context, user_id) if len(context.args) >= 2 else invalid(),
        "pomodoro": lambda: goal_pomodoro(update, context, user_id) if len(context.args) >= 3 else invalid(),
        "plan": lambda: goal_plan(update, context, user_id),
        "advice": lambda: goal_advice(update, context, user_id),
        "ask": lambda: goal_ask(update, context, user_id) if len(context.args) >= 2 else invalid(),
        "export": lambda: goal_export(update, context, user_id) if len(context.args) >= 2 else invalid(),
    }

    handler = handlers.get(sub)
    if handler:
        await handler()
    else:
        await update.message.reply_text("Noto'g'ri buyruq. /modeinfo")


async def invalid():
    pass


# ─── CORE ──────────────────────────────────────────────────

async def goal_add(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    raw = " ".join(context.args[1:])
    parts = raw.rsplit(" ", 1)
    if len(parts) != 2:
        await update.message.reply_text("Format: /goal add <nomi> <soat>")
        return

    name, hours_str = parts
    try:
        hours = float(hours_str)
    except ValueError:
        await update.message.reply_text("Soat son bo'lishi kerak.")
        return
    if not name:
        await update.message.reply_text("Maqsad nomini kiriting.")
        return

    data = load_user(user_id)
    new_id = next_goal_id(user_id)
    goal = {
        "id": new_id,
        "name": name,
        "hours": hours,
        "hours_spent": 0,
        "status": "active",
        "priority": "medium",
        "deadline": None,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "completed_at": None,
        "category": "general",
        "reminder": None,
        "repeat": None,
        "pomodoro_count": 0,
    }
    data["goals"].append(goal)
    save_user(user_id)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔴 High", callback_data=f"prio_{new_id}_high"),
            InlineKeyboardButton("🟡 Medium", callback_data=f"prio_{new_id}_medium"),
            InlineKeyboardButton("🟢 Low", callback_data=f"prio_{new_id}_low"),
        ],
        [InlineKeyboardButton("📅 Muddat belgilash", callback_data=f"askdead_{new_id}")],
    ])

    await update.message.reply_text(
        f"✅ *Maqsad qo'shildi!*\n\n"
        f"📌 `[{new_id}]` {name}\n"
        f"⏱ {hours} soat\n"
        f"📂 Kategoriya: general",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


async def goal_list(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    clean_old_dates(user_id)
    goals = get_goals(user_id)
    if not goals:
        await update.message.reply_text("Hech qanday maqsad yo'q. `/goal add` bilan qo'shing.")
        return

    cats = defaultdict(list)
    for g in goals:
        cats[g.get("category", "general")].append(g)

    lines = ["📋 *Maqsadlar ro'yxati:*\n"]
    buttons = []
    for cat_name, cat_goals in sorted(cats.items()):
        lines.append(f"📂 *{cat_name}*")
        for g in cat_goals:
            lines.append(format_goal(g))
            if g["status"] == "active":
                buttons.append(InlineKeyboardButton(
                    f"{'✅' if g['status']=='done' else '⏳'} [{g['id']}] {g['name'][:15]}",
                    callback_data=f"done_{g['id']}",
                ))
        lines.append("")

    if buttons:
        rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        keyboard = InlineKeyboardMarkup(rows)
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard,
        )
    else:
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def goal_done(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        goal_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID son bo'lishi kerak.")
        return

    data = load_user(user_id)
    g = find_goal(user_id, goal_id)
    if not g:
        await update.message.reply_text(f"Maqsad ID {goal_id} topilmadi.")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    today_str = date.today().isoformat()
    g["status"] = "done"
    g["completed_at"] = now_str

    meta = data["meta"]
    meta["total_completed"] += 1

    if today_str not in meta["completed_dates"]:
        meta["completed_dates"].append(today_str)
        if meta["last_active_date"]:
            last = datetime.strptime(meta["last_active_date"], "%Y-%m-%d").date()
            if (date.today() - last).days == 1:
                meta["streak"] += 1
            elif (date.today() - last).days == 0:
                pass
            else:
                meta["streak"] = 1
        else:
            meta["streak"] = 1
        meta["last_active_date"] = today_str
        if meta["streak"] > meta["best_streak"]:
            meta["best_streak"] = meta["streak"]

    new_badges = check_and_award_badges(user_id, meta)

    # Handle repeat
    if g.get("repeat"):
        new_g = dict(g)
        new_g["id"] = next_goal_id(user_id)
        new_g["status"] = "active"
        new_g["completed_at"] = None
        new_g["created"] = now_str
        new_g["hours_spent"] = 0
        new_g["pomodoro_count"] = 0
        if g["repeat"] == "weekly":
            new_g["deadline"] = (date.today() + timedelta(days=7)).isoformat()
        elif g["repeat"] == "monthly":
            new_g["deadline"] = (date.today() + timedelta(days=30)).isoformat()
        data["goals"].append(new_g)

    save_user(user_id)

    text = f"✅ *{g['name']}* bajarildi! 🎉"
    if new_badges:
        text += "\n\n🏅 *Yangi yutuqlar:*"
        for name, desc in new_badges:
            text += f"\n• {name} — {desc}"
    if meta["streak"] > 1:
        text += f"\n\n🔥 *Streak:* {meta['streak']} kun"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def goal_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        goal_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID son bo'lishi kerak.")
        return

    data = load_user(user_id)
    for i, g in enumerate(data["goals"]):
        if g["id"] == goal_id:
            removed = data["goals"].pop(i)
            save_user(user_id)
            await update.message.reply_text(f"🗑 *{removed['name']}* o'chirildi.", parse_mode=ParseMode.MARKDOWN)
            return
    await update.message.reply_text(f"Maqsad ID {goal_id} topilmadi.")


async def goal_priority(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        goal_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID son bo'lishi kerak.")
        return
    priority = context.args[2].lower()
    if priority not in ("high", "medium", "low"):
        await update.message.reply_text("Ustuvorlik: high, medium yoki low")
        return
    g = find_goal(user_id, goal_id)
    if not g:
        await update.message.reply_text(f"ID {goal_id} topilmadi.")
        return
    g["priority"] = priority
    save_user(user_id)
    await update.message.reply_text(f"🎯 *{g['name']}* ustuvorligi: {priority}", parse_mode=ParseMode.MARKDOWN)


async def goal_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        goal_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID son bo'lishi kerak.")
        return
    try:
        dl = datetime.strptime(context.args[2], "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text("Sana YYYY-MM-DD formatida. Masalan: 2026-06-01")
        return
    if dl < date.today():
        await update.message.reply_text("Muddat o'tgan sana.")
        return
    g = find_goal(user_id, goal_id)
    if not g:
        await update.message.reply_text(f"ID {goal_id} topilmadi.")
        return
    g["deadline"] = context.args[2]
    save_user(user_id)
    await update.message.reply_text(
        f"📅 *{g['name']}* muddati: {context.args[2]} ({(dl - date.today()).days} kun qoldi)",
        parse_mode=ParseMode.MARKDOWN,
    )


async def goal_category(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        goal_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID son bo'lishi kerak.")
        return
    cat = context.args[2].lower()
    g = find_goal(user_id, goal_id)
    if not g:
        await update.message.reply_text(f"ID {goal_id} topilmadi.")
        return
    g["category"] = cat
    save_user(user_id)
    await update.message.reply_text(f"📂 *{g['name']}* kategoriyasi: {cat}", parse_mode=ParseMode.MARKDOWN)


async def goal_repeat(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        goal_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID son bo'lishi kerak.")
        return
    val = context.args[2].lower()
    if val not in ("weekly", "monthly", "none"):
        await update.message.reply_text("Qiymat: weekly, monthly yoki none")
        return
    g = find_goal(user_id, goal_id)
    if not g:
        await update.message.reply_text(f"ID {goal_id} topilmadi.")
        return
    g["repeat"] = val if val != "none" else None
    save_user(user_id)
    if g["repeat"]:
        await update.message.reply_text(f"🔁 *{g['name']}* {val} takrorlanadi", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"🔁 *{g['name']}* takrorlanishi o'chirildi", parse_mode=ParseMode.MARKDOWN)


async def goal_remind(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        goal_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID son bo'lishi kerak.")
        return
    val = context.args[2].lower()
    if val not in ("daily", "weekly", "off") and val.count(":") != 1:
        await update.message.reply_text("Qiymat: daily, weekly, HH:MM yoki off")
        return
    if val.count(":") == 1:
        try:
            h, m = map(int, val.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            await update.message.reply_text("Vaqt HH:MM formatida (00-23:00-59)")
            return
    g = find_goal(user_id, goal_id)
    if not g:
        await update.message.reply_text(f"ID {goal_id} topilmadi.")
        return
    g["reminder"] = val if val != "off" else None
    # Reset sent flag
    if user_id in USER_CACHE:
        USER_CACHE[user_id]["meta"].pop(f"_remind_sent_{goal_id}", None)
    save_user(user_id)
    if g["reminder"]:
        await update.message.reply_text(f"⏰ *{g['name']}* uchun eslatma: {val}", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"⏰ *{g['name']}* eslatmasi o'chirildi", parse_mode=ParseMode.MARKDOWN)


# ─── STATS ─────────────────────────────────────────────────

async def goal_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    clean_old_dates(user_id)
    data = load_user(user_id)
    goals = data["goals"]
    meta = data["meta"]

    total = len(goals)
    done = sum(1 for g in goals if g["status"] == "done")
    active = sum(1 for g in goals if g["status"] == "active")
    missed = sum(1 for g in goals if g["status"] == "missed")
    total_h = sum(g["hours"] for g in goals)
    done_h = sum(g["hours"] for g in goals if g["status"] == "done")

    pct = (done / total * 100) if total else 0
    bar_len = 20
    filled = int(bar_len * pct / 100)
    bar = "🟩" * filled + "⬜" * (bar_len - filled)

    cats = defaultdict(int)
    for g in goals:
        if g["status"] == "active":
            cats[g.get("category", "general")] += 1

    cat_lines = ""
    if cats:
        cat_lines = "\n📂 *Kategoriyalar:*\n"
        for name, count in sorted(cats.items(), key=lambda x: -x[1]):
            cat_lines += f"  • {name}: {count} ta\n"

    text = (
        "📊 *Statistika*\n\n"
        f"Jami: {total} ta\n"
        f"✅ Bajarilgan: {done}\n"
        f"⏳ Faol: {active}\n"
        f"❌ O'tkazilgan: {missed}\n"
        f"⏱ Jami vaqt: {total_h}h | Bajarilgan: {done_h}h\n"
        f"🏅 Jami yutuq: {len(meta['badges'])}\n"
        f"🔥 Streak: {meta['streak']} kun (eng yaxshi: {meta['best_streak']})\n"
        f"🍅 Pomodoro: {meta['pomodoro_total']}\n\n"
        f"*Taraqqiyot:*\n{bar} {pct:.0f}%"
        f"{cat_lines}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def goal_today(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    clean_old_dates(user_id)
    goals = get_goals(user_id)
    today = date.today()

    today_list = []
    upcoming = []

    for g in goals:
        if g["status"] != "active":
            continue
        if g.get("deadline"):
            dl = datetime.strptime(g["deadline"], "%Y-%m-%d").date()
            remaining = (dl - today).days
            if remaining <= 0:
                today_list.append((0, g))
            elif remaining <= 7:
                upcoming.append((remaining, g))
        else:
            today_list.append((0, g))

    if not today_list and not upcoming:
        await update.message.reply_text("Bugun uchun maqsad yo'q. `/goal add` bilan qo'shing.")
        return

    lines = []
    if today_list:
        lines.append("📌 *Bugungi maqsadlar:*\n")
        for _, g in sorted(today_list, key=lambda x: -{"high": 3, "medium": 2, "low": 1}.get(x[1]["priority"], 0)):
            dl = f" (kechikkan!)" if g.get("deadline") and datetime.strptime(g["deadline"], "%Y-%m-%d").date() < today else ""
            lines.append(f"  • `[{g['id']}]` {g['name']} — {g['hours']}h {dl}")

    if upcoming:
        lines.append("\n📅 *Yaqin kunlarda:*\n")
        for days, g in sorted(upcoming, key=lambda x: x[0]):
            lines.append(f"  • `[{g['id']}]` {g['name']} — {days} kun qoldi")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def goal_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    clean_old_dates(user_id)
    goals = get_goals(user_id)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    week_done = 0
    week_hours = 0
    day_counts = defaultdict(int)

    for g in goals:
        if g["status"] == "done" and g.get("completed_at"):
            try:
                cd = datetime.strptime(g["completed_at"], "%Y-%m-%d %H:%M").date()
                if week_start <= cd <= week_end:
                    week_done += 1
                    week_hours += g["hours"]
                    day_counts[cd.isoformat()] += 1
            except ValueError:
                pass

    # Best day
    best_day = ""
    if day_counts:
        best_day_date = max(day_counts, key=day_counts.get)
        best_day_name = datetime.strptime(best_day_date, "%Y-%m-%d").strftime("%A")
        best_day = f"🏆 Eng yaxshi kun: {best_day_name} ({day_counts[best_day_date]} ta)"

    active = sum(1 for g in goals if g["status"] == "active")
    created = sum(1 for g in goals if g.get("created") and week_start <= datetime.strptime(g["created"], "%Y-%m-%d %H:%M").date() <= week_end)

    # Day of week names
    day_names = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]

    lines = [
        f"📊 *Haftalik hisobot*\n{week_start} — {week_end}\n",
        f"✅ Bajarilgan: {week_done} ta ({week_hours}h)",
        f"⏳ Faol qolgan: {active} ta",
        f"🆕 Yangi qo'shilgan: {created} ta\n",
    ]

    if day_counts:
        lines.append("*Kunlar:*")
        for i in range(7):
            d = (week_start + timedelta(days=i)).isoformat()
            count = day_counts.get(d, 0)
            bar = "🟩" * count + "⬜" * max(0, 5 - count)
            lines.append(f"  {day_names[i]}: {bar} {count}")

    if best_day:
        lines.append(f"\n{best_day}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─── STREAK & BADGES ──────────────────────────────────────

async def goal_streak(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    data = load_user(user_id)
    meta = data["meta"]

    completed = sorted(meta["completed_dates"])
    today_str = date.today().isoformat()

    # Calculate current streak from data
    streak = 0
    if completed:
        check_date = date.today()
        while check_date.isoformat() in completed:
            streak += 1
            check_date -= timedelta(days=1)

    fire = "🔥" * min(streak, 5) + ("⭐" if streak > 5 else "")

    text = (
        f"*Streak ma'lumotlari*\n\n"
        f"{fire}\n"
        f"Joriy: *{streak}* kun\n"
        f"Eng yaxshi: *{meta['best_streak']}* kun\n"
        f"Jami bajarilgan: *{meta['total_completed']}* ta\n\n"
        f"📅 Faol kunlar: {len(completed)} ta\n\n"
    )

    # Show last 7 days
    day_names = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]
    days_bar = ""
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        if i == 0:
            days_bar += f"[{'✅' if d in completed else '⬜'}]{day_names[(date.today().weekday() - i) % 7]} "
        else:
            days_bar += f"{'✅' if d in completed else '⬜'}{day_names[(date.today().weekday() - i) % 7]} "
    text += f"Oxirgi 7 kun:\n{days_bar}"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def goal_badges(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    data = load_user(user_id)
    meta = data["meta"]

    if not meta["badges"]:
        await update.message.reply_text(
            "Hali yutuq yo'q. Maqsadlarni bajarib, yutuqlarni oching!\n\n"
            "Mavjud yutuqlar:\n"
            "🥉 1 ta — Birinchi qadam\n"
            "🥈 5 ta — Beshlik\n"
            "🥇 10 ta — O'nlik\n"
            "🏆 25 ta — Lider\n"
            "👑 50 ta — Usta\n"
            "💎 100 ta — Legend\n"
            "🔥 3/7/14 kun — Streak yutuqlari"
        )
        return

    text = "🏅 *Yutuqlarim*\n\n"
    for b in meta["badges"]:
        text += f"{b['name']} — {b['desc']}\n"
    text += f"\nJami: {len(meta['badges'])} ta"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── GRAPH ─────────────────────────────────────────────────

async def goal_graph(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        goal_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID son bo'lishi kerak.")
        return

    g = find_goal(user_id, goal_id)
    if not g:
        await update.message.reply_text(f"ID {goal_id} topilmadi.")
        return

    pct = min(g["hours_spent"] / g["hours"] * 100, 100) if g["hours"] > 0 else 0
    bar_len = 20
    filled = int(bar_len * pct / 100)
    bar = "🟩" * filled + "⬜" * (bar_len - filled)

    deadline_info = ""
    if g.get("deadline"):
        dl = datetime.strptime(g["deadline"], "%Y-%m-%d").date()
        remaining = (dl - date.today()).days
        status = "✅ muddat yetarli" if remaining > 7 else "⚠️ muddat yaqin" if remaining > 0 else "❌ muddat o'tgan"
        deadline_info = f"📅 {g['deadline']} ({remaining} kun) {status}"

    priority_colors = {"high": "🔴", "medium": "🟡", "low": "🟢"}

    text = (
        f"📊 *Progress: {g['name']}*\n\n"
        f"{bar} {pct:.0f}%\n"
        f"⏱ {g['hours_spent']}/{g['hours']} soat\n"
        f"🎯 Holat: {g['status']}\n"
        f"{priority_colors.get(g['priority'], '⚪')} Priority: {g['priority']}\n"
        f"📂 Kategoriya: {g.get('category', 'general')}\n"
        f"🍅 Pomodoro: {g['pomodoro_count']}\n"
    )
    if deadline_info:
        text += f"{deadline_info}\n"
    if g.get("repeat"):
        text += f"🔁 Takrorlanadi: {g['repeat']}\n"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── POMODORO ──────────────────────────────────────────────

async def goal_pomodoro(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    try:
        goal_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID son bo'lishi kerak.")
        return
    action = context.args[2].lower()
    if action not in ("start", "stop", "stats"):
        await update.message.reply_text("Action: start, stop yoki stats")
        return

    data = load_user(user_id)
    g = find_goal(user_id, goal_id)
    if not g:
        await update.message.reply_text(f"ID {goal_id} topilmadi.")
        return

    if action == "start":
        if g.get("pomodoro_active"):
            await update.message.reply_text("Pomodoro allaqachon ishga tushgan!")
            return
        g["pomodoro_active"] = True
        g["pomodoro_start"] = datetime.now().isoformat()
        save_user(user_id)

        # Schedule end notification in 25 min
        await context.job_queue.run_once(
            pomodoro_end_callback,
            25 * 60,
            data={"user_id": user_id, "goal_id": goal_id, "goal_name": g["name"]},
        )

        await update.message.reply_text(
            f"🍅 *Pomodoro boshlandi!*\n\n"
            f"📌 {g['name']}\n"
            f"⏱ 25 daqiqa ish, keyin 5 daqiqa tanaffus\n\n"
            f"Diqqat! Faqat bitta vazifaga odatlan!",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action == "stop":
        if not g.get("pomodoro_active"):
            await update.message.reply_text("Faol pomodoro yo'q.")
            return
        g["pomodoro_active"] = False
        if g["pomodoro_start"]:
            try:
                start = datetime.fromisoformat(g["pomodoro_start"])
                elapsed = (datetime.now() - start).total_seconds() / 60
                g["hours_spent"] = g.get("hours_spent", 0) + elapsed / 60
            except ValueError:
                pass
        g["pomodoro_start"] = None
        g["pomodoro_count"] = g.get("pomodoro_count", 0) + 1
        data["meta"]["pomodoro_total"] += 1
        save_user(user_id)

        await update.message.reply_text(
            f"⏹ Pomodoro to'xtatildi.\n"
            f"🍅 {g['name']}: {g['pomodoro_count']} marta",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action == "stats":
        await update.message.reply_text(
            f"🍅 *Pomodoro statistikasi*\n\n"
            f"📌 {g['name']}\n"
            f"Jami: {g.get('pomodoro_count', 0)} ta\n"
            f"⏱ Sarflangan: {g.get('hours_spent', 0):.1f}h / {g['hours']}h\n"
            f"👤 Sizning jami: {data['meta'].get('pomodoro_total', 0)} ta",
            parse_mode=ParseMode.MARKDOWN,
        )


async def pomodoro_end_callback(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    user_id = job_data["user_id"]
    goal_id = job_data["goal_id"]
    goal_name = job_data["goal_name"]

    data = load_user(user_id)
    g = find_goal(user_id, goal_id)
    if g and g.get("pomodoro_active"):
        g["pomodoro_active"] = False
        g["pomodoro_count"] = g.get("pomodoro_count", 0) + 1
        data["meta"]["pomodoro_total"] += 1
        if g["pomodoro_start"]:
            try:
                start = datetime.fromisoformat(g["pomodoro_start"])
                elapsed = (datetime.now() - start).total_seconds() / 60
                g["hours_spent"] = g.get("hours_spent", 0) + elapsed / 60
            except ValueError:
                pass
        g["pomodoro_start"] = None
        save_user(user_id)

    try:
        await context.bot.send_message(
            user_id,
            f"⏰ *Pomodoro tugadi!* 🎉\n\n"
            f"📌 {goal_name}\n"
            f"✅ 25 daqiqa ish tugadi\n"
            f"☕ 5 daqiqa tanaffus qiling!\n\n"
            f"Davom etish: /goal pomodoro {goal_id} start",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass


# ─── PLAN ─────────────────────────────────────────────────

async def goal_plan(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    clean_old_dates(user_id)
    goals = get_goals(user_id)
    today = date.today()

    active = [g for g in goals if g["status"] == "active"]
    if not active:
        await update.message.reply_text("Faol maqsad yo'q. `/goal add` bilan qo'shing.")
        return

    # Sort by priority then deadline
    priority_order = {"high": 0, "medium": 1, "low": 2}

    def sort_key(g):
        p = priority_order.get(g["priority"], 3)
        if g.get("deadline"):
            try:
                dl = datetime.strptime(g["deadline"], "%Y-%m-%d").date()
                remaining = (dl - today).days
                return (p, remaining)
            except ValueError:
                pass
        return (p, 999)

    sorted_goals = sorted(active, key=sort_key)

    lines = ["📋 *Bugungi reja*\n"]

    total_h = 0
    for i, g in enumerate(sorted_goals[:5], 1):
        dl = ""
        if g.get("deadline"):
            try:
                dl_date = datetime.strptime(g["deadline"], "%Y-%m-%d").date()
                remaining = (dl_date - today).days
                dl = f" [{'⚠️' if remaining < 3 else '📅'} {remaining} kun]"
            except ValueError:
                pass
        lines.append(f"{i}. `[{g['id']}]` {g['name']} — {g['hours']}h {dl}")
        total_h += g["hours"]

    lines.append(f"\n⏱ Jami: ~{total_h} soat")
    lines.append(f"🎯 Diqqat: yuqori priority va yaqin deadline'ga e'tibor bering!")

    if len(sorted_goals) > 5:
        lines.append(f"\n➕ Yana {len(sorted_goals) - 5} ta maqsad bor")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─── ADVICE ───────────────────────────────────────────────

async def goal_advice(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    clean_old_dates(user_id)
    data = load_user(user_id)
    goals = data["goals"]
    meta = data["meta"]

    if not goals:
        await update.message.reply_text("Avval maqsad qo'shing: /goal add <nomi> <soat>")
        return

    msg = await update.message.reply_text("🤔 *AI tahlil qilmoqda...*", parse_mode=ParseMode.MARKDOWN)

    if AI_ENABLED:
        goals_text = json.dumps(goals, indent=2, ensure_ascii=False)
        meta_text = json.dumps(meta, indent=2, ensure_ascii=False)

        system_prompt = (
            "Sen maqsadlarni boshqarish bo'yicha ekspert AI yordamchisan. "
            "Foydalanuvchining maqsadlarini tahlil qilib, quyidagilarni qil:"
            "\n1. Maqsadlarning mohiyatini tushun"
            "\n2. Ustuvorlik va muddatlarni tahlil qil"
            "\n3. Qaysi maqsadga ko'proq e'tibor berish kerakligini ayt"
            "\n4. Motivatsiya beruvchi va amaliy maslahatlar yoz"
            "\n5. Vaqtni samarali boshqarish bo'yicha tavsiyalar ber"
            "\n6. Agar xato yoki muammo ko'rsang, ogohlantir"
            "\n\nO'zbek tilida yoz. Qisqa va aniq bo'l. 5-8 ta maslahat."
        )
        user_prompt = (
            f"Mening maqsadlarim:\n{goals_text}\n\n"
            f"Statistika:\n{meta_text}\n\n"
            "Mening maqsadlarimni tahlil qil va foydali maslahatlar ber."
        )

        try:
            response = AI_CLIENT.chat.completions.create(
                model=AI_CONFIG.get("model", "gpt-4o-free"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=1000,
            )
            ai_text = response.choices[0].message.content.strip()
            text = f"🤖 *AI Maslahatlar*\n\n{ai_text}"
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await msg.edit_text(f"❌ AI xatosi: {e}\n\nOddiy maslahatlar ko'rsatilmoqda...")
            await send_basic_advice(update, context, user_id)
    else:
        await msg.edit_text("AI sozlanmagan. Oddiy maslahatlar:")
        await send_basic_advice(update, context, user_id)


async def send_basic_advice(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    data = load_user(user_id)
    goals = data["goals"]
    meta = data["meta"]

    active = [g for g in goals if g["status"] == "active"]
    done = [g for g in goals if g["status"] == "done"]
    missed = [g for g in goals if g["status"] == "missed"]

    missed_with_deadline = [g for g in active if g.get("deadline") and datetime.strptime(g["deadline"], "%Y-%m-%d").date() < date.today()]
    high_priority = [g for g in active if g["priority"] == "high"]

    lines = ["💡 *Maslahatlar*\n"]

    if meta["streak"] >= 7:
        lines.append(f"🔥 Ajoyib! {meta['streak']} kunlik seriya! 🎉")
    elif meta["streak"] >= 3:
        lines.append(f"🔥 Yaxshi! {meta['streak']} kun. Yana 4 kun va haftalik seriyaga erishasiz!")
    elif meta["streak"] == 0 and meta["total_completed"] > 0:
        lines.append("Bugun bitta maqsadni bajarib, streak'ni yangilang!")

    if missed_with_deadline:
        lines.append(f"⚠️ {len(missed_with_deadline)} ta maqsad kechikkan. Ularni qayta ko'rib chiqing.")

    if len(active) > 7:
        lines.append("📌 Juda ko'p faol maqsad (7+). 3-5 ta bilan boshlang.")

    if high_priority:
        lines.append(f"🔴 {len(high_priority)} ta yuqori priority bor — avval ularni bajaring!")

    pct = (len(done) / len(goals) * 100) if goals else 0
    if pct < 20 and len(goals) > 5:
        lines.append("📈 20% dan kam bajarilgan. Kichik maqsadlardan boshlang!")
    elif pct > 80:
        lines.append(f"🎉 {pct:.0f}% bajarilgan! Yakunlashga oz qoldi!")

    if not missed and not missed_with_deadline:
        lines.append("👍 Barcha maqsadlar o'z vaqtida. Zo'r intizom!")

    lines.append(f"\n💬 {random.choice(QUOTES)}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def goal_ask(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    if not AI_ENABLED:
        await update.message.reply_text(
            "AI sozlanmagan. config.json da `ai` sozlamalarini to'ldiring:\n"
            "```json\n\"ai\": {\n"
            '  "api_key": "sk-...",\n'
            '  "base_url": "https://...",\n'
            '  "model": "gpt-4o-free"\n'
            "}\n```",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    question = " ".join(context.args[1:])
    data = load_user(user_id)
    goals = data["goals"]

    if not goals:
        await update.message.reply_text("Avval maqsad qo'shing: /goal add <nomi> <soat>")
        return

    msg = await update.message.reply_text("🤔 *AI o'ylamoqda...*", parse_mode=ParseMode.MARKDOWN)

    goals_text = json.dumps(
        [{"id": g["id"], "name": g["name"], "hours": g["hours"],
          "status": g["status"], "priority": g["priority"],
          "deadline": g.get("deadline"), "category": g.get("category")}
         for g in goals],
        indent=2, ensure_ascii=False,
    )

    system_prompt = (
        "Sen maqsadlarni boshqarish bo'yicha ekspert AI yordamchisan. "
        "Foydalanuvchining maqsadlari ro'yxati beriladi. "
        "Foydalanuvchi savoliga maqsadlari kontekstida javob ber. "
        "Maqsadlarning mohiyatini tushunib, eng foydali javobni yoz. "
        "O'zbek tilida, qisqa va tushunarli qilib javob ber."
    )

    try:
        response = AI_CLIENT.chat.completions.create(
            model=AI_CONFIG.get("model", "gpt-4o-free"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Mening maqsadlarim:\n{goals_text}\n\nSavol: {question}"},
            ],
            temperature=0.7,
            max_tokens=1000,
        )
        answer = response.choices[0].message.content.strip()
        await msg.edit_text(
            f"🤖 *AI javob*\n\n{answer}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await msg.edit_text(f"❌ AI xatosi: {e}")


# ─── EXPORT ────────────────────────────────────────────────

async def goal_export(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    fmt = context.args[1].lower()
    if fmt not in ("csv", "json"):
        await update.message.reply_text("Format: csv yoki json")
        return

    data = load_user(user_id)
    goals = data["goals"]

    if not goals:
        await update.message.reply_text("Eksport qilish uchun maqsad yo'q.")
        return

    if fmt == "json":
        content = json.dumps(goals, indent=2, ensure_ascii=False)
        buf = io.BytesIO(content.encode())
        buf.name = f"goals_{user_id}.json"
        await update.message.reply_document(buf, caption="📤 JSON eksport")
    else:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Name", "Hours", "HoursSpent", "Status", "Priority", "Deadline", "Category", "Repeat", "Reminder", "Pomodoro", "Created", "CompletedAt"])
        for g in goals:
            writer.writerow([
                g["id"], g["name"], g["hours"], g.get("hours_spent", 0),
                g["status"], g["priority"], g.get("deadline", ""),
                g.get("category", "general"), g.get("repeat", ""),
                g.get("reminder", ""), g.get("pomodoro_count", 0),
                g["created"], g.get("completed_at", ""),
            ])
        buf = io.BytesIO(output.getvalue().encode("utf-8-sig"))
        buf.name = f"goals_{user_id}.csv"
        await update.message.reply_document(buf, caption="📤 CSV eksport")


# ═══════════════════════════════════════════════════════════
# MODE 2 — AI AKADEMIK (OCR + DUAL AI ANALYSIS)
# ═══════════════════════════════════════════════════════════


async def mode2_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages in AI Akademik mode."""
    user_id = update.effective_user.id
    n_models = len(AKADEMIK_MODELS)

    if not update.message.photo:
        models_list = "\n".join(f"• `{m}`" for m in AKADEMIK_MODELS)
        await update.message.reply_text(
            f"📸 Menga qo'lda yozilgan misol rasmini yuboring.\n\n"
            f"Matematika, geometriya, algebra bo'yicha "
            f"{n_models} xil AI tekshirib beradi:\n{models_list}",
        )
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    img_bytes = io.BytesIO()
    await file.download_to_memory(img_bytes)
    img_bytes.seek(0)

    msg = await update.message.reply_text(
        "🔍 *Rasm tahlil qilinmoqda...*", parse_mode=ParseMode.MARKDOWN
    )

    extracted = await extract_text_from_image(img_bytes)

    if not extracted or extracted.strip() == "":
        await msg.edit_text(
            "❌ Matn topilmadi. Aniqroq rasm yuboring.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await msg.edit_text(
        f"📝 *Matn chiqarildi:*\n```\n{extracted[:1000]}\n```\n\n"
        f"🤖 {n_models} ta AI tekshirmoqda...",
        parse_mode=ParseMode.MARKDOWN,
    )

    results = await analyze_with_both_ai(extracted)

    response_parts = [f"📝 *Matn:* `{extracted[:200]}`\n"]

    for model_name, result_text in results:
        if result_text.startswith("Xatolik"):
            response_parts.append(f"\n❌ *{model_name}*\n{result_text}")
        else:
            response_parts.append(f"\n✅ *{model_name}*\n{result_text}")

    final_text = "\n".join(response_parts)

    if len(final_text) > 4000:
        final_text = final_text[:3997] + "..."

    await msg.edit_text(final_text, parse_mode=ParseMode.MARKDOWN)


async def extract_text_from_image(img_bytes: io.BytesIO) -> str:
    """Extract text using EasyOCR first, fallback to AI Vision."""
    try:
        import easyocr
        reader = easyocr.Reader(["en", "ru"], gpu=False)
        img_bytes.seek(0)
        results = reader.readtext(img_bytes.read(), detail=0)
        if results:
            return "\n".join(results)
    except ImportError:
        pass
    except Exception:
        pass

    if AKADEMIK_ENABLED:
        import base64
        img_bytes.seek(0)
        b64 = base64.b64encode(img_bytes.getvalue()).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{b64}"
        for model in AKADEMIK_MODELS:
            try:
                response = AKADEMIK_CLIENT.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Rasmdagi barcha matnni, raqamlarni va "
                                        "matematik ifodalarni aynan qanday bo'lsa, "
                                        "shunday chiqar. Hech narsa qo'shma. "
                                        "Faqat matnni yoz."
                                    ),
                                },
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                    ],
                    max_completion_tokens=2000,
                )
                return response.choices[0].message.content.strip()
            except Exception:
                continue

    return ""


async def analyze_with_both_ai(text: str) -> list:
    """Send text to both AI models and return results."""
    system_prompt = (
        "Sen matematika, geometriya va algebra bo'yicha ekspert o'qituvchisan. "
        "Berilgan masalani tekshir, yechimini ko'rsat va tushuntir. "
        "Agar masala to'liq bo'lmasa, to'ldirib yech. "
        "Javobni Markdown va LaTeX formatida yoz. "
        "O'zbek tilida tushunarli qilib yoz. "
        "Qadamma-qadam yechimni ko'rsat. "
        "Formulalar uchun $$...$$ yoki \\(...\\) ishlat."
    )

    results = []
    for model in AKADEMIK_MODELS:
        display = f"{'👁️' if model == AKADEMIK_MODELS[0] else '🧠'} {model}"
        try:
            response = AKADEMIK_CLIENT.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Mana qo'lda yozilgan masala:\n```\n{text}\n```\n\n"
                            "Masalani tekshir, to'g'ri yechimini ko'rsat va tushuntir."
                        ),
                    },
                ],
                temperature=0.3,
                max_completion_tokens=2000,
            )
            answer = response.choices[0].message.content.strip()
            results.append((f"✅ {display}", answer))
        except Exception as e:
            emsg = str(e)[:150]
            results.append((f"❌ {display}", f"Xatolik: {emsg}"))

    return results


# ═══════════════════════════════════════════════════════════
# MODE SWITCHING
# ═══════════════════════════════════════════════════════════

async def switch_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return
    USER_SESSION[user_id] = mode
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(
        f"✅ `{mode}` rejimiga o'tildi!\n/modeinfo — yordam",
        parse_mode=ParseMode.MARKDOWN,
    )


async def mode1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_mode(update, context, "mode1")


async def mode2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_mode(update, context, "mode2")


async def mode_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return
    await show_mode_info(update, context, user_id)


async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return
    mode = USER_SESSION.get(user_id, "mode1")
    if mode == "mode2":
        await mode2_handler(update, context)


# ═══════════════════════════════════════════════════════════
# START / HELP
# ═══════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("Ruxsat yo'q.")
        return
    if user_id not in USER_SESSION:
        USER_SESSION[user_id] = "mode1"

    text = (
        "🤖 *Ko'p rejimli Bot*\n\n"
        "Rejimlar:\n"
        "• `/mode1` 📋 Maqsadlar boshqaruvi\n"
        "• `/mode2` 📸 AI Akademik (OCR + AI tahlil)\n\n"
        "Almashtirish: /mode1, /mode2 yoki tugmalar\n"
        "/modeinfo — joriy rejim yordami"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_mode_keyboard(USER_SESSION[user_id]),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("mode_"):
        mode = data.replace("mode_", "")
        USER_SESSION[user_id] = mode
        await query.edit_message_text(
            f"✅ `{mode}` rejimiga o'tildi!\n/modeinfo — yordam",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data.startswith("done_"):
        gid = int(data.split("_")[1])
        g = find_goal(user_id, gid)
        if g:
            g["status"] = "done"
            g["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            meta = load_user(user_id)["meta"]
            meta["total_completed"] += 1
            save_user(user_id)
            await query.edit_message_text(
                f"✅ *{g['name']}* bajarildi!", parse_mode=ParseMode.MARKDOWN
            )
        return

    if data.startswith("prio_"):
        _, gid, priority = data.split("_")
        g = find_goal(user_id, int(gid))
        if g:
            g["priority"] = priority
            save_user(user_id)
            icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
            await query.edit_message_text(
                f"🎯 *{g['name']}* → {icons[priority]} {priority}",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    if data.startswith("askdead_"):
        gid = data.split("_")[1]
        await query.edit_message_text(
            f"Muddatni kiriting: `/goal deadline {gid} YYYY-MM-DD`\n"
            f"Masalan: `/goal deadline {gid} 2026-06-01`",
            parse_mode=ParseMode.MARKDOWN,
        )


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    import logging
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.WARNING,
    )

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(CommandHandler("mode1", mode1))
    application.add_handler(CommandHandler("mode2", mode2))
    application.add_handler(CommandHandler(["akademik", "ocr"], mode2))
    application.add_handler(CommandHandler("modeinfo", mode_info))

    application.add_handler(CommandHandler("goal", goal_command))

    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))

    async def photo_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            return
        mode = USER_SESSION.get(user_id, "mode1")
        if mode == "mode2":
            await mode2_handler(update, context)

    application.add_handler(MessageHandler(filters.PHOTO, photo_router))

    if hasattr(application, "job_queue") and application.job_queue:
        application.job_queue.run_repeating(check_reminders, interval=30, first=10)

    print("🤖 Bot ishga tushdi! Barcha funksiyalar yuklandi.", flush=True)

    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    except Exception as e:
        print(f"❌ Bot xatosi: {e}", flush=True)
        raise


if __name__ == "__main__":
    main()
