@echo off
rem 刊读 Windows 桌面版打包：需先创建 .venv 并安装 requirements.txt
cd /d %~dp0
.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name Kandu ^
  --add-data "kandu/assets;assets" ^
  --add-binary "D:/anaconda/Library/bin/tcl86t.dll;." ^
  --add-binary "D:/anaconda/Library/bin/tk86t.dll;." ^
  --add-binary "D:/anaconda/Library/bin/zlib.dll;." ^
  --add-binary "D:/anaconda/Library/bin/zlib-ng2.dll;." ^
  --add-data "D:/anaconda/Library/lib/tcl8.6;tcl8.6" ^
  --add-data "D:/anaconda/Library/lib/tk8.6;tk8.6" ^
  --runtime-hook "hooks/rthook_tcl.py" ^
  main.py
echo 产物：dist\Kandu\Kandu.exe
