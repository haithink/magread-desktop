@echo off
rem 刊读 Windows 桌面版打包：需先创建 .venv 并安装 requirements.txt
cd /d %~dp0

rem 生成 PE 版本信息（版本号取自 kandu/__init__.py）
.venv\Scripts\python.exe gen_ver.py || goto :fail

.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name Kandu ^
  --version-file "ver.txt" ^
  --add-data "kandu/assets;assets" ^
  --add-binary "D:/anaconda/Library/bin/tcl86t.dll;." ^
  --add-binary "D:/anaconda/Library/bin/tk86t.dll;." ^
  --add-binary "D:/anaconda/Library/bin/zlib.dll;." ^
  --add-binary "D:/anaconda/Library/bin/zlib-ng2.dll;." ^
  --add-binary "D:/anaconda/Library/bin/sqlite3.dll;." ^
  --add-data "D:/anaconda/Library/lib/tcl8.6;tcl8.6" ^
  --add-data "D:/anaconda/Library/lib/tk8.6;tk8.6" ^
  --runtime-hook "hooks/rthook_tcl.py" ^
  main.py || goto :fail

rem 构建后自检：exe 必须刚更新过（防运行中文件锁定导致静默失败）
.venv\Scripts\python.exe check_build.py || goto :fail

echo 产物：dist\Kandu\Kandu.exe
goto :eof
:fail
echo 构建失败！请确认 Kandu.exe 没有正在运行（任务管理器结束进程后重试）。
exit /b 1
