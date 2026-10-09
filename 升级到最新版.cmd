@echo off
chcp 65001 >nul
echo 正在从 GitHub 获取最新程序文件，不会修改本机 API key 和配置...
"%~dp0python\python.exe" "%~dp0upgrade.py"
if errorlevel 1 pause
