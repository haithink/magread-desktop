"""One-time snapshot of desktop results into a separate web data directory."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile


def import_desktop(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Web and desktop data directories must be separate')
    if destination.exists():
        raise FileExistsError('Web data already exists; the desktop snapshot will not replace it')
    library = json.loads((source / 'books.json').read_text(encoding='utf-8-sig'))
    settings = json.loads((source / 'settings.json').read_text(encoding='utf-8-sig'))
    if not isinstance(library, list) or not isinstance(settings, dict):
        raise ValueError('Invalid desktop library')
    missing = [m['fileName'] for m in library if not Path(m['path']).is_file()]
    if missing:
        raise FileNotFoundError(f'{len(missing)} original publication files are missing')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kandu-web-import-', dir=destination.parent) as tmp:
        staging = Path(tmp) / 'data'
        staging.mkdir()
        for name in ('cache', 'covers', 'reading_history', 'article_states', 'discussions', 'vocab'):
            if (source / name).exists():
                shutil.copytree(source / name, staging / name)
        for name in ('wordbook.sqlite3', 'ecdict.db'):
            file = source / name
            if file.exists():
                with closing(sqlite3.connect(file.as_uri() + '?mode=ro', uri=True)) as src:
                    with closing(sqlite3.connect(staging / name)) as dst:
                        src.backup(dst)
        (staging / 'books.json').write_text(json.dumps(library, ensure_ascii=False), encoding='utf-8')
        settings.update(booksDir=str(destination / 'books'), libraryDir='', migratedLibrary=True)
        (staging / 'settings.json').write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')
        (staging / 'desktop-import.json').write_text(json.dumps(dict(
            source=str(source), importedAt=datetime.now(timezone.utc).isoformat(), books=len(library),
            note='Original publication files are referenced; generated/user data is an independent snapshot.'
        ), ensure_ascii=False), encoding='utf-8')
        os.rename(staging, destination)
    return len(library)
