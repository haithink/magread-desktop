"""基础缩写和可确认搭配的本地规则；已知词表不等于掌握所有语境用法。"""
import re


_APOSTROPHES = str.maketrans({c: "'" for c in "‘’ʼ＇"})


def normalize_apostrophes(text):
    # 等长替换，保留正文中的字符位置。
    return text.translate(_APOSTROPHES)


_CONTRACTIONS = set("""
don't doesn't didn't won't wouldn't couldn't shouldn't isn't aren't wasn't weren't
hasn't haven't hadn't can't cannot mustn't needn't mightn't shan't oughtn't ain't let's
""".split())
for _suffix, _subjects in {
    "m": "i",
    "re": "you we they there what who",
    "ve": "i you we they could would should might must",
    "ll": "i you he she it we they there that what who",
    "d": "i you he she it we they there that what who",
    "s": "he she it there that this what who where when why how",
}.items():
    _CONTRACTIONS.update(f"{subject}'{_suffix}" for subject in _subjects.split())


def is_basic_contraction(word):
    return normalize_apostrophes(word).strip().lower() in _CONTRACTIONS


# 只对有明确搭配线索的用法兜底，不把所有已知词重新变成生词。
# 模型仍须结合原文核对；离线提示有独立来源标记，不冒充模型释义。
_CONTEXT_RULES = (
    ("chart", re.compile(
        r"\bchart(?:s|ed|ing)?\b(?:\s+(?:a|an|the|his|her|its|our|their|your|my|one's|"
        r"own|new|independent|different|future|political|economic))"
        r"{0,5}\s+(?:paths?|courses?|ways?|futures?|strateg(?:y|ies)|roadmaps?)\b", re.I),
     "v.", "规划、开辟（道路或方向）"),
    ("stubborn", re.compile(
        r"\bstubborn\b(?:[\s,]+(?:even|almost|quiet|angry|fierce|strong|unyielding))"
        r"{0,4}[\s,]+(?:determination|resolve|persistence|insistence|resistance|refusal|defiance|optimism)\b", re.I),
     "adj.", "执拗而坚定的；不肯退让的"),
)


def contextual_words(text):
    text = normalize_apostrophes(text)
    return {word: {"translation": sense, "pos": pos, "senseSource": "context_rule"}
            for word, pattern, pos, sense in _CONTEXT_RULES if pattern.search(text)}


# 这里只列有明确边界的固定表达；不是把相邻的常用词拼成候选。
# 释义用于模型漏项时兜底，展示时标记为本地提示，仍允许模型按本文改写。
_PHRASE_RULES = (
    ("paper over", r"paper(?:s|ed|ing)?\s+over", "掩盖、粉饰（问题等），并未真正解决；也可指用纸覆盖"),
    ("gloss over", r"gloss(?:es|ed|ing)?\s+over", "对问题轻描淡写、敷衍带过"),
    ("boil down to", r"boil(?:s|ed|ing)?\s+down\s+to", "归根结底是；可归结为"),
    ("fall short of", r"(?:fall(?:s|ing)?|fell|fallen)\s+short\s+of", "未达到（预期、要求或目标）"),
    ("in light of", r"in\s+(?:the\s+)?light\s+of", "鉴于、考虑到（某种情况）"),
    ("at odds with", r"at\s+odds\s+with", "与……不一致、相冲突"),
    ("in the wake of", r"in\s+the\s+wake\s+of", "随着……而来；在……之后（常指后果）"),
    ("on the brink of", r"on\s+the\s+brink\s+of", "濒临；即将发生（某种变化）"),
)
_PHRASE_PATTERNS = [(lemma, re.compile(r"(?<![\w'-])" + pattern + r"(?![\w'-])", re.I), sense)
                    for lemma, pattern, sense in _PHRASE_RULES]


def phrase_candidates(text):
    """保留原文连续片段和词形，不受读者已知单词表影响。"""
    text = normalize_apostrophes(text)
    result = {}
    for lemma, pattern, sense in _PHRASE_PATTERNS:
        for match in pattern.finditer(text):
            word = re.sub(r"\s+", " ", match.group()).lower()
            result[word] = dict(lemma=lemma, translation=sense, pos="phr.", senseSource="phrase_rule")
    return result
