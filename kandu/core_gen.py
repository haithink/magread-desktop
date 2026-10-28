"""生成编排：摘要 / 逐段生词表 / 句解（移植 GenService + VocabFilter + VocabNormalizer + Sentences）。"""
import re
import time

from . import core_config as cfgmod
from . import core_llm as llm
from . import core_prompts as prompts
from . import core_parsers as store

LEVEL_LABEL = {"cet4": "大学英语四级 CET-4", "cet6": "大学英语六级 CET-6", "toefl": "托福词汇 TOEFL", "gre": " GRE 词汇"}


def level_label(level: str) -> str:
    return LEVEL_LABEL.get(level, level)


# ---------------- 句子切分 ----------------

_ENDERS = set(".!?")
_CLOSERS = set("\"')]”’")
_ABBREV = {"mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "etc", "inc", "ltd", "no", "fig", "u.s", "u.k", "d.c", "al"}
_ABBREV_RE = re.compile(r"^[a-z]([.-][a-z])[.]?$", re.IGNORECASE)


def split_sentences(text: str):
    """[(start, end, text)]，对 U.S./Mr. 等缩写做了简单豁免。"""
    out = []
    start = 0
    i = 0
    while i < len(text):
        c = text[i]
        if c in _ENDERS:
            j = i + 1
            while j < len(text) and (text[j] in _ENDERS or text[j] in _CLOSERS):
                j += 1
            is_end = True
            if j < len(text) and text[j] == " " and j + 1 < len(text) and text[j + 1].islower():
                ts = max(text.rfind(" ", 0, i), -1) + 1
                token = text[ts:j].strip()
                stripped = token.rstrip(".!?").lower()
                if stripped in _ABBREV or _ABBREV_RE.match(stripped):
                    is_end = False
            if is_end and j > start:
                piece = text[start:j].strip()
                if piece:
                    out.append((start, j, piece))
                start = j
            i = j
        else:
            i += 1
    if start < len(text):
        piece = text[start:].strip()
        if piece:
            out.append((start, len(text), piece))
    return out


def sentence_at(sents, offset: int):
    if not sents:
        return None
    for s in sents:
        if s[0] <= offset < s[1]:
            return s
    return sents[0] if offset <= sents[0][0] else sents[-1]


def neighbors(sents, sent):
    idx = next((k for k, s in enumerate(sents) if s[0] == sent[0] and s[1] == sent[1]), -1)
    before = sents[idx - 1][2] if idx > 0 else ""
    after = sents[idx + 1][2] if 0 <= idx < len(sents) - 1 else ""
    return before, after


# ---------------- 本地候选词粗筛 ----------------

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'-]*")

ALWAYS_KNOWN = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "ok", "okay", "tv", "ai",
    "don't", "doesn't", "didn't", "won't", "wouldn't", "couldn't", "shouldn't",
    "isn't", "aren't", "wasn't", "weren't", "hasn't", "haven't", "hadn't",
    "can't", "cannot", "mustn't", "needn't", "ain't", "let's", "that's",
    "there's", "it's", "he's", "she's", "i'm", "i've", "i'll", "i'd",
    "you're", "you've", "you'll", "we're", "we've", "we'll", "they're",
    "they've", "they'll", "gonna", "wanna", "www", "http", "https", "com",
    "org", "pdf", "epub", "app", "vs",
}


def lemma_forms(word: str):
    w = word.lower()
    forms = [w]
    if w.endswith("'s"):
        forms.append(w[:-2])
    if w.endswith("s") and not w.endswith("ss"):
        forms.append(w[:-1])
    if w.endswith("es"):
        forms.append(w[:-2])
    if w.endswith("ies"):
        forms.append(w[:-3] + "y")
    if w.endswith("ed"):
        forms += [w[:-1], w[:-2]]
        if w.endswith("ied"):
            forms.append(w[:-3] + "y")
    if w.endswith("ing"):
        forms += [w[:-3], w[:-3] + "e"]
        if w.endswith("ying"):
            forms.append(w[:-4] + "ie")
    return list(dict.fromkeys(forms))


def candidates(paragraph_texts, dict_set):
    """疑似超纲候选词（原形小写，保持出现顺序，上限 50）。"""
    out = []
    seen = set()
    for text in paragraph_texts:
        for m in _TOKEN.finditer(text):
            raw = m.value.strip("'-")
            if len(raw) < 3 or raw.lower() in ALWAYS_KNOWN:
                continue
            forms = lemma_forms(raw)
            if any(f in dict_set for f in forms):
                continue
            base = min(forms, key=len)
            if base not in seen:
                seen.add(base)
                out.append(base)
                if len(out) >= 50:
                    return out
    return out


# ---------------- 生成任务 ----------------

class GenError(Exception):
    pass


def _chat_retry(cfg, system, user, max_tokens, on_chars=None, attempts=2):
    last = None
    for i in range(attempts):
        try:
            return llm.chat(cfg, system, user, max_tokens=max_tokens, on_delta=on_chars)
        except Exception as e:
            last = e
            if i == 0:
                time.sleep(1.5)
    raise last if last else GenError("调用失败")


