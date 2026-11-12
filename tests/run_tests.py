# -*- coding: utf-8 -*-
"""刊读桌面版回归测试入口。

强制沙箱：所有测试必须经由本脚本运行（它把数据目录重定向到项目内
_sandbox/，并在结束后清理），绝不触碰真实 %LOCALAPPDATA% 数据。
用法：.venv\\Scripts\\python.exe tests\\run_tests.py [名称...]
"""
import os
import sys
import tempfile

# 必须在导入 kandu 之前设置沙箱数据目录
SANDBOX = os.path.join(tempfile.gettempdir(), "kandu_test_sandbox")
os.environ["KANDU_DATA_DIR"] = SANDBOX
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

assert "KanduDesktop" not in os.environ["KANDU_DATA_DIR"], "沙箱配置无效"

import json as J
import re
import shutil
import threading
import time
from unittest.mock import patch

from kandu import core_config as cfgmod
from kandu import core_parsers as store
from kandu import core_gen as gen
from kandu import core_dict
import kandu.core_llm as llm

RESULTS = []


def case(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


META = {"id": "T", "fileName": "t.epub", "ext": "epub", "size": 1, "lastModified": 0, "path": ""}
ART = {"index": 0, "title": "t", "paragraphs": ["text"]}


@case("mojibake 修复矩阵")
def test_mojibake():
    from kandu.core_gen import fix_mojibake, looks_mojibake
    for m in ["一", "裂", "€", "国会会对伊朗强硬回击？", "尼日利亚是一座无烟的大城市",
              "What about English? Café ça va."]:
        cor = m.encode("utf-8").decode("cp1252", errors="replace")
        if "�" in cor:
            assert fix_mojibake(cor) == cor  # 不可逆保持原样
        else:
            assert fix_mojibake(cor) == m
    assert fix_mojibake("正常中文内容。") == "正常中文内容。"


@case("digest 智能续传与失败批记录")
def test_digest_resume():
    calls = []
    def fake(cfg, system, user, max_tokens=0, on_delta=None, **kwargs):
        calls.append(cfg["model"])
        idxs = [int(m) for m in re.findall(r"index=(\d+)", user)]
        if "bad" in cfg["model"]:
            raise llm.LlmError("sensitive information upstream_error")
        arr = [{"index": i, "titleEn": f"T{i}", "titleZh": f"中{i}", "summaryZh": f"摘{i}"} for i in idxs]
        return J.dumps(arr, ensure_ascii=False)
    gen._chat_retry = fake
    meta = dict(META, id="T_DIGEST")
    articles = [{"index": i, "title": f"A{i}", "paragraphs": [f"p{i} text"]} for i in range(8)]
    store._json_write(store._cache_dir(meta["id"]) / "content.json",
                      {"bookSize": 1, "format": "epub", "articles": articles})
    d = gen.generate_digest(meta, {"name": "b", "provider": "openai", "baseUrl": "x",
                                   "apiKey": "k", "model": "bad"})
    assert d.get("failedBatches", 0) >= 1
    assert any("（本篇摘要生成失败" in a["summaryZh"] for a in d["articles"])
    d2 = gen.generate_digest(meta, {"name": "g", "provider": "openai", "baseUrl": "x",
                                    "apiKey": "k", "model": "good"})
    assert all("（本篇摘要生成失败" not in a["summaryZh"] for a in d2["articles"])
    assert len(d2["articles"]) == 8


@case("vocab 分块隔离与 LLM 轮换补缺")
def test_vocab_resume():
    calls = []
    def fake(cfg, system, user, max_tokens=0, on_delta=None, **kwargs):
        calls.append(cfg["model"])
        idxs = [int(m) for m in re.findall(r"### 段落 (-?\d+)", user)]
        if 3 in idxs and cfg["model"] == "bad":
            raise llm.LlmError("sensitive information upstream_error")
        arr = [{"index": i, "startWords": f"p{i}",
                "entries": [] if i < 0 else [{"word": "word", "sense": f"释{i}"}]} for i in idxs]
        return J.dumps({"paragraphs": arr}, ensure_ascii=False)
    gen._chat_retry = fake
    llm.chat = fake
    meta = dict(META, id="T_VOCAB")
    art = {"index": 0, "title": "t", "paragraphs": [(f"para {i} " + "word " * 150) for i in range(9)]}
    bad = {"name": "bad", "provider": "openai", "baseUrl": "x", "apiKey": "k", "model": "bad"}
    good = {"name": "good", "provider": "openai", "baseUrl": "x", "apiKey": "k", "model": "good"}
    # 第一轮：轮换里只有坏渠道 → 含第 3 段的块失败
    v1 = gen.generate_vocab(meta, art, "cet4", bad, llm_rotation=[bad])
    assert v1.get("censorHint") is True
    assert any("3" in key.split(",") for key in v1.get("chunkFailures", {}))
    assert 3 not in v1.get("processedParagraphs", [])
    # 第二轮：轮换加入好渠道 → 自动换好渠道补缺成功
    calls.clear()
    v2 = gen.generate_vocab(meta, art, "cet4", bad, llm_rotation=[bad, good])
    print('  pass2 calls:', calls)
    print('  pass2 processed:', sorted(v2.get("processedParagraphs", [])))
    print('  pass2 chunkFailures:', v2.get("chunkFailures"))
    assert "good" in calls, "应自动换好渠道"
    assert 3 in v2.get("processedParagraphs", [])
    assert not v2.get("failedChunks")
    assert not v2.get("chunkFailures"), "补缺成功后应清除历史失败记录"
    # 第三轮：全部完成 → 无可补
    calls.clear()
    gen.generate_vocab(meta, art, "cet4", bad, llm_rotation=[bad, good])
    assert calls == []


@case("本地词典查词")
def test_dict():
    core_dict.ensure_db(background=False)
    r = core_dict.lookup("concealing")
    assert r and "conceal" in J.dumps(r)
    r = core_dict.lookup("xxxxq")
    assert r and r.get("miss")


@case("生词本地筛词、紧凑结果与语境补全")
def test_vocab_compact():
    core_dict.ensure_db(background=False)
    lexicon = core_dict.lookup_many(["running", "children", "went", "business", "researchers"])
    assert lexicon["running"]["lemma"] == "run"
    assert lexicon["children"]["lemma"] == "child"
    assert lexicon["went"]["lemma"] == "go"
    assert lexicon["researchers"]["lemma"] == "researcher"
    words = gen.candidates(["running children went business researchers"], {"run", "child", "go"}, lexicon)
    assert words == ["business", "researcher"], words
    assert gen.candidates(["business"], set()) == ["business"], "不能用最短猜测词形把单词截坏"

    art = {"index": 0, "title": "A candid look at a phenomenon", "paragraphs": [
        "The researchers investigated the phenomenon at the bank of the river.",
        "The phenomenon shaped interest rates and yielded further observations.",
    ]}
    cfgmod.ensure_wordlists()
    calls = []
    def fake(cfg, system, user, max_tokens=0, on_delta=None, **kwargs):
        calls.append((system, user, max_tokens))
        assert '"startWords"' not in system and "已有本地音标" in user
        assert max_tokens < 16000
        return J.dumps({"paragraphs": [
            {"index": -1, "entries": [["phenomenon", "n.", "现象", "希腊语：显现", False, ""]]},
            {"index": 0, "entries": [
                ["phenomenon", "n.", "现象", "希腊语：显现", False, ""],
                ["bank", "n.", "常见义银行；本文义河岸", "", False, ""],
            ]},
            {"index": 1, "entries": [
                ["phenomenon", "n.", "现象", "希腊语：显现", False, ""],
                ["interest rate", "n.", "利率", "", True, ""],
            ]},
        ]}, ensure_ascii=False)
    with patch.object(llm, "chat", fake):
        result = gen.generate_vocab(dict(META, id="T_COMPACT"), art, "cet4", {"name": "test"})
    assert len(calls) == 1, "短文章的标题与正文应合为一次请求"
    assert result["processedParagraphs"] == [-1, 0, 1]
    assert result["titleEntries"][0]["phonetic"], "音标应由本地词典补齐"
    assert len(result["paragraphs"]) == 2, "重复单词必须保留在各自段落"
    for p in result["paragraphs"]:
        assert all(e["inText"] in art["paragraphs"][p["index"]] for e in p["entries"])
    assert result["idioms"][0] == {"word": "bank", "common": "银行", "inText": "河岸"}
    assert any(e["word"] == "interest rate" for e in result["paragraphs"][1]["entries"])


@case("基础缩写统一过滤且保留所有格和完整短语")
def test_vocab_contractions():
    from kandu.core_vocab_rules import is_basic_contraction
    contractions = ["He'd", "He’d", "heʼd", "she‘d", "you＇d", "we'd", "they'd", "it's", "WHO'S", "could've"]
    core_dict.ensure_db(background=False)
    for word in contractions:
        assert is_basic_contraction(word), word
        assert not gen.candidates([word], set(), gen._vocab_lexicon([(0, word)])), word
        assert not gen._vocab_sections({"paragraphs": [{"index": 0, "entries": [
            [word, "缩写", "基础缩写", "", False, ""]]}]}, [(0, word)], {})[0]["entries"]
    for word in ["author's", "o'reilly's", "rock'n'roll", "he'd rather"]:
        assert not is_basic_contraction(word), word
    assert "author" in gen.candidates(["author's"], set(), gen._vocab_lexicon([(0, "author's")]))
    entries = gen._vocab_sections({"paragraphs": [{"index": 0, "entries": [
        ["he’d rather", "phr.", "他宁愿", "", False, ""]]}]}, [(0, "He’d rather read.")], {})[0]["entries"]
    assert entries[0]["word"] == "he'd rather", entries


@case("四级已知词搭配义补漏、词形变化与普通用法区分")
def test_vocab_context_rules():
    from kandu.core_vocab_rules import contextual_words
    core_dict.ensure_db(background=False)
    cfgmod.ensure_wordlists()
    known = cfgmod.wordset("cet4")
    assert {"chart", "stubborn"} <= known
    text = "He’d also developed a stubborn, even angry determination to chart his own path through the world."
    lexicon = gen._vocab_lexicon([(0, text)])
    candidates = gen.candidates([text], known, lexicon)
    assert {"chart", "stubborn"} <= set(candidates) and "he'd" not in candidates, candidates
    required = gen._required_candidates(text, candidates, lexicon)
    assert {"chart", "stubborn"} <= required.keys()
    assert all(required[w]["senseSource"] == "context_rule" for w in ("chart", "stubborn"))
    for text in ["She charted her own course.", "Charting a new path takes time.", "He charts his future."]:
        lexicon = gen._vocab_lexicon([(0, text)])
        candidates = gen.candidates([text], known, lexicon)
        assert "chart" in candidates, (text, candidates)
        assert "chart" in gen._required_candidates(text, candidates, lexicon)
    for text in ["A stubborn child studied a bar chart.", "The chart of our future is blue.",
                 "He was stubborn. Determination mattered.", "The chart is clear. His path is long."]:
        assert not contextual_words(text), text
    assert not gen.candidates(["A stubborn child studied a bar chart."], known,
                              gen._vocab_lexicon([(0, "A stubborn child studied a bar chart.")])), "普通已知用法不应一律变成生词"


@case("语境补漏与旧缓存迁移不增加模型请求且保留个人收藏")
def test_vocab_context_cache():
    core_dict.ensure_db(background=False)
    text = "He’d also developed a stubborn, even angry determination to chart his own path through the world."
    art = dict(index=0, title="A story", paragraphs=[text, "An unprocessed paragraph."])
    old = dict(articleIndex=0, level="cet4", coverageVersion=1, titleEntries=[],
               processedParagraphs=[-1, 0], phraseProcessedParagraphs=[-1, 0],
               chunkFailures={"1": ["old"]}, paragraphs=[dict(index=0, startWords=text[:30], entries=[
                   dict(word="he’d", sense="he had 的缩写"),
                   dict(word="path", sense="已有本文义", note="原有笔记")])])
    original = J.loads(J.dumps(old))
    with patch.object(llm, "chat", side_effect=AssertionError("缓存补漏不能调用模型")):
        audited = gen.audit_vocab_coverage(old, art, "cet4")
        assert gen.audit_vocab_coverage(audited, art, "cet4") == audited
    assert old == original, "迁移不能原位改动原始缓存"
    assert audited["coverageVersion"] == gen.VOCAB_COVERAGE_VERSION
    assert audited["processedParagraphs"] == [-1, 0] and audited["chunkFailures"] == {"1": ["old"]}
    assert audited["phraseProcessedParagraphs"] == [-1, 0]
    assert all(p["index"] != 1 for p in audited["paragraphs"])
    entries = {e["word"]: e for e in audited["paragraphs"][0]["entries"]}
    assert "he’d" not in entries and "he'd" not in entries
    assert entries["path"]["note"] == "原有笔记" and entries["path"]["sense"] == "已有本文义"
    for word in ["chart", "stubborn"]:
        assert entries[word]["contextPending"] and entries[word]["senseSource"] == "context_rule"
        assert entries[word]["phonetic"] and not entries[word]["root"]
    assert "规划" in entries["chart"]["sense"] and "坚定" in entries["stubborn"]["sense"]
    # 即使模型遗漏或又给出缩写，也只用同一次请求的结果加本地兜底。
    meta = dict(META, id="T_CONTEXT_RULES")
    art = dict(art, paragraphs=[text])
    def fake(cfg, system, user, **kwargs):
        must = next(line for line in user.splitlines() if line.startswith("必须收录"))
        assert "chart" in must and "stubborn" in must and "he'd" not in must
        assert "本地搭配提示" in user and "基础人称/助动词缩写" in system
        return J.dumps({"paragraphs": [dict(index=-1, entries=[]), dict(index=0, entries=[
            ["he’d", "缩写", "he had", "", False, ""]])]})
    with patch.object(llm, "chat", side_effect=fake) as chat:
        generated = gen.generate_vocab(meta, art, "cet4", dict(name="test"))
        assert chat.call_count == 1
    assert {e["word"] for e in generated["paragraphs"][0]["entries"]} >= {"chart", "stubborn"}
    assert not generated["failedChunks"] and generated["processedParagraphs"] == [-1, 0]
    def enrich(cfg, system, user, **kwargs):
        assert "### 段落 -1" not in user
        return J.dumps({"paragraphs": [dict(index=0, entries=[
            ["chart", "v.", "开辟自己的人生道路", "", False, ""],
            ["stubborn", "adj.", "执拗而坚定的", "", False, ""]])]})
    with patch.object(llm, "chat", side_effect=enrich) as chat:
        enriched = gen.generate_vocab(meta, art, "cet4", dict(name="test"), enrich=True)
        assert chat.call_count == 1
    assert gen.vocab_context_pending(enriched) == 0
    assert all(not e.get("senseSource") for p in enriched["paragraphs"] for e in p["entries"])
    # 用户显式收藏的基础缩写仍允许展示；本地过滤不删除生词本记录。
    personal = dict(generated, titleEntries=[dict(word="he’d", sense="个人笔记", senseSource="wordbook")])
    assert gen.vocab_normalize(personal, art)["titleEntries"] == personal["titleEntries"]


@case("本地语境提示在生词窗口明确标记来源")
def test_vocab_context_ui():
    import tkinter as tk
    from types import SimpleNamespace
    from kandu import ui_vocabulary as sheets
    root = tk.Tk()
    root.withdraw()
    try:
        app = SimpleNamespace(settings={"sheetFontSize": 11}, save_settings=lambda: None)
        win, txt, _ = sheets.make_sheet(root, app, "段落生词", "本地语境提示")
        sheets.render_vocab(txt, dict(paragraphs=[dict(index=0, entries=[
            dict(word="chart", sense="规划、开辟（道路或方向）", senseSource="context_rule", contextPending=True)
        ])]), 0)
        content = txt.get("1.0", "end")
        assert "本地搭配提示（可补充本文含义）" in content and "规划" in content
        assert "词典释义" not in content
    finally:
        root.destroy()


@case("超纲漏词覆盖、专名区分、词形误筛与离线兜底")
def test_vocab_coverage():
    core_dict.ensure_db(background=False)
    cfgmod.ensure_wordlists()
    known = cfgmod.wordset("cet4")
    assert {"space", "system", "technology", "internet", "story", "the"} <= known
    assert "candid" not in known and "shameless" not in known
    text = "Candid remarks helped. The response was candid and shameless. Candid supplied data to Goodhart and NASA."
    units = [(0, text)]
    lexicon = gen._vocab_lexicon(units)
    candidates = gen.candidates([text], known, lexicon)
    assert "candid" in candidates and "shameless" in candidates
    assert not gen.candidates(["The story was about a woman and her children this year."], known,
        gen._vocab_lexicon([(0, "The story was about a woman and her children this year.")])), "基础词表缺项不能变成批量生词"
    required = gen._required_candidates(text, candidates, lexicon)
    assert "candid" in required and "shameless" in required
    assert "goodhart" not in required and "nasa" not in required
    assert "candid" not in gen._required_candidates("Data from Candid arrived.", ["candid"], lexicon), "机构名场景不应强制按普通词补齐"
    assert "candid" in gen._required_candidates("He paused. Candid comments followed.", ["candid"], lexicon)
    assert "new" in gen.lemma_forms("news")
    assert gen.candidates(["news"], {"new"}, core_dict.lookup_many(["news"])) == ["news"], "独立词不能被猜测词形误筛"

    art = {"index": 0, "title": "A story", "paragraphs": [
        "The response was candid and shameless.", "Candid remarks helped."]}
    calls = []
    def omitted(config, system, user, **kwargs):
        calls.append(user)
        assert "不可再按自己的常用程度" in system and "必须收录" in user
        return J.dumps({"paragraphs": [{"index": int(i), "entries": []}
            for i in re.findall(r"### 段落 (-?\d+)", user)]})
    meta = dict(META, id="T_COVERAGE")
    with patch.object(llm, "chat", omitted):
        vocab = gen.generate_vocab(meta, art, "cet4", {"name": "test"})
    assert len(calls) == 1, "漏词兜底不应自动增加模型调用"
    assert vocab["processedParagraphs"] == [-1, 0, 1] and not vocab["failedChunks"]
    assert gen.vocab_context_pending(vocab) >= 3
    for section in vocab["paragraphs"]:
        candid = next(e for e in section["entries"] if e["word"] == "candid")
        assert candid["senseSource"] == "dictionary" and candid["contextPending"]
        assert "坦白" in candid["sense"] and candid["phonetic"] and not candid["root"]
        assert candid["inText"] in art["paragraphs"][section["index"]]
    with patch.object(llm, "chat", side_effect=AssertionError("打开完整缓存不能重新调用模型")):
        cached = gen.generate_vocab(meta, art, "cet4", {"name": "test"})
    assert cached == vocab
    # “补充本文释义”只处理待补词所在段落，原有词条照常保留。
    def enrich(config, system, user, **kwargs):
        assert "### 段落 -1" not in user
        return J.dumps({"paragraphs": [
            {"index": 0, "entries": [["candid", "adj.", "坦率直言的", "", False, ""],
                                     ["shameless", "adj.", "不知羞耻的", "", False, ""]]},
            {"index": 1, "entries": [["candid", "adj.", "坦率的", "", False, ""]]}]})
    with patch.object(llm, "chat", enrich):
        filled = gen.generate_vocab(meta, art, "cet4", {"name": "test"}, enrich=True)
    assert gen.vocab_context_pending(filled) == 0, [(p["index"], e["word"]) for p in filled["paragraphs"] for e in p["entries"] if e.get("contextPending")]
    assert all(not e.get("senseSource") for p in filled["paragraphs"] for e in p["entries"])
    # 语境释义依然遗漏时不能丢掉词典兜底，也不能误标语境义已补齐。
    store.save_vocab(meta["id"], vocab)
    with patch.object(llm, "chat", omitted):
        still_pending = gen.generate_vocab(meta, art, "cet4", {"name": "test"}, enrich=True)
    assert gen.vocab_context_pending(still_pending) == gen.vocab_context_pending(vocab)


@case("旧词表加载自动补漏与生词弹窗的释义来源标注")
def test_vocab_coverage_ui():
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_reader import ReaderFrame

    meta = dict(META, id="T_COVERAGE_UI")
    art = {"index": 0, "title": "A story", "paragraphs": ["The reply was candid."]}
    store.save_vocab(meta["id"], {"level": "cet4", "articleIndex": 0, "titleEntries": [],
        "paragraphs": [], "processedParagraphs": [-1, 0], "idioms": []})
    root = tk.Tk()
    posts, events, errors = [], [], []
    root.report_callback_exception = lambda *args: errors.append(args)
    app = SimpleNamespace(settings={"readerFontSize": 13, "sheetFontSize": 11, "vocabLevel": "cet4", "llms": []},
                          post=lambda *args: posts.append(args), emit=lambda *args: events.append(args),
                          open_digest=lambda *args: None, save_settings=lambda: None)
    try:
        frame = ReaderFrame(root, app, meta, 0)
        frame.pack(fill="both", expand=True)
        with patch.object(store, "ensure_content", return_value={"articles": [art]}), patch.object(llm, "chat",
                side_effect=AssertionError("旧缓存补漏不能调用模型")):
            frame._load_worker()
        assert events[0][0] == "content_loaded"
        assert gen.vocab_context_pending(store.load_vocab(meta["id"], 0, "cet4")) == 1
        frame.on_content_loaded(events[0][1])
        root.update()
        frame._show_para_vocab(0)
        root.update()
        win = next(w for w in frame.winfo_children() if isinstance(w, tk.Toplevel))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        txt = next(w for w in descendants(win) if isinstance(w, tk.Text))
        assert "词典释义（待补充本文含义）" in txt.get("1.0", "end")
        button = next(w for w in descendants(win) if w.winfo_class() in ("Button", "TButton")
                      and "补充本文释义" in str(w.cget("text")))
        with patch.object(frame, "generate_vocab", side_effect=lambda **kw: setattr(frame, "vocab_running", True)) as generate:
            button.invoke()
            generate.assert_called_once_with(enrich=True)
        assert not win.winfo_exists() and not errors, errors
    finally:
        root.destroy()


@case("旧词表离线补漏、保留既有词条且不误标失败段完成")
def test_vocab_cached_coverage():
    art = {"index": 0, "title": "A story", "paragraphs": [
        "The bank was candid.", "The bank was open.", "An opaque wall."]}
    existing = {"articleIndex": 0, "level": "cet4", "titleEntries": [], "idioms": [],
        "paragraphs": [{"index": 0, "startWords": "The bank was candid.", "entries": [
            {"word": "bank", "sense": "河岸", "root": "原有说明", "inText": "The bank was candid"}]}],
        "processedParagraphs": [-1, 0, 1], "chunkFailures": {"2": ["test"]}}
    original = J.loads(J.dumps(existing))
    with patch.object(llm, "chat", side_effect=AssertionError("旧缓存补漏必须离线")):
        audited = gen.audit_vocab_coverage(existing, art, "cet4")
        assert audited == gen.audit_vocab_coverage(audited, art, "cet4"), "重复打开必须幂等"
    assert existing == original, "审核不能原地修改正在显示的词表"
    assert audited["processedParagraphs"] == [-1, 0, 1] and audited["chunkFailures"] == {"2": ["test"]}
    assert all(p["index"] != 2 for p in audited["paragraphs"]), "未生成的段落不能由离线兜底误报已完成"
    entries = next(p["entries"] for p in audited["paragraphs"] if p["index"] == 0)
    assert entries[0]["sense"] == "河岸" and entries[0]["root"] == "原有说明"
    assert any(e["word"] == "candid" and e["contextPending"] for e in entries)


@case("生词分块候选隔离、长段落与旧缓存续传")
def test_vocab_payload():
    units = [(7, "phenomenon " * 400)]
    user = gen.prompts.vocab_user("test", -1, units, {0: ["unrelated"], 7: ["phenomenon"]})
    assert "unrelated" not in user
    assert units[0][1] in user, "长段落不能再在 3500 字符处静默截断"
    assert "### 段落 -1" not in user, "已处理标题不能每块重复生成"
    art = {"index": 0, "title": "Title", "paragraphs": ["Already cached.", "A new phenomenon appears."]}
    meta = dict(META, id="T_OLD_CACHE")
    store.save_vocab(meta["id"], {"articleIndex": 0, "level": "cet4", "titleEntries": [],
        "paragraphs": [{"index": 0, "startWords": "Already cached.", "entries": [{
            "word": "cached", "sense": "已缓存", "inText": "Already cached."}]}],
        "processedParagraphs": [-1, 0], "chunkFailures": {"0,1": ["old"]}})
    def fake(cfg, system, user, **kwargs):
        assert cfg["name"] == "new"
        assert "### 段落 0" not in user and "### 段落 -1" not in user
        return J.dumps({"paragraphs": [{"index": 1, "entries": [["phenomenon", "n.", "现象", "", False, ""]]}]})
    with patch.object(llm, "chat", fake):
        result = gen.generate_vocab(meta, art, "cet4", {"name": "old"}, llm_rotation=[{"name": "new"}])
    assert result["processedParagraphs"] == [-1, 0, 1]
    assert result["paragraphs"][0]["entries"][0]["sense"] == "已缓存"
    assert not result["chunkFailures"]


@case("生词缺段和无效响应不能误报完成")
def test_vocab_incomplete():
    art = {"index": 0, "title": "Title", "paragraphs": ["A phenomenon appears.", "A second observation."]}
    for case_id, response in enumerate([
        {}, {"paragraphs": [{"index": -1, "entries": []}]},
        {"paragraphs": [{"index": i, "entries": [["inventedword", "n.", "无效", "", False, ""]]}
                        for i in [-1, 0, 1]]},
    ]):
        with patch.object(llm, "chat", return_value=J.dumps(response)):
            result = gen.generate_vocab(dict(META, id=f"T_BAD_{case_id}"), art, "cet4", {"name": "bad"})
        assert result["processedParagraphs"] == ([-1] if case_id == 1 else [])
        assert result["failedChunks"] == 1
    with patch.object(llm, "chat", side_effect=llm.LlmError("暂时不可用")) as chat:
        result = gen.generate_vocab(dict(META, id="T_BAD_0"), art, "cet4", {"name": "bad"})
    assert chat.call_count == 1, "显式补缺应允许重试过去失败的同名配置"
    assert result["failedChunks"] == 1
    assert result["chunkErrors"]["-1,0,1"][0]["error"] == "暂时不可用"


@case("生词串段自动归位与连字符定位")
def test_vocab_misplaced():
    units = [(0, "This is a sprawling science-fiction novel."),
             (1, "Its prophetic pages discuss modernity.")]
    response = {"paragraphs": [
        {"index": 0, "entries": [["science fiction", "n.", "科幻", "", True, ""],
                                 ["prophetic", "adj.", "预言的", "", False, ""]]},
        {"index": 1, "entries": [["prophetic", "adj.", "预言的", "", False, ""]]},
    ]}
    sections = gen._vocab_sections(response, units, {})
    assert [e["word"] for e in sections[0]["entries"]] == ["science fiction"]
    assert [e["word"] for e in sections[1]["entries"]] == ["prophetic"]
    assert "science-fiction" in sections[0]["entries"][0]["inText"]
    response["paragraphs"][0]["entries"].append(["inventedword", "n.", "不存在", "", False, ""])
    try:
        gen._vocab_sections(response, units, {})
    except gen.VocabValidationError as error:
        assert set(error.sections) == {1}
        assert 0 in error.errors
    else:
        raise AssertionError("原文中完全不存在的词不能误标完成")


@case("生词部分成功保存、缩小补缺与失败渠道重试")
def test_vocab_partial_retry():
    art = {"index": 0, "title": "Novel", "paragraphs": ["A prophetic novel.", "A sprawling story."]}
    meta = dict(META, id="T_PARTIAL_RETRY")
    calls = []
    cfg = {"name": "same", "model": "deepseek-flash"}
    def first(config, system, user, **kwargs):
        calls.append(user)
        assert config["thinkingMode"] == "disabled"
        return J.dumps({"paragraphs": [{"index": -1, "entries": []},
            {"index": 0, "entries": [["prophetic", "adj.", "预言的", "", False, ""]]}]})
    with patch.object(llm, "chat", first):
        v = gen.generate_vocab(meta, art, "cet4", cfg)
    assert v["processedParagraphs"] == [-1, 0]
    assert v["chunkFailures"] == {"1": ["same"]}
    assert v["chunkErrors"]["1"]
    assert "thinkingMode" not in cfg, "任务参数不能改写用户配置"
    def retry(config, system, user, **kwargs):
        calls.append(user)
        assert re.findall(r"### 段落 (-?\d+)", user) == ["1"]
        return J.dumps({"paragraphs": [{"index": 1, "entries": [["sprawling", "adj.", "庞杂的", "", False, ""]]}]})
    with patch.object(llm, "chat", retry):
        v = gen.generate_vocab(meta, art, "cet4", cfg)
    assert len(calls) == 2
    assert v["processedParagraphs"] == [-1, 0, 1]
    assert not v["chunkErrors"] and not v["chunkFailures"] and not v["failedChunks"]
    meta = dict(META, id="T_PARTIAL_ROTATE")
    def rotated(config, system, user, **kwargs):
        return first(config, system, user, **kwargs) if config["name"] == "same" else retry(config, system, user, **kwargs)
    with patch.object(llm, "chat", rotated):
        v = gen.generate_vocab(meta, art, "cet4", cfg, llm_rotation=[{"name": "backup"}])
    assert v["processedParagraphs"] == [-1, 0, 1]
    assert not v["failedChunks"]


@case("生词截断 JSON 不可修补为完成")
def test_vocab_strict_json():
    art = {"index": 0, "title": "Title", "paragraphs": ["A prophetic novel."]}
    broken = '{"paragraphs":[{"index":-1,"entries":[]},{"index":0,"entries":[]}'
    with patch.object(llm, "chat", return_value=broken):
        v = gen.generate_vocab(dict(META, id="T_STRICT_JSON"), art, "cet4", {"name": "mock"})
    assert v["processedParagraphs"] == []
    assert v["failedChunks"] == 1


@case("生词最多两路并发与逐块落盘")
def test_vocab_parallel():
    art = {"index": 0, "title": "Title", "paragraphs": ["phenomenon " * 250 for _ in range(6)]}
    timings = {}
    for workers in [1, 2]:
        state = {"active": 0, "peak": 0, "calls": 0}
        lock = threading.Lock()
        progress, saved = [], []
        meta = dict(META, id=f"T_PARALLEL_{workers}")
        real_save = store.save_vocab
        def save(book_id, vocab):
            saved.append(set(vocab["processedParagraphs"]))
            real_save(book_id, vocab)
        def fake(cfg, system, user, max_tokens=0, on_delta=None, **kwargs):
            with lock:
                state["active"] += 1
                state["calls"] += 1
                state["peak"] = max(state["peak"], state["active"])
            try:
                time.sleep(0.06)  # 模拟固定网络等待，不连接真实模型。
                if on_delta:
                    on_delta(40)
                indices = [int(i) for i in re.findall(r"### 段落 (-?\d+)", user)]
                return J.dumps({"paragraphs": [{"index": i, "entries": []} for i in indices]})
            finally:
                with lock:
                    state["active"] -= 1
        started = time.perf_counter()
        with patch.object(llm, "chat", fake), patch.object(store, "save_vocab", save):
            result = gen.generate_vocab(meta, art, "cet4", {"name": "test"}, max_workers=workers,
                on_progress=lambda done, total, name: progress.append((done, total)))
        timings[workers] = time.perf_counter() - started
        assert state["peak"] == workers, state
        assert state["calls"] == 6, "标题不应单独增加调用"
        assert result["processedParagraphs"] == [-1, *range(6)]
        assert progress[-1] == (6, 6)
        assert [d for d, _ in progress] == sorted(d for d, _ in progress)
        assert len(saved) >= 6 and saved[0] < saved[-1], "必须保留逐块续传落盘"
    print(f"  mock latency: serial={timings[1]:.3f}s, parallel={timings[2]:.3f}s")


@case("紧凑词条输出体积")
def test_vocab_output_size():
    old = [{"word": f"phenomenon{i}", "phonetic": "fəˈnɒmɪnən", "pos": "n.", "sense": "本文现象",
            "root": "希腊语：显现", "term": False, "inText": "research into the phenomenon continues"} for i in range(40)]
    compact = [[e["word"], e["pos"], e["sense"], e["root"], e["term"], ""] for e in old]
    before = len(J.dumps(old, ensure_ascii=False, separators=(",", ":")))
    after = len(J.dumps(compact, ensure_ascii=False, separators=(",", ":")))
    assert after < before * 0.5
    print(f"  40 entries: object={before} chars, compact={after} chars ({1-after/before:.0%} smaller)")


@case("LLM 普通 JSON 不重复请求且不误判推理内容截断")
def test_llm_json_response():
    class Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        def __init__(self, body, headers=None):
            self.text = J.dumps(body, ensure_ascii=False)
            if headers is not None:
                self.headers = headers
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def iter_lines(self, **kwargs):
            yield self.text.encode("utf-8")
    cfg = {"name": "test", "baseUrl": "http://local.test", "apiKey": "test", "model": "test"}
    assert "thinking" not in llm._body(cfg, "s", "u", 2048, .3, True)
    for provider in ["openai", "anthropic"]:
        assert llm._body(dict(cfg, provider=provider, thinkingMode="disabled"), "s", "u", 2048, .3, True)["thinking"] == {"type": "disabled"}
    body = {"choices": [{"message": {"content": "结果", "reasoning_content": "thought"}, "finish_reason": "stop"}]}
    for headers in [{"Content-Type": "application/json"}, {}]:
        with patch.object(llm.requests, "post", return_value=Response(body, headers)) as post:
            assert llm.chat(cfg, "s", "u") == "结果"
            assert post.call_count == 1, "忽略流式参数的网关不应被请求两次"
    anthropic = {"content": [{"type": "text", "text": "结果"}], "stop_reason": "end_turn"}
    with patch.object(llm.requests, "post", return_value=Response(anthropic)) as post:
        assert llm.chat(dict(cfg, provider="anthropic"), "s", "u") == "结果"
        assert post.call_count == 1
    rejected = Response({})
    rejected.status_code = 400
    rejected.text = "max_tokens must be less than 1500"
    with patch.object(llm.requests, "post", side_effect=[rejected, Response(body)]) as post:
        assert llm.chat(cfg, "s", "u", max_tokens=2048) == "结果"
        assert [c.kwargs["json"]["max_tokens"] for c in post.call_args_list] == [2048, 1024]
    truncated = {"choices": [{"message": {"content": '{"paragraphs":[]}'}, "finish_reason": "length"}]}
    with patch.object(llm.requests, "post", return_value=Response(truncated)):
        try:
            llm.chat(cfg, "s", "u", max_tokens=32768, require_complete=True)
        except llm.LlmError:
            pass
        else:
            raise AssertionError("不能把被截断的生词表当作完整结果")
    assert llm._looks_truncated(J.dumps({"stop_reason": "max_tokens"}))


@case("LLM SSE 实时进度与推理额度重试")
def test_llm_stream():
    cfg = {"name": "test", "baseUrl": "http://local.test", "apiKey": "test", "model": "test"}
    class Response:
        status_code = 200
        headers = {"Content-Type": "text/event-stream"}
        def __init__(self, frames, done=True):
            self.frames = frames
            self.done = done
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def iter_lines(self, **kwargs):
            for frame in self.frames:
                yield ('data: ' + J.dumps(frame, ensure_ascii=False)).encode('utf-8')
            if self.done:
                yield b'data: [DONE]'
    counts = []
    responses = [Response([
        {"choices": [{"delta": {"reasoning_content": "thought"}, "finish_reason": "length"}]},
    ]), Response([
        {"choices": [{"delta": {"content": "测试"}}]},
        {"choices": [{"delta": {"content": "完成"}, "finish_reason": "stop"}]},
    ])]
    with patch.object(llm.requests, "post", side_effect=responses) as post:
        assert llm.chat(cfg, "s", "u", max_tokens=2048, on_delta=counts.append) == "测试完成"
        assert post.call_count == 2, "推理烧光额度应直接扩容，无需重复非流式请求"
        assert [c.kwargs["json"]["max_tokens"] for c in post.call_args_list] == [2048, 4096]
    assert counts[-1] == 4 and 2 in counts, counts
    with patch.object(llm.requests, "post", return_value=Response([
        {"choices": [{"delta": {"content": '{"paragraphs":[]}'}}]},
    ], done=False)) as post:
        try:
            llm.chat(cfg, "s", "u", require_complete=True)
        except llm.LlmError as error:
            assert "中断" in str(error)
        else:
            raise AssertionError("没有结束标记的流式响应不能视为成功")
        assert post.call_count == 1
    with patch.object(llm.requests, "post", return_value=Response([
        {"error": {"message": "upstream temporary failure"}},
    ])):
        try:
            llm.chat(cfg, "s", "u")
        except llm.LlmError as error:
            assert "upstream temporary failure" in str(error)
        else:
            raise AssertionError("OpenAI 流中的 error 事件不能被忽略")


@case("句解关闭 DeepSeek 思考、流式进度、耗时与缓存")
def test_analysis_latency():
    result = {"vocab": [], "grammarDiagram": "A shop opens.\n主语 谓语",
              "grammarPoints": [{"title": "一般现在时", "text": "描述日常活动。"}],
              "context": "介绍小店的日常。", "translation": "一家小店开门了。"}
    class Response:
        status_code = 200
        headers = {"Content-Type": "text/event-stream"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __init__(self, provider): self.provider = provider
        def iter_lines(self, **kwargs):
            raw = J.dumps(result, ensure_ascii=False)
            for text in (raw[:35], raw[35:]):
                frame = ({"type": "content_block_delta", "delta": {"text": text}}
                         if self.provider == "anthropic" else {"choices": [{"delta": {"content": text}}]})
                yield ("data: " + J.dumps(frame, ensure_ascii=False)).encode("utf8")
            end = ({"type": "message_stop"} if self.provider == "anthropic"
                   else {"choices": [{"delta": {}, "finish_reason": "stop"}]})
            yield ("data: " + J.dumps(end)).encode("utf8")
    for provider, model in [("anthropic", "deepseek-v4-flash"), ("openai", "deepseek-chat"), ("openai", "other-model")]:
        meta = dict(META, id="T_ANALYSIS_" + provider + model)
        cfg = {"name": "test", "provider": provider, "baseUrl": "http://local.test",
               "apiKey": "not-real", "model": model}
        original = dict(cfg)
        updates = []
        with patch.object(llm.requests, "post", return_value=Response(provider)) as post:
            analysis = gen.analyze(meta, ART, "A shop opens.", "", "", cfg, on_progress=updates.append)
            assert post.call_count == 1, "成功句解只能发送一次请求"
            payload = post.call_args.kwargs["json"]
            if "deepseek" in model:
                assert payload["thinking"] == {"type": "disabled"}
            else:
                assert "thinking" not in payload, "其他模型不应收到 DeepSeek 参数"
            assert cfg == original, "不能修改用户保存的模型配置"
            assert not analysis["partial"] and analysis["grammarPoints"] == result["grammarPoints"]
            stats = analysis["generationStats"]
            assert stats["calls"] == 1 and stats["outputChars"] > 0
            assert 0 <= stats["firstTextMs"] <= stats["modelMs"] <= stats["elapsedMs"]
            assert "apiKey" not in J.dumps(stats) and "not-real" not in J.dumps(stats)
            assert updates[0]["chars"] == 0 and updates[-1]["chars"] > 0
            assert gen.analyze(meta, ART, "A shop opens.", "", "", cfg) == analysis
            assert post.call_count == 1, "已有完整缓存不能重新调用 API"
    attempts = []
    with patch.object(llm, "chat", side_effect=[llm.LlmError("temporary"), "ok"]), patch.object(gen.time, "sleep"):
        assert gen._chat_retry(cfg, "s", "u", 12000, on_attempt=attempts.append) == "ok"
    assert attempts == [1, 2], "自动重试必须报告次数"


@case("句解等待提示、接收进度与完成失败状态")
def test_analysis_progress_ui():
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_reader import ReaderFrame
    root = tk.Tk()
    root.geometry("900x620")
    posts, errors = [], []
    cfg = {"name": "test", "provider": "anthropic", "baseUrl": "http://local.test",
           "apiKey": "not-real", "model": "deepseek-v4-flash"}
    app = SimpleNamespace(settings={"readerFontSize": 13, "sheetFontSize": 11, "vocabLevel": "cet4",
                                    "llms": [cfg], "defaultLlm": "test"},
                          post=lambda *args: posts.append(args), save_settings=lambda: None,
                          open_digest=lambda *args: None)
    root.report_callback_exception = lambda *args: errors.append(args)
    try:
        reader = ReaderFrame(root, app, dict(META, id="T_ANALYSIS_UI"), 0)
        reader.pack(fill="both", expand=True)
        reader.on_content_loaded(({"articles": [{"index": 0, "title": "A shop", "paragraphs": ["A shop opens."]}]}, 0))
        root.update()
        reader._start_analysis(0, "A shop opens.")
        root.update()
        win, txt, bar = reader._sheets["A shop opens."]
        label = bar._analysis_status
        assert "等待模型响应" in label.cget("text")
        count = len(posts)
        reader._start_analysis(0, "A shop opens.")
        assert len(posts) == count, "等待中重复双击不能再发请求"
        reader.on_analysis_progress(("A shop opens.", {"chars": 120, "attempt": 2}))
        reader.after_cancel(reader._analysis_tick_after)
        reader._tick_analysis_progress()
        assert "120" in label.cget("text") and "第 2 次" in label.cget("text")
        analysis = {"vocab": [], "grammarDiagram": "A shop opens.", "grammarPoints": [],
                    "context": "示例", "translation": "小店开门了。",
                    "generationStats": {"elapsedMs": 2580, "model": cfg["model"]}}
        reader.on_analysis_done(("A shop opens.", "A shop opens.", analysis))
        assert "完成 · 2.6 秒" in label.cget("text")
        assert "小店开门了。" in txt.get("1.0", "end")
        assert "2.6 秒" in txt.get("1.0", "end")
        reader._start_analysis(0, "A shop closes.")
        reader.on_analysis_error(("A shop closes.", "示例错误"))
        failed_bar = reader._sheets["A shop closes."][2]
        assert "失败" in failed_bar._analysis_status.cget("text")
        reader._retry_analysis(cfg, "A shop closes.")
        assert len([w for w in failed_bar.winfo_children() if w is failed_bar._analysis_status]) == 1
        win.destroy()
        reader.destroy()
        root.update()
        assert not errors, errors
    finally:
        root.destroy()


@case("句解分区导航、窄窗大字与语法图解滚动")
def test_analysis_reading_layout():
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import ttk
    from types import SimpleNamespace
    from kandu.ui_analysis import make_sheet, render_analysis, render_loading, _toggle_diagram

    root = tk.Tk()
    root.withdraw()
    saves, errors = [], []
    root.report_callback_exception = lambda *args: errors.append(args)
    app = SimpleNamespace(settings={"sheetFontSize": 11}, save_settings=lambda: saves.append(True))
    sentence = "The shop became a place where people shared stories."
    analysis = {"vocab": [{"word": "ingenuity", "phonetic": "ˌɪndʒəˈnjuːəti", "sense": "巧思", "note": "词源说明"}],
                "grammarDiagram": "Main clause: " + "subject predicate complement " * 12 + "\n定语从句",
                "grammarPoints": [{"title": "定语从句", "text": "where 指代先行词 place，充当从句中的地点状语。" * 5} for _ in range(4)],
                "context": "小店成为了邻里分享故事的场所。" * 12,
                "translation": "修理铺成为人们分享故事的地方。"}
    try:
        win, txt, footer = make_sheet(root, app, "An article title " * 20)
        render_loading(txt, sentence)
        root.update()
        assert txt._analysis_nav["source"].cget("state") == "normal"
        assert txt._analysis_nav["translation"].cget("state") == "disabled"
        render_analysis(txt, analysis, sentence)
        assert not txt.tag_ranges("diagram"), "旧空格图默认折叠，避免把模型的错位标注作为成分对应显示"
        _toggle_diagram(txt)
        root.update()
        assert txt.cget("state") == "disabled"
        for content in (sentence, "ingenuity", "词源说明", analysis["translation"]):
            assert content in txt.get("1.0", "end")
        assert txt.tag_ranges("source") and txt.tag_ranges("translation")
        assert tkfont.Font(root=root, font=txt.tag_cget("source", "font")).actual("family") == "Georgia"
        assert txt.tag_cget("diagram", "wrap") == "none", "语法图解不能自动折行破坏对齐"

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        widgets = list(descendants(win))
        vertical = next(w for w in widgets if isinstance(w, ttk.Scrollbar) and str(w.cget("orient")) == "vertical")
        horizontal = next(w for w in widgets if isinstance(w, ttk.Scrollbar) and str(w.cget("orient")) == "horizontal")
        for geometry in ("560x420", "960x860"):
            win.geometry(geometry)
            root.update()
            assert vertical.winfo_ismapped()
            assert txt.winfo_width() > 400 and txt.winfo_height() > 150
            for key, btn in txt._analysis_nav.items():
                assert btn.winfo_ismapped() and btn.winfo_width() >= btn.winfo_reqwidth()
                btn.invoke()
                root.update()
                assert txt.bbox("section_" + key) is not None, f"导航未定位到 {key}"
            txt._analysis_nav["grammar"].invoke()
            root.update()
            assert horizontal.winfo_ismapped(), "过宽图解必须能横向滚动查看"
            txt.xview_moveto(.5)
            root.update()
            assert txt.xview()[0] > 0
            txt.yview_moveto(1)
            root.update()
            assert txt.yview()[1] > .999, "译文必须可以完整滚动到"
        plus = next(w for w in widgets if isinstance(w, tk.Button) and w.cget("text") == "A+")
        for _ in range(11):
            plus.invoke()
        root.update()
        assert app.settings["sheetFontSize"] == 22 and saves
        assert tkfont.Font(root=root, font=txt.tag_cget("source", "font")).actual("size") == 25
        txt._analysis_nav["translation"].invoke()
        root.update()
        assert txt.bbox("section_translation") is not None
        txt.tag_add("sel", "1.0", "1.8")
        assert txt.get("sel.first", "sel.last"), "文本必须保留原生选择和复制能力"
        assert not win.attributes("-topmost") and root.grab_current() is None
        render_analysis(txt, {"grammarDiagram": "A shop opens.", "translation": "小店开门了。"}, sentence)
        assert txt._analysis_nav["vocab"].cget("state") == "disabled"
        assert "section_vocab" not in txt.mark_names(), "重新渲染必须清除旧导航定位"
        assert "ingenuity" not in txt.get("1.0", "end")
        assert not errors, errors
    finally:
        root.destroy()


@case("语法图解缩进、中英文列对齐与字号缩放")
def test_analysis_diagram_alignment():
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_analysis import make_sheet, render_analysis, _styles, _toggle_diagram

    root = tk.Tk()
    root.withdraw()
    app = SimpleNamespace(settings={"sheetFontSize": 11}, save_settings=lambda: None)
    # 中文占两个英文列；第一行缩进和中文标签之间的空格都参与定位。
    diagram = "    [We]    [read]    [books]\n    主语    谓语      宾语\n"
    tabbed = "    [We]\t[read]\t[books]\n    主语\t谓语\t宾语\n"
    try:
        win, txt, _ = make_sheet(root, app, "Alignment regression")
        win.geometry("960x860")
        for original in (diagram, tabbed):
            render_analysis(txt, {"grammarDiagram": original}, "We read books.")
            _toggle_diagram(txt)
            root.update()
            start, end = txt.tag_ranges("diagram")
            assert txt.get(start, end) == original, "图解的原始缩进、空格与换行不能被修改"
            first_line = int(str(start).split(".")[0])
            english, chinese = original.splitlines()
            for size in range(9, 23):
                _styles(txt, size)
                txt.yview(start)
                txt.xview_moveto(0)
                root.update()
                for word, label in (("[We]", "主语"), ("[read]", "谓语"), ("[books]", "宾语")):
                    upper = txt.bbox(f"{first_line}.{english.index(word)}")
                    lower = txt.bbox(f"{first_line + 1}.{chinese.index(label)}")
                    assert upper is not None and lower is not None
                    assert abs(upper[0] - lower[0]) <= 1, f"{size} 号 {word}/{label} 错位：{upper[0]} vs {lower[0]}"
        # 缓存中双重转义的换行同样支持，且不删除首行缩进。
        render_analysis(txt, {"grammarDiagram": diagram.rstrip("\n").replace("\n", "\\n")})
        _toggle_diagram(txt)
        start, end = txt.tag_ranges("diagram")
        assert txt.get(start, end) == diagram
    finally:
        root.destroy()


@case("JSON 修补")
def test_repair():
    broken = '{"paragraphs":[{"index":1,"startWords":"He had","entries":[{"word":"patent","sense":"专利"},{"word":"sensor",'
    import json as J2
    obj = J2.loads(gen.prompts.repair_truncated_json(broken))
    assert obj["paragraphs"][0]["entries"][0]["word"] == "patent"


@case("UI 启动完整性（防编辑错位回归）")
def test_ui_startup():
    import time
    import tkinter as tk
    import main as appmod
    app = appmod.App()
    try:
        app.update()
        assert type(app.current).__name__ == "LibraryFrame", f"启动页异常: {type(app.current).__name__}"
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        btns = [c for c in descendants(app.current) if c.winfo_class() in ("Button", "TButton")]
        labels = [b.cget("text") for b in btns]
        for need in ("添加刊物", "从文件夹导入", "建立索引", "生成全部摘要", "设置"):
            assert any(need in label for label in labels), f"缺少按钮: {need}（实际 {labels}）"
        t0 = time.time()
        while time.time() - t0 < 3:
            app.update(); time.sleep(0.02)
    finally:
        app.destroy()


@case("首页书架自适应、筛选复用、封面按需加载与继续阅读")
def test_library_layout_and_navigation():
    import statistics
    import tkinter as tk
    from PIL import Image
    from types import SimpleNamespace
    from kandu.ui_library import LibraryFrame

    root = tk.Tk()
    errors, opened, resumed, saves = [], [], [], []
    root.report_callback_exception = lambda *args: errors.append(args)
    metas = [dict(META, id=f"T_HOME_{i}", fileName=f"Journal_{i:02d}_2026.11." + ("pdf" if i % 3 == 0 else "epub"),
                  ext="pdf" if i % 3 == 0 else "epub") for i in range(60)]
    metas[7]["fileName"] = "A very long journal title with many words and a long issue date 2026.11.epub"
    for meta in metas[:-2]:
        Image.new("RGB", (250, 340), "#587763").save(store.cover_file(meta["id"]))
    store.cover_file(metas[-1]["id"]).write_bytes(b"invalid cover")
    store.save_reading_progress(metas[0]["id"], 5, {"title": "The article to resume", "titleZh": "继续这篇文章",
        "paragraphIndex": 7, "paragraphOffset": 2, "paragraphStart": "Sample", "paragraphCount": 20,
        "wordCount": 900, "progress": .4, "atStart": False, "atEnd": False})
    app = SimpleNamespace(settings={"recentBooks": [metas[0]["id"], metas[1]["id"], "removed", metas[2]["id"]]},
        open_settings=lambda: None, start_indexing=lambda: None, start_batch_digest=lambda: None,
        save_settings=lambda: saves.append(True), open_digest=lambda m: opened.append(m["id"]),
        open_reader=lambda m, i: resumed.append((m["id"], i)),
        index_state={"running": False, "done": 0}, batch_state={"running": False, "done": 0})

    def pump(seconds=.25):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            time.sleep(.005)

    try:
        root.geometry("900x620")
        with patch.object(store, "load_library", return_value=metas), patch.object(store, "has_digest", return_value=False), \
             patch("kandu.ui_library.Image.open", wraps=Image.open) as images:
            frame = LibraryFrame(root, app)
            frame.pack(fill="both", expand=True)
            pump()
            assert 0 < images.call_count < len(metas) // 2, "启动时只能加载可见及相邻行封面"
            identities = {key: str(entry["frame"]) for key, entry in frame._cards.items()}
            for geometry, expected_cols in (("900x620", 4), ("1180x800", 5), ("1600x900", 8), ("900x620", 4)):
                root.geometry(geometry)
                pump()
                assert frame._cols == expected_cols, (geometry, frame._cols, root.winfo_width(), frame.canvas.winfo_width())
                assert frame.canvas.winfo_height() > 400, "顶部操作区不能挤占大部分屏幕"
                for book_id in frame._visible_ids[:expected_cols]:
                    entry = frame._cards[book_id]
                    card = entry["frame"]
                    assert card.winfo_rootx() >= frame.canvas.winfo_rootx()
                    assert card.winfo_rootx() + card.winfo_width() <= frame.canvas.winfo_rootx() + frame.canvas.winfo_width()
                    assert card.winfo_height() <= 264 and entry["cover"].winfo_width() == 126
                for widget in (frame.search_entry, frame.sort_choice, frame.index_btn, frame.digest_btn):
                    assert widget.winfo_ismapped() and widget.winfo_width() >= widget.winfo_reqwidth()
                    assert widget.winfo_rootx() + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width()
            assert len(frame._recent_cards) == 3
            recent_btn = next(w for w in frame._recent_cards[0].winfo_children() if isinstance(w, tk.Button))
            recent_btn.invoke()
            assert resumed == [(metas[0]["id"], 5)] and saves
            fallback_btn = next(w for w in frame._recent_cards[1].winfo_children() if isinstance(w, tk.Button))
            fallback_btn.invoke()
            assert opened == [metas[1]["id"]], (opened, resumed)
            frame._cards[metas[3]["id"]]["cover"].event_generate("<Button-1>")
            assert opened[-1] == metas[3]["id"], "单击封面应打开对应刊物"
            with patch.object(frame, "_show_menu") as menu:
                frame._cards[metas[1]["id"]]["title"].event_generate("<Button-3>", rootx=20, rooty=30)
                assert menu.call_args.args == (metas[1], 20, 30)
            original_images = dict(frame._photo_cache)
            timings = []
            with patch.object(store, "has_digest", side_effect=AssertionError("筛选不能重读摘要状态")), \
                 patch.object(frame, "_load_thumb", side_effect=AssertionError("已加载的卡片不能重读封面")):
                for key in ("epub", "pdf", "all") * 3:
                    started = time.perf_counter()
                    frame._select_format(key)
                    root.update()
                    timings.append(time.perf_counter() - started)
                    assert all(key == "all" or frame._cards[i]["meta"]["ext"] == key for i in frame._visible_ids)
            print(f"  60 books, filter incl. layout/paint median: {statistics.median(timings) * 1000:.1f}ms")
            assert statistics.median(timings) < .5, "筛选不能出现明显卡顿"
            assert all(frame._photo_cache[k] is image for k, image in original_images.items())
            frame.search_var.set("jOuRnAl 04 2026")
            pump(.2)
            assert frame._visible_ids == [metas[4]["id"]]
            frame.search_var.set("no such journal")
            pump(.2)
            assert not frame._visible_ids and frame.empty.winfo_ismapped()
            frame.empty_action.invoke()
            pump()
            assert len(frame._visible_ids) == len(metas)
            frame.sort_var.set("最近打开")
            frame._apply_filters()
            assert frame._visible_ids[:3] == [metas[i]["id"] for i in (0, 1, 2)]
            frame.sort_var.set("名称排序")
            frame._apply_filters()
            assert frame._visible_ids[0] == metas[7]["id"]
            assert identities == {key: str(entry["frame"]) for key, entry in frame._cards.items()}
            frame.canvas.yview_moveto(1)
            pump()
            assert frame.canvas.yview()[1] > .999
            assert frame._cards[metas[-1]["id"]]["loaded"], "损坏封面应显示占位，不重复尝试加载"
            frame.on_wheel(SimpleNamespace(delta=120))
            pump()
            assert frame.canvas.yview()[1] < .999
            frame.search_var.set("pending")
            pending = [getattr(frame, name) for name in ("_search_after", "_layout_after", "_thumb_after", "_tick_after")]
            frame.destroy()
            pump()
            callbacks = root.tk.call("after", "info")
            assert not any(job in callbacks for job in pending if job), "离开书库必须取消未完成的 UI 回调"
            assert not errors, errors
    finally:
        root.destroy()


@case("首页空书库、导入刷新与后台任务状态")
def test_library_empty_and_refresh():
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_library import LibraryFrame

    root = tk.Tk()
    root.geometry("900x620")
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    meta = dict(META, id="T_HOME_IMPORT", fileName="New_issue_2026.epub")
    app = SimpleNamespace(settings={}, open_settings=lambda: None, start_indexing=lambda: None,
        start_batch_digest=lambda: None, index_state={"running": False, "done": 0},
        batch_state={"running": False, "done": 0})
    try:
        with patch.object(store, "load_library", return_value=[]):
            frame = LibraryFrame(root, app)
        frame.pack(fill="both", expand=True)
        root.update()
        assert frame.empty.winfo_ismapped() and not frame._recent_cards
        assert "开始阅读" in frame.empty_title.cget("text")
        with patch("kandu.ui_library.filedialog.askopenfilenames", return_value=["chosen.epub"]), \
             patch.object(frame, "_import") as importing:
            frame.empty_action.invoke()
            importing.assert_called_once_with(["chosen.epub"])
        with patch.object(store, "load_library", return_value=[meta]):
            frame.on_import_done({"added": 1, "skipped": 0})
        root.update()
        assert not frame.empty.winfo_ismapped() and frame._visible_ids == [meta["id"]]
        card = frame._cards[meta["id"]]
        assert "待生成" in card["state"].cget("text")
        app.index_state.update(running=True, done=1, total=2, current="new.epub")
        frame.after_cancel(frame._tick_after)
        frame._tick_index_status()
        assert frame.index_btn.cget("state") == "disabled" and "后台索引" in frame.status.cget("text")
        app.index_state.update(running=False, done=0)
        store.save_digest({"bookId": meta["id"], "articles": []})
        app.batch_state.update(running=False, done=1, total=1, failed=0)
        frame.after_cancel(frame._tick_after)
        frame._tick_index_status()
        root.update()
        assert frame.index_btn.cget("state") == "normal" and "批量摘要完成" in frame.status.cget("text")
        assert "已就绪" in card["state"].cget("text")
        frame._purge_one(meta, digest_only=True)
        assert frame._cards[meta["id"]] is card and "待生成" in card["state"].cget("text")
        with patch("kandu.ui_library.messagebox.askyesno", return_value=True), \
             patch.object(store, "remove_book") as remove, patch.object(store, "load_library", return_value=[]):
            frame._remove_book(meta)
            remove.assert_called_once_with(meta["id"])
        root.update()
        assert not frame._cards and frame.empty.winfo_ismapped()
        assert not errors, errors
    finally:
        root.destroy()


@case("生词表弹窗滚动条、拖动和滚轮")
def test_vocab_sheet_scroll():
    import tkinter as tk
    from tkinter import ttk
    from types import SimpleNamespace
    from kandu.ui_reader import ReaderFrame, _render_vocab

    app = tk.Tk()
    app.withdraw()
    holder = tk.Frame(app)
    holder.app = SimpleNamespace(settings={"sheetFontSize": 11}, save_settings=lambda: None)
    win, txt, toolbar = ReaderFrame._make_sheet(holder, "生词表测试", "长文章")
    try:
        _render_vocab(txt, {"paragraphs": [{"index": 0, "startWords": "Long article", "entries": [
            {"word": f"word{i}", "sense": "本文释义", "inText": "Some source context", "root": "词根说明"}
            for i in range(265)
        ]}]}, None)

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        scrollbar = next(w for w in descendants(win) if isinstance(w, ttk.Scrollbar))
        for width, height in [(860, 800), (520, 400), (1200, 900)]:
            win.geometry(f"{width}x{height}")
            app.update()
            assert scrollbar.winfo_ismapped(), f"{width}x{height} 下滚动条被挤出窗口"
            assert scrollbar.winfo_width() >= scrollbar.winfo_reqwidth()
            assert scrollbar.winfo_rootx() + scrollbar.winfo_width() <= win.winfo_rootx() + win.winfo_width()
            assert scrollbar.winfo_rootx() >= txt.winfo_rootx() + txt.winfo_width()
            assert txt.yview()[1] < 1, "长生词表应超出可视区域"
            txt.tk.call(scrollbar.cget("command"), "moveto", .5)
            app.update()
            assert .45 < txt.yview()[0] < .55, "拖动滚动条应滚动文本"
            assert abs(scrollbar.get()[0] - txt.yview()[0]) < .01
            txt.yview_moveto(0)
            txt.event_generate("<MouseWheel>", delta=-120)
            app.update()
            assert txt.yview()[0] > 0, "只读生词表应能使用鼠标滚轮"
            txt.yview_moveto(1)
            app.update()
            assert txt.yview()[1] > .999, f"应能滚到最后一个词条：{txt.yview()}"
            txt.yview_moveto(0)
        before = int(txt.cget("font").split()[-1])
        plus = next(w for w in descendants(win) if isinstance(w, (tk.Button, ttk.Button)) and w.cget("text") == "A+")
        plus.invoke()
        app.update()
        assert int(txt.cget("font").split()[-1]) == before + 1
        assert scrollbar.winfo_ismapped(), "放大字体后滚动条仍应可见"
    finally:
        win.destroy()
        app.destroy()


@case("同段生词窗口复用、恢复、刷新与关闭重开")
def test_vocab_sheet_reuse():
    import tkinter as tk
    from tkinter import ttk
    from types import SimpleNamespace
    from kandu.ui_reader import ReaderFrame

    root = tk.Tk()
    root.geometry("900x620")
    app = SimpleNamespace(settings={"readerFontSize": 13, "sheetFontSize": 11,
                                    "vocabLevel": "cet4", "llms": []},
                          post=lambda *args: None, save_settings=lambda: None,
                          open_digest=lambda *args: None)
    meta = dict(META, id="T_VOCAB_REUSE")
    article = {"index": 0, "title": "Repair stories", "paragraphs": ["Old objects.", "Useful skills."]}
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    try:
        reader = ReaderFrame(root, app, meta, 0)
        reader.pack(fill="both", expand=True)
        reader.on_content_loaded(({"articles": [article]}, 0))
        reader.vocab = {"level": "cet4", "articleIndex": 0, "paragraphs": [
            {"index": 0, "entries": [{"word": f"word{i}", "sense": "示例释义"} for i in range(100)]},
            {"index": 1, "entries": [{"word": "ingenuity", "sense": "巧思"}]}]}
        root.update()
        reader._show_para_vocab(0)
        root.update()
        win, txt, bar = reader._vocab_sheets[0]
        win.geometry("520x400")
        root.update()
        txt.yview_moveto(.45)
        root.update()
        scroll_index = txt.index("@0,0")
        for _ in range(4):
            reader._show_para_vocab(0)
            root.update()
            assert reader._vocab_sheets[0][0] is win
            assert len([w for w in reader.winfo_children() if isinstance(w, tk.Toplevel)]) == 1
            assert txt.index("@0,0") == scroll_index, "重复查看应保留当前可见词条，不能跳到开头"
        assert not win.attributes("-topmost") and root.grab_current() is None and not win.transient()
        win.withdraw()
        root.update()
        reader._show_para_vocab(0)
        root.update()
        assert win.winfo_ismapped() and win.focus_lastfor() is txt, "应恢复已有窗口并聚焦词条"
        reader.vocab["paragraphs"][0]["entries"][0].update(
            sense="最新释义", senseSource="dictionary", contextPending=True)
        for _ in range(3):
            reader._show_para_vocab(0)
            root.update()
        assert "最新释义" in txt.get("1.0", "end"), "复用窗口应显示最新缓存词条"
        enrich = lambda: [w for w in bar.winfo_children() if isinstance(w, (tk.Button, ttk.Button))
                          and str(w.cget("text")).startswith("补充本文释义")]
        assert len(enrich()) == 1, "复用窗口不能重复添加补充释义按钮"
        reader.vocab["paragraphs"][0]["entries"][0].pop("senseSource")
        reader.vocab["paragraphs"][0]["entries"][0].pop("contextPending")
        reader._show_para_vocab(0)
        assert not enrich(), "补充释义完成后不能保留旧按钮"
        reader._show_para_vocab(1)
        reader._show_full_vocab()
        root.update()
        assert len(reader._vocab_sheets) == 3, "不同段落与全篇窗口应各自独立"
        full = reader._vocab_sheets[None][0]
        reader._show_full_vocab()
        assert reader._vocab_sheets[None][0] is full
        win.destroy()
        assert 0 not in reader._vocab_sheets, "关闭窗口必须清理登记"
        reader._show_para_vocab(0)
        root.update()
        replacement = reader._vocab_sheets[0][0]
        assert replacement is not win and replacement.winfo_ismapped()
        reader.destroy()
        assert not reader._vocab_sheets, "离开文章时必须关闭窗口并清理登记"
        assert not errors, errors
    finally:
        root.destroy()


@case("阅读弹窗首次与重复打开均可切回正文")
def test_reader_sheet_activation():
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_reader import ReaderFrame

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL

    def hwnd(widget):
        return user32.GetAncestor(widget.winfo_id(), 2)  # GA_ROOT：Tk 外层原生窗口

    def above(front, back):
        handle = hwnd(front)
        while handle:
            if handle == hwnd(back):
                return True
            handle = user32.GetWindow(handle, 2)  # GW_HWNDNEXT：从前往后检查堆叠
        return False

    root = tk.Tk()
    root.geometry("800x600+40+40")
    holder = tk.Frame(root)
    holder.app = SimpleNamespace(settings={"sheetFontSize": 11}, save_settings=lambda: None)
    body = tk.Text(root)
    body.pack(fill="both", expand=True)
    def pump():
        for _ in range(10):
            root.update()
            time.sleep(.02)

    try:
        pump()
        for attempt in range(3):
            body.focus_set()
            pump()
            win, txt, _ = ReaderFrame._make_sheet(holder, "段落生词", "窗口切换测试")
            win.geometry("560x440+160+110")
            try:
                pump()
                assert win.focus_lastfor() == txt, "首次映射必须将弹窗焦点交给内容区"
                assert root.grab_current() is None and not win.transient()
                assert not win.attributes("-topmost"), "阅读弹窗不能强制置顶"
                # 共享桌面上用户可能正在其他应用操作。仅在测试窗口位于前台时
                # 验证原生激活顺序；始终验证 Tk 的焦点请求，不强行抢占用户焦点。
                if user32.GetForegroundWindow() in (hwnd(root), hwnd(win)):
                    assert user32.GetForegroundWindow() == hwnd(win), f"第 {attempt + 1} 次打开后弹窗未激活"
                    assert root.focus_get() == txt
                    assert above(win, root), "打开时弹窗应显示在正文前方"
                    assert user32.SetForegroundWindow(hwnd(root)), "无法激活正文窗口"
                    pump()
                    if user32.GetForegroundWindow() == hwnd(root):
                        assert above(root, win), "切回正文后，生词窗口仍挡在正文前方"
                else:
                    print("  external app active: verified sheet focus request; native foreground check skipped")
                assert win.winfo_ismapped(), "切回正文应保留弹窗，允许再次查看"
            finally:
                win.destroy()
                pump()
    finally:
        root.destroy()


@case("文章标记独立保存、缓存清理与写入失败")
def test_article_states():
    bid = "T_STATES"
    assert store.load_article_states(bid) == {}
    store.set_article_state(bid, 2, "read")
    store.set_article_state(bid, 8, "uninterested")
    assert store.load_article_states(bid) == {"2": "read", "8": "uninterested"}
    assert store.load_article_states("T_OTHER_BOOK") == {}
    digest = {"bookId": bid, "articles": []}
    store.save_digest(digest)
    store.save_digest(digest)  # 摘要重新生成不能覆盖阅读记录
    store.purge_book_cache(bid, digest_only=True)
    store.save_digest(digest)
    store.purge_digests()
    store.purge_book_cache(bid)
    assert store.load_article_states(bid)["8"] == "uninterested"
    store.purge_all()
    assert store.load_article_states(bid)["2"] == "read"
    for index, status in [(2, "bad"), (-1, "read"), (True, "read")]:
        try:
            store.set_article_state(bid, index, status)
            raise AssertionError("无效标记应被拒绝")
        except ValueError:
            pass
    target = store._article_states_file(bid)
    original = target.read_bytes()
    with patch("os.replace", side_effect=OSError("模拟磁盘写入失败")):
        try:
            store.set_article_state(bid, 2, "unread")
            raise AssertionError("写入失败应上报")
        except OSError:
            pass
    assert target.read_bytes() == original, "保存失败不能破坏原有标记"
    assert not list(target.parent.glob("*.tmp"))
    store.set_article_state(bid, 2, "unread")
    assert store.load_article_states(bid) == {"2": "unread", "8": "uninterested"}
    store.remove_book(bid)
    assert store.load_article_states(bid) == {}


@case("阅读历史独立保存、损坏隔离、缓存清理与写入失败")
def test_reading_history_storage():
    bid = "T_READING_HISTORY"
    sample = {"title": "The Repair Shop", "titleZh": "修理铺", "paragraphIndex": 7,
              "paragraphOffset": 120, "paragraphStart": "A quiet shop", "paragraphCount": 40,
              "wordCount": 2400, "progress": .35, "atStart": False, "atEnd": False}
    assert store.load_reading_history(bid) == {}
    with patch("time.time", return_value=100):
        saved = store.save_reading_progress(bid, 2, sample)
    assert saved["lastReadAt"] == 100000 and saved["paragraphOffset"] == 120
    with patch("time.time", return_value=200):
        store.save_reading_progress(bid, 9, dict(sample, title="A Different Article"))
    original = store.load_reading_history(bid)
    assert set(original) == {"2", "9"} and original["2"]["lastReadAt"] < original["9"]["lastReadAt"]
    assert store.load_reading_history("T_OTHER_READING_BOOK") == {}
    store.set_article_state(bid, 2, "unread")
    store.save_digest({"bookId": bid, "articles": []})
    store.purge_digests()
    store.purge_book_cache(bid)
    store.purge_all()
    assert store.load_reading_history(bid) == original, "清除可重新生成的缓存不能删除阅读位置"
    assert store.load_article_states(bid) == {"2": "unread"}, "自动记录位置不能更改手动标记"
    with patch("os.replace", side_effect=OSError("disk full")):
        try:
            store.save_reading_progress(bid, 2, dict(sample, paragraphIndex=8))
        except OSError:
            pass
        else:
            raise AssertionError("写入失败必须报告")
    assert store.load_reading_history(bid) == original
    assert not list(store._reading_history_file(bid).parent.glob("*.tmp"))
    damaged = dict(original, bad=saved, **{"5": dict(saved, progress=float("nan")), "6": None})
    store._json_write(store._reading_history_file(bid), damaged)
    assert store.load_reading_history(bid) == original
    store.remove_book(bid)
    assert store.load_reading_history(bid) == {} and not store._reading_history_file(bid).exists()


@case("摘要浏览标记、筛选搜索、返回位置与布局")
def test_digest_browsing():
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_digest import DigestFrame

    meta = dict(META, id="T_DIGEST_UI", fileName="wired_2026.10.02.epub")
    titles = [
        ("You’ve Never Heard of China’s Greatest Sci-Fi Novel", "你可能从未听说过的中国科幻巨著"),
        ("The Quiet Return of the Repair Shop", "修理铺正在悄悄回归"),
        ("What Happens When We Let Nature Back In?", "当我们重新接纳自然，会发生什么？"),
    ]
    articles = [{"index": i, "titleEn": titles[i % 3][0], "titleZh": titles[i % 3][1],
                 "summaryZh": "从一部小说出发，重新认识技术、社会和想象力之间的关系。"
                              "作者探访创作者与读者，讲述作品背后的时代变迁，也提出了关于未来的思考。"}
                for i in range(18)]
    digest = {"bookId": meta["id"], "articles": articles}
    store.save_digest(digest)
    opened = []
    errors = []
    app = SimpleNamespace(settings={"llms": [{"name": "测试模型一"}, {"name": "测试模型二"}]},
                          show_library=lambda: None,
                          open_reader=lambda m, i: opened.append((m["id"], i)),
                          post=lambda *args: (_ for _ in ()).throw(AssertionError("界面测试不可调用 LLM")))
    root = tk.Tk()
    root.title("刊读 · 摘要浏览预览")
    root.geometry("1180x800+40+40")
    root.report_callback_exception = lambda *args: errors.append(args)
    frame = None
    try:
        with patch.object(gen, "digest_incomplete", return_value=False):
            frame = DigestFrame(root, app, meta)
        frame.pack(fill="both", expand=True)
        root.update()
        time.sleep(.05)
        root.update()
        assert frame._counts == {"unread": 18, "read": 0, "uninterested": 0}
        frame._cards[0]["marks"]["read"].invoke()
        frame._cards[1]["marks"]["uninterested"].invoke()
        assert opened == [], "标记按钮不能打开正文"
        assert frame._counts == {"unread": 16, "read": 1, "uninterested": 1}
        frame._cards[2]["open"].invoke()
        assert opened == [(meta["id"], 2)]
        assert frame._state(2) == "unread", "进入正文不应自动标记已读"
        frame.filter_buttons["read"].invoke()
        assert list(frame._cards) == [0]
        frame._cards[0]["marks"]["unread"].invoke()
        assert not frame._cards, "标记改变后应立即更新当前筛选结果"
        frame.filter_buttons["uninterested"].invoke()
        assert list(frame._cards) == [1]
        frame._reset_filters()
        frame.search_var.set("CHINA 小说")  # 英文不分大小写，同时搜索中文摘要
        root.update()
        time.sleep(.2)
        root.update()
        assert list(frame._cards) == list(range(0, 18, 3))
        frame.search_var.set("修理铺")
        frame._apply_search()
        assert list(frame._cards) == list(range(1, 18, 3))
        frame.search_var.set("不存在的文章")
        frame._apply_search()
        assert not frame._cards
        frame._reset_filters()
        frame._cards[0]["marks"]["read"].invoke()
        frame._fill(digest)
        assert frame._state(0) == "read", "刷新摘要不能丢失标记"
        before = dict(frame._states)
        with patch.object(store, "set_article_state", side_effect=OSError("只读目录")):
            frame._mark(0, "unread")
        assert frame._states == before
        assert "标记保存失败" in frame.status.cget("text")
        frame.status.config(text="摘要已缓存 · 阅读标记仅保存在本机")
        for geometry in ("900x620", "1180x800", "1500x900"):
            root.geometry(geometry)
            root.update()
            assert frame.vbar.winfo_ismapped()
            assert frame.vbar.winfo_width() >= frame.vbar.winfo_reqwidth()
            assert frame.vbar.winfo_rootx() >= frame.canvas.winfo_rootx() + frame.canvas.winfo_width()
            assert frame.vbar.winfo_rootx() + frame.vbar.winfo_width() <= root.winfo_rootx() + root.winfo_width()
            for index in frame._mapped_cards:
                card = frame._cards[index]
                if not card["frame"].winfo_ismapped():
                    continue
                left = card["frame"].winfo_rootx()
                right = left + card["frame"].winfo_width()
                assert left >= frame.canvas.winfo_rootx()
                assert right <= frame.canvas.winfo_rootx() + frame.canvas.winfo_width()
                assert card["open"].winfo_width() >= card["open"].winfo_reqwidth()
                assert card["marks"]["uninterested"].winfo_rootx() + card["marks"]["uninterested"].winfo_width() < card["open"].winfo_rootx()
                for label in card["labels"]:
                    assert label.winfo_reqwidth() <= card["frame"].winfo_width()
            assert frame.search_entry.winfo_width() >= frame.search_entry.winfo_reqwidth()
        frame.canvas.tk.call(frame.vbar.cget("command"), "moveto", .4)
        root.update()
        assert .38 < frame.canvas.yview()[0] < .42
        assert abs(frame.vbar.get()[0] - frame.canvas.yview()[0]) < .01
        frame.on_wheel(SimpleNamespace(delta=-120))
        assert frame.canvas.yview()[0] > .4
        frame.canvas.yview_moveto(1)
        assert frame.canvas.yview()[1] > .999
        frame.search_var.set("小说")
        frame._apply_search()
        frame._select_filter("unread")
        frame.canvas.yview_moveto(.4)
        previous = frame.canvas.yview()[0]
        frame.destroy()
        with patch.object(gen, "digest_incomplete", return_value=False):
            frame = DigestFrame(root, app, meta)
        frame.pack(fill="both", expand=True)
        root.update()
        time.sleep(.05)
        root.update()
        assert frame.search_var.get() == "小说" and frame._filter == "unread"
        assert abs(frame.canvas.yview()[0] - previous) < .02, f"返回目录应恢复滚动位置：{previous} -> {frame.canvas.yview()[0]}"
        assert frame._state(0) == "read" and frame._state(1) == "uninterested"
        # 可选本地预览，用样例摘要截图，不读取用户数据。
        if os.environ.get("KANDU_CAPTURE_DIGEST"):
            from pathlib import Path
            from PIL import ImageGrab
            frame._reset_filters()
            root.geometry("1180x900+40+40")
            root.update()
            root.lift()
            time.sleep(.3)
            root.update()
            target = Path(__file__).resolve().parents[1] / "dist" / "digest-preview.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(target)
        assert not errors, f"界面回调异常：{errors}"
    finally:
        root.destroy()


@case("摘要顶部紧凑布局与最近阅读入口")
def test_digest_compact_header():
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_digest import DigestFrame

    meta = dict(META, id="T_COMPACT_DIGEST")
    digest = {"bookId": meta["id"], "articles": [
        {"index": i, "titleEn": f"A story about repair {i}", "titleZh": f"修理铺的故事 {i}",
         "summaryZh": "人们重新学习修理物品，让熟悉的街区再次焕发生机。"} for i in range(12)]}
    store.save_digest(digest)
    for i in range(3):
        store.save_reading_progress(meta["id"], i, {
            "title": digest["articles"][i]["titleEn"], "titleZh": digest["articles"][i]["titleZh"],
            "paragraphIndex": i + 1, "paragraphOffset": 0, "paragraphStart": "People repair things.",
            "paragraphCount": 30, "wordCount": 1200, "progress": (i + 1) / 30,
            "atStart": False, "atEnd": False})
    opened = []
    app = SimpleNamespace(settings={"llms": []}, show_library=lambda: None,
                          open_reader=lambda book, index: opened.append(index),
                          post=lambda *args: (_ for _ in ()).throw(AssertionError("不能调用 LLM")))
    root = tk.Tk()
    try:
        with patch.object(gen, "digest_incomplete", return_value=False):
            frame = DigestFrame(root, app, meta)
        frame.pack(fill="both", expand=True)
        for geometry in ("900x620", "1180x800"):
            root.geometry(geometry)
            root.update()
            top = frame.canvas.winfo_rooty() - frame.winfo_rooty()
            assert top <= 285, f"三个最近阅读入口时顶部占用过多：{geometry}, {top}px"
            assert frame.canvas.winfo_height() > root.winfo_height() / 2
            assert len(frame.recent_buttons) == 3 and frame.recent_section.winfo_ismapped()
            for button in frame.recent_buttons.values():
                assert button.winfo_ismapped() and button.winfo_width() >= button.winfo_reqwidth()
            print(f"  {geometry}: header {top}px, article viewport {frame.canvas.winfo_height()}px")
        frame.recent_buttons[1].invoke()
        assert opened == [1]
        progress = dict(store.load_reading_history(meta["id"])["1"])
        for index in (4, 5, 999):
            store.save_reading_progress(meta["id"], index, dict(progress, title=f"Article {index}"))
        frame.recent_more_btn.invoke()
        root.update()
        assert len(frame._recent_window._recent_items) == 5, "本刊更多应显示超过三篇，且排除已不在摘要中的文章"
        assert all(item[0]["id"] == meta["id"] for item in frame._recent_window._recent_items)
    finally:
        root.destroy()


@case("摘要标记局部更新、筛选移除与性能")
def test_digest_incremental_mark():
    import statistics
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_digest import DigestFrame

    meta = dict(META, id="T_DIGEST_PERF")
    articles = [{"index": i, "titleEn": f"Article {i}: A different way to understand the world",
                 "titleZh": f"第 {i + 1} 篇：用另一种方式理解世界",
                 "summaryZh": "这篇文章探讨技术、社会和生活的变化，介绍不同观点与相关背景。" * 5}
                for i in range(80)]
    store.save_digest({"bookId": meta["id"], "articles": articles})
    app = SimpleNamespace(settings={}, show_library=lambda: None, open_reader=lambda *a: None)
    root = tk.Tk()
    root.geometry("1180x800")
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    try:
        with patch.object(gen, "digest_incomplete", return_value=False):
            frame = DigestFrame(root, app, meta)
        frame.pack(fill="both", expand=True)
        root.update()
        time.sleep(.05)
        root.update()
        frame._mark(0, "read")
        cards = dict(frame._cards)
        frame.canvas.yview_moveto(.35)
        root.update()
        scroll = frame.canvas.yview()
        bounds = {i: (c["y"], c["height"]) for i, c in cards.items()}
        local_times = []
        with patch.object(frame, "_render", side_effect=AssertionError("标记不能重建整页")), \
             patch.object(frame, "_make_card", side_effect=AssertionError("标记不能创建卡片")):
            for state in ("uninterested", "unread", "read", "uninterested", "unread"):
                start = time.perf_counter()
                frame._mark(0, state)
                root.update()
                local_times.append(time.perf_counter() - start)
                assert all(frame._cards[i] is c and c["frame"].winfo_exists() for i, c in cards.items())
                assert bounds == {i: (c["y"], c["height"]) for i, c in cards.items()}
                assert frame.canvas.yview() == scroll, "全部列表中标记不应改变滚动位置"
            assert "未读" in frame._cards[0]["badge"].cget("text")
            assert frame._counts == {"unread": 80, "read": 0, "uninterested": 0}
            with patch.object(store, "set_article_state", side_effect=AssertionError("重复标记无需写盘")):
                frame._mark(0, "unread")
        print(f"  80 articles, median mark incl. save/layout/paint: {statistics.median(local_times)*1000:.1f}ms")
        # 在未读筛选中标记：仅隐藏当前卡片，保留其余控件和附近文章位置。
        frame._select_filter("unread")
        root.update()
        cards = dict(frame._cards)
        victim, following = 35, 36
        height = frame._scroll_height
        frame.canvas.yview_moveto((cards[victim]["y"] + 24) / height)
        root.update()
        relative_y = cards[following]["y"] - frame.canvas.canvasy(0)
        with patch.object(frame, "_render", side_effect=AssertionError("筛选内标记不能重建整页")), \
             patch.object(frame, "_make_card", side_effect=AssertionError("筛选内标记不能创建卡片")):
            frame._mark(victim, "uninterested")
            root.update()
            assert victim not in frame._cards and cards[victim]["frame"].winfo_exists()
            assert frame.canvas.itemcget(cards[victim]["item"], "state") == "hidden"
            assert len(frame._visible_articles) == 79
            assert all(frame._cards[i] is c for i, c in cards.items() if i != victim)
            assert abs(cards[following]["y"] - frame.canvas.canvasy(0) - relative_y) <= 24
            assert frame._counts["uninterested"] == 1
            assert "显示 79 / 80" in frame.result_label.cget("text")
        frame._select_filter("uninterested")
        frame._mark(victim, "read")
        root.update()
        assert not frame._cards and not frame._visible_articles
        assert frame._empty.winfo_ismapped(), "最后一张卡片移除后应显示空态"
        assert frame.vbar.winfo_ismapped()
        assert not errors, errors
    finally:
        root.destroy()


@case("摘要筛选搜索复用卡片、缓存失效与切换性能")
def test_digest_filter_cache():
    import statistics
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_digest import DigestFrame

    meta = dict(META, id="T_DIGEST_FILTER_CACHE")
    articles = [{"index": i, "titleEn": f"Article {i}: Nature and the future",
                 "titleZh": f"第 {i + 1} 篇：自然与未来",
                 "summaryZh": ("科技观察" if i % 2 else "自然观察") + "：探索社会、生活与技术的变化。" * 6}
                for i in range(80)]
    digest = {"bookId": meta["id"], "articles": articles}
    store.save_digest(digest)
    for i in range(0, 80, 4):
        store.set_article_state(meta["id"], i, "read")
    for i in range(1, 80, 4):
        store.set_article_state(meta["id"], i, "uninterested")
    root = tk.Tk()
    root.geometry("1180x800")
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    app = SimpleNamespace(settings={}, show_library=lambda: None, open_reader=lambda *a: None)
    try:
        with patch.object(gen, "digest_incomplete", return_value=False):
            frame = DigestFrame(root, app, meta)
        frame.pack(fill="both", expand=True)
        root.update()
        time.sleep(.05)
        root.update()
        cache = dict(frame._card_cache)
        snapshots = {i: (c["frame"], *c["labels"], *c["marks"].values(), c["open"])
                     for i, c in cache.items()}
        times = []
        with patch.object(frame, "_make_card", side_effect=AssertionError("切换筛选/搜索不能重建卡片")):
            for state in ("unread", "all", "read", "uninterested", "all", "unread", "all"):
                start = time.perf_counter()
                frame._select_filter(state)
                root.update()
                times.append(time.perf_counter() - start)
                expected = [a["index"] for a in articles if state == "all" or frame._state(a["index"]) == state]
                assert list(frame._cards) == expected
                positions = [frame.canvas.coords(cache[i]["item"])[1] for i in expected]
                assert all(a < b for a, b in zip(positions, positions[1:])), "筛选不能打乱文章顺序"
                assert all(frame._card_cache[i] is card for i, card in cache.items())
                assert all(w.winfo_exists() for widgets in snapshots.values() for w in widgets)
                for i, card in cache.items():
                    assert frame.canvas.itemcget(card["item"], "state") == ("normal" if i in frame._mapped_cards else "hidden")
                    if card["frame"].winfo_ismapped():
                        assert i in expected
                assert len(frame._mapped_cards) < 10, "屏幕外卡片不应全部绘制"
            # 重复点击当前筛选无需任何界面重排。
            with patch.object(frame, "_render", side_effect=AssertionError("重复筛选无需重新渲染")):
                frame._select_filter("all")
            frame.search_var.set("科技观察")
            frame._apply_search()
            root.update()
            assert list(frame._cards) == list(range(1, 80, 2))
            frame._select_filter("unread")
            root.update()
            assert list(frame._cards) == list(range(3, 80, 4))
            frame.search_var.set("没有匹配结果")
            frame._apply_search()
            root.update()
            assert not frame._cards and frame._empty.winfo_ismapped()
            empty = frame._empty
            frame._select_filter("read")
            assert frame._empty is empty, "空态也应复用"
            frame._reset_filters()
            root.update()
            assert list(frame._cards) == list(range(80))
            assert not empty.winfo_ismapped()
            # 隐藏期间调整窗口大小，重新显示的卡片必须使用当前宽度。
            frame._select_filter("read")
            root.geometry("900x620")
            root.update()
            frame._select_filter("all")
            root.update()
            assert all(card["width"] == frame._content_width for card in cache.values())
            assert all(int(label.cget("wraplength")) == frame._content_width - 56
                       for card in cache.values() for label in card["labels"])
            frame._select_filter("unread")
            frame._mark(2, "read")
            root.update()
            assert 2 not in frame._cards and frame._card_cache[2] is cache[2]
            frame._select_filter("all")
            root.update()
            assert frame._cards[2] is cache[2] and "已读" in cache[2]["badge"].cget("text")
            for fraction in (0, .25, .5, .95, 1):
                frame.canvas.yview_moveto(fraction)
                root.update()
                top = frame.canvas.canvasy(0)
                bottom = top + frame.canvas.winfo_height()
                assert 0 < len(frame._mapped_cards) < 10
                for i, card in frame._cards.items():
                    if card["y"] + card["height"] > top and card["y"] < bottom:
                        assert i in frame._mapped_cards and card["frame"].winfo_ismapped(), "滚动不能出现空白文章"
            assert frame.canvas.yview()[1] > .999 and cache[79]["frame"].winfo_ismapped()
            for state in ("unread", "all", "read", "all", "uninterested", "all"):
                frame._select_filter(state)  # 连续切换，中途不等待绘制
            root.update()
            assert list(frame._cards) == list(range(80))
            assert 0 in frame._mapped_cards and frame._cards[0]["frame"].winfo_ismapped()
            frame._fill(digest)
            assert all(frame._cards[i] is c for i, c in cache.items()), "未改变的摘要不应使缓存失效"
        print(f"  80 articles, cached filter median incl. layout/paint: {statistics.median(times)*1000:.1f}ms")
        changed = [dict(a) for a in articles]
        changed[3]["summaryZh"] = "更新后的文章摘要"
        changed = changed[1:]  # 删除一篇，更新一篇
        with patch.object(frame, "_make_card", wraps=frame._make_card) as make:
            frame._fill({"bookId": meta["id"], "articles": changed})
            root.update()
            assert make.call_count == 1
        assert 0 not in frame._card_cache and not cache[0]["frame"].winfo_exists()
        assert not cache[3]["frame"].winfo_exists()
        assert frame._cards[3] is not cache[3]
        assert all(frame._cards[i] is cache[i] for i in range(1, 80) if i != 3)
        assert any(w.cget("text") == "更新后的文章摘要" for w in frame._cards[3]["labels"])
        with patch.object(frame, "_make_card", side_effect=AssertionError("文章重排序也应复用卡片")):
            frame._fill({"bookId": meta["id"], "articles": list(reversed(changed))})
            root.update()
            assert list(frame._cards) == list(range(79, 0, -1))
            positions = [frame.canvas.coords(card["item"])[1] for card in frame._cards.values()]
            assert all(a < b for a, b in zip(positions, positions[1:]))
        assert frame.vbar.winfo_ismapped() and not errors, errors
    finally:
        root.destroy()


@case("阅读页排版、字号与选区、滚动和阅读交互")
def test_reader_layout():
    import tkinter as tk
    import tkinter.font as tkfont
    from types import SimpleNamespace
    from kandu.ui_reader import ReaderFrame, READING_FAMILY

    meta = dict(META, id="T_READER_LAYOUT", fileName="wired_2026.10.02.epub")
    title = "The Quiet Return of the Repair Shop: A Different Way to Think About the Things We Own"
    paragraphs = [
        "A small repair shop opens before sunrise. Its owner keeps a notebook beside the window, "
        "recording the stories of objects that people are not ready to throw away. "
        "The work is patient and precise, and each repair offers another way to think about the future."
        for _ in range(48)
    ]
    article = {"index": 2, "title": title, "paragraphs": paragraphs}
    store.save_digest({"bookId": meta["id"], "articles": [
        {"index": 2, "titleEn": title, "titleZh": "修理铺正在悄悄回归：重新理解我们拥有的物品", "summaryZh": ""}
    ]})
    posts, saves, back, errors = [], [], [], []
    app = SimpleNamespace(settings={"readerFontSize": 13, "vocabLevel": "cet4", "llms": []},
                          save_settings=lambda: saves.append(True),
                          open_digest=lambda m: back.append(m["id"]),
                          post=lambda *args: posts.append(args))
    root = tk.Tk()
    root.geometry("1180x820+40+40")
    root.title("刊读 · 阅读界面预览")
    root.report_callback_exception = lambda *args: errors.append(args)
    try:
        frame = ReaderFrame(root, app, meta, 2)
        frame.pack(fill="both", expand=True)
        frame.on_content_loaded(({"articles": [article]}, 2))
        root.update()
        assert len(posts) == 1, "阅读页美化不应额外请求模型"
        assert frame.text.cget("state") == "disabled"
        assert tkfont.Font(root=root, font=frame.text.cget("font")).actual("family") == READING_FAMILY
        assert title in frame.text.get("1.0", "end")
        assert "修理铺正在悄悄回归" in frame.text.get("1.0", "end")
        for i in (0, 10, 47):
            ranges = frame._para_range(i)
            assert frame.text.get(*ranges) == paragraphs[i] + "\n", "段落标签必须保留原文和定位"
            assert frame._para_at(ranges[0]) == i
        for geometry in ("900x620", "1180x820", "1600x1000"):
            root.geometry(geometry)
            root.update()
            assert frame.page.winfo_width() <= 920
            left = frame.page.winfo_rootx() - root.winfo_rootx()
            right = root.winfo_rootx() + root.winfo_width() - frame.page.winfo_rootx() - frame.page.winfo_width()
            assert abs(left - right) <= 2 and min(left, right) >= 24
            assert frame.vbar.winfo_ismapped()
            assert frame.vbar.winfo_width() >= frame.vbar.winfo_reqwidth()
            assert frame.vbar.winfo_rootx() >= frame.text.winfo_rootx() + frame.text.winfo_width()
            assert frame.vbar.winfo_rootx() + frame.vbar.winfo_width() <= frame.page.winfo_rootx() + frame.page.winfo_width()
            for button in (frame.back_btn, frame.vocab_btn, frame.font_up_btn, frame.font_down_btn, frame.top_btn):
                assert button.winfo_ismapped() and button.winfo_width() >= button.winfo_reqwidth()
                assert root.winfo_rootx() <= button.winfo_rootx() < root.winfo_rootx() + root.winfo_width()
            assert frame.text.yview()[1] < 1
        # 真实滚轮和拖动必须同步更新进度；到底部显示 100%，但不改阅读标记。
        frame.text.yview_moveto(0)
        frame.text.event_generate("<MouseWheel>", delta=-120)
        root.update()
        assert frame.text.yview()[0] > 0
        frame.text.tk.call(frame.vbar.cget("command"), "moveto", .5)
        root.update()
        assert frame._progress_fraction > .45
        frame.text.yview_moveto(1)
        root.update()
        assert frame.text.yview()[1] > .999
        assert frame.progress_label.cget("text").endswith("100%")
        frame.top_btn.invoke()
        root.update()
        assert frame.text.yview()[0] == 0 and frame._progress_fraction == 0
        assert store.load_article_states(meta["id"]) == {}
        # 字号调整仅改样式：不重建文本，保留选区与所在段落。
        frame.text.yview(frame._para_range(15)[0])
        root.update()
        anchor_para = frame._para_at(frame.text.index("@0,0"))
        selected = frame._para_range(16)[0]
        frame.text.tag_add("sel", selected, f"{selected}+20c")
        selection = tuple(map(str, frame.text.tag_ranges("sel")))
        text = frame.text.get("1.0", "end")
        with patch.object(frame, "_rebuild", side_effect=AssertionError("字号或窗口变化不能重建正文")):
            frame.font_up_btn.invoke()
            root.update()
            assert app.settings["readerFontSize"] == 14 and frame.font_value.cget("text") == "14"
            assert frame.text.get("1.0", "end") == text
            assert tuple(map(str, frame.text.tag_ranges("sel"))) == selection
            assert frame._para_at(frame.text.index("@0,0")) == anchor_para
            root.geometry("900x620")
            root.update()
            assert frame.text.get("1.0", "end") == text
            frame._font(100)
            assert app.settings["readerFontSize"] == 26 and frame.font_up_btn.cget("state") == "disabled"
            frame._font(-100)
            assert app.settings["readerFontSize"] == 11 and frame.font_down_btn.cget("state") == "disabled"
            frame._font(2)
        assert saves
        root.geometry("1180x820")
        frame.text.tag_remove("sel", "1.0", "end")
        frame.text.see(frame._para_range(0)[0])
        root.update()
        index = frame.text.search("Its owner", frame._para_range(0)[0], stopindex=frame._para_range(0)[1])
        frame.text.see(index)
        root.update()
        x, y, _, _ = frame.text.bbox(index)
        with patch.object(frame, "_start_analysis") as analyze:
            frame._on_double_click(SimpleNamespace(x=x + 2, y=y + 2))
            assert analyze.call_count == 1 and analyze.call_args.args[0] == 0
            assert "Its owner" in analyze.call_args.args[1]
        frame.text.tag_add("sel", index, f"{index}+30c")
        with patch.object(frame, "_start_analysis") as analyze:
            frame._analyze_selection()
            assert analyze.call_args.args == (0, frame.text.get(index, f"{index}+30c").strip())
        # 段前生词链接与正文独立，不能污染原文的句子偏移。
        frame.vocab = {"level": "cet4", "articleIndex": 2, "titleEntries": [], "idioms": [],
                       "processedParagraphs": list(range(48)),
                       "paragraphs": [{"index": 0, "entries": [{"word": "precise", "sense": "精确的"}]}]}
        frame._rebuild(keep_scroll=False)
        root.update()
        assert frame.text.tag_ranges("pv_0")
        assert frame.text.get(*frame._para_range(0)) == paragraphs[0] + "\n"
        with patch.object(frame, "_show_full_vocab") as show:
            frame.vocab_btn.invoke()
            show.assert_called_once()
        with patch.object(frame, "_show_para_vocab") as show:
            badge = frame.text.tag_ranges("pv_0")[0]
            frame.text.see(badge)
            root.update()
            x, y, _, _ = frame.text.bbox(badge)
            frame.text.event_generate("<Motion>", x=x + 6, y=y + 6)
            frame.text.event_generate("<Button-1>", x=x + 6, y=y + 6)
            root.update()
            show.assert_called_once_with(0)
        if os.environ.get("KANDU_CAPTURE_READER"):
            from pathlib import Path
            from PIL import ImageGrab
            frame.top_btn.invoke()
            root.geometry("1180x900+40+40")
            root.update()
            root.lift()
            time.sleep(.2)
            x, y = root.winfo_rootx(), root.winfo_rooty()
            target = Path(__file__).resolve().parents[1] / "dist" / "reader-preview.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(target)
        frame.back_btn.invoke()
        assert back == [meta["id"]] and not errors, errors
    finally:
        root.destroy()


@case("最近文章入口、词数、自动保存与跨窗口段落续读")
def test_reading_resume():
    import tkinter as tk
    from types import SimpleNamespace
    import main as appmod
    from kandu.ui_reader import ReaderFrame

    meta = dict(META, id="T_READING_RESUME", fileName="Reading progress.epub")
    paragraphs = [f"Section {i}: " + "People repair useful objects and share their stories with neighbors. " * 16
                  for i in range(30)]
    article = {"index": 2, "title": "The Repair Shop", "paragraphs": paragraphs}
    digest = {"bookId": meta["id"], "articles": [
        {"index": i, "titleEn": article["title"] if i == 2 else f"Article {i}",
         "titleZh": "修理铺的故事" if i == 2 else f"文章 {i}", "summaryZh": "阅读位置会自动保存。"}
        for i in range(6)]}
    store.save_digest(digest)
    for i in range(4):
        with patch("time.time", return_value=100 + i):
            store.save_reading_progress(meta["id"], i, {
                "title": f"Article {i}", "titleZh": f"文章 {i}", "paragraphIndex": i,
                "paragraphOffset": 0, "paragraphStart": "", "paragraphCount": 30,
                "wordCount": 4830, "progress": i / 30, "atStart": i == 0, "atEnd": False})
    posts, errors = [], []
    app = SimpleNamespace(settings={"readerFontSize": 13, "sheetFontSize": 11, "vocabLevel": "cet4", "llms": []},
                          post=lambda *args: posts.append(args), save_settings=lambda: None,
                          show_library=lambda: None, current=None)
    root = tk.Tk()
    root.geometry("1180x820")
    app.container = root
    root.report_callback_exception = lambda *args: errors.append(args)

    def swap(frame):
        if app.current is not None:
            app.current.destroy()
        app.current = frame
        frame.pack(fill="both", expand=True)

    def open_reader(book, index):
        frame = ReaderFrame(root, app, book, index)
        swap(frame)
        frame.on_content_loaded(({"articles": [article]}, index))

    app._swap = swap
    app.open_reader = open_reader
    app.open_digest = lambda book: appmod.App.open_digest(app, book)

    def pump():
        root.update()
        time.sleep(.06)
        root.update()

    def top_position(frame):
        position = frame._capture_reading_position()
        return position["paragraphIndex"], position["paragraphOffset"]

    try:
        open_reader(meta, 2)
        pump()
        frame = app.current
        assert frame.text.yview()[0] == 0, "同索引不同标题的历史不能误跳转"
        assert frame.word_count_label.cget("text") == "全文 4,830 词"
        with patch.object(store, "save_reading_progress", wraps=store.save_reading_progress) as save:
            for i in range(7, 13):
                frame.text.yview(f"{frame._para_range(i)[0]}+240c")
                root.update()
            assert not save.called, "滚动时不能逐帧写文件"
            time.sleep(.75)
            root.update()
            assert save.call_count == 1, "滚动停止后应自动保存一次"
        saved = store.load_reading_history(meta["id"])["2"]
        assert saved["paragraphIndex"] == 12 and saved["paragraphOffset"] > 0
        assert .39 < saved["progress"] < .44, "长段落进度应按正文位置计算，不能受估算行高影响"
        assert frame.word_count_label.winfo_ismapped(), "滚动后总词数必须常驻可见"
        assert store.load_article_states(meta["id"]) == {}
        frame.text.yview(f"{frame._para_range(13)[0]}+180c")
        root.update()
        with patch.object(store, "save_reading_progress", side_effect=OSError("只读目录")):
            frame.save_reading_position()
        assert "阅读位置保存失败" in frame.status.cget("text")
        assert store.load_reading_history(meta["id"])["2"] == saved
        frame.text.yview(f"{frame._para_range(12)[0]}+240c")
        root.update()
        # 立即返回，不等自动保存；摘要必须读到刚离开的最新位置。
        with patch.object(gen, "digest_incomplete", return_value=False):
            frame.back_btn.invoke()
        pump()
        summary = app.current
        assert list(summary.recent_buttons) == [2, 3, 1], "最近入口应按实际阅读时间排序且最多显示 3 篇"
        assert summary.recent_section.winfo_ismapped()
        with patch.object(summary, "_make_card", side_effect=AssertionError("筛选应继续复用卡片")):
            summary._select_filter("read")
            summary.search_var.set("不存在的标题")
            summary._apply_search()
            pump()
        assert not summary._cards and summary.recent_buttons[2].winfo_ismapped(), "续读入口不受筛选与搜索影响"
        app.settings["readerFontSize"] = 17
        root.geometry("900x620")
        store.save_vocab(meta["id"], {"articleIndex": 2, "level": "cet4", "titleEntries": [], "idioms": [],
            "paragraphs": [{"index": i, "entries": [{"word": "repair", "sense": "修理"}]} for i in range(30)],
            "processedParagraphs": [-1, *range(30)]})
        summary.recent_buttons[2].invoke()
        pump()
        frame = app.current
        paragraph, offset = top_position(frame)
        assert paragraph == 12 and abs(offset - saved["paragraphOffset"]) < 100, (paragraph, offset, saved)
        assert abs(frame._progress_fraction - saved["progress"]) < .02, "字号和宽度变化不应大幅改变阅读百分比"
        assert "已回到上次阅读位置" in frame.status.cget("text")
        assert frame.word_count_label.winfo_ismapped() and "4,830" in frame.word_count_label.cget("text")
        # 正文前新增一段时，用原段落文本锚点找回位置。
        article = dict(article, paragraphs=["A newly inserted introduction.", *paragraphs])
        open_reader(meta, 2)
        pump()
        frame = app.current
        assert top_position(frame)[0] == 13
        if os.environ.get("KANDU_CAPTURE_RESUME"):
            from pathlib import Path
            from PIL import ImageGrab
            target = Path(__file__).resolve().parents[1] / "dist" / "reader-resume-preview.png"
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(target)
            with patch.object(gen, "digest_incomplete", return_value=False):
                frame.back_btn.invoke()
            pump()
            summary = app.current
            summary._reset_filters()
            root.geometry("1180x820")
            pump()
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(target.with_name("digest-recent-preview.png"))
            summary.recent_buttons[2].invoke()
            pump()
            frame = app.current
        frame.text.yview(f"{frame._para_range(18)[0]}+200c")
        root.update()
        root.destroy()  # 关闭程序时，尚未触发的自动保存也必须落盘。
        assert store.load_reading_history(meta["id"])["2"]["paragraphIndex"] == 18
        root = tk.Tk()
        root.geometry("1180x820")
        app.container = root
        root.report_callback_exception = lambda *args: errors.append(args)
        app.current = None
        open_reader(meta, 2)
        pump()
        frame = app.current
        assert top_position(frame)[0] == 18, "重启后应恢复上次阅读段落"
        frame.text.yview_moveto(1)
        root.update()
        open_reader(meta, 2)
        pump()
        frame = app.current
        assert frame.text.yview()[1] > .999 and frame.progress_label.cget("text").endswith("100%")
        frame.top_btn.invoke()
        root.update()
        open_reader(meta, 2)
        pump()
        assert app.current.text.yview()[0] == 0, "回到开头后也应更新保存位置"
        assert len(posts) == 6 + bool(os.environ.get("KANDU_CAPTURE_RESUME")) and not errors, (len(posts), errors)
    finally:
        if root.winfo_exists():
            root.destroy()


@case("生词查找、段落定位与离线查词状态")
def test_vocabulary_navigation():
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_vocabulary import make_sheet, render_vocab, render_lookup, _search, _move_hit

    root = tk.Tk()
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    app = SimpleNamespace(settings={}, save_settings=lambda: None)
    win, txt, _ = make_sheet(root, app, "本篇生词表", "A long article title " * 20)
    try:
        vocab = {"paragraphs": [
            {"index": i, "entries": [{"word": f"craft{i}", "sense": "patient 手艺人", "root": "craft 手艺"}
                                     for _ in range(5)]} for i in range(8)]}
        render_vocab(txt, vocab, None)
        root.update()
        assert len(txt._vocab_section_choice.cget("values")) == 8
        txt._vocab_section_choice.current(6)
        txt._vocab_section_choice.event_generate("<<ComboboxSelected>>")
        root.update()
        assert txt.compare(txt.index("@0,0"), "<=", "vocab_section_6")
        assert txt.yview()[0] > .5
        txt._vocab_query.set("CRAFT6")
        _move_hit(txt, 1)  # 输入后立刻回车也应按新查询定位，不使用旧结果。
        assert len(txt._vocab_hits) == 5 and txt._vocab_hit == 0
        _move_hit(txt, -1)
        assert txt._vocab_hit == 4, "查找应支持循环向前定位"
        txt._vocab_query.set("patient")
        _search(txt)
        assert not txt._vocab_hits and "未找到" in txt._vocab_status.cget("text"), "查找只匹配英文词头"
        txt._vocab_query.set("")
        render_vocab(txt, vocab, 2)
        assert len(txt._vocab_sections) == 1 and "5 个词条" in txt._vocab_status.cget("text")
        assert "craft2" in txt.get("1.0", "end") and "craft3" not in txt.get("1.0", "end")
        render_vocab(txt, {"paragraphs": []}, None)
        assert str(txt._vocab_section_choice.cget("state")) == "disabled"
        assert "尚未收录" in txt.get("1.0", "end")
        for result, expected in [(None, "尚未就绪"), ({"miss": True}, "未收录"),
                                  ({"word": "craft", "lookedUp": "crafts", "translation": "手艺", "exchange": "crafts"}, "还原原形")]:
            render_lookup(txt, "crafts", result)
            assert expected in txt.get("1.0", "end")
            assert str(txt.cget("state")) == "disabled"
        txt.tag_add("sel", "1.0", "1.5")
        assert txt.get("sel.first", "sel.last") == "craft", "词条应可选择复制"
        txt._vocab_query.set("pending")
        win.destroy()
        root.update()
        assert not errors, errors
    finally:
        root.destroy()


@case("句法成分锚定、嵌套、无效片段与新旧缓存兼容")
def test_grammar_components():
    from kandu.core_grammar import normalize_components, legacy_components
    sentence = "There’s a reason that we read books."
    components = normalize_components([
        {"text": "There's", "label": "引导词与谓语"},
        {"text": "a reason that we read books", "label": "真正主语", "children": [
            {"text": "that we read books", "label": "定语从句", "children": [
                {"text": "we", "label": "主语"}, {"text": "read", "label": "谓语"},
                {"text": "books", "label": "宾语"}, {"text": "There’s", "label": "越出父成分"}]}]},
        {"text": "invented words", "label": "臆造片段"}], sentence)
    assert len(components) == 2 and components[0]["text"] == "There’s"
    clause = components[1]["children"][0]
    assert [c["label"] for c in clause["children"]] == ["主语", "谓语", "宾语"]
    for component in [*components, clause, *clause["children"]]:
        assert sentence[component["start"]:component["end"]] == component["text"]
    assert not normalize_components([{"text": "he", "label": "主语"}], "The book.")
    repeated = normalize_components([{"text": "that", "label": "一"}, {"text": "that", "label": "二"}], "that that")
    assert [c["start"] for c in repeated] == [0, 5]
    assert not normalize_components([None, {"text": 5, "label": "错"}, {"text": "we", "label": []}], sentence)
    assert normalize_components([{"text": "read books", "label": "谓语"}], "We read\n  books.")[0]["text"] == "read\n  books"
    legacy = {"grammarDiagram": "We [read books].\n       主语  谓语 错位",
              "grammarPoints": [{"title": "谓宾结构", "text": "“read books” 是谓宾结构。"},
                                {"title": "宾语", "text": "“books” 是宾语。"},
                                {"title": "错误引文", "text": "“invented” 不在原文。"}]}
    recovered = legacy_components(legacy, "We read books.")
    assert len(recovered) == 1 and recovered[0]["text"] == "read books"
    assert recovered[0]["children"][0]["text"] == "books"
    nested = legacy_components({
        "grammarDiagram": "I believe [that we read books [that matter]].\n    标注位置有误",
        "grammarPoints": [{"title": "宾语从句", "text": "“that we read books...” 作宾语。"},
                          {"title": "定语从句", "text": "“that matter” 修饰 books。"}]},
        "I believe that we read books that matter.")
    assert nested[0]["text"] == "that we read books that matter"
    assert nested[0]["children"][0]["text"] == "that matter"
    assert not legacy_components({"grammarPoints": [{"title": "主语", "text": "“that” 是主语。"}]}, "that that")
    result = {"vocab": [], "grammarComponents": [{"text": "We", "label": "主语"},
              {"text": "read", "label": "谓语"}, {"text": "books", "label": "宾语"}],
              "grammarPoints": [], "context": "描述阅读行为。", "translation": "我们读书。"}
    meta = dict(META, id="T_STRUCTURED_GRAMMAR")
    with patch.object(gen, "_chat_retry", return_value=J.dumps(result)) as chat:
        analysis = gen.analyze(meta, ART, "We read books.", "", "", {"model": "test"})
        assert not analysis["partial"] and len(analysis["grammarComponents"]) == 3
        assert not analysis["grammarDiagram"]
        assert gen.analyze(meta, ART, "We read books.", "", "", {}) == analysis
        assert chat.call_count == 1
    result["grammarComponents"] = [{"text": "made up", "label": "无效"}]
    with patch.object(gen, "_chat_retry", return_value=J.dumps(result)):
        invalid = gen.analyze(meta, ART, "Another sentence.", "", "", {"model": "test"})
        assert invalid["partial"] and store.load_analysis(meta["id"], "Another sentence.", 0) is None
    old = {"grammarDiagram": "We read books.\n主语 谓语 宾语", "context": "原缓存", "translation": "我们读书。"}
    store.save_analysis(meta["id"], "Old cached sentence.", 0, old)
    with patch.object(gen, "_chat_retry", side_effect=AssertionError("旧缓存不应请求模型")):
        assert gen.analyze(meta, ART, "Old cached sentence.", "", "", {}) == old


@case("成分对应列、窄窗换行、大字与原图折叠")
def test_grammar_component_layout():
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_analysis import make_sheet, render_analysis, _styles, _toggle_diagram
    root = tk.Tk()
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *a: errors.append(a)
    app = SimpleNamespace(settings={"sheetFontSize": 11}, save_settings=lambda: None)
    words = "very interesting books about nature and the places where we live"
    sentence = "We read " + words + "."
    analysis = {"grammarComponents": [
        {"text": "We", "label": "主语"}, {"text": "read", "label": "谓语"},
        {"text": words, "label": "宾语", "children": [{"text": "interesting", "label": "定语"}]}]}
    try:
        win, txt, _ = make_sheet(root, app, "Grammar alignment")
        render_analysis(txt, analysis, sentence)
        for geometry in ("960x860", "560x420"):
            win.geometry(geometry)
            for size in (9, 11, 15, 22):
                _styles(txt, size)
                root.update()
                columns = []
                for tag, label, depth, _ in txt._grammar_rows:
                    start, end = txt.tag_ranges(tag)
                    source = txt.index(f"{start} +{len(label) + 1} chars")
                    txt.yview(start)
                    root.update()
                    english_box = txt.bbox(source)
                    label_box = txt.bbox(start)
                    assert english_box and label_box and english_box[0] > label_box[0]
                    columns.append((depth, english_box[0]))
                    second = txt.index(f"{source} +1 display lines display linestart")
                    if txt.compare(second, "<", txt.index(f"{end} -1 chars")):
                        continuation = txt.bbox(second)
                        assert continuation is not None and abs(continuation[0] - english_box[0]) <= 1, (geometry, size, label, continuation, english_box)
                assert len({x for depth, x in columns if depth == 0}) == 1, "所有主成分应共用同一原文列"
                assert columns[-1][1] == columns[0][1] + 18, "从句内部成分应缩进一层"
                assert txt.xview() == (0.0, 1.0), "结构化成分应换行显示，不产生横向长图"
        legacy = {"grammarDiagram": "We [read books].\n                        主语 谓语",
                  "grammarPoints": [{"title": "谓宾结构", "text": "“read books” 构成谓宾结构。"}],
                  "translation": "我们读书。"}
        render_analysis(txt, legacy, "We read books.")
        assert txt._grammar_rows and not txt.tag_ranges("diagram"), "旧缓存应优先显示有明确引文的成分对应"
        for _ in range(3):
            _toggle_diagram(txt)
            assert txt.tag_ranges("diagram") and txt._legacy_diagram_open
            assert "我们读书。" in txt.get("1.0", "end")
            _toggle_diagram(txt)
            assert not txt.tag_ranges("diagram") and not txt._legacy_diagram_open
            assert txt.get("1.0", "end").count("构成谓宾结构") == 1
        render_analysis(txt, analysis, sentence)
        assert "legacy_diagram_start" not in txt.mark_names()
        txt.tag_add("sel", "1.0", "1.8")
        assert txt.get("sel.first", "sel.last") and str(txt.cget("state")) == "disabled"
        assert not errors, errors
    finally:
        root.destroy()


@case("书库刊名分组、分类筛选、日期排序与卡片复用")
def test_library_publication_groups():
    import tkinter as tk
    from types import SimpleNamespace
    from kandu.ui_library import LibraryFrame, publication
    root = tk.Tk()
    root.geometry("900x620")
    errors = []
    root.report_callback_exception = lambda *a: errors.append(a)
    names = ["wired_2026.09.02.epub", "TheEconomist.2026.11.07.epub",
             "wired_2026.11.02.epub", "TheEconomist.2026.10.31.pdf",
             "new_yorker.2026.10.19.pdf", "Atlantic_2026.09.02.epub", "Other.epub"]
    metas = [dict(META, id=f"T_GROUP_{i}", fileName=name, ext=name.rsplit(".", 1)[1])
             for i, name in enumerate(names)]
    app = SimpleNamespace(settings={}, open_settings=lambda: None, start_indexing=lambda: None,
                          start_batch_digest=lambda: None, index_state={"running": False, "done": 0},
                          batch_state={"running": False, "done": 0})
    try:
        with patch.object(store, "load_library", return_value=metas):
            frame = LibraryFrame(root, app)
        frame.pack(fill="both", expand=True)
        root.update()
        frame._layout_cards()
        root.update()
        identities = {key: entry["frame"] for key, entry in frame._cards.items()}
        assert len(frame._groups) == 5 and len(frame._group_headings) == 5
        assert frame._groups[0][1] == [metas[2]["id"], metas[0]["id"]], "新刊应在同刊分组内排前面"
        assert len(set(frame._visible_ids)) == len(metas)
        previous_bottom = 0
        for label, ids in frame._groups:
            heading = frame._group_headings[label]
            assert heading.winfo_y() >= previous_bottom
            assert frame._cards[ids[0]]["position"][1] > heading.winfo_y() + heading.winfo_height()
            previous_bottom = max(frame._cards[i]["position"][1] + 264 for i in ids)
        category = publication(metas[1])
        frame.category_var.set(category)
        frame.category_choice.event_generate("<<ComboboxSelected>>")
        root.update()
        assert frame._visible_ids == [metas[1]["id"], metas[3]["id"]]
        assert set(frame._group_headings) == {category}
        frame._select_format("pdf")
        assert frame._visible_ids == [metas[3]["id"]]
        frame.search_var.set("11.07")
        frame._apply_filters()
        root.update()
        assert not frame._groups and not frame._group_headings and frame.empty.winfo_ismapped()
        frame._reset_filters()
        root.update()
        assert len(frame._groups) == 5 and frame.category_var.get() == "全部刊物"
        assert all(entry["frame"] is identities[key] for key, entry in frame._cards.items())
        for widget in (frame.category_choice, frame.search_entry, frame.sort_choice):
            assert widget.winfo_ismapped() and widget.winfo_width() >= widget.winfo_reqwidth()
        assert frame.category_choice.winfo_rootx() + frame.category_choice.winfo_width() < frame.search_entry.master.winfo_rootx()
        frame.canvas.yview_moveto(1)
        root.update()
        assert frame.canvas.yview()[1] > .999
        assert not errors, errors
    finally:
        root.destroy()


@case("缺失封面后台补取、即时刷新、去重与索引跳过正文仍补封面")
def test_cover_repair():
    import io
    import zipfile
    import tkinter as tk
    from PIL import Image
    from pathlib import Path
    from types import SimpleNamespace
    from kandu.ui_library import LibraryFrame
    from main import App
    image = io.BytesIO()
    Image.new("RGB", (160, 220), "#548376").save(image, "JPEG")
    source = Path(SANDBOX) / "repair.epub"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfile full-path="EPUB/book.opf"/></container>')
        archive.writestr("EPUB/book.opf", '<package><metadata><meta name="cover" content="c"/></metadata>'
                         '<manifest><item id="c" href="images/cover.jpg" media-type="image/jpeg"/></manifest></package>')
        archive.writestr("EPUB/images/cover.jpg", image.getvalue())
    meta = dict(META, id="T_COVER_REPAIR", path=str(source))
    bad = dict(meta, id="T_COVER_BAD", path=str(Path(SANDBOX) / "bad.epub"))
    Path(bad["path"]).write_bytes(b"not an epub")
    posts, events, errors = [], [], []
    root = tk.Tk()
    root.geometry("900x620")
    root.report_callback_exception = lambda *a: errors.append(a)
    app = SimpleNamespace(settings={}, open_settings=lambda: None, start_indexing=lambda: None,
                          start_batch_digest=lambda: None, index_state={"running": False, "done": 0},
                          batch_state={"running": False, "done": 0}, post=lambda *args: posts.append(args),
                          emit=lambda *args: events.append(args))
    try:
        with patch.object(store, "load_library", return_value=[meta, bad]):
            frame = LibraryFrame(root, app)
        frame.pack(fill="both", expand=True)
        root.update()
        assert len(posts) == 1 and not store.has_cover(meta["id"]), "渲染不能同步提取封面"
        frame._render()
        root.update()
        assert len(posts) == 1, "重绘不能重复排队"
        worker = threading.Thread(target=posts[0][0], args=posts[0][1:])
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive() and len(events) == 1
        assert events[0] == ("covers_ready", {meta["id"]: True, bad["id"]: False})
        frame.on_covers_ready(events[0][1])
        root.update()
        assert meta["id"] in frame._photo_cache and not frame._cover_pending
        photo = frame._photo_cache[meta["id"]]
        frame._render()
        root.update()
        assert len(posts) == 1 and frame._photo_cache[meta["id"]] is photo
        frame._repair_missing_covers([bad], retry=True)
        assert len(posts) == 2, "失败封面允许手动重试"
        holder = SimpleNamespace(index_state={"running": True, "errors": [], "done": 0})
        with patch.object(store, "has_content", return_value=True), \
             patch.object(store, "ensure_cover") as cover, \
             patch.object(store, "ensure_content", side_effect=AssertionError("已有正文不能重复解析")):
            App._index_worker(holder, [meta])
        cover.assert_called_once_with(meta)
        assert holder.index_state["skipped"] == 1 and not holder.index_state["running"]
        assert not errors, errors
    finally:
        root.destroy()


@case("更多最近阅读跨刊汇总、分页、刷新复用与文章链接")
def test_recent_articles_more():
    import tkinter as tk
    from tkinter import ttk
    from types import SimpleNamespace
    from kandu.ui_recent import show_recent
    from kandu.ui_library import LibraryFrame
    root = tk.Tk()
    root.geometry("900x620")
    errors, opened = [], []
    root.report_callback_exception = lambda *a: errors.append(a)
    metas = [dict(META, id="T_RECENT_MORE_A", fileName="wired_2026.11.02.epub"),
             dict(META, id="T_RECENT_MORE_B", fileName="TheEconomist.2026.11.07.epub")]
    record = {"title": "Same title", "titleZh": "同名文章", "paragraphIndex": 7,
              "paragraphOffset": 40, "paragraphStart": "We read", "paragraphCount": 20,
              "wordCount": 1000, "progress": .35, "atStart": False, "atEnd": False}
    history = {str(i): dict(record, title=f"Article {i}", titleZh=f"文章 {i}", lastReadAt=1000000000000 + i)
               for i in range(58)}
    store._atomic_json_write(store._reading_history_file(metas[0]["id"]), history)
    store._atomic_json_write(store._reading_history_file(metas[1]["id"]),
                             {"4": dict(record, lastReadAt=1000000000080)})
    store._atomic_json_write(store._reading_history_file("T_REMOVED_BOOK"),
                             {"1": dict(record, lastReadAt=1000000000090)})
    app = SimpleNamespace(settings={"recentBooks": [metas[0]["id"]]}, open_settings=lambda: None,
                          start_indexing=lambda: None, start_batch_digest=lambda: None,
                          index_state={"running": False, "done": 0}, batch_state={"running": False, "done": 0},
                          open_reader=lambda m, i: opened.append((m["id"], i)))
    try:
        with patch.object(store, "load_library", return_value=metas):
            frame = LibraryFrame(root, app)
        frame.pack(fill="both", expand=True)
        root.update()
        frame.recent_more_btn.invoke()
        root.update()
        win = frame._recent_window
        assert len(win._recent_items) == 59 and win._recent_shown == 50
        assert [(m["id"], i) for m, i, _ in win._recent_items[:3]] == [
            (metas[1]["id"], 4), (metas[0]["id"], 57), (metas[0]["id"], 56)]
        txt = win._recent_text
        assert "第 8 / 20 段 · 35%" in txt.get("1.0", "end")
        assert "TheEconomist.2026.11.07.epub" in txt.get("1.0", "end")
        win.geometry("520x400")
        root.update()
        assert txt.winfo_height() > 180
        assert any(isinstance(w, ttk.Scrollbar) and w.winfo_ismapped() for w in txt.master.winfo_children())
        txt.yview_moveto(.5)
        root.update()
        before = txt.index("@0,0")
        win._recent_more.invoke()
        root.update()
        assert win._recent_shown == 59 and win._recent_more.cget("state") == "disabled"
        assert txt.index("@0,0") == before, "分页追加不能跳回开头"
        assert "Article 0" in txt.get("1.0", "end")
        frame.recent_more_btn.invoke()
        assert frame._recent_window is win, "重复点击更多应唤回同一个列表窗口"
        assert not win.attributes("-topmost") and root.grab_current() is None and not win.transient()
        # 原有列表窗口刷新后可收窄到本刊，也可更新新增文章的阅读时间。
        store.save_reading_progress(metas[0]["id"], 30, record)
        assert show_recent(frame, app, [metas[0]], {30, 20}) is win
        assert [i for _, i, _ in win._recent_items] == [30, 20]
        txt.yview_moveto(0)
        root.update()
        title = txt.bbox(txt.tag_ranges("recent_article_0")[0])
        assert title
        txt.event_generate("<Motion>", x=title[0] + 2, y=title[1] + 2)
        txt.event_generate("<Button-1>", x=title[0] + 2, y=title[1] + 2)
        root.update()
        assert opened == [(metas[0]["id"], 30)] and not win.winfo_exists()
        frame.recent_more_btn.invoke()
        root.update()
        replacement = frame._recent_window
        assert replacement is not win
        frame.destroy()
        root.update()
        assert not replacement.winfo_exists(), "离开首页应关闭最近阅读窗口"
        empty = show_recent(root, app, [])
        assert not empty._recent_items and "还没有" in empty._recent_text.get("1.0", "end")
        assert not errors, errors
    finally:
        root.destroy()


@case("PDF 按文章书签切分、同页边界与正文段落")
def test_pdf_articles():
    import pymupdf
    from pathlib import Path
    path = Path(SANDBOX) / "articles.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 90), "Magazine cover and contents")
        page = doc.new_page()
        page.insert_text((72, 90), "First article", fontsize=22)
        page.insert_text((72, 150), "Alpha body begins here.\nThis is a wrapped line.")
        page.insert_text((72, 210), "Alpha has a separate paragraph.")
        page.insert_text((72, 300), "Second article", fontsize=22)
        page.insert_text((72, 360), "Beta belongs only to the second article.")
        page = doc.new_page()
        page.insert_text((72, 90), "Beta continues on another page.")
        page.insert_text((72, 130), "| Next | Section menu | Main menu | Previous |", fontsize=8)
        page = doc.new_page()
        page.insert_text((72, 90), "Third article", fontsize=22)
        page.insert_text((72, 160), "Gamma belongs only to the third article.")
        toc = [[1, "Magazine section", 2], [2, "First article", 2],
               [2, "Second article", 2], [2, "Third article", 4]]
        doc.set_toc(toc)
        doc.set_toc_item(1, kind=pymupdf.LINK_GOTO, pno=2, to=pymupdf.Point(72, 60))
        doc.set_toc_item(2, kind=pymupdf.LINK_GOTO, pno=2, to=pymupdf.Point(72, 270))
        doc.save(path)
    articles = store.pdf_parse(str(path))
    assert [a["title"] for a in articles] == ["First article", "Second article", "Third article"]
    assert [a["index"] for a in articles] == [0, 1, 2]
    assert articles[0]["paragraphs"] == ["Alpha body begins here. This is a wrapped line.",
                                        "Alpha has a separate paragraph."]
    assert articles[1]["paragraphPages"] == [2, 3]
    assert articles[1]["pageStart"] == 2 and articles[1]["pageEnd"] == 3
    assert "Beta" not in " ".join(articles[0]["paragraphs"])
    assert "Gamma" not in " ".join(articles[1]["paragraphs"])
    assert not any("Section menu" in p for a in articles for p in a["paragraphs"])


@case("PDF 可点击目录、无目录说明与扫描件报错")
def test_pdf_directory_fallback():
    import pymupdf
    from pathlib import Path
    path = Path(SANDBOX) / "linked.pdf"
    with pymupdf.open() as doc:
        for _ in range(4):
            doc.new_page()
        page = doc[0]
        page.insert_text((72, 72), "Magazine Articles")
        for i in range(1, 4):
            y = 110 + i * 40
            page.insert_text((72, y), f"Article number {i}")
            rect = page.search_for(f"Article number {i}")[0]
            page.insert_link({"kind": pymupdf.LINK_GOTO, "from": rect, "page": i,
                              "to": pymupdf.Point(72, 60)})
            doc[i].insert_text((72, 90), f"Independent article body {i}.")
        doc.save(path)
    arts = store.pdf_parse(str(path))
    assert len(arts) == 3 and all(a["pdfSplitMethod"] == "linked_contents" for a in arts)
    assert [a["title"] for a in arts] == [f"Article number {i}" for i in range(1, 4)]
    plain = path.with_name("plain.pdf")
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 90), "A standalone document without a table of contents.")
        doc.save(plain)
    art = store.pdf_parse(str(plain))[0]
    assert art["pdfSplitMethod"] == "document" and "未识别文章目录" in art["title"]
    blank = path.with_name("scan.pdf")
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(blank)
    try:
        store.pdf_parse(str(blank))
    except ValueError as error:
        assert "OCR" in str(error)
    else:
        raise AssertionError("不能把扫描件伪装成有正文的文章")


