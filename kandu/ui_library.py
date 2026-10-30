"""书库：应用自管的刊物收藏（添加/移除），不依赖磁盘目录。"""
import os
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

from kandu import core_config as cfgmod
from kandu import core_parsers as store

FAMILY = "Microsoft YaHei UI"
THUMB_W, THUMB_H = 128, 172
COLS = 5
PAPER = "#f7faf7"


class LibraryFrame(tk.Frame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, bg=PAPER)
        self.app = app
        self.metas = store.load_library()
        self._photo_cache = {}
        self._importing = False

        bar = tk.Frame(self, bg="#1b4b43")
        bar.pack(fill="x")
        tk.Label(bar, text="刊读 · 书库", bg="#1b4b43", fg="white",
                 font=(FAMILY, 13, "bold")).pack(side="left", padx=14, pady=8)
        ttk.Button(bar, text="添加刊物", command=self.add_files).pack(side="right", padx=(6, 12), pady=6)
        ttk.Button(bar, text="从文件夹导入", command=self.import_folder).pack(side="right", padx=6, pady=6)
        ttk.Button(bar, text="设置", command=self.app.open_settings).pack(side="right", padx=6, pady=6)

        self.status = tk.Label(self, text="", bg=PAPER, fg="#446", anchor="w")
        self.status.pack(fill="x", padx=14, pady=(6, 0))
        self.index_btn = ttk.Button(bar, text="建立索引", command=self.app.start_indexing)
        self.index_btn.pack(side="right", padx=6, pady=6)
        self.digest_btn = ttk.Button(bar, text="生成全部摘要", command=self.app.start_batch_digest)
        self.digest_btn.pack(side="right", padx=6, pady=6)
        self.after(400, self._tick_index_status)

        self.canvas = tk.Canvas(self, bg=PAPER, highlightthickness=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=PAPER)
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw", width=self._inner_width())
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.vbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure("all", width=self._inner_width()))

        if self.metas:
            self.status.config(text=f"书库共 {len(self.metas)} 本 · 双击打开，右键管理 · 点「添加刊物」扩充")
            self._render()
            self._maybe_migrate()
        elif self.app.settings.get("libraryDir") and not self.app.settings.get("migratedLibrary"):
            # 旧版本用户：从原目录自动迁移进书库
            self._maybe_migrate()
        else:
            self.status.config(text="书库还是空的。点右上角「添加刊物」或「从文件夹导入」开始")

    def _inner_width(self):
        return max(self.canvas.winfo_width(), 900)

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
        for w in self.inner.winfo_children():
            w.destroy()
        self._photo_cache.clear()
        row = 0
        # 最近阅读（最多 6 本，小卡片）
        by_id = {m["id"]: m for m in self.metas}
        recent = [by_id[i] for i in (self.app.settings.get("recentBooks") or []) if i in by_id][:6]
        if recent:
            tk.Label(self.inner, text="最近阅读", bg=PAPER, fg="#1b4b43",
                     font=(FAMILY, 11, "bold")).grid(row=row, column=0, sticky="w", padx=6)
            row += 1
            for i, meta in enumerate(recent):
                mini = tk.Frame(self.inner, bg="white", bd=1, relief="solid")
                mini.grid(row=row, column=i, padx=8, pady=(4, 10), sticky="n")
                img = tk.Label(mini, bg="#dde8e4", width=88, height=118)
                img.pack(padx=4, pady=(4, 2))
                self._load_thumb(img, meta, 88)
                tk.Label(mini, text=meta["fileName"][:16], bg="white", fg="#333",
                         font=(FAMILY, 8), wraplength=92).pack(padx=2, pady=(0, 4))
                for w in (mini, img):
                    w.bind("<Double-Button-1>", lambda e, m=meta: self.app.open_digest(m))
            row += 1
            tk.Label(self.inner, text="全部刊物", bg=PAPER, fg="#1b4b43",
                     font=(FAMILY, 11, "bold")).grid(row=row, column=0, sticky="w", padx=6)
            row += 1
        for i, meta in enumerate(self.metas):
            card = tk.Frame(self.inner, bg="white", bd=1, relief="solid")
            card.grid(row=row + i // COLS, column=i % COLS, padx=10, pady=10, sticky="n")
            img_label = tk.Label(card, bg="#dde8e4", width=THUMB_W, height=THUMB_H)
            img_label.pack(padx=6, pady=(6, 2))
            self._load_thumb(img_label, meta)
            has_d = store.has_digest(meta["id"])
            name_label = tk.Label(card, text=meta["fileName"][:40], bg="white", fg="#222",
                                  font=(FAMILY, 9), wraplength=THUMB_W + 20)
            name_label.pack(padx=4, pady=(0, 2))
            state_label = tk.Label(card, text="已生成摘要 ✓" if has_d else "未生成摘要", bg="white",
                                   fg="#7fa829" if has_d else "#b0b8b6", font=(FAMILY, 8))
            state_label.pack(pady=(0, 6))
            for w in (card, img_label, name_label, state_label):
                w.bind("<Double-Button-1>", lambda e, m=meta: self.app.open_digest(m))
                w.bind("<Button-1>", lambda e, m=meta: self._hint(m))
                w.bind("<Button-3>", lambda e, m=meta: self._card_menu(e, m))

    def _alive(self):
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def _hint(self, meta):
        if self._alive():
            self.status.config(text=f"双击打开：{meta['fileName']}　右键卡片可管理")

    def _card_menu(self, event, meta):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="打开", command=lambda: self.app.open_digest(meta))
        menu.add_command(label="清除本书摘要", command=lambda: self._purge_one(meta, digest_only=True))
        menu.add_separator()
        menu.add_command(label="移出书库（删除文件与缓存）", command=lambda: self._remove_book(meta))
        try:
            menu.tk_popup(event.x_root, event.y_root)
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
                "会删除：书库中的文件副本、摘要、生词表与句解缓存。\n"
                "不影响你磁盘上其他位置的文件。",
                parent=self):
            return
        store.remove_book(meta["id"])
        if self._alive():
            self.metas = store.load_library()
            self.status.config(text=f"已移出：{meta['fileName']}")
            self._render()

    def _load_thumb(self, label, meta, w=THUMB_W):
        h = int(w * THUMB_H / THUMB_W)
        path = store.cover_file(meta["id"])
        try:
            if not path.exists():
                raise FileNotFoundError
            img = Image.open(path)
        except Exception:
            label.config(text="无封面", fg="#778")
            return
        img.thumbnail((w, h))
        photo = ImageTk.PhotoImage(img)
        self._photo_cache[f"{meta['id']}_{w}"] = photo
        label.config(image=photo, width=w, height=h, bg="white")

    def on_error(self, msg):
        self._importing = False
        messagebox.showerror("操作失败", str(msg), parent=self)

    # ---------- 后台索引进度 ----------
    def _tick_index_status(self):
        if not self._alive():
            return
        st = self.app.index_state
        bt = self.app.batch_state
        try:
            if bt["running"]:
                self.status.config(text=f"批量生成摘要中… {bt['done']}/{bt['total']}　{bt['current']}")
                self.index_btn.state(["disabled"])
                self.digest_btn.state(["disabled"])
            elif st["running"]:
                self.status.config(text=f"后台索引中… {st['done']}/{st['total']}　{st['current']}")
                self.index_btn.state(["disabled"])
                self.digest_btn.state(["disabled"])
            else:
                self.index_btn.state(["!disabled"])
                self.digest_btn.state(["!disabled"])
                if bt["done"] and not bt.get("reported"):
                    bt["reported"] = True
                    msg = f"批量摘要完成：{bt['total']} 本" + (f"，{bt['failed']} 本有失败批次（可再跑续补）" if bt["failed"] else "")
                    self.status.config(text=msg)
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
        except tk.TclError:
            pass
        self.after(400, self._tick_index_status)
