"""Desktop and mobile browser tests against an isolated local server."""
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch
from wsgiref.simple_server import make_server

from run_web_tests import WebTests, SANDBOX, config, store, gen, llm, core_dict
from playwright.sync_api import sync_playwright, expect


def main():
    WebTests.setUpClass()
    app = WebTests.app
    article = dict(index=0, title='Reading across devices', paragraphs=[
        f'Paragraph {i}. The writer gave a candid reply. ' + 'We read the world one sentence at a time. ' * 10
        for i in range(32)])
    store._atomic_json_write(config.app_dir() / 'cache/sample/content.json', dict(
        bookSize=WebTests.meta['size'], format='epub', articles=[article]))
    server = make_server('127.0.0.1', 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    errors = []
    requests = []
    def fake_vocab(meta, art, level, cfg, **kwargs):
        kwargs['on_progress'](0,1,'test')
        kwargs['on_chars'](150)
        time.sleep(.2)
        result=dict(articleIndex=0,level=level,titleEntries=[],processedParagraphs=[-1,*range(32)],
            paragraphs=[dict(index=i,entries=[dict(word='candid',sense='坦率的',inText='a candid reply')]) for i in range(32)])
        store.save_vocab(meta['id'],result)
        return result
    def fake_discussion(cfg,system,messages,on_text,control):
        on_text('这是')
        time.sleep(.2)
        on_text('这是一个模拟回答。')
        return '这是一个模拟回答。'
    try:
        with (sync_playwright() as playwright, patch.object(gen,'generate_vocab',side_effect=fake_vocab) as vocab,
              patch.object(llm,'conversation',side_effect=fake_discussion), patch.object(core_dict,'lookup_many',return_value={})):
            chrome=Path(os.environ.get('KANDU_TEST_CHROME') or Path(__file__).resolve().parents[2] / 'chrome-win64/chrome.exe')
            browser=playwright.chromium.launch(executable_path=str(chrome),headless=True)
            desktop=browser.new_context(viewport={'width':1440,'height':1000})
            mobile=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,device_scale_factor=1)
            pages=[]
            for context in (desktop,mobile):
                page=context.new_page()
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('response',lambda response:requests.append((response.status,response.url)) if response.status>=400 else None)
                page.goto(url)
                page.locator('#password').fill(WebTests.authcode)
                page.locator('#login-form button').click()
                expect(page.get_by_role('heading',name='我的阅读室')).to_be_visible()
                expect(page.locator('.book-card')).to_have_count(1)
                pages.append(page)
            page=pages[0]
            page.locator('.book-card .cover-link').click()
            expect(page.locator('.article-card')).to_have_count(1)
            page.locator('.card-bottom select').select_option('read')
            expect(page.locator('.state-pill')).to_have_text('已读')
            page.locator('.card-bottom a').click()
            expect(page.locator('.paragraph')).to_have_count(32)
            expect(page.locator('#badge-0')).to_contain_text('待生成')
            page.locator('#badge-0').click()
            expect(page.locator('#sheet')).to_be_visible()
            expect(page.locator('.notice')).to_contain_text('尚未生成')
            page.get_by_role('button',name='生成 / 补缺全文生词',exact=True).click()
            expect(page.locator('#sheet-content .vocab-word').first).to_have_text('candid',timeout=10000)
            expect(page.locator('.notice')).to_have_count(0)
            page.locator('.entry-top button').first.click()
            expect(page.locator('.entry-top button').first).to_have_text('编辑收藏')
            page.locator('#sheet-close').click()
            expect(page.locator('#badge-0')).to_have_text('词 1 · 本段生词')
            assert vocab.call_count==1
            page.locator('#para-19').scroll_into_view_if_needed()
            time.sleep(1.3)
            record=store.load_reading_history('sample')['0']
            assert record['paragraphIndex']>10,record
            stored_paragraph=record['paragraphIndex']
            phone=pages[1]
            phone.locator('.book-card .cover-link').click()
            phone.locator('.card-bottom a').click()
            expect(phone.locator('.paragraph')).to_have_count(32)
            expect(phone.locator('#badge-0')).to_have_text('词 1 · 本段生词')
            time.sleep(.5)
            assert abs(store.load_reading_history('sample')['0']['paragraphIndex']-stored_paragraph)<=1
            width=phone.evaluate('() => ({scroll:document.documentElement.scrollWidth, width:innerWidth})')
            assert width['scroll']<=width['width'],width
            phone.locator('.reader-toolbar').get_by_role('button',name='与 AI 讨论').click()
            phone.locator('.discussion-input textarea').fill('作者想表达什么？')
            phone.locator('.discussion-input button[type=submit]').click()
            expect(phone.locator('.answer-text')).to_have_text('这是一个模拟回答。',timeout=10000)
            expect(phone.locator('.discussion-input button[type=submit]')).to_be_enabled()
            phone.locator('#sheet-close').click()
            phone.get_by_role('link',name='生词本',exact=True).click()
            expect(phone.locator('.vocab-word')).to_have_text('candid')
            phone.locator('.entry-top button').first.click()
            phone.get_by_label('释义',exact=True).fill('手机上编辑的释义')
            phone.get_by_role('button',name='保存词条',exact=True).click()
            expect(phone.locator('.entry-sense')).to_have_text('手机上编辑的释义')
            phone.get_by_role('link',name='设置',exact=True).click()
            expect(phone.get_by_role('heading',name='阅读设置')).to_be_visible()
            width=phone.evaluate('() => ({scroll:document.documentElement.scrollWidth, width:innerWidth})')
            assert width['scroll']<=width['width'],width
            assert not errors,errors
            assert not requests,requests
            print('BROWSER PASS: desktop + 390px phone; login, mark, vocab generation, collection, discussion, progress sync, edit, no overflow/errors')
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        WebTests.tearDownClass()
        SANDBOX.cleanup()


if __name__=='__main__':
    main()
