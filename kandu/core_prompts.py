"""提示词与 JSON 提取/修补（对齐 Android 版 Prompts 与固定精读格式）。"""
import re


def digest_system() -> str:
    return (
        "你是资深外刊编辑和译者。用户会给你一本英文杂志中若干篇文章的标题和开头文本，"
        "请为每一篇生成：1) titleEn：文章英文标题（若给出标题原样保留；若没有标题则根据开头拟出）；"
        "2) titleZh：地道、简洁的中文标题（意译，不要直译腔）；3) summaryZh：100～150 字的中文内容摘要，"
        "概括文章核心论点与关键信息，不用套话。只输出 JSON 数组，不要输出任何其他文字，不要展示思考过程。"
    )


def digest_user(book_name: str, items) -> str:
    """items: [(index, title, head_text)]"""
    out = [f"杂志文件：{book_name}。以下是 {len(items)} 篇文章，编号 index 如下。"]
    out.append('只输出 JSON 数组，元素格式：{"index":编号,"titleEn":"...","titleZh":"...","summaryZh":"..."}')
    for index, title, head in items:
        out.append("")
        out.append(f"### 文章 index={index}")
        if title:
            out.append(f"标题：{title}")
        out.append(head)
    return "\n".join(out)


def vocab_system(level_label: str) -> str:
    return (
        "你是英语外刊精读助教。学生的词汇量基准是「" + level_label + "」："
        "四级及更常用的词一律不算生词，不要收录；只收录超纲词、专业术语、熟词僻义（常见词的非常用义项）。"
        "专有名词（人名/地名/机构名）不收录。缩略语如 IMF、NATO 收录为术语。"
        "每个词条给出：word（原形，小写）、phonetic（美式音标，没有把握就留空串）、"
        "pos（词性，如 n. / v. / adj.）、sense（中文释义，取「本文语境下的义项」，不超过 25 字；"
        "熟词僻义须写明「常见义 X；本文义 Y」）、inText（该词在原文中出现的短语，含前后 2～4 个词，不超过 40 字符）、"
        "root（词根词缀拆解或词源线索，如「im- 不 + penetr 穿入 + -able」，不超过 40 字；没有词根故事的给空串，不强加）、"
        "term（是否术语，布尔）。务必控制总输出长度，宁可精简也不要截断。"
        "只输出 JSON 对象，格式："
        '{"paragraphs":[{"index":段落编号,"startWords":"该段开头前几个单词","entries":[{...}]}],'
        '"titleEntries":[标题生词，格式同 entries],'
        '"idioms":[{"word":"熟词僻义词","common":"常见义","inText":"本文义"}]}'
        "没有生词的段落也要有对应元素，entries 为空数组。不要输出 JSON 以外的任何文字，不要展示思考过程。"
    )


def vocab_user(title: str, title_index: int, units, candidates) -> str:
    """units: [(index, text)]; candidates: {index: [words]}"""
    out = [f"以下是文章《{title[:80]}》的标题和逐段文本。段落编号是全文统一编号。"]
    out.append("输出 JSON 里 paragraphs 的 index 必须原样使用我给出的段落编号，绝不要自行重新计数。")
    out.append("startWords 直接摘取该段开头的连续几个单词。")
    out.append("")
    out.append(f"### 标题（段落编号 {title_index}）")
    out.append(title)
    for index, text in units:
        out.append("")
        out.append(f"### 段落 {index}")
        out.append(text[:3500])
    out.append("")
    out.append("提示：以下是我按四级词表初步筛出的候选超纲词，供参考；请自行判断增删（尤其注意熟词僻义不在此列）：")
    for index, words in candidates.items():
        if not words:
            continue
        out.append(f"段落 {index}：{', '.join(words)}")
    return "\n".join(out)


def analysis_system() -> str:
    return (
        "你是英语语法精讲老师，给词汇量 CET-4～6 的中国学生逐句精讲外刊。按固定格式输出 JSON："
        '1) vocab：疑难词汇数组 [{"word":"","phonetic":"美式音标","sense":"中文释义；'
        '熟词僻义须写「常见义 X；本文义 Y」","note":"词根词缀拆解或说明，可空串"}]，'
        "只收学生可能不懂的词（四级内常见词不收），专有名词不收；"
        "2) grammarDiagram：语法对齐图解——把英文原句按成分用空格排开（可多行），每个成分正下方对齐写中文成分名"
        "（主语/谓语/宾语/形式主语/定语从句等），短语和从句用方括号 [] 框住，从属说明用 └── 前缀；"
        "换行用 \\n；图解行与标注行必须上下对齐；"
        '3) grammarPoints：编号语法点数组 [{"title":"如「定语从句」","text":"结合本句讲解"}]；'
        "4) context：该句在文章中的功能、与上下文的呼应、作者修辞意图，1～2 句；"
        "5) translation：一两句自然的中文参考译文。只输出 JSON 对象，不要输出任何其他文字，不要展示思考过程。"
    )


def analysis_user(sentence: str, before: str, after: str, title: str) -> str:
    lines = [f"文章标题：{title[:80]}"]
    if before:
        lines.append(f"上句：{before[:200]}")
    lines.append(f"待解析句子：{sentence}")
    if after:
        lines.append(f"下句：{after[:200]}")
    return "\n".join(lines)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_block(text: str) -> str:
    t = text.strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    start = None
    for i, c in enumerate(t):
        if c in "{[":
            start = i
            break
    if start is not None and start > 0:
        t = t[start:]
    end = max(t.rfind("}"), t.rfind("]"))
    if 0 <= end < len(t) - 1:
        t = t[: end + 1]
    return t.strip()


def repair_truncated_json(text: str):
    """截断 JSON 兜底修补：裁残句/悬空键，补全括号；只保住已生成部分。"""
    s = text.strip()
    if len(s) < 2:
        return None
    in_str = False
    esc = False
    for c in s:
        if esc:
            esc = False
            continue
        if c == "\\" and in_str:
            esc = True
        elif c == '"':
            in_str = not in_str
    if in_str:
        cut = s.rfind('"')
        if cut <= 0:
            return None
        s = s[:cut]
    # 裁掉悬空键/悬空逗号——裁剪可能连环露出新的悬空，循环到稳定
    prev = None
    while prev != s:
        prev = s
        s = s.rstrip().rstrip(",:").rstrip()
        s = re.sub(r",?\s*\"[^\"]*\"\s*$", "", s)
    s = s.rstrip().rstrip(",:").rstrip()
    stack = []
    in_str = False
    esc = False
    for c in s:
        if esc:
            esc = False
            continue
        if c == "\\" and in_str:
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "{":
                stack.append("}")
            elif c == "[":
                stack.append("]")
            elif c == "}" and stack and stack[-1] == "}":
                stack.pop()
            elif c == "]" and stack and stack[-1] == "]":
                stack.pop()
    while stack:
        s += stack.pop()
    return s