@case("PDF 旧整本缓存迁移、阅读位置保留与摘要重新生成")
def test_pdf_cache_upgrade():
    import pymupdf
    from pathlib import Path
    from concurrent.futures import ThreadPoolExecutor
    path = Path(SANDBOX) / "legacy.pdf"
    with pymupdf.open() as doc:
        for i in range(3):
            doc.new_page().insert_text((72, 100), f"Unique body of article {i}. More original text to read.")
        doc.set_toc([[1, f"Article {i}", i + 1] for i in range(3)])
        doc.save(path)
    meta = dict(META, id="T_PDF_UPGRADE", ext="pdf", path=str(path), size=path.stat().st_size)
    with pymupdf.open(path) as doc:
        paragraphs = [" ".join(p.get_text().split()) for p in doc]
    folder = store._cache_dir(meta["id"])
    legacy = {"bookSize": meta["size"], "format": "pdf", "articles": [
        {"index": 0, "title": "legacy", "paragraphs": paragraphs}]}
    store._json_write(folder / "content.json", legacy)
    old_digest = {"bookId": meta["id"], "articles": [
        {"index": 0, "titleEn": "legacy", "titleZh": "整本", "summaryZh": "这是错误的整本摘要"}]}
    store.save_digest(old_digest)
    for name in ("vocab_0_cet4.json", "analysis_fake.json"):
        store._json_write(folder / name, {"old": True})
    position = {"title": "legacy", "titleZh": "旧刊物名", "paragraphIndex": 1, "paragraphOffset": 12,
                "paragraphStart": paragraphs[1][:160], "paragraphCount": 3, "wordCount": 50,
                "progress": .4, "atStart": False, "atEnd": False}
    record = store.save_reading_progress(meta["id"], 0, position)
    store.set_article_state(meta["id"], 0, "read")
    assert not store.has_content(meta) and not store.has_digest(meta["id"])
    assert store.load_digest(meta["id"]) is None and gen.digest_incomplete(meta)
    # 两个后台入口同时索引时只能解析一次，不能重复迁移或覆盖阅读位置。
    with patch.object(store, "pdf_parse", wraps=store.pdf_parse) as parse:
        with ThreadPoolExecutor(max_workers=2) as executor:
            contents = list(executor.map(store.ensure_content, [meta, meta]))
        assert parse.call_count == 1
    assert contents[0] == contents[1] and len(contents[0]["articles"]) == 3
    assert store.has_content(meta)
    history = store.load_reading_history(meta["id"])
    assert list(history) == ["1"] and history["1"]["title"] == "Article 1"
    assert history["1"]["paragraphOffset"] == 12 and history["1"]["lastReadAt"] == record["lastReadAt"]
    assert store.load_article_states(meta["id"]) == {}
    backup = folder / f"before_pdf_v{store.PDF_PARSER_VERSION}"
    assert J.loads((backup / "content.json").read_text(encoding="utf-8")) == legacy
    assert J.loads((backup / "digest.json").read_text(encoding="utf-8")) == old_digest
    assert J.loads((backup / "reading_history.json").read_text(encoding="utf-8"))["0"] == record
    assert J.loads((backup / "article_states.json").read_text(encoding="utf-8")) == {"0": "read"}
    assert (backup / "vocab_0_cet4.json").is_file() and not (folder / "vocab_0_cet4.json").exists()
    assert (backup / "analysis_fake.json").is_file() and not (folder / "analysis_fake.json").exists()
    calls = []
    def fake(cfg, system, user, max_tokens=0, on_chars=None, **kwargs):
        indexes = [int(v) for v in re.findall(r"index=(\d+)", user)]
        calls.extend(indexes)
        return J.dumps([{"index": i, "titleEn": f"Article {i}", "titleZh": f"文章{i}",
                         "summaryZh": f"独立摘要{i}"} for i in indexes], ensure_ascii=False)
    with patch.object(gen, "_chat_retry", fake):
        digest = gen.generate_digest(meta, {})
        assert calls == [0, 1, 2], "不能复用旧的 index=0 整本摘要"
        assert len(digest["articles"]) == 3 and store.has_digest(meta["id"])
        assert not gen.digest_incomplete(meta)
        calls.clear()
        gen.generate_digest(meta, {})
        assert not calls, "升级后的完整摘要继续复用缓存"
    assert store.load_reading_history(meta["id"]) == history


