"""One shelf entry per Podcast; episode identities and learning data stay intact."""
from pathlib import Path

from kandu import core_parsers as store
from kandu import podcasts, article_tags


PREFIX = 'podcast-series-'


def source_id(meta):
    value = meta.get('sourceId')
    return value if meta.get('sourceType') == 'podcast' and value in podcasts.SOURCES else None


def parent_id(meta):
    value = source_id(meta)
    return PREFIX + value if value else None


def episodes(value, library=None):
    entries = store.load_library() if library is None else library
    return sorted((m for m in entries if source_id(m) == value),
        key=lambda m: (m.get('episodeNumber', 0), m.get('publishedAt', ''), m['id']), reverse=True)


def metadata(book_id, library=None):
    if not book_id.startswith(PREFIX):
        return None
    value = book_id[len(PREFIX):]
    if value not in podcasts.SOURCES:
        return None
    entries = episodes(value, library)
    if not entries:
        return None
    spec = podcasts.source(value)
    return dict(id=book_id, sourceId=value, sourceType='podcast-series', title=spec['name'],
        publication=spec['name'], fileName=spec['name'], ext='podcast', sourceUrl=spec['url'],
        available=any(Path(m['path']).is_file() for m in entries))


def directory(meta):
    items = []
    for episode in episodes(meta['sourceId']):
        digest = store.load_digest(episode['id']) or {}
        summary = next((a for a in digest.get('articles', []) if a['index'] == 0), {})
        # Every imported transcript is exactly one article. Never parse all full
        # transcripts just to display this lightweight series directory.
        items.append(dict(index=len(items), bookId=episode['id'], articleIndex=0,
            titleEn=episode.get('title') or episode['fileName'], titleZh=summary.get('titleZh', ''),
            summaryZh=summary.get('summaryZh', ''), episodeNumber=episode.get('episodeNumber', 0),
            publishedAt=episode.get('publishedAt', ''), guest=episode.get('guest', ''),
            sourceUrl=episode.get('sourceUrl', ''),
            tags=article_tags.load(episode['id']).get('0', []),
            state=store.load_article_states(episode['id']).get('0', 'unread'),
            history=store.load_reading_history(episode['id']).get('0'), earlierOccurrence=None))
    return items


def card(meta, entries, digests):
    unread = sum(store.load_article_states(m['id']).get('0', 'unread') == 'unread' for m in entries)
    summaries = sum(any(a.get('index') == 0 and a.get('summaryZh') and
        not a['summaryZh'].startswith('（本篇摘要生成失败')
        for a in digests[m['id']].get('articles', [])) for m in entries)
    return dict(meta, cover=True, articleCount=len(entries), unreadCount=unread, summaryCount=summaries,
        totalArticleCount=len(entries), duplicateCount=0, articleCountKnown=True)
