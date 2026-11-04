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
    hit = lookup_many([word]).get((word or "").strip().lower())
    if hit and not hit.get("miss"):
        hit["lookedUp"] = word
    return hit


def lookup_many(words):
    """一次连接批量查词，附带 ECDICT 的原形（exchange 中的 0 字段）。"""
    if not STATE["ready"]:
        return {}
    words = list(dict.fromkeys(w.strip().lower() for w in words if w and w.strip()))
    if not words:
        return {}
    con = sqlite3.connect(str(_db_path()))
    try:
        queried = {}
        out = {}
        for word in words:
            for form in lemma_forms(word):
                if form not in queried:
                    queried[form] = _query_one(con, form)
                hit = queried[form]
                if hit:
                    hit = dict(hit, lookedUp=word)
                    lemma = next((v[2:] for v in (hit.get("exchange") or "").split("/")
                                  if v.startswith("0:")), hit["word"])
                    hit["lemma"] = lemma
                    out[word] = hit
                    break
            else:
                out[word] = {"word": word, "miss": True}
        return out
    finally:
        con.close()


def prewarm():
    ensure_db(background=True)
