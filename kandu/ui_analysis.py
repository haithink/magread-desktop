"""句子讲解窗口：原句、词汇、语法、语境与译文的阅读排版。"""
import tkinter as tk
from tkinter import font as tkfont, ttk

FAMILY = "Microsoft YaHei UI"
GREEN = "#1b4b43"
INK = "#2d3e36"
MUTED = "#738079"
PAPER = "#fffdf8"
BG = "#f3f4ef"


def _styles(txt, size):
    txt.configure(font=(FAMILY, size), spacing1=2, spacing2=5, spacing3=6)
    txt.tag_configure("eyebrow", font=(FAMILY, max(8, size - 2), "bold"),
                      foreground=MUTED, spacing1=12, spacing3=10)
    txt.tag_configure("source", font=("Georgia", size + 3), foreground=GREEN,
                      spacing1=4, spacing2=8, spacing3=16)
    txt.tag_configure("section", font=(FAMILY, size + 1, "bold"), foreground=GREEN,
                      spacing1=20, spacing3=12)
    txt.tag_configure("word", font=("Georgia", size + 2, "bold"), foreground=GREEN,
                      spacing1=9, spacing3=3)
    txt.tag_configure("phonetic", font=(FAMILY, max(9, size - 1)), foreground=MUTED)
    txt.tag_configure("body", foreground=INK, spacing3=10)
    txt.tag_configure("note", foreground="#6c7e64", font=(FAMILY, max(9, size - 1)), spacing3=12)
    txt.tag_configure("point", font=(FAMILY, size, "bold"), foreground=INK,
                      spacing1=10, spacing3=4)
    # Consolas 回退中文时，汉字宽度并非英文的两倍；新宋体保持严格的 2:1 列宽。
    diagram_font = tkfont.Font(root=txt, family="NSimSun", size=size)
    txt.tag_configure("diagram", font=("NSimSun", size), foreground="#365c54",
                      background="#edf2eb", lmargin1=14, lmargin2=14, rmargin=14,
                      spacing1=10, spacing2=6, spacing3=10, wrap="none",
                      tabs=(diagram_font.measure(" ") * 8,), tabstyle="wordprocessor")
    txt.tag_configure("translation", font=(FAMILY, size + 1), foreground=GREEN,
                      background="#e8f1e6", lmargin1=16, lmargin2=16, rmargin=16,
                      spacing1=12, spacing2=8, spacing3=12)
    txt.tag_configure("meta", font=(FAMILY, max(8, size - 2)), foreground=MUTED,
                      spacing1=8, spacing3=6)
    txt.tag_configure("warn", foreground="#9a6238", spacing1=10, spacing3=12)
    txt.tag_configure("term", foreground="#9a6238")


def make_sheet(parent, app, subtitle):
    win = tk.Toplevel(parent)
    win.title(f"句子讲解 — {subtitle[:60]}")
    win.geometry("960x860")
    win.minsize(560, 420)
    win.configure(bg=BG)
    brand = tk.Frame(win, bg=GREEN, padx=24, pady=8)
    brand.pack(fill="x")
    tk.Label(brand, text="刊读  /  SENTENCE STUDY", bg=GREEN, fg="#dce9df",
             font=(FAMILY, 9)).pack(side="left")
    heading = tk.Frame(win, bg=BG, padx=24, pady=12)
    heading.pack(fill="x")
    heading.columnconfigure(0, weight=1)
    tk.Label(heading, text="句子讲解", bg=BG, fg=GREEN, font=(FAMILY, 19, "bold"),
             anchor="w").grid(row=0, column=0, sticky="ew")
    tk.Label(heading, text=subtitle, bg=BG, fg=MUTED, font=(FAMILY, 9),
             anchor="w", width=1).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 0))
    tools = tk.Frame(heading, bg=BG)
    tools.grid(row=0, column=1, padx=(12, 0))
    nav = tk.Frame(win, bg=BG, padx=24, pady=4)
    nav.pack(fill="x")
    footer = tk.Frame(win, bg=GREEN, padx=14, pady=5)
    footer.pack(side="bottom", fill="x")
    tk.Label(footer, text="句解", bg=GREEN, fg="#dce9df", font=(FAMILY, 9)).pack(side="left")
    page = tk.Frame(win, bg=PAPER, highlightthickness=1, highlightbackground="#dde3d8")
    page.pack(fill="both", expand=True, padx=24, pady=(8, 16))
    page.rowconfigure(0, weight=1)
    page.columnconfigure(0, weight=1)
    txt = tk.Text(page, bg=PAPER, fg=INK, bd=0, highlightthickness=0, wrap="word",
                  padx=26, pady=16, cursor="arrow", selectbackground="#d2e2d3",
                  selectforeground=INK, undo=False)
    txt.grid(row=0, column=0, sticky="nsew")
    vertical = ttk.Scrollbar(page, orient="vertical", command=txt.yview)
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal = ttk.Scrollbar(page, orient="horizontal", command=txt.xview)
    horizontal.grid(row=1, column=0, sticky="ew")
    horizontal.grid_remove()

    def horizontal_range(first, last):
        horizontal.set(first, last)
        if float(first) > 0 or float(last) < .999:
            horizontal.grid()
        else:
            horizontal.grid_remove()

    txt.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal_range)
    txt._analysis_nav = {}
    txt._analysis_source = ""
    txt._analysis_size = int(app.settings.get("sheetFontSize", 11))
    _styles(txt, txt._analysis_size)

    def button(parent, text, command):
        return tk.Button(parent, text=text, command=command, bg="#e5ebe2", fg=GREEN,
                         activebackground="#d7e3d4", activeforeground=GREEN,
                         relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                         font=(FAMILY, 9), padx=10, pady=5)

    def font_size(delta):
        app.settings["sheetFontSize"] = max(9, min(22, txt._analysis_size + delta))
        txt._analysis_size = app.settings["sheetFontSize"]
        app.save_settings()
        _styles(txt, txt._analysis_size)
        size_label.configure(text=str(txt._analysis_size))

    button(tools, "A−", lambda: font_size(-1)).pack(side="left")
    size_label = tk.Label(tools, text=str(txt._analysis_size), bg=BG, fg=MUTED, font=(FAMILY, 9), width=3)
    size_label.pack(side="left")
    button(tools, "A+", lambda: font_size(1)).pack(side="left")

    def jump(mark):
        if mark in txt.mark_names():
            txt.xview_moveto(0)
            txt.yview(mark)

    for mark, title in (("source", "原句"), ("vocab", "词汇"), ("grammar", "语法"),
                        ("context", "语境"), ("translation", "译文")):
        btn = button(nav, title, lambda m=mark: jump("section_" + m))
        btn.configure(state="disabled")
        btn.pack(side="left", padx=(0, 6))
        txt._analysis_nav[mark] = btn

    def copy_source():
        if txt._analysis_source:
            win.clipboard_clear()
            win.clipboard_append(txt._analysis_source)

    button(footer, "复制原句", copy_source).pack(side="right")

    def activate(event):
        if event.widget is win:
            win.unbind("<Map>", binding)
            win.after_idle(txt.focus_set)

    binding = win.bind("<Map>", activate, add="+")
    return win, txt, footer


