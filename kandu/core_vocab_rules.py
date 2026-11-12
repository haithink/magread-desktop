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
