"""阅读页：正文渲染、双击句解、长按/选中解析、全篇与逐段生词表。"""
import tkinter as tk
from tkinter import ttk, messagebox

from kandu import core_parsers as store
from kandu import core_llm as llm
from kandu import core_gen as gen
from kandu.core_config import app_dir

FAMILY = "Microsoft YaHei UI"


class ReaderFrame(tk.Frame):
    def __init__(self, parent, app, meta, article_index):
        super().__init__(parent, bg="white")
        self.app = app
        self.meta = meta
        self.article_index = article_index
        self.article = None
        self.vocab = None
        self.vocab_running = False
        self.analysis_busy = set()
        self._sheets = {}

        bar = tk.Frame(self, bg="#1b4b43")
        bar.pack(fill="x")
        ttk.Button(bar, text="← 返回", command=self.app.show_library).pack(side="left", padx=10, pady=6)
        self.title_label = tk.Label(bar, text="加载中…", bg="#1b4b43", fg="white",
                                    font=(FAMILY, 12, "bold"))
        self.title_label.pack(side="left", padx=6)
        self.vocab_btn = ttk.Button(bar, text="生成生词表", command=self._vocab_btn)
        self.vocab_btn.pack(side="right", padx=10, pady=6)
        ttk.Button(bar, text="A+", width=3, command=lambda: self._font(+1)).pack(side="right", padx=4, pady=6)
        ttk.Button(bar, text="A-", width=3, command=lambda: self._font(-1)).pack(side="right", padx=4, pady=6)

        self.status = tk.Label(self, text="", bg="white", fg="#446", anchor="w")
        self.status.pack(fill="x", padx=14, pady=(4, 0))

        body = tk.Frame(self, bg="white")
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", state="disabled", bg="white", bd=0,
                            padx=28, pady=16, cursor="arrow",
                            font=(FAMILY, int(self.app.settings.get("readerFontSize", 13))))
        vbar = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=vbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        self.text.tag_config("t_title", font=(FAMILY, 17, "bold"), spacing3=6)
        self.text.tag_config("t_hint", foreground="#8a9", font=(FAMILY, 9))
        self.text.tag_config("sel", background="#cde8e1")
        self.text.tag_config("t_badge", foreground="#7fa829", font=(FAMILY, 9, "underline"))
        self.text.bind("<Double-Button-1>", self._on_double_click)
        self.text.bind("<Button-3>", self._on_right_click)
        self.text.bind("<Return>", lambda e: self._on_double_click(e))

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="解析选中内容", command=self._analyze_selection)
        self.menu.add_command(label="解析整段", command=self._analyze_whole_paragraph)

        self.app.post(self._load_worker)

    # ---------- 数据加载 ----------
    def _load_worker(self):
        content = store.ensure_content(self.meta)
        self.app.emit("content_loaded", (content, self.article_index))

    def on_content_loaded(self, payload):
        content, article_index = payload
        art = next((a for a in content["articles"] if a["index"] == article_index), None)
        if art is None:
            self.status.config(text=f"找不到文章 #{article_index}")
            return
        self.article = art
        self.title_label.config(text=f"{art['title'][:40]}  ·  {self.meta['fileName']}")
        cached = store.load_vocab(self.meta["id"], article_index, self.app.settings["vocabLevel"])
        if cached:
            self.vocab = gen.vocab_normalize(cached, art)
        self._rebuild(keep_scroll=False)

    # ---------- 渲染 ----------
    def _font_size(self):
        return int(self.app.settings.get("readerFontSize", 13))

    def _font(self, delta):
        size = max(11, min(26, self._font_size() + delta))
        self.app.settings["readerFontSize"] = size
        self.app.save_settings()
        self.text.configure(font=(FAMILY, size))
        self._rebuild(keep_scroll=True)

    def _total_words(self):
        if not self.vocab:
            return 0
        return len(self.vocab.get("titleEntries", [])) + sum(
            len(p.get("entries", [])) for p in self.vocab.get("paragraphs", [])
        )

    def _entries_for(self, idx):
        if not self.vocab:
            return []
        for p in self.vocab.get("paragraphs", []):
            if p.get("index") == idx:
                return p.get("entries", [])
        return []

    def _rebuild(self, keep_scroll=True):
        if self.article is None:
            return
        frac = self.text.yview()[0] if keep_scroll else 0.0
        size = self._font_size()
        self.text.configure(state="normal", font=(FAMILY, size))
        self.text.delete("1.0", "end")

        self.text.tag_config("t_title", font=(FAMILY, size + 4, "bold"),
                             foreground="#1b4b43", spacing1=6, spacing3=10)
        self.text.tag_config("t_body", spacing1=2, spacing2=6, spacing3=14)
        self.text.tag_config("t_hint", font=(FAMILY, 9), foreground="#8a9a94", spacing3=16)

        self.text.insert("end", self.article["title"] + "\n", "t_title")
        self.text.insert(
            "end",
            "双击句子看句解 · 选中后右键解析 · 点段前「词」看本段生词\n",
            "t_hint",
        )
        for i, para in enumerate(self.article["paragraphs"]):
            entries = self._entries_for(i)
            if entries:
                self.text.insert("end", f"  [词 {len(entries)}] ", f"t_badge pv_{i}")
                self.text.tag_bind(f"pv_{i}", "<Button-1>",
                                   lambda e, k=i: self._show_para_vocab(k))
            self.text.insert("end", para + "\n\n", f"t_body p_{i}")
        self.text.configure(state="disabled")
        self.text.yview_moveto(frac)

        total = self._total_words()
        self.vocab_btn.config(text=f"生词表 {total}" if self.vocab else "生成生词表")
        self._refresh_status()

    def _refresh_status(self):
        if self.vocab_running:
            return  # 进度由 on_vocab_* 事件刷新
        self.status.config(text="")

    # ---------- 生词表 ----------
    def _vocab_btn(self):
        if self.vocab is not None:
            self._show_full_vocab()
        else:
            self.generate_vocab()

    def generate_vocab(self):
        if self.article is None or self.vocab_running:
            return
        cfg = dict(self.app.settings["llm"])
        invalid = llm.validate(cfg)
        if invalid:
            self.status.config(text=f"⚠ {invalid}（设置里配置）")
            return
        self.vocab_running = True
        self.vocab_btn.config(state="disabled")
        self.status.config(text="生词表生成中… 准备中")
        self.app.post(self._vocab_worker, cfg)

    def _vocab_worker(self, cfg):
        def progress(done, total):
            self.app.emit("vocab_progress", {"done": done, "total": total})

        def chars(n):
            self.app.emit("vocab_chars", n)

        try:
            vocab = gen.generate_vocab(
                self.meta, self.article, self.app.settings["vocabLevel"], cfg,
                on_progress=progress, on_chars=chars,
            )
            self.app.emit("vocab_done", vocab)
        except Exception as e:
            self.app.emit("vocab_error", str(e))

    def on_vocab_progress(self, p):
        self.status.config(text=f"生词表生成中… 第 {p['done']}/{p['total']} 块")

    def on_vocab_chars(self, n):
        if n > 0:
            head = self.status.cget("text").split(" · ")[0]
            self.status.config(text=f"{head} · 已接收 {n} 字")

    def on_vocab_done(self, vocab):
        self.vocab_running = False
        self.vocab_btn.config(state="normal")
        self.vocab = vocab
        self._rebuild(keep_scroll=True)
        self.status.config(text=f"生词表完成（{self._total_words()} 词，已缓存）")
        self._show_full_vocab()

    def on_vocab_error(self, msg):
        self.vocab_running = False
        self.vocab_btn.config(state="normal")
        self.status.config(text=f"生词表出错：{msg}")

    # ---------- 句解 ----------
    def _para_at(self, index):
        names = self.text.tag_names(index)
        for n in names:
            if n.startswith("p_"):
                return int(n[2:])
        return None

    def _para_range(self, idx):
        ranges = self.text.tag_ranges(f"p_{idx}")
        return ranges if ranges else None

    def _on_double_click(self, event):
        try:
            index = self.text.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        para = self._para_at(index)
        if para is None:
            return
        rng = self._para_range(para)
        if not rng:
            return
        local = len(self.text.get(rng[0], index))
        para_text = self.article["paragraphs"][para]
        sent = gen.sentence_at(gen.split_sentences(para_text), local)
        if sent:
            self._start_analysis(para, sent[2])
        return "break"

    def _on_right_click(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
        return "break"

    def _analyze_selection(self):
        try:
            rng = self.text.tag_ranges("sel")
        except tk.TclError:
            return
        if not rng:
            self.status.config(text="先选中一段文字再解析")
            return
        selected = self.text.get(rng[0], rng[1]).strip()
        if not selected:
            return
        para = self._para_at(rng[0])
        if len(selected) < 3:
            self.status.config(text="选中的内容太短")
            return
        # 截断到所在段落边界内的文本
        self._start_analysis(para if para is not None else 0, selected[:2200])

    def _analyze_whole_paragraph(self):
        try:
            index = self.text.index("insert")
        except tk.TclError:
            return
        para = self._para_at(index)
        if para is None:
            self.status.config(text="把光标放到某段里再解析整段")
            return
        text = self.article["paragraphs"][para][:2200]
        self._start_analysis(para, text)

    def _before_after(self, para, local_end=None):
        sents = gen.split_sentences(self.article["paragraphs"][para])
        return sents

    def _start_analysis(self, para, sentence):
        key = sentence
        if key in self.analysis_busy:
            return
        cached = store.load_analysis(self.meta["id"], sentence, self.article["index"])
        if cached:
            self._show_analysis(sentence, cached)
            return
        cfg = dict(self.app.settings["llm"])
        invalid = llm.validate(cfg)
        if invalid:
            self.status.config(text=f"⚠ {invalid}（设置里配置）")
            return
        self.analysis_busy.add(key)
        win, txt = self._make_sheet("句解", self.article["title"][:36])
        self._sheets[key] = (win, txt)
        _render_loading(txt, sentence)
        self.app.post(self._analysis_worker, cfg, para, sentence, key)

    def _analysis_worker(self, cfg, para, sentence, key):
        try:
            # 上下文：所在段落中该句的前后句
            para_text = self.article["paragraphs"][para]
            sents = gen.split_sentences(para_text)
            hit = gen.sentence_at(sents, para_text.find(sentence[:40]) if sentence in para_text else 0)
            before, after = gen.neighbors(sents, hit) if hit else ("", "")
            analysis = gen.analyze(self.meta, self.article, sentence, before, after, cfg)
            self.app.emit("analysis_done", (key, sentence, analysis))
        except Exception as e:
            self.app.emit("analysis_error", (key, str(e)))

    def on_analysis_done(self, payload):
        key, sentence, analysis = payload
        self.analysis_busy.discard(key)
        entry = self._sheets.pop(key, None)
        if entry and entry[0].winfo_exists():
            _render_analysis(entry[1], analysis)

    def on_analysis_error(self, payload):
        key, msg = payload
        self.analysis_busy.discard(key)
        entry = self._sheets.pop(key, None)
        if entry and entry[0].winfo_exists():
            _render_error(entry[1], msg)

    # ---------- 生词表弹层 ----------
    def _show_para_vocab(self, para):
        if self.vocab is None:
            self.generate_vocab()
            return
        self._render_vocab_sheet(f"第 {para + 1} 段生词", only_para=para)

    def _show_full_vocab(self):
        label = gen.level_label(self.app.settings["vocabLevel"])
        self._render_vocab_sheet(f"本篇生词表（{label}，{self._total_words()} 词）", only_para=None)

    def _render_vocab_sheet(self, title, only_para):
        win, txt = self._make_sheet(title, self.article["title"][:36])
        _render_vocab(txt, self.vocab, only_para)

    # ---------- 公共弹层 ----------
    def _make_sheet(self, title, subtitle):
        win = tk.Toplevel(self)
        win.title(f"{title} — {subtitle}")
        win.geometry("760x680")
        txt = tk.Text(win, wrap="word", bd=0, padx=20, pady=14,
                      font=(FAMILY, 11))
        vbar = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vbar.set)
        txt.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")
        _config_tags(txt)
        return win, txt


