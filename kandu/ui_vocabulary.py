"""生词与离线查词窗口：统一阅读排版，保留原生选择、复制与滚动。"""
import tkinter as tk
from tkinter import ttk

FAMILY = "Microsoft YaHei UI"
GREEN = "#1b4b43"
INK = "#2d3e36"
MUTED = "#738079"
PAPER = "#fffdf8"
BG = "#f3f4ef"


def button(parent, text, command, **kwargs):
    return tk.Button(parent, text=text, command=command, bg="#e5ebe2", fg=GREEN,
                     activebackground="#d7e3d4", activeforeground=GREEN, relief="flat",
                     bd=0, highlightthickness=0, cursor="hand2", font=(FAMILY, 9),
                     padx=kwargs.pop("padx", 10), pady=kwargs.pop("pady", 5), **kwargs)


def config_tags(txt, size=11):
    txt.configure(font=(FAMILY, size), spacing1=2, spacing2=5, spacing3=5)
    txt.tag_configure("h1", font=(FAMILY, size + 1, "bold"), foreground=GREEN, spacing1=14, spacing3=12)
    txt.tag_configure("h2", font=(FAMILY, size, "bold"), foreground=GREEN,
                      background="#eaf0e5", spacing1=14, spacing3=10, lmargin1=12, lmargin2=12)
    txt.tag_configure("word", font=("Georgia", size + 3, "bold"), foreground=GREEN, spacing1=12, spacing3=6)
    txt.tag_configure("phonetic", font=(FAMILY, size), foreground=MUTED)
    txt.tag_configure("meta", font=(FAMILY, max(8, size - 2)), foreground=MUTED, spacing3=8)
    txt.tag_configure("label", font=(FAMILY, max(8, size - 2), "bold"), foreground="#718569")
    txt.tag_configure("sense", font=(FAMILY, size), foreground=INK, spacing3=6)
    txt.tag_configure("root", font=(FAMILY, max(9, size - 1)), foreground="#60765a", spacing3=9)
    txt.tag_configure("term", font=(FAMILY, max(8, size - 2), "bold"), foreground="#9a7139")
    txt.tag_configure("quote", font=("Georgia", size), foreground="#577268", spacing2=5, spacing3=8)
    txt.tag_configure("warn", foreground="#9a7139", font=(FAMILY, max(9, size - 1)), spacing3=8)
    txt.tag_configure("search_hit", background="#f2e3af")
    txt.tag_configure("search_current", background="#dfc77b", foreground=GREEN)
    txt.tag_raise("sel")


