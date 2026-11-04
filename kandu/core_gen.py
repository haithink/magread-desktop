"""生成编排：摘要 / 逐段生词表 / 句解（移植 GenService + VocabFilter + VocabNormalizer + Sentences）。"""
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        if len(w) > 4 and w[-3] == w[-4]:
            forms.append(w[:-3])
        if w.endswith("ied"):
            forms.append(w[:-3] + "y")
    if w.endswith("ing"):
        forms += [w[:-3], w[:-3] + "e"]
        if len(w) > 5 and w[-4] == w[-5]:
            forms.append(w[:-4])
        if w.endswith("ying"):
            forms.append(w[:-4] + "ie")
    return list(dict.fromkeys(forms))


def candidates(paragraph_texts, dict_set, lexicon=None):
    """本地排除已掌握词及其变形；词典原形优先，避免把 business 等词截坏。"""
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
            hit = (lexicon or {}).get(raw.lower()) or {}
            base = hit.get("lemma") or hit.get("word") or raw.lower()
            if base in dict_set or base in ALWAYS_KNOWN:
                continue
            if base not in seen:
                seen.add(base)
                out.append(base)
    return out


# ---------------- 生成任务 ----------------

class GenError(Exception):
    pass


class VocabValidationError(GenError):
    """保留响应中已通过校验的段落，仅补未完成部分。"""
    def __init__(self, sections, errors):
        self.sections = sections
        self.errors = errors
        super().__init__("；".join(f"段落 {i}: {text}" for i, text in errors.items())[:500])


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
    failed_batches = []
    censor_flag = {"hit": False}

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
            err_text = str(last_err)
            failed_batches.append(err_text)
            if _is_censor(err_text):
                censor_flag["hit"] = True
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
    final = _final_digest(meta, articles, existing, collected)
    final["failedBatches"] = len(failed_batches)
    final["censorHint"] = censor_flag["hit"]
    final["lastError"] = failed_batches[-1] if failed_batches else ""
    store.save_digest(final)
    return final


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


