"""User-owned article topics, independent of generated summaries and book files."""
from kandu import core_config as config, core_parsers as store


def normalize(tags):
    if not isinstance(tags, list) or len(tags) > 8:
        raise ValueError('每篇文章最多 8 个标签')
    result, seen = [], set()
    for tag in tags:
        if not isinstance(tag, str):
            raise ValueError('标签必须是文本')
        tag = ' '.join(tag.split())
        if not tag or len(tag) > 24:
            raise ValueError('每个标签应为 1 至 24 个字')
        if tag.casefold() not in seen:
            result.append(tag)
            seen.add(tag.casefold())
    return result


def path(book_id):
    return config.app_dir() / 'article_tags' / (store.sanitize_id(book_id) + '.json')


def load(book_id):
    return store._json_read(path(book_id)) or {}


def save(book_id, index, tags):
    tags = normalize(tags)
    value = load(book_id)
    if tags:
        value[str(index)] = tags
    else:
        value.pop(str(index), None)
    store._atomic_json_write(path(book_id), value)
    return tags
