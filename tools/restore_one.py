# -*- coding: utf-8 -*-
"""恢复指定一本书的摘要（命令行参数传文件名关键字）"""
import sys
import time

sys.path.insert(0, '.')

from kandu import core_config as cfgmod
from kandu import core_parsers as store
from kandu import core_gen as gen

KEY = sys.argv[1] if len(sys.argv) > 1 else "TheEconomist.2026.10.10"

settings = cfgmod.load_settings()
cfg = cfgmod.get_llm(settings)
metas = store.scan(settings.get("libraryDir") or "")
meta = next((m for m in metas if KEY in m["fileName"]), None)
if meta is None:
    print(f"找不到匹配 {KEY} 的书目", flush=True)
    sys.exit(1)

print(f"目标：{meta['fileName']}", flush=True)
t0 = time.time()
d = gen.generate_digest(meta, cfg)
ok = sum(1 for a in d["articles"] if "（本篇摘要生成失败" not in a["summaryZh"])
print(f"完成：{len(d['articles'])} 篇（成功 {ok}），耗时 {round(time.time()-t0)} 秒", flush=True)
if d.get("failedBatches"):
    print(f"失败批次：{d['failedBatches']}（再运行本脚本只补缺的）", flush=True)
