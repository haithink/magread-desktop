# -*- coding: utf-8 -*-
"""维护脚本：为缺少摘要的书目重新生成摘要（用真实数据目录与默认 LLM）。

用途：缓存丢失/损坏后的批量恢复。默认 LLM 失败的批次留占位，
可再次运行续补（智能续传只请求缺失部分）。
"""
import sys
import time

sys.path.insert(0, '.')

from kandu import core_config as cfgmod
from kandu import core_parsers as store
from kandu import core_gen as gen


def main():
    settings = cfgmod.load_settings()
    cfg = cfgmod.get_llm(settings)
    metas = store.scan(settings.get("libraryDir") or "")
    todo = [m for m in metas if gen.digest_incomplete(m)]
    print(f"{len(metas)} 本书，其中 {len(todo)} 本需要生成/补全摘要", flush=True)
    ok, failed = 0, []
    for i, m in enumerate(todo):
        print(f"[{i + 1}/{len(todo)}] {m['fileName']} …", flush=True)
        try:
            d = gen.generate_digest(m, cfg)
            fb = d.get("failedBatches") or 0
            print(f"    完成：{len(d['articles'])} 篇" + (f"（{fb} 批失败，可再跑本脚本续补）" if fb else ""), flush=True)
            ok += 1
        except Exception as e:
            print(f"    失败：{str(e)[:150]}", flush=True)
            failed.append(m["fileName"])
    print(f"\n恢复完成：成功 {ok}，失败 {len(failed)}")
    if failed:
        print("失败书目：", "、".join(failed), flush=True)


if __name__ == "__main__":
    main()