@case('个人生词本持久化、去重、编辑删除与并发查词')
def test_wordbook_store():
    from kandu import core_wordbook as words
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    source = dict(bookId='T_WB', articleIndex=2, paragraph=0, articleTitle='A candid view')
    hit = dict(word='candid', lemma='candid', phonetic='kændɪd', translation='adj. 坦率的', pos='adj.')
    with patch.object(cfgmod, 'app_dir', return_value=Path(SANDBOX) / 'wb_store'):
        cfgmod.app_dir().mkdir(parents=True)
        first = words.remember_lookup('Candid', hit, source)
        words.save(dict(word='candid', sense='我记录的：坦诚的', note='注意语境', root='个人词源'), original='candid')
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: words.remember_lookup('CANDID', hit, source), range(12)))
        record = words.get('Candid')
        assert record['lookups'] == 13 and len(words.list_words()) == 1
        assert record['sense'] == '我记录的：坦诚的' and record['note'] == '注意语境'
        assert record['created_at'] == first['created_at'] and record['source'] == source
        assert len(words.list_words('坦诚')) == len(words.list_words('语境')) == 1
        words.save(dict(word='honest', sense='诚实的'))
        try:
            words.save(dict(word='honest'), original='candid')
        except ValueError:
            pass
        else:
            raise AssertionError('重命名不能覆盖另一词')
        renamed = words.save(dict(word='candour'), original='candid')
        assert renamed['sense'] == record['sense'] and not words.get('candid')
        assert not renamed['aliases'], '重命名不能继续匹配旧单词'
        assert words.delete('CANDOUR') and not words.delete('candour')
        assert [r['word'] for r in words.list_words()] == ['honest']
        for bad in ('', '你好', '<script>', '../bad'):
            try:
                words.save(dict(word=bad))
            except ValueError:
                pass
            else:
                raise AssertionError('非法单词应拒绝')


