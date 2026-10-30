@echo off
chcp 65001 >nul
title 刊读 · 后台批量生成摘要
cd /d %~dp0..

echo ============================================
echo  刊读 · 后台批量生成全部书籍摘要
echo  使用默认 LLM（★），自动跳过已完整的书
echo  关闭本窗口 = 停止生成（已完成部分不丢失）
echo ============================================
echo.

.venv\Scripts\python.exe tools\restore_digests.py
echo.
echo 全部结束。按任意键关闭窗口。
pause >nul
