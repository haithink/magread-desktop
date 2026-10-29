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
            raw = m.group(0).strip("'-")
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
        if want_array and isinstance(obj, dict):
            # 模型偶发返回单个对象而非数组：尽量收敛
            if "index" in obj:
                return [obj]
            lists = [v for v in obj.values() if isinstance(v, list)]
            if len(lists) == 1:
                return lists[0]
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


# cp1252 特有字符（€‚ƒ„…†‡ˆ‰Š‹ŒŽ''""•–—˜™š›œžŸ）+ Latin-1 补充区
_MOJIBAKE_CHARS = re.compile(r"[\xa1-\xff€‚ƒ„…†‡ˆ‰Š‹ŒŽ''""•–—˜™š›œžŸ†]")


def looks_mojibake(s: str) -> bool:
    """UTF-8 被 latin-1/cp1252 误解码的特征：出现成片西文扩展区字符。
    宽松判定即可——修复动作自带可逆性校验，误判只是原样返回，零成本。"""
    if not s:
        return False
    hits = len(_MOJIBAKE_CHARS.findall(s))
    has_cjk = any("一" <= c <= "鿿" for c in s)
    return hits >= 2 and not has_cjk


def fix_mojibake(s: str) -> str:
    """被 latin-1/cp1252 误解码的 UTF-8 可确定性还原；不可还原则原样返回。"""
    if not isinstance(s, str) or not looks_mojibake(s):
        return s
    for enc in ("cp1252", "latin-1"):
        try:
            return s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    # cp1252 的 5 个未定义字节解码成 C1 控制符，编码器不接受：手工反向映射
    out = bytearray()
    for c in s:
        o = ord(c)
        try:
            out += c.encode("cp1252")
        except UnicodeEncodeError:
            if o in (0x81, 0x8D, 0x8F, 0x90, 0x9D):
                out.append(o)
            else:
                return s
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return s


PLACEHOLDER_SUMMARY = "（本篇摘要生成失败，可重试）"


def digest_incomplete(meta) -> bool:
    """digest 缺文章或含占位摘要即视为不完整。"""
    digest = store.load_digest(meta["id"])
    if not digest:
        return True
    try:
        content = store.ensure_content(meta)
    except Exception:
        return False
    have = {a.get("index"): a for a in digest.get("articles", [])}
    for a in content["articles"]:
        e = have.get(a["index"])
        if not e or not (e.get("summaryZh") or "").strip() or e.get("summaryZh") == PLACEHOLDER_SUMMARY:
            return True
    return False


def generate_digest(meta, cfg, on_progress=None, on_chars=None, resume=True):
    """生成整本摘要，分批（每批 6 篇）请求，逐批落盘。

    resume=True 时只对缺摘要/占位的文章发请求（智能续传）；
    落盘保证全量文章都在，失败批次留占位（下次续传补齐）。
    """
    content = store.ensure_content(meta)
    articles = content["articles"]
    if not articles:
        raise GenError("该文件没有解析出文章")

    existing = {
        a["index"]: a for a in (store.load_digest(meta["id"]) or {}).get("articles", [])
    }

    def is_done(a):
        e = existing.get(a["index"])
        if not e:
            return False
        s = (e.get("summaryZh") or "").strip()
        if not s or s == PLACEHOLDER_SUMMARY:
            return False
        # 乱码视为未生成（可被续传替换）
        return not looks_mojibake(s)

    todo = articles if not resume else [a for a in articles if not is_done(a)]
    batches = [todo[i : i + 6] for i in range(0, len(todo), 6)]
    total = len(batches)
    if total == 0:
        return _final_digest(meta, articles, existing, {}, on_progress)

    collected = {}
    for bi, batch in enumerate(batches):
        if on_progress:
            on_progress(bi, total)
        items = [(a["index"], a["title"], "\n\n".join(a["paragraphs"])[:1600]) for a in batch]
        arr = None
        last_err = None
        for attempt in range(2):  # 解析失败整批重试一次
            try:
                raw = _chat_retry(cfg, prompts.digest_system(), prompts.digest_user(meta["fileName"], items), 8000, on_chars)
                arr = _parse_json(raw, want_array=True)
                break
            except (GenError, llm.LlmError) as e:
                last_err = e
                time.sleep(1)
        if arr is None:
            # 该批放弃（留占位），继续后面的批次——不让一个坏批次阻塞整轮
            if on_progress:
                on_progress(bi + 1, total)
            continue
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
                "titleEn": fix_mojibake((el.get("titleEn") or "").strip()) or art["title"],
                "titleZh": fix_mojibake((el.get("titleZh") or "").strip()),
                "summaryZh": fix_mojibake((el.get("summaryZh") or "").strip()),
            }
        digest = _final_digest(meta, articles, existing, collected, on_progress, bi, total)
        store.save_digest(digest)
        for a in batch:
            if a["index"] in collected:
                existing[a["index"]] = collected[a["index"]]
        if on_progress:
            on_progress(bi + 1, total)
    return store.load_digest(meta["id"]) or {"bookId": meta["id"], "articles": [], "generatedAt": 0}


def _final_digest(meta, articles, existing, collected, on_progress=None, done=None, total=None):
    merged = {}
    for a in articles:
        d = collected.get(a["index"]) or existing.get(a["index"])
        if not d or not (d.get("summaryZh") or "").strip():
            d = {"index": a["index"], "titleEn": a["title"], "titleZh": "", "summaryZh": PLACEHOLDER_SUMMARY}
        merged[a["index"]] = d
    return {
        "bookId": meta["id"],
        "articles": [merged[k] for k in sorted(merged)],
        "generatedAt": int(time.time() * 1000),
    }


