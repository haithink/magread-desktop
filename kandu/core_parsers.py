"""EPUB / PDF 解析 + 刊物库扫描与缓存（对齐 Android 版 BookStore 目录结构）。"""
import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup

from . import core_config as cfgmod

WATERMARK = re.compile(r"(?i)zlibrary|this (article|file) was downloaded by|中商海外高资")
SKIP_NAME = ["cover", "toc", "ad_page", "masthead", "titlepage", "title_page", "colophon", "nav"]
BAD_ID = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_id(name: str) -> str:
    return BAD_ID.sub("_", name) or "book"


def scan(library_dir: str):
    """扫描目录下的 epub/pdf，返回 BookMeta 字典列表。"""
    if not library_dir:
        return []
    root = Path(library_dir)
    if not root.is_dir():
        return []
    metas = []
    for p in sorted(root.iterdir(), key=lambda x: x.name, reverse=True):
        if not p.is_file():
            continue
        lower = p.name.lower()
        if lower.endswith(".epub"):
            ext = "epub"
        elif lower.endswith(".pdf"):
            ext = "pdf"
        else:
            continue
        metas.append(
            {
                "id": sanitize_id(p.name[: -(len(ext) + 1)]),
                "fileName": p.name,
                "ext": ext,
                "size": p.stat().st_size,
                "lastModified": int(p.stat().st_mtime * 1000),
                "path": str(p),
            }
        )
    return metas


# ---------------- EPUB ----------------

_NS = "{http://www.idpf.org/2007/opf}"
_DC = "{http://purl.org/dc/elements/1.1/}"


def _read_entry(zf: zipfile.ZipFile, name: str):
    try:
        return zf.read(name)
    except KeyError:
        return None


def _parse_container(data: bytes):
    try:
        root = ET.fromstring(data)
        for rf in root.iter():
            if rf.tag.endswith("rootfile"):
                full = rf.get("full-path")
                if full:
                    return full
    except Exception:
        pass
    return None


def _normalize_path(dir_part: str, href: str) -> str:
    from urllib.parse import unquote

    raw = unquote(href)
    combined = f"{dir_part}/{raw}" if dir_part else raw
    stack = []
    for seg in combined.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if stack:
                stack.pop()
        else:
            stack.append(seg)
    return "/".join(stack)


def _parse_opf(data: bytes):
    """返回 (cover_href, spine_hrefs)。"""
    manifest = {}
    spine = []
    cover_id = None
    try:
        root = ET.fromstring(data)
    except Exception:
        return None, []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "item":
            iid = el.get("id")
            if iid:
                manifest[iid] = {
                    "href": el.get("href") or "",
                    "mediaType": el.get("media-type") or "",
                    "properties": el.get("properties") or "",
                }
        elif tag == "meta" and el.get("name") == "cover":
            cover_id = el.get("content")
        elif tag == "itemref" and el.get("idref"):
            spine.append(el.get("idref"))

    cover_href = None
    if cover_id and cover_id in manifest:
        cover_href = manifest[cover_id]["href"] or None
    if not cover_href:
        for it in manifest.values():
            if "cover-image" in it["properties"].split():
                cover_href = it["href"]
                break
    if not cover_href:
        for it in manifest.values():
            if it["mediaType"].startswith("image/") and "cover" in it["href"].lower():
                cover_href = it["href"]
                break
    hrefs = [manifest[i]["href"] for i in spine if i in manifest and manifest[i]["href"]]
    if not cover_href:
        for h in hrefs:
            if "cover" in h.lower():
                cover_href = h
                break
    return cover_href, hrefs


def _is_skippable(href: str) -> bool:
    name = href.split("/")[-1].lower()
    return any(s in name for s in SKIP_NAME)


