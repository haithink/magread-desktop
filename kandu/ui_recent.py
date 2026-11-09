"""最近阅读文章列表：跨刊物汇总、按时间排序，直接恢复已有阅读位置。"""
from datetime import datetime
import tkinter as tk
from tkinter import ttk

from kandu import core_parsers as store

GREEN = "#1b4b43"
PAPER = "#fffdf8"
BG = "#f3f4ef"
FAMILY = "Microsoft YaHei UI"


def show_recent(parent, app, metas, article_indices=None):
    current = getattr(parent, "_recent_window", None)
    if current is not None and current.winfo_exists():
        current._recent_metas = metas
        current._recent_indices = article_indices
        current.deiconify()
        current.lift()
        current._refresh_recent()
        current._recent_text.focus_set()
        return current

    win = tk.Toplevel(parent)
    parent._recent_window = win
    win.title("最近阅读 · 刊读")
    win.geometry("880x720")
    win.minsize(520, 400)
    win.configure(bg=BG)
    tk.Label(win, text="最近阅读", bg=GREEN, fg="white", anchor="w", padx=24, pady=14,
             font=(FAMILY, 16, "bold")).pack(fill="x")
    tk.Label(win, text="按最近阅读时间排列 · 点击文章继续上次阅读", bg=BG, fg="#738079",
             anchor="w", padx=24, pady=10, font=(FAMILY, 10)).pack(fill="x")
    footer = tk.Frame(win, bg=BG, padx=24, pady=10)
    footer.pack(side="bottom", fill="x")
    status = tk.Label(footer, bg=BG, fg="#738079", font=(FAMILY, 9))
    status.pack(side="left")
    page = tk.Frame(win, bg=PAPER)
    page.pack(fill="both", expand=True, padx=24)
    page.columnconfigure(0, weight=1)
    page.rowconfigure(0, weight=1)
    txt = tk.Text(page, wrap="word", padx=18, pady=12, bg=PAPER, fg="#2d3e36", relief="flat",
                  highlightthickness=0, font=(FAMILY, 11), cursor="arrow", selectbackground="#d2e2d3")
    txt.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(page, command=txt.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    txt.configure(yscrollcommand=scrollbar.set)
    txt.tag_configure("title", foreground=GREEN, font=(FAMILY, 12, "bold"), spacing1=10, spacing3=5)
    txt.tag_configure("english", foreground="#536e62", font=("Georgia", 11), spacing3=7)
    txt.tag_configure("meta", foreground="#738079", font=(FAMILY, 9), spacing3=16)
    win._recent_text = txt
    win._recent_items = []
    win._recent_shown = 0
    win._recent_metas = metas
    win._recent_indices = article_indices

    def wheel(event):
        if event.delta:
            txt.yview_scroll(-max(1, abs(event.delta) // 120) * (1 if event.delta > 0 else -1) * 3, "units")
        return "break"

    txt.bind("<MouseWheel>", wheel)

    def open_article(meta, index):
        # 关闭列表后再切换页面，避免父页面销毁时留下悬空窗口。
        win.destroy()
        app.open_reader(meta, index)

    def append_rows():
        anchor = txt.index("@0,0")
        txt.configure(state="normal")
        end = min(win._recent_shown + 50, len(win._recent_items))
        for rank in range(win._recent_shown, end):
            meta, index, record = win._recent_items[rank]
            tag = f"recent_article_{rank}"
            title = record.get("titleZh") or record["title"]
            txt.insert("end", title + "\n", ("title", tag))
            if record.get("titleZh") and record["titleZh"] != record["title"]:
                txt.insert("end", record["title"] + "\n", ("english", tag))
            location = "已到文末" if record["atEnd"] else (
                "文章开头" if record["atStart"] else
                f"第 {record['paragraphIndex'] + 1} / {record['paragraphCount']} 段 · {round(record['progress'] * 100)}%")
            try:
                when = datetime.fromtimestamp(record["lastReadAt"] / 1000).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError, OverflowError):
                when = ""
            txt.insert("end", f"{meta['fileName']}\n{location}  ·  {when}\n", "meta")
            txt.tag_bind(tag, "<Button-1>", lambda e, m=meta, i=index: open_article(m, i))
            txt.tag_bind(tag, "<Enter>", lambda e: txt.configure(cursor="hand2"))
            txt.tag_bind(tag, "<Leave>", lambda e: txt.configure(cursor="arrow"))
        win._recent_shown = end
        if not win._recent_items:
            txt.insert("end", "还没有文章阅读记录，打开一篇文章后会自动记录在这里。", "meta")
        txt.configure(state="disabled")
        txt.yview(anchor)
        status.configure(text=f"已显示 {end} / {len(win._recent_items)} 篇")
        more.configure(state="normal" if end < len(win._recent_items) else "disabled")

    more = tk.Button(footer, text="再显示 50 篇", command=append_rows, bg="#e5ebe2", fg=GREEN,
                     relief="flat", padx=12, pady=5, font=(FAMILY, 9), cursor="hand2")
    more.pack(side="right")
    win._recent_more = more

    def refresh():
        items = []
        for meta in {m["id"]: m for m in win._recent_metas}.values():
            for key, record in store.load_reading_history(meta["id"]).items():
                index = int(key)
                if win._recent_indices is None or index in win._recent_indices:
                    items.append((meta, index, record))
        items.sort(key=lambda item: (-item[2]["lastReadAt"], item[0]["id"], item[1]))
        win._recent_items = items
        win._recent_shown = 0
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        for tag in txt.tag_names():
            if str(tag).startswith("recent_article_"):
                txt.tag_delete(tag)
        append_rows()
        txt.yview_moveto(0)

    win._refresh_recent = refresh
    refresh()

    def activate(event):
        if event.widget is win:
            win.unbind("<Map>", binding)
            win.after_idle(txt.focus_set)

    binding = win.bind("<Map>", activate, add="+")
    return win