def _extract_json(text: str):
    return prompts.extract_json_block(text)


def _parse_json(raw: str, want_array: bool):
    t = _extract_json(raw)
    import json

    def load(s):
        obj = json.loads(s)
        if want_array and not isinstance(obj, list):
            raise ValueError("not array")
        if not want_array and not isinstance(obj, dict):
            raise ValueError("not object")
        return obj

    try:
        return load(t)
    except Exception:
        repaired = prompts.repair_truncated_json(t)
        if repaired is not None:
            try:
                return load(repaired)
            except Exception:
                pass
    raise GenError(f"模型输出不是有效 JSON（可能被长度截断）：{raw[:200]}")


def generate_digest(meta, cfg, on_progress=None, on_chars=None):
    """生成整本摘要，分批（每批 6 篇）请求，逐批落盘。返回 BookDigest dict。"""
    content = store.ensure_content(meta)
    articles = content["articles"]
    if not articles:
        raise GenError("该文件没有解析出文章")

    batches = [articles[i : i + 6] for i in range(0, len(articles), 6)]
    collected = {}
    existing = {
        a["index"]: a for a in (store.load_digest(meta["id"]) or {}).get("articles", [])
    }

    total = len(batches)
    for bi, batch in enumerate(batches):
        if on_progress:
            on_progress(bi, total)
        items = [(a["index"], a["title"], "\n\n".join(a["paragraphs"])[:1600]) for a in batch]
        raw = _chat_retry(cfg, prompts.digest_system(), prompts.digest_user(meta["fileName"], items), 8000, on_chars)
        arr = _parse_json(raw, want_array=True)
        for el in arr:
            if not isinstance(el, dict):
                continue
            index = el.get("index")
            try:
                index = int(index)
            except Exception:
                continue
            art = next((a for a in articles if a["index"] == index), None)
            if art is None:
                continue
            collected[index] = {
                "index": index,
                "titleEn": (el.get("titleEn") or "").strip() or art["title"],
                "titleZh": (el.get("titleZh") or "").strip(),
                "summaryZh": (el.get("summaryZh") or "").strip(),
            }
        merged = dict(existing)
        merged.update(collected)
        # 批内缺漏占位
        for a in batch:
            if a["index"] not in merged:
                merged[a["index"]] = {
                    "index": a["index"],
                    "titleEn": a["title"],
                    "titleZh": "",
                    "summaryZh": "（本篇摘要生成失败，可重试）",
                }
        digest = {
            "bookId": meta["id"],
            "articles": [merged[k] for k in sorted(merged)],
            "generatedAt": int(time.time() * 1000),
        }
        store.save_digest(digest)
        if on_progress:
            on_progress(bi + 1, total)
    return store.load_digest(meta["id"]) or {"bookId": meta["id"], "articles": [], "generatedAt": 0}


def generate_vocab(meta, article, level, cfg, on_progress=None, on_chars=None):
    """生成一篇文章的生词表：本地粗筛候选 → 分块 LLM 确认 → 规范化落盘。"""
    dict_set = cfgmod.wordset(level)
    label = level_label(level)

    units = [(-1, article["title"])]
    for i, p in enumerate(article["paragraphs"]):
        units.append((i, p))

    cand = {}
    if dict_set:
        for idx, text in units:
            cand[idx] = [] if idx == -1 else candidates([text], dict_set)

    chunks = []
    current = []
    chars = 0
    for idx, text in units:
        if idx == -1:
            chunks.append([(-1, text)])
            continue
        if chars + len(text) > 4500 and current:
            chunks.append(current)
            current = []
            chars = 0
        current.append((idx, text))
        chars += len(text)
    if current:
        chunks.append(current)

    paragraphs_out = []
    title_entries = []
    idioms_out = []
    seen = set()

    total = len(chunks)
    for ci, chunk in enumerate(chunks):
        if on_progress:
            on_progress(ci, total)
        if on_chars:
            on_chars(0)
        user = prompts.vocab_user(article["title"], -1, chunk, cand)
        raw = _chat_retry(cfg, prompts.vocab_system(label), user, 16000, on_chars)
        obj = _parse_json(raw, want_array=False)

        for p in obj.get("paragraphs", []):
            if not isinstance(p, dict):
                continue
            try:
                idx = int(p.get("index"))
            except Exception:
                continue
            entries = [_parse_entry(e, seen) for e in p.get("entries", [])]
            entries = [e for e in entries if e]
            if idx < 0:
                title_entries.extend(entries)
                continue
            start_words = (p.get("startWords") or "").strip() or (
                article["paragraphs"][idx][:30] if 0 <= idx < len(article["paragraphs"]) else ""
            )
            paragraphs_out.append({"index": idx, "startWords": start_words, "entries": entries})
        for e in obj.get("titleEntries", []):
            entry = _parse_entry(e, seen)
            if entry:
                title_entries.append(entry)
        for io in obj.get("idioms", []):
            if isinstance(io, dict) and (io.get("word") or "").strip():
                idioms_out.append(
                    {"word": io["word"], "common": io.get("common", ""), "inText": io.get("inText", "")}
                )
        if on_progress:
            on_progress(ci + 1, total)

    draft = {
        "level": level,
        "articleIndex": article["index"],
        "titleEntries": title_entries,
        "paragraphs": sorted(paragraphs_out, key=lambda x: x["index"]),
        "idioms": list({i["word"].lower(): i for i in idioms_out}.values()),
    }
    result = vocab_normalize(draft, article)
    store.save_vocab(meta["id"], result)
    return result


