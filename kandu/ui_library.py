"""书架：目录扫描 + 封面网格。"""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

from kandu import core_config as cfgmod
from kandu import core_parsers as store

THUMB_W, THUMB_H = 128, 172
COLS = 5


class LibraryFrame(tk.Frame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, bg="#f7faf7")
        self.app = app
        self.metas = []
        self._photo_cache = {}
        self._scanning = False

        bar = tk.Frame(self, bg="#1b4b43")
        bar.pack(fill="x")
        tk.Label(bar, text="刊读 · 书架", bg="#1b4b43", fg="white",
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left", padx=14, pady=8)
        self.dir_label = tk.Label(bar, text=self._dir_text(), bg="#1b4b43", fg="#cde8e1")
        self.dir_label.pack(side="left", padx=8)
        ttk.Button(bar, text="选择目录", command=self.pick_dir).pack(side="right", padx=(6, 12), pady=6)
        ttk.Button(bar, text="重新扫描", command=self.rescan).pack(side="right", padx=6, pady=6)
        ttk.Button(bar, text="设置", command=self.app.open_settings).pack(side="right", padx=6, pady=6)

        self.status = tk.Label(self, text="", bg="#f7faf7", fg="#446", anchor="w")
        self.status.pack(fill="x", padx=14, pady=(6, 0))

        self.canvas = tk.Canvas(self, bg="#f7faf7", highlightthickness=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg="#f7faf7")
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw", width=self._inner_width())
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.vbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure("all", width=self._inner_width()))
        # 滚轮：bind_all 是应用级绑定，离开本页时必须解绑，否则操作已销毁控件报 TclError
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.bind("<Destroy>", self._on_destroy)

    def _on_wheel(self, e):
        try:
            if self.canvas.winfo_exists():
                self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        except tk.TclError:
            pass

    def _on_destroy(self, e):
        if e.widget is self:
            try:
                self.unbind_all("<MouseWheel>")
            except tk.TclError:
                pass

        if not self.app.settings.get("libraryDir"):
            self.status.config(text="先点右上角「选择目录」挑选存放 EPUB / PDF 刊物的文件夹")
        self.rescan()

    def _inner_width(self):
        return max(self.canvas.winfo_width(), 900)

    def _dir_text(self):
        d = self.app.settings.get("libraryDir") or "（未选择目录）"
        return f"目录：{d}"

    def pick_dir(self):
        d = filedialog.askdirectory(title="选择刊物文件所在目录",
                                    initialdir=self.app.settings.get("libraryDir") or os.path.expanduser("~"))
        if not d:
            return
        self.app.settings["libraryDir"] = os.path.normpath(d)
        self.app.save_settings()
        self.dir_label.config(text=self._dir_text())
        self.rescan()

    def rescan(self):
        if self._scanning:
            return
        d = self.app.settings.get("libraryDir") or ""
        if not d:
            return
        self._scanning = True
        self.status.config(text="扫描中…")
        self.app.post(self._scan_worker, d)

    def _scan_worker(self, d):
        metas = store.scan(d)
        for m in metas:
            store.ensure_cover(m)
        self.app.emit("scan_done", metas)

    def on_scan_done(self, metas):
        self._scanning = False
        self.metas = metas
        self.status.config(text=f"共 {len(metas)} 本刊物")
        self._render()

    def _render(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self._photo_cache.clear()
        for i, meta in enumerate(self.metas):
            card = tk.Frame(self.inner, bg="white", bd=1, relief="solid")
            card.grid(row=i // COLS, column=i % COLS, padx=10, pady=10, sticky="n")
            img_label = tk.Label(card, bg="#dde8e4", width=THUMB_W, height=THUMB_H)
            img_label.pack(padx=6, pady=(6, 2))
            self._load_thumb(img_label, meta)
            has_d = store.has_digest(meta["id"])
            tk.Label(card, text=meta["fileName"][:40], bg="white", fg="#222",
                     font=("Microsoft YaHei UI", 9), wraplength=THUMB_W + 20).pack(padx=4, pady=(0, 2))
            tk.Label(card, text="已生成摘要 ✓" if has_d else "未生成摘要", bg="white",
                     fg="#7fa829" if has_d else "#b0b8b6", font=("Microsoft YaHei UI", 8)).pack(pady=(0, 6))
            for w in (card, img_label):
                w.bind("<Double-Button-1>", lambda e, m=meta: self.app.open_digest(m))
                w.bind("<Button-1>", lambda e, m=meta: self._hint(m))
                w.bind("<Button-3>", lambda e, m=meta: self._card_menu(e, m))

    def _card_menu(self, event, meta):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="打开", command=lambda: self.app.open_digest(meta))
        menu.add_command(label="清除本书摘要", command=lambda: self._purge_one(meta, digest_only=True))
        menu.add_command(label="清除本书全部缓存", command=lambda: self._purge_one(meta, digest_only=False))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _purge_one(self, meta, digest_only: bool):
        store.purge_book_cache(meta["id"], digest_only=digest_only)
        if self._alive():
            self.status.config(text=f"已清除：{meta['fileName']}（{'仅摘要' if digest_only else '全部缓存'}）")
            self._render()

    def _alive(self):
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def _hint(self, meta):
        if self._alive():
            self.status.config(text=f"双击打开：{meta['fileName']}　右键卡片可清除该书缓存")

    def _load_thumb(self, label, meta):
        path = store.cover_file(meta["id"])
        try:
            if not path.exists():
                raise FileNotFoundError
            img = Image.open(path)
        except Exception:
            label.config(text="无封面", fg="#778")
            return
        img.thumbnail((THUMB_W, THUMB_H))
        photo = ImageTk.PhotoImage(img)
        self._photo_cache[meta["id"]] = photo
        label.config(image=photo, width=THUMB_W, height=THUMB_H, bg="white")

    def on_error(self, msg):
        self._scanning = False
        messagebox.showerror("扫描失败", str(msg), parent=self)
