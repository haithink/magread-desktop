"""Completed reading metadata only; no article bodies or model credentials."""
import hashlib
import re
import time
import unicodedata
import threading
from functools import wraps
from urllib.parse import urlsplit, urlunsplit

from kandu import core_config as config, core_gen as gen, core_parsers as store

_LOCK = threading.RLock()


def synchronized(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return call


def title_key(title):
    # Ignore typography, punctuation and case, retaining word boundaries.
    text = unicodedata.normalize('NFKC', title).casefold()
    text = re.sub(r"['’‘`ʼ]", '', text)
    return ' '.join(''.join(' ' if unicodedata.category(c).startswith(('P', 'Z')) else c
                            for c in text).split())


def load():
    data = store._json_read(config.app_dir() / 'reading-stats.json')
    if not isinstance(data, dict):
        data = {}
    return dict(sources=data.get('sources', {}), excluded=data.get('excluded', []))


def save(data):
    store._atomic_json_write(config.app_dir() / 'reading-stats.json', data)


def web_id(book_id, index):
    return f'web:{book_id}:{index}'


def merge(data, source):
    key = source['id']
    previous = data['sources'].get(key, {})
    source['firstReadAt'] = min(previous.get('firstReadAt', source['readAt']), source['readAt'])
    source['readAt'] = max(previous.get('readAt', 0), source['readAt'])
    source['wordCount'] = max(previous.get('wordCount', 0), source['wordCount'])
    data['sources'][key] = source


def web_source(meta, art, when=None):
    return dict(id=web_id(meta['id'], art['index']), kind='web', title=art['title'],
                wordCount=len(gen._TOKEN.findall(' '.join(art['paragraphs']))),
                bookId=meta['id'], articleIndex=art['index'], source=meta.get('title') or meta['fileName'],
                publication=meta.get('publication', ''),
                readAt=when or int(time.time() * 1000))


@synchronized
def mark_web(meta, art, completed):
    data = load()
    key = web_id(meta['id'], art['index'])
    data['excluded'] = [k for k in data['excluded'] if k != key]
    if completed:
        merge(data, web_source(meta, art))
    else:
        data['sources'].pop(key, None)
        data['excluded'].append(key)
    save(data)


@synchronized
def mark_cached(meta, index):
    """UI marks are explicit completion evidence; never infer it from scrolling."""
    completed = store.load_article_states(meta['id']).get(str(index)) == 'read'
    cached = store._json_read(store._cache_dir(meta['id']) / 'content.json') or {}
    article = next((a for a in cached.get('articles', []) if a['index'] == index), None)
    if article:
        mark_web(meta, article, completed)
    elif not completed:
        data = load()
        key = web_id(meta['id'], index)
        data['sources'].pop(key, None)
        if key not in data['excluded']:
            data['excluded'].append(key)
        save(data)


@synchronized
def summary(days=365, timezone_offset=0):
    data = load()
    before = repr(data)
    # Migrate existing completion evidence using caches only. Never parse a book
    # or call a model just to display statistics.
    library = store.load_library()
    for meta in library:
        states = store.load_article_states(meta['id'])
        history = store.load_reading_history(meta['id'])
        eligible = {int(i) for i, value in states.items() if value == 'read'}
        eligible = {i for i in eligible if web_id(meta['id'], i) not in data['sources']
                    and web_id(meta['id'], i) not in data['excluded']}
        if not eligible:
            continue
        cached = store._json_read(store._cache_dir(meta['id']) / 'content.json') or {}
        articles = {a['index']: a for a in cached.get('articles', [])}
        for index in eligible:
            h = history.get(str(index), {})
            if index in articles:
                source = web_source(meta, articles[index], h.get('lastReadAt'))
            elif h.get('title'):
                source = dict(id=web_id(meta['id'], index), kind='web', bookId=meta['id'],
                              articleIndex=index, source=meta.get('title') or meta['fileName'], title=h['title'],
                              wordCount=h.get('wordCount', 0), readAt=h['lastReadAt'])
            else:
                continue
            merge(data, source)
    if repr(data) != before:
        save(data)
    groups = {}
    for source in data['sources'].values():
        key = title_key(source['title'])
        if not key:
            continue
        item = groups.setdefault(key, dict(title=source['title'], wordCount=0, readAt=0, sources=[]))
        item['wordCount'] = max(item['wordCount'], source['wordCount'])
        item['readAt'] = max(item['readAt'], source['readAt'])
        item['sources'].append(source)
    articles = sorted(groups.values(), key=lambda a: a['readAt'], reverse=True)
    from kandu import reading_activity
    publications = {m['id']:m.get('publication') or re.sub(r'[_ .-]*\d{4}.*$', '', m['fileName']).replace('_',' ').strip()
        for m in library}
    analytics = reading_activity.report(articles, days=days, timezone_offset=timezone_offset, publications=publications)
    return dict(articleCount=len(articles), totalWords=sum(a['wordCount'] for a in articles),
        articles=articles, analytics=analytics)


def imported_source(value):
    if not isinstance(value, dict):
        raise ValueError('文章记录格式无效')
    title = value.get('title')
    count = value.get('wordCount')
    when = value.get('readAt')
    url = value.get('url')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 500 or not title_key(title):
        raise ValueError('文章标题无效')
    if type(count) is not int or not 1 <= count <= 10_000_000:
        raise ValueError('文章词数无效')
    if type(when) is not int or not 0 < when <= int(time.time() * 1000) + 300_000:
        raise ValueError('阅读时间无效')
    if not isinstance(url, str) or len(url) > 4000 or any(ord(c) < 33 for c in url):
        raise ValueError('原文链接无效')
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('只接受 HTTP / HTTPS 原文链接')
    parsed.port  # Validate the port before saving.
    url = urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, parsed.query, ''))
    return dict(id='chrome:' + hashlib.sha256(url.encode()).hexdigest(), kind='chrome',
                title=title.strip(), url=url, wordCount=count, source=parsed.hostname, readAt=when)


def import_records(records):
    if not isinstance(records, list) or not 0 <= len(records) <= 100:
        raise ValueError('每次同步最多包含 100 条文章记录')
    if not records:  # An authenticated connection check, with no side effects.
        return 0
    sources = [imported_source(value) for value in records]
    data = load()
    for source in sources:
        merge(data, source)
    save(data)
    return len(sources)