def _parse_entry(el, seen):
    if not isinstance(el, dict):
        return None
    word = (el.get("word") or "").strip().lower()
    if not word:
        return None
    dedup = f"{word}|{(el.get('sense') or '')[:20]}"
    if dedup in seen:
        return None
    seen.add(dedup)
    return {
        "word": word,
        "phonetic": (el.get("phonetic") or "").strip(),
        "pos": (el.get("pos") or "").strip(),
        "sense": (el.get("sense") or "").strip() or "（模型未给释义）",
        "inText": (el.get("inText") or "").strip(),
        "root": (el.get("root") or "").strip(),
        "term": el.get("term") in (True, "true", "True"),
    }


def analyze(meta, article, sentence, before, after, cfg):
    cached = store.load_analysis(meta["id"], sentence, article["index"])
    if cached:
        return cached
    user = prompts.analysis_user(sentence, before, after, article["title"])
    raw = _chat_retry(cfg, prompts.analysis_system(), user, 8000)
    obj = _parse_json(raw, want_array=False)

    def _s(o, k, default=""):
        v = o.get(k)
        return v if isinstance(v, str) else default

    analysis = {
        "vocab": [],
        "grammarDiagram": _s(obj, "grammarDiagram").replace("\\n", "\n"),
        "grammarPoints": [
            {"title": gp.get("title", ""), "text": gp.get("text", "")}
            for gp in obj.get("grammarPoints", [])
            if isinstance(gp, dict)
        ],
        "context": _s(obj, "context"),
        "translation": _s(obj, "translation"),
    }
    for v in obj.get("vocab", []):
        if isinstance(v, dict) and (v.get("word") or "").strip():
            analysis["vocab"].append(
                {
                    "word": v["word"].strip(),
                    "phonetic": (v.get("phonetic") or "").strip(),
                    "sense": _s(v, "sense"),
                    "note": _s(v, "note"),
                }
            )
    store.save_analysis(meta["id"], sentence, article["index"], analysis)
    return analysis


# ---------------- 生词表规范化（锚点定位自愈） ----------------

def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower().strip()


def vocab_normalize(vocab: dict, article: dict) -> dict:
    """词条级 inText 反查段落 → 小节级 startWords → 原编号兜底；
    小节头开头词一律取正文真实开头。"""
    paragraphs = article["paragraphs"]
    if not vocab.get("paragraphs") or not paragraphs:
        return vocab
    n = len(paragraphs)
    para_norm = [_norm_text(p) for p in paragraphs]

    def section_anchor(pv):
        sw = _norm_text(pv.get("startWords") or "").rstrip(",:.!？…;")
        if len(sw) < 6:
            return min(max(pv.get("index", 0), 0), n - 1)
        best, best_score = -1, 0
        for i, p in enumerate(para_norm):
            if p.startswith(sw):
                score = len(sw) + 1000
            elif sw in p[:150]:
                score = len(sw)
            else:
                score = 0
            if score > best_score:
                best_score, best = score, i
        return best if best >= 0 else min(max(pv.get("index", 0), 0), n - 1)

    def entry_anchor(in_text, claimed, anchor):
        t = _norm_text(in_text)
        if len(t) < 10:
            return None
        hits = [i for i, p in enumerate(para_norm) if t in p]
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]
        if claimed in hits:
            return claimed
        if anchor in hits:
            return anchor
        return hits[0]

    grouped = {}
    for pv in vocab["paragraphs"]:
        anchor = section_anchor(pv)
        claimed = min(max(pv.get("index", 0), 0), n - 1)
        for e in pv.get("entries", []):
            target = entry_anchor(e.get("inText", ""), claimed, anchor)
            target = anchor if target is None else target
            bucket = grouped.setdefault(target, [])
            if all(x["word"] != e["word"] for x in bucket):
                bucket.append(e)

    merged = []
    for idx in sorted(grouped):
        raw = paragraphs[idx]
        head = raw if len(raw) <= 30 else raw[:30].rsplit(" ", 1)[0] or raw[:30]
        merged.append({"index": idx, "startWords": head.strip(), "entries": grouped[idx]})

    out = dict(vocab)
    out["paragraphs"] = merged
    return out