def _is_censor(text: str) -> bool:
    """模型侧内容安全过滤的拒绝（中国系上游常见）。"""
    t = (text or "").lower()
    keys = ("sensitive", "敏感", "upstream_error", "content_filter", "content filter", "risk")
    return any(k in t for k in keys)


def generate_vocab(meta, article, level, cfg, on_progress=None, on_chars=None):
    """生成一篇文章的生词表：本地粗筛候选 → 分块 LLM 确认 → 规范化落盘。

    支持续传：已生成过的段落块自动跳过；单块失败（如内容触发模型
    安全过滤）留待下次，不阻塞其余块。"""
    dict_set = cfgmod.wordset(level)
    label = level_label(level)

    existing = store.load_vocab(meta["id"], article["index"], level) or {}
    processed = set(existing.get("processedParagraphs", []))
    merged_sections = {p["index"]: p for p in existing.get("paragraphs", [])}
    merged_title = list(existing.get("titleEntries", []))
    merged_idioms = list(existing.get("idioms", []))

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

    # 续传：块内所有单元（含标题单元 -1）都已处理过则跳过
    def chunk_done(chunk):
        return all(idx in processed for idx, _ in chunk)

    pending = [c for c in chunks if not chunk_done(c)]
    failed_chunks = []
    censor_hit = False
    seen = set()

    total = len(pending)
    done_count = len(chunks) - total
    if on_progress:
        on_progress(0, total)
    for ci, chunk in enumerate(pending):
        if on_progress:
            on_progress(ci, total)
        if on_chars:
            on_chars(0)
        user = prompts.vocab_user(article["title"], -1, chunk, cand)
        obj = None
        last_err = None
        for attempt in range(2):
            try:
                raw = _chat_retry(cfg, prompts.vocab_system(label), user, 16000, on_chars)
                obj = _parse_json(raw, want_array=False)
                break
            except Exception as e:
                last_err = e
                msg = str(e)
                if _is_censor(msg) or _is_censor(getattr(getattr(e, '__cause__', None), 'message', '')):
                    censor_hit = True
                    break  # 内容过滤重试也没用
                time.sleep(1)
        if obj is None:
            failed_chunks.append({"chunk": ci, "error": str(last_err)})
            if on_progress:
                on_progress(ci + 1, total)
            continue

        new_sections = {}
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
                by_word = {e["word"]: e for e in merged_title}
                for e in entries:
                    by_word[e["word"]] = e
                merged_title = list(by_word.values())
                continue
            start_words = (p.get("startWords") or "").strip() or (
                article["paragraphs"][idx][:30] if 0 <= idx < len(article["paragraphs"]) else ""
            )
            new_sections[idx] = {"index": idx, "startWords": start_words, "entries": entries}
        merged_sections.update(new_sections)
        for e in obj.get("titleEntries", []):
            entry = _parse_entry(e, seen)
            if entry:
                by_word = {x["word"]: x for x in merged_title}
                by_word[entry["word"]] = entry
                merged_title = list(by_word.values())
        for io in obj.get("idioms", []):
            if isinstance(io, dict) and (io.get("word") or "").strip():
                merged_idioms.append(
                    {"word": io["word"], "common": io.get("common", ""), "inText": io.get("inText", "")}
                )
        processed.update(idx for idx, _ in chunk)

        draft = {
            "level": level,
            "articleIndex": article["index"],
            "titleEntries": merged_title,
            "paragraphs": sorted(merged_sections.values(), key=lambda x: x["index"]),
            "idioms": merged_idioms,
            "processedParagraphs": sorted(processed),
        }
        result = vocab_normalize(draft, article)
        if failed_chunks:
            result["failedChunks"] = len(failed_chunks)
            result["censorHint"] = censor_hit
        store.save_vocab(meta["id"], result)
        if on_progress:
            on_progress(ci + 1, total)

    draft = {
        "level": level,
        "articleIndex": article["index"],
        "titleEntries": merged_title,
        "paragraphs": sorted(merged_sections.values(), key=lambda x: x["index"]),
        "idioms": list({i["word"].lower(): i for i in merged_idioms}.values()),
        "processedParagraphs": sorted(processed),
    }
    if failed_chunks:
        draft["failedChunks"] = len(failed_chunks)
        if censor_hit:
            draft["censorHint"] = True
    result = vocab_normalize(draft, article)
    if failed_chunks:
        result["failedChunks"] = len(failed_chunks)
        result["censorHint"] = censor_hit
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


def _analysis_partial(analysis: dict) -> bool:
    """缺语境/译文/图解任一主要部分即视为不完整（多为输出被截断后修补）。"""
    if not isinstance(analysis, dict):
        return True
    return not (
        (analysis.get("context") or "").strip()
        and (analysis.get("translation") or "").strip()
        and (analysis.get("grammarDiagram") or "").strip()
    )


def analyze(meta, article, sentence, before, after, cfg):
    cached = store.load_analysis(meta["id"], sentence, article["index"])
    if cached and not _analysis_partial(cached):
        return cached
    user = prompts.analysis_user(sentence, before, after, article["title"])
    raw = _chat_retry(cfg, prompts.analysis_system(), user, 12000)
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
    analysis["partial"] = _analysis_partial(analysis)
    if not analysis["partial"]:
        # 残缺结果不落盘：下次双击会自动重试
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
