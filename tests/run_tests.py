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

    art = {"index": 0, "title": "A phenomenon", "paragraphs": [
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
    assert result["paragraphs"][1]["entries"][-1]["word"] == "interest rate"


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
        bar = app.current.winfo_children()[0]
        btns = [c for c in bar.winfo_children() if c.winfo_class() == "TButton"]
        labels = [b.cget("text") for b in btns]
        for need in ("添加刊物", "从文件夹导入", "建立索引"):
            assert need in labels, f"缺少按钮: {need}（实际 {labels}）"
        t0 = time.time()
        while time.time() - t0 < 3:
            app.update(); time.sleep(0.02)
    finally:
        app.destroy()


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
        plus = next(w for w in toolbar.winfo_children() if isinstance(w, ttk.Button) and w.cget("text") == "A+")
        plus.invoke()
        app.update()
        assert int(txt.cget("font").split()[-1]) == before + 1
        assert scrollbar.winfo_ismapped(), "放大字体后滚动条仍应可见"
    finally:
        win.destroy()
        app.destroy()


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
