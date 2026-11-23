"""Bounded sentence context and complete JSON fields from a streaming answer."""
import hashlib
import json

CONTEXT_VERSION = 1


def context(article, paragraph, sentence, start=None):
    if type(paragraph) is not int or not 0 <= paragraph < len(article['paragraphs']):
        raise ValueError('段落编号无效')
    text = article['paragraphs'][paragraph]
    if start is None:
        start = text.find(sentence)
    if type(start) is not int or start < 0 or text[start:start+len(sentence)] != sentence:
        raise ValueError('句子位置与原文不匹配，请重新选择')
    # Retain the entire selected sentence, centering the bounded paragraph
    # window on its occurrence rather than cutting from the paragraph start.
    left = max(0, start - max(0, (6000-len(sentence))//2))
    right = min(len(text), max(left+6000, start+len(sentence)))
    return dict(articleTitle=article['title'][:500], sentence=sentence,
        paragraphIndex=paragraph, paragraphOffset=left,
        selectedStart=start-left, selectedEnd=start-left+len(sentence),
        paragraph=text[left:right],
        previousParagraph=article['paragraphs'][paragraph-1][-1000:] if paragraph else '',
        nextParagraph=article['paragraphs'][paragraph+1][:1000] if paragraph+1 < len(article['paragraphs']) else '')


def identity(context, cfg):
    model = [cfg.get(k, '') for k in ('provider', 'baseUrl', 'model')]
    if cfg.get('thinkingLevel', 'auto') != 'auto':
        model.append(cfg['thinkingLevel'])
    return hashlib.sha256(json.dumps([CONTEXT_VERSION, context, model],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def partial_object(raw):
    """Only return completed top-level fields; never repair an unfinished value.

    Reading field by field avoids treating braces/keys inside quoted strings
    as structure, and lets an early meaning/translation precede long arrays.
    """
    decoder = json.JSONDecoder()
    start = raw.find('{')
    if start < 0:
        return {}
    cursor, result = start+1, {}
    try:
        while cursor < len(raw):
            while cursor < len(raw) and raw[cursor].isspace():
                cursor += 1
            if cursor == len(raw) or raw[cursor] == '}':
                break
            key, cursor = decoder.raw_decode(raw, cursor)
            if not isinstance(key, str):
                break
            while cursor < len(raw) and raw[cursor].isspace():
                cursor += 1
            if cursor == len(raw) or raw[cursor] != ':':
                break
            cursor += 1
            while cursor < len(raw) and raw[cursor].isspace():
                cursor += 1
            value, cursor = decoder.raw_decode(raw, cursor)
            result[key] = value
            while cursor < len(raw) and raw[cursor].isspace():
                cursor += 1
            if cursor == len(raw) or raw[cursor] != ',':
                break
            cursor += 1
    except (ValueError, TypeError):
        pass
    return result