def _config_tags(txt):
    txt.tag_config("h1", font=(FAMILY, 13, "bold"), foreground="#1b4b43", spacing1=10)
    txt.tag_config("h2", font=(FAMILY, 11, "bold"), foreground="#7fa829", spacing1=10)
    txt.tag_config("word", font=(FAMILY, 11, "bold"), foreground="#1b4b43")
    txt.tag_config("meta", foreground="#7a8a86", font=(FAMILY, 9))
    txt.tag_config("sense", font=(FAMILY, 11))
    txt.tag_config("root", foreground="#5c7f10", font=(FAMILY, 10))
    txt.tag_config("term", foreground="#8a6d00", font=(FAMILY, 9, "bold"))
    txt.tag_config("quote", foreground="#556", font=(FAMILY, 10, "italic"))
    txt.tag_config("mono", font=("Consolas", 10), background="#f2f6f4")
    txt.tag_config("trans", background="#e9f4ef", font=(FAMILY, 11))
    txt.tag_config("src", foreground="#667", font=(FAMILY, 10))


def _render_loading(txt, sentence):
    txt.configure(state="normal")
    txt.delete("1.0", "end")
    txt.insert("end", sentence + "\n", "src")
    txt.insert("end", "\n解析中…（首次解析约需十几秒，结果会缓存）", "meta")
    txt.configure(state="disabled")


