"""按 PDF 内部文章目录切分正文，不把页数误当作文章边界。"""
import re
from pathlib import Path

import pymupdf

PARSER_VERSION = 2
_NAV = re.compile(r"^\|?\s*(?:(?:Next|Previous|Section menu|Main menu)\s*\|\s*)+(?:Next|Previous|Section menu|Main menu)?\s*\|?$", re.I)
_WATERMARK = re.compile(r"zlibrary|this (?:article|file) was downloaded by|中商海外高资", re.I)


def _text(value):
    return " ".join(value.replace("\u00ad", "").split())


def _outline_starts(doc):
    toc = doc.get_toc(simple=False)
    starts = []
    for i, item in enumerate(toc):
        level, title, page = item[:3]
        # 刊名和栏目是父节点，只有叶节点代表文章。
        if i + 1 < len(toc) and toc[i + 1][0] > level:
            continue
        dest = item[3] if len(item) > 3 else {}
        if not title.strip() or not 1 <= page <= len(doc) or dest.get("kind") != pymupdf.LINK_GOTO:
            continue
        point = dest.get("to")
        y = max(0, min(float(point.y), doc[page - 1].rect.height)) if point is not None else 0
        starts.append({"title": _text(title), "page": page - 1, "y": y})
    return _unique_starts(starts)


def _unique_starts(starts):
    # 同一锚点的目录别名不重复创建文章；同页不同位置仍是独立文章。
    unique = {}
    for start in starts:
        unique.setdefault((start["page"], round(start["y"], 1)), start)
    return sorted(unique.values(), key=lambda s: (s["page"], s["y"]))


def _linked_starts(doc):
    """无书签时，尝试封面后的可点击目录；忽略逐页的前后翻页导航。"""
    starts = []
    for page in list(doc.pages(0, min(12, len(doc)))):
        blocks = page.get_text("blocks")
        links = [link for link in page.get_links() if link.get("kind") == pymupdf.LINK_GOTO
                 and 0 <= link.get("page", -1) < len(doc) and link["page"] > page.number]
        candidates = []
        for link in links:
            rect = link["from"]
            label = _text(page.get_textbox(rect))
            if len(label) < 8 or not re.search(r"[A-Za-z]{3}", label) or _NAV.fullmatch(label):
                continue
            # 目录中的换行标题可能覆盖多个链接矩形，取所在文本块。
            block = next((b for b in blocks if b[6] == 0 and pymupdf.Rect(b[:4]).contains(rect)), None)
            if block and len(_text(block[4])) <= 250:
                label = _text(block[4])
            point = link.get("to")
            candidates.append({"title": label, "page": link["page"], "y": max(0, point.y) if point else 0})
        # 真实目录应同时指向几篇不同文章，文章中的推荐链接不作为目录。
        if len({c["page"] for c in candidates}) >= 3 and re.search(
                r"\b(?:contents|articles|in this issue)\b", page.get_text(), re.I):
            starts.extend(candidates)
    return _unique_starts(starts)


def parse(file_path):
    with pymupdf.open(file_path) as doc:
        if not len(doc):
            raise ValueError("PDF 没有页面")
        starts = _outline_starts(doc)
        method = "outline"
        if not starts:
            starts = _linked_starts(doc)
            method = "linked_contents"
        if not starts:
            starts = [{"title": Path(file_path).stem + "（完整 PDF · 未识别文章目录）", "page": 0, "y": 0}]
            method = "document"

        articles = []
        page_blocks = {}
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else {"page": len(doc), "y": 0}
            paragraphs, paragraph_pages = [], []
            for page_no in range(start["page"], min(end["page"] + 1, len(doc))):
                lower = start["y"] if page_no == start["page"] else 0
                upper = end["y"] if page_no == end["page"] else float("inf")
                if upper <= lower:
                    continue
                if page_no not in page_blocks:
                    page_blocks[page_no] = doc[page_no].get_text("blocks")
                for block in page_blocks[page_no]:
                    if block[6] != 0:
                        continue
                    # 按行判断同页边界，跨越目录锚点的文本块也不会串篇。
                    if block[3] <= lower or block[1] >= upper:
                        continue
                    if block[1] < lower - 2 or block[3] > upper + 2:
                        text = doc[page_no].get_textbox(pymupdf.Rect(block[0], max(block[1], lower),
                                                                   block[2], min(block[3], upper)))
                    else:
                        text = block[4]
                    text = _text(text)
                    if not text or _NAV.fullmatch(text) or _WATERMARK.search(text):
                        continue
                    if text.casefold() == start["title"].casefold():
                        continue
                    paragraphs.append(text)
                    paragraph_pages.append(page_no + 1)
            if not paragraphs:
                continue
            articles.append({"index": len(articles), "title": start["title"], "paragraphs": paragraphs,
                             "pageStart": start["page"] + 1, "pageEnd": paragraph_pages[-1],
                             "paragraphPages": paragraph_pages, "pdfSplitMethod": method})
        if not articles:
            raise ValueError("PDF 未提取到可阅读文字，可能是扫描件，需要先进行 OCR 文字识别。")
        return articles


def relocate_legacy_record(file_path, old_article, record, articles):
    """旧版一页一段的位置迁到对应文章；不把整本阅读状态套到第一篇。"""
    old_paragraphs = old_article.get("paragraphs", [])
    old_index = record["paragraphIndex"]
    if old_index >= len(old_paragraphs):
        return None
    old_text = old_paragraphs[old_index]
    page_no = None
    with pymupdf.open(file_path) as doc:
        index = 0
        for page in doc:
            text = _text(page.get_text())
            if text and not _WATERMARK.search(text):
                if index == old_index and text == _text(old_text):
                    page_no = page.number + 1
                    break
                index += 1
    if page_no is None:
        return None
    choices = []
    for article in articles:
        for i, (text, source_page) in enumerate(zip(article["paragraphs"], article["paragraphPages"])):
            if source_page != page_no:
                continue
            offset = old_text.find(text)
            if offset >= 0:
                # 优先取包含旧字符位置的段落，其次取最近的段落。
                distance = max(offset - record["paragraphOffset"], record["paragraphOffset"] - offset - len(text), 0)
                choices.append((distance, article["index"], i, offset))
    if not choices:
        return None
    _, index, paragraph, old_offset = min(choices)
    article = next(a for a in articles if a["index"] == index)
    offset = max(0, min(record["paragraphOffset"] - old_offset, len(article["paragraphs"][paragraph])))
    consumed = sum(len(p) + 1 for p in article["paragraphs"][:paragraph]) + offset
    total = sum(len(p) + 1 for p in article["paragraphs"])
    return index, dict(record, title=article["title"], titleZh="", paragraphIndex=paragraph,
                       paragraphOffset=offset, paragraphStart=article["paragraphs"][paragraph][:160],
                       paragraphCount=len(article["paragraphs"]),
                       wordCount=len(re.findall(r"[A-Za-z]+(?:[’'-][A-Za-z]+)*", " ".join(article["paragraphs"]))),
                       progress=consumed / max(total, 1), atStart=consumed == 0, atEnd=False)