@case('个人生词覆盖四级词、标题、变形、旧缓存与删除')
def test_wordbook_overlay():
    from kandu import core_wordbook as words
    from pathlib import Path
    import copy
    with patch.object(cfgmod, 'app_dir', return_value=Path(SANDBOX) / 'wb_overlay'), patch.object(core_dict, 'lookup_many', return_value={}):
        cfgmod.app_dir().mkdir(parents=True)
        words.save(dict(word='simple', sense='简单的'))  # 即便已经掌握，也须显示
        words.save(dict(word='cat', sense='猫'))
        words.save(dict(word='in fact', sense='事实上'))
        words.remember_lookup('running', dict(word='running', lemma='run', translation='跑'), {})
        article = dict(index=3, title='A simple view', paragraphs=['SIMPLE cats are running, in fact.', 'Education matters.'])
        cached = dict(level='cet4', articleIndex=3, processedParagraphs=[-1, 0, 1], titleEntries=[], idioms=[], paragraphs=[
            dict(index=0, entries=[dict(word='simple', sense='本文特定含义', inText='SIMPLE cats')])])
        before = copy.deepcopy(cached)
        shown = words.overlay(cached, article)
        assert cached == before, '个人生词叠加不能写入旧模型缓存'
        assert shown['titleEntries'][0]['word'] == 'simple'
        entries = shown['paragraphs'][0]['entries']
        assert len(entries) == 4 and {e['word'] for e in entries} == {'simple', 'cat', 'run', 'in fact'}
        assert entries[0]['sense'] == '本文特定含义' and entries[0]['inWordbook']
        assert len(shown['paragraphs']) == 1, '不能把 cat 匹配到 education'
        words.save(dict(word='cat', sense='猫科动物'), original='cat')
        assert next(e for e in words.overlay(cached, article)['paragraphs'][0]['entries'] if e['word'] == 'cat')['sense'] == '猫科动物'
        for record in words.list_words():
            words.delete(record['word'])
        assert words.overlay(cached, article) == cached, '删除后只移除个人叠加，模型释义仍保留'
        assert words.overlay(None, article)['paragraphs'] == []


