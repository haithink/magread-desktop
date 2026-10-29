# -*- coding: utf-8 -*-
"""设置对话框：多 LLM 配置管理（新增/编辑/删除/设默认）+ 词汇级别 + 缓存管理。"""
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


def gen_level_label(level: str) -> str:
    return gen.level_label(level)


class SettingsDialog(tk.Toplevel):
    def __init__(self, app, on_saved=None):
        super().__init__(app)
        self.app = app
        self.on_saved = on_saved
        self.title("设置")
        self.geometry("680x720")
        self.transient(app)
        self.grab_set()

        box = ttk.Frame(self, padding=14)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        r = 0

        # ---- LLM 配置管理 ----
        ttk.Label(box, text="大模型配置（可配置多个，失败时可换用其他）").grid(
            row=r, column=0, columnspan=2, sticky="w")
        r += 1
        list_frame = ttk.Frame(box)
        list_frame.grid(row=r, column=0, columnspan=2, sticky="ew")
        self.llm_list = tk.Listbox(list_frame, height=5, font=(FAMILY, 10), selectmode="browse")
        lb_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.llm_list.yview)
        self.llm_list.configure(yscrollcommand=lb_scroll.set)
        self.llm_list.pack(side="left", fill="both", expand=True)
        lb_scroll.pack(side="right", fill="y")
        r += 1

        btn_row = ttk.Frame(box)
        btn_row.grid(row=r, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Button(btn_row, text="新增", command=self._add_llm).pack(side="left")
        ttk.Button(btn_row, text="编辑", command=self._edit_llm).pack(side="left", padx=6)
        ttk.Button(btn_row, text="删除", command=self._del_llm).pack(side="left", padx=6)
        ttk.Button(btn_row, text="设为默认 ★", command=self._set_default).pack(side="left", padx=6)
        r += 1
        self.llm_hint = ttk.Label(box, text="", foreground="#5c7f10", wraplength=600)
        self.llm_hint.grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1

        ttk.Separator(box).grid(row=r, column=0, columnspan=2, sticky="ew", pady=10)
        r += 1

        ttk.Label(box, text="词汇级别").grid(row=r, column=0, sticky="w")
        ttk.Label(box, text=f"{gen_level_label(app.settings['vocabLevel'])}"
                            f"（词表 {cfgmod.word_count(app.settings['vocabLevel'])} 词，内置离线词库）").grid(row=r, column=1, sticky="w")
        r += 1

        ttk.Label(box, text="缓存").grid(row=r, column=0, sticky="w")
        try:
            size_mb = sum(f.stat().st_size for f in (cfgmod.app_dir() / "cache").rglob("*") if f.is_file()) / 1048576
        except Exception:
            size_mb = 0
        ttk.Label(box, text=f"正文/摘要/生词/句解缓存 {size_mb:.1f} MB").grid(row=r, column=1, sticky="w")
        r += 1
        purge_row = ttk.Frame(box)
        purge_row.grid(row=r, column=1, sticky="w", pady=6)
        ttk.Button(purge_row, text="清空摘要缓存", command=self._purge_digest).pack(side="left")
        ttk.Button(purge_row, text="清空全部缓存", command=self._purge).pack(side="left", padx=8)
        r += 1

        ttk.Label(box, text="版本").grid(row=r, column=0, sticky="w")
        ttk.Label(box, text=f"v{__version__}").grid(row=r, column=1, sticky="w")

        self._refresh_llm_list()

    # ---- LLM 列表管理 ----
    def _refresh_llm_list(self):
        self.llm_list.delete(0, "end")
        dn = cfgmod.default_llm_name(self.app.settings)
        for p in self.app.settings.get("llms") or []:
            star = "★ " if p.get("name") == dn else "   "
            self.llm_list.insert("end", f"{star}{p.get('name')}  —  {p.get('provider')} / {p.get('model')}")
        n = len(self.app.settings.get("llms") or [])
        self.llm_hint.config(
            text=f"共 {n} 个配置。生成时优先使用 ★ 默认；失败时可选择其他配置重试。"
                 if n > 1 else "当前 1 个配置（或未配置）。可新增多个，失败时换用。")

    def _selected_index(self):
        sel = self.llm_list.curselection()
        return sel[0] if sel else None

    def _add_llm(self):
        LlmProfileDialog(self, initial=None, on_save=self._save_profile)

    def _edit_llm(self):
        idx = self._selected_index()
        llms = self.app.settings.get("llms") or []
        if idx is None or idx >= len(llms):
            messagebox.showinfo("提示", "先在列表中选择一个配置", parent=self)
            return
        LlmProfileDialog(self, initial=dict(llms[idx]), on_save=lambda cfg, i=idx: self._save_profile(cfg, i))

    def _save_profile(self, cfg, index=None):
        llms = self.app.settings.setdefault("llms", [])
        if index is None:
            llms.append(cfg)
        else:
            old_name = llms[index].get("name")
            llms[index] = cfg
            if old_name == self.app.settings.get("defaultLlm"):
                self.app.settings["defaultLlm"] = cfg["name"]
        if len(llms) == 1 or not self.app.settings.get("defaultLlm"):
            self.app.settings["defaultLlm"] = llms[0].get("name", "")
        self.app.save_settings()
        self._refresh_llm_list()
        if self.on_saved:
            self.on_saved()

    def _del_llm(self):
        idx = self._selected_index()
        llms = self.app.settings.get("llms") or []
        if idx is None or idx >= len(llms):
            messagebox.showinfo("提示", "先在列表中选择一个配置", parent=self)
            return
        name = llms[idx].get("name")
        if not messagebox.askyesno("删除", f"删除配置「{name}」？", parent=self):
            return
        llms.pop(idx)
        if self.app.settings.get("defaultLlm") == name:
            self.app.settings["defaultLlm"] = llms[0].get("name", "") if llms else ""
        self.app.save_settings()
        self._refresh_llm_list()

    def _set_default(self):
        idx = self._selected_index()
        llms = self.app.settings.get("llms") or []
        if idx is None or idx >= len(llms):
            messagebox.showinfo("提示", "先在列表中选择一个配置", parent=self)
            return
        self.app.settings["defaultLlm"] = llms[idx].get("name", "")
        self.app.save_settings()
        self._refresh_llm_list()

    def _purge(self):
        if messagebox.askyesno("清空全部缓存", "将删除所有正文/摘要/生词/句解缓存，设置不会丢失。继续？", parent=self):
            store.purge_all()
            messagebox.showinfo("完成", "缓存已清空。", parent=self)

    def _purge_digest(self):
        if messagebox.askyesno("清空摘要缓存", "将删除所有书目的摘要缓存（生词表与句解保留），下次打开会重新生成摘要。继续？", parent=self):
            n = store.purge_digests()
            messagebox.showinfo("完成", f"已清除 {n} 本书的摘要缓存。", parent=self)


class LlmProfileDialog(tk.Toplevel):
    """单个 LLM 配置的编辑/新增对话框。"""

    def __init__(self, parent, initial=None, on_save=None):
        super().__init__(parent)
        self.app = parent.app if hasattr(parent, "app") else parent
        self.on_save = on_save
        self.title("编辑 LLM 配置" if initial else "新增 LLM 配置")
        self.geometry("640x660")
        self.transient(parent)
        self.grab_set()

        self.q = queue.Queue()
        self.after(80, self._poll)

        s = self.app.settings
        box = ttk.Frame(self, padding=14)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        r = 0

        g = lambda w, row, col=1, sticky="ew", **kw: w.grid(row=row, column=col, sticky=sticky, **kw)

        g(ttk.Label(box, text="配置名称"), r, 0, sticky="w")
        self.name = tk.StringVar(value=(initial or {}).get("name") or f"LLM{len(s.get('llms') or []) + 1}")
        g(ttk.Entry(box, textvariable=self.name), r)
        r += 1

        g(ttk.Label(box, text="接口类型"), r, 0, sticky="w")
        self.provider = tk.StringVar(value=(initial or {}).get("provider", "openai"))
        g(ttk.Radiobutton(box, text="OpenAI 兼容（/chat/completions）", variable=self.provider, value="openai"), r, 1, sticky="w")
        r += 1
        g(ttk.Radiobutton(box, text="Anthropic 原生（/v1/messages）", variable=self.provider, value="anthropic"), r, 1, sticky="w")
        r += 1

        def field_row(label, key, show=None):
            nonlocal r
            g(ttk.Label(box, text=label), r, 0, sticky="w", pady=(8, 0))
            var = tk.StringVar(value=(initial or {}).get(key, ""))
            e = ttk.Entry(box, textvariable=var, show=show)
            g(e, r, pady=(8, 0))
            r += 1
            return var

        self.base_url = field_row("API 地址 Base URL", "baseUrl")
        self.api_key = field_row("API Key", "apiKey")
        self.model = field_row("模型名 Model", "model")

        g(ttk.Label(box, text="从 cc-switch JSON 粘贴："), r, 0, sticky="nw", pady=(12, 0))
        paste_frame = ttk.Frame(box)
        g(paste_frame, r, pady=(12, 0))
        self.paste = tk.Text(paste_frame, height=5, font=(FAMILY, 9))
        self.paste.pack(fill="x")
        r += 1

        btns = ttk.Frame(box)
        g(btns, r, sticky="w", pady=6)
        ttk.Button(btns, text="解析并填入", command=self._parse_paste).pack(side="left")
        ttk.Button(btns, text="获取可用模型", command=self._fetch_models).pack(side="left", padx=8)
        ttk.Button(btns, text="测试连接", command=self._test).pack(side="left", padx=8)
        ttk.Button(btns, text="保存此配置", command=self._save).pack(side="left", padx=8)
        r += 1

        self.test_label = ttk.Label(box, text="", wraplength=520)
        g(self.test_label, r, sticky="w")

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
            "name": self.name.get().strip() or "LLM",
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
        self.test_label.config(text=f"该 Key 可用 {len(ids)} 个模型，双击名称填入", foreground="#446")
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
        if self.on_save:
            self.on_save(cfg)
        self.destroy()