def _parse_article_html(index: int, data: bytes):
    html = data.decode("utf-8", "ignore")
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    for sel in ("h1", "h2", "h3"):
        el = soup.find(sel)
        if el and el.get_text(strip=True):
            title = el.get_text(" ", strip=True).strip()
            break
    paragraphs = []
    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True).replace(" ", " ").strip()
        if not text:
            continue
        if WATERMARK.search(text):
            continue
        if len(text) == 1 and not text[0].isalpha():
            continue
        paragraphs.append(text)
    total = sum(len(t) for t in paragraphs)
    if not paragraphs or total < 30:
        return None
    if not title and len(paragraphs) < 2:
        return None
    return {"index": index, "title": title or paragraphs[0][:60], "paragraphs": paragraphs}


def epub_parse(file_path: str):
    """返回 (cover_bytes, articles)。文章按 spine 顺序编号。"""
    cover_bytes = None
    articles = []
    with zipfile.ZipFile(file_path) as zf:
        names = set(zf.namelist())
        opf_path = None
        if "META-INF/container.xml" in names:
            opf_path = _parse_container(zf.read("META-INF/container.xml"))
        if not opf_path:
            for n in zf.namelist():
                if n.lower().endswith(".opf"):
                    opf_path = n
                    break
        opf_path = opf_path or "EPUB/content.opf"
        opf = _read_entry(zf, opf_path)
        if opf is None:
            raise ValueError(f"EPUB 中找不到 OPF：{opf_path}")
        info = _parse_opf(opf)
        cover_href, spine_hrefs = info
        opf_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
        if cover_href:
            cover_bytes = _read_entry(zf, _normalize_path(opf_dir, cover_href))
        for href in spine_hrefs:
            if _is_skippable(href):
                continue
            data = _read_entry(zf, _normalize_path(opf_dir, href))
            if data is None:
                continue
            art = _parse_article_html(len(articles), data)
            if art:
                articles.append(art)
    return cover_bytes, articles


