"""Desktop adapters for contextual explanations, vocabulary hints and activity."""
import queue
import re
import threading
import time
import tkinter as tk

from kandu import core_config as config, core_gen as gen, core_llm as llm
from kandu import core_parsers as store, core_sentence, core_dict, core_wordbook as words
from kandu import word_meaning, reading_activity, reading_stats
from kandu.core_reading_clock import ReadingClock
from kandu.reading_tasks import WRITER
from kandu.ui_analysis import make_sheet, render_loading, render_analysis
from kandu.ui_vocabulary import render_lookup, button
from kandu.ui_wordbook import notify_changed

PAPER, GREEN, INK, MUTED = '#f3f4ef', '#1b4b43', '#2d3e36', '#79847c'
FAMILY = 'Microsoft YaHei UI'


class ReadingTools:
    def _init_reading_tools(self, footer):
        self._tools_closed = False
        self._tool_events = queue.Queue()
        self._tool_after = self._clock_after = self._highlight_after = self._hover_after = None
        self._hover_guard_after = None
        self._hover_window = None
        self._hover_target = None
        self._highlight_hits = {}
        self._analysis_requests = {}
        self._lookup_controls = set()
        self._focus_after = None
        self._clock = ReadingClock()
        self._clock_writer = WRITER
        self._clock_ready = False
        self._clock_navigation = False
        self._clock_last_sample = time.monotonic()
        self._clock_input = time.monotonic()
        self._clock_paused = False
        self._clock_completed = store.load_article_states(self.meta['id']).get(str(self.article_index)) == 'read'
        self._clock_rereading = False
        self.clock_label = tk.Label(footer, text='计时 00:00', bg=PAPER, fg=MUTED, font=(FAMILY, 9))
        self.clock_label.grid(row=1, column=0, sticky='w', pady=(5,0))
        self.pause_btn = self._button(footer, '暂停计时', self._toggle_clock, bg=PAPER, fg=GREEN, pady=2)
        self.pause_btn.grid(row=1, column=1, sticky='e')
        self.complete_btn = self._button(footer, '已读 · 停止计时' if self._clock_completed else '标记已读',
            self._toggle_complete, bg=PAPER, fg=GREEN, pady=2)
        self.complete_btn.grid(row=1, column=2, columnspan=2, sticky='e')
        for sequence in ('<Motion>', '<KeyPress>', '<ButtonPress>', '<MouseWheel>'):
            self.text.bind(sequence, self._reading_input, add='+')
        self.text.bind('<Motion>', self._hover_motion, add='+')
        self.text.bind('<Button-1>', self._highlight_click, add='+')
        # Scrollbar drags and searches establish a baseline, never read the gap.
        self.vbar.bind('<ButtonPress-1>', lambda e:self._reading_navigation(), add='+')
        self.vbar.configure(command=self._scrollbar_navigation)
        self._poll_reading_tools()
        self._tick_reading_clock()

    def _reading_input(self, event=None):
        self._clock_input = time.monotonic()

    def _reading_navigation(self):
        self._clock_navigation = True
        self._reading_input()

    def _scrollbar_navigation(self, *args):
        self._reading_navigation()
        self.text.yview(*args)

    def _clock_active(self):
        root = self.winfo_toplevel()
        focus = root.focus_displayof()
        # Sentence/word aids are part of reading comprehension, like Web.
        top = focus.winfo_toplevel() if focus is not None else None
        foreground = top is root or (top is not None and (top.master is self or getattr(top,'_reading_owner',None) is self))
        return (self._clock_ready and foreground and (not self._clock_completed or self._clock_rereading)
            and not self._clock_paused and not self.vocab_running
            and time.monotonic()-self._clock_input < 120 and root.state() != 'iconic')

    def _sample_clock(self, force=False):
        if not self._clock_ready or self._tools_closed:
            return
        now = time.monotonic()
        if not force and now-self._clock_last_sample < 5:
            return
        position = self._capture_reading_position()
        if position is None:
            return
        sample = self._clock.sample(position, self._clock_navigation)
        self._clock_navigation = False
        self._clock_last_sample = now
        meta, article, events = self.meta, self.article, self._tool_events
        def write():
            try:
                reading_activity.record(meta, article, sample)
            except Exception as error:
                events.put(('clock_error', str(error)))
        self._clock_writer.submit(write)

    def _tick_reading_clock(self):
        self._clock_after = None
        if self._tools_closed:
            return
        self._clock.tick(self._clock_active())
        self._sample_clock()
        seconds = self._clock.elapsed//1000
        reason = ('已读停计时' if self._clock_completed and not self._clock_rereading else '已暂停' if self._clock_paused
            else '生成生词中 · 暂停' if self.vocab_running else '计时' if self._clock.running else '暂歇')
        self.clock_label.configure(text=f'{reason}  {seconds//60:02d}:{seconds%60:02d}')
        self.pause_btn.configure(text='重读计时' if self._clock_completed and not self._clock_rereading
            else '继续计时' if self._clock_paused else '暂停计时')
        self._clock_after = self.after(1000, self._tick_reading_clock)

    def _toggle_clock(self):
        self._clock.tick(self._clock_active())
        self._sample_clock(force=True)
        if self._clock_completed and not self._clock_rereading:
            self._clock_rereading = True
            self._clock_paused = False
        else:
            self._clock_paused = not self._clock_paused
        self._clock.running = False
        self._reading_input()
        self.pause_btn.configure(text='继续计时' if self._clock_paused else '暂停计时')

    def _toggle_complete(self):
        self._clock.tick(self._clock_active())
        self._sample_clock(force=True)
        completed = not self._clock_completed
        store.set_article_state(self.meta['id'], self.article_index, 'read' if completed else 'unread')
        self._clock_writer.submit(reading_stats.mark_web, self.meta, self.article, completed)
        self._clock_completed = completed
        self._clock_rereading = False
        self._clock.running = False
        self._reading_input()
        self.complete_btn.configure(text='已读 · 停止计时' if completed else '标记已读')
        self.pause_btn.configure(text='重读计时' if completed else '暂停计时')
        self.status.configure(text='已标记已读，全文词数计入统计；再次点击可改回未读。' if completed else '已改为未读，继续按实际阅读采样统计。')

    def _reading_tools_loaded(self):
        self._clock_ready = True
        self._clock.running = False
        self._clock_input = time.monotonic()
        self._sample_clock(force=True)
        self._queue_highlights()

    def _reading_tools_scroll(self):
        if hasattr(self, '_highlight_after') and not self._tools_closed:
            self._hide_hover()
            self._queue_highlights()

    def _close_reading_tools(self):
        if self._tools_closed:
            return
        self._clock.tick(self._clock_active())
        self._sample_clock(force=True)
        self._tools_closed = True
        self._hide_hover()
        for job in (self._tool_after, self._clock_after, self._highlight_after, self._hover_guard_after, self._focus_after):
            if job is not None:
                self.after_cancel(job)
        for request in self._analysis_requests.values():
            request['control'].cancel()
        for control in self._lookup_controls:
            control.cancel()

    def _poll_reading_tools(self):
        self._tool_after = None
        if self._tools_closed:
            return
        # Bound work per frame so streamed model events cannot starve Tk.
        for _ in range(25):
            try:
                kind, payload = self._tool_events.get_nowait()
            except queue.Empty:
                break
            try:
                if kind.startswith('sentence_'):
                    self._sentence_event(kind, payload)
                elif kind.startswith('word_'):
                    self._word_event(kind, payload)
                elif kind == 'clock_error':
                    self.status.configure(text='计时保存失败：' + payload)
            except Exception as error:
                self.app._handle_handler_error(error)
        self._tool_after = self.after(80, self._poll_reading_tools)

    # ---- Contextual sentence explanations and explicit second opinions ----
    def _start_analysis(self, para, sentence, *, start=None, cfg=None, force=False, deep=False):
        if self.article is None:
            return
        cfg = dict(cfg or config.get_llm(self.app.settings))
        try:
            context = core_sentence.context(self.article, para, sentence, start)
        except ValueError as error:
            self.status.configure(text=str(error))
            return
        key = core_sentence.identity(context, cfg)
        request = self._analysis_requests.get(key)
        if request and request['win'].winfo_exists():
            win = request['win']
            win.deiconify()
            win.lift()
            if request['busy'] or not (force or deep):
                return
        else:
            win, txt, bar = make_sheet(self, self.app, self.article['title'])
            self._track_aid_activity(win)
            request = dict(win=win, txt=txt, bar=bar, context=context, cfg=cfg, previous=None,
                busy=False, control=llm.RequestControl(), sentence=sentence, para=para)
            self._analysis_requests[key] = request
            win._reading_anchor = para
            request['status'] = tk.Label(bar, bg=GREEN, fg='#e1ece5', font=(FAMILY,9))
            request['status'].pack(side='left', padx=8)
            actions = tk.Frame(win, bg=PAPER)
            actions.pack(side='bottom', fill='x', before=bar, padx=24, pady=4)
            for label, review in [('重新讲解',False), ('深入复核',True)]:
                button(actions,label,lambda d=review:self._start_analysis(para,sentence,start=context['paragraphOffset']+context['selectedStart'],
                    cfg=cfg,force=True,deep=d)).pack(side='left',padx=(0,6))
            button(actions,'停止',lambda:self._cancel_sentence(key)).pack(side='right')
            def close():
                self._cancel_sentence(key)
                win.destroy()
            win.protocol('WM_DELETE_WINDOW',close)
        cached = store.load_analysis(self.meta['id'], sentence, self.article_index, key)
        if cached and not cached.get('partial') and not (force or deep):
            request['previous'] = cached
            render_analysis(request['txt'],cached,sentence)
            request['status'].configure(text='已加载上下文缓存 · ' + self._model_label(cfg))
            self._place_sentence(win, ready=True)
            return
        invalid = llm.validate(cfg)
        if invalid:
            request['status'].configure(text=invalid)
            return
        request.update(busy=True, control=llm.RequestControl(), started=time.monotonic(), last_partial=None)
        if request['previous'] is None:
            render_loading(request['txt'],sentence)
        request['status'].configure(text=('深入复核中…' if deep else '等待模型响应…')+' · '+self._model_label(cfg))
        self._place_sentence(win, ready=False)
        control, events, article, meta = request['control'], self._tool_events, self.article, self.meta
        def worker():
            last_preview, last_progress = None, 0
            def update(value):
                nonlocal last_progress
                now=time.monotonic()
                if not value.get('chars') or now-last_progress >= .15:
                    events.put(('sentence_progress',(key,control,value)))
                    last_progress=now
            def preview(value):
                nonlocal last_preview
                if value and value != last_preview:
                    events.put(('sentence_partial',(key,control,value)))
                    last_preview=value
            try:
                result=gen.analyze(meta,article,sentence,'','',cfg,on_progress=update,context=context,
                    force=force,deep=deep,on_partial=preview,control=control,cache_identity=key)
                events.put(('sentence_done',(key,control,result)))
            except Exception as error:
                events.put(('sentence_error',(key,control,str(error))))
        threading.Thread(target=worker,daemon=True).start()

    def _place_sentence(self, win, ready):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = min(920,sw-70), min(790,sh-100)
        if ready:
            win.geometry(f'{width}x{height}+{(sw-width)//2}+{max(0,(sh-height)//2-20)}')
            win.lift()
        else:
            # A compact nonmodal status window sits beside the reading column.
            win.minsize(360,220)
            width,height=min(440,sw-60),260
            x,y=max(0,sw-width-16),max(0,sh-height-70)
            paragraph=getattr(win,'_reading_anchor',None)
            ranges=self._para_range(paragraph) if paragraph is not None else None
            line=self.text.dlineinfo(ranges[0]) if ranges else None
            if line:
                line_y=self.text.winfo_rooty()+line[1]
                if y-24 <= line_y <= y+height+24:
                    y=max(0,line_y-height-40)
            win.geometry(f'{width}x{height}+{x}+{y}')
            if self._focus_after:
                self.after_cancel(self._focus_after)
            def focus():
                self._focus_after=None
                if not self._tools_closed:
                    self.text.focus_set()
            self._focus_after=self.after_idle(focus)

    @staticmethod
    def _model_label(cfg):
        return f"{cfg.get('name') or '默认'} / {cfg.get('model') or '未指定模型'}"

    def _cancel_sentence(self, key):
        request=self._analysis_requests.get(key)
        if request:
            request['control'].cancel()
            request['busy']=False
            if request['win'].winfo_exists():
                if request['previous']:
                    render_analysis(request['txt'],request['previous'],request['sentence'])
                request['status'].configure(text='已停止；已有讲解保留')

    def _sentence_event(self, kind, payload):
        key,control,value=payload
        request=self._analysis_requests.get(key)
        if not request or request['control'] is not control or control.event.is_set() or not request['win'].winfo_exists():
            return
        if kind == 'sentence_progress':
            text=f"已接收 {value['chars']:,} 字" if value['chars'] else '等待模型响应'
            request['status'].configure(text=f"{text} · {value['elapsedMs']/1000:.1f} 秒 · " + self._model_label(request['cfg']))
        elif kind == 'sentence_partial':
            if request['previous'] is None:
                render_analysis(request['txt'],value,request['sentence'])
        elif kind == 'sentence_done':
            request.update(previous=value,busy=False)
            render_analysis(request['txt'],value,request['sentence'])
            request['status'].configure(text=f"完成 · {value.get('generationStats',{}).get('elapsedMs',0)/1000:.1f} 秒 · " + self._model_label(request['cfg']))
            self._place_sentence(request['win'],ready=True)
        elif kind == 'sentence_error':
            request['busy']=False
            if request['previous']:
                render_analysis(request['txt'],request['previous'],request['sentence'])
            request['status'].configure(text='失败：'+value,wraplength=550)

    # ---- Offline dictionary immediately; context-specific AI explanation next ----
    def _lookup_selection(self, para, offset):
        if self.article is None:
            return
        ranges=self.text.tag_ranges('sel')
        if ranges:
            quote=self.text.get(*ranges).strip()
            para=self._para_at(ranges[0])
            pr=self._para_range(para) if para is not None else None
            offset=len(self.text.get(pr[0],ranges[0])) if pr else None
        elif para is not None and offset is not None:
            text=self.article['paragraphs'][para]
            hit=next((m for m in gen._TOKEN.finditer(text.replace('’',"'")) if m.start() <= offset < m.end()),None)
            quote=hit.group().strip("'-") if hit else ''
            offset=hit.start() if hit else None
        else:
            quote=''
        try:
            words.normalize(quote)
            if len(quote.split())>3:
                raise ValueError('长句请使用“解析选中内容”')
            context=word_meaning.selection(self.article,para,quote,offset)
        except (ValueError,TypeError) as error:
            self.status.configure(text=str(error) or '请选中单词或短语')
            return
        word=context['word']
        win,txt,bar=self._make_sheet('查词 · '+word,self.article['title'])
        render_lookup(txt,word,None)
        progress=tk.Label(bar,bg=PAPER,fg=MUTED,font=(FAMILY,9),text='正在查询词典与本文词义…')
        progress.pack(side='left',padx=6)
        control=llm.RequestControl()
        self._lookup_controls.add(control)
        view=dict(win=win,txt=txt,bar=bar,progress=progress,word=word,result=None,meaning=None,control=control)
        cfg=dict(config.get_llm(self.app.settings))
        view['modelLabel']=self._model_label(cfg)
        source=self._word_source(para)
        source.update(selectedWord=context['word'], selectedStart=context['selectedStart'])
        book_id=self.meta['id']
        self._track_aid_activity(win)
        events=self._tool_events
        button(bar,'查看 / 编辑生词本',lambda:self._open_wordbook(view.get('savedWord',word))).pack(side='right',padx=4)
        def remove():
            words.delete(view.get('savedWord',word))
            notify_changed(self.app)
            progress.configure(text='已移出生词本；后台释义不会重新收藏')
        button(bar,'移出生词本',remove).pack(side='right',padx=4)
        win.protocol('WM_DELETE_WINDOW',lambda:(control.cancel(),win.destroy()))
        def worker():
            try:
                control.check()
                core_dict.ensure_db(background=False)
                result=core_dict.lookup(word)
                control.check()
                saved=words.remember_lookup(word,result,source)
                events.put(('word_dictionary',(view,result,saved['word'])))
                if llm.validate(cfg):
                    events.put(('word_status',(view,'已收藏 · 未配置模型，使用离线词典')))
                    return
                meaning=word_meaning.explain(book_id,context,cfg,
                    lambda **v:events.put(('word_status',(view,v.get('status','')+' · '+self._model_label(cfg)))),control)
                control.check()
                words.attach_context(saved['word'],meaning,source)
                events.put(('word_meaning',(view,meaning,None)))
            except llm.RequestCancelled:
                pass
            except Exception as error:
                events.put(('word_status',(view,'本文词义未生成：'+str(error))))
            finally:
                events.put(('word_finished',(view,'')))
        threading.Thread(target=worker,daemon=True).start()

    def _word_event(self, kind, payload):
        view=payload[0]
        if kind == 'word_finished':
            self._lookup_controls.discard(view['control'])
        if not view['win'].winfo_exists() or view['control'].event.is_set():
            return
        if kind == 'word_dictionary':
            view.update(result=payload[1],savedWord=payload[2])
            view['progress'].configure(text='已收藏 · 正在结合上下文解释…')
            notify_changed(self.app)
        elif kind == 'word_meaning':
            view['meaning']=payload[1]
            view['progress'].configure(text='本文词义已缓存 · 已保存最近来源')
        elif kind == 'word_status':
            view['progress'].configure(text=payload[1],wraplength=480)
        label=view['modelLabel']
        if label not in view['progress'].cget('text'):
            view['progress'].configure(text=view['progress'].cget('text')+' · '+label)
        render_lookup(view['txt'],view['word'],view['result'])
        meaning=view['meaning']
        if meaning:
            txt=view['txt']
            txt.configure(state='normal')
            txt.insert('1.0',f"本文义  {meaning.get('pos','')}\n{meaning['sense']}\n{meaning['explanation']}\n\n"
                f"原句\n{meaning['sentence']}\n\n译文\n{meaning['translation']}\n\n",'body')
            txt.configure(state='disabled')
            txt.yview_moveto(0)

    # ---- Only tag vocabulary in and near the visible viewport ----
    def _track_aid_activity(self, win):
        for sequence in ('<Motion>', '<KeyPress>', '<ButtonPress>', '<MouseWheel>'):
            win.bind(sequence, self._reading_input, add='+')

    def _queue_highlights(self):
        if self._highlight_after is None and self.article:
            self._highlight_after=self.after(100,self._refresh_highlights)

    def _refresh_highlights(self):
        self._highlight_after=None
        if self._tools_closed or not self.article:
            return
        for tag in self._highlight_hits:
            self.text.tag_delete(tag)
        self._highlight_hits={}
        top=self._para_at(self.text.index('@0,0')) or 0
        bottom=self._para_at(self.text.index(f'@0,{self.text.winfo_height()}'))
        bottom=bottom if bottom is not None else top+3
        for para in range(max(0,top-1),min(len(self.article['paragraphs']),bottom+2)):
            text=self.article['paragraphs'][para]
            start=self._para_range(para)[0]
            tokens=list(re.finditer(r"[A-Za-z]+(?:['’][A-Za-z]+)?",text))
            forms=[set(gen.lemma_forms(m.group().lower().replace('’',"'"))) for m in tokens]
            for entry in self._entries_for(para):
                spellings=re.findall(r"[a-z]+(?:'[a-z]+)?",entry['word'].lower().replace('’',"'"))
                if not spellings:
                    continue
                for i, token in enumerate(tokens):
                    end=i+len(spellings)
                    if end>len(tokens) or not all(w in forms[i+j] for j,w in enumerate(spellings)):
                        continue
                    if any(not re.fullmatch(r'[\s\-–—]+',text[tokens[j-1].end():tokens[j].start()]) for j in range(i+1,end)):
                        continue
                    tag='vh_'+str(len(self._highlight_hits))
                    # Text's character offsets count a supplementary character once.
                    begin=token.start()
                    finish=tokens[end-1].end()
                    self.text.tag_add(tag,f'{start}+{begin}c',f'{start}+{finish}c')
                    self.text.tag_configure(tag,background='#eef1df',foreground='#3f5941')
                    self._highlight_hits[tag]=(para,entry)
        self.text.tag_raise('sel')

    def _highlight_at(self,event):
        for tag in self.text.tag_names(f'@{event.x},{event.y}'):
            if tag in self._highlight_hits:
                return tag,self._highlight_hits[tag]
        return None

    def _hover_motion(self,event):
        hit=self._highlight_at(event)
        target=hit[0] if hit else None
        if target == self._hover_target:
            return
        self._hide_hover()
        self._hover_target=target
        if hit:
            x,y=event.x_root,event.y_root
            self._hover_after=self.after(1000,lambda:self._show_hover(hit,x,y))

    def _highlight_click(self,event):
        hit=self._highlight_at(event)
        if hit:
            self._hide_hover()
            self._hover_target=hit[0]
            self._show_hover(hit,event.x_root,event.y_root)

    def _show_hover(self,hit,x,y):
        self._hover_after=None
        if self._tools_closed or hit[0] not in self._highlight_hits:
            return
        para,entry=hit[1]
        win=tk.Toplevel(self)
        self._hover_window=win
        win.overrideredirect(True)
        win.configure(bg='#d6dfd1',padx=1,pady=1)
        inner=tk.Frame(win,bg='#fffdf8',padx=14,pady=12)
        inner.pack()
        tk.Label(inner,text=entry['word']+'  '+(entry.get('phonetic') or ''),bg='#fffdf8',fg=GREEN,
            font=('Georgia',12,'bold'),anchor='w').pack(fill='x')
        tk.Label(inner,text=(entry.get('sense') or '可打开本段生词表查看')[:1800],bg='#fffdf8',fg=INK,
            font=(FAMILY,10),wraplength=330,justify='left').pack(fill='x',pady=8)
        button(inner,'加入 / 查看个人生词本',lambda:self._collect_vocab_entry(entry,para)).pack(side='left')
        button(inner,'×',self._hide_hover).pack(side='right',padx=(8,0))
        win.update_idletasks()
        win.geometry(f'+{max(0,min(x+12,self.winfo_screenwidth()-win.winfo_reqwidth()-16))}'
            f'+{max(0,min(y+24,self.winfo_screenheight()-win.winfo_reqheight()-60))}')
        self._hover_guard_after=self.after(200,self._guard_hover)

    def _guard_hover(self):
        self._hover_guard_after=None
        win=self._hover_window
        if win is None or not win.winfo_exists():
            return
        x,y=self.winfo_pointerxy()
        widget=self.winfo_containing(x,y)
        in_popup=widget is not None and widget.winfo_toplevel() is win
        in_word=widget is self.text and self._hover_target in self.text.tag_names(f'@{x-self.text.winfo_rootx()},{y-self.text.winfo_rooty()}')
        if not in_popup and not in_word:
            self._hide_hover()
        else:
            self._hover_guard_after=self.after(200,self._guard_hover)

    def _hide_hover(self):
        for name in ('_hover_after','_hover_guard_after'):
            value=getattr(self,name,None)
            if value is not None:
                self.after_cancel(value)
                setattr(self,name,None)
        win=getattr(self,'_hover_window',None)
        if win is not None and win.winfo_exists():
            win.destroy()
        self._hover_window=None
        self._hover_target=None
