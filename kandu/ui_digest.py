"""目录页：每篇文章中英文标题 + 中文摘要。"""
import tkinter as tk
from tkinter import ttk

from kandu import core_parsers as store
from kandu import core_llm as llm
from kandu import core_gen as gen

# 单飞锁：同一本书同时只允许一个生成线程
_RUNNING = set()


class DigestFrame(tk.Frame):
    def __init__(self, parent, app, meta):
        super().__init__(parent, bg="white")
        self.app = app
        self.meta = meta
        self.generating = False

        bar = tk.Frame(self, bg="#1b4b43")
        bar.pack(fill="x")
        ttk.Button(bar, text="← 返回", command=self.app.show_library).pack(side="left", padx=10, pady=6)
        tk.Label(bar, text=meta["fileName"], bg="#1b4b43", fg="white",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left", padx=6)
        self.gen_btn = ttk.Button(bar, text="补全/刷新摘要", command=self.generate)
        self.gen_btn.pack(side="right", padx=10, pady=6)

        self.progress_row = tk.Frame(self, bg="white")
        self.status = tk.Label(self.progress_row, text="", bg="white", fg="#446")
        self.status.pack(side="left", padx=14, pady=4)
        self.bar = ttk.Progressbar(self.progress_row, mode="determinate", length=320)
        self.bar.pack(side="left", padx=8, pady=4)
        self.progress_row.pack(fill="x")

        cols = ("titleEn", "titleZh", "summaryZh")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        headings = {"titleEn": ("英文标题", 360), "titleZh": ("中文标题", 240), "summaryZh": ("中文摘要", 520)}
        for c, (label, width) in headings.items():
            self.tree.heading(c, text=label)
            self.tree.column(c, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.tree.bind("<Double-1>", self._open)

        digest = store.load_digest(meta["id"])
        if digest is None:
            self.generate()
        else:
            self._fill(digest)
            if gen.digest_incomplete(meta):
                self.status.config(text="检测到摘要不完整，自动续传缺失批次…")
                self.generate()
            else:
                self.status.config(text="已加载缓存摘要（点右上角可重新生成）")

    def _open(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        index = int(sel[0])
        self.app.open_reader(self.meta, index)

    def generate(self):
        if self.generating:
            return
        if self.meta["id"] in _RUNNING:
            self.status.config(text="该刊物已在后台生成中，请稍候（防重复烧 token）")
            return
        cfg = self.app.settings["llm"]
        invalid = llm.validate(cfg)
        if invalid:
            self.status.config(text=f"⚠ {invalid}（点右上角「设置」配置）")
            return
        self.generating = True
        _RUNNING.add(self.meta["id"])
        self.gen_btn.config(state="disabled")
        self.bar.config(value=0)
        self.status.config(text="解析刊物正文…")
        self.app.post(self._worker, dict(cfg))

    def _worker(self, cfg):
        def progress(done, total):
            self.app.emit("digest_progress", {"done": done, "total": total})

        def chars(n):
            self.app.emit("digest_chars", n)

        try:
            store.ensure_content(self.meta)
            digest = gen.generate_digest(self.meta, cfg, on_progress=progress, on_chars=chars)
            self.app.emit("digest_done", digest)
        except Exception as e:
            self.app.emit("digest_error", str(e))

    def on_digest_progress(self, p):
        total = max(p["total"], 1)
        self.bar.config(maximum=total, value=p["done"])
        if p["total"] == 0:
            self.status.config(text="摘要已完整，无需生成")
        else:
            self.status.config(text=f"生成中… 第 {p['done']}/{p['total']} 批（只请求缺失部分）")

    def on_digest_chars(self, n):
        if n > 0:
            head = self.status.cget("text").split(" · ")[0]
            self.status.config(text=f"{head} · 已接收 {n} 字")

    def on_digest_done(self, digest):
        self.generating = False
        _RUNNING.discard(self.meta["id"])
        self.gen_btn.config(state="normal")
        self.status.config(text=f"完成：{len(digest['articles'])} 篇（已缓存，重开不再调用大模型）")
        self._fill(digest)

    def on_digest_error(self, msg):
        self.generating = False
        _RUNNING.discard(self.meta["id"])
        self.gen_btn.config(state="normal")
        digest = store.load_digest(self.meta["id"])
        if digest:
            self._fill(digest)
            self.status.config(text=f"部分批次失败：{msg}（已生成部分已保存，再点一次只补缺的）")
        else:
            self.status.config(text=f"出错：{msg}")

    def _fill(self, digest):
        self.tree.delete(*self.tree.get_children())
        for a in digest["articles"]:
            self.tree.insert("", "end", iid=str(a["index"]),
                             values=(a.get("titleEn", ""), a.get("titleZh", ""), a.get("summaryZh", "")))
