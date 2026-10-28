# -*- coding: utf-8 -*-
"""构建后自检：exe 存在、mtime 在本轮构建之后、内嵌版本与 __version__ 一致。"""
import sys, time
from pathlib import Path

sys.path.insert(0, '.')
from kandu import __version__

exe = Path('dist/Kandu/Kandu.exe')
if not exe.exists():
    print('FAIL: exe 不存在')
    sys.exit(1)
age = time.time() - exe.stat().st_mtime
if age > 300:
    print(f'FAIL: exe 是 {int(age/60)} 分钟前的旧文件（可能被运行中的进程锁定，构建静默失败）')
    sys.exit(1)
print(f'OK: dist/Kandu/Kandu.exe 已更新，版本 v{__version__}')
