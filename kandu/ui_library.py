"""书库：应用自管的刊物收藏（添加/移除），不依赖磁盘目录。"""
import os
import re
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

from kandu import core_config as cfgmod
from kandu import core_parsers as store

FAMILY = "Microsoft YaHei UI"
THUMB_W, THUMB_H = 126, 166
PAPER = "#f3f4ef"
GREEN = "#1b4b43"
INK = "#263e38"
MUTED = "#738079"
WHITE = "#fffdf8"


class LibraryFrame(tk.Frame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, bg=PAPER)
        self.app = app
        self.metas = store.load_library()
        self._photo_cache = {}
        self._importing = False
        self._cards = {}
        self._recent_cards = []
        self._visible_ids = []
        self._mounted_ids = set()
        self._cols = 0
        self._search_after = self._layout_after = self._thumb_after = None
        self._tick_after = None
        self._format = "all"
        self._digest_ids = set()
        self._card_popup = None
        self.bind("<Destroy>", self._on_destroy, add="+")

        bar = tk.Frame(self, bg=GREEN, padx=24, pady=10)
        self.toolbar = bar
        bar.pack(fill="x")
        self._logo = tk.PhotoImage(master=self, file=str(cfgmod.asset_path("kandu-32.png")))
        tk.Label(bar, image=self._logo, bg=GREEN).pack(side="left", padx=(0, 10))
        tk.Label(bar, text="刊读", bg=GREEN, fg="white", font=(FAMILY, 15, "bold")).pack(side="left")
        tk.Label(bar, text="ENGLISH READING", bg=GREEN, fg="#bdd0c5", font=(FAMILY, 8)).pack(side="left", padx=16)
        self._button(bar, "设置", self.app.open_settings, bg=GREEN, fg="#e0eae2").pack(side="right")
        self._button(bar, "从文件夹导入", self.import_folder, bg=GREEN, fg="#e0eae2").pack(side="right", padx=12)
        self._button(bar, "+ 添加刊物", self.add_files, bg="#e5eddf").pack(side="right")

        heading = tk.Frame(self, bg=PAPER, padx=28, pady=10)
        heading.pack(fill="x")
        tk.Label(heading, text="我的书库", bg=PAPER, fg=INK, font=(FAMILY, 17, "bold")).pack(side="left")
        self.overview = tk.Label(heading, bg=PAPER, fg=MUTED, font=(FAMILY, 9))
        self.overview.pack(side="left", padx=16)
        self.digest_btn = self._button(heading, "生成全部摘要", self.app.start_batch_digest)
        self.digest_btn.pack(side="right")
        self.index_btn = self._button(heading, "建立索引", self.app.start_indexing)
        self.index_btn.pack(side="right", padx=(0, 8))

        controls = tk.Frame(self, bg=PAPER, padx=28, pady=2)
        controls.pack(fill="x")
        self.filter_buttons = {}
        for key, title in (("all", "全部"), ("epub", "EPUB"), ("pdf", "PDF")):
            btn = self._button(controls, title, lambda k=key: self._select_format(k), pady=4)
            btn.pack(side="left", padx=(0, 6))
            self.filter_buttons[key] = btn
        self.sort_var = tk.StringVar(self, "默认顺序")
        self.sort_choice = ttk.Combobox(controls, textvariable=self.sort_var, state="readonly",
                                        values=("默认顺序", "名称排序", "最近打开"), width=11)
        self.sort_choice.pack(side="right", padx=(12, 0), ipady=3)
        self.sort_choice.bind("<<ComboboxSelected>>", lambda e: self._apply_filters())
        search = tk.Frame(controls, bg=WHITE, highlightthickness=1, highlightbackground="#dce2d7", padx=8, pady=4)
        search.pack(side="right")
        tk.Label(search, text="搜索刊物", bg=WHITE, fg=MUTED, font=(FAMILY, 9)).pack(side="left", padx=(0, 8))
        self.search_var = tk.StringVar(self)
        self.search_entry = tk.Entry(search, textvariable=self.search_var, width=23, bg=WHITE, fg=INK,
                                      relief="flat", font=(FAMILY, 10))
        self.search_entry.pack(side="left")
        self.search_entry.bind("<Escape>", lambda e: self.search_var.set(""))
        self._button(search, "×", lambda: self.search_var.set(""), bg=WHITE, padx=3, pady=0).pack(side="left")
        self.search_var.trace_add("write", self._queue_search)

        footer = tk.Frame(self, bg=PAPER, padx=28, pady=7)
        footer.pack(side="bottom", fill="x")
        self.status = tk.Label(footer, text="单击封面打开刊物 · 右键管理", bg=PAPER, fg=MUTED,
                               anchor="w", width=1, font=(FAMILY, 9))
        self.status.pack(fill="x")

        body = tk.Frame(self, bg=PAPER)
        body.pack(fill="both", expand=True, padx=(22, 12), pady=(12, 0))
        self.canvas = tk.Canvas(body, bg=PAPER, highlightthickness=0)
        self.vbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=PAPER)
        self.inner.bind("<Configure>", self._inner_configure)
        self._canvas_item = self.canvas.create_window((0, 0), window=self.inner, anchor="nw", width=800)
        self.canvas.configure(yscrollcommand=self._scroll_changed)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", self._canvas_configure)
        self.recent_section = tk.Frame(self.inner, bg=PAPER)
        self.recent_section.pack(fill="x", padx=6)
        shelf_heading = tk.Frame(self.inner, bg=PAPER)
        shelf_heading.pack(fill="x", padx=6, pady=(0, 6))
        self.result_label = tk.Label(shelf_heading, bg=PAPER, fg=INK, font=(FAMILY, 10, "bold"))
        self.result_label.pack(side="left")
        self.shelf = tk.Frame(self.inner, bg=PAPER)
        self.shelf.pack(fill="x")
        self.empty = tk.Frame(self.inner, bg=WHITE, padx=32, pady=32,
                               highlightthickness=1, highlightbackground="#dde3d8")
        self.empty_title = tk.Label(self.empty, bg=WHITE, fg=GREEN, font=(FAMILY, 17, "bold"))
        self.empty_title.pack(pady=(0, 10))
        self.empty_text = tk.Label(self.empty, bg=WHITE, fg=MUTED, font=(FAMILY, 10))
        self.empty_text.pack(pady=(0, 18))
        self.empty_action = self._button(self.empty, "添加刊物", self.add_files, bg=GREEN, fg="white")
        self.empty_action.pack()
        self._render()
        self._tick_after = self.after(400, self._tick_index_status)

        if self.metas or self.app.settings.get("libraryDir"):
            self._maybe_migrate()

    @staticmethod
    def _button(parent, text, command, bg="#e6ebe1", fg=GREEN, padx=12, pady=6):
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground="#d4e2d2",
                         activeforeground=GREEN, disabledforeground="#9ca99f", relief="flat", bd=0,
                         highlightthickness=0, cursor="hand2", font=(FAMILY, 9), padx=padx, pady=pady)

    def _on_destroy(self, event):
        if event.widget is self:
            for name in ("_search_after", "_layout_after", "_thumb_after", "_tick_after"):
                job = getattr(self, name)
                if job is not None:
                    self.after_cancel(job)
                    setattr(self, name, None)

    def _canvas_configure(self, event):
        self.canvas.itemconfigure(self._canvas_item, width=event.width)
        if self._layout_after is not None:
            self.after_cancel(self._layout_after)
        self._layout_after = self.after(70, self._layout_cards)

    def _inner_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._queue_thumbs()

    def _scroll_changed(self, first, last):
        self.vbar.set(first, last)
        self._queue_thumbs()

    def _queue_thumbs(self):
        if self._thumb_after is None:
            self._thumb_after = self.after_idle(self._load_visible_thumbs)

    def on_wheel(self, e):
        try:
            if self._alive() and self.canvas.winfo_exists():
                self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        except tk.TclError:
            pass

    # ---------- 添加 / 导入 ----------
    def add_files(self):
        files = filedialog.askopenfilenames(
            title="选择刊物文件（可多选）",
            filetypes=[("刊物文件", "*.epub *.pdf"), ("所有文件", "*.*")],
            initialdir=self._suggest_dir())
        if not files:
            return
        self._import(list(files))

    def import_folder(self):
        d = filedialog.askdirectory(title="选择包含 EPUB / PDF 的文件夹",
                                    initialdir=self._suggest_dir())
        if not d:
            return
        files = []
        for name in sorted(os.listdir(d)):
            if name.lower().endswith((".epub", ".pdf")):
                files.append(os.path.join(d, name))
        if not files:
            messagebox.showinfo("没有找到刊物", "该文件夹下没有 EPUB / PDF 文件", parent=self)
            return
        self._import(files)

    def _suggest_dir(self):
        bd = self.app.settings.get("booksDir")
        if bd and os.path.isdir(bd):
            return bd
        ld = self.app.settings.get("libraryDir")
        return ld if ld and os.path.isdir(ld) else os.path.expanduser("~")

    def _import(self, files):
        if self._importing:
            return
        # 存储位置：设置指定；否则默认 appdata/books，
        # 若空间不足且源文件在别的盘，则自动改用源盘 KanduLibrary
        books_dir = self.app.settings.get("booksDir") or str(cfgmod.app_dir() / "books")
        try:
            need = sum(os.path.getsize(f) for f in files)
            free = shutil.disk_usage(books_dir.split(":")[0] + ":").free if ":" in books_dir else 0
            src_drive = os.path.splitdrive(files[0])[0]
            if free < need + 1024 ** 3 and src_drive and src_drive != os.path.splitdrive(books_dir)[0]:
                books_dir = src_drive + "\\KanduLibrary"
                self.app.settings["booksDir"] = books_dir
                self.app.save_settings()
                messagebox.showinfo(
                    "存储位置调整",
                    f"默认存储盘空间不足，书库文件将存放在：\n{books_dir}", parent=self)
        except Exception:
            pass
        self._importing = True
        self.status.config(text=f"导入中… 0/{len(files)}")
        self.app.post(self._import_worker, files, books_dir)

    def _import_worker(self, files, books_dir):
        added = skipped = 0
        for i, f in enumerate(files):
            self.app.emit("import_progress", {"done": i, "total": len(files), "name": os.path.basename(f)})
            try:
                meta, status = store.add_book(f, books_dir)
                if status == "added":
                    added += 1
                else:
                    skipped += 1
            except Exception as e:
                self.app.emit("import_error", f"{os.path.basename(f)}：{e}")
                continue
            store.ensure_cover(meta)
        self.app.emit("import_done", {"added": added, "skipped": skipped, "total": len(files)})

    def _maybe_migrate(self):
        """旧版本迁移：把原 libraryDir 里的刊物一次性导入书库。"""
        if self.app.settings.get("migratedLibrary"):
            return
        src = self.app.settings.get("libraryDir")
        if not src or not os.path.isdir(src):
            return
        files = [os.path.join(src, n) for n in sorted(os.listdir(src))
                 if n.lower().endswith((".epub", ".pdf"))]
        if not files:
            return
        self.app.settings["migratedLibrary"] = True
        self.app.save_settings()
        # 存储位置：与源同盘的 KanduLibrary（同盘移动瞬时完成，不占 C 盘）
        drive = os.path.splitdrive(src)[0]
        books_dir = drive + "\\KanduLibrary"
        self.app.settings["booksDir"] = books_dir
        self.app.save_settings()
        self._importing = True
        self.status.config(text=f"检测到原目录有 {len(files)} 本刊物，正在迁移进书库…")
        self.app.post(self._migrate_worker, files, books_dir)

    def _migrate_worker(self, files, books_dir):
        added = 0
        for i, f in enumerate(files):
            self.app.emit("import_progress", {"done": i, "total": len(files), "name": os.path.basename(f)})
            try:
                meta, status = store.add_book(f, books_dir)
                if status == "added":
                    added += 1
            except Exception as e:
                self.app.emit("import_error", f"{os.path.basename(f)}：{e}")
                continue
            store.ensure_cover(meta)
        self.app.emit("import_done", {"added": added, "skipped": 0, "total": len(files)})

    def on_import_progress(self, p):
        self.status.config(text=f"导入中… {p['done']}/{p['total']}　{p['name']}")

    def on_import_done(self, r):
        self._importing = False
        self.metas = store.load_library()
        self.status.config(text=f"导入完成：新增 {r['added']} 本" +
                                (f"，跳过已存在 {r['skipped']} 本" if r["skipped"] else ""))
        self._render()

    def on_import_error(self, msg):
        messagebox.showerror("导入失败", str(msg), parent=self)

    # ---------- 渲染 ----------
    def _render(self):
        """仅书库数据变化时刷新；筛选、排序和缩放窗口复用已有卡片与封面。"""
        by_id = {m["id"]: m for m in self.metas}
        for book_id in set(self._cards) - by_id.keys():
            self._cards.pop(book_id)["frame"].destroy()
            self._photo_cache.pop(book_id, None)
            self._mounted_ids.discard(book_id)
        self._digest_ids = {m["id"] for m in self.metas if store.has_digest(m["id"])}
        for meta in self.metas:
            entry = self._cards.get(meta["id"])
            if entry and entry["meta"] != meta:
                entry["frame"].destroy()
                self._cards.pop(meta["id"])
                self._photo_cache.pop(meta["id"], None)
                entry = None
            if entry is None:
                entry = self._cards[meta["id"]] = self._make_card(meta)
            elif meta["id"] not in self._photo_cache:
                entry["loaded"] = False
            ready = meta["id"] in self._digest_ids
            entry["state"].configure(text="摘要已就绪" if ready else "待生成摘要",
                                      fg="#4e7857" if ready else "#9a8b72")
        self.overview.configure(text=f"{len(self.metas)} 本刊物  ·  {len(self._digest_ids)} 本有摘要")
        self._render_recent(by_id)
        self._apply_filters()

    @staticmethod
    def _book_title(meta):
        return os.path.splitext(meta["fileName"])[0].replace("_", " ")

    def _make_card(self, meta):
        card = tk.Frame(self.shelf, bg=WHITE, width=186, height=264,
                         highlightthickness=1, highlightbackground="#dfe4d9", takefocus=True, cursor="hand2")
        card.pack_propagate(False)
        top = tk.Frame(card, bg=WHITE)
        top.pack(fill="x", padx=12, pady=(6, 0))
        badge = tk.Label(top, text=meta["ext"].upper(), bg=WHITE, fg=MUTED, font=(FAMILY, 8))
        badge.pack(side="left")
        menu = self._button(top, "···", lambda: self._button_menu(menu, meta), bg=WHITE, padx=5, pady=0)
        menu.pack(side="right")
        cover = tk.Canvas(card, width=THUMB_W, height=THUMB_H, bg="#edf0e7", highlightthickness=0)
        cover.pack(pady=(2, 5))
        # 无封面也按像素布局，避免 Label 的 width/height 被当成字符数。
        title = self._book_title(meta)
        cover.create_rectangle(0, 0, THUMB_W, THUMB_H, fill="#e3e9dc", outline="")
        cover.create_rectangle(0, 0, 7, THUMB_H, fill=GREEN, outline="")
        cover.create_text(20, 22, text="KANDU / READ", font=(FAMILY, 7), fill="#55715c", anchor="nw")
        cover.create_line(20, 42, THUMB_W - 18, 42, fill="#9eaf94")
        cover.create_text(20, 58, text=title[:55], width=THUMB_W - 36, anchor="nw",
                           font=(FAMILY, 10, "bold"), fill=GREEN)
        cover.create_text(20, THUMB_H - 24, text=meta["ext"].upper(), font=(FAMILY, 8), fill="#55715c", anchor="w")
        name = tk.Label(card, text=title, bg=WHITE, fg=INK, font=(FAMILY, 9), height=2,
                         wraplength=164, anchor="n", justify="center")
        name.pack(fill="x", padx=12)
        state = tk.Label(card, bg=WHITE, font=(FAMILY, 8))
        state.pack(pady=(2, 5))
        for widget in (card, top, badge, cover, name, state):
            widget.bind("<Button-1>", lambda e, m=meta: self.app.open_digest(m))
            widget.bind("<Button-3>", lambda e, m=meta: self._card_menu(e, m))
            widget.bind("<Enter>", lambda e: card.configure(highlightbackground="#9bb397"))
            widget.bind("<Leave>", lambda e: card.configure(highlightbackground="#dfe4d9"))
        card.bind("<Return>", lambda e: self.app.open_digest(meta))
        card.bind("<FocusIn>", lambda e: card.configure(highlightbackground=GREEN))
        card.bind("<FocusOut>", lambda e: card.configure(highlightbackground="#dfe4d9"))
        return {"frame": card, "meta": dict(meta), "cover": cover, "title": name, "state": state,
                "loaded": False, "position": None, "placed": None, "wrap": 164}

    def _render_recent(self, by_id):
        for widget in self.recent_section.winfo_children():
            widget.destroy()
        self._recent_cards.clear()
        recent = [by_id[i] for i in dict.fromkeys(self.app.settings.get("recentBooks") or []) if i in by_id][:3]
        if recent:
            tk.Label(self.recent_section, text="继续阅读", bg=PAPER, fg=GREEN,
                     font=(FAMILY, 10, "bold")).pack(anchor="w", pady=(0, 7))
            row = tk.Frame(self.recent_section, bg=PAPER)
            row.pack(fill="x", pady=(0, 16))
            for i, meta in enumerate(recent):
                row.columnconfigure(i, weight=1, uniform="recent")
                mini = tk.Frame(row, bg="#e7eee1", height=84, highlightthickness=1, highlightbackground="#d8e2cf")
                mini.grid(row=0, column=i, sticky="ew", padx=(0, 10 if i < len(recent) - 1 else 0))
                mini.grid_propagate(False)
                mini.columnconfigure(0, weight=1)
                history = store.load_reading_history(meta["id"])
                latest = max(history.items(), key=lambda pair: pair[1]["lastReadAt"], default=None)
                label = tk.Label(mini, text=self._book_title(meta), width=1, anchor="w", bg="#e7eee1",
                                  fg=GREEN, font=(FAMILY, 9, "bold"))
                label.grid(row=0, column=0, sticky="ew", padx=12, pady=(9, 2))
                hint = (latest[1].get("titleZh") or latest[1]["title"]) if latest else "浏览本刊文章摘要"
                line = tk.Label(mini, text=hint, width=1, anchor="w", bg="#e7eee1", fg=MUTED, font=(FAMILY, 8))
                line.grid(row=1, column=0, sticky="ew", padx=12)
                action = lambda m=meta, last=latest: self._continue_reading(m, last)
                btn = self._button(mini, "继续 →" if latest else "打开 →", action, bg="#e7eee1", padx=8, pady=3)
                btn.grid(row=0, column=1, rowspan=2, padx=(0, 7), pady=(8, 0))
                if latest:
                    record = latest[1]
                    progress = "已到文末" if record["atEnd"] else f"第 {record['paragraphIndex'] + 1} 段 · {int(record['progress'] * 100)}%"
                    tk.Label(mini, text=progress, anchor="w", bg="#e7eee1", fg="#567451",
                             font=(FAMILY, 8)).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(3, 7))
                for widget in (mini, label, line):
                    widget.configure(cursor="hand2")
                    widget.bind("<Button-1>", lambda e, fn=action: fn())
                self._recent_cards.append(mini)

    def _continue_reading(self, meta, latest):
        if latest:
            recent = [i for i in self.app.settings.get("recentBooks", []) if i != meta["id"]]
            self.app.settings["recentBooks"] = [meta["id"], *recent][:12]
            self.app.save_settings()
            self.app.open_reader(meta, int(latest[0]))
        else:
            self.app.open_digest(meta)

    def _queue_search(self, *_):
        if self._search_after is not None:
            self.after_cancel(self._search_after)
        self._search_after = self.after(150, self._apply_filters)

    def _select_format(self, key):
        self._format = key
        self._apply_filters()

    def _reset_filters(self):
        self.search_var.set("")
        self._format = "all"
        self._apply_filters()

    @staticmethod
    def _search_text(text):
        return re.sub(r"[\s_\-]+", " ", text.casefold()).strip()

    def _apply_filters(self):
        if self._search_after is not None:
            self.after_cancel(self._search_after)
            self._search_after = None
        words = self._search_text(self.search_var.get()).split()
        metas = [m for m in self.metas if (self._format == "all" or m["ext"] == self._format)
                 and all(word in self._search_text(m["fileName"]) for word in words)]
        if self.sort_var.get() == "名称排序":
            metas.sort(key=lambda m: self._search_text(m["fileName"]))
        elif self.sort_var.get() == "最近打开":
            order = {book_id: i for i, book_id in enumerate(self.app.settings.get("recentBooks", []))}
            metas.sort(key=lambda m: order.get(m["id"], len(order)))
        self._visible_ids = [m["id"] for m in metas]
        visible = set(self._visible_ids)
        for book_id, entry in self._cards.items():
            if book_id not in visible:
                entry["frame"].place_forget()
                entry["placed"] = None
                self._mounted_ids.discard(book_id)
        for key, button in self.filter_buttons.items():
            button.configure(bg=GREEN if key == self._format else "#e6ebe1",
                             fg="white" if key == self._format else GREEN)
        filtered = self._format != "all" or bool(words)
        self.result_label.configure(text=f"找到 {len(metas)} / {len(self.metas)} 本刊物" if filtered else f"全部刊物  /  {len(metas)}")
        if metas:
            self.empty.pack_forget()
        else:
            self.empty.pack(fill="x", padx=6, pady=8)
            self.empty_title.configure(text="没有找到匹配的刊物" if self.metas else "从一本刊物，开始阅读")
            self.empty_text.configure(text="试试刊名、日期，或切换文件格式。" if self.metas else "添加 EPUB / PDF 刊物，在这里收藏并继续你的英文阅读。")
            self.empty_action.configure(text="清除筛选" if self.metas else "+ 添加刊物",
                                        command=self._reset_filters if self.metas else self.add_files)
        self._layout_cards()
        self.canvas.yview_moveto(0)

    def _layout_cards(self):
        if self._layout_after is not None:
            self.after_cancel(self._layout_after)
            self._layout_after = None
        width = max(1, self.canvas.winfo_width())
        cols = max(1, min(8, width // 192))
        self._cols = cols
        self.shelf.configure(height=((len(self._visible_ids) + cols - 1) // cols) * 276)
        for i, book_id in enumerate(self._visible_ids):
            entry = self._cards[book_id]
            left, right = (i % cols) * width // cols, (i % cols + 1) * width // cols
            entry["position"] = (left + 6, i // cols * 276, max(1, right - left - 12))
            wrap = max(100, right - left - 36)
            if wrap != entry["wrap"]:
                entry["title"].configure(wraplength=wrap)
                entry["wrap"] = wrap
        self._queue_thumbs()

    def _load_visible_thumbs(self):
        self._thumb_after = None
        if not self._alive():
            return
        top = self.canvas.canvasy(0) - 280
        bottom = self.canvas.canvasy(self.canvas.winfo_height()) + 280
        mounted = set()
        for book_id in self._visible_ids:
            entry = self._cards[book_id]
            x, row_y, width = entry["position"]
            y = self.shelf.winfo_y() + row_y
            if top <= y + 264 and y <= bottom:
                mounted.add(book_id)
                if entry["placed"] != entry["position"]:
                    entry["frame"].place(x=x, y=row_y, width=width, height=264)
                    entry["placed"] = entry["position"]
                if not entry["loaded"]:
                    self._load_thumb(entry)
        for book_id in self._mounted_ids - mounted:
            self._cards[book_id]["frame"].place_forget()
            self._cards[book_id]["placed"] = None
        self._mounted_ids = mounted

    def _load_thumb(self, entry):
        entry["loaded"] = True
        path = store.cover_file(entry["meta"]["id"])
        if not path.is_file():
            return
        try:
            with Image.open(path) as original:
                original.thumbnail((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
                image = original.convert("RGB")
            photo = ImageTk.PhotoImage(image, master=self)
        except (OSError, ValueError):
            return
        self._photo_cache[entry["meta"]["id"]] = photo
        entry["cover"].delete("all")
        entry["cover"].create_image(THUMB_W // 2, THUMB_H // 2, image=photo)

    def _button_menu(self, button, meta):
        self._show_menu(meta, button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())

    def _alive(self):
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def _card_menu(self, event, meta):
        self._show_menu(meta, event.x_root, event.y_root)

    def _show_menu(self, meta, x, y):
        if self._card_popup is not None:
            self._card_popup.destroy()
        menu = tk.Menu(self, tearoff=0)
        self._card_popup = menu
        menu.add_command(label="打开", command=lambda: self.app.open_digest(meta))
        menu.add_command(label="清除本书摘要", command=lambda: self._purge_one(meta, digest_only=True))
        menu.add_separator()
        menu.add_command(label="移出书库（删除文件与缓存）", command=lambda: self._remove_book(meta))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _purge_one(self, meta, digest_only: bool):
        store.purge_book_cache(meta["id"], digest_only=digest_only)
        if self._alive():
            self.status.config(text=f"已清除：{meta['fileName']}（{'仅摘要' if digest_only else '全部缓存'}）")
            self._render()

    def _remove_book(self, meta):
        if not messagebox.askyesno(
                "移出书库",
                f"将「{meta['fileName']}」移出书库？\n\n"
                "会删除：书库中的文件副本、摘要、生词表与句解缓存、阅读标记和阅读位置。\n"
                "不影响你磁盘上其他位置的文件。",
                parent=self):
            return
        store.remove_book(meta["id"])
        if self._alive():
            self.metas = store.load_library()
            self.status.config(text=f"已移出：{meta['fileName']}")
            self._render()

    def on_error(self, msg):
        self._importing = False
        messagebox.showerror("操作失败", str(msg), parent=self)

    # ---------- 后台索引进度 ----------
    def _tick_index_status(self):
        self._tick_after = None
        if not self._alive():
            return
        st = self.app.index_state
        bt = self.app.batch_state
        try:
            if bt["running"]:
                self.status.config(text=f"批量生成摘要中… {bt['done']}/{bt['total']}　{bt['current']}")
                self.index_btn.configure(state="disabled")
                self.digest_btn.configure(state="disabled")
            elif st["running"]:
                self.status.config(text=f"后台索引中… {st['done']}/{st['total']}　{st['current']}")
                self.index_btn.configure(state="disabled")
                self.digest_btn.configure(state="disabled")
            else:
                self.index_btn.configure(state="normal")
                self.digest_btn.configure(state="normal")
                if bt["done"] and not bt.get("reported"):
                    bt["reported"] = True
                    msg = f"批量摘要完成：{bt['total']} 本" + (f"，{bt['failed']} 本有失败批次（可再跑续补）" if bt["failed"] else "")
                    self.status.config(text=msg)
                    self._refresh_assets()
                elif st["done"] and not st.get("reported"):
                    st["reported"] = True
                    skipped = st.get("skipped", 0)
                    msg = f"索引建立完成：本次处理 {st['done']} 本"
                    if skipped:
                        msg += f"（另有 {skipped} 本已有索引，自动跳过）"
                    if st["errors"]:
                        msg += f"，失败 {len(st['errors'])}（详见 error.log）"
                        for e in st["errors"]:
                            self.app._log_error("索引失败：" + e)
                    self.status.config(text=msg)
                    self._refresh_assets()
        except tk.TclError:
            pass
        self._tick_after = self.after(400, self._tick_index_status)

    def _refresh_assets(self):
        self._digest_ids = {m["id"] for m in self.metas if store.has_digest(m["id"])}
        for book_id, entry in self._cards.items():
            ready = book_id in self._digest_ids
            entry["state"].configure(text="摘要已就绪" if ready else "待生成摘要",
                                      fg="#4e7857" if ready else "#9a8b72")
            if book_id not in self._photo_cache:
                entry["loaded"] = False
        self.overview.configure(text=f"{len(self.metas)} 本刊物  ·  {len(self._digest_ids)} 本有摘要")
        self._queue_thumbs()
