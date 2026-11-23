"""Lossless paragraph-only PDF upgrades; article IDs and generated work stay intact."""
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from . import core_config as config, core_pdf


def paragraph_map(old, new):
    """Validate that the only change is joining adjacent blocks with a space."""
    mapping, cursor = {}, 0
    for index, paragraph in enumerate(new):
        combined = ''
        while cursor < len(old):
            offset = len(combined) + bool(combined)
            combined += (' ' if combined else '') + old[cursor]
            if not paragraph.startswith(combined):
                raise ValueError('PDF 正文发生变化，已保留原缓存，未迁移段落')
            mapping[cursor] = (index, offset)
            cursor += 1
            if combined == paragraph:
                break
        if combined != paragraph:
            raise ValueError('PDF 段落无法对应，已保留原缓存')
    if cursor != len(old):
        raise ValueError('PDF 段落数量无法对应，已保留原缓存')
    return mapping


def migrate(meta, cache, articles):
    from . import core_parsers as store, core_discussion as discussion, core_wordbook as wordbook

    old_articles = cache['articles']
    if len(old_articles) != len(articles):
        raise ValueError('PDF 文章目录发生变化，已保留原缓存')
    changes = {}
    for old, new in zip(old_articles, articles):
        if (old['index'], old['title']) != (new['index'], new['title']):
            raise ValueError('PDF 文章无法对应，已保留原缓存')
        mapping = paragraph_map(old['paragraphs'], new['paragraphs'])
        if old['paragraphs'] != new['paragraphs']:
            changes[old['index']] = (old, new, mapping)
    if not changes:
        return

    folder = store._cache_dir(meta['id'])
    backup = folder / f'before_pdf_paragraphs_v{core_pdf.PARAGRAPH_VERSION}'
    backup.mkdir(exist_ok=True)

    def original(path, name=None):
        saved = backup / (name or path.name)
        if path.exists() and not saved.exists():
            # A process interruption must not leave a partial backup that a
            # retry would mistake for the immutable original.
            with tempfile.NamedTemporaryFile(dir=backup, suffix='.tmp', delete=False) as f:
                temporary = Path(f.name)
            try:
                shutil.copy2(path, temporary)
                os.replace(temporary, saved)
            finally:
                temporary.unlink(missing_ok=True)
        return store._json_read(saved)

    original(folder / 'content.json')
    # These files need no edits; keep a snapshot alongside migrated data.
    original(folder / 'digest.json')
    history_path = store._reading_history_file(meta['id'])
    history = original(history_path, 'reading_history.json')
    if history:
        for key, record in history.items():
            if not key.isdecimal() or int(key) not in changes:
                continue
            _, article, mapping = changes[int(key)]
            if record.get('paragraphIndex') in mapping:
                index, offset = mapping[record['paragraphIndex']]
                record.update(paragraphIndex=index, paragraphOffset=offset + record.get('paragraphOffset', 0),
                              paragraphCount=len(article['paragraphs']), paragraphStart=article['paragraphs'][index][:160])
        store._atomic_json_write(history_path, history)

    for path in folder.glob('vocab_*.json'):
        match = re.fullmatch(r'vocab_(\d+)_.+\.json', path.name)
        if not match or int(match[1]) not in changes:
            continue
        _, article, mapping = changes[int(match[1])]
        vocab = original(path)
        if not isinstance(vocab, dict):
            continue
        sections = {}
        for section in vocab.get('paragraphs', []):
            if section.get('index') not in mapping:
                continue
            index = mapping[section['index']][0]
            target = sections.setdefault(index, dict(section, index=index,
                                        startWords=article['paragraphs'][index][:30], entries=[]))
            for entry in section.get('entries', []):
                if entry not in target['entries']:
                    target['entries'].append(entry)
        vocab['paragraphs'] = [sections[i] for i in sorted(sections)]
        for field in ('processedParagraphs', 'phraseProcessedParagraphs'):
            if field in vocab:
                done = set(vocab[field])
                groups = {}
                for old_index, (new_index, _) in mapping.items():
                    groups.setdefault(new_index, set()).add(old_index)
                vocab[field] = ([-1] if -1 in done else []) + [i for i, group in groups.items() if group <= done]
        store._atomic_json_write(path, vocab)

    for old, article, mapping in changes.values():
        path = discussion.history_path(meta, old)
        data = original(path, 'discussion_' + path.name)
        if not data:
            continue
        for message in data.get('messages', []):
            if message.get('paragraph') in mapping:
                message['paragraph'] = mapping[message['paragraph']][0]
        data['articleKey'] = discussion.article_key(meta, article)
        store._atomic_json_write(discussion.history_path(meta, article), data)

    # Snapshot only the source fields being changed. Compare-and-swap avoids
    # touching a later personal edit, including when an interrupted upgrade retries.
    snapshot = backup / 'wordbook_sources.json'
    if (config.app_dir() / 'wordbook.sqlite3').exists():
        with wordbook.connection() as db:
            if not snapshot.exists():
                sources = []
                for row in db.execute('SELECT word, source FROM words'):
                    source = json.loads(row['source'])
                    if source.get('bookId') == meta['id'] and source.get('articleIndex') in changes:
                        sources.append(dict(row))
                store._atomic_json_write(snapshot, sources)
            for row in store._json_read(snapshot) or []:
                source = json.loads(row['source'])
                mapping = changes[source['articleIndex']][2]
                if source.get('paragraph') in mapping:
                    source['paragraph'] = mapping[source['paragraph']][0]
                    db.execute('UPDATE words SET source=? WHERE word=? AND source=?',
                               (json.dumps(source, ensure_ascii=False), row['word'], row['source']))


def upgrade(meta, cache):
    from . import core_parsers as store

    # Old synthetic/imported caches without PDF page provenance are left alone.
    if any(a.get('paragraphPages') for a in cache['articles']):
        articles = core_pdf.parse(meta['path'])
        migrate(meta, cache, articles)
        cache = dict(cache, articles=articles)
    cache = dict(cache, pdfParagraphVersion=core_pdf.PARAGRAPH_VERSION)
    # Commit last: an interrupted migration reuses immutable backups on retry.
    store._atomic_json_write(store._cache_dir(meta['id']) / 'content.json', cache)
    return cache
