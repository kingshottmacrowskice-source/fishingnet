@echo off
setlocal
chcp 65001 >nul
title FishingNet 清空内容数据
cd /d "%~dp0"
".venv\Scripts\python.exe" "%~dp0scripts\clear_content_data.py"
echo.
echo 完成。保留：登录信息、平台账号、模型配置；清空：信息、实体、日报、自动任务。
pause
