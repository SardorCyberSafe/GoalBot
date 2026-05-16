@echo off
chcp 65001 >nul
title GoalBot - O'chirish
echo GoalBot o'chirilmoqda...

:: Botni to'xtatish
echo [1/2] Bot to'xtatilmoqda...
taskkill /f /im python.exe /fi "WINDOWTITLE eq bot.py" >nul 2>&1

:: Task Scheduler dan o'chirish
echo [2/2] Task Scheduler dan o'chirilmoqda...
schtasks /delete /tn "GoalBot" /f >nul 2>&1

echo [OK] GoalBot o'chirildi.
pause
