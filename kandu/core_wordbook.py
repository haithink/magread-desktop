"""跨刊物个人生词本；独立 SQLite 存储，文章生词表按需叠加，不污染模型缓存。"""
import copy
import json
import re
import sqlite3
import time
from contextlib import contextmanager

from . import core_config as cfgmod


def normalize(word):
    word = re.sub(r"\s+", " ", str(word or '').replace('’', "'").strip().lower())
    if not re.fullmatch(r"[a-z]+(?:[' -][a-z]+)*", word) or len(word) > 100:
        raise ValueError('请输入英文单词或短语（最多 100 个字符）')
    return word


@contextmanager
def connection():
    db = sqlite3.connect(cfgmod.app_dir() / 'wordbook.sqlite3', timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS words (
            word TEXT PRIMARY KEY, phonetic TEXT NOT NULL DEFAULT '', pos TEXT NOT NULL DEFAULT '',
            sense TEXT NOT NULL DEFAULT '', root TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
            aliases TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, lookups INTEGER NOT NULL DEFAULT 0)''')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _record(row):
    if row is None:
        return None
    value = dict(row)
    value['aliases'] = json.loads(value['aliases'])
    value['source'] = json.loads(value['source'])
    return value


def get(word):
    word = normalize(word)
    with connection() as db:
        return _record(db.execute('SELECT * FROM words WHERE word=?', (word,)).fetchone())


def list_words(query=''):
    with connection() as db:
        rows = [_record(r) for r in db.execute('SELECT * FROM words ORDER BY updated_at DESC, word')]
    query = str(query).strip().casefold()
    return [r for r in rows if not query or query in '\n'.join(
        [r['word'], r['sense'], r['note'], *r['aliases']]).casefold()]


def save(value, original=None):
    """手动新增或编辑；重命名冲突明确报错，避免覆盖另一词的笔记。"""
    word = normalize(value.get('word'))
    original = normalize(original) if original else None
    now = time.time_ns() // 1_000_000
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM words WHERE word=?', (original or word,)).fetchone()
        if original and old is None:
            raise ValueError('这个词已被删除，请刷新后重试')
        if original and original != word and db.execute('SELECT 1 FROM words WHERE word=?', (word,)).fetchone():
            raise ValueError('生词本中已有这个单词，请选择已有词条编辑')
        record = _record(old) or dict(word=word, aliases=[], source={}, lookups=0, created_at=now)
        for field in ('phonetic', 'pos', 'sense', 'root', 'note'):
            record[field] = str(value.get(field, record.get(field, ''))).strip()[:12000]
        if 'source' in value:
            record['source'] = value['source']
        if original and original != word:
            # The old spelling is no longer a match after an explicit rename.
            record['aliases'] = []
            db.execute('DELETE FROM words WHERE word=?', (original,))
        _write(db, dict(record, word=word, updated_at=now))
    return get(word)


def _write(db, r):
    db.execute('''INSERT OR REPLACE INTO words
        (word,phonetic,pos,sense,root,note,aliases,source,created_at,updated_at,lookups)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
        (r['word'], r.get('phonetic', ''), r.get('pos', ''), r.get('sense', ''), r.get('root', ''),
         r.get('note', ''), json.dumps(r['aliases'], ensure_ascii=False),
         json.dumps(r['source'], ensure_ascii=False), r['created_at'], r['updated_at'], r['lookups']))


def remember_lookup(word, result, source):
    """重复查词只补空字段，保留手工修改的释义、词源和笔记。"""
    result = result or {}
    raw = normalize(word)
    key = normalize(result.get('lemma') or result.get('word') or raw)
    now = time.time_ns() // 1_000_000
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM words WHERE word=?', (key,)).fetchone()
        r = _record(row) or dict(word=key, aliases=[], source={}, created_at=now, lookups=0)
        defaults = dict(phonetic=result.get('phonetic', ''), pos=result.get('pos', ''),
                        sense=result.get('translation', '').replace('\\n', '；').replace('\n', '；'), root='', note='')
        for field, value in defaults.items():
            r[field] = r.get(field) or value
        r['aliases'] = list(dict.fromkeys([*r['aliases'], raw]))
        r.update(source=source, updated_at=now, lookups=r['lookups'] + 1)
        _write(db, r)
    return get(key)


def delete(word):
    with connection() as db:
        return db.execute('DELETE FROM words WHERE word=?', (normalize(word),)).rowcount > 0


def article_matches(article, records=None):
    """词边界、大小写与词典原形匹配；不会把 cat 匹配到 education。"""
    records = list_words() if records is None else records
    if not records:
        return {}
    from . import core_dict, core_gen
    units = [(-1, article.get('title', '')), *enumerate(article.get('paragraphs', []))]
    tokens = {i: list(core_gen._TOKEN.finditer(text.replace('’', "'"))) for i, text in units}
    lexicon = core_dict.lookup_many([m.group().strip("'-").lower() for matches in tokens.values() for m in matches])
    aliases = {}
    phrases = []
    for record in records:
        for spelling in {record['word'], *record['aliases']}:
            if ' ' in spelling:
                phrases.append((re.compile(r"(?<![a-z])" + re.escape(spelling).replace(r'\ ', r'\s+') + r"(?![a-z])", re.I), record))
            else:
                aliases[spelling] = record
    result = {}
    for index, text in units:
        hits = {}
        clean = text.replace('’', "'")
        for match in tokens[index]:
            raw = match.group().strip("'-").lower()
            hit = lexicon.get(raw) or {}
            forms = [raw, hit.get('lemma'), hit.get('word')]
            if not hit or hit.get('miss'):
                forms += core_gen.lemma_forms(raw)
            record = next((aliases[f] for f in forms if f in aliases), None)
            if record:
                hits.setdefault(record['word'], (record, match.start(), match.end()))
        for pattern, record in phrases:
            match = pattern.search(clean)
            if match:
                hits.setdefault(record['word'], (record, match.start(), match.end()))
        if hits:
            result[index] = [dict(record, inText=text[max(0, start - 45):min(len(text), end + 65)].strip())
                             for record, start, end in hits.values()]
    return result


def overlay(vocab, article, level='cet4', matches=None):
    """个人收藏实时叠加，既适用于旧缓存，也适用于尚未生成生词表的文章。"""
    out = copy.deepcopy(vocab or dict(level=level, articleIndex=article['index'],
                                    titleEntries=[], paragraphs=[], idioms=[]))
    matches = article_matches(article) if matches is None else matches
    sections = {p['index']: p for p in out.get('paragraphs', [])}
    for index, records in matches.items():
        if index == -1:
            entries = out.setdefault('titleEntries', [])
        else:
            section = sections.setdefault(index, dict(index=index, startWords=article['paragraphs'][index][:30], entries=[]))
            entries = section['entries']
        for r in records:
            spellings = {r['word'], *r['aliases']}
            existing = next((e for e in entries if e['word'].lower() in spellings), None)
            if existing is not None:
                existing['inWordbook'] = True
                existing['wordbookWord'] = r['word']
                continue
            entries.append(dict(word=r['word'], phonetic=r['phonetic'], pos=r['pos'], sense=r['sense'],
                                root=r['root'], note=r['note'], inText=r['inText'], term=False,
                                inWordbook=True, wordbookWord=r['word'], senseSource='wordbook'))
    out['paragraphs'] = sorted(sections.values(), key=lambda p: p['index'])
    return out
