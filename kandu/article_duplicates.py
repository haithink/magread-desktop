"""Find earlier occurrences by title without changing articles or parsing other books."""
from datetime import date
from functools import lru_cache
from pathlib import Path
import re

from kandu import core_config as config, core_parsers as store
from kandu.reading_stats import title_key


_ISSUE_DATE = re.compile(r'(?<!\d)((?:19|20)\d{2})[._\s-](\d{1,2})'
                         r'(?:[._\s-](\d{1,2}))?(?!\d)')


def issue_date(meta):
    """Use the publication date in the filename, never import time or file modification time."""
    match = _ISSUE_DATE.search(Path(meta.get('fileName', '')).stem)
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3] or 1))
    except ValueError:
        return None


@lru_cache(maxsize=128)
def _cached_titles(path, mtime_ns, file_size, source_size, ext, kind):
    # Keep only a small title/index list in memory, not full article bodies. The
    # file signature invalidates it after generation, replacement or migration.
    data = store._json_read(Path(path))
    if not isinstance(data, dict):
        return ()
    if kind == 'content':
        if data.get('bookSize') != source_size or data.get('format') != ext:
            return ()
        if ext == 'pdf' and data.get('pdfParserVersion') != store.PDF_PARSER_VERSION:
            return ()
    elif ext == 'pdf' and (data.get('pdfParserVersion') != store.PDF_PARSER_VERSION
                           or data.get('pdfSourceSize') != source_size):
        return ()
    entries = data.get('articles', [])
    if not isinstance(entries, list):
        return ()
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or type(entry.get('index')) is not int or entry['index'] < 0:
            continue
        title = entry.get('title' if kind == 'content' else 'titleEn')
        key = title_key(title) if isinstance(title, str) else ''
        if key:
            result.append((key, entry['index']))
    return tuple(result)


def cached_titles(meta):
    folder = config.app_dir() / 'cache' / store.sanitize_id(meta['id'])
    for kind, name in [('content', 'content.json'), ('digest', 'digest.json')]:
        path = folder / name
        try:
            stat = path.stat()
        except OSError:
            continue
        # Existing but invalid content must not fall back to a potentially stale
        # summary with old indices. Missing caches never trigger an EPUB/PDF parse.
        return _cached_titles(str(path), stat.st_mtime_ns, stat.st_size, meta.get('size'), meta['ext'], kind)
    return ()


def earlier_occurrences(meta, articles):
    current = issue_date(meta)
    if current is None:
        return {}
    wanted = {title_key(a['title']) for a in articles} - {''}
    if not wanted:
        return {}
    earlier = []
    for candidate in store.load_library():
        published = issue_date(candidate)
        if candidate['id'] != meta['id'] and published is not None and published < current:
            earlier.append((published, candidate['fileName'], candidate['id'], candidate))
    found = {}
    for published, _, _, candidate in sorted(earlier):
        for key, index in cached_titles(candidate):
            if key in wanted and key not in found:
                found[key] = dict(bookId=candidate['id'], articleIndex=index,
                                  fileName=candidate['fileName'], issueDate=published.isoformat())
        if len(found) == len(wanted):
            break
    return {a['index']: found[key] for a in articles if (key := title_key(a['title'])) in found}


def library_counts(library, digests):
    """Count each issue's visible articles in one chronological pass over cached titles."""
    result, dated, earlier_titles = {}, {}, set()

    def count(meta, titles, previous):
        visible = {index for key, index in titles if key not in previous}
        hidden = {index for key, index in titles if key in previous}
        states = store.load_article_states(meta['id'])
        unread = sum(states.get(str(index), 'unread') == 'unread' for index in visible)
        summaries = {entry['index'] for entry in digests.get(meta['id'], {}).get('articles', [])
                     if entry.get('index') in visible and entry.get('summaryZh')
                     and not entry['summaryZh'].startswith('（本篇摘要生成失败')}
        result[meta['id']] = dict(articleCount=len(visible), unreadCount=unread, summaryCount=len(summaries),
            totalArticleCount=len(visible | hidden), duplicateCount=len(hidden), articleCountKnown=bool(titles))

    for meta in library:
        titles = cached_titles(meta)
        published = issue_date(meta)
        if published is None:
            count(meta, titles, set())
        else:
            dated.setdefault(published, []).append((meta, titles))
    for published in sorted(dated):
        # Same-day PDF/EPUB versions must not hide each other. Add their titles
        # only after counting every issue on that date, matching the directory.
        for meta, titles in dated[published]:
            count(meta, titles, earlier_titles)
        for _, titles in dated[published]:
            earlier_titles.update(key for key, _ in titles)
    return result