@case('个人生词本查词自动入库、阅读页同步与管理UI')
def test_wordbook_ui():
    import tkinter as tk
    from types import SimpleNamespace
    from pathlib import Path
    import queue
    from kandu.ui_reader import ReaderFrame
    from kandu.ui_wordbook import show_wordbook, notify_changed
    from kandu import core_wordbook as words
    root = tk.Tk()
    root.geometry('1180x800')
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    root.settings = dict(readerFontSize=13, sheetFontSize=11, vocabLevel='cet4', llms=[])
    root.post = lambda *args: None
    root.save_settings = lambda: None
    root.q = queue.Queue()
    root.emit = lambda kind, payload=None: root.q.put((kind, payload))
    article = dict(index=0, title='A simple view', paragraphs=['A simple test starts here.', 'An honest view.'])
    meta = dict(META, id='T_WB_UI')
    try:
        with patch.object(cfgmod, 'app_dir', return_value=Path(SANDBOX) / 'wb_ui'), patch.object(core_dict, 'lookup_many', return_value={}):
            cfgmod.app_dir().mkdir(parents=True)
            reader = ReaderFrame(root, root, meta, 0)
            root.current = reader
            reader.pack(fill='both', expand=True)
            reader.on_content_loaded((dict(articles=[article]), 0))
            root.update()
            assert reader.vocab is None
            win, txt, bar = reader._make_sheet('查词', article['title'])
            hit = dict(word='simple', lemma='simple', phonetic='sɪmpl', translation='简单的', pos='adj.')
            with patch.object(core_dict, 'ensure_db'), patch.object(core_dict, 'lookup', return_value=hit):
                reader._lookup_worker('SIMPLE', 0, win, bar, txt)
            assert words.get('simple')['lookups'] == 1
            assert root.q.get()[0] == 'wordbook_changed'
            kind, payload = root.q.get()
            assert kind == 'lookup_done'
            notify_changed(root)
            reader.on_lookup_done(payload)
            reader._show_para_vocab(0)
            root.update()
            assert reader.text.tag_ranges('pv_0') and reader._entries_for(0)[0]['word'] == 'simple'
            sheet = reader._vocab_sheets[0][1]
            assert '已收录生词本' in sheet.get('1.0', 'end')
            assert reader.vocab is None and not store.load_vocab(meta['id'], 0, 'cet4')
            manager = show_wordbook(root, meta, article)
            root.update()
            assert manager is show_wordbook(root, meta, article), '生词本窗口应复用'
            manager.select_word('simple')
            manager.texts['sense'].delete('1.0', 'end')
            manager.texts['sense'].insert('1.0', '个人修改的释义')
            manager.save_entry()
            root.update()
            assert '个人修改的释义' in sheet.get('1.0', 'end')
            manager.new_entry()
            manager.fields['word'].set('honest')
            manager.texts['sense'].insert('1.0', '诚实的')
            manager.save_entry()
            root.update()
            assert reader._entries_for(1)[0]['word'] == 'honest'
            words.save(dict(word='elsewhere', sense='不在本文'))
            notify_changed(root)
            manager.only_article.set(True)
            manager.refresh()
            assert set(manager.tree.get_children()) == {'simple', 'honest'}
            manager.query.set('诚实')
            manager.refresh()
            assert manager.tree.get_children() == ('honest',)
            manager.select_word('honest')
            with patch('kandu.ui_wordbook.messagebox.askyesno', return_value=True):
                manager.delete_entry()
            root.update()
            assert not words.get('honest') and not reader._entries_for(1)
            manager.query.set('')
            manager.refresh()
            manager.select_word('simple')
            with patch('kandu.ui_wordbook.messagebox.askyesno', return_value=True):
                manager.delete_entry()
            root.update()
            assert not reader._entries_for(0) and 'simple' not in sheet.get('1.0', 'end'), (reader._entries_for(0), sheet.get('1.0', 'end'), errors)
            # 查词窗在结果回来前被关闭，仍然完成入库。
            win.destroy()
            with patch.object(core_dict, 'ensure_db'), patch.object(core_dict, 'lookup', return_value=hit):
                reader._lookup_worker('simple', 0, win, bar, txt)
            assert words.get('simple')
            assert root.q.get()[0] == 'wordbook_changed'
            reader.on_lookup_done(root.q.get()[1])
            assert not errors, errors
    finally:
        root.destroy()