def _render_analysis(txt, analysis):
    """句解渲染：疑难词汇 → 语法对齐图解 → 语法点 → 语境 → 参考译文"""
    txt.configure(state="normal")
    txt.delete("1.0", "end")
    vocab = analysis.get("vocab") or []
    if vocab:
        txt.insert("end", "一、疑难词汇\n", "h1")
        for v in vocab:
            line = v.get("word", "")
            if v.get("phonetic"):
                line += f"  /{v['phonetic']}/"
            txt.insert("end", line, "word")
            if v.get("sense"):
                txt.insert("end", "\n" + v["sense"], "sense")
            if v.get("note"):
                txt.insert("end", "\n" + v["note"], "root")
            txt.insert("end", "\n\n")
    if analysis.get("grammarDiagram"):
        txt.insert("end", "二、语法图解\n", "h1")
        txt.insert("end", analysis["grammarDiagram"] + "\n", "mono")
    for i, p in enumerate(analysis.get("grammarPoints") or [], 1):
        txt.insert("end", f"{i}. {p.get('title', '')}\n", "word")
        if p.get("text"):
            txt.insert("end", p["text"] + "\n\n", "sense")
    if analysis.get("context"):
        txt.insert("end", "三、语境\n", "h1")
        txt.insert("end", analysis["context"] + "\n\n", "sense")
    if analysis.get("translation"):
        txt.insert("end", "参考译文\n", "h1")
        txt.insert("end", analysis["translation"] + "\n", "trans")
    txt.configure(state="disabled")


