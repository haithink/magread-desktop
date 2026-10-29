"""阅读页：正文渲染、双击句解、长按/选中解析、全篇与逐段生词表。"""
import tkinter as tk
from tkinter import ttk, messagebox

from kandu import core_parsers as store
from kandu import core_llm as llm
from kandu import core_gen as gen
from kandu import core_dict
from kandu import core_config as cfgmod
from kandu.core_config import app_dir


def _render_lookup(txt, word, result):
    """本地查词结果渲染"""
    txt.configure(state="normal")
    txt.delete("1.0", "end")
    if not result:
        txt.insert("end", "词典尚未就绪（首次初始化约需半分钟），稍后再试。", "meta")
        txt.configure(state="disabled")
        return
    if result.get("miss"):
        txt.insert("end", f"本地词典未收录「{word}」。\n\n", "word")
        txt.insert("end", "可能是专有名词、生僻变形或合成词。可以选中整句用「解析」让大模型讲解，"
                          "或确认拼写后重试。", "sense")
        txt.configure(state="disabled")
        return
    txt.insert("end", result.get("word", word) + "\n", "word")
    meta_bits = []
    if result.get("phonetic"):
        meta_bits.append("/" + result["phonetic"].strip("/") + "/")
    if result.get("pos"):
        meta_bits.append(result["pos"])
    if meta_bits:
        txt.insert("end", "  ".join(meta_bits) + "\n", "meta")
    txt.insert("end", "\n" + (result.get("translation") or "（无释义）") + "\n", "sense")
    if result.get("exchange"):
        txt.insert("end", "\n变形：" + result["exchange"], "root")
    if result.get("lookedUp") and result["lookedUp"].lower() != word.lower():
        txt.insert("end", f"\n（由「{result['lookedUp']}」还原原形）", "meta")
    txt.insert("end", "\n\n来源：本地词典 ECDICT（离线）", "meta")
    txt.configure(state="disabled")


FAMILY = "Microsoft YaHei UI"
INK_PAPER = "#EAF2E5"  # 阅读页浅豆绿纸色


