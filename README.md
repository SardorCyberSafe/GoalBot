# 🤖 GoalBot — Smart Goal Manager + AI Akademik OCR

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4)](https://core.telegram.org/bots)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

A feature-rich Telegram bot with **two powerful modes** — a complete **Goal Management System** and an **AI-powered OCR + Math Analysis Tool**.

---

## ✨ Features

### 📋 Mode 1: Goal Manager
- **Add goals** with estimated time (`/goal add Python 40`)
- **Priorities** — High 🔴 / Medium 🟡 / Low 🟢
- **Deadlines** — set and track due dates
- **Categories** — organize goals by topic
- **Recurring goals** — weekly/monthly repeat
- **Reminders** — daily, weekly, or custom time
- **Streak tracking** — consecutive days 🔥
- **Badges & achievements** — milestones 🏅
- **Pomodoro timer** — 25-min focus sessions 🍅
- **Statistics** — progress bar, category breakdown
- **Weekly reports** — day-by-day activity
- **AI Assistant** — ask questions about your goals 🤖
- **Export** — CSV/JSON backup

### 📸 Mode 2: AI Akademik
Upload a photo of **handwritten math, geometry, or algebra problems** and get:
- **OCR** — extracts text using EasyOCR + AI Vision
- **Dual AI analysis** — two models check your work:
  - `qwen3.6-plus-preview-free`
  - `gpt-5.5-free`
- **Detailed solutions** — step-by-step in LaTeX/Markdown
- **Error checking** — each model result shown separately ✅/❌

---

## 🚀 Quick Start

### 1. Clone
```bash
git clone https://github.com/SardorCyberSafe/GoalBot.git
cd GoalBot
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure
```bash
cp config.example.json config.json
# Edit config.json with your Telegram token and API keys
python setup.py   # Encrypt sensitive data (optional)
```

### 4. Run
```bash
python bot.py
```

### 5. Open Telegram
Search for your bot and send `/start`

---

## 🪟 Windows Installation (One-Click)

1. Download the repository
2. Run `install.bat` **as Administrator**
3. It will:
   - Install Python (if missing)
   - Install dependencies
   - Add bot to Task Scheduler (auto-start on boot)
   - Launch the bot

---

## 🤖 Bot Commands

### Mode 1 — Goals
| Command | Description |
|---------|-------------|
| `/goal add <name> <hours>` | Add a new goal |
| `/goal list` | List all goals |
| `/goal done <id>` | Mark as completed |
| `/goal delete <id>` | Delete a goal |
| `/goal priority <id> <level>` | Set priority |
| `/goal deadline <id> YYYY-MM-DD` | Set deadline |
| `/goal category <id> <name>` | Set category |
| `/goal stats` | View statistics |
| `/goal today` | Today's goals |
| `/goal advice` | AI-generated advice |
| `/goal ask <question>` | Ask AI about goals |
| `/goal pomodoro <id> start` | Start pomodoro |

### Mode 2 — AI Akademik
| Command | Description |
|---------|-------------|
| `/akademik` | Switch to OCR mode |
| *(send a photo)* | Upload handwritten problem |

### General
| Command | Description |
|---------|-------------|
| `/mode1` | Switch to Goal Manager |
| `/mode2` | Switch to AI Akademik |
| `/modeinfo` | Current mode help |

---

## 🏗 Architecture

```
GoalBot/
├── bot.py              # Main bot (1550+ lines)
├── config.json         # Encrypted configuration
├── config.example.json # Configuration template
├── setup.py            # Encryption setup
├── requirements.txt    # Python dependencies
├── .gitignore          # Git ignore rules
├── install.bat         # Windows installer
├── start_bot.vbs       # Silent startup script
├── uninstall.bat       # Remove from autostart
└── data/               # User data (auto-created)
    └── {user_id}.json  # Per-user goal storage
```

---

## 🔒 Security

- API keys are **encrypted** using `cryptography` (AES/Fernet)
- Encryption key stored in `bot.key` (never committed)
- All user data stored locally in `data/` folder
- Bot supports **user whitelist** (`allowed_users` in config)

---

## 🛠 Tech Stack

- **Python** 3.10+
- **python-telegram-bot** v20.8 — Telegram Bot API
- **OpenAI** — AI chat completions
- **EasyOCR** — Optical Character Recognition
- **cryptography** — Fernet encryption

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

## 👤 Author

**SardorCyberSafe**
- GitHub: [@SardorCyberSafe](https://github.com/SardorCyberSafe)
- Telegram: [@hakimov_jon_bot](https://t.me/hakimov_jon_bot)

---

*Built with ❤️ for smart productivity and learning.*
