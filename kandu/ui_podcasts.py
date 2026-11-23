"""Transcript imports and a single native shelf directory for each Podcast."""
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from kandu import podcasts, podcast_library, article_tags, core_llm as llm
from kandu import core_config as config, core_parsers as store, reading_stats
from kandu.ui_digest import DigestFrame
from kandu.reading_tasks import WRITER

_IMPORT_LOCK = store.LIBRARY_LOCK

PAPER, GREEN, FAMILY = '#f3f4ef', '#1b4b43', 'Microsoft YaHei UI'


def show_import(app):
    existing = getattr(app, '_podcast_import_window', None)
    if existing is not None and existing.winfo_exists():
        existing.deiconify()
        existing.lift()
        return existing
    win = tk.Toplevel(app, bg=PAPER)
    app._podcast_import_window = win
    win.title('导入 Podcast Transcript · 刊读')
    win.geometry('700x440')
    win.minsize(600,400)
    win.events, win.control, win.running = queue.Queue(), llm.RequestControl(), False
    body = tk.Frame(win,bg=PAPER,padx=24,pady=20)
    body.pack(fill='both',expand=True)
    tk.Label(body,text='Lex Fridman Podcast',bg=PAPER,fg=GREEN,font=(FAMILY,18,'bold')).pack(anchor='w')
    tk.Label(body,text='仅保存官网 Transcript，并生成整期摘要；不会自动生成生词表。\n重复导入会补缺，停止任务会保留已经保存的内容。',
        bg=PAPER,fg='#738079',justify='left',font=(FAMILY,10)).pack(anchor='w',pady=14)
    row=tk.Frame(body,bg=PAPER)
    row.pack(fill='x')
    tk.Label(row,text='最近期数（1–50）',bg=PAPER,font=(FAMILY,10)).pack(side='left')
    count=tk.StringVar(win,'10')
    ttk.Spinbox(row,from_=1,to=50,textvariable=count,width=5).pack(side='left',padx=10)
    names=config.llm_names(app.settings)
    choice=ttk.Combobox(row,values=names,state='readonly',width=22)
    choice.set(config.default_llm_name(app.settings))
    choice.pack(side='right')
    tk.Label(body,text='或指定官网 Transcript 链接（每行一条，留空则导入最近节目）：',bg=PAPER,
        fg='#738079',font=(FAMILY,9)).pack(anchor='w',pady=(14,4))
    urls=tk.Text(body,height=3,font=(FAMILY,9),wrap='word')
    urls.pack(fill='x')
    status=tk.Label(body,text='等待导入',bg=PAPER,fg=GREEN,wraplength=640,justify='left',anchor='w',font=(FAMILY,10))
    status.pack(fill='x',pady=14)
    progress=ttk.Progressbar(body,mode='indeterminate')
    progress.pack(fill='x')
    actions=tk.Frame(body,bg=PAPER)
    actions.pack(fill='x',pady=(14,0))
    def start():
        if win.running:
            return
        cfg=dict(config.get_llm(app.settings,choice.get()))
        if error:=llm.validate(cfg):
            status.configure(text=error)
            return
        try:
            n=int(count.get())
            if not 1<=n<=50:
                raise ValueError()
            selected=[s.strip() for s in urls.get('1.0','end').splitlines() if s.strip()] or None
            podcasts.validate_selection(podcasts.source('lex-fridman'),selected)
        except ValueError as error:
            status.configure(text=str(error) or '请输入 1–50 的整数')
            return
        win.control=llm.RequestControl()
        win.running=True
        start_button.configure(state='disabled')
        progress.start(30)
        control=win.control
        def worker():
            try:
                report=podcasts.import_source('lex-fridman',n,cfg,
                    lambda **v:win.events.put(('progress',v)),control,transcript_urls=selected,
                    data_lock=_IMPORT_LOCK)
                win.events.put(('done',report['status']))
            except Exception as error:
                win.events.put(('done',str(error)))
        threading.Thread(target=worker,daemon=True).start()
    def poll():
        try:
            for _ in range(25):
                kind,value=win.events.get_nowait()
                status.configure(text=value.get('status','') if kind=='progress' else value)
                if kind=='done':
                    win.running=False
                    progress.stop()
                    start_button.configure(state='normal')
                    app.emit('podcast_imported')
        except queue.Empty:
            pass
        win.after(100,poll)
    start_button=ttk.Button(actions,text='开始 / 补缺',command=start)
    start_button.pack(side='left')
    ttk.Button(actions,text='停止',command=lambda:win.control.cancel()).pack(side='left',padx=10)
    ttk.Button(actions,text='返回书库',command=app.show_library).pack(side='right')
    def close():
        if win.running:
            win.withdraw()
        else:
            win.destroy()
    win.protocol('WM_DELETE_WINDOW',close)
    win.after(100,poll)
    return win


