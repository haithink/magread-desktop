"""目录页：每篇文章中英文标题 + 中文摘要（卡片式，摘要自动换行）。"""
import tkinter as tk
from tkinter import ttk

from kandu import core_parsers as store
from kandu import core_llm as llm
from kandu import core_gen as gen
from kandu import core_config as cfgmod

FAMILY = "Microsoft YaHei UI"
_RUNNING = set()


class DigestFrame(tk.Frame):
    def __init__(self, parent, app, meta):
        super().__init__(parent, bg="white")
        self.app = app
        self.meta = meta
        self.generating = False
        self._summary_labels = []

        bar = tk.Frame(self, bg="#1b4b43")
        bar.pack(fill="x")
        ttk.Button(bar, text="← 返回", command=self.app.show_library).pack(side="left", padx=10, pady=6)
        tk.Label(bar, text=meta["fileName"], bg="#1b4b43", fg="white",
                 font=(FAMILY, 12, "bold")).pack(side="left", padx=6)
        self.gen_btn = ttk.Button(bar, text="补全/刷新摘要", command=self.generate)
        self.gen_btn.pack(side="right", padx=10, pady=6)
        # 本次生成使用的 LLM（默认 ★ 优先）
        names = cfgmod.llm_names(self.app.settings)
        if len(names) > 1:
            tk.Label(bar, text="使用", bg="#1b4b43", fg="#cde8e1").pack(side="right", padx=(6, 2))
            self.llm_choice = ttk.Combobox(bar, values=names, state="readonly", width=16)
            self.llm_choice.set(cfgmod.default_llm_name(self.app.settings))
            self.llm_choice.pack(side="right", padx=4)

        self.progress_row = tk.Frame(self, bg="white")
        self.status = tk.Label(self.progress_row, text="", bg="white", fg="#446")
        self.status.pack(side="left", padx=14, pady=4)
        self.bar = ttk.Progressbar(self.progress_row, mode="determinate", length=320)
        self.bar.pack(side="left", padx=8, pady=4)
        self.progress_row.pack(fill="x")

        # 卡片式滚动列表（Treeview 不支持换行，弃用）
        body = tk.Frame(self, bg="white")
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg="white", highlightthickness=0, bd=0)
        self.vbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg="white")
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True, padx=(16, 0), pady=(4, 8))
        self.vbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", self._on_canvas_resize)

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

    # ---- 布局 ----
    def _on_canvas_resize(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        w = max(e.width - 40, 320)
        for lbl in self._summary_labels:
            try:
                lbl.configure(wraplength=w)
            except tk.TclError:
                pass

    def on_wheel(self, e):
        try:
            if self.winfo_exists() and self.canvas.winfo_exists():
                self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        except tk.TclError:
            pass

    def _open(self, index):
        self.app.open_reader(self.meta, index)

    # ---- 生成 ----
    def generate(self):
        if self.generating:
            return
        if self.meta["id"] in _RUNNING:
            self.status.config(text="该刊物已在后台生成中，请稍候（防重复烧 token）")
            return
        name = self.llm_choice.get() if hasattr(self, "llm_choice") else None
        cfg = dict(cfgmod.get_llm(self.app.settings, name))
        invalid = llm.validate(cfg)
        if invalid:
            self.status.config(text=f"⚠ {invalid}（点右上角「设置」配置）")
            return
        others = cfgmod.other_llms(self.app.settings, exclude_name=cfg.get("name"))
        self._tried_name = cfg.get("name", "")
        self._can_switch = bool(others)
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
        fb = digest.get("failedBatches") or 0
        if fb:
            censor = "（部分内容触发模型安全过滤）" if digest.get("censorHint") else ""
            switch = "；可换上方 LLM 再点一次只补缺的" if getattr(self, "_can_switch", False) else ""
            self.status.config(text=f"部分批次失败（{fb} 批）{censor}{switch}")
        else:
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

    # ---- 渲染 ----
    def _fill(self, digest):
        for w in self.inner.winfo_children():
            w.destroy()
        self._summary_labels.clear()
        for a in digest["articles"]:
            card = tk.Frame(self.inner, bg="white", bd=1, relief="solid")
            card.pack(fill="x", padx=8, pady=5)
            en = tk.Label(card, text=a.get("titleEn", ""), bg="white", fg="#1b4b43",
                          font=(FAMILY, 11, "bold"), anchor="w", justify="left", wraplength=980)
            en.pack(fill="x", padx=12, pady=(8, 0))
            widgets = [card, en]
            if a.get("titleZh", "").strip():
                zh = tk.Label(card, text=a["titleZh"], bg="white", fg="#7fa829",
                              font=(FAMILY, 10), anchor="w", justify="left", wraplength=980)
                zh.pack(fill="x", padx=12, pady=(2, 0))
                widgets.append(zh)
            if a.get("summaryZh", "").strip():
                sm = tk.Label(card, text=a["summaryZh"], bg="white", fg="#4a5568",
                              font=(FAMILY, 10), anchor="w", justify="left", wraplength=980)
                sm.pack(fill="x", padx=12, pady=(4, 10))
                widgets.append(sm)
                self._summary_labels.append(sm)
            for w in widgets:
                w.bind("<Button-1>", lambda e, i=a["index"]: self._open(i))
                w.bind("<Enter>", lambda e, c=card: c.configure(bg="#eef6f2"))
                w.bind("<Leave>", lambda e, c=card: c.configure(bg="white"))
        self.canvas.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