def _reset(txt, sentence):
    txt.configure(state="normal")
    txt.delete("1.0", "end")
    for mark in txt.mark_names():
        if str(mark).startswith("section_"):
            txt.mark_unset(mark)
    txt._analysis_source = sentence or ""
    for btn in getattr(txt, "_analysis_nav", {}).values():
        btn.configure(state="disabled")


def _section(txt, key, title, tag="section"):
    mark = "section_" + key
    txt.mark_set(mark, "end-1c")
    txt.mark_gravity(mark, "left")
    txt.insert("end", title + "\n", tag)
    btn = getattr(txt, "_analysis_nav", {}).get(key)
    if btn is not None:
        btn.configure(state="normal")


def render_loading(txt, sentence):
    _reset(txt, sentence)
    _section(txt, "source", "ORIGINAL  /  原句", "eyebrow")
    txt.insert("end", sentence + "\n\n", "source")
    txt.insert("end", "正在讲解这个句子…\n", "section")
    txt.insert("end", "接收进度显示在窗口底部，完整结果会自动缓存。", "meta")
    txt.configure(state="disabled")
    txt.yview_moveto(0)


def render_analysis(txt, analysis, sentence=None):
    _reset(txt, sentence)
    if sentence:
        _section(txt, "source", "ORIGINAL  /  原句", "eyebrow")
        txt.insert("end", sentence.strip() + "\n", "source")
    if analysis.get("partial"):
        txt.insert("end", "本次讲解尚不完整，关闭窗口后可重新双击此句重试。\n", "warn")
    vocab = analysis.get("vocab") or []
    if vocab:
        _section(txt, "vocab", "01  /  疑难词汇")
        for item in vocab:
            txt.insert("end", item.get("word", ""), "word")
            if item.get("phonetic"):
                txt.insert("end", "   /" + item["phonetic"].strip("/") + "/", "phonetic")
            txt.insert("end", "\n")
            if item.get("sense"):
                txt.insert("end", item["sense"] + "\n", "body")
            if item.get("note"):
                txt.insert("end", item["note"] + "\n", "note")
    if analysis.get("grammarDiagram") or analysis.get("grammarPoints"):
        _section(txt, "grammar", "02  /  句子结构与语法")
        if analysis.get("grammarDiagram"):
            # 缩进也是图解布局的一部分，不能用 strip() 删除第一行的空格。
            diagram = analysis["grammarDiagram"].replace("\\n", "\n")
            txt.insert("end", diagram if diagram.endswith("\n") else diagram + "\n", "diagram")
        for i, point in enumerate(analysis.get("grammarPoints") or [], 1):
            txt.insert("end", f"{i:02d}   {point.get('title', '')}\n", "point")
            if point.get("text"):
                txt.insert("end", point["text"] + "\n", "body")
    if analysis.get("context"):
        _section(txt, "context", "03  /  放回上下文")
        txt.insert("end", analysis["context"] + "\n", "body")
    if analysis.get("translation"):
        _section(txt, "translation", "04  /  参考译文")
        txt.insert("end", analysis["translation"] + "\n", "translation")
    stats = analysis.get("generationStats") or {}
    if stats.get("elapsedMs") is not None:
        txt.insert("end", f"\n生成耗时 {stats['elapsedMs'] / 1000:.1f} 秒\n", "meta")
    txt.configure(state="disabled")
    txt.xview_moveto(0)
    txt.yview_moveto(0)
