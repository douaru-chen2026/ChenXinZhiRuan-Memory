@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================================
echo   辰心知阮 · 阿阮本地入箱（照片先在本机上锁，再上云）
echo ============================================================
python local_inbox.py
if errorlevel 1 (
  echo.
  echo 如果提示找不到 python，先装 Python 并勾选 Add Python to PATH：
  echo https://www.python.org/downloads/
)
pause
