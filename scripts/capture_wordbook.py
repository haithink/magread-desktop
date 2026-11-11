"""用真实文章和离线词典查词生成展示图；所有写入均在临时数据目录中。"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

from PIL import Image

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))


def capture(widget, output):
    """只捕获本窗口，兼容被遮挡或远程桌面未显示的情况。"""
    user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetDC.argtypes = [wintypes.HWND]
    user.GetDC.restype = ctypes.c_void_p
    gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi.SelectObject.restype = ctypes.c_void_p
    user.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]
    gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    hwnd = user.GetAncestor(widget.winfo_id(), 2)
    width, height = widget.winfo_width(), widget.winfo_height()
    dc = user.GetDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    previous = gdi.SelectObject(memory, bitmap)

    class Header(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG), ('height', wintypes.LONG),
                    ('planes', wintypes.WORD), ('bits', wintypes.WORD), ('compression', wintypes.DWORD),
                    ('image_size', wintypes.DWORD), ('x', wintypes.LONG), ('y', wintypes.LONG),
                    ('used', wintypes.DWORD), ('important', wintypes.DWORD)]
    header = Header(ctypes.sizeof(Header), width, -height, 1, 32)
    try:
        assert user.PrintWindow(hwnd, memory, 3)
        bits = ctypes.create_string_buffer(width * height * 4)
        assert gdi.GetDIBits(memory, bitmap, 0, height, bits, ctypes.byref(header), 0)
        img = Image.frombuffer('RGB', (width, height), bits, 'raw', 'BGRX', 0, 1)
        assert img.getextrema()[0][0] != img.getextrema()[0][1], 'Blank screenshot'
        img.save(output, optimize=True)
    finally:
        gdi.SelectObject(memory, previous)
        gdi.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi.DeleteDC.argtypes = [ctypes.c_void_p]
        user.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, dc)


personal = Path(os.environ['LOCALAPPDATA']) / 'KanduDesktop'
metas = json.loads((personal / 'books.json').read_text(encoding='utf-8'))
meta = next(m for m in metas if m['id'] == 'TheEconomist.2026.10.10')
content = json.loads((personal / 'cache' / meta['id'] / 'content.json').read_text(encoding='utf-8'))
article = next(a for a in content['articles'] if a['index'] == 63)
assert (personal / 'ecdict.db').is_file()

with tempfile.TemporaryDirectory(prefix='kandu-wordbook-capture-') as sandbox:
    os.environ['KANDU_DATA_DIR'] = sandbox
    from kandu import core_dict, core_wordbook as words
    from kandu.ui_wordbook import show_wordbook
    from kandu.ui_icon import set_app_icon
    root = tk.Tk()
    root.withdraw()
    root.current = None
    root.settings = {}
    set_app_icon(root)
    try:
        with patch.object(core_dict, '_db_path', return_value=personal / 'ecdict.db'), patch.dict(core_dict.STATE, ready=True):
            # 来源与释义均来自真实文章和离线词典，无虚构的模型回答。
            candidates = ['neutrinos', 'diminutive', 'laureates', 'electromagnetic', 'undivided', 'spades', 'citation']
            for word in candidates:
                hit = core_dict.lookup(word)
                matches = words.article_matches(article, [dict(word=word, aliases=[])])
                if hit and not hit.get('miss') and matches:
                    index = next(iter(matches))
                    text = article['title'] if index == -1 else article['paragraphs'][index]
                    words.remember_lookup(word, hit, dict(bookId=meta['id'], bookTitle=meta['fileName'],
                        articleIndex=article['index'], articleTitle=article['title'], paragraph=index, context=text[:500]))
            records = words.list_words()
            assert records, '真实文章中没有匹配到可查词条'
            manager = show_wordbook(root, meta, article)
            manager.geometry('1080x760+40+40')
            preferred = next((r for r in records if r['word'] == 'diminutive'), records[0])
            manager.select_word(preferred['word'])
            manager.status.configure(text='右键查词自动收录 · 跨刊物保留 · 修改后点击保存 · 删除后同步更新阅读页')
            for _ in range(15):
                root.update()
                time.sleep(.04)
            output = repo / 'docs/screenshots/desktop-wordbook.png'
            capture(manager, output)
            print('Captured wordbook from actual article and dictionary:', output)
    finally:
        root.destroy()
