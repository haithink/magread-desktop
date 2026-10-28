# -*- coding: utf-8 -*-
"""从 kandu.__version__ 生成 PyInstaller 的 PE 版本信息文件（exe 属性可见）。"""
import sys
sys.path.insert(0, '.')
from kandu import __version__

tmpl = '''# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(%s),
    prodvers=(%s),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'yichen'),
        StringStruct('FileDescription', 'Kandu - LLM assisted English journal reader'),
        StringStruct('FileVersion', '%s'),
        StringStruct('ProductName', 'Kandu Desktop'),
        StringStruct('ProductVersion', '%s'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
'''
parts = [int(x) for x in (__version__.split('.') + ['0', '0', '0'])[:3]] + [0]
vernums = tuple(parts[:4])
verstr = '.'.join(str(x) for x in vernums)
vernums_src = ', '.join(str(x) for x in vernums)
open('ver.txt', 'w', encoding='utf-8').write(tmpl % (vernums_src, vernums_src, __version__, __version__))
print('ver.txt written for', __version__)
