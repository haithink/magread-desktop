"""Web parity contracts exercised through native adapters, with no live APIs."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kandu import core_config as config, core_parsers as store, core_gen as gen, core_llm as llm
from kandu import core_sentence as sentence, word_meaning, core_wordbook as words, core_dict
from kandu import reading_activity as activity, reading_stats as stats, article_tags, article_duplicates
from kandu import podcasts, podcast_library, podcast_covers
from kandu.reading_tasks import WRITER

CFG = dict(name='Preferred', provider='openai', model='deepseek-test',
           baseUrl='https://example.invalid', apiKey='fake-only')


class PortFeatures(unittest.TestCase):
    def setUp(self):
        folder=Path(tempfile.mkdtemp(dir=config.app_dir()))
        self.addCleanup(__import__('shutil').rmtree, folder, True)
        self.folder=folder
        p=patch.object(config,'app_dir',return_value=folder)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda:WRITER.submit(lambda:None).result(timeout=10))
        self.meta=dict(id='feature-book',fileName='Wired_2026.11.epub',publication='Wired',ext='epub',size=1,path='')
        self.art=dict(index=0,title='A Candid View!',paragraphs=['A candid reply. Another candid reply.'])

    def cache(self,meta=None,art=None):
        meta,art=meta or self.meta,art or self.art
        store._atomic_json_write(store._cache_dir(meta['id'])/'content.json',dict(bookSize=meta['size'],articles=[art],format='epub'))

    def test_thinking_payload_and_native_settings_round_trip(self):
        original=copy.deepcopy(CFG)
        for provider,model in [('openai','deepseek-test'),('anthropic','deepseek-test'),('openai','gpt-6.1'),('anthropic','claude-test')]:
            cfg=dict(CFG,provider=provider,model=model)
            for level in llm.THINKING_LEVELS:
                source=dict(cfg,thinkingLevel=level)
                effective=llm.thinking_config(source,deepseek_mode='disabled')
                body=llm._body(effective,'system','text',1000,.3,True)
                if model.startswith('deepseek'):
                    self.assertEqual(body['thinking']['type'],'disabled' if level in ('auto','disabled') else 'enabled')
                elif provider=='openai' and level!='auto':
                    self.assertEqual(body['reasoning_effort'],'none' if level=='disabled' else level)
                elif provider=='anthropic' and level not in ('auto','disabled'):
                    self.assertEqual(body['thinking'],dict(type='adaptive'))
                self.assertEqual(source,dict(cfg,thinkingLevel=level))
        self.assertEqual(CFG,original)
        from kandu.ui_settings import LlmProfileDialog
        # The actual editor persists the selected value, including closing thought.
        root=tk.Tk();root.withdraw()
        self.addCleanup(root.destroy)
        root.settings={}
        editor=LlmProfileDialog(root,CFG)
        for level,label in editor.thinking_labels.items():
            editor.thinking.set(label)
            self.assertEqual(editor._current_cfg()['thinkingLevel'],level)
        editor.destroy()
        settings=dict(llms=[dict(CFG,thinkingLevel='low')],defaultLlm=CFG['name'])
        config.save_settings(settings)
        self.assertEqual(config.load_settings()['llms'][0]['thinkingLevel'],'low')

    def test_context_cache_occurrences_partial_review_and_safe_failure(self):
        art=dict(index=0,title='A title',paragraphs=['Before.','🌙 A reply. A reply.','After.'])
        text=art['paragraphs'][1];at=text.rfind('A reply.')
        context=sentence.context(art,1,'A reply.',at)
        self.assertEqual(context['previousParagraph'],'Before.')
        self.assertEqual(context['selectedStart'],at)
        first=sentence.identity(sentence.context(art,1,'A reply.',text.find('A reply.')),CFG)
        key=sentence.identity(context,CFG)
        self.assertNotEqual(first,key)
        self.assertNotEqual(sentence.identity(context,dict(CFG,thinkingLevel='low')),key)
        result=dict(context='说话者作了回答。',translation='一个回答。',vocab=[],grammarComponents=[dict(text='A reply.',label='主句')])
        previews=[]
        with patch.object(gen,'_chat_retry',return_value=json.dumps(result)) as api:
            value=gen.analyze(self.meta,art,'A reply.','','',CFG,context=context,cache_identity=key,on_partial=previews.append)
            self.assertEqual(api.call_count,1)
            self.assertEqual(gen.analyze(self.meta,art,'A reply.','','',CFG,context=context,cache_identity=key),value)
            self.assertEqual(api.call_count,1)
        with patch.object(gen,'_chat_retry',return_value='{}'):
            with self.assertRaises(gen.GenError):
                gen.analyze(self.meta,art,'A reply.','','',CFG,context=context,cache_identity=key,force=True,deep=True)
        self.assertEqual(store.load_analysis(self.meta['id'],'A reply.',0,key),value)
        control=llm.RequestControl();control.cancel()
        with patch.object(llm,'conversation') as api:
            with self.assertRaises(llm.RequestCancelled):
                gen.analyze(self.meta,art,'A reply.','','',CFG,context=context,cache_identity=key,force=True,control=control)
            api.assert_not_called()

    def test_phrase_lookup_context_cache_personal_edit_delete_protection(self):
        art=dict(index=0,title='Artemis',paragraphs=['It will not paper over the problem.'])
        context=word_meaning.selection(art,0,'paper over',12)
        self.assertEqual(context['word'],'paper over')
        with self.assertRaises(ValueError):word_meaning.selection(art,0,'not paper over the',8)
        output=dict(sense='掩盖',pos='短语动词',explanation='把问题暂时掩盖起来。',translation='它不会掩盖问题。')
        with patch.object(llm,'chat',return_value=json.dumps(output)) as api:
            meaning=word_meaning.explain('phrase',context,CFG,lambda **v:None,llm.RequestControl())
            self.assertEqual(word_meaning.explain('phrase',context,CFG,lambda **v:None,llm.RequestControl()),meaning)
            self.assertEqual(api.call_count,1)
        source=dict(bookId='phrase',articleIndex=0,paragraph=0,context=art['paragraphs'][0],selectedWord='paper over',selectedStart=12)
        saved=words.remember_lookup('paper over',None,source)
        words.attach_context(saved['word'],meaning,source)
        self.assertIn('掩盖',words.get('paper over')['source']['contextMeaning']['sense'])
        edited=words.get('paper over')
        words.save(dict(edited,sense='我的个人解释'),original='paper over')
        words.attach_context('paper over',dict(meaning,sense='迟到的解释'),source)
        self.assertEqual(words.get('paper over')['sense'],'我的个人解释')
        words.delete('paper over')
        words.attach_context('paper over',meaning,source)
        self.assertIsNone(words.get('paper over'))

    def test_reading_clock_pause_sleep_navigation_and_title_dedup(self):
        from kandu.core_reading_clock import ReadingClock
        now=[0.0];clock=ReadingClock(lambda:now[0])
        clock.tick(True);now[0]=5;clock.tick(True)
        self.assertEqual(clock.elapsed,5000)
        now[0]=6;clock.tick(False);now[0]=26;clock.tick(True)
        self.assertEqual(clock.elapsed,5000)
        now[0]=200;clock.tick(True)
        self.assertEqual(clock.elapsed,5000)
        art=dict(index=0,title='An AI conversation',paragraphs=[' '.join(['word']*600)])
        meta=dict(self.meta,publication='Lex Fridman Podcast',fileName='Lex Fridman Podcast')
        self.cache(meta,art);store.save_library([meta])
        base=time.time_ns()//1_000_000-60_000
        def record(seq,elapsed,position,navigation=False):
            with patch.object(activity.time,'time_ns',return_value=(base+elapsed)*1_000_000):
                return activity.record(meta,art,dict(sessionId='contract-session',sequence=seq,elapsedMs=elapsed,
                    paragraphIndex=0,paragraphOffset=position*5,navigation=navigation))
        record(1,0,0)
        record(2,30000,60)
        before=stats.summary()['analytics']['readingVolume']['totalWords']
        self.assertGreater(before,0)
        record(3,35000,550,True)
        self.assertEqual(stats.summary()['analytics']['readingVolume']['totalWords'],before)
        # Retrying a sample is idempotent, including the pause/navigation flag.
        record(3,35000,550,True)
        partial=stats.summary()
        self.assertEqual(partial['articleCount'],0)
        self.assertTrue(partial['analytics']['readingSources'])
        self.assertEqual(partial['analytics']['readingSources'][0]['name'],'Lex Fridman Podcast')
        store.set_article_state(meta['id'],0,'read');stats.mark_web(meta,art,True)
        second=dict(meta,id='duplicate',fileName='Other publication')
        self.cache(second,dict(art,title='An AI Conversation!'))
        store.save_library([meta,second]);store.set_article_state(second['id'],0,'read')
        stats.mark_web(second,dict(art,title='An AI Conversation!'),True)
        complete=stats.summary()
        self.assertEqual(complete['articleCount'],1)
        self.assertEqual(complete['totalWords'],600)
        self.assertEqual(complete['analytics']['readingVolume']['totalWords'],600)
        for m in (meta,second):
            store.set_article_state(m['id'],0,'unread');stats.mark_web(m,art,False)
        self.assertEqual(stats.summary()['articleCount'],0)
        self.assertEqual(stats.summary()['analytics']['readingVolume']['totalWords'],before)

    def test_duplicates_tags_and_unread_counts_use_same_directory(self):
        early=dict(self.meta,id='early',fileName='Wired_2026.01.epub')
        late=dict(self.meta,id='late',fileName='Wired_2026.02.epub')
        for m in (early,late):
            self.cache(m)
            store.save_digest(dict(bookId=m['id'],articles=[dict(index=0,titleEn=self.art['title'],summaryZh='摘要')]))
        store.save_library([early,late])
        duplicates=article_duplicates.earlier_occurrences(late,[self.art])
        self.assertIn(0,duplicates)
        def counts_now():
            library=store.load_library()
            return article_duplicates.library_counts(library,{m['id']:store.load_digest(m['id']) for m in library})
        counts=counts_now()
        self.assertEqual(counts['early']['articleCount'],1)
        self.assertEqual(counts['late']['articleCount'],0)
        article_tags.save(early['id'],0,['AI','产业','AI'])
        self.assertEqual(article_tags.load(early['id'])['0'],['AI','产业'])
        store.set_article_state(early['id'],0,'read')
        self.assertEqual(counts_now()['early']['unreadCount'],0)

    def test_podcast_import_resume_cancel_group_and_bounded_summary(self):
        turns=''.join('<div class="ts-segment"><span class="ts-name">Guest</span><span class="ts-text">'
            + ('We discuss AI and scientific evidence. '*12)+'</span></div>' for _ in range(8))
        html='<h1 class="entry-title">Transcript for AI conversation | Lex Fridman Podcast #99</h1><div class="entry-content">'+turns+'</div>'
        catalog='<div class="episodes-all"><div class="episode-item"><div class="episode-title">AI conversation</div><span class="ep-person">AI guest</span><a href="https://lexfridman.com/ai-transcript">Transcript</a></div></div>'
        cfg=dict(CFG)
        def fetch(spec,url,control):
            control.check();return catalog if url==spec['url'] else html
        with patch.object(podcasts,'fetch_html',side_effect=fetch), patch.object(podcast_covers,'ensure_series_cover',return_value=None), \
             patch.object(llm,'conversation',return_value=json.dumps(dict(titleZh='AI 对话',summaryZh='探讨 AI 产业及未来。'))) as api:
            report=podcasts.import_source('lex-fridman',1,cfg,lambda **v:None,llm.RequestControl())
            self.assertEqual(report['imported'],1)
            self.assertEqual(report['summaries'],1)
            episode=store.load_library()[0]
            self.assertEqual(len(store.ensure_content(episode)['articles']),1)
            self.assertIsNone(store.load_vocab(episode['id'],0,'cet4'))
            second=podcasts.import_source('lex-fridman',1,cfg,lambda **v:None,llm.RequestControl())
            self.assertEqual(second['existing'],1)
            self.assertEqual(api.call_count,1)
            series=podcast_library.metadata(podcast_library.parent_id(episode))
            self.assertEqual(len(podcast_library.directory(series)),1)
            control=llm.RequestControl();control.cancel()
            with self.assertRaises(llm.RequestCancelled):podcasts.import_source('lex-fridman',1,cfg,lambda **v:None,control)
            self.assertEqual(podcasts.status('lex-fridman')['state'],'stopped')
        context=podcasts.summary_context([' '.join(['begin']*10000),' '.join(['end']*10000)],[],budget=2000)
        self.assertLess(len(json.dumps(context)),4000)
        (store._cache_dir(episode['id'])/'digest.json').unlink()
        with patch.object(llm,'conversation',return_value=json.dumps(dict(titleZh='AI 对话',summaryZh='并发补缺只生成一次。'))) as api:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as executor:
                results=[executor.submit(podcasts.generate_digest,episode,cfg,lambda **v:None,llm.RequestControl()) for _ in range(2)]
                for result in results:self.assertTrue(result.result(timeout=3)['articles'])
            self.assertEqual(api.call_count,1)

    def reader(self):
        from kandu.ui_reader import ReaderFrame
        root=tk.Tk();root.geometry('1050x750')
        errors=[];root.report_callback_exception=lambda *v:errors.append(v)
        app=SimpleNamespace(settings=dict(llms=[CFG],defaultLlm=CFG['name'],vocabLevel='cet4',readerFontSize=13),
            post=lambda *v:None,save_settings=lambda:None,open_digest=lambda *v:None,
            _handle_handler_error=lambda e:errors.append(e))
        reader=ReaderFrame(root,app,self.meta,0);reader.pack(fill='both',expand=True)
        self.addCleanup(root.destroy)
        return root,app,reader,errors

    def test_native_highlights_phrase_inflections_unicode_and_clock_aids(self):
        with patch.object(core_dict,'lookup_many',return_value={}):
            root,app,reader,errors=self.reader()
            art=dict(index=0,title='An article',paragraphs=['🌙 Uncertainties cannot be papered over. An education is useful.'])
            reader.on_content_loaded((dict(articles=[art]),0))
            reader.vocab=dict(level='cet4',articleIndex=0,titleEntries=[],paragraphs=[dict(index=0,entries=[
                dict(word='uncertainty',sense='不确定性'),dict(word='paper over',sense='掩盖'),dict(word='cat',sense='猫')])])
            reader._rebuild(keep_scroll=True);root.update()
            reader._refresh_highlights()
            hits=[reader.text.get(*reader.text.tag_ranges(tag)) for tag in reader._highlight_hits]
            self.assertIn('Uncertainties',hits);self.assertIn('papered over',hits)
            self.assertNotIn('cat',hits)
            win=reader._make_sheet('生词','article')[0]
            win.update_idletasks()
            child=tk.Label(win,text='辅助阅读');child.pack()
            with patch.object(root,'focus_displayof',return_value=child):
                reader._clock_input=time.monotonic()
                self.assertTrue(reader._clock_active())
                reader.vocab_running=True;self.assertFalse(reader._clock_active())
                reader.vocab_running=False;reader._clock_paused=True;self.assertFalse(reader._clock_active())
                reader._clock_paused=False;reader._clock_completed=True;self.assertFalse(reader._clock_active())
                reader._clock_rereading=True;self.assertTrue(reader._clock_active())
            reader.destroy();root.update()
            self.assertFalse(errors,errors)

    def test_statistics_refresh_waits_for_pending_writer(self):
        from kandu.ui_statistics import StatisticsDialog
        root=tk.Tk();root.withdraw();root.current=None
        self.addCleanup(root.destroy)
        stored=[];gate=threading.Event()
        pending=WRITER.submit(lambda:(gate.wait(3),stored.append('saved')))
        with patch.object(stats,'summary',side_effect=lambda **v:(stored.append('summary') or {})):
            dialog=StatisticsDialog(root)
            gate.set();pending.result(timeout=3)
            WRITER.submit(lambda:None).result(timeout=3)
            self.assertEqual(stored,['saved','summary'])
            dialog.destroy()

    def test_native_phrase_lookup_keeps_position_and_does_not_restore_deleted_word(self):
        with patch.object(core_dict,'lookup_many',return_value={}),patch.object(core_dict,'ensure_db'), \
                patch.object(core_dict,'lookup',return_value=dict(miss=True,word='paper over')):
            root,app,reader,errors=self.reader()
            art=dict(index=0,title='Artemis',paragraphs=['It will not paper over the problem.'])
            reader.on_content_loaded((dict(articles=[art]),0));root.update()
            start=reader._para_range(0)[0]
            reader.text.tag_add('sel',f'{start}+12c',f'{start}+22c')
            entered,release=threading.Event(),threading.Event()
            def meaning(book_id,context,cfg,update,control):
                entered.set();self.assertTrue(release.wait(3));control.check()
                return dict(word='paper over',sense='掩盖',pos='短语动词',explanation='暂时掩盖问题。',
                    sentence=context['sentence'],translation='它不会掩盖问题。')
            def pump(predicate):
                end=time.monotonic()+3
                while not predicate() and time.monotonic()<end:
                    root.update();time.sleep(.01)
                self.assertTrue(predicate())
            with patch.object(word_meaning,'explain',side_effect=meaning):
                before=reader._capture_reading_position()
                reader._lookup_selection(0,12)
                self.assertTrue(entered.wait(2))
                self.assertIsNotNone(words.get('paper over'))
                words.delete('paper over');release.set()
                pump(lambda:not reader._lookup_controls)
                self.assertIsNone(words.get('paper over'))
                self.assertEqual(reader._capture_reading_position(),before)
                self.assertFalse(errors,errors)
            reader.destroy();root.update()

    def test_preferred_model_retries_before_backups_and_records_actual_models(self):
        self.cache()
        cached=dict(articleIndex=0,level='cet4',titleEntries=[],paragraphs=[],processedParagraphs=[],
            coverageVersion=gen.VOCAB_COVERAGE_VERSION,phraseCoverageVersion=gen.PHRASE_COVERAGE_VERSION,
            chunkFailures={'-1,0':[CFG['name']]})
        store.save_vocab(self.meta['id'],cached)
        backup=dict(CFG,name='Backup',model='other-model')
        calls=[];events=[]
        def model(cfg,system,user,**kwargs):
            calls.append(cfg['name'])
            if cfg['name']==CFG['name']:
                raise llm.LlmError('temporary failure')
            return json.dumps(dict(paragraphs=[dict(index=-1,entries=[]),dict(index=0,entries=[])]))
        with patch.object(core_dict,'ensure_db'),patch.object(core_dict,'lookup_many',return_value={}), \
                patch.object(gen,'candidates',return_value=[]),patch.object(llm,'chat',side_effect=model):
            result=gen.generate_vocab(self.meta,self.art,'cet4',CFG,llm_rotation=[backup],
                on_model=lambda key,cfg,active:events.append((cfg['name'],active)))
        self.assertEqual(calls,[CFG['name'],'Backup'])
        self.assertEqual(events,[(CFG['name'],True),(CFG['name'],False),('Backup',True),('Backup',False)])
        self.assertEqual([m['name'] for m in result['generationStats']['modelsUsed']],calls)
        self.assertNotIn(CFG['apiKey'],json.dumps(result['generationStats']))

    def test_native_podcast_series_job_reuses_request_and_cancel_on_leave(self):
        from kandu.ui_podcasts import PodcastSeriesFrame
        meta=dict(self.meta,sourceType='podcast',sourceId='lex-fridman',episodeNumber=99,title='AI guest')
        self.cache(meta);store.save_library([meta])
        series=podcast_library.metadata(podcast_library.parent_id(meta))
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        app=SimpleNamespace(settings=dict(llms=[CFG],defaultLlm=CFG['name']),post=lambda *v:None,show_library=lambda:None)
        frame=PodcastSeriesFrame(root,app,series)
        calls=[];entered=threading.Event();release=threading.Event()
        def generate(meta,cfg,update,control):
            calls.append(meta['id']);entered.set()
            self.assertTrue(release.wait(3));control.check()
        with patch.object(podcasts,'generate_digest',side_effect=generate):
            frame.generate();self.assertTrue(entered.wait(2))
            frame.generate();self.assertEqual(len(calls),1)
            job=frame._series_job
            frame.destroy();self.assertTrue(job['control'].event.is_set())
            release.set()
            end=time.monotonic()+2
            while job['running'] and time.monotonic()<end:time.sleep(.01)
            self.assertFalse(job['running'])
            self.assertEqual(len(calls),1)

    def test_native_event_routing_discards_destroyed_page(self):
        from main import App
        root=App();self.addCleanup(root.destroy)
        old=tk.Frame(root);new=tk.Frame(root);root.current=new
        seen=[];old.on_sample=lambda value:seen.append(('old',value));new.on_sample=lambda value:seen.append(('new',value))
        root.q.put(('target',(old,'sample',1)));root._poll()
        self.assertEqual(seen,[('old',1)])
        old.destroy();root.q.put(('target',(old,'sample',2)));root._poll()
        self.assertEqual(seen,[('old',1)])


def run_contracts():
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PortFeatures))
    assert result.wasSuccessful(), 'Desktop Web-port contracts failed'
