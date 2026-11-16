"""Isolated API/concurrency tests. Never reads or changes the user's desktop library."""
import os
from pathlib import Path
import sys
import tempfile

SANDBOX = tempfile.TemporaryDirectory(prefix='kandu-web-tests-')
os.environ['KANDU_DATA_DIR'] = str(Path(SANDBOX.name) / 'web')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contextlib import closing
import json
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from kandu import core_config as config, core_parsers as store, core_gen as gen
from kandu import core_llm as llm, core_wordbook as wordbook, core_dict
from webapp.bootstrap import import_desktop
from webapp.server import create_app


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = config.app_dir()
        books = root / 'books'
        books.mkdir()
        source = books / 'sample.epub'
        source.write_bytes(b'publication source')
        cls.meta = dict(id='sample', fileName='Wired_2026.11.epub', ext='epub', size=source.stat().st_size,
                        path=str(source), lastModified=0)
        cls.article = dict(index=0, title='A candid view', paragraphs=['The reply was candid.', 'We read books.'])
        store.save_library([cls.meta])
        store._atomic_json_write(root / 'cache/sample/content.json', dict(bookSize=cls.meta['size'],
            format='epub', articles=[cls.article]))
        store.save_digest(dict(bookId='sample', articles=[dict(index=0, titleEn='A candid view',
            titleZh='坦率的看法', summaryZh='这是已经生成的摘要。')]))
        config.save_settings(dict(config.DEFAULTS, llms=[dict(name='test', provider='openai', baseUrl='https://example.invalid',
            apiKey='SOURCE_SECRET_DO_NOT_EXPOSE', model='test-model')], defaultLlm='test'))
        cls.app = create_app()
        cls.app.testing = True
        cls.authcode = (root / 'access-code.txt').read_text().strip()
        cls.network = patch.object(llm, 'chat', side_effect=AssertionError('No real model calls in tests'))
        cls.network.start()
        cls.conversation = patch.object(llm, 'conversation', side_effect=AssertionError('No real model calls in tests'))
        cls.conversation.start()

    @classmethod
    def tearDownClass(cls):
        cls.app.extensions['jobs'].pool.shutdown(wait=True)
        cls.network.stop()
        cls.conversation.stop()

    def setUp(self):
        self.client = self.app.test_client()
        response = self.client.post('/api/login', json={'password':self.authcode})
        self.assertEqual(response.status_code, 200)
        self.headers = {'X-CSRF-Token':response.json['csrf'], 'Origin':'http://localhost'}

    def post(self, path, data):
        return self.client.post('/api'+path, json=data, headers=self.headers)

    def wait_job(self, response):
        self.assertEqual(response.status_code, 202, response.json)
        job_id = response.json['jobId']
        for _ in range(100):
            result = self.client.get('/api/jobs/'+job_id).json
            if result['state'] not in ('queued','running'):
                return result
            time.sleep(.02)
        self.fail('Job did not finish')

    def test_auth_csrf_origin_host_and_secret(self):
        client = self.app.test_client()
        self.assertEqual(client.get('/api/library').status_code, 401)
        self.assertEqual(client.get('/api/books/sample/cover').status_code, 401)
        self.assertEqual(client.get('/api/books/sample/file').status_code, 401)
        self.assertEqual(client.post('/api/login', json={'password':'bad'}).status_code, 401)
        self.assertEqual(self.client.post('/api/logout', json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/settings', json={}, headers=dict(self.headers, Origin='https://evil.invalid')).status_code, 403)
        self.assertEqual(self.client.get('/api/library', headers={'Host':'evil.invalid'}).status_code, 400)
        self.assertEqual(self.client.get('/api/settings').status_code, 200)
        self.assertNotIn('SOURCE_SECRET', self.client.get('/api/settings').text)
        self.assertNotIn('SOURCE_SECRET', self.client.get('/api/library').text)
        self.assertEqual(self.client.get('/api/session').headers['X-Content-Type-Options'], 'nosniff')

    def test_read_existing_cache_without_model_or_reparse(self):
        with patch.object(store, 'epub_parse', side_effect=AssertionError('Do not reparse cached source')):
            books = self.client.get('/api/library').json['books']
            self.assertEqual(books[0]['summaryCount'], 1)
            self.assertNotIn('path', books[0])
            content = self.client.get('/api/books/sample').json
            self.assertEqual(content['articles'][0]['summaryZh'], '这是已经生成的摘要。')
            article = self.client.get('/api/books/sample/articles/0').json
            self.assertEqual(article['article']['paragraphs'], self.article['paragraphs'])
            response = self.client.get('/api/books/sample/articles/0/vocab').json
            self.assertFalse(response['generated'])
            self.assertEqual(response['missing'], [0,1])
        self.assertEqual(self.client.get('/api/books/missing').status_code, 404)
        self.assertEqual(self.client.get('/api/books/sample/articles/999').status_code, 404)

    def test_progress_and_marks_shared_between_clients(self):
        data = dict(paragraphIndex=1, paragraphOffset=4)
        response = self.post('/books/sample/articles/0/progress', data)
        self.assertEqual(response.status_code, 200, response.json)
        other = self.app.test_client()
        other.post('/api/login', json={'password':self.authcode})
        self.assertEqual(other.get('/api/books/sample/articles/0').json['history']['paragraphIndex'],1)
        self.assertEqual(self.post('/books/sample/articles/0/progress',dict(paragraphIndex=-1)).status_code,400)
        self.assertEqual(self.post('/books/sample/state',dict(articleIndex=0,state='uninterested')).status_code,200)
        self.assertEqual(other.get('/api/books/sample').json['articles'][0]['state'],'uninterested')
        self.assertEqual(self.post('/books/sample/state',dict(articleIndex=0,state='invalid')).status_code,400)

    def test_wordbook_lookup_overlay_crud(self):
        with patch.object(core_dict,'ensure_db'), patch.object(core_dict,'lookup',return_value=dict(word='candid',lemma='candid',translation='坦率的')):
            result=self.post('/books/sample/articles/0/lookup',dict(word='candid',paragraph=0))
            self.assertEqual(result.status_code,200,result.json)
            self.assertEqual(result.json['saved']['sense'],'坦率的')
        with patch.object(core_dict,'lookup_many',return_value={}):
            overlay=self.client.get('/api/books/sample/articles/0/vocab').json
            self.assertFalse(overlay['generated'])
            self.assertEqual(overlay['vocab']['paragraphs'][0]['entries'][0]['word'],'candid')
        edit=self.post('/wordbook',dict(original='candid',word='candid',sense='个人修改',note='重要用法'))
        self.assertEqual(edit.status_code,200)
        self.assertEqual(self.client.get('/api/wordbook?q=重要').json['words'][0]['sense'],'个人修改')
        delete=self.client.delete('/api/wordbook/candid',headers=self.headers)
        self.assertEqual(delete.status_code,200)
        self.assertFalse(wordbook.get('candid'))

    def test_vocabulary_jobs_progress_and_cached_result(self):
        entered, release = threading.Event(), threading.Event()
        result = dict(articleIndex=0,level='cet4',processedParagraphs=[-1,0,1],titleEntries=[],paragraphs=[
            dict(index=0,entries=[dict(word='candid',sense='模拟本文义')])])
        def generate(meta, art, level, cfg, **kwargs):
            entered.set()
            kwargs['on_progress'](0,1,'test')
            kwargs['on_chars'](200)
            release.wait(3)
            store.save_vocab(meta['id'],result)
            return result
        with patch.object(gen,'generate_vocab',side_effect=generate) as call:
            first=self.post('/books/sample/articles/0/generate-vocab',{})
            self.assertTrue(entered.wait(2))
            second=self.post('/books/sample/articles/0/generate-vocab',{})
            self.assertEqual(first.json['jobId'],second.json['jobId'])
            progress=self.client.get('/api/jobs/'+first.json['jobId']).json
            self.assertIn('200',progress['status'])
            self.assertNotIn('control',progress)
            release.set()
            self.assertEqual(self.wait_job(first)['state'],'done')
            self.assertEqual(call.call_count,1)
        with patch.object(core_dict,'lookup_many',return_value={}):
            shown=self.client.get('/api/books/sample/articles/0/vocab').json
        self.assertTrue(shown['generated'])
        self.assertFalse(shown['missing'])
        (config.app_dir()/'cache/sample/vocab_0_cet4.json').unlink()

    def test_job_error_redacts_credentials(self):
        with patch.object(gen,'generate_digest',side_effect=RuntimeError('Failed SOURCE_SECRET_DO_NOT_EXPOSE')):
            job=self.wait_job(self.post('/books/sample/generate-digest',{}))
        self.assertEqual(job['state'],'error')
        self.assertNotIn('SOURCE_SECRET',job['error'])

    def test_discussion_cumulative_stream_and_persistence(self):
        def converse(cfg, system, messages, on_text, control):
            self.assertIn('candid',system)
            on_text('这个')
            on_text('这个词表示坦率。')
            return '这个词表示坦率。'
        with patch.object(llm,'conversation',side_effect=converse):
            response=self.post('/books/sample/articles/0/discussion',dict(question='什么意思？',quote='candid',paragraph=0))
            result=self.wait_job(response)
        self.assertEqual(result['state'],'done',result)
        self.assertEqual(result['text'],'这个词表示坦率。')
        messages=self.client.get('/api/books/sample/articles/0/discussion').json['messages']
        self.assertEqual(messages[-1]['content'],'这个词表示坦率。')
        self.assertEqual(messages[-1]['status'],'complete')
        self.assertEqual(self.post('/books/sample/articles/0/discussion',dict(question='q',quote='not in text',paragraph=0)).status_code,400)

    def test_discussion_stop_preserves_received_answer(self):
        entered=threading.Event()
        def converse(cfg,system,messages,on_text,control):
            on_text('已经收到的部分')
            entered.set()
            for _ in range(150):
                control.check()
                time.sleep(.02)
            raise AssertionError('Expected stop request')
        with patch.object(llm,'conversation',side_effect=converse):
            response=self.post('/books/sample/articles/0/discussion',dict(question='请慢慢解释'))
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.post('/jobs/'+response.json['jobId']+'/stop',{}).status_code,200)
            result=self.wait_job(response)
        self.assertEqual(result['state'],'stopped')
        messages=self.client.get('/api/books/sample/articles/0/discussion').json['messages']
        self.assertEqual(messages[-1]['content'],'已经收到的部分')
        self.assertEqual(messages[-1]['status'],'stopped')

    def test_sentence_analysis_uses_existing_cache(self):
        sentence=self.article['paragraphs'][0]
        cached=dict(vocab=[],grammarComponents=[dict(text=sentence,label='原句',start=0,end=len(sentence))],
                    grammarPoints=[],translation='这个回答是坦率的。',context='评价回答的态度。')
        store.save_analysis('sample',sentence,0,cached)
        result=self.wait_job(self.post('/books/sample/articles/0/analyze',dict(sentence=sentence,paragraph=0)))
        self.assertEqual(result['state'],'done',result)
        self.assertEqual(result['result']['translation'],cached['translation'])
        self.assertEqual(self.post('/books/sample/articles/0/analyze',dict(sentence='invented sentence',paragraph=0)).status_code,400)

    def test_upload_indexes_epub_without_model_and_rejects_duplicate(self):
        from io import BytesIO
        import zipfile
        stream=BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
            z.writestr('content.opf','<package><manifest><item id="article" href="article.xhtml"/></manifest><spine><itemref idref="article"/></spine></package>')
            z.writestr('article.xhtml','<html><body><h1>A fresh article</h1><p>This is a new publication with enough original text for indexing and reading.</p></body></html>')
        data=stream.getvalue()
        response=self.client.post('/api/books',data={'file':(BytesIO(data),'Uploaded.epub')},headers=self.headers)
        result=self.wait_job(response)
        self.assertEqual(result['state'],'done',result)
        self.assertEqual(result['result']['bookId'],'Uploaded')
        self.assertEqual(self.client.get('/api/books/Uploaded').json['articles'][0]['titleEn'],'A fresh article')
        duplicate=self.client.post('/api/books',data={'file':(BytesIO(data),'Uploaded.epub')},headers=self.headers)
        self.assertEqual(duplicate.status_code,409)
        invalid=self.client.post('/api/books',data={'file':(BytesIO(data),'other.exe')},headers=self.headers)
        self.assertEqual(invalid.status_code,400)
        store.remove_book('Uploaded')

    def test_desktop_import_independent_and_does_not_copy_publications(self):
        target=Path(SANDBOX.name)/'new-server'
        imported=import_desktop(config.app_dir(),target)
        self.assertEqual(imported,1)
        self.assertFalse((target/'books').exists())
        entries=json.loads((target/'books.json').read_text(encoding='utf-8'))
        self.assertEqual(entries[0]['path'],self.meta['path'])
        (target/'cache/sample/digest.json').write_text('{}')
        self.assertEqual(store.load_digest('sample')['articles'][0]['summaryZh'],'这是已经生成的摘要。')
        with self.assertRaises(FileExistsError):import_desktop(config.app_dir(),target)
        with self.assertRaises(ValueError):import_desktop(config.app_dir(),config.app_dir())


if __name__=='__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        SANDBOX.cleanup()
