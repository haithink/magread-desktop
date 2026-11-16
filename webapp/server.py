"""Authenticated LAN API; model jobs stay off HTTP worker threads."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import re
import secrets
import shutil
import socket
import threading
import time
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, request, send_file, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from kandu import core_config as config, core_parsers as store, core_gen as gen
from kandu import core_llm as llm, core_wordbook as wordbook, core_dict, core_discussion as discussion
from webapp import __version__

STATIC = Path(__file__).parent / 'static'
BOOK_ID = re.compile(r'[A-Za-z0-9._-]{1,220}\Z')


def clean_error(error):
    text = str(error)[:1000]
    for model in config.load_settings().get('llms', []):
        if model.get('apiKey'):
            text = text.replace(model['apiKey'], '[已隐藏密钥]')
    return re.sub(r'sk-[A-Za-z0-9_-]+', '[已隐藏密钥]', text)


class Jobs:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='kandu-web')
        self.lock = threading.RLock()
        self.items = OrderedDict()

    def submit(self, key, action):
        with self.lock:
            for job in self.items.values():
                if job['key'] == key and job['state'] in ('queued', 'running'):
                    return job['id']
            active = sum(j['state'] in ('queued', 'running') for j in self.items.values())
            if active >= 8:
                abort(429, '已有较多任务，请稍后重试')
            while len(self.items) >= 80:
                removable = next((i for i, j in self.items.items() if j['state'] not in ('queued', 'running')), None)
                if removable is None:
                    break
                del self.items[removable]
            job_id = secrets.token_urlsafe(18)
            job = dict(id=job_id, key=key, state='queued', status='等待前面的任务', text='',
                       result=None, error='', createdAt=time.time(), control=llm.RequestControl())
            self.items[job_id] = job

        def update(**values):
            with self.lock:
                job.update(values)

        def run():
            try:
                update(state='running', status='准备中')
                result = action(update, job['control'])
                update(state='done', status='已完成', result=result)
            except llm.RequestCancelled:
                update(state='stopped', status='已停止')
            except Exception as error:
                update(state='error', error=clean_error(error), status='任务未完成')
        self.pool.submit(run)
        return job_id

    def public(self, job_id):
        with self.lock:
            job = self.items.get(job_id)
            if not job:
                abort(404, '任务已过期，请刷新页面查看已保存结果')
            return {k: v for k, v in job.items() if k not in ('key', 'control')}


def create_app(allowed_hosts=(), secure_cookie=False):
    root = config.app_dir()
    auth_file = root / 'web-auth.json'
    if not auth_file.exists():
        code = secrets.token_urlsafe(12)
        store._atomic_json_write(auth_file, dict(secret=secrets.token_hex(32), passwordHash=generate_password_hash(code)))
        (root / 'access-code.txt').write_text(code + '\n', encoding='utf-8')
    auth = json.loads(auth_file.read_text(encoding='utf-8'))
    app = Flask(__name__, static_folder=None)
    app.config.update(SECRET_KEY=auth['secret'], MAX_CONTENT_LENGTH=150 * 1024**2,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=secure_cookie, SESSION_COOKIE_NAME='kandu_web',
                      PERMANENT_SESSION_LIFETIME=timedelta(days=30))
    hosts = {'localhost', '127.0.0.1', '::1', socket.gethostname().lower(), *[h.lower() for h in allowed_hosts]}
    hosts.update(r[4][0] for r in socket.getaddrinfo(socket.gethostname(), None))
    jobs = Jobs()
    app.extensions['jobs'] = jobs
    data_lock = threading.RLock()
    book_locks, rate = {}, {}
    started = time.time()

    def book(book_id):
        if not BOOK_ID.fullmatch(book_id) or book_id in ('.', '..'):
            abort(404, '找不到刊物')
        meta = store.get_book(book_id)
        if meta is None:
            abort(404, '找不到刊物')
        return meta

    def article(book_id, index):
        meta = book(book_id)
        art = next((a for a in store.ensure_content(meta)['articles'] if a['index'] == index), None)
        if art is None:
            abort(404, '找不到文章')
        return meta, art

    def payload():
        if not request.is_json:
            abort(415, '请求需要 JSON 格式')
        value = request.get_json()
        if not isinstance(value, dict):
            abort(400, '无效请求')
        return value

    def model(data):
        settings = config.load_settings()
        name = data.get('modelName')
        cfg = config.get_llm(settings, name)
        if name and name not in config.llm_names(settings):
            abort(400, '模型配置已改变，请刷新')
        if error := llm.validate(cfg):
            abort(400, error + '，请在设置中配置模型')
        return dict(cfg)

    def lock_for(book_id):
        with data_lock:
            return book_locks.setdefault(book_id, threading.Lock())

    def accepted(job_id):
        return jsonify(jobId=job_id), 202

    @app.before_request
    def guard():
        hostname = urlsplit(request.host_url).hostname
        if not hostname or hostname.lower() not in hosts:
            abort(400, '此访问地址尚未在服务端启用')
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('Origin')
            if origin:
                source = urlsplit(origin)
                if source.netloc.lower() != request.host.lower() or source.scheme not in ('http', 'https'):
                    abort(403, '拒绝跨站请求')
        if request.path.startswith('/api/') and request.path not in ('/api/session', '/api/login', '/api/health'):
            if not session.get('authenticated'):
                abort(401, '请先输入访问密码')
            if request.method not in ('GET', 'HEAD'):
                if not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf', 'missing')):
                    abort(403, '会话已失效，请刷新页面后重试')

    @app.after_request
    def headers(response):
        response.headers.update({'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin',
            'X-Frame-Options': 'SAMEORIGIN', 'Cache-Control': 'no-store',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'"})
        return response

    @app.errorhandler(Exception)
    def failed(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        if isinstance(error, (ValueError, FileNotFoundError)):
            return jsonify(error=clean_error(error)), 400
        app.logger.error('Request failed: %s', type(error).__name__)
        return jsonify(error='服务器处理失败，请重试或查看服务器运行日志'), 500

    @app.get('/')
    def index():
        return send_file(STATIC / 'index.html')

    @app.get('/static/<name>')
    def static(name):
        if name not in ('app.js', 'app.css', 'icon.svg'):
            abort(404)
        return send_file(STATIC / name)

    @app.get('/api/health')
    def health():
        return jsonify(status='ok', version=__version__, startedAt=started)

    @app.get('/api/session')
    def get_session():
        return jsonify(authenticated=bool(session.get('authenticated')), csrf=session.get('csrf'), version=__version__)

    @app.post('/api/login')
    def login():
        data = payload()
        address = request.remote_addr or 'unknown'
        now = time.time()
        with data_lock:
            recent = [t for t in rate.get(address, []) if now - t < 600]
            if len(recent) >= 10:
                abort(429, '尝试次数过多，请十分钟后再试')
            if len(rate) > 1024:
                rate.clear()
            rate[address] = recent + [now]
        password = data.get('password', '')
        if not isinstance(password, str) or len(password) > 200 or not check_password_hash(auth['passwordHash'], password):
            abort(401, '访问密码不正确')
        with data_lock:
            rate.pop(address, None)
        session.clear()
        session.update(authenticated=True, csrf=secrets.token_urlsafe(24))
        session.permanent = True
        return jsonify(csrf=session['csrf'])

    @app.post('/api/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/library')
    def library():
        books, recent = [], []
        for meta in store.load_library():
            digest = store.load_digest(meta['id']) or {}
            entries = digest.get('articles', [])
            publication = re.sub(r'[_ .-]*\d{4}.*$', '', meta['fileName']).replace('_', ' ').strip() or '其他刊物'
            books.append(dict(id=meta['id'], fileName=meta['fileName'], ext=meta['ext'], publication=publication,
                articleCount=len(entries), summaryCount=sum(bool(e.get('summaryZh')) and not e['summaryZh'].startswith('（本篇摘要生成失败') for e in entries),
                cover=store.has_cover(meta['id']), available=Path(meta['path']).is_file()))
            for idx, record in store.load_reading_history(meta['id']).items():
                recent.append(dict(record, bookId=meta['id'], articleIndex=int(idx), bookTitle=meta['fileName']))
        recent.sort(key=lambda r: r['lastReadAt'], reverse=True)
        return jsonify(books=books, recent=recent[:80])

    @app.get('/api/books/<book_id>/cover')
    def cover(book_id):
        meta = book(book_id)
        path = store.cover_file(meta['id'])
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype='image/jpeg')

    @app.get('/api/books/<book_id>/file')
    def original_file(book_id):
        meta = book(book_id)
        return send_file(meta['path'], mimetype='application/pdf' if meta['ext'] == 'pdf' else 'application/epub+zip',
            as_attachment=meta['ext'] != 'pdf', download_name=meta['fileName'], conditional=True)

    @app.get('/api/books/<book_id>')
    def digest(book_id):
        meta = book(book_id)
        content = store.ensure_content(meta)
        summaries = {a['index']: a for a in (store.load_digest(book_id) or {}).get('articles', [])}
        states, history = store.load_article_states(book_id), store.load_reading_history(book_id)
        items = [dict(index=a['index'], titleEn=a['title'], titleZh=summaries.get(a['index'], {}).get('titleZh', ''),
            summaryZh=summaries.get(a['index'], {}).get('summaryZh', ''),
            state=states.get(str(a['index']), 'unread'), history=history.get(str(a['index']))) for a in content['articles']]
        return jsonify(id=book_id, fileName=meta['fileName'], ext=meta['ext'], articles=items)

    @app.post('/api/books/<book_id>/state')
    def mark(book_id):
        data = payload()
        index = data.get('articleIndex')
        if type(index) is not int:
            abort(400, '文章编号无效')
        article(book_id, index)
        with data_lock:
            result = store.set_article_state(book_id, index, data.get('state'))
        return jsonify(states=result)

    @app.get('/api/books/<book_id>/articles/<int:index>')
    def read(book_id, index):
        meta, art = article(book_id, index)
        summary = next((a for a in (store.load_digest(book_id) or {}).get('articles', []) if a['index'] == index), {})
        return jsonify(article=art, titleZh=summary.get('titleZh', ''), summaryZh=summary.get('summaryZh', ''),
            bookTitle=meta['fileName'], ext=meta['ext'], history=store.load_reading_history(book_id).get(str(index)),
            wordCount=len(gen._TOKEN.findall(' '.join(art['paragraphs']))),
            state=store.load_article_states(book_id).get(str(index), 'unread'))

    @app.post('/api/books/<book_id>/articles/<int:index>/progress')
    def save_progress(book_id, index):
        _, art = article(book_id, index)
        data = payload()
        para = data.get('paragraphIndex')
        offset = data.get('paragraphOffset', 0)
        if type(para) is not int or not 0 <= para < len(art['paragraphs']) or type(offset) is not int or offset < 0:
            abort(400, '阅读位置无效')
        offset = min(offset, len(art['paragraphs'][para]))
        progress = (sum(len(p) for p in art['paragraphs'][:para]) + offset) / max(1, sum(len(p) for p in art['paragraphs']))
        with data_lock:
            record = store.save_reading_progress(book_id, index, dict(title=art['title'], titleZh='',
                paragraphIndex=para, paragraphOffset=offset, paragraphCount=len(art['paragraphs']),
                paragraphStart=art['paragraphs'][para][:100], wordCount=len(gen._TOKEN.findall(' '.join(art['paragraphs']))),
                progress=progress, atStart=para == 0 and offset == 0, atEnd=progress >= .995))
        return jsonify(record=record)

    @app.get('/api/books/<book_id>/articles/<int:index>/vocab')
    def vocabulary(book_id, index):
        _, art = article(book_id, index)
        level = config.load_settings()['vocabLevel']
        cached = store.load_vocab(book_id, index, level)
        vocab = wordbook.overlay(gen.vocab_normalize(cached, art) if cached else None, art, level)
        missing = [i for i in range(len(art['paragraphs'])) if i not in (cached or {}).get('processedParagraphs', [])]
        return jsonify(vocab=vocab, missing=missing, generated=bool(cached), level=level)

    @app.post('/api/books/<book_id>/articles/<int:index>/lookup')
    def lookup(book_id, index):
        meta, art = article(book_id, index)
        data = payload()
        value = wordbook.normalize(data.get('word', ''))
        para = data.get('paragraph', 0)
        if type(para) is not int or not 0 <= para < len(art['paragraphs']):
            abort(400, '段落编号无效')
        core_dict.ensure_db(background=False)
        result = core_dict.lookup(value) or {}
        source = dict(bookId=book_id, bookTitle=meta['fileName'], articleIndex=index,
            articleTitle=art['title'], paragraph=para, context=art['paragraphs'][para][:800])
        saved = wordbook.remember_lookup(value, result, source)
        return jsonify(result=result, saved=saved)

    @app.get('/api/wordbook')
    def words():
        return jsonify(words=wordbook.list_words(request.args.get('q', '')[:200]))

    @app.post('/api/wordbook')
    def save_word():
        data = payload()
        return jsonify(word=wordbook.save(data, original=data.get('original')))

    @app.delete('/api/wordbook/<word>')
    def delete_word(word):
        return jsonify(deleted=wordbook.delete(word))

    @app.get('/api/settings')
    def settings():
        data = config.load_settings()
        return jsonify(vocabLevel=data['vocabLevel'], models=[dict(name=p.get('name', ''), model=p.get('model', ''),
            provider=p.get('provider', 'openai'), configured=llm.is_complete(p)) for p in data.get('llms', [])],
            defaultModel=config.default_llm_name(data), version=__version__)

    @app.post('/api/settings')
    def save_settings():
        data = payload()
        with data_lock:
            current = config.load_settings()
            if 'vocabLevel' in data:
                if data['vocabLevel'] not in gen.LEVEL_LABEL:
                    abort(400, '未知词汇级别')
                current['vocabLevel'] = data['vocabLevel']
            if data.get('configJson'):
                parsed = llm.parse_pasted_json(str(data['configJson'])[:40000])
                if not parsed:
                    abort(400, '无法识别配置 JSON')
                cfg, _ = parsed
                if error := llm.validate(cfg):
                    abort(400, error)
                name = str(data.get('name') or cfg['model']).strip()[:80]
                current['llms'] = [p for p in current['llms'] if p.get('name') != name] + [dict(cfg, name=name)]
                current['defaultLlm'] = name
            if data.get('defaultModel'):
                if data['defaultModel'] not in config.llm_names(current):
                    abort(400, '找不到模型')
                current['defaultLlm'] = data['defaultModel']
            store._atomic_json_write(config.settings_path(), current)
        return jsonify(ok=True)

    @app.post('/api/books/<book_id>/generate-digest')
    def generate_digest(book_id):
        meta, cfg = book(book_id), model(payload())
        def work(update, control):
            with lock_for(book_id):
                value = gen.generate_digest(meta, cfg,
                    on_progress=lambda done, total: update(status=f'摘要已完成 {done}/{total} 批'),
                    on_chars=lambda chars: update(status=f'摘要正在接收 · {chars} 字'))
                return dict(failed=value.get('failedBatches', 0))
        return accepted(jobs.submit(('digest', book_id), work))

    @app.post('/api/books/<book_id>/articles/<int:index>/generate-vocab')
    def generate_vocab(book_id, index):
        meta, art = article(book_id, index)
        data = payload()
        cfg = model(data)
        settings = config.load_settings()
        phrases = data.get('phrasesOnly') is True
        para = data.get('paragraph')
        if para is not None and (type(para) is not int or not 0 <= para < len(art['paragraphs'])):
            abort(400, '段落编号无效')
        def work(update, control):
            with lock_for(book_id):
                value = gen.generate_vocab(meta, art, settings['vocabLevel'], cfg,
                    on_progress=lambda done, total, name=None: update(status=f'已完成 {done}/{total} 块 · {name or cfg.get("name", "")}'),
                    on_chars=lambda chars: update(status=f'正在接收词表 · {chars} 字'),
                    llm_rotation=settings['llms'], phrases_only=phrases, only_para=para)
                return dict(failed=value.get('failedPhraseChunks' if phrases else 'failedChunks', 0))
        return accepted(jobs.submit(('vocab', book_id, index, settings['vocabLevel'], phrases, para), work))

    @app.post('/api/books/<book_id>/articles/<int:index>/analyze')
    def analyze(book_id, index):
        meta, art = article(book_id, index)
        data = payload()
        sentence = str(data.get('sentence', '')).strip()
        para = data.get('paragraph')
        if type(para) is not int or not 0 <= para < len(art['paragraphs']) or not sentence or len(sentence) > 5000 or sentence not in art['paragraphs'][para]:
            abort(400, '请选择同一段落中的原文句子')
        cfg = model(data)
        def work(update, control):
            with lock_for(book_id):
                text = art['paragraphs'][para]
                sents = gen.split_sentences(text)
                hit = gen.sentence_at(sents, text.find(sentence))
                before, after = gen.neighbors(sents, hit) if hit else ('', '')
                return gen.analyze(meta, art, sentence, before, after, cfg, on_progress=lambda p: update(
                    status=f'已接收 {p["chars"]} 字 · {p["elapsedMs"] / 1000:.0f} 秒'))
        return accepted(jobs.submit(('analysis', book_id, index, sentence), work))

    @app.get('/api/books/<book_id>/articles/<int:index>/discussion')
    def messages(book_id, index):
        meta, art = article(book_id, index)
        return jsonify(messages=discussion.load(meta, art))

    @app.post('/api/books/<book_id>/articles/<int:index>/discussion')
    def ask(book_id, index):
        meta, art = article(book_id, index)
        data = payload()
        question = str(data.get('question', '')).strip()
        quote = str(data.get('quote', '')).strip()
        para = data.get('paragraph')
        if not question or len(question) > 6000 or len(quote) > 5000:
            abort(400, '请输入问题，问题最多 6000 字，引用最多 5000 字')
        if quote and (type(para) is not int or not 0 <= para < len(art['paragraphs']) or quote not in art['paragraphs'][para]):
            abort(400, '引用内容与段落不匹配，请重新选择')
        cfg = model(data)
        def work(update, control):
            with lock_for(book_id):
                control.check()
                history = discussion.load(meta, art)
                system, request_messages, excerpted = discussion.build_request(meta, art, history, question, quote, para)
                history.extend([dict(role='user', content=question, status='complete', quote=quote,
                    paragraph=para, createdAt=time.time()), dict(role='assistant', content='', status='pending',
                    model=cfg.get('name', ''), createdAt=time.time())])
                discussion.save(meta, art, history)
                response, last_saved = history[-1], time.monotonic()
                def delta(text):
                    nonlocal last_saved
                    response['content'] = text
                    update(text=response['content'], status=f'正在回答 · {len(response["content"])} 字')
                    if time.monotonic() - last_saved > 2:
                        discussion.save(meta, art, history)
                        last_saved = time.monotonic()
                try:
                    update(status='等待模型回答', excerpted=excerpted)
                    llm.conversation(cfg, system, request_messages, on_text=delta, control=control)
                    response['status'] = 'complete'
                except llm.RequestCancelled:
                    response['status'] = 'stopped'
                    raise
                except Exception as error:
                    response.update(status='error', error=clean_error(error))
                    raise
                finally:
                    discussion.save(meta, art, history)
                return dict(excerpted=excerpted)
        return accepted(jobs.submit(('discussion', book_id, index, question, quote, para), work))

    @app.get('/api/jobs/<job_id>')
    def job(job_id):
        return jsonify(jobs.public(job_id))

    @app.post('/api/jobs/<job_id>/stop')
    def stop(job_id):
        jobs.public(job_id)
        with jobs.lock:
            current = jobs.items[job_id]
            if current['key'][0] != 'discussion':
                abort(400, '此任务暂不支持中途停止')
            current['control'].cancel()
        return jsonify(ok=True)

    @app.post('/api/books')
    def upload():
        file = request.files.get('file')
        if not file or not file.filename:
            abort(400, '请选择 EPUB 或 PDF 文件')
        name = Path(file.filename.replace('\\', '/')).name[:180]
        ext = Path(name).suffix.lower()
        if ext not in ('.epub', '.pdf'):
            abort(400, '仅支持 EPUB 和 PDF')
        bid = store.sanitize_id(Path(name).stem)
        if not BOOK_ID.fullmatch(bid) or bid in ('.', '..'):
            abort(400, '刊物文件名无效')
        if store.get_book(bid):
            abort(409, '同名刊物已在书库中')
        staging = root / 'uploads' / secrets.token_hex(12)
        staging.mkdir(parents=True)
        path = staging / name
        file.save(path)
        def work(update, control):
            try:
                update(status='正在解析刊物和提取封面')
                if ext == '.epub':
                    _, articles = store.epub_parse(str(path))
                else:
                    articles = store.pdf_parse(str(path))
                if not articles:
                    raise ValueError('没有找到可阅读的正文')
                with data_lock:
                    if store.get_book(bid):
                        raise ValueError('同名刊物已被其他设备添加')
                    meta, _ = store.add_book(str(path), str(root / 'books'))
                store.ensure_content(meta)
                store.ensure_cover(meta)
                return dict(bookId=meta['id'])
            finally:
                shutil.rmtree(staging)
        try:
            return accepted(jobs.submit(('upload', bid, secrets.token_hex(8)), work))
        except Exception:
            shutil.rmtree(staging)
            raise

    config.ensure_wordlists()
    return app
