"""本地词典（ECDICT 精简版，77 万词条）：首次使用解压并建 SQLite 索引，
之后毫秒级离线查词，不依赖 LLM 与网络。"""
import sqlite3
import threading
import zipfile
from pathlib import Path

from . import core_config as cfgmod
from .core_gen import lemma_forms

STATE = {"ready": False, "building": False, "error": ""}
_LOCK = threading.Lock()


def _db_path() -> Path:
    return cfgmod.app_dir() / "ecdict.db"


def _asset_path() -> Path:
    import sys

    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    roots.append(Path(__file__).resolve().parent)
    roots.append(Path(__file__).resolve().parent.parent)
    for r in roots:
        p = r / "assets" / "ecdict_compact.tsv.zip"
        if p.exists():
            return p
    raise FileNotFoundError("ecdict_compact.tsv.zip 缺失")


def ensure_db(background: bool = True, on_done=None):
    """确保词典 SQLite 就绪；首次构建约 20~40 秒（后台线程）。"""
    if STATE["ready"] or STATE["building"]:
        return
    if _db_path().exists():
        STATE["ready"] = True
        return
    if background:
        threading.Thread(target=_build_blocking, args=(on_done,), daemon=True).start()
    else:
        _build_blocking(on_done)


def _build_blocking(on_done=None):
    with _LOCK:
        if STATE["ready"] or STATE["building"]:
            return
        STATE["building"] = True
        try:
            db = _db_path()
            tmp = db.with_suffix(".tmp")
            if tmp.exists():
                tmp.unlink()
            con = sqlite3.connect(str(tmp))
            con.execute("CREATE TABLE entries(word TEXT PRIMARY KEY, pos TEXT, phonetic TEXT, translation TEXT, exchange TEXT)")
            with zipfile.ZipFile(_asset_path()) as z:
                with z.open("ecdict_compact.tsv") as f:
                    import io

                    reader = io.TextIOWrapper(f, encoding="utf-8")
                    batch = []
                    for line in reader:
                        parts = line.rstrip("\n").split("\t")
                        if len(parts) != 5:
                            continue
                        batch.append(tuple(parts))
                        if len(batch) >= 20000:
                            con.executemany("INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?)", batch)
                            batch = []
                    if batch:
                        con.executemany("INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?)", batch)
            con.commit()
            con.close()
            tmp.rename(db)
            STATE["ready"] = True
            STATE["error"] = ""
            if on_done:
                on_done(True, "")
        except Exception as e:
            STATE["error"] = str(e)
            if on_done:
                on_done(False, str(e))
        finally:
            STATE["building"] = False


def _query_one(con, word: str):
    row = con.execute(
        "SELECT word, pos, phonetic, translation, exchange FROM entries WHERE word = ?", (word,)
    ).fetchone()
    if not row:
        return None
    return {"word": row[0], "pos": row[1], "phonetic": row[2], "translation": row[3], "exchange": row[4]}


def lookup(word: str):
    """查词：原形优先，其次常见变形。未就绪返回 None；未收录返回 {'word':…, 'miss': True}。"""
    w = (word or "").strip().lower()
    if not w:
        return None
    if not STATE["ready"]:
        return None
    con = sqlite3.connect(str(_db_path()))
    try:
        tried = []
        for form in [w] + [f for f in lemma_forms(w) if f != w]:
            if form in tried:
                continue
            tried.append(form)
            hit = _query_one(con, form)
            if hit:
                hit["lookedUp"] = word
                return hit
        return {"word": word, "miss": True}
    finally:
        con.close()


def prewarm():
    ensure_db(background=True)
