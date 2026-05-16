@echo off
chcp 65001 >nul
title GoalBot - O'rnatish
echo ====================================
echo    GoalBot - O'rnatish
echo ====================================
echo.

:: Admin huquqini tekshirish
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [INFO] Admin huquqi talab qilinadi...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

:: Python tekshirish
echo [1/4] Python tekshirilmoqda...
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [INFO] Python topilmadi. O'rnatilmoqda...
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements >nul 2>&1
    if %errorLevel% neq 0 (
        echo [XATO] Python o'rnatilmadi. https://python.org dan yuklab oling.
        pause
        exit /b
    )
)
echo [OK] Python topildi

:: Kutubxonalarni o'rnatish
echo [2/4] Kutubxonalar o'rnatilmoqda...
pip install -r requirements.txt -q
if %errorLevel% equ 0 (
    echo [OK] Kutubxonalar o'rnatildi
) else (
    echo [XATO] Kutubxonalarni o'rnatishda xatolik
    pause
    exit /b
)

:: Task Scheduler ga qo'shish
echo [3/4] Task Scheduler sozlanmoqda...
schtasks /create /tn "GoalBot" /tr "wscript.exe \"%cd%\start_bot.vbs\"" /sc onstart /delay 0001:00 /ru %USERNAME% /f >nul 2>&1
if %errorLevel% equ 0 (
    echo [OK] Task Scheduler ga qo'shildi
) else (
    echo [WARN] Task Scheduler ga qo'shilmadi. Qo'lda qo'shishingiz mumkin.
)

:: Botni ishga tushirish
echo [4/4] Bot ishga tushirilmoqda...
wscript.exe "%~dp0start_bot.vbs"
echo [OK] Bot ishga tushdi!

echo.
echo ====================================
echo    O'RNATISH TUGADI!
echo ====================================
echo.
echo Telegram da @hakimov_jon_bot ga yozing
echo /start - boshlash
echo.
pause
