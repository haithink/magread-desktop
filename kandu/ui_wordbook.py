"""个人生词本：可搜索的词条列表与编辑面板，首页及阅读页共用。"""
import tkinter as tk
from tkinter import ttk, messagebox

from . import core_wordbook as words, core_dict
from .ui_vocabulary import FAMILY, GREEN, INK, MUTED, PAPER, BG, button


def show_wordbook(app, meta=None, article=None, query='', initial=None):
    win = getattr(app, '_wordbook_window', None)
    if win is None or not win.winfo_exists():
        win = WordbookWindow(app)
        app._wordbook_window = win
    win.set_context(meta, article)
    win.query.set(query)
    win.refresh()
    if initial:
        record = words.get(initial['word'])
        if record:
            win.select_word(record['word'])
        else:
            win.new_entry(initial)
    win.deiconify()
    win.lift()
    win.search.focus_set()
    return win


def notify_changed(app):
    """主线程更新打开的管理窗与当前文章，不调用模型。"""
    manager = getattr(app, '_wordbook_window', None)
    if manager is not None and manager.winfo_exists():
        manager.refresh()
    current = getattr(app, 'current', None)
    callback = getattr(current, 'on_wordbook_changed', None)
    if callback:
        callback()


class WordbookWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title('个人生词本 · 刊读')
        self.geometry('1060x740')
        self.minsize(800, 570)
        self.configure(bg=BG)
        self.meta = self.article = None
        self.original = None
        self.rows = {}
        self._search_after = None
        self._loading = False
        self._dirty = False
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Destroy>', self._destroyed, add='+')

        brand = tk.Frame(self, bg=GREEN, padx=24, pady=12)
        brand.pack(fill='x')
        tk.Label(brand, text='个人生词本', bg=GREEN, fg='white', font=(FAMILY, 17, 'bold')).pack(side='left')
        tk.Label(brand, text='MY WORDS', bg=GREEN, fg='#bdd0c5', font=(FAMILY, 9)).pack(side='left', padx=18)
        self.count = tk.Label(brand, bg=GREEN, fg='#dce9df', font=(FAMILY, 10))
        self.count.pack(side='right')
        controls = tk.Frame(self, bg=BG, padx=22, pady=14)
        controls.pack(fill='x')
        tk.Label(controls, text='搜索', bg=BG, fg=MUTED, font=(FAMILY, 10)).pack(side='left', padx=(0, 10))
        self.query = tk.StringVar(self)
        self.search = tk.Entry(controls, textvariable=self.query, width=26, font=(FAMILY, 11), relief='flat', bg=PAPER, fg=INK)
        self.search.pack(side='left', fill='x', expand=True, ipady=6)
        self.search.bind('<Escape>', lambda e: self.query.set(''))
        self.query.trace_add('write', self._queue_search)
        button(controls, '清空搜索', lambda: self.query.set('')).pack(side='left', padx=8)
        self.only_article = tk.BooleanVar(self, False)
        self.scope_button = tk.Checkbutton(controls, text='仅本篇出现', variable=self.only_article, command=self.refresh,
                                          bg=BG, fg=GREEN, activebackground=BG, font=(FAMILY, 9))
        self.scope_button.pack(side='left', padx=6)
        button(controls, '+ 新增单词', self.new_entry).pack(side='right', padx=(8, 0))

        body = tk.PanedWindow(self, orient='horizontal', bg='#dce3d7', bd=0, sashwidth=5)
        body.pack(fill='both', expand=True, padx=22)
        left = tk.Frame(body, bg=PAPER)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, columns=('word', 'sense'), show='headings', selectmode='browse')
        self.tree.heading('word', text='单词')
        self.tree.heading('sense', text='释义')
        self.tree.column('word', width=140, minwidth=85)
        self.tree.column('sense', width=205, minwidth=100)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(left, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<<TreeviewSelect>>', self._selected)
        self.tree.bind('<Delete>', lambda e: self.delete_entry())
        body.add(left, minsize=240, width=370)

        editor = tk.Frame(body, bg=PAPER, padx=20, pady=14)
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)
        self.fields = {key: tk.StringVar(self) for key in ('word', 'phonetic', 'pos')}
        for row, label, key in ((0, '单词', 'word'), (1, '音标', 'phonetic'), (2, '词性', 'pos')):
            tk.Label(editor, text=label, bg=PAPER, fg=MUTED, font=(FAMILY, 10)).grid(row=row, column=0, sticky='w', pady=5, padx=(0, 12))
            entry = tk.Entry(editor, textvariable=self.fields[key], bg='#f2f5ee', fg=INK, relief='flat', font=(FAMILY, 11))
            entry.grid(row=row, column=1, columnspan=3, sticky='ew', ipady=5, pady=5)
            if key == 'word':
                self.word_input = entry
            self.fields[key].trace_add('write', self._edited)
        self.texts = {}
        for row, label, key, height in ((3, '释义', 'sense', 4), (4, '词根词源', 'root', 2), (5, '学习笔记', 'note', 3)):
            tk.Label(editor, text=label, bg=PAPER, fg=MUTED, font=(FAMILY, 10)).grid(row=row, column=0, sticky='nw', pady=8)
            frame = tk.Frame(editor, bg=PAPER)
            frame.grid(row=row, column=1, columnspan=3, sticky='nsew', pady=5)
            txt = tk.Text(frame, height=height, width=20, wrap='word', bg='#f2f5ee', fg=INK, bd=0,
                          highlightthickness=0, font=(FAMILY, 10), padx=8, pady=6, undo=True)
            txt.pack(side='left', fill='both', expand=True)
            bar = ttk.Scrollbar(frame, command=txt.yview)
            bar.pack(side='right', fill='y')
            txt.configure(yscrollcommand=bar.set)
            txt.bind('<<Modified>>', self._text_edited)
            self.texts[key] = txt
        editor.rowconfigure(3, weight=1)
        editor.rowconfigure(5, weight=1)
        self.source = tk.Label(editor, text='右键查词会自动收录；也可在这里手动新增。', bg=PAPER, fg=MUTED,
                               font=(FAMILY, 9), justify='left', anchor='w', wraplength=400)
        self.source.grid(row=6, column=0, columnspan=4, sticky='ew', pady=10)
        actions = tk.Frame(editor, bg=PAPER)
        actions.grid(row=7, column=0, columnspan=4, sticky='ew', pady=(5, 0))
        button(actions, '词典补全', self.fill_dictionary).pack(side='left')
        button(actions, '删除此词', self.delete_entry).pack(side='left', padx=8)
        button(actions, '保存词条', self.save_entry).pack(side='right')
        body.add(editor, minsize=430)
        self.status = tk.Label(self, text='可搜索单词、释义和学习笔记。', bg=BG, fg=MUTED,
                               font=(FAMILY, 9), anchor='w', padx=22, pady=12)
        self.status.pack(fill='x')
        self.bind('<Control-s>', lambda e: self.save_entry())

    def set_context(self, meta, article):
        self.meta, self.article = meta, article
        self.scope_button.configure(state='normal' if article else 'disabled')
        if not article:
            self.only_article.set(False)

    def _queue_search(self, *_):
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(150, self.refresh)

    def _edited(self, *_):
        if not self._loading:
            self._dirty = True

    def _text_edited(self, event):
        if event.widget.edit_modified():
            self._edited()
            event.widget.edit_modified(False)

    def refresh(self):
        self._search_after = None
        all_rows = words.list_words()
        query = self.query.get().strip().casefold()
        current = None
        if self.only_article.get() and self.article:
            current = {r['word'] for group in words.article_matches(self.article, all_rows).values() for r in group}
        self._loading = True
        try:
            self.rows = {r['word']: r for r in all_rows}
            if self.tree.get_children():
                self.tree.delete(*self.tree.get_children())
            for r in all_rows:
                if current is not None and r['word'] not in current:
                    continue
                if query and query not in '\n'.join([r['word'], r['sense'], r['note'], *r['aliases']]).casefold():
                    continue
                self.tree.insert('', 'end', iid=r['word'], values=(r['word'], r['sense'].replace('\n', ' ')))
            shown = len(self.tree.get_children())
            self.count.configure(text=f'{shown} / {len(all_rows)} 个词')
            if self.original and self.tree.exists(self.original):
                self.tree.selection_set(self.original)
            if not shown:
                self.status.configure(text='没有匹配词条。右键查词后会自动加入，也可以点击「新增单词」。')
        finally:
            self._loading = False

    def _confirm_discard(self):
        return not self._dirty or messagebox.askyesno('尚未保存', '放弃当前词条尚未保存的修改？', parent=self)

    def _selected(self, _=None):
        selected = self.tree.selection()
        if self._loading or not selected or selected[0] == self.original:
            return
        if not self._confirm_discard():
            if self.original and self.tree.exists(self.original):
                self.tree.selection_set(self.original)
            return
        self._fill(self.rows[selected[0]], selected[0])

    def select_word(self, word):
        if not self._confirm_discard():
            return
        if self.tree.exists(word):
            self.tree.selection_set(word)
            self.tree.see(word)
        self._fill(self.rows[word], word)

    def _fill(self, record, original=None):
        self._loading = True
        self.original = original
        self._source_data = record.get('source', {})
        for key, var in self.fields.items():
            var.set(record.get(key, ''))
        for key, txt in self.texts.items():
            txt.delete('1.0', 'end')
            txt.insert('1.0', record.get(key, ''))
            txt.edit_modified(False)
            txt.edit_reset()
        source = self._source_data
        info = ' · '.join(filter(None, [source.get('bookTitle'), source.get('articleTitle'),
                              f"第 {source['paragraph'] + 1} 段" if source.get('paragraph', -1) >= 0 else '']))
        self.source.configure(text=('最近来源：' + info + '\n' + source.get('context', ''))[:350] if info else '手动收录 · 可填写释义和自己的学习笔记')
        self._loading = self._dirty = False
        self.status.configure(text='词条已载入，可修改后保存。' if original else '填写英文单词和释义，然后保存。')

    def new_entry(self, value=None):
        if not self._confirm_discard():
            return
        if self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())
        self._fill(value or {})
        self.word_input.focus_set()

    def save_entry(self):
        value = {key: var.get() for key, var in self.fields.items()}
        value.update({key: txt.get('1.0', 'end-1c') for key, txt in self.texts.items()})
        value['source'] = getattr(self, '_source_data', {})
        try:
            if self.original is None and words.get(value['word']):
                raise ValueError('该单词已在生词本中，请选择已有词条编辑')
            record = words.save(value, self.original)
        except (ValueError, OSError) as exc:
            messagebox.showerror('保存生词', str(exc), parent=self)
            return
        self._fill(record, record['word'])
        notify_changed(self.app)
        self.status.configure(text=f"已保存「{record['word']}」，阅读中的文章生词表已同步。")

    def delete_entry(self):
        if not self.original:
            return
        word = self.original
        if not messagebox.askyesno('删除生词', f'从个人生词本删除「{word}」？\n文章中原有的模型生词释义仍保留。', parent=self):
            return
        words.delete(word)
        self._fill({})
        notify_changed(self.app)
        self.status.configure(text=f'已删除「{word}」。')

    def fill_dictionary(self):
        try:
            word = words.normalize(self.fields['word'].get())
        except ValueError as exc:
            self.status.configure(text=str(exc))
            return
        core_dict.ensure_db(background=True)
        hit = core_dict.lookup(word)
        if hit and not hit.get('miss'):
            for key in ('phonetic', 'pos'):
                if not self.fields[key].get():
                    self.fields[key].set(hit.get(key, ''))
            if not self.texts['sense'].get('1.0', 'end-1c').strip():
                self.texts['sense'].insert('1.0', hit.get('translation', '').replace('\\n', '\n'))
            self.status.configure(text='已补充空白字段，点击保存后生效。')
        else:
            self.status.configure(text='词典正在初始化，稍后再试。' if not core_dict.STATE['ready'] else '本地词典未收录，可手动填写释义。')

    def close(self):
        if self._confirm_discard():
            self.destroy()

    def _destroyed(self, event):
        if event.widget is self and self._search_after:
            self.after_cancel(self._search_after)
            self._search_after = None
