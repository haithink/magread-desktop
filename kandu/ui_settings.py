"""设置对话框：LLM 接口配置 / cc-switch 粘贴 / 连接测试 / 缓存管理。
全部子控件用显式行列 grid，避免几何管理器混用。"""
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from kandu import core_config as cfgmod
from kandu import core_llm as llm
from kandu import core_parsers as store
from kandu import core_gen as gen
from kandu import __version__

FAMILY = "Microsoft YaHei UI"


class SettingsDialog(tk.Toplevel):
    def __init__(self, app, on_saved=None):
        super().__init__(app)
        self.app = app
        self.on_saved = on_saved
        self.title("设置 · 大模型接口")
        self.geometry("680x700")
        self.transient(app)
        self.grab_set()

        self.q = queue.Queue()
        self.after(80, self._poll)

        s = app.settings
        box = ttk.Frame(self, padding=14)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        r = 0

        def g(w, row, col=1, sticky="ew", **kw):
            w.grid(row=row, column=col, sticky=sticky, **kw)

        # 接口类型
        g(ttk.Label(box, text="接口类型"), r, 0, sticky="w")
        self.provider = tk.StringVar(value=s["llm"].get("provider", "openai"))
        g(ttk.Radiobutton(box, text="OpenAI 兼容（/chat/completions）", variable=self.provider, value="openai"), r, 1, sticky="w")
        r += 1
        g(ttk.Radiobutton(box, text="Anthropic 原生（/v1/messages）", variable=self.provider, value="anthropic"), r, 1, sticky="w")
        r += 1

        def field_row(label, key, show=None):
            nonlocal r
            g(ttk.Label(box, text=label), r, 0, sticky="w", pady=(8, 0))
            var = tk.StringVar(value=s["llm"].get(key, ""))
            e = ttk.Entry(box, textvariable=var, show=show)
            g(e, r, 1, pady=(8, 0))
            r += 1
            return var

        self.base_url = field_row("API 地址 Base URL", "baseUrl")
        self.api_key = field_row("API Key", "apiKey")
        self.model = field_row("模型名 Model", "model")

        # cc-switch 粘贴
        g(ttk.Label(box, text="从 cc-switch JSON 粘贴："), r, 0, sticky="nw", pady=(12, 0))
        paste_frame = ttk.Frame(box)
        g(paste_frame, r, 1, pady=(12, 0))
        self.paste = tk.Text(paste_frame, height=5, font=(FAMILY, 9))
        self.paste.pack(fill="x")
        r += 1

        btns = ttk.Frame(box)
        g(btns, r, 1, sticky="w", pady=6)
        ttk.Button(btns, text="解析并填入", command=self._parse_paste).pack(side="left")
        ttk.Button(btns, text="获取可用模型", command=self._fetch_models).pack(side="left", padx=8)
        ttk.Button(btns, text="测试连接", command=self._test).pack(side="left", padx=8)
        ttk.Button(btns, text="保存配置", command=self._save).pack(side="left", padx=8)
        r += 1

        self.test_label = ttk.Label(box, text="", wraplength=520)
        g(self.test_label, r, 1, sticky="w")
        r += 1

        ttk.Separator(box).grid(row=r, column=0, columnspan=2, sticky="ew", pady=12)
        r += 1

        ttk.Label(box, text="词汇级别").grid(row=r, column=0, sticky="w")
        ttk.Label(box, text=f"{gen_level_label(app.settings['vocabLevel'])}"
                            f"（词表 {cfgmod.word_count(app.settings['vocabLevel'])} 词，内置离线词库）").grid(row=r, column=1, sticky="w")
        r += 1

        try:
            size_mb = sum(f.stat().st_size for f in (cfgmod.app_dir() / "cache").rglob("*") if f.is_file()) / 1048576
        except Exception:
            size_mb = 0
        ttk.Label(box, text="缓存").grid(row=r, column=0, sticky="w")
        ttk.Label(box, text=f"正文/摘要/生词/句解缓存 {size_mb:.1f} MB").grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(box, text="版本").grid(row=r, column=0, sticky="w")
        ttk.Label(box, text=f"v{__version__}").grid(row=r, column=1, sticky="w")
        r += 1
        purge_row = ttk.Frame(box)
        purge_row.grid(row=r, column=1, sticky="w", pady=6)
        ttk.Button(purge_row, text="清空摘要缓存", command=self._purge_digest).pack(side="left")
        ttk.Button(purge_row, text="清空全部缓存", command=self._purge).pack(side="left", padx=8)

    def _parse_paste(self):
        text = self.paste.get("1.0", "end").strip()
        parsed = llm.parse_pasted_json(text)
        if not parsed:
            self.test_label.config(text="无法解析：不是有效的 JSON 或缺少 URL 字段", foreground="#b00")
            return
        cfg, note = parsed
        self.provider.set(cfg["provider"])
        self.base_url.set(cfg["baseUrl"])
        self.api_key.set(cfg["apiKey"])
        self.model.set(cfg["model"])
        self.test_label.config(text=note, foreground="#5c7f10")

    def _current_cfg(self):
        return {
            "provider": self.provider.get(),
            "baseUrl": self.base_url.get().strip(),
            "apiKey": self.api_key.get().strip(),
            "model": self.model.get().strip(),
        }

    def _fetch_models(self):
        cfg = self._current_cfg()
        invalid = llm.validate(cfg)
        if invalid:
            self.test_label.config(text=invalid, foreground="#b00")
            return
        self.test_label.config(text="获取模型列表中…", foreground="#446")
        threading.Thread(target=self._models_worker, args=(cfg,), daemon=True).start()

    def _models_worker(self, cfg):
        try:
            ids = llm.list_models(cfg)
            self.q.put(("models", ids))
        except Exception as e:
            self.q.put(("models_err", str(e)))

    def _show_models(self, ids):
        self.test_label.config(text=f"该 Key 可用 {len(ids)} 个模型，双击名称填入（如仍 404，说明此分组不含该模型）", foreground="#446")
        win = tk.Toplevel(self)
        win.title("可用模型（双击填入）")
        win.geometry("460x520")
        lb = tk.Listbox(win, font=(FAMILY, 10))
        vbar = ttk.Scrollbar(win, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=vbar.set)
        lb.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        vbar.pack(side="right", fill="y", pady=10)
        for m in ids:
            lb.insert("end", m)
        lb.bind("<Double-1>", lambda e: (self.model.set(lb.get(lb.curselection() or "")), win.destroy()))

    def _test(self):
        cfg = self._current_cfg()
        invalid = llm.validate(cfg)
        if invalid:
            self.test_label.config(text=invalid, foreground="#b00")
            return
        self.test_label.config(text="测试中…", foreground="#446")
        threading.Thread(target=self._test_worker, args=(cfg,), daemon=True).start()

    def _test_worker(self, cfg):
        result = llm.test(cfg)
        self.q.put(("test", result))

    def _poll(self):
        if not self.winfo_exists():
            return
        while True:
            try:
                kind, payload = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "test":
                if payload["ok"]:
                    self.test_label.config(
                        text=f"连接成功（{payload['latencyMs']} ms）：{payload['reply']}",
                        foreground="#5c7f10")
                else:
                    err = payload["error"]
                    hint = "（可点「获取可用模型」查看此 Key 支持的模型名）" if "model_not_found" in err or "404" in err else ""
                    self.test_label.config(text=f"连接失败：{err}{hint}", foreground="#b00")
            elif kind == "models":
                self._show_models(payload)
            elif kind == "models_err":
                self.test_label.config(text=f"获取模型列表失败：{payload}", foreground="#b00")
        self.after(80, self._poll)

    def _save(self):
        cfg = self._current_cfg()
        invalid = llm.validate(cfg)
        if invalid:
            self.test_label.config(text=invalid, foreground="#b00")
            return
        self.app.settings["llm"] = cfg
        self.app.save_settings()
        if self.on_saved:
            self.on_saved()
        messagebox.showinfo("已保存", "大模型配置已保存。", parent=self)
        self.destroy()

    def _purge(self):
        if messagebox.askyesno("清空全部缓存", "将删除所有正文/摘要/生词/句解缓存，设置不会丢失。继续？", parent=self):
            store.purge_all()
            messagebox.showinfo("完成", "缓存已清空。", parent=self)
            self.destroy()

    def _purge_digest(self):
        if messagebox.askyesno("清空摘要缓存", "将删除所有书目的摘要缓存（生词表与句解保留），下次打开会重新生成摘要。继续？", parent=self):
            n = store.purge_digests()
            messagebox.showinfo("完成", f"已清除 {n} 本书的摘要缓存。", parent=self)
            self.destroy()


def gen_level_label(level: str) -> str:
    return gen.level_label(level)