def _render_error(txt, msg):
    txt.configure(state="normal")
    txt.insert("end", f"\n出错：{msg}", "term")
    txt.configure(state="disabled")


def _render_vocab(txt, vocab, only_para):
    txt.configure(state="normal")
    txt.delete("1.0", "end")

    def entry_block(e):
        line = f"{e['word']}  "
        if e.get("phonetic"):
            line += f"/{e['phonetic']}/  "
        if e.get("pos"):
            line += f"{e['pos']}  "
        txt.insert("end", line, "word")
        if e.get("term"):
            txt.insert("end", "[术语] ", "term")
        txt.insert("end", "\n" + e.get("sense", ""), "sense")
        if e.get("inText"):
            txt.insert("end", f"\n原文：{e['inText']}", "quote")
        if e.get("root"):
            txt.insert("end", f"\n拆词：{e['root']}", "root")
        txt.insert("end", "\n\n")

    if only_para is None:
        te = vocab.get("titleEntries", [])
        if te:
            txt.insert("end", "〇 · 标题生词\n", "h1")
            for e in te:
                entry_block(e)
        idioms = vocab.get("idioms", [])
        if idioms:
            txt.insert("end", "〇 · 熟词僻义（常见义 → 本文义）\n", "h1")
            for it in idioms:
                txt.insert("end", it["word"] + "\n", "word")
                txt.insert("end", f"常见义：{it.get('common', '')}\n", "sense")
                txt.insert("end", f"本文义：{it.get('inText', '')}\n\n", "sense")
        txt.insert("end", "逐段生词\n", "h1")

    sections = [p for p in vocab.get("paragraphs", []) if only_para is None or p.get("index") == only_para]
    for p in sections:
        txt.insert("end", f"第 {p['index'] + 1} 段 ｜ {p.get('startWords', '')}…\n", "h2")
        entries = p.get("entries", [])
        if not entries:
            if only_para is not None:
                txt.insert("end", "本段没有收录生词\n\n", "meta")
            continue
        for e in entries:
            entry_block(e)
    txt.configure(state="disabled")