def epub_cover_stream(file_path: str):
    """轻量封面提取（一次流式扫描）。"""
    container = None
    opf_candidates = {}
    image_candidates = {}
    with zipfile.ZipFile(file_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            lower = name.lower()
            try:
                if lower == "meta-inf/container.xml":
                    container = zf.read(name)
                elif lower.endswith(".opf") and info.file_size < 2_000_000:
                    opf_candidates[name] = zf.read(name)
                elif (
                    lower.endswith((".jpg", ".jpeg", ".png", ".webp"))
                    and info.file_size < 1_200_000
                    and "cover" in lower
                ):
                    image_candidates[name] = zf.read(name)
            except Exception:
                continue
    opf_path = _parse_container(container) if container else None
    opf_bytes = None
    if opf_path:
        opf_bytes = opf_candidates.get(opf_path)
    if opf_bytes is None and opf_candidates:
        opf_bytes = next(iter(opf_candidates.values()))
    cover_href = _parse_opf(opf_bytes)[0] if opf_bytes else None
    from urllib.parse import unquote

    if cover_href:
        for cand in (cover_href, unquote(cover_href)):
            if cand in image_candidates:
                return image_candidates[cand]
        tail = cover_href.split("/")[-1].lower()
        for name, data in image_candidates.items():
            if name.lower().endswith(tail):
                return data
    return max(image_candidates.values(), key=len) if image_candidates else None


# ---------------- PDF ----------------

def pdf_parse(file_path: str):
    """PDF：单篇文章，每页一个粗略段落。"""
    doc = pymupdf.open(file_path)
    try:
        paragraphs = []
        for page in doc:
            text = " ".join(
                ln.strip() for ln in page.get_text("text").splitlines() if ln.strip()
            )
            if text and not WATERMARK.search(text):
                paragraphs.append(text)
        title = Path(file_path).stem
        if not paragraphs:
            paragraphs = ["(PDF 文本提取失败)"]
        return [{"index": 0, "title": title, "paragraphs": paragraphs}]
    finally:
        doc.close()


def pdf_cover(file_path: str, target_width: int = 480):
    """渲染首页 PNG 字节，失败返回 None。"""
    try:
        doc = pymupdf.open(file_path)
        try:
            page = doc[0]
            zoom = target_width / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            return pix.tobytes("png")
        finally:
            doc.close()
    except Exception:
        return None


# ---------------- 缓存与库存 ----------------

def _cache_dir(book_id: str) -> Path:
    d = cfgmod.app_dir() / "cache" / book_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def covers_dir() -> Path:
    return cfgmod._sub("covers")


def cover_file(book_id: str) -> Path:
    return covers_dir() / f"{book_id}.jpg"


def has_cover(book_id: str) -> bool:
    return cover_file(book_id).exists()


def ensure_cover(meta: dict) -> str | None:
    out = cover_file(meta["id"])
    if out.exists():
        return str(out)
    data = None
    if meta["ext"] == "epub":
        try:
            data = epub_cover_stream(meta["path"])
        except Exception:
            data = None
        if data is None:
            try:
                data = epub_parse(meta["path"])[0]
            except Exception:
                data = None
    else:
        data = pdf_cover(meta["path"])
    if data:
        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.save(out, "JPEG", quality=85)
            return str(out)
        except Exception:
            return None
    return None


def _json_read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _json_write(p: Path, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def ensure_content(meta: dict) -> dict:
    """解析正文（content.json 缓存，源文件大小变化时失效）。"""
    cf = _cache_dir(meta["id"]) / "content.json"
    cache = _json_read(cf)
    if cache and cache.get("bookSize") == meta["size"]:
        return {
            "bookId": meta["id"],
            "format": cache["format"],
            "articles": cache["articles"],
        }
    if meta["ext"] == "epub":
        _cover, articles = epub_parse(meta["path"])
        fmt = "epub"
    else:
        articles = pdf_parse(meta["path"])
        fmt = "pdf"
    payload = {"bookSize": meta["size"], "format": fmt, "articles": articles}
    _json_write(cf, payload)
    return {"bookId": meta["id"], "format": fmt, "articles": articles}


def load_digest(book_id: str):
    return _json_read(_cache_dir(book_id) / "digest.json")


def save_digest(digest: dict):
    _json_write(_cache_dir(digest["bookId"]) / "digest.json", digest)


def has_digest(book_id: str) -> bool:
    return (_cache_dir(book_id) / "digest.json").exists()


def load_vocab(book_id: str, article_index: int, level: str):
    return _json_read(_cache_dir(book_id) / f"vocab_{article_index}_{level}.json")


def save_vocab(book_id: str, vocab: dict):
    _json_write(
        _cache_dir(book_id) / f"vocab_{vocab['articleIndex']}_{vocab['level']}.json",
        vocab,
    )


def analysis_key(book_id: str, article_index: int, text: str) -> str:
    return hashlib.sha1(f"{book_id}|{article_index}|{text}".encode("utf-8")).hexdigest()


def load_analysis(book_id: str, text: str, article_index: int):
    return _json_read(_cache_dir(book_id) / f"analysis_{analysis_key(book_id, article_index, text)}.json")


def save_analysis(book_id: str, text: str, article_index: int, analysis: dict):
    _json_write(
        _cache_dir(book_id) / f"analysis_{analysis_key(book_id, article_index, text)}.json",
        analysis,
    )


def purge_all():
    import shutil

    for name in ("cache", "books", "covers"):
        p = cfgmod.app_dir() / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


def purge_digests():
    """清空所有书目的摘要缓存（保留正文/生词/句解）。"""
    cache_root = cfgmod.app_dir() / "cache"
    if not cache_root.exists():
        return 0
    n = 0
    for f in cache_root.glob("*/digest.json"):
        f.unlink(missing_ok=True)
        n += 1
    return n


def purge_book_cache(book_id: str, digest_only: bool = False):
    """清除单本书缓存；digest_only=True 仅删摘要，否则整本缓存+封面。"""
    d = cfgmod.app_dir() / "cache" / book_id
    if digest_only:
        (d / "digest.json").unlink(missing_ok=True)
        return
    import shutil

    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    cover_file(book_id).unlink(missing_ok=True)