@case('重点短语独立识别、旧缓存按段补充与失败重试')
def test_phrase_enrichment():
    import copy
    article = dict(index=0, title='A simple story', paragraphs=[
        'They fell for it in the end.', 'We take care of them.', 'We read a book.'])
    meta = dict(META, id='T_PHRASES')
    old = dict(level='cet4', articleIndex=0, coverageVersion=gen.VOCAB_COVERAGE_VERSION,
               processedParagraphs=[-1, 0, 1, 2], titleEntries=[], idioms=[],
               paragraphs=[dict(index=0, startWords=article['paragraphs'][0], entries=[
                   dict(word='end', sense='原有本文义', note='保留数据', inText='in the end')])])
    store.save_vocab(meta['id'], old)
    calls = []
    def fake(cfg, system, user, **kwargs):
        calls.append(user)
        assert '不受四级/六级单词表限制' in system and '本地候选' not in user
        indices = [int(i) for i in re.findall(r'### 段落 (-?\d+)', user)]
        return J.dumps(dict(paragraphs=[dict(index=i, entries=(
            [['fell for', 'phr.', '上当；信以为真', 'fall for 的过去式', False, ''],
             ['in the end', 'phr.', '最终', '', False, '']] if i == 0 else
            [['take care of', 'phr.', '照顾', '', False, '']] if i == 1 else [])) for i in indices]))
    with patch.object(llm, 'chat', fake):
        result = gen.generate_vocab(meta, article, 'cet4', dict(name='test'), phrases_only=True, only_para=0)
        assert len(calls) == 1 and '### 段落 1' not in calls[0], '段落补充不能请求全文'
        assert result['phraseProcessedParagraphs'] == [0] and result['processedParagraphs'] == [-1, 0, 1, 2]
        entries = result['paragraphs'][0]['entries']
        assert next(e for e in entries if e['word'] == 'end')['note'] == '保留数据'
        assert len([e for e in entries if gen.is_phrase(e)]) == 2
        assert all(e['inText'] in article['paragraphs'][0] for e in entries)
        assert gen.phrase_pending(result, article, 0) == 0
        gen.generate_vocab(meta, article, 'cet4', dict(name='test'), phrases_only=True, only_para=0)
        assert len(calls) == 1, '已检查的段落（包括无短语）不应重复请求'
        result = gen.generate_vocab(meta, article, 'cet4', dict(name='test'), phrases_only=True)
        assert len(calls) == 2 and '### 段落 0\n' not in calls[1]
        assert result['phraseProcessedParagraphs'] == [-1, 0, 1, 2]
        assert gen.phrase_pending(result, article) == 0
        gen.generate_vocab(meta, article, 'cet4', dict(name='test'), phrases_only=True)
        assert len(calls) == 2
    # 不存在的短语和错误返回的单词不能把段落标为已检查；有效兄弟段落应保留。
    meta = dict(meta, id='T_PHRASES_RETRY')
    store.save_vocab(meta['id'], copy.deepcopy(old))
    def bad(cfg, system, user, **kwargs):
        return J.dumps(dict(paragraphs=[dict(index=-1, entries=[]), dict(index=0, entries=[
            ['imaginary phrase', 'phr.', '不存在', '', False, '']]), dict(index=1, entries=[
            ['care', 'n.', '单词不算短语', '', False, '']]), dict(index=2, entries=[])]))
    with patch.object(llm, 'chat', bad):
        result = gen.generate_vocab(meta, article, 'cet4', dict(name='test'), phrases_only=True)
    assert result['phraseProcessedParagraphs'] == [-1, 2]
    assert result['failedPhraseChunks'] == 1 and '0,1' in result['phraseFailures']
    assert result['processedParagraphs'] == [-1, 0, 1, 2], '短语失败不能污染已完成的单词生词表'
    calls.clear()
    with patch.object(llm, 'chat', fake):
        result = gen.generate_vocab(meta, article, 'cet4', dict(name='test'), phrases_only=True)
    assert result['failedPhraseChunks'] == 0 and not result['phraseFailures']
    assert '### 段落 -1\n' not in calls[0] and '### 段落 2\n' not in calls[0]
    # 新生成的普通生词表同时检查短语，之后无需额外请求。
    meta = dict(meta, id='T_PHRASES_NEW')
    def normal(cfg, system, user, **kwargs):
        assert '必须独立检查重点短语' in system
        return J.dumps(dict(paragraphs=[dict(index=-1, entries=[]), dict(index=0, entries=[
            ['fell for', 'phr.', '上当', '', False, '']])]))
    with patch.object(llm, 'chat', normal):
        result = gen.generate_vocab(meta, dict(article, paragraphs=article['paragraphs'][:1]), 'cet4', dict(name='test'))
    assert result['phraseProcessedParagraphs'] == [-1, 0]


