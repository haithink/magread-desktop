"""Export a self-contained reading library without model credentials (stdlib only)."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import zipfile


SAFE_SETTINGS = ('vocabLevel', 'levelChosen', 'readerFontSize', 'sheetFontSize', 'recentBooks')
DATA_FOLDERS = ('cache', 'covers', 'reading_history', 'article_states', 'discussions', 'vocab', 'article_tags', 'podcasts')


def export_data(source, output, on_progress=print):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source):
        raise ValueError('Save the backup outside the application data directory')
    if output.exists():
        raise FileExistsError(f'Backup already exists: {output}')
    library = json.loads((source / 'books.json').read_text(encoding='utf-8-sig'))
    settings_path = source / 'settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.exists() else {}
    if not isinstance(library, list) or not isinstance(settings, dict):
        raise ValueError('Invalid library or settings')

    books, entries, ids = [], [], set()
    for entry in library:
        bid, ext = entry['id'], entry['ext']
        if not re.fullmatch(r'[A-Za-z0-9._-]+', bid) or bid in ('.', '..') or bid in ids or ext not in ('epub', 'pdf'):
            raise ValueError(f'Invalid or duplicate book ID: {bid}')
        ids.add(bid)
        book = Path(entry['path'])
        if not book.is_file():
            raise FileNotFoundError(f'Missing book: {book}')
        if book.stat().st_size != entry['size']:
            raise ValueError(f'Book changed since import: {book}')
        relative = f'books/{bid}.{ext}'
        books.append((book, relative))
        entries.append({**{k: entry[k] for k in ('id', 'fileName', 'ext', 'size', 'lastModified', 'addedAt',
                        'title', 'publication', 'sourceType', 'sourceId', 'sourceUrl', 'episodeNumber',
                        'publishedAt', 'guest') if k in entry},
                        'path': relative})

    safe = {k: settings[k] for k in SAFE_SETTINGS if k in settings}
    safe.update(booksDir='', libraryDir='', migratedLibrary=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = 'Kandu-reading-data/'
    manifest = dict(format='kandu-reading-data', version=1,
                    createdAt=datetime.now(timezone.utc).isoformat(), books=len(entries), files=[])
    fd, temporary = tempfile.mkstemp(prefix=output.stem + '-', suffix='.partial', dir=output.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            def add(relative, file=None, data=None, stored=False):
                name = 'data/' + relative
                digest, size = hashlib.sha256(), 0
                info = zipfile.ZipInfo(prefix + name)
                info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
                before = file.stat() if file else None
                with archive.open(info, 'w', force_zip64=True) as target:
                    if file:
                        with file.open('rb') as stream:
                            while chunk := stream.read(1024 * 1024):
                                target.write(chunk)
                                digest.update(chunk)
                                size += len(chunk)
                    else:
                        target.write(data)
                        digest.update(data)
                        size = len(data)
                if file:
                    after = file.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise RuntimeError(f'File changed during export; retry after generation finishes: {file}')
                manifest['files'].append(dict(path=name, size=size, sha256=digest.hexdigest()))

            def add_json(relative, value):
                add(relative, data=json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))

            add_json('books.json', entries)
            add_json('settings.json', safe)
            for i, (book, relative) in enumerate(books, 1):
                on_progress(f'Books {i}/{len(books)}: {book.name}')
                add(relative, file=book, stored=True)
            for folder in DATA_FOLDERS:
                root = source / folder
                if not root.exists():
                    continue
                for file in sorted(root.rglob('*')):
                    if file.is_file():
                        if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
                            raise ValueError(f'Linked data file is unsupported: {file}')
                        add(file.relative_to(source).as_posix(), file=file)
            stats = source / 'reading-stats.json'
            if stats.exists():
                add(stats.name, file=stats)
            for database in (source / name for name in ('wordbook.sqlite3', 'reading-activity.sqlite3')):
                if not database.exists():
                    continue
                # SQLite backup includes committed WAL changes even if the reader is open.
                with tempfile.TemporaryDirectory(prefix='kandu-wordbook-') as staging:
                    snapshot = Path(staging) / database.name
                    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as src:
                        with closing(sqlite3.connect(snapshot)) as dst:
                            src.backup(dst)
                            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                                raise ValueError('Personal wordbook failed integrity check')
                    add(database.name, file=snapshot)
            archive.writestr(prefix + 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.write(Path(__file__).with_name('restore_reading_data.ps1'), prefix + 'Restore-ReadingData.ps1')
            archive.writestr(prefix + 'Restore-ReadingData.cmd',
                '@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Restore-ReadingData.ps1"\r\n'
                'if errorlevel 1 echo Restore failed. Your previous library is preserved.\r\npause\r\n')
            archive.writestr(prefix + '使用说明.txt',
                '刊读阅读数据迁移包\r\n\r\n'
                '1. 在新电脑完整解压此压缩包（不要在压缩软件里直接运行）。\r\n'
                '2. 关闭新电脑上正在运行的刊读，双击 Restore-ReadingData.cmd。\r\n'
                '3. 看到 RESTORE COMPLETE 后，打开刊读 v0.8.18 或更新版本。无需重新导入刊物。\r\n\r\n'
                '包含：原刊文件、摘要、生词、句解、封面、阅读记录、文章标记、个人生词本、AI 讨论、阅读统计、文章标签和播客元数据。\r\n'
                '恢复会自动修正刊物路径，保留原书籍 ID，使已有生成结果继续生效。\r\n'
                '目标电脑原数据目录会先改名备份，再恢复本包的整个书库；此操作不是两份书库的合并。\r\n'
                '目标电脑已配置的 LLM 保留；包内不带源电脑的 API 密钥，首次使用需自行配置。\r\n'
                '如果使用 KANDU_DATA_DIR 环境变量指定了数据目录，脚本将恢复到该目录。\r\n')
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('Backup integrity check failed')
        temporary.rename(output)
    finally:
        temporary.unlink(missing_ok=True)
    on_progress(f'EXPORT COMPLETE: {output} ({output.stat().st_size / 1024**2:.1f} MiB)')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(os.environ.get('KANDU_DATA_DIR') or
                        Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'KanduDesktop'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export_data(args.source, args.output)