class ReaderFrame(tk.Frame):
    def __init__(self, parent, app, meta, article_index):
        super().__init__(parent, bg=INK_PAPER)
        self.app = app
        self.meta = meta
        self.article_index = article_index
        self.article = None
        self.vocab = None
        self.vocab_running = False
        self.analysis_busy = set()
        self._last_click = (None, None)
        self._sheets = {}
        self._sheets = {}

        bar = tk.Frame(self, bg="#1b4b43")
        bar.pack(fill="x")
        # 阅读页返回应回到该书的摘要页（书目页的返回才是回书架）
        ttk.Button(bar, text="← 返回", command=lambda: self.app.open_digest(self.meta)).pack(side="left", padx=10, pady=6)
        self.title_label = tk.Label(bar, text="加载中…", bg="#1b4b43", fg="white",
                                    font=(FAMILY, 12, "bold"))
        self.title_label.pack(side="left", padx=6)
        self.vocab_btn = ttk.Button(bar, text="生成生词表", command=self._vocab_btn)
        self.vocab_btn.pack(side="right", padx=10, pady=6)
        ttk.Button(bar, text="A+", width=3, command=lambda: self._font(+1)).pack(side="right", padx=4, pady=6)
        ttk.Button(bar, text="A-", width=3, command=lambda: self._font(-1)).pack(side="right", padx=4, pady=6)

        self.status = tk.Label(self, text="", bg=INK_PAPER, fg="#5a6b5a", anchor="w")
        self.status.pack(fill="x", padx=14, pady=(4, 0))

        body = tk.Frame(self, bg=INK_PAPER)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", state="disabled", bg=INK_PAPER, bd=0,
                            padx=28, pady=16, cursor="arrow",
                            font=(FAMILY, int(self.app.settings.get("readerFontSize", 13))))
        vbar = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=vbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        self.text.tag_config("t_title", font=(FAMILY, 17, "bold"), spacing3=6)
        self.text.tag_config("t_hint", foreground="#8a9", font=(FAMILY, 9))
        self.text.tag_config("sel", background="#cfe3c8")
        self.text.tag_config("t_badge", foreground="#5f8a17", font=(FAMILY, 9, "underline"))
        self.text.bind("<Double-Button-1>", self._on_double_click)
        self.text.bind("<Button-3>", self._on_right_click)
        self.text.bind("<Return>", lambda e: self._on_double_click(e))

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="查词（本地词典）", command=self._menu_lookup)
        self.menu.add_separator()
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
        if self.vocab is None:
            self.vocab_btn.config(text="生成生词表")
        elif self.vocab.get("failedChunks") or self._missing_paragraphs() is not None:
            miss = self._missing_paragraphs()
            self.vocab_btn.config(text=f"补缺生词表" + (f"（缺 {miss} 段）" if miss else ""))
        else:
            self.vocab_btn.config(text=f"生词表 {total}")
        self._refresh_status()

    def _refresh_status(self):
        if self.vocab_running:
            return  # 进度由 on_vocab_* 事件刷新
        self.status.config(text="")

    # ---------- 生词表 ----------
    def _vocab_btn(self):
        if self.vocab_running:
            return
        state = self._vocab_state()
        if state == "complete":
            self._show_full_vocab()
            return
        # none / partial（含旧版无失败记录的缺失块）→ 生成 / 轮换补缺
        self.generate_vocab()

    def _vocab_state(self):
        """none：未生成；partial：有段落缺失或存在可重试的失败块；complete：其余。"""
        if not self.vocab:
            return "none"
        if self.article is None:
            return "complete"
        processed = set(self.vocab.get("processedParagraphs", []))
        if any(i not in processed for i in range(len(self.article["paragraphs"]))):
            return "partial"  # 旧版缓存可能无 chunkFailures，缺段落即需补
        cf = self.vocab.get("chunkFailures") or {}
        names = {p.get("name") for p in (self.app.settings.get("llms") or [])}
        for key, tried in cf.items():
            if len(set(tried) & names) < len(names):
                return "partial"
        return "complete"

    def _missing_paragraphs(self):
        """尚无生词数据的段落数（用于按钮文案）；完整时返回 None。"""
        if not self.vocab or self.article is None:
            return None
        processed = set(self.vocab.get("processedParagraphs", []))
        missing = sum(1 for i in range(len(self.article["paragraphs"])) if i not in processed)
        return missing or None

    def generate_vocab(self, cfg=None):
        if self.article is None or self.vocab_running:
            return
        cfg = dict(cfg or cfgmod.get_llm(self.app.settings))
        invalid = llm.validate(cfg)
        if invalid:
            self.status.config(text=f"⚠ {invalid}（设置里配置）")
            return
        self.vocab_running = True
        self.vocab_btn.config(state="disabled")
        self.status.config(text=f"生词表生成中… 准备中（{cfg.get('name', '默认')}）")
        self.app.post(self._vocab_worker, cfg)

    def _vocab_worker(self, cfg):
        def progress(done, total, llm_name=None):
            self.app.emit("vocab_progress", {"done": done, "total": total, "llm": llm_name})

        def chars(n):
            self.app.emit("vocab_chars", n)

        rotation = [dict(p) for p in (self.app.settings.get("llms") or [])]
        try:
            vocab = gen.generate_vocab(
                self.meta, self.article, self.app.settings["vocabLevel"], cfg,
                on_progress=progress, on_chars=chars, llm_rotation=rotation,
            )
            self.app.emit("vocab_done", vocab)
        except Exception as e:
            self.app.emit("vocab_error", str(e))

    def on_vocab_progress(self, p):
        llm = p.get("llm")
        self.status.config(text=f"生词表生成中… 第 {p['done']}/{p['total']} 块"
                                + (f" · {llm}" if llm else ""))

    def on_vocab_chars(self, n):
        if n > 0:
            head = self.status.cget("text").split(" · ")[0]
            self.status.config(text=f"{head} · 已接收 {n} 字")

    def on_vocab_done(self, vocab):
        self.vocab_running = False
        self.vocab_btn.config(state="normal")
        self.vocab = vocab
        self._rebuild(keep_scroll=True)
        failed = vocab.get("failedChunks") or 0
        hint = "（部分内容可能触发模型安全过滤）" if vocab.get("censorHint") else ""
        if failed:
            self.status.config(text=f"生词表部分完成：{failed} 块失败{hint}。点「补缺生词表」自动轮换其他 LLM 重试")
        else:
            self.status.config(text=f"生词表完成（{self._total_words()} 词，已缓存）")
        self._show_full_vocab()

    def on_vocab_error(self, msg):
        self.vocab_running = False
        self.vocab_btn.config(state="normal")
        self.status.config(text=f"生词表出错：{msg}")
        self._offer_other_llm(lambda cfg: self.generate_vocab(cfg))

    def _offer_other_llm(self, retry, failed_name=None):
        """默认 LLM 失败时，若配置了其他 LLM 则弹出选择框供换用重试。"""
        others = cfgmod.other_llms(self.app.settings, exclude_name=failed_name)
        if not others:
            return
        win = tk.Toplevel(self)
        win.title("换一个 LLM 重试")
        win.geometry("480x300")
        ttk.Label(win, text="当前 LLM 生成失败。选择其他配置重试：",
                  font=(FAMILY, 10)).pack(padx=14, pady=(12, 8), anchor="w")
        lb = tk.Listbox(win, font=(FAMILY, 10), selectmode="browse")
        lb.pack(fill="both", expand=True, padx=12)
        for p in others:
            lb.insert("end", f"{p.get('name')}  —  {p.get('provider')} / {p.get('model')}")

        def go(_e=None):
            sel = lb.curselection()
            if sel:
                cfg = others[sel[0]]
                win.destroy()
                retry(dict(cfg))
        ttk.Button(win, text="用此配置重试", command=go).pack(pady=8)
        lb.bind("<Double-1>", go)

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
        self._last_click = self._word_at_click(event)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
        return "break"

    def _menu_lookup(self):
        para, offset = self._last_click
        self._lookup_selection(para, offset)

    def _word_at_click(self, event):
        try:
            index = self.text.index(f"@{event.x},{event.y}")
            para = self._para_at(index)
            if para is None:
                return None, None
            rng = self._para_range(para)
            local = len(self.text.get(rng[0], index))
            return para, local
        except tk.TclError:
            return None, None

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
        if cached and not cached.get("partial"):
            win, txt = self._make_sheet("句解", self.article["title"][:36])
            self._sheets[key] = (win, txt)
            _render_analysis(txt, cached, sentence)
            return
        cfg = dict(cfgmod.get_llm(self.app.settings))
        invalid = llm.validate(cfg)
        if invalid:
            self.status.config(text=f"⚠ {invalid}（设置里配置）")
            return
        self.analysis_busy.add(key)
        win, txt = self._make_sheet("句解", self.article["title"][:36])
        self._sheets[key] = (win, txt)
        _render_loading(txt, sentence)
        self.app.post(self._analysis_worker, cfg, para, sentence, key)

    # ---------- 本地查词（不依赖 LLM） ----------
    def _lookup_selection(self, para, offset):
        """右键「查词」：优先取选中文本，否则取点击位置的单词。"""
        word = ""
        rng = self.text.tag_ranges("sel")
        if rng:
            word = self.text.get(rng[0], rng[1]).strip()
        if not word and para is not None and para < len(self.article["paragraphs"]):
            para_text = self.article["paragraphs"][para]
            if offset is not None and 0 <= offset <= len(para_text):
                left = max(para_text.rfind(" ", 0, offset), para_text.rfind("\n", 0, offset))
                right = min(
                    para_text.find(" ", offset) if para_text.find(" ", offset) >= 0 else len(para_text),
                    para_text.find("\n", offset) if para_text.find("\n", offset) >= 0 else len(para_text),
                )
                word = para_text[left + (1 if left >= 0 else 0):right].strip(",.!?;:\"'()[]")
        word = word.strip(",.!?;:\"'()[]“”‘’")
        if not word:
            self.status.config(text="先选中或把光标放到要查的单词上")
            return
        win = tk.Toplevel(self)
        win.title(f"查词 · {word}")
        win.geometry("560x420")
        txt = tk.Text(win, wrap="word", bd=0, padx=18, pady=14, font=(FAMILY, 11))
        vbar = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vbar.set)
        txt.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")
        _config_tags(txt)
        txt.insert("end", word + "\n", "word")
        txt.insert("end", "\n查询中…（词典首次初始化约需半分钟）", "meta")
        self.app.post(self._lookup_worker, word, para, win, txt)

    def _lookup_worker(self, word, para, win, txt):
        core_dict.ensure_db(background=False)
        result = core_dict.lookup(word)
        self.app.q.put(("lookup_done", (win, txt, word, para, result)))

    def on_lookup_done(self, payload):
        win, txt, word, para, result = payload
        if not win.winfo_exists():
            return
        _render_lookup(txt, word, result)
        if result and not result.get("miss"):
            add_btn = ttk.Button(win, text="收进本篇生词表", command=lambda: self._add_word_to_vocab(para, result))
            add_btn.pack(anchor="w", padx=18, pady=(0, 12))

    def _add_word_to_vocab(self, para, result):
        level = self.app.settings["vocabLevel"]
        vocab = self.vocab or {"level": level, "articleIndex": self.article_index,
                               "titleEntries": [], "paragraphs": [], "idioms": []}
        entry = {
            "word": (result.get("word") or "").lower(),
            "phonetic": result.get("phonetic", ""),
            "pos": result.get("pos", ""),
            "sense": result.get("translation", "").replace("\n", "；"),
            "inText": "",
            "root": "",
            "term": False,
        }
        sections = {p["index"]: p for p in vocab["paragraphs"]}
        sec = sections.setdefault(para, {"index": para, "startWords": "", "entries": []})
        if all(e["word"] != entry["word"] for e in sec["entries"]):
            sec["entries"].append(entry)
        vocab["paragraphs"] = sorted(sections.values(), key=lambda x: x["index"])
        from kandu.core_gen import vocab_normalize
        normalized = vocab_normalize(vocab, self.article)
        store.save_vocab(self.meta["id"], normalized)
        self.vocab = normalized
        self._rebuild(keep_scroll=True)
        self.status.config(text=f"已把「{entry['word']}」收进第 {para + 1} 段生词表")

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
            _render_analysis(entry[1], analysis, sentence)

    def on_analysis_error(self, payload):
        key, msg = payload
        self.analysis_busy.discard(key)
        entry = self._sheets.get(key)
        if entry and entry[0].winfo_exists():
            _render_error(entry[1], msg)
        # 多 LLM 时提供换用重试（保留弹窗，重试后原窗渲染）
        failed_name = cfgmod.default_llm_name(self.app.settings)
        self._offer_other_llm(lambda cfg: self._retry_analysis(cfg, key), failed_name=failed_name)

    def _retry_analysis(self, cfg, key):
        if self.article is None or key in self.analysis_busy:
            return
        sentence = key
        para = next((i for i, p in enumerate(self.article["paragraphs"]) if sentence[:40] in p), 0)
        self.analysis_busy.add(key)
        entry = self._sheets.get(key)
        if entry and entry[0].winfo_exists():
            _render_loading(entry[1], sentence)
        self.app.post(self._analysis_worker, cfg, para, sentence, key)

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
        win.geometry("860x800")
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
    txt.tag_config("warn", foreground="#b06000", font=(FAMILY, 9))


def _render_loading(txt, sentence):
    txt.configure(state="normal")
    txt.delete("1.0", "end")
    txt.insert("end", sentence + "\n", "src")
    txt.insert("end", "\n解析中…（首次解析约需十几秒，结果会缓存）", "meta")
    txt.configure(state="disabled")


def _render_analysis(txt, analysis, sentence=None):
    """句解渲染：原句 → 疑难词汇 → 语法对齐图解 → 语法点 → 语境 → 参考译文"""
    txt.configure(state="normal")
    txt.delete("1.0", "end")
    if sentence:
        txt.insert("end", sentence + "\n", "src")
    if analysis.get("partial"):
        txt.insert("end", "⚠ 本次解析可能不完整（模型输出被截断）。关闭弹窗后重新双击本句将自动重试。\n\n", "warn")
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
