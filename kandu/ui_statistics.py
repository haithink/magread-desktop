"""Native reading dashboard, using the same accounting rules as Web."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from kandu import reading_stats, core_parsers as store
from kandu.reading_tasks import WRITER

PAPER, GREEN, INK, MUTED = '#f3f4ef', '#1b4b43', '#2d3e36', '#79847c'
FAMILY = 'Microsoft YaHei UI'


def show_statistics(app):
    existing = getattr(app, '_statistics_window', None)
    if existing is not None and existing.winfo_exists():
        existing.deiconify()
        existing.lift()
        existing.refresh()
        return existing
    app._statistics_window = StatisticsDialog(app)
    return app._statistics_window


class StatisticsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app, bg=PAPER)
        self.app, self.events = app, queue.Queue()
        self.title('阅读统计 · 刊读')
        self.geometry('1080x820')
        self.minsize(780, 600)
        self.pending = False
        bar = tk.Frame(self, bg=GREEN, padx=22, pady=14)
        bar.pack(fill='x')
        tk.Label(bar, text='每一段阅读，都在积累', font=(FAMILY, 17, 'bold'), bg=GREEN, fg='white').pack(side='left')
        ttk.Button(bar, text='刷新统计', command=self.refresh).pack(side='right')
        self.overview = tk.Label(self, bg=PAPER, fg=GREEN, font=(FAMILY, 13), anchor='w', justify='left')
        self.overview.pack(fill='x', padx=22, pady=14)
        self.note = tk.Label(self, bg=PAPER, fg=MUTED, anchor='w', justify='left', font=(FAMILY, 9),
            text='按标题去重 · 已读文章计入全文，未读完文章计入有效位置采样 · 跳转不计阅读量\n速度按有效计时计算，至少 30 秒及 20 词才显示；历史记录没有计时则显示 —。')
        self.note.pack(fill='x', padx=22, pady=(0, 10))
        tabs = ttk.Notebook(self)
        tabs.pack(fill='both', expand=True, padx=22, pady=(0, 18))
        trends, completed, sources = (tk.Frame(tabs, bg='white') for _ in range(3))
        tabs.add(trends, text='每日阅读趋势')
        tabs.add(completed, text='已读文章')
        tabs.add(sources, text='阅读来源')
        self.chart = tk.Canvas(trends, bg='white', highlightthickness=0)
        self.chart.pack(fill='both', expand=True)
        self.chart.bind('<Configure>', lambda e: self._draw())
        self.tree = self._table(completed, [('title','文章标题',420), ('words','总词数',85),
            ('source','出处',230), ('minutes','有效分钟',80), ('speed','词/分钟',80)])
        self.tree.bind('<Double-1>', self._open_article)
        self.source_tree = self._table(sources, [('source','阅读来源',390), ('words','已读词数（估算）',160),
            ('complete','已完成篇数',110), ('partial','阅读中篇数',110)])
        self.value, self.articles = None, []
        self._poll_after = self.after(80, self._poll)
        self.refresh()

    def _table(self, parent, columns):
        tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show='headings', selectmode='browse')
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=60, anchor='w' if key in ('title','source') else 'e')
        scroll = ttk.Scrollbar(parent, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True)
        return tree

    def refresh(self):
        if self.pending:
            return
        self.pending = True
        reader = getattr(self.app, 'current', None)
        flush = getattr(reader, '_sample_clock', None)
        if flush:
            flush(force=True)
        self.overview.configure(text='正在汇总本机阅读记录…')
        def worker():
            try:
                offset = time.altzone if time.daylight and time.localtime().tm_isdst else time.timezone
                self.events.put(('done', reading_stats.summary(days=30, timezone_offset=offset // 60)))
            except Exception as error:
                self.events.put(('error', str(error)))
        # Queued after reader flushes, including readers just closed by navigation.
        WRITER.submit(worker)

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                self.pending = False
                if kind == 'error':
                    self.overview.configure(text='统计加载失败：' + value)
                else:
                    self._render(value)
        except queue.Empty:
            pass
        self._poll_after = self.after(80, self._poll)

    def destroy(self):
        self.after_cancel(self._poll_after)
        super().destroy()

    def _render(self, value):
        self.value, self.articles = value, value['articles']
        analytics = value['analytics']
        volume, timing = analytics['readingVolume'], analytics['totals']
        self.overview.configure(text=f"累计实际阅读 {volume['totalWords']:,} 词     已完成 {value['articleCount']} 篇 / {value['totalWords']:,} 词\n"
            f"有效阅读 {timing['activeMs']/60000:.1f} 分钟     连续 {analytics['currentStreak']} 天     最长 {analytics['longestStreak']} 天")
        self.tree.delete(*self.tree.get_children())
        for i, article in enumerate(self.articles):
            t = article['timing']
            self.tree.insert('', 'end', iid=str(i), values=(article['title'], f"{article['wordCount']:,}",
                ' / '.join(dict.fromkeys(s['source'] for s in article['sources'])),
                f"{t['activeMs']/60000:.1f}" if t['activeMs'] else '—', t['wordsPerMinute'] or '—'))
        self.source_tree.delete(*self.source_tree.get_children())
        for source in analytics['readingSources']:
            self.source_tree.insert('', 'end', values=(source['name'], f"{source['words']:,}",
                source['completedArticleCount'], source['unfinishedArticleCount']))
        self._draw()

    def _draw(self):
        c = self.chart
        c.delete('all')
        if not self.value:
            return
        data = self.value['analytics']['daily']
        width, height = max(500, c.winfo_width()), max(280, c.winfo_height())
        for row, (field, title, color) in enumerate([
                ('measuredWords', '每日有效采样词数', '#6c9278'), ('wordsPerMinute', '每日阅读速度 · 词/分钟', '#387d91')]):
            top = 32 + row * height / 2
            bottom = top + height / 2 - 84
            left, right = 64, width - 26
            maximum = max([v[field] or 0 for v in data] + [1])
            c.create_text(18, top-16, text=title, anchor='w', fill=GREEN, font=(FAMILY, 11, 'bold'))
            c.create_line(left, top+8, left, bottom, right, bottom, fill='#dfe5da')
            c.create_text(left-8, top+8, text=f'{maximum:,}', anchor='e', fill=MUTED)
            c.create_text(left-8, bottom, text='0', anchor='e', fill=MUTED)
            step = (right-left) / max(1,len(data))
            points = []
            for i, value in enumerate(data):
                x = left + (i+.5)*step
                if value[field] is not None:
                    y = bottom - value[field]/maximum * max(1,bottom-top-8)
                    if row == 0:
                        c.create_rectangle(x-step*.34, y, x+step*.34, bottom, fill=color, outline='')
                    else:
                        points.extend([x,y])
                        c.create_oval(x-2,y-2,x+2,y+2,fill=color,outline='')
                elif len(points) >= 4:
                    c.create_line(*points,fill=color,width=2)
                    points=[]
                else:
                    points=[]
                if i % 7 == 0 or i == len(data)-1:
                    c.create_text(x,bottom+16,text=value['date'][5:],fill=MUTED,font=(FAMILY,8))
            if len(points) >= 4:
                c.create_line(*points, fill=color, width=2)

    def _open_article(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        article = self.articles[int(selection[0])]
        for source in article['sources']:
            meta = store.get_book(source.get('bookId',''))
            if meta:
                self.app.open_reader(meta, source['articleIndex'])
                self.withdraw()
                return
