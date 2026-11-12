"""文章 AI 讨论：独立非模态窗口、流式展示和持久会话。"""
import copy
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import core_config as cfgmod
from . import core_discussion as discussion
from . import core_llm as llm

FAMILY = "Microsoft YaHei UI"
BG, PAPER, GREEN, INK, MUTED = "#f3f4ef", "#fffdf8", "#1b4b43", "#2d3e36", "#738079"


def show_discussion(app, meta, article, quote="", paragraph=None):
    windows = getattr(app, "_discussion_windows", None)
    if windows is None:
        windows = app._discussion_windows = {}
    key = discussion.article_key(meta, article)
    win = windows.get(key)
    if win is None or not win.winfo_exists():
        win = windows[key] = DiscussionWindow(app, meta, article)
    win.set_context(quote, paragraph)
    win.deiconify()
    win.lift()
    win.prompt.focus_set()
    return win


class DiscussionWindow(tk.Toplevel):
    def __init__(self, app, meta, article):
        super().__init__(app)
        self.app, self.meta, self.article = app, dict(meta), copy.deepcopy(article)
        self.messages = []
        self.quote, self.paragraph = "", None
        self.loaded = self.busy = self._closed = False
        self._request_id = 0
        self._control = None
        self._queue = queue.Queue()
        self._started = self._last_save = 0
        self._excerpted = False
        self.title("与 AI 讨论 — " + article["title"][:75])
        self.geometry("920x790")
        self.minsize(620, 540)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        brand = tk.Frame(self, bg=GREEN, padx=22, pady=12)
        brand.pack(fill="x")
        tk.Label(brand, text="刊读  /  ARTICLE DISCUSSION", bg=GREEN, fg="#dce9df",
                 font=(FAMILY, 9)).pack(side="left")
        heading = tk.Frame(self, bg=BG, padx=22, pady=12)
        heading.pack(fill="x")
        heading.columnconfigure(0, weight=1)
        tk.Label(heading, text="与 AI 讨论", bg=BG, fg=GREEN, font=(FAMILY, 18, "bold"),
                 anchor="w").grid(row=0, column=0, sticky="ew")
        self.model = ttk.Combobox(heading, values=cfgmod.llm_names(app.settings), state="readonly", width=19)
        self.model.set(cfgmod.default_llm_name(app.settings))
        self.model.grid(row=0, column=1, padx=(12, 0))
        self._button(heading, "模型设置", self._settings).grid(row=0, column=2, padx=(8, 0))
        self.article_label = tk.Label(heading, text=article["title"], bg=BG, fg=MUTED,
                                     font=(FAMILY, 9), anchor="w", justify="left", wraplength=800)
        self.article_label.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(7, 0))
        heading.bind("<Configure>", lambda e: self.article_label.configure(wraplength=max(300, e.width - 44)))
        composer = tk.Frame(self, bg=BG)
        composer.pack(side="bottom", fill="x")
        page = tk.Frame(self, bg=PAPER, highlightbackground="#dce3d7", highlightthickness=1)
        page.pack(fill="both", expand=True, padx=22)
        page.rowconfigure(0, weight=1)
        page.columnconfigure(0, weight=1)
        self.transcript = tk.Text(page, bg=PAPER, fg=INK, wrap="word", bd=0, highlightthickness=0,
                                  padx=20, pady=14, font=(FAMILY, 11), spacing2=5, spacing3=9,
                                  state="disabled", height=8, width=1, selectbackground="#dbe9db")
        self.transcript.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(page, command=self.transcript.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.transcript.configure(yscrollcommand=scrollbar.set)
        for tag, options in {
            "role": dict(foreground=GREEN, font=(FAMILY, 10, "bold"), spacing1=16),
            "quote": dict(foreground=MUTED, font=("Georgia", 10), lmargin1=12, lmargin2=12),
            "muted": dict(foreground=MUTED, font=(FAMILY, 9)),
            "error": dict(foreground="#99603b", font=(FAMILY, 10)),
        }.items():
            self.transcript.tag_configure(tag, **options)
        context = tk.Frame(composer, bg=BG, padx=22, pady=7)
        context.pack(fill="x")
        context.columnconfigure(0, weight=1)
        self.quote_label = tk.Label(context, text="围绕文章提问，也可在正文选中文字后右键带入引用。", bg=BG,
                                    fg=MUTED, anchor="w", justify="left", font=(FAMILY, 9), wraplength=720)
        self.quote_label.grid(row=0, column=0, sticky="ew")
        self.clear_quote_btn = self._button(context, "取消引用", lambda: self.set_context("", None))
        self.clear_quote_btn.grid(row=0, column=1, padx=(8, 0))
        context.bind("<Configure>", lambda e: self.quote_label.configure(wraplength=max(250, e.width - 150)))
        editor = tk.Frame(composer, bg=PAPER, highlightbackground="#dce3d7", highlightthickness=1)
        editor.pack(fill="x", padx=22)
        self.prompt = tk.Text(editor, height=4, width=1, wrap="word", bd=0, highlightthickness=0, bg=PAPER,
                              fg=INK, font=(FAMILY, 11), padx=12, pady=9, undo=True)
        self.prompt.pack(fill="both", expand=True)
        self.prompt.bind("<Control-Return>", self._keyboard_send)
        footer = tk.Frame(composer, bg=BG, padx=22, pady=10)
        footer.pack(fill="x")
        footer.columnconfigure(0, weight=1)
        self.status = tk.Label(footer, text="正在加载本篇会话…", bg=BG, fg=MUTED, anchor="w",
                               justify="left", width=1, wraplength=800, font=(FAMILY, 9))
        self.status.grid(row=0, column=0, columnspan=5, sticky="ew")
        footer.bind("<Configure>", lambda e: self.status.configure(wraplength=max(250, e.width - 44)))
        self.copy_btn = self._button(footer, "复制回答", self._copy)
        self.copy_btn.grid(row=1, column=1, padx=(6, 0), pady=(6, 0))
        self.retry_btn = self._button(footer, "重试", lambda: self.send(retry=True))
        self.retry_btn.grid(row=1, column=2, padx=(6, 0), pady=(6, 0))
        self.stop_btn = self._button(footer, "停止", self.stop)
        self.stop_btn.grid(row=1, column=3, padx=(6, 0), pady=(6, 0))
        self.send_btn = self._button(footer, "发送 ↗", self.send, primary=True)
        self.send_btn.grid(row=1, column=4, padx=(8, 0), pady=(6, 0))
        tk.Label(composer, text="Ctrl+Enter 发送 · Enter 换行 · 本篇会话自动保存", bg=BG,
                 fg=MUTED, font=(FAMILY, 9)).pack(anchor="w", padx=22, pady=(0, 12))
        self._buttons()
        self._poll_after = self.after(80, self._poll)
        threading.Thread(target=self._load, daemon=True).start()

    @staticmethod
    def _button(parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg=GREEN if primary else "#e5ebe2",
                         fg="white" if primary else GREEN, activebackground="#d7e3d4", relief="flat", bd=0,
                         highlightthickness=0, cursor="hand2", font=(FAMILY, 9), padx=10, pady=6)

    def _load(self):
        try:
            self._queue.put((0, "loaded", discussion.load(self.meta, self.article)))
        except Exception as error:
            self._queue.put((0, "load_error", str(error)))

    def set_context(self, quote, paragraph):
        self.quote, self.paragraph = (quote[:5980] + " [引用后文省略]" if len(quote) > 6000 else quote), paragraph
        label = f"第 {paragraph + 1} 段" if isinstance(paragraph, int) and paragraph >= 0 else "选中文字"
        self.quote_label.configure(text=(f"引用 · {label}：" + self.quote[:180].replace("\n", " ")
                                        + ("…" if len(self.quote) > 180 else "")) if self.quote else
                                   "围绕文章提问，也可在正文选中文字后右键带入引用。")
        self.clear_quote_btn.configure(state="normal" if self.quote else "disabled")

    def _keyboard_send(self, _event=None):
        self.send()
        return "break"

    def _settings(self):
        if hasattr(self.app, "open_settings"):
            self.app.open_settings(on_saved=self._refresh_models)

    def _refresh_models(self):
        if self._closed:
            return
        names = cfgmod.llm_names(self.app.settings)
        self.model.configure(values=names)
        if self.model.get() not in names:
            self.model.set(cfgmod.default_llm_name(self.app.settings))

    def _buttons(self):
        self.send_btn.configure(state="normal" if self.loaded and not self.busy else "disabled")
        self.stop_btn.configure(state="normal" if self.busy else "disabled")
        retryable = bool(self.messages and self.messages[-1]["role"] == "assistant"
                         and self.messages[-1]["status"] in {"stopped", "error"})
        self.retry_btn.configure(state="normal" if self.loaded and not self.busy and retryable else "disabled")
        self.model.configure(state="disabled" if self.busy else "readonly")

    def _save(self):
        try:
            discussion.save(self.meta, self.article, self.messages)
            self._last_save = time.monotonic()
            return True
        except Exception as error:
            self.status.configure(text=f"会话保存失败：{error}")
            return False

    def send(self, retry=False):
        if self.busy or not self.loaded:
            return
        self._refresh_models()
        cfg = cfgmod.get_llm(self.app.settings, self.model.get())
        if not llm.is_complete(cfg):
            self.status.configure(text="请先在设置中配置可用模型，再重新打开讨论窗口。")
            return
        if retry:
            if len(self.messages) < 2 or self.messages[-1]["status"] not in {"error", "stopped"}:
                return
            user = self.messages[-2]
            prior = self.messages[:-2]
        else:
            question = self.prompt.get("1.0", "end-1c").strip()
            if not question:
                self.prompt.focus_set()
                return
            if len(question) > 8000:
                self.status.configure(text="问题超过 8000 字符，请分次提问。")
                return
            user = dict(role="user", content=question, quote=self.quote, paragraph=self.paragraph,
                        status="complete", createdAt=time.time())
            prior = list(self.messages)
        old = self.messages
        self.messages = prior + [user, dict(role="assistant", content="", status="pending", createdAt=time.time(),
                                           model=cfg.get("model", ""))]
        if not self._save():
            self.messages = old
            return
        if not retry:
            self.prompt.delete("1.0", "end")
        self.busy = True
        self._started = time.monotonic()
        self._excerpted = False
        self._control = llm.RequestControl()
        self._request_id += 1
        self._render()
        self._buttons()
        threading.Thread(target=self._answer, args=(self._request_id, dict(cfg), prior, user, self._control),
                         daemon=True).start()

    def _answer(self, request_id, cfg, prior, user, control):
        try:
            system, messages, excerpted = discussion.build_request(
                self.meta, self.article, prior, user["content"], user.get("quote", ""), user.get("paragraph"))
            control.check()
            self._queue.put((request_id, "context", excerpted))
            answer = llm.conversation(cfg, system, messages, control=control,
                                      on_text=lambda text: self._queue.put((request_id, "text", text)))
            self._queue.put((request_id, "done", answer))
        except Exception as error:
            message = str(error).replace(cfg.get("apiKey") or "\x00", "[redacted]")
            self._queue.put((request_id, "error", message))

    def _poll(self):
        if self._closed:
            return
        changed = False
        loaded = False
        while True:
            try:
                request_id, event, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if event == "loaded":
                self.messages, self.loaded = payload, True
                self.status.configure(text="可继续本篇讨论。" if payload else "可以询问文章观点、背景或难句。")
                changed = True
                loaded = True
            elif event == "load_error":
                self.status.configure(text="会话读取失败，原文件未覆盖：" + payload)
            elif request_id == self._request_id and self.busy:
                if event == "context":
                    self._excerpted = payload
                elif event == "text":
                    self.messages[-1]["content"] = payload
                    changed = True
                elif event in {"done", "error"}:
                    assistant = self.messages[-1]
                    assistant["status"] = "complete" if event == "done" else "error"
                    assistant["content" if event == "done" else "error"] = payload
                    self.busy = False
                    self.status.configure(text=(f"回答完成 · {round(time.monotonic() - self._started, 1)} 秒 · 已保存"
                                                if event == "done" else "回答未完成，可重试；已有内容保留。"))
                    self._save()
                    changed = True
        if self.busy:
            chars = len(self.messages[-1]["content"])
            self.status.configure(text=(f"正在接收 · {chars} 字符" if chars else "等待模型回答")
                                  + f" · {int(time.monotonic() - self._started)} 秒"
                                  + (" · 使用文章节选" if self._excerpted else ""))
            if chars and time.monotonic() - self._last_save > 1.5:
                self._save()
        if changed:
            self._render() if loaded else self._update_answer()
            self._buttons()
        self._poll_after = self.after(80, self._poll)

    def _render(self):
        txt = self.transcript
        bottom = txt.yview()[1] >= .98
        position = txt.yview()[0]
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        if not self.messages:
            txt.insert("end", "从文章出发，把不明白的地方聊清楚。\n", "role")
            txt.insert("end", "例如：作者的核心论点是什么？这段话为什么这样说？这个表达在这里是什么意思？\n", "muted")
        for message in self.messages:
            role = "你" if message["role"] == "user" else "AI"
            txt.insert("end", role + ("  ·  " + message.get("model", "") if role == "AI" else "") + "\n", "role")
            if message.get("quote"):
                txt.insert("end", "引用：" + message["quote"][:240] + ("…" if len(message["quote"]) > 240 else "") + "\n", "quote")
            if message is self.messages[-1] and message["role"] == "assistant":
                txt.mark_set("answer_start", "end-1c")
                txt.mark_gravity("answer_start", "left")
            txt.insert("end", message["content"] + "\n")
            if message["status"] == "pending" and not message["content"]:
                txt.insert("end", "正在等待回答…\n", "muted")
            elif message["status"] == "stopped":
                txt.insert("end", "已停止 · 可点击重试\n", "muted")
            elif message["status"] == "error":
                txt.insert("end", message.get("error", "回答未完成") + "\n", "error")
        txt.configure(state="disabled")
        txt.see("end") if bottom else txt.yview_moveto(position)

    def _update_answer(self):
        # 流式只替换最后一条回答，不重新排版整段会话，保留读者查看历史的位置。
        txt = self.transcript
        if not self.messages or "answer_start" not in txt.mark_names():
            self._render()
            return
        bottom, position = txt.yview()[1] >= .98, txt.yview()[0]
        txt.configure(state="normal")
        txt.delete("answer_start", "end-1c")
        message = self.messages[-1]
        txt.insert("end", message["content"] + "\n")
        if message["status"] == "pending" and not message["content"]:
            txt.insert("end", "正在等待回答…\n", "muted")
        elif message["status"] == "stopped":
            txt.insert("end", "已停止 · 可点击重试\n", "muted")
        elif message["status"] == "error":
            txt.insert("end", message.get("error", "回答未完成") + "\n", "error")
        txt.configure(state="disabled")
        txt.see("end") if bottom else txt.yview_moveto(position)

    def stop(self):
        if not self.busy:
            return
        self._control.cancel()
        # 停止时收下尚未由 UI 轮询显示的正文片段，再忽略晚到的旧任务结果。
        while True:
            try:
                request_id, event, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if request_id == self._request_id and event in {"text", "done"}:
                self.messages[-1]["content"] = payload
        self._request_id += 1
        self.busy = False
        self.messages[-1]["status"] = "stopped"
        self.status.configure(text="已停止，收到的内容已保留。")
        self._save()
        self._update_answer()
        self._buttons()

    def _copy(self):
        answer = next((m["content"] for m in reversed(self.messages) if m["role"] == "assistant" and m["content"]), "")
        if answer:
            self.clipboard_clear()
            self.clipboard_append(answer)
            self.status.configure(text="已复制最近一次回答。")

    def destroy(self):
        if self._closed:
            return
        self.stop()
        if self.loaded:
            self._save()
        self._closed = True
        if self._poll_after is not None:
            self.after_cancel(self._poll_after)
        windows = getattr(self.app, "_discussion_windows", {})
        key = discussion.article_key(self.meta, self.article)
        if windows.get(key) is self:
            windows.pop(key)
        super().destroy()
