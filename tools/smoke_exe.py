"""Launch the packaged GUI in a temporary data directory and close its own window."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kandu import __version__


def smoke(exe):
    user32=ctypes.WinDLL('user32',use_last_error=True)
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user32.EnumWindows.argtypes=[callback,wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user32.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    with tempfile.TemporaryDirectory(prefix='kandu-exe-smoke-') as folder:
        env=dict(os.environ,KANDU_DATA_DIR=folder)
        process=subprocess.Popen([str(Path(exe).resolve())],env=env)
        windows=[]
        @callback
        def collect(hwnd,lparam):
            pid=wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if pid.value==process.pid:
                title=ctypes.create_unicode_buffer(1024)
                user32.GetWindowTextW(hwnd,title,len(title))
                if f'Kandu Desktop v{__version__}' in title.value:
                    windows.append((hwnd,title.value))
            return True
        try:
            end=time.monotonic()+20
            while not windows and process.poll() is None and time.monotonic()<end:
                user32.EnumWindows(collect,0)
                time.sleep(.1)
            if not windows:
                raise RuntimeError(f'Packaged main window not found; exit={process.poll()}')
            time.sleep(.4)
            user32.PostMessageW(windows[0][0],0x0010,0,0)
            if process.wait(timeout=10)!=0:
                raise RuntimeError('Packaged application did not exit cleanly')
            if (Path(folder)/'error.log').exists():
                raise RuntimeError('Packaged application wrote an error log')
            if not (Path(folder)/'vocab/cet4_words.txt').is_file():
                raise RuntimeError('Bundled wordlists were not extracted')
            print('PASS packaged launch / assets / native window / clean close:',windows[0][1])
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


if __name__=='__main__':
    smoke(sys.argv[1] if len(sys.argv)>1 else 'dist/Kandu/Kandu.exe')
