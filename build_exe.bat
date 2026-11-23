@echo off
setlocal
cd /d "%~dp0"
rem Build with official CPython 3.12 x64 and its bundled Tcl/Tk.
rem py -3.12 -m venv .venv
rem .venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe gen_ver.py || goto :fail
.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name Kandu ^
  --version-file "ver.txt" ^
  --icon "kandu/assets/kandu.ico" ^
  --add-data "kandu/assets;assets" ^
  main.py || goto :fail
.venv\Scripts\python.exe check_build.py || goto :fail
.venv\Scripts\python.exe tools\smoke_exe.py || goto :fail
.venv\Scripts\python.exe tools\package_release.py || goto :fail
echo Built: dist\Kandu\Kandu.exe
exit /b 0
:fail
echo Build failed. Check the log and close any running copy of dist\Kandu\Kandu.exe.
exit /b 1
