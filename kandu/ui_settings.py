"""设置对话框：LLM 接口配置 / cc-switch 粘贴 / 连接测试 / 缓存管理。"""
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from kandu import core_config as cfgmod
from kandu import core_llm as llm
from kandu import core_parsers as store

FAMILY = "Microsoft YaHei UI"


class SettingsDialog(tk.Toplevel):
    def __init__(self, app: "App", on_saved=None):
        super().__init__(app)
        self.app = app
        self.on_saved = on_saved
        self.title("设置 · 大模型接口")
        self.geometry("640x640")
        self.transient(app)
        self.grab_set()

        self.q = queue.Queue()
        self.after(80, self._poll)

        s = app.settings
        box = ttk.Frame(self, padding=14)
        box.pack(fill="both", expand=True)

        ttk.Label(box, text="接口类型").grid(row=0, column=0, sticky="w")
        self.provider = tk.StringVar(value=s["llm"].get("provider", "openai"))
        ttk.Radiobutton(box, text="OpenAI 兼容（/chat/completions）", variable=self.provider, value="openai").grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(box, text="Anthropic 原生（/v1/messages）", variable=self.provider, value="anthropic").grid(row=1, column=1, sticky="w")

        def row(label, key, width=70):
            ttk.Label(box, text=label).grid(sticky="w", pady=(8, 0))
            var = tk.StringVar(value=s["llm"].get(key, ""))
            e = ttk.Entry(box, textvariable=var, width=width)
            e.grid(sticky="ew")
            return var

        self.base_url = row("API 地址 Base URL")
        self.api_key = row("API Key")
        self.model = row("模型名 Model")

        ttk.Label(box, text="从 cc-switch JSON 粘贴：").grid(sticky="w", pady=(12, 0))
        self.paste = tk.Text(box, height=5, font=(FAMILY, 9))
        self.paste.pack(fill="x")
        row2 = ttk.Frame(box)
        row2.pack(fill="x", pady=4)
        ttk.Button(row2, text="解析并填入", command=self._parse_paste).pack(side="left")
        ttk.Button(row2, text="测试连接", command=self._test).pack(side="left", padx=8)
        ttk.Button(row2, text="保存配置", command=self._save).pack(side="left", padx=8)
        self.test_label = ttk.Label(box, text="", wraplength=560)
        self.test_label.pack(anchor="w", pady=4)

        ttk.Separator(box).pack(fill="x", pady=10)
        info = ttk.Frame(box)
        info.pack(fill="x")
        ttk.Label(info, text=f"词汇级别：{gen_level_label(app.settings['vocabLevel'])}"
                             f"（词表 {cfgmod.word_count(app.settings['vocabLevel'])} 词，内置离线词库）").pack(anchor="w")
        try:
            size_mb = sum(f.stat().st_size for f in (cfgmod.app_dir() / "cache").rglob("*") if f.is_file()) / 1048576
        except Exception:
            size_mb = 0
        ttk.Label(info, text=f"缓存（正文/摘要/生词/句解）：{size_mb:.1f} MB").pack(anchor="w", pady=(4, 0))
        ttk.Button(info, text="清空全部缓存", command=self._purge).pack(anchor="w", pady=4)

        box.columnconfigure(1, weight=1)

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
                    self.test_label.config(text=f"连接失败：{payload['error']}", foreground="#b00")
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


def gen_level_label(level: str) -> str:
    from kandu import core_gen as gen
    return gen.level_label(level)
