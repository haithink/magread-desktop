"""文章摘要浏览：手动阅读标记、状态筛选和本地搜索。"""
import tkinter as tk
from tkinter import ttk

from kandu import core_parsers as store
from kandu import core_llm as llm
from kandu import core_gen as gen
from kandu import core_config as cfgmod

FAMILY = "Microsoft YaHei UI"
PAPER = "#f3f4ef"
GREEN = "#1b4b43"
INK = "#263e38"
MUTED = "#738079"
STATES = {
    "unread": ("未读", "#536a9a", "#edf1f9"),
    "read": ("已读", "#32745c", "#e9f3ed"),
    "uninterested": ("不感兴趣", "#84796d", "#f2eee8"),
}
_RUNNING = set()


class DigestFrame(tk.Frame):
    def __init__(self, parent, app, meta):
        super().__init__(parent, bg=PAPER)
        self.app = app
        self.meta = meta
        self.generating = False
        self._articles = []
        self._text_labels = []
        self._cards = {}
        self._states = store.load_article_states(meta["id"])
        self._search_after = None
        self._restore_after = None
        if not hasattr(app, "_digest_views"):
            app._digest_views = {}
        view = app._digest_views.get(meta["id"], {})
        self._filter = view.get("filter", "all")
        if self._filter not in ("all", *STATES):
            self._filter = "all"
        self.search_var = tk.StringVar(self, value=view.get("search", ""))

        bar = tk.Frame(self, bg=GREEN, padx=20, pady=12)
        bar.pack(fill="x")
        bar.columnconfigure(1, weight=1)
        self._button(bar, "← 书库", self.app.show_library,
                     bg=GREEN, fg="#e1ece5").grid(row=0, column=0, padx=(0, 18))
        tk.Label(bar, text=meta["fileName"], bg=GREEN, fg="white", width=1,
                 font=(FAMILY, 11, "bold"), anchor="w").grid(row=0, column=1, sticky="ew")
        actions = tk.Frame(bar, bg=GREEN)
        actions.grid(row=0, column=2, padx=(12, 0))
        # 本次生成使用的 LLM（默认 ★ 优先）
        names = cfgmod.llm_names(self.app.settings)
        if len(names) > 1:
            self.llm_choice = ttk.Combobox(actions, values=names, state="readonly", width=14)
            self.llm_choice.set(cfgmod.default_llm_name(self.app.settings))
            self.llm_choice.pack(side="left", padx=(0, 12))
        self.gen_btn = self._button(actions, "补全摘要", self.generate, bg="#dfece3", fg=GREEN)
        self.gen_btn.pack(side="left")

        heading = tk.Frame(self, bg=PAPER, padx=32, pady=20)
        heading.pack(fill="x")
        title_row = tk.Frame(heading, bg=PAPER)
        title_row.pack(fill="x")
        tk.Label(title_row, text="文章摘要", bg=PAPER, fg=INK,
                 font=(FAMILY, 23, "bold")).pack(side="left")
        self.overview = tk.Label(title_row, text="", bg=PAPER, fg=GREEN, font=(FAMILY, 10))
        self.overview.pack(side="right", pady=(12, 0))
        tk.Label(heading, text="先浏览，再选一篇慢慢读。阅读状态由你手动标记，自动保存。",
                 bg=PAPER, fg=MUTED, font=(FAMILY, 10), anchor="w").pack(fill="x", pady=(4, 0))

        controls = tk.Frame(self, bg=PAPER, padx=32)
        controls.pack(fill="x")
        controls.columnconfigure(0, weight=1)
        filters = tk.Frame(controls, bg=PAPER)
        filters.grid(row=0, column=0, sticky="w")
        self.filter_buttons = {}
        for key, label in [("all", "全部"), *[(s, v[0]) for s, v in STATES.items()]]:
            button = self._button(filters, label, lambda s=key: self._select_filter(s))
            button.pack(side="left", padx=(0, 6))
            self.filter_buttons[key] = button
        search_box = tk.Frame(controls, bg="white", highlightthickness=1,
                              highlightbackground="#d9dfd7", padx=8, pady=6)
        search_box.grid(row=0, column=1, sticky="e", padx=(12, 0))
        tk.Label(search_box, text="搜索", bg="white", fg=MUTED, font=(FAMILY, 9)).pack(side="left", padx=(0, 8))
        self.search_entry = tk.Entry(search_box, textvariable=self.search_var, width=20,
                                     relief="flat", bg="white", fg=INK, font=(FAMILY, 10))
        self.search_entry.pack(side="left")
        self.search_entry.bind("<Escape>", lambda e: self._reset_filters())
        self._button(search_box, "×", lambda: self.search_var.set(""), padx=3, pady=0).pack(side="left")
        self.result_label = tk.Label(controls, text="", bg=PAPER, fg=MUTED, font=(FAMILY, 9), anchor="w")
        self.result_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 8))

        self.progress_row = tk.Frame(self, bg=PAPER, padx=32, pady=8)
        self.progress_row.pack(side="bottom", fill="x")
        self.progress_row.columnconfigure(0, weight=1)
        self.status = tk.Label(self.progress_row, text="", bg=PAPER, fg=MUTED,
                               font=(FAMILY, 9), anchor="w", justify="left", wraplength=800)
        self.status.grid(row=0, column=0, sticky="ew")
        self.bar = ttk.Progressbar(self.progress_row, mode="determinate", length=160)
        self.bar.grid(row=0, column=1, padx=(12, 0))
        self.bar.grid_remove()

        body = tk.Frame(self, bg=PAPER)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=PAPER, highlightthickness=0, bd=0, yscrollincrement=24)
        self.vbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=PAPER)
        self.inner.bind("<Configure>", lambda e: self._update_scrollregion())
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="n")
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns", pady=(0, 8))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.search_var.trace_add("write", self._queue_search)

        digest = store.load_digest(meta["id"])
        if digest is None:
            self._render()
            self.generate()
        else:
            self._fill(digest)
            if gen.digest_incomplete(meta):
                self.status.config(text="检测到摘要不完整，自动续传缺失批次…")
                self.generate()
            else:
                self.status.config(text="摘要已缓存 · 阅读标记仅保存在本机")
        self._restore_after = self.after(30, lambda: self._restore_scroll(view.get("scroll", 0)))

    # ---- 布局 ----
    @staticmethod
    def _button(parent, text, command, bg="white", fg=INK, padx=12, pady=6):
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                         activebackground="#e3ebe3", activeforeground=GREEN,
                         relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                         font=(FAMILY, 10), padx=padx, pady=pady)

    def _update_scrollregion(self):
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), self.inner.winfo_reqheight()))

    def _on_canvas_resize(self, e):
        self._layout_content(e.width)

    def _layout_content(self, available):
        width = max(240, min(available - 48, 1024))
        self.canvas.coords(self._win, available / 2, 0)
        self.canvas.itemconfigure(self._win, width=width)
        for lbl in self._text_labels:
            lbl.configure(wraplength=width - 56)
        self.status.configure(wraplength=max(200, available - 260))
        self._update_scrollregion()

    def _restore_scroll(self, fraction):
        self._restore_after = None
        # 标题和摘要换行会改变列表高度，先完成布局再按比例恢复位置。
        self.canvas.update_idletasks()
        self._update_scrollregion()
        self.canvas.yview_moveto(fraction)

    def _remember_view(self):
        self.app._digest_views[self.meta["id"]] = {
            "filter": self._filter, "search": self.search_var.get(),
            "scroll": self.canvas.yview()[0],
        }

    def destroy(self):
        self._remember_view()
        for pending in (self._search_after, self._restore_after):
            if pending:
                self.after_cancel(pending)
        super().destroy()

    def on_wheel(self, e):
        try:
            if self.winfo_exists() and self.canvas.winfo_exists():
                if e.delta:
                    self.canvas.yview_scroll(-max(1, abs(e.delta) // 120) * (1 if e.delta > 0 else -1) * 3, "units")
        except tk.TclError:
            pass

    def _open(self, index):
        self._remember_view()
        self.app.open_reader(self.meta, index)

    def _state(self, index):
        return self._states.get(str(index), "unread")

    def _mark(self, index, state):
        if self._state(index) == state:
            return
        try:
            self._states = store.set_article_state(self.meta["id"], index, state)
        except (OSError, ValueError) as e:
            self.status.config(text=f"标记保存失败：{e}")
            return
        self._update_counts()
        card = self._cards.get(index)
        if card is None:
            return
        if self._filter == "all" or self._filter == state:
            label, color, tint = STATES[state]
            card["badge"].config(text=f"  {label}  ", fg=color, bg=tint)
            for key, button in card["marks"].items():
                _, fg, bg = STATES[key]
                button.config(bg=bg if key == state else "white",
                              fg=fg if key == state else MUTED)
        else:
            # 只移除不再符合筛选的卡片。保留其他控件，避免整页重建和闪烁。
            top = self.canvas.canvasy(0)
            anchor = next((c["frame"] for i, c in self._cards.items() if i != index
                           and c["frame"].winfo_y() + c["frame"].winfo_height() > top), None)
            offset = anchor.winfo_y() - top if anchor is not None else 0
            self._text_labels = [w for w in self._text_labels if w not in card["labels"]]
            card["frame"].destroy()
            del self._cards[index]
            self._visible_articles = [a for a in self._visible_articles if a["index"] != index]
            if not self._visible_articles:
                self._show_empty()
            self._update_result_label()
            self.canvas.update_idletasks()
            self._update_scrollregion()
            if anchor is not None:
                height = max(1, self.inner.winfo_reqheight())
                self.canvas.yview_moveto(max(0, anchor.winfo_y() - offset) / height)

    def _select_filter(self, state):
        self._filter = state
        self._render()

    def _queue_search(self, *_):
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(160, self._apply_search)

    def _apply_search(self):
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = None
        self._render()

    def _reset_filters(self):
        self._filter = "all"
        self.search_var.set("")
        self._apply_search()

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
        self.bar.grid()
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
        self.bar.grid_remove()
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
        self.bar.grid_remove()
        digest = store.load_digest(self.meta["id"])
        if digest:
            self._fill(digest)
            self.status.config(text=f"部分批次失败：{msg}（已生成部分已保存，再点一次只补缺的）")
        else:
            self.status.config(text=f"出错：{msg}")

    # ---- 渲染 ----
    def _fill(self, digest):
        self._articles = digest["articles"]
        self._render(keep_scroll=True)

    def _render(self, keep_scroll=False):
        scroll = self.canvas.yview()[0] if keep_scroll else 0
        for w in self.inner.winfo_children():
            w.destroy()
        self._text_labels.clear()
        self._cards.clear()
        self._update_counts()
        query = self.search_var.get().strip().casefold()
        self._visible_articles = [a for a in self._articles
                                  if (self._filter == "all" or self._state(a["index"]) == self._filter)
                                  and all(term in " ".join(str(a.get(k, "")) for k in
                                          ("titleEn", "titleZh", "summaryZh")).casefold()
                                          for term in query.split())]
        self._update_result_label()
        for article in self._visible_articles:
            self._make_card(article)
        if not self._visible_articles:
            self._show_empty()
        self._layout_content(self.canvas.winfo_width())
        self.canvas.update_idletasks()
        self._update_scrollregion()
        self.canvas.yview_moveto(scroll)

    def _update_counts(self):
        self._counts = {s: sum(self._state(a["index"]) == s for a in self._articles) for s in STATES}
        total = len(self._articles)
        for key, button in self.filter_buttons.items():
            label = "全部" if key == "all" else STATES[key][0]
            count = total if key == "all" else self._counts[key]
            button.config(text=f"{label}  {count}", bg=GREEN if key == self._filter else "#e7ebe4",
                          fg="white" if key == self._filter else MUTED)
        self.overview.config(text=f"{total} 篇文章   /   已读 {self._counts['read']} 篇")

    def _update_result_label(self):
        query = self.search_var.get().strip()
        self.result_label.config(text=f"显示 {len(self._visible_articles)} / {len(self._articles)} 篇" +
                                 (f" · 搜索：{query}" if query else ""))

    def _show_empty(self):
        empty = tk.Frame(self.inner, bg=PAPER, pady=56)
        empty.pack(fill="x")
        tk.Label(empty, text="暂时没有匹配的文章", bg=PAPER, fg=INK,
                 font=(FAMILY, 15, "bold")).pack()
        tk.Label(empty, text="试试其他状态，或换一个搜索词。", bg=PAPER,
                 fg=MUTED, font=(FAMILY, 10)).pack(pady=(10, 18))
        self._button(empty, "查看全部文章", self._reset_filters, bg=GREEN, fg="white").pack()

    def _make_card(self, article):
        index = article["index"]
        state = self._state(index)
        label, color, tint = STATES[state]
        card = tk.Frame(self.inner, bg="white", highlightthickness=1,
                        highlightbackground="#dce3da", padx=24, pady=18)
        card.pack(fill="x", pady=(2, 14))
        meta_row = tk.Frame(card, bg="white")
        meta_row.pack(fill="x", pady=(0, 10))
        tk.Label(meta_row, text=f"ARTICLE  {index + 1:02d}", bg="white", fg=MUTED,
                 font=(FAMILY, 9)).pack(side="left")
        badge = tk.Label(meta_row, text=f"  {label}  ", bg=tint, fg=color,
                         font=(FAMILY, 9), pady=3, width=8)
        badge.pack(side="right")
        text_widgets = []
        for key, font, fg, padding in [
            ("titleEn", ("Georgia", 17, "bold"), INK, (0, 6)),
            ("titleZh", (FAMILY, 11, "bold"), "#526a5c", (0, 12)),
            ("summaryZh", (FAMILY, 11), "#56615d", (0, 16)),
        ]:
            text = article.get(key, "").strip()
            if not text:
                continue
            widget = tk.Label(card, text=text, bg="white", fg=fg, font=font,
                              anchor="w", justify="left", wraplength=940, cursor="hand2")
            widget.pack(fill="x", pady=padding)
            widget.bind("<Button-1>", lambda e, i=index: self._open(i))
            text_widgets.append(widget)
        self._text_labels.extend(text_widgets)
        tk.Frame(card, bg="#edf0ea", height=1).pack(fill="x", pady=(0, 12))
        footer = tk.Frame(card, bg="white")
        footer.pack(fill="x")
        tk.Label(footer, text="标记为", bg="white", fg=MUTED,
                 font=(FAMILY, 9)).pack(side="left", padx=(0, 8))
        buttons = {}
        for key, (name, fg, bg) in STATES.items():
            button = self._button(footer, name, lambda i=index, s=key: self._mark(i, s),
                                  bg=bg if state == key else "white",
                                  fg=fg if state == key else MUTED, padx=10, pady=5)
            button.pack(side="left", padx=(0, 4))
            buttons[key] = button
        open_button = self._button(footer, "阅读全文  →", lambda i=index: self._open(i), bg=GREEN, fg="white")
        open_button.pack(side="right")
        self._cards[index] = {"frame": card, "badge": badge, "marks": buttons,
                              "open": open_button, "labels": text_widgets}
