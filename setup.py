"""Bir martalik sozlash: kalit yaratish va config ni shifrlash."""
import json
from pathlib import Path

try:
    from cryptography.fernet import Fernet
except ImportError:
    print("cryptography kutubxonasi o'rnatilmoqda...")
    import subprocess
    subprocess.run(["pip", "install", "cryptography", "-q"])
    from cryptography.fernet import Fernet

CONFIG_PATH = Path("config.json")
KEY_PATH = Path("bot.key")

if KEY_PATH.exists():
    ans = input("bot.key fayli mavjud. Qayta yaratish? (y/n): ")
    if ans.lower() != "y":
        print("Bekor qilindi.")
        exit()

key = Fernet.generate_key()
cipher = Fernet(key)

with open(CONFIG_PATH) as f:
    cfg = json.load(f)

sections = {
    "ai_encrypted": cfg.get("ai", {}),
    "ai_akademik_encrypted": cfg.get("ai_akademik", {}),
}

for new_key, data in sections.items():
    if data:
        encrypted = cipher.encrypt(json.dumps(data).encode())
        cfg[new_key] = encrypted.decode()
    else:
        cfg[new_key] = ""

cfg.pop("ai", None)
cfg.pop("ai_akademik", None)

with open(CONFIG_PATH, "w") as f:
    json.dump(cfg, f, indent=2)

KEY_PATH.write_bytes(key)

print(f"✅ Kalit yaratildi: {KEY_PATH}")
print(f"✅ Config shifrlandi: {CONFIG_PATH}")
print("⚠️  bot.key faylini o'chirmang! U bo'lmasa config ochilmaydi.")