def _vocab_context(word, text, lexicon):
    """从正文找词条的真实短语，省去模型重复抄写原文和段首。"""
    text = re.sub(r"\s+", " ", text.replace("’", "'")).strip()
    # 连字符/空格是排版差异，如 science-fiction 与 science fiction。
    context_token = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
    tokens = list(context_token.finditer(text))
    exact = re.search(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", text, re.IGNORECASE)
    if exact:
        start, end = exact.span()
    else:
        wanted = [m.group().lower() for m in context_token.finditer(word.replace("’", "'"))]
        match = next((i for i in range(len(tokens) - len(wanted) + 1) if wanted and all(
            w in lemma_forms(tokens[i + j].group().lower()) or
            w == (lexicon.get(tokens[i + j].group().lower()) or {}).get("lemma")
            for j, w in enumerate(wanted))), None)
        if match is None:
            return ""
        start, end = tokens[match].start(), tokens[match + len(wanted) - 1].end()
    first = next((i for i, m in enumerate(tokens) if m.end() > start), 0)
    last = next((i for i, m in enumerate(tokens) if m.end() >= end), first)
    return text[tokens[max(0, first - 3)].start():tokens[min(len(tokens) - 1, last + 3)].end()] if tokens else ""


def _vocab_sections(obj, chunk, lexicon):
    """展开紧凑结果；缺段/坏结构不得标成完成，兼容旧对象格式响应。"""
    expected = dict(chunk)
    sections, errors, relocated = {}, {}, []
    rows = obj.get("paragraphs")
    if not isinstance(rows, list):
        raise GenError("生词表缺少 paragraphs 数组")
    if -1 in expected and "titleEntries" in obj and not any(p.get("index") == -1 for p in rows if isinstance(p, dict)):
        rows = rows + [{"index": -1, "entries": obj["titleEntries"]}]
    seen_indices = set()
    for row in rows:
        if not isinstance(row, dict):
            raise GenError("生词表段落结构无效")
        try:
            idx = int(row["index"])
        except (KeyError, TypeError, ValueError):
            raise GenError("生词表缺少有效段落编号")
        if idx not in expected:
            continue
        if idx in seen_indices or not isinstance(row.get("entries"), list):
            errors[idx] = "生词表段落重复或词条结构无效"
            sections.pop(idx, None)
            continue
        seen_indices.add(idx)
        entries, seen = [], set()
        for raw in row["entries"]:
            if isinstance(raw, list) and len(raw) == 6:
                raw = dict(zip(("word", "pos", "sense", "root", "term", "phonetic"), raw))
            if isinstance(raw, dict):
                raw = {k: ("" if value is None and k in {"pos", "root", "phonetic", "inText"} else value)
                       for k, value in raw.items()}
            if not isinstance(raw, dict) or not all(isinstance(raw.get(k, ""), str)
                                                  for k in ("word", "pos", "sense", "root", "phonetic", "inText")):
                errors[idx] = "生词表词条结构无效"
                continue
            if not raw.get("word", "").strip() or not raw.get("sense", "").strip():
                errors[idx] = "生词表缺少词语或本文释义"
                continue
            entry = _parse_entry(raw, seen)
            if entry:
                entry["inText"] = _vocab_context(entry["word"], expected[idx], lexicon)
                if not entry["inText"]:
                    matches = [(i, _vocab_context(entry["word"], text, lexicon))
                               for i, text in expected.items() if i != idx]
                    matches = [(i, context) for i, context in matches if context]
                    if len(matches) == 1:
                        target, entry["inText"] = matches[0]
                        relocated.append((target, entry))
                        continue
                    errors[idx] = "生词无法在原文唯一定位：" + entry["word"]
                    continue
                entries.append(entry)
        if idx not in errors:
            sections[idx] = {"index": idx, "startWords": expected[idx][:30], "entries": entries}
    for idx, entry in relocated:
        if idx in sections and all((e["word"], e["sense"]) != (entry["word"], entry["sense"])
                                   for e in sections[idx]["entries"]):
            sections[idx]["entries"].append(entry)
    for idx in expected.keys() - sections.keys():
        errors.setdefault(idx, "模型未返回完整段落，保留待补缺")
    if errors:
        raise VocabValidationError(sections, errors)
    return sections


def generate_vocab(meta, article, level, cfg, on_progress=None, on_chars=None, llm_rotation=None, max_workers=2):
    """本地筛词/音标 + 紧凑语境释义；最多两个请求并行，主线程逐块落盘。"""
    started = time.perf_counter()
    existing = store.load_vocab(meta["id"], article["index"], level) or {}
    processed = set(existing.get("processedParagraphs", []))
    units = [(-1, article["title"]), *enumerate(article["paragraphs"])]
    units = [(idx, text) for idx, text in units if idx not in processed]
    if not units:
        return vocab_normalize(existing, article)

    from . import core_dict
    cfgmod.ensure_wordlists()
    dict_set = cfgmod.wordset(level)
    core_dict.ensure_db(background=False)
    words = [m.group().lower() for _, text in units for m in _TOKEN.finditer(text.replace("’", "'"))]
    lexicon = core_dict.lookup_many(words)
    bases = [hit["lemma"] for hit in lexicon.values() if hit.get("lemma")]
    lexicon.update(core_dict.lookup_many(bases))
    cand = {idx: candidates([text.replace("’", "'")], dict_set, lexicon) for idx, text in units}

    rotation = [dict(cfg)]
    for p in llm_rotation or []:
        if all(p.get("name") != r.get("name") for r in rotation):
            rotation.append(dict(p))
    chunk_failures = {k: list(v) for k, v in existing.get("chunkFailures", {}).items()}
    tried_by_index = {}
    for key, names in chunk_failures.items():
        for idx in map(int, key.split(",")):
            tried_by_index.setdefault(idx, set()).update(names)

    chunks, current, chars = [], [], 0
    for idx, text in units:
        # 标题随首个正文块处理，不再为标题单独发请求。
        if chars + len(text) > 4500 and current and any(i >= 0 for i, _ in current):
            chunks.append(current)
            current, chars = [], 0
        current.append((idx, text))
        chars += len(text)
    if current:
        chunks.append(current)

    pending = []
    for chunk in chunks:
        tried = set.intersection(*(tried_by_index.get(idx, set()) for idx, _ in chunk))
        # 显式点击补缺就是重试授权。优先新渠道，过去失败的配置也不能永久封锁。
        pending.append((chunk, [c for c in rotation if c.get("name") not in tried]
                              + [c for c in rotation if c.get("name") in tried]))

    merged_sections = {p["index"]: p for p in existing.get("paragraphs", [])}
    merged_title = list(existing.get("titleEntries", []))
    merged_idioms = list(existing.get("idioms", []))
    chunk_errors = dict(existing.get("chunkErrors", {}))
    completed = failed_chunks = 0
    censor_hit = False
    lock = threading.Lock()
    received = {}
    stats = {"chunks": len(chunks), "requests": 0, "inputChars": 0, "outputChars": 0,
             "workers": min(max(1, max_workers), 2, len(chunks))}
    system = prompts.vocab_system(level_label(level))

    def run_chunk(chunk, configs):
        key = ",".join(str(idx) for idx, _ in chunk)
        remaining, collected = list(chunk), {}
        failures, details, censor = [], [], False

        def chars_received(n):
            with lock:
                received[key] = max(received.get(key, 0), n)
                if on_chars:
                    on_chars(sum(received.values()))

        for config in configs:
            if not remaining:
                break
            name = config.get("name", "?")
            local_words = {w for idx, _ in remaining for w in cand[idx] if (lexicon.get(w) or {}).get("phonetic")}
            user = prompts.vocab_user(article["title"], -1, remaining, cand, local_words)
            candidate_count = sum(len(cand[idx]) for idx, _ in remaining)
            budget = min(8192, max(2048, 512 + candidate_count * 110))
            request_cfg = dict(config)
            if "deepseek" in (config.get("model") or "").lower():
                request_cfg["thinkingMode"] = "disabled"
            with lock:
                stats["requests"] += 1
                stats["inputChars"] += len(system) + len(user)
                if on_progress:
                    on_progress(completed, len(chunks), name)
            try:
                raw = llm.chat(request_cfg, system, user, max_tokens=budget, on_delta=chars_received, require_complete=True)
                with lock:
                    stats["outputChars"] += len(raw)
                import json
                # 不把修补后的半截 JSON 当作完整生词结果。
                result = _vocab_sections(json.loads(_extract_json(raw)), remaining, lexicon)
                collected.update(result)
                remaining = []
            except VocabValidationError as error:
                collected.update(error.sections)
                remaining = [(i, text) for i, text in remaining if i not in collected]
                failures.append(name)
                details.append({"llm": name, "error": str(error)})
            except Exception as error:
                failures.append(name)
                censor |= _is_censor(str(error))
                message = str(error)
                if config.get("apiKey"):
                    message = message.replace(config["apiKey"], "[redacted]")
                details.append({"llm": name, "error": message[:500]})
        return key, collected, failures, censor, remaining, details

    def save():
        draft = {"level": level, "articleIndex": article["index"], "titleEntries": merged_title,
                 "paragraphs": sorted(merged_sections.values(), key=lambda p: p["index"]),
                 "idioms": list({i["word"].lower(): i for i in merged_idioms}.values()),
                 "processedParagraphs": sorted(processed), "chunkFailures": chunk_failures,
                 "chunkErrors": chunk_errors,
                 "failedChunks": failed_chunks, "censorHint": censor_hit,
                 "generationStats": dict(stats, elapsedMs=round((time.perf_counter() - started) * 1000))}
        result = vocab_normalize(draft, article)
        store.save_vocab(meta["id"], result)
        return result

    if on_chars:
        on_chars(0)
    with ThreadPoolExecutor(max_workers=stats["workers"]) as pool:
        futures = [pool.submit(run_chunk, chunk, configs) for chunk, configs in pending]
        for future in as_completed(futures):
            key, sections, failures, censor, remaining, details = future.result()
            censor_hit |= censor
            if sections:
                entries = [e for section in sections.values() for e in section["entries"]]
                # 原形/音标查询用一条 SQLite 连接，本地数据优先，未收录词保留模型音标。
                phonetics = core_dict.lookup_many([e["word"] for e in entries])
                for entry in entries:
                    if (phonetics.get(entry["word"]) or {}).get("phonetic"):
                        entry["phonetic"] = phonetics[entry["word"]]["phonetic"]
                    sense = entry["sense"]
                    if sense.startswith("常见义") and "；本文义" in sense:
                        common, contextual = sense[len("常见义"):].split("；本文义", 1)
                        merged_idioms.append({"word": entry["word"], "common": common, "inText": contextual})
                for idx, section in sections.items():
                    if idx == -1:
                        merged_title = section["entries"]
                    else:
                        merged_sections[idx] = section
                processed.update(sections)
                chunk_failures = {k: v for k, v in chunk_failures.items()
                                  if not all(int(i) in processed for i in k.split(","))}
                chunk_errors = {k: v for k, v in chunk_errors.items()
                                if not all(int(i) in processed for i in k.split(","))}
            if remaining:
                failed_chunks += 1
                remaining_key = ",".join(str(i) for i, _ in remaining)
                # 切掉已完成段落，失败记录只描述真正仍需补缺的部分。
                chunk_failures.pop(key, None)
                chunk_errors.pop(key, None)
                chunk_failures[remaining_key] = list(dict.fromkeys(failures))
                chunk_errors[remaining_key] = details
            with lock:
                completed += 1
                if on_progress:
                    on_progress(completed, len(chunks), None)
            save()
    return save()


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


def backfill_phonetics(entries):
    """词条音标缺失时从本地 ECDICT 词典离线回填（原位修改，返回是否有改动）。"""
    changed = False
    missing = [e for e in entries if isinstance(e, dict) and not (e.get("phonetic") or "").strip()]
    if not missing:
        return False
    try:
        from . import core_dict
        core_dict.ensure_db(background=False)
        if not core_dict.STATE.get("ready"):
            return False
        records = core_dict.lookup_many([e.get("word", "") for e in missing])
        for e in missing:
            r = records.get(e.get("word", "").strip().lower())
            if r and (r.get("phonetic") or "").strip():
                e["phonetic"] = r["phonetic"]
                changed = True
    except Exception:
        pass
    return changed


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
    backfill_phonetics(analysis["vocab"])
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
