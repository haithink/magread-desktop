"""PDF page-boundary paragraphs and lossless cache migration, with synthetic books."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
from kandu import core_pdf as pdf, core_parsers as store, core_config as config
from kandu import core_pdf_migration as migration, core_discussion as discussion, core_wordbook as wordbook


def make_pdf(path):
    with pymupdf.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), 'An ordinary paragraph ending here.', fontname='cour', fontsize=12)
        page.insert_text((72, 680), 'Wall Street was uneasy about it.\nWhat did Google have to show for', fontname='cour', fontsize=12)
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 75), 'it? Disappointing ad sales followed.\nA trail of resignations followed.', fontname='cour', fontsize=12)
        page.insert_text((72, 180), 'This is a real paragraph break.', fontname='cour', fontsize=12)
        page.insert_text((72, 680), 'This is another complete paragraph.\nIt should stay separate.', fontname='cour', fontsize=12)
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 75), 'A new paragraph starts on this page.', fontname='cour', fontsize=12)
        page.insert_text((72, 680), 'These are some more details to follow\nAn unfinished sentence right before', fontname='cour', fontsize=12)
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 75), 'another article, never join across articles.', fontname='cour', fontsize=12)
        doc.set_toc([[1, 'First article', 1], [1, 'Second article', 4]])
        doc.save(path)


class PdfParagraphTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=config.app_dir()))
        self.path = self.folder/'test.pdf'
        make_pdf(self.path)
        self.meta = dict(id=self.folder.name, path=str(self.path), ext='pdf', size=self.path.stat().st_size)
        with patch.object(pdf, '_continues', return_value=False):
            self.old = pdf.parse(self.path)
        self.new = pdf.parse(self.path)

    def fixture(self):
        store._atomic_json_write(store._cache_dir(self.meta['id'])/'content.json', dict(
            format='pdf', bookSize=self.meta['size'], pdfParserVersion=pdf.PARSER_VERSION, articles=self.old))
        store.save_vocab(self.meta['id'], dict(articleIndex=0, level='cet4', titleEntries=[dict(word='future')],
            paragraphs=[dict(index=1, startWords='Wall Street', entries=[dict(word='uneasy', sense='anxious')]),
                        dict(index=2, startWords='it?', entries=[dict(word='resignation', sense='quitting')]),
                        dict(index=3, entries=[dict(word='break', sense='a pause')])],
            processedParagraphs=[-1, 1, 2, 3], phraseProcessedParagraphs=[-1, 2, 3],
            coverageVersion=12, phraseCoverageVersion=8))
        store._atomic_json_write(store._reading_history_file(self.meta['id']), {'0':dict(
            title='First article', paragraphIndex=2, paragraphOffset=4, paragraphCount=len(self.old[0]['paragraphs']),
            paragraphStart='it?', wordCount=123, lastReadAt=12345, progress=.3, atStart=False, atEnd=False)})
        discussion.save(self.meta, self.old[0], [dict(role='user', content='Explain', paragraph=2,
            quote='Disappointing ad sales', status='complete', createdAt=1234)])
        wordbook.save(dict(word='resignation', sense='my meaning', note='my note',
            source=dict(bookId=self.meta['id'], articleIndex=0, paragraph=2, context='Disappointing ad sales')))
        folder=store._cache_dir(self.meta['id'])
        for name in ['digest.json','analysis_saved.json']:
            store._atomic_json_write(folder/name, dict(saved='keep this content', bookId=self.meta['id'],
                pdfParserVersion=pdf.PARSER_VERSION, pdfSourceSize=self.meta['size']))

    def test_page_continuation_and_real_breaks_and_article_boundary(self):
        self.assertEqual(len(self.new),2)
        old, new=self.old[0],self.new[0]
        self.assertEqual(len(old['paragraphs'])-len(new['paragraphs']),1)
        self.assertIn('show for it? Disappointing',new['paragraphs'][1])
        self.assertEqual(new['paragraphs'][2], 'This is a real paragraph break.')
        self.assertEqual(new['paragraphs'][3],old['paragraphs'][4])
        self.assertEqual(new['paragraphs'][4],old['paragraphs'][5])
        self.assertEqual(new['paragraphs'][-1],old['paragraphs'][-1])
        self.assertEqual(new['paragraphPages'],[1,1,2,2,3,3])
        self.assertEqual(new['pageEnd'],3)

    def test_layout_safeguards(self):
        previous=dict(page=0, height=792, bottom=720, top=650, left=72, width=460, lastWidth=430,
                      lines=4, size=12, font='Times', text='An unfinished sentence with')
        current=dict(previous,page=1,top=72,text='a continuation',lines=3)
        self.assertTrue(pdf._continues(previous,current))
        for change in [dict(top=300),dict(left=95),dict(font='Times-Bold'),dict(size=18),
                       dict(text='1. List item'),dict(page=2)]:
            self.assertFalse(pdf._continues(previous,dict(current,**change)),change)
        for change in [dict(text='A completed sentence.”'),dict(text='A heading:'),dict(lastWidth=100),
                       dict(lines=1),dict(bottom=400)]:
            self.assertFalse(pdf._continues(dict(previous,**change),current),change)

    def test_continuation_over_three_pages_keeps_last_page(self):
        path=self.folder/'continuous.pdf'
        with pymupdf.open() as doc:
            for index in range(3):
                page=doc.new_page(width=612,height=792)
                if index==0:
                    point,text=(72,680),'A long paragraph continues across pages\nwith more words near the bottom margin'
                elif index==1:
                    point,text=(72,75),'and carries on through another full page'
                    # One body block stretching from page top to page bottom.
                    text+='\n'+('\n'.join(['with more words near the bottom margin']*38))
                else:
                    point,text=(72,75),'until the paragraph is finally finished.'
                page.insert_text(point,text,fontname='cour',fontsize=12)
            doc.save(path)
        article=pdf.parse(path)[0]
        self.assertEqual(len(article['paragraphs']),1)
        self.assertEqual(article['paragraphPages'],[1])
        self.assertEqual(article['pageEnd'],3)

    def test_migration_preserves_generated_content_positions_sources_and_conversation(self):
        self.fixture()
        folder=store._cache_dir(self.meta['id'])
        untouched={name:(folder/name).read_bytes() for name in ['digest.json','analysis_saved.json']}
        before_word=wordbook.get('resignation')
        result=store.ensure_content(self.meta)
        self.assertEqual(result['articles'],self.new)
        for name,value in untouched.items():self.assertEqual((folder/name).read_bytes(),value)
        self.assertEqual(store.load_digest(self.meta['id'])['saved'],'keep this content')
        vocab=store.load_vocab(self.meta['id'],0,'cet4')
        self.assertEqual([p['index'] for p in vocab['paragraphs']],[1,2])
        self.assertEqual([e['word'] for e in vocab['paragraphs'][0]['entries']],['uneasy','resignation'])
        self.assertEqual(vocab['processedParagraphs'],[-1,1,2])
        self.assertEqual(vocab['phraseProcessedParagraphs'],[-1,2])
        self.assertEqual(vocab['coverageVersion'],12)
        history=store.load_reading_history(self.meta['id'])['0']
        self.assertEqual(history['paragraphIndex'],1)
        self.assertEqual(history['paragraphOffset'],len(self.old[0]['paragraphs'][1])+1+4)
        self.assertEqual(history['lastReadAt'],12345)
        self.assertEqual(history['progress'],.3)
        self.assertEqual(history['paragraphCount'],len(self.new[0]['paragraphs']))
        messages=discussion.load(self.meta,self.new[0])
        self.assertEqual(messages[0]['paragraph'],1)
        self.assertEqual(messages[0]['quote'],'Disappointing ad sales')
        after_word=wordbook.get('resignation')
        self.assertEqual(after_word['source']['paragraph'],1)
        self.assertEqual({k:v for k,v in before_word.items() if k!='source'},
                         {k:v for k,v in after_word.items() if k!='source'})
        backup=folder/'before_pdf_paragraphs_v1'
        self.assertEqual(json.loads((backup/'content.json').read_text('utf-8'))['articles'],self.old)
        before={p:p.read_bytes() for p in folder.rglob('*.json')}
        store.ensure_content(self.meta)
        self.assertEqual(before,{p:p.read_bytes() for p in folder.rglob('*.json')})

    def test_interruption_retry_does_not_apply_indices_twice(self):
        self.fixture()
        real_write=store._atomic_json_write
        def interrupted(path,data):
            if path.name=='content.json': raise OSError('Simulated interruption at commit')
            return real_write(path,data)
        with patch.object(store,'_atomic_json_write',side_effect=interrupted):
            with self.assertRaises(OSError):store.ensure_content(self.meta)
        store.ensure_content(self.meta)
        self.assertEqual(store.load_reading_history(self.meta['id'])['0']['paragraphIndex'],1)
        self.assertEqual(len(store.load_vocab(self.meta['id'],0,'cet4')['paragraphs'][0]['entries']),2)
        self.assertEqual(discussion.load(self.meta,self.new[0])[0]['paragraph'],1)

    def test_interrupted_backup_is_never_used_as_an_original(self):
        self.fixture()
        def interrupted(source,target):
            Path(target).write_bytes(b'{"incomplete":')
            raise OSError('Simulated interruption during backup')
        with patch.object(migration.shutil,'copy2',side_effect=interrupted):
            with self.assertRaises(OSError):store.ensure_content(self.meta)
        backup=store._cache_dir(self.meta['id'])/'before_pdf_paragraphs_v1'
        self.assertFalse((backup/'content.json').exists())
        self.assertEqual(list(backup.glob('*.tmp')),[])
        store.ensure_content(self.meta)
        self.assertEqual(store.load_reading_history(self.meta['id'])['0']['paragraphIndex'],1)
        self.assertEqual(len(store.load_vocab(self.meta['id'],0,'cet4')['paragraphs'][0]['entries']),2)

    def test_changed_text_rejected_before_migration(self):
        self.fixture()
        changed=copy.deepcopy(self.new)
        changed[0]['paragraphs'][1]+=' unexpected text'
        folder=store._cache_dir(self.meta['id'])
        before={p:p.read_bytes() for p in folder.glob('*.json')}
        with patch.object(pdf,'parse',return_value=changed):
            with self.assertRaises(ValueError):store.ensure_content(self.meta)
        self.assertEqual(before,{p:p.read_bytes() for p in folder.glob('*.json')})


if __name__=='__main__':
    raise SystemExit('Use tests/run_tests.py to enforce the isolated sandbox')