@case('段落词条和短语一键收藏、编辑与查词后正文位置稳定')
def test_vocab_collect_and_position():
    import tkinter as tk
    from pathlib import Path
    from kandu.ui_reader import ReaderFrame
    from kandu.ui_wordbook import notify_changed
    from kandu import core_wordbook as words
    root = tk.Tk()
    root.geometry('1120x740')
    root.settings = dict(readerFontSize=13, sheetFontSize=11, vocabLevel='cet4', llms=[])
    root.post = lambda *args: None
    root.save_settings = lambda: None
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    article = dict(index=0, title='Reading positions', paragraphs=[
        f'Paragraph {i}. ' + ('A candid writer takes care of the books and reads them in the end. ' * 18)
        for i in range(36)])
    meta = dict(META, id='T_WB_ANCHOR')
    try:
        with patch.object(cfgmod, 'app_dir', return_value=Path(SANDBOX) / 'wb_anchor'), patch.object(core_dict, 'lookup_many', return_value={}):
            cfgmod.app_dir().mkdir(parents=True)
            reader = ReaderFrame(root, root, meta, 0)
            root.current = reader
            reader.pack(fill='both', expand=True)
            reader.on_content_loaded((dict(articles=[article]), 0))
            root.update()
            reader.text.yview(f'{reader._para_range(21)[0]}+400c')
            root.update()
            before = reader._capture_reading_position()
            # 模拟右键查词成功，前面所有段落都出现同一个收藏词，新增 36 个徽标。
            words.remember_lookup('candid', dict(word='candid', translation='坦率的'), reader._word_source(21))
            notify_changed(root)
            root.update()
            after = reader._capture_reading_position()
            assert (after['paragraphIndex'], after['paragraphOffset']) == (before['paragraphIndex'], before['paragraphOffset']), (before, after)
            for _ in range(35):
                time.sleep(.04)
                root.update()
            history = store.load_reading_history(meta['id'])['0']
            assert history['paragraphIndex'] == before['paragraphIndex'] and abs(history['progress'] - before['progress']) < .001, (before, after, history, reader._capture_reading_position())
            reader.vocab = dict(articleIndex=0, level='cet4', paragraphs=[dict(index=21, entries=[
                dict(word='writer', phonetic='raɪtər', pos='n.', sense='本文的作者', root='write + -er'),
                dict(word='takes care of', pos='phr.', sense='照顾；照料', root='take care of 的第三人称形式', inText='takes care of the books')])])
            reader._rebuild()
            root.update()
            assert reader._capture_reading_position()['paragraphIndex'] == before['paragraphIndex']
            reader._show_para_vocab(21)
            root.update()
            win, txt, _ = reader._vocab_sheets[21]
            assert '重点短语' in txt.get('1.0', 'end') and '照顾；照料' in txt.get('1.0', 'end')
            # 点击真实文本链接，使用词条自身段落，不能取当前选中文字或全篇窗口索引。
            def click_collect(word):
                start = txt.search(word, '1.0', exact=True)
                assert start
                tags = [t for t in txt.tag_names() if t.startswith('collect_') and txt.compare(txt.tag_ranges(t)[0], '>', start)]
                tag = min(tags, key=lambda t: tuple(map(int, str(txt.tag_ranges(t)[0]).split('.'))))
                index = txt.tag_ranges(tag)[0]
                txt.see(index)
                root.update()
                x, y, w, h = txt.bbox(index)
                txt.event_generate('<Motion>', x=x + 2, y=y + h // 2)
                txt.event_generate('<Button-1>', x=x + 2, y=y + h // 2)
                root.update()
            click_collect('writer')
            saved = words.get('writer')
            assert saved and saved['sense'] == '本文的作者' and saved['phonetic'] == 'raɪtər'
            assert saved['source']['paragraph'] == 21 and saved['source']['articleTitle'] == article['title']
            assert '已收录生词本' in txt.get('1.0', 'end')
            click_collect('writer')
            assert root._wordbook_window.original == 'writer', '已收藏入口应打开对应词条编辑'
            root._wordbook_window.destroy()
            click_collect('takes care of')
            saved = words.get('takes care of')
            assert saved and saved['sense'] == '照顾；照料'
            words.save(dict(saved, sense='手动修改', note='个人笔记'))
            words.remember_entry(dict(word='takes care of', sense='模型释义'), {})
            assert words.get('takes care of')['sense'] == '手动修改' and words.get('takes care of')['note'] == '个人笔记'
            assert reader._capture_reading_position()['paragraphIndex'] == before['paragraphIndex']
            reader._show_full_vocab()
            full = reader._vocab_sheets[None][1]
            assert '＋ 加入个人生词本' in full.get('1.0', 'end') or '编辑收藏' in full.get('1.0', 'end')
            # 删除个人收藏后同步更新，正文当前位置仍不后退。
            words.delete('candid')
            notify_changed(root)
            root.update()
            assert reader._capture_reading_position()['paragraphIndex'] == before['paragraphIndex']
            # 段落补充按钮传递本段编号，处理中禁用，进度和结果直接更新原窗口。
            cfg = dict(name='test', baseUrl='https://example.invalid', apiKey='test', model='test')
            with patch.object(cfgmod, 'get_llm', return_value=cfg), patch.object(root, 'post') as post:
                win._phrase_button.invoke()
            assert post.call_args.args[2:] == (False, True, 21)
            assert str(win._phrase_button.cget('state')) == 'disabled' and reader._phrases_running
            reader.on_vocab_progress(dict(done=0, total=1, llm='test'))
            reader.on_vocab_chars(80)
            assert '短语解释' in txt._vocab_status.cget('text') and '80' in txt._vocab_status.cget('text')
            reader.on_vocab_done(dict(reader.vocab, phraseProcessedParagraphs=[21]))
            root.update()
            assert reader._vocab_sheets[21][0] is win and not reader.vocab_running
            assert win._phrase_button.cget('text') == '短语已检查' and str(win._phrase_button.cget('state')) == 'disabled'
            assert '重点短语' in txt.get('1.0', 'end') and '可选中复制' in txt._vocab_status.cget('text')
            assert not errors, errors
    finally:
        root.destroy()


@case("文章讨论持久化、内容隔离、缓存清理与原子写入失败")
def test_discussion_storage():
    from kandu import core_discussion as discussion
    meta = dict(META, id="T_DISCUSS_STORE")
    article = dict(index=3, title="Article A", paragraphs=["A precise explanation."])
    messages = [dict(role="user", content="为什么？", quote="A precise explanation.", paragraph=0,
                     status="complete", createdAt=1, apiKey="must-not-save"),
                dict(role="assistant", content="已经收到的内容", status="pending", createdAt=2, model="test")]
    discussion.save(meta, article, messages)
    path = discussion.history_path(meta, article)
    assert path.parent.name == "discussions" and "must-not-save" not in path.read_text(encoding="utf-8")
    loaded = discussion.load(meta, article)
    assert loaded[0]["quote"] == messages[0]["quote"] and loaded[1]["status"] == "stopped"
    assert loaded[1]["content"] == "已经收到的内容" and messages[1]["status"] == "pending"
    assert discussion.load(meta, dict(article, index=9)) == loaded, "文章序号变化不能丢失会话"
    assert discussion.load(meta, dict(article, paragraphs=["Different text."])) == []
    assert discussion.load(dict(meta, id="T_OTHER_BOOK"), article) == []
    store.purge_book_cache(meta["id"])
    assert discussion.load(meta, article) == loaded, "清理生成缓存不能删除讨论"
    original = path.read_bytes()
    with patch("os.replace", side_effect=OSError("disk unavailable")):
        try:
            discussion.save(meta, article, [])
        except OSError:
            pass
        else:
            raise AssertionError("保存失败必须暴露给界面")
    assert path.read_bytes() == original
    path.write_text('{"broken":', encoding="utf-8")
    try:
        discussion.load(meta, article)
    except ValueError:
        pass
    else:
        raise AssertionError("损坏的会话不能静默当成空会话覆盖")
    assert path.read_text(encoding="utf-8") == '{"broken":'


@case("文章讨论上下文、连续追问和长文章有界截取")
def test_discussion_context():
    from kandu import core_discussion as discussion
    article = dict(index=0, title="Evidence", paragraphs=["First evidence.", "Second example."])
    history = [dict(role="user", content="第一个问题", quote="First evidence.", paragraph=0, status="complete"),
               dict(role="assistant", content="第一个回答", status="complete"),
               dict(role="user", content="失败的问题", status="complete"),
               dict(role="assistant", content="不完整回答", status="error")]
    system, messages, excerpted = discussion.build_request(META, article, history, "继续解释", "Second example.", 1)
    assert not excerpted and "First evidence." in system and "Second example." in system
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == "第一个回答" and "第 2 段" in messages[-1]["content"]
    assert "失败的问题" not in str(messages) and "不完整回答" not in str(messages)
    assert len(discussion.request_messages(history, "question", limit=10)) == 1
    paragraphs = [f"Paragraph {i}. " + "Some general background. " * 80 for i in range(100)]
    paragraphs[87] = "A rare quasar is the relevant source. " * 50
    long_article = dict(article, paragraphs=paragraphs)
    context, excerpted = discussion.article_context(long_article, "What is the quasar?", 50, limit=6000)
    assert excerpted and len(context) <= 6000 and "[第 51 段]" in context and "quasar" in context
    context, excerpted = discussion.article_context(dict(article, paragraphs=["A" * 50000]), "why", 0, limit=5000)
    assert excerpted and len(context) <= 5000 and "省略" in context
    system, _, excerpted = discussion.build_request(META, long_article, [], "why", paragraph=50)
    assert excerpted and "仅提供文章节选" in system


@case("讨论双协议多轮消息、流式正文、普通JSON与禁止隐式重发")
def test_discussion_transport():
    cfg = dict(baseUrl="http://local.test", apiKey="fake-discussion-key", model="deepseek-chat")
    messages = [dict(role="user", content="question 1"), dict(role="assistant", content="answer 1"),
                dict(role="user", content="follow-up question")]
    class Response:
        status_code = 200
        headers = {"Content-Type": "text/event-stream"}
        def __init__(self, frames=None, body=None):
            self.frames = frames or []
            self.text = J.dumps(body) if body else ""
            if body: self.headers = {"Content-Type": "application/json"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_lines(self, **kwargs):
            for frame in self.frames:
                yield ("data: " + J.dumps(frame, ensure_ascii=False)).encode("utf-8")
    for provider in ["openai", "anthropic"]:
        frames = ([dict(choices=[dict(delta=dict(content="先看原文"))]),
                   dict(choices=[dict(delta=dict(content="再解释。"), finish_reason="stop")])]
                  if provider == "openai" else [dict(type="content_block_delta", delta=dict(text="先看原文")),
                   dict(type="content_block_delta", delta=dict(text="再解释。")), dict(type="message_stop")])
        updates = []
        with patch.object(llm.requests, "post", return_value=Response(frames)) as post:
            assert llm.conversation(dict(cfg, provider=provider), "system instruction", messages, on_text=updates.append) == "先看原文再解释。"
        assert post.call_count == 1 and updates[0] == "先看原文" and updates[-1] == "先看原文再解释。"
        payload = post.call_args.kwargs["json"]
        assert payload["thinking"] == dict(type="disabled")
        assert payload["messages"] == messages if provider == "anthropic" else payload["messages"][1:] == messages
    assert "thinkingMode" not in cfg
    body = dict(choices=[dict(message=dict(content="普通 JSON 回答"), finish_reason="stop")])
    with patch.object(llm.requests, "post", return_value=Response(body=body)) as post:
        assert llm.conversation(cfg, "s", messages) == "普通 JSON 回答"
        assert post.call_count == 1
    body = dict(choices=[dict(message=dict(content="partial"), finish_reason="length")])
    with patch.object(llm.requests, "post", return_value=Response(body=body)) as post:
        updates = []
        try: llm.conversation(cfg, "s", messages, on_text=updates.append)
        except llm.LlmError as error: assert "长度" in str(error)
        else: raise AssertionError("截断回答不能标记为完整")
        assert updates[-1] == "partial" and post.call_count == 1
    partial = [dict(choices=[dict(delta=dict(content="只收到一半"))])]
    with patch.object(llm.requests, "post", return_value=Response(partial)) as post:
        try: llm.conversation(cfg, "s", messages)
        except llm.LlmError as error: assert "中断" in str(error)
        else: raise AssertionError("断流不得当成完整回答")
        assert post.call_count == 1
    with patch.object(llm.requests, "post", side_effect=llm.requests.Timeout("timeout fake-discussion-key")) as post:
        try: llm.conversation(cfg, "s", messages)
        except llm.LlmError as error: assert cfg["apiKey"] not in str(error)
        else: raise AssertionError("超时必须报错")
        assert post.call_count == 1, "讨论超时不得偷偷重发整个请求"
    assert llm._public_text("<think>private thinking") == ""
    assert llm._public_text("<think>private thinking</think>回答") == "回答"


@case("讨论取消等待与接收、停止后不继续请求")
def test_discussion_cancel():
    cfg = dict(baseUrl="http://local.test", apiKey="fake", model="test")
    messages = [dict(role="user", content="q")]
    control = llm.RequestControl()
    control.cancel()
    with patch.object(llm.requests, "post") as post:
        try: llm.conversation(cfg, "s", messages, control=control)
        except llm.RequestCancelled: pass
        else: raise AssertionError("已取消不能发送请求")
        post.assert_not_called()
    entered, released, closed = threading.Event(), threading.Event(), threading.Event()
    class Response:
        status_code = 200
        headers = {"Content-Type": "text/event-stream"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def close(self):
            closed.set()
            released.set()
        def iter_lines(self, **kwargs):
            entered.set()
            released.wait(3)
            yield b'data: {"choices":[{"delta":{"content":"late"},"finish_reason":"stop"}]}'
    errors, updates = [], []
    control = llm.RequestControl()
    def worker():
        try: llm.conversation(cfg, "s", messages, control=control, on_text=updates.append)
        except Exception as error: errors.append(error)
    with patch.object(llm.requests, "post", return_value=Response()) as post:
        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(2)
        control.cancel()
        thread.join(2)
        assert not thread.is_alive() and closed.is_set() and not updates
        assert isinstance(errors[0], llm.RequestCancelled) and post.call_count == 1


@case("讨论窗口连续追问、去重、停止重试、保存恢复与关闭保护")
def test_discussion_ui():
    import tkinter as tk
    from kandu import core_discussion as discussion
    from kandu.ui_discussion import show_discussion
    root = tk.Tk()
    root.withdraw()
    root.settings = dict(llms=[dict(name="test", baseUrl="http://local.test", apiKey="fake", model="test")], defaultLlm="test")
    article = dict(index=0, title="Discussion UI", paragraphs=["A candid explanation."])
    meta = dict(META, id="T_DISCUSS_UI")
    callbacks = []
    root.report_callback_exception = lambda *args: callbacks.append(args)
    def pump(condition, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.update()
            if condition(): return
            time.sleep(.02)
        raise AssertionError("UI condition timed out")
    calls = []
    gate = threading.Event()
    def fake(cfg, system, messages, on_text, control):
        calls.append(messages)
        if len(calls) == 3:
            on_text("保留半段回答")
            gate.wait(2)
            return "过期回答不得覆盖重试"
        on_text("正在解释")
        return f"回答 {len(calls)}"
    try:
        win = show_discussion(root, meta, article, "A candid explanation.", 0)
        pump(lambda: win.loaded)
        assert show_discussion(root, meta, article) is win
        win.set_context("A candid explanation.", 0)
        with patch.object(llm, "conversation", side_effect=fake):
            win.prompt.insert("1.0", "解释一下")
            win.send()
            win.send()
            pump(lambda: not win.busy)
            assert len(calls) == 1 and "回答 1" in win.transcript.get("1.0", "end")
            assert "<quote>" in calls[0][-1]["content"]
            win.prompt.insert("1.0", "再举例")
            win.send()
            pump(lambda: not win.busy)
            assert len(calls[1]) == 3 and calls[1][1]["content"] == "回答 1"
            win.prompt.insert("1.0", "继续")
            win.send()
            pump(lambda: win.messages[-1]["content"] == "保留半段回答")
            win.stop()
            assert not win.busy and win.messages[-1]["status"] == "stopped"
            win.send(retry=True)
            pump(lambda: not win.busy)
            assert win.messages[-1]["content"] == "回答 4" and len(win.messages) == 6
            gate.set()
            for _ in range(8): root.update(); time.sleep(.02)
            assert "过期回答" not in win.transcript.get("1.0", "end")
        win._copy()
        assert root.clipboard_get() == "回答 4"
        saved = discussion.load(meta, article)
        win.destroy()
        win = show_discussion(root, meta, article)
        pump(lambda: win.loaded)
        assert win.messages == saved and "回答 4" in win.transcript.get("1.0", "end")
        # 关闭窗口取消当前任务；晚到的结果不会访问已销毁的 Tk 控件。
        gate.clear()
        with patch.object(llm, "conversation", side_effect=lambda *a, **kw: gate.wait(1) or "late"):
            win.prompt.insert("1.0", "关闭前提问")
            win.send()
            win.destroy()
            gate.set()
            for _ in range(8): root.update(); time.sleep(.02)
        assert discussion.load(meta, article)[-1]["status"] == "stopped"
        assert not callbacks, callbacks
    finally:
        gate.set()
        root.destroy()


@case("阅读页提问入口引用选区和当前段落且保持阅读位置")
def test_discussion_reader_entry():
    import tkinter as tk
    from kandu.ui_reader import ReaderFrame
    root = tk.Tk()
    root.geometry("1180x800")
    root.settings = dict(readerFontSize=13, sheetFontSize=11, vocabLevel="cet4", llms=[])
    root.post = lambda *args: None
    root.save_settings = lambda: None
    article = dict(index=0, title="Reading discussion", paragraphs=[
        f"Paragraph {i}. " + "A simple story. " * 100 for i in range(20)])
    try:
        reader = ReaderFrame(root, root, dict(META, id="T_DISCUSS_ENTRY"), 0)
        reader.pack(fill="both", expand=True)
        reader.on_content_loaded((dict(articles=[article]), 0))
        root.update()
        reader.text.yview(reader._para_range(12)[0])
        root.update()
        before = reader._capture_reading_position()
        with patch("kandu.ui_reader.show_discussion") as show:
            reader.discussion_btn.invoke()
            assert show.call_args.args[-1] == before["paragraphIndex"]
            assert reader._capture_reading_position() == before
            start = reader._para_range(12)[0]
            reader.text.tag_add("sel", start, f"{start}+12c")
            reader._open_discussion()
            assert show.call_args.args[-2] == reader.text.get(start, f"{start}+12c").strip()
            assert show.call_args.args[-1] == 12
            reader.text.tag_remove("sel", "1.0", "end")
            reader._last_click = (5, 0)
            reader._open_discussion(from_menu=True)
            assert show.call_args.args[-1] == 5
        assert reader._capture_reading_position() == before
    finally:
        root.destroy()


@case("讨论保存失败不发送、读取损坏不覆盖与模型切换")
def test_discussion_ui_failures():
    import tkinter as tk
    from kandu import core_discussion as discussion
    from kandu.ui_discussion import show_discussion
    root = tk.Tk()
    root.withdraw()
    root.settings = dict(llms=[dict(name="first", baseUrl="http://local.test", apiKey="fake", model="test-1"),
                               dict(name="second", baseUrl="http://local.test", apiKey="fake", model="test-2")], defaultLlm="first")
    meta = dict(META, id="T_DISCUSS_UI_ERRORS")
    article = dict(index=0, title="Errors", paragraphs=["A source."])
    try:
        win = show_discussion(root, meta, article)
        deadline = time.monotonic() + 3
        while not win.loaded and time.monotonic() < deadline: root.update(); time.sleep(.02)
        assert win.loaded
        win.prompt.insert("1.0", "保留我的问题")
        with patch.object(discussion, "save", side_effect=OSError("disk failure")), patch.object(llm, "conversation") as chat:
            win.send()
            chat.assert_not_called()
        assert "保留我的问题" in win.prompt.get("1.0", "end") and not win.messages
        assert "保存失败" in win.status.cget("text")
        win.model.set("second")
        with patch.object(llm, "conversation", return_value="second answer") as chat:
            win.send()
            deadline = time.monotonic() + 3
            while win.busy and time.monotonic() < deadline: root.update(); time.sleep(.02)
            assert not win.busy and chat.call_args.args[0]["model"] == "test-2"
        for geometry in ("620x540", "920x790"):
            win.geometry(geometry)
            for _ in range(8): root.update(); time.sleep(.02)
            for button in [win.send_btn, win.stop_btn, win.retry_btn, win.copy_btn]:
                assert button.winfo_rootx() + button.winfo_width() <= win.winfo_rootx() + win.winfo_width(), (geometry, button.cget("text"), button.winfo_rootx(), button.winfo_width(), win.winfo_rootx(), win.winfo_width())
                assert button.winfo_rooty() + button.winfo_height() <= win.winfo_rooty() + win.winfo_height(), geometry
            assert win.transcript.winfo_height() > 80
        win.destroy()
        path = discussion.history_path(meta, article)
        path.write_text("broken history", encoding="utf-8")
        win = show_discussion(root, meta, article)
        deadline = time.monotonic() + 3
        while "读取失败" not in win.status.cget("text") and time.monotonic() < deadline: root.update(); time.sleep(.02)
        assert not win.loaded and str(win.send_btn.cget("state")) == "disabled"
        win.destroy()
        assert path.read_text(encoding="utf-8") == "broken history"
    finally:
        root.destroy()


def main():
    only = set(sys.argv[1:])
    if os.path.exists(SANDBOX):
        shutil.rmtree(SANDBOX, ignore_errors=True)
    failed = []
    for name, fn in RESULTS:
        if only and name not in only:
            continue
        original_chat, original_retry = llm.chat, gen._chat_retry
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {e}")
            import traceback
            traceback.print_exc()
        finally:
            llm.chat, gen._chat_retry = original_chat, original_retry
    shutil.rmtree(SANDBOX, ignore_errors=True)
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