class PodcastSeriesFrame(DigestFrame):
    def __init__(self,parent,app,meta):
        self._series_poll = None
        self._series_job = None
        self._series_initializing=True
        self._episodes=podcast_library.directory(meta)
        super().__init__(parent,app,meta)
        self._series_initializing=False
        self.gen_btn.configure(text='补全节目摘要')
        self._refresh_directory()
        existing = getattr(app, '_podcast_series_jobs', {}).get(meta['sourceId'])
        if existing and existing['running']:
            self._series_job = existing
            self.generating = True
            self.gen_btn.configure(state='disabled')
            self._poll_series()

    def _refresh_directory(self):
        self._episodes=podcast_library.directory(self.meta)
        self._states={str(a['index']):a['state'] for a in self._episodes}
        self._reading_history={str(a['index']):a['history'] for a in self._episodes if a.get('history')}
        self._fill(dict(articles=self._episodes))

    def _directory_insights_worker(self,articles):
        self.app.emit('directory_insights',({}, {str(a['index']):a.get('tags',[]) for a in articles}))

    def _target(self,index):
        article=next(a for a in self._episodes if a['index']==index)
        return store.get_book(article['bookId']), article['articleIndex']

    def _open(self,index):
        meta,article_index=self._target(index)
        if meta:
            self.app.open_reader(meta,article_index)

    def _mark(self,index,state):
        meta,article_index=self._target(index)
        if meta is None:
            return
        store.set_article_state(meta['id'],article_index,state)
        WRITER.submit(reading_stats.mark_cached,meta,article_index)
        self._states[str(index)]=state
        for article in self._episodes:
            if article['index']==index:
                article['state']=state
        card=self._card_cache.get(index)
        if card:
            self._update_card_state(card,state)
        self._render(keep_scroll=True)

    def _edit_tags(self,index):
        meta,article_index=self._target(index)
        old=article_tags.load(meta['id']).get(str(article_index),[])
        value=simpledialog.askstring('节目标签','用逗号分隔，最多 8 个标签。',initialvalue='，'.join(old),parent=self)
        if value is None:
            return
        try:
            article_tags.save(meta['id'],article_index,[v.strip() for v in value.replace('，',',').split(',') if v.strip()])
            self._refresh_directory()
        except ValueError as error:
            self.status.configure(text=str(error))

    def _render_recent(self):
        super()._render_recent()
        if hasattr(self,'recent_more_btn'):
            self.recent_more_btn.configure(command=self._recent_episodes)

    def _recent_episodes(self):
        from kandu.ui_recent import show_recent
        return show_recent(self,self.app,podcast_library.episodes(self.meta['sourceId']))

    def generate(self):
        if self._series_initializing:
            return
        if self.generating:
            return
        if not hasattr(self.app, '_podcast_series_jobs'):
            self.app._podcast_series_jobs = {}
        jobs = self.app._podcast_series_jobs
        current = jobs.get(self.meta['sourceId'])
        if current and current['running']:
            self._series_job = current
            self.generating = True
            self.gen_btn.configure(state='disabled')
            self._poll_series()
            return
        cfg=dict(config.get_llm(self.app.settings,self.llm_choice.get() if hasattr(self,'llm_choice') else None))
        if error:=llm.validate(cfg):
            self.status.configure(text=error)
            return
        self.generating=True
        self.gen_btn.configure(state='disabled')
        self._series_job = dict(running=True,control=llm.RequestControl(),status='准备补全节目摘要',failed=0)
        jobs[self.meta['sourceId']] = self._series_job
        threading.Thread(target=self._series_worker,args=(cfg,self._series_job,self.meta['sourceId']),daemon=True).start()
        self._poll_series()

    @staticmethod
    def _series_worker(cfg,job,source_id):
        failed=0
        try:
            entries=podcast_library.episodes(source_id)
            for i,meta in enumerate(entries):
                job['control'].check()
                try:
                    job['control'].check()
                    podcasts.generate_digest(meta,cfg,lambda **v:job.update(status=v['status']),job['control'])
                except llm.RequestCancelled:
                    raise
                except Exception:
                    failed+=1
                job.update(status=f"摘要 {i+1}/{len(entries)} · 失败 {failed} · {cfg.get('name','默认')} / {cfg.get('model','')}")
        except llm.RequestCancelled:
            job['status']='已停止补全，已生成的摘要保留'
        except Exception as error:
            failed+=1
            job['status']='补全失败：'+str(error)
        finally:
            job.update(failed=failed,running=False)

    def _poll_series(self):
        self._series_poll = None
        job = self._series_job
        self.status.configure(text=job['status'])
        if job['running']:
            self._series_poll = self.after(100,self._poll_series)
        else:
            self.on_series_done(job['failed'])
            if job['control'].event.is_set():
                self.status.configure(text=job['status'])

    def destroy(self):
        if self._series_job and self._series_job['running']:
            self._series_job['control'].cancel()
        if self._series_poll is not None:
            self.after_cancel(self._series_poll)
        super().destroy()

    def on_series_progress(self,text):
        self.status.configure(text=text)

    def on_series_done(self,failed):
        self.generating=False
        self.gen_btn.configure(state='normal')
        self._refresh_directory()
        self.status.configure(text=f'节目摘要已补全 · 失败 {failed} 期（可再次补缺）')
