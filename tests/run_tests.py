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
    def fake(cfg, system, user, max_tokens=0, on_delta=None):
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
    def fake(cfg, system, user, max_tokens=0, on_delta=None):
        calls.append(cfg["model"])
        idxs = [int(m) for m in re.findall(r"### 段落 (\d+)", user)]
        if 3 in idxs and cfg["model"] == "bad":
            raise llm.LlmError("sensitive information upstream_error")
        arr = [{"index": i, "startWords": f"p{i}",
                "entries": [{"word": f"w{i}", "sense": f"释{i}"}]} for i in idxs]
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
    assert "0,1,2,3,4" in v1.get("chunkFailures", {})
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


def main():
    only = set(sys.argv[1:])
    if os.path.exists(SANDBOX):
        shutil.rmtree(SANDBOX, ignore_errors=True)
    failed = []
    for name, fn in RESULTS:
        if only and name not in only:
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {e}")
            import traceback
            traceback.print_exc()
    shutil.rmtree(SANDBOX, ignore_errors=True)
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