def make_sheet(parent, app, title, subtitle):
    win = tk.Toplevel(parent)
    win.title(f"{title} — {subtitle}")
    win.geometry("900x820")
    win.minsize(520, 400)
    win.configure(bg=BG)
    brand = tk.Frame(win, bg=GREEN, padx=24, pady=8)
    brand.pack(fill="x")
    tk.Label(brand, text="刊读  /  VOCABULARY", bg=GREEN, fg="#dce9df", font=(FAMILY, 9)).pack(side="left")
    heading = tk.Frame(win, bg=BG, padx=24, pady=10)
    heading.pack(fill="x")
    heading.columnconfigure(0, weight=1)
    win._sheet_title = tk.Label(heading, text=title, bg=BG, fg=GREEN, anchor="w", width=1,
                                font=(FAMILY, 16, "bold"))
    win._sheet_title.grid(row=0, column=0, sticky="ew")
    tk.Label(heading, text=subtitle, bg=BG, fg=MUTED, anchor="w", width=1,
             font=(FAMILY, 9)).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
    tools = tk.Frame(heading, bg=BG)
    tools.grid(row=0, column=1, padx=(8, 0))
    controls = tk.Frame(win, bg=BG, padx=24)
    controls.pack(fill="x", pady=(0, 8))
    controls.columnconfigure(0, weight=1)
    search_box = tk.Frame(controls, bg=PAPER, highlightthickness=1, highlightbackground="#dce3d7", padx=8, pady=3)
    search_box.grid(row=0, column=0, sticky="ew", padx=(0, 10))
    tk.Label(search_box, text="查找词条", bg=PAPER, fg=MUTED, font=(FAMILY, 9)).pack(side="left", padx=(0, 8))
    query = tk.StringVar(win)
    search = tk.Entry(search_box, textvariable=query, width=8, relief="flat", bg=PAPER, fg=INK, font=(FAMILY, 10))
    search.pack(side="left", fill="x", expand=True)
    footer = tk.Frame(win, bg=GREEN, padx=14, pady=6)
    footer.pack(side="bottom", fill="x")
    txt_status = tk.Label(footer, text="可选中复制", bg=GREEN, fg="#dce9df", font=(FAMILY, 9), anchor="w", width=1)
    txt_status.pack(side="left", fill="x", expand=True)
    actions = tk.Frame(win, bg=BG, padx=24, pady=4)
    # 操作区在正文之外，窄窗下不与字号和状态文字挤占同一行。
    actions.pack(side="bottom", fill="x")
    page = tk.Frame(win, bg=PAPER, highlightthickness=1, highlightbackground="#dde3d8")
    page.pack(fill="both", expand=True, padx=24, pady=(0, 10))
    page.columnconfigure(0, weight=1)
    page.rowconfigure(0, weight=1)
    txt = tk.Text(page, bg=PAPER, fg=INK, bd=0, highlightthickness=0, wrap="word", padx=24, pady=14,
                  cursor="arrow", selectbackground="#d2e2d3", selectforeground=INK, undo=False)
    txt.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(page, orient="vertical", command=txt.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    txt.configure(yscrollcommand=scrollbar.set)
    txt._vocab_status = txt_status
    txt._vocab_query = query
    txt._vocab_hits = []
    txt._vocab_hit = -1
    txt._vocab_search_after = None
    txt._vocab_sections = []
    txt._vocab_word_ranges = []
    txt._vocab_count_text = "可选中复制"
    txt._vocab_size = max(9, min(22, int(app.settings.get("sheetFontSize", 11))))

    def change_size(delta):
        anchor = txt.index("@0,0")
        txt._vocab_size = max(9, min(22, txt._vocab_size + delta))
        app.settings["sheetFontSize"] = txt._vocab_size
        app.save_settings()
        config_tags(txt, txt._vocab_size)
        size_label.configure(text=str(txt._vocab_size))
        txt.update_idletasks()
        txt.yview(anchor)

    button(tools, "A−", lambda: change_size(-1), padx=7).pack(side="left")
    size_label = tk.Label(tools, text=str(txt._vocab_size), width=3, bg=BG, fg=MUTED, font=(FAMILY, 9))
    size_label.pack(side="left")
    button(tools, "A+", lambda: change_size(1), padx=7).pack(side="left")
    config_tags(txt, txt._vocab_size)

    def choose_section(_event=None):
        index = sections.current()
        if 0 <= index < len(txt._vocab_sections):
            txt.yview(txt._vocab_sections[index][1])

    sections = ttk.Combobox(controls, state="disabled", width=14, font=(FAMILY, 9))
    sections.set("段落定位")
    sections.grid(row=0, column=1, sticky="e", ipady=4)
    sections.bind("<<ComboboxSelected>>", choose_section)
    txt._vocab_section_choice = sections
    button(search_box, "↑", lambda: _move_hit(txt, -1), padx=4, pady=0).pack(side="left")
    button(search_box, "↓", lambda: _move_hit(txt, 1), padx=4, pady=0).pack(side="left")
    button(search_box, "×", lambda: query.set(""), padx=4, pady=0).pack(side="left")

    def queue_search(*_):
        if txt._vocab_search_after is not None:
            txt.after_cancel(txt._vocab_search_after)
        txt._vocab_search_after = txt.after(120, lambda: _search(txt))

    query.trace_add("write", queue_search)
    search.bind("<Return>", lambda e: _move_hit(txt, 1))
    search.bind("<Shift-Return>", lambda e: _move_hit(txt, -1))
    search.bind("<Escape>", lambda e: query.set(""))
    win.bind("<Control-f>", lambda e: search.focus_set())
    win.bind("<Control-Home>", lambda e: txt.yview_moveto(0))
    button(footer, "回到开头 ↑", lambda: txt.yview_moveto(0), pady=2).pack(side="right")

    def cleanup(event):
        if event.widget is win and txt._vocab_search_after is not None:
            txt.after_cancel(txt._vocab_search_after)
            txt._vocab_search_after = None

    win.bind("<Destroy>", cleanup, add="+")

    def activate(event):
        if event.widget is win:
            win.unbind("<Map>", binding)
            win.after_idle(txt.focus_set)

    binding = win.bind("<Map>", activate, add="+")
    return win, txt, actions


def _move_hit(txt, delta):
    if txt._vocab_search_after is not None:
        _search(txt, jump=False)
    if not txt._vocab_hits:
        return
    if txt._vocab_hit < 0:
        txt._vocab_hit = 0 if delta > 0 else len(txt._vocab_hits) - 1
    else:
        txt._vocab_hit = (txt._vocab_hit + delta) % len(txt._vocab_hits)
    start, end = txt._vocab_hits[txt._vocab_hit]
    txt.tag_remove("search_current", "1.0", "end")
    txt.tag_add("search_current", start, end)
    txt.see(start)
    txt._vocab_status.configure(text=f"词条 {txt._vocab_hit + 1} / {len(txt._vocab_hits)}  ·  {txt._vocab_count_text}")


def _search(txt, jump=True):
    if not hasattr(txt, "_vocab_query"):
        return
    if txt._vocab_search_after is not None:
        txt.after_cancel(txt._vocab_search_after)
        txt._vocab_search_after = None
    txt.tag_remove("search_hit", "1.0", "end")
    txt.tag_remove("search_current", "1.0", "end")
    txt._vocab_hits = []
    txt._vocab_hit = -1
    query = txt._vocab_query.get().strip()
    if query:
        for start, end in txt._vocab_word_ranges:
            if query.casefold() in txt.get(start, end).casefold():
                txt._vocab_hits.append((start, end))
                txt.tag_add("search_hit", start, end)
    txt._vocab_status.configure(text=("未找到匹配词条  ·  " if query and not txt._vocab_hits else "") + txt._vocab_count_text)
    if jump and txt._vocab_hits:
        _move_hit(txt, 1)


def _reset(txt):
    txt.configure(state="normal")
    txt.delete("1.0", "end")
    for mark in txt.mark_names():
        if str(mark).startswith("vocab_section_"):
            txt.mark_unset(mark)
    txt._vocab_sections = []
    txt._vocab_word_ranges = []


def _section(txt, title):
    mark = f"vocab_section_{len(txt._vocab_sections)}"
    txt.mark_set(mark, "end-1c")
    txt.mark_gravity(mark, "left")
    txt._vocab_sections.append((title, mark))
    txt.insert("end", title + "\n", "h2")


def _word(txt, word):
    start = txt.index("end-1c")
    txt.insert("end", word, "word")
    txt._vocab_word_ranges.append((start, txt.index("end-1c")))


def _finish(txt, summary):
    txt.configure(state="disabled")
    txt.yview_moveto(0)
    if hasattr(txt, "_vocab_status"):
        txt._vocab_count_text = summary
        choice = txt._vocab_section_choice
        choice.configure(values=[title for title, _ in txt._vocab_sections],
                         state="readonly" if txt._vocab_sections else "disabled")
        choice.set("段落定位")
        _search(txt, jump=False)


def render_vocab(txt, vocab, only_para):
    _reset(txt)
    count = 0

    def entry_block(entry):
        nonlocal count
        count += 1
        _word(txt, entry["word"])
        if entry.get("phonetic"):
            txt.insert("end", "   /" + entry["phonetic"].strip("/") + "/", "phonetic")
        if entry.get("pos"):
            txt.insert("end", "   " + entry["pos"], "meta")
        if entry.get("term"):
            txt.insert("end", "   术语", "term")
        txt.insert("end", "\n")
        offline = entry.get("senseSource") == "dictionary"
        if offline:
            txt.insert("end", "词典释义（待补充本文含义）\n", "warn")
        txt.insert("end", "释义  " if offline else "本文义  ", "label")
        txt.insert("end", entry.get("sense", "") + "\n", "sense")
        if entry.get("inText"):
            txt.insert("end", "原文  ", "label")
            txt.insert("end", entry["inText"] + "\n", "quote")
        if entry.get("root"):
            txt.insert("end", "词根词源  ", "label")
            txt.insert("end", entry["root"] + "\n", "root")
        txt.insert("end", "\n")

    if only_para is None:
        if vocab.get("titleEntries"):
            _section(txt, "标题生词")
            for entry in vocab["titleEntries"]:
                entry_block(entry)
        if vocab.get("idioms"):
            _section(txt, "熟词僻义")
            for entry in vocab["idioms"]:
                count += 1
                _word(txt, entry["word"])
                txt.insert("end", "\n常见义  ", "label")
                txt.insert("end", entry.get("common", "") + "\n", "sense")
                txt.insert("end", "本文义  ", "label")
                txt.insert("end", entry.get("inText", "") + "\n\n", "sense")
    sections = [p for p in vocab.get("paragraphs", []) if only_para is None or p.get("index") == only_para]
    for paragraph in sections:
        entries = paragraph.get("entries", [])
        if not entries and only_para is None:
            continue
        _section(txt, f"第 {paragraph['index'] + 1} 段  /  {len(entries)} 词")
        if paragraph.get("startWords"):
            txt.insert("end", paragraph["startWords"].rstrip("…") + "…\n", "meta")
        for entry in entries:
            entry_block(entry)
    if not count:
        txt.insert("end", "本段没有收录生词\n" if only_para is not None else "本篇尚未收录生词\n", "meta")
    _finish(txt, f"{count} 个词条  ·  可选中复制")


def render_lookup(txt, word, result):
    _reset(txt)
    _word(txt, (result or {}).get("word") or word)
    txt.insert("end", "\n")
    if not result:
        txt.insert("end", "词典尚未就绪（首次初始化约需半分钟），稍后再试。\n", "meta")
    elif result.get("miss"):
        txt.insert("end", f"本地词典未收录「{word}」。\n\n", "sense")
        txt.insert("end", "可以选中整句用「解析」查看语境，或确认拼写后重试。\n", "meta")
    else:
        if result.get("phonetic"):
            txt.insert("end", "/" + result["phonetic"].strip("/") + "/   ", "phonetic")
        if result.get("pos"):
            txt.insert("end", result["pos"], "meta")
        txt.insert("end", "\n\n")
        _section(txt, "词典释义")
        txt.insert("end", (result.get("translation") or "（无释义）") + "\n", "sense")
        if result.get("exchange"):
            _section(txt, "词形变化")
            txt.insert("end", result["exchange"] + "\n", "root")
        if result.get("lookedUp") and result["lookedUp"].casefold() != result.get("word", word).casefold():
            txt.insert("end", f"\n由「{result['lookedUp']}」还原原形\n", "meta")
        txt.insert("end", "\n来源：本地词典 ECDICT（离线）\n", "meta")
    _finish(txt, "本地词典  ·  可选中复制")
