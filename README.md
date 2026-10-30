# 刊读 · Kandu Desktop

「刊读」Android App 的 Windows 桌面版：选择期刊目录，基于大模型辅助精读
经济学人 / Wired 等英文原刊（EPUB / PDF）。

## 下载 Windows 安装包

**[下载刊读 v0.5.0（Windows 64 位，约 62 MiB）](https://github.com/haithink/magread-desktop/raw/refs/heads/main/downloads/Kandu-v0.5.0-win64.zip)**

免安装：解压后运行 `Kandu.exe`（`_internal` 依赖目录需与 exe 同级保留）。
数据目录在 `%LOCALAPPDATA%\KanduDesktop`，与解压位置无关。

## 功能

1. **书库** — 添加刊物（多选）/ 从文件夹导入，文件纳入应用自管的存储与索引，
   增删自如，不再依赖磁盘上的某个目录；支持后台建立内容索引（秒开）与进度查询
2. **摘要** — 打开刊物分批调用大模型，生成每篇文章中英文标题 + 中文摘要，缓存到本地；
   支持「批量生成全部摘要」后台任务（跳过已完成、失败可续）
3. **阅读** — 双击句子 → 句解（疑难词汇 / 语法对齐图解 / 语境 / 参考译文）；选中后右键解析；段落前「词 N」徽标查看本段生词；顶栏「生词表」看全篇；弹层字号可调
4. **LLM 配置** — 多配置管理（新增/编辑/删除/★ 默认），OpenAI 兼容 / Anthropic 原生；
   支持粘贴 cc-switch JSON 自动识别；连通性测试；获取可用模型；
   生词表失败块**自动轮换其他已配置 LLM** 补缺
5. **可靠性** — SSE 流式、推理模型额度自适应重试、截断 JSON 自动修补、分批落盘、
   生词表词条级段落锚点自愈、运行日志（error.log）

数据目录：`%LOCALAPPDATA%\KanduDesktop`（settings.json / books.json / vocab / covers / cache）

## 开发运行

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\python.exe main.py
```

注意：pymupdf 钉在 1.24.14（2.0 的 wheel 在部分机器上段错误）。

## 打包 exe

```bat
build_exe.bat
```

产物：`dist\Kandu\Kandu.exe`（整目录拷走即可用），并同步生成
`dist\Kandu-v<版本>-win64.zip` 分发包。
tcl/tk、sqlite3 等 DLL 从 anaconda（`D:\anaconda\Library`）手动打入，见脚本参数；
如 Python 安装位置不同，改脚本里的路径。构建脚本自带自检：exe 未更新或被
运行中进程锁定时会明确报错。

## 与 Android 版的关系

缓存/设置 JSON 字段与 Android 版同构（digest.json / vocab_*_level.json /
analysis_<sha1>.json），提示词、生成流程、修复策略保持一致，便于双端对照排错。
另有 Chrome 扩展版（`../magread-chrome`，网页文章精读辅助），见其 README。

## 许可证

Copyright (c) 2026 haithink and contributors.

本项目原创代码采用 [GNU Affero General Public License v3.0](LICENSE)
（SPDX：`AGPL-3.0-only`）。桌面版使用的 PyMuPDF 提供 AGPL 与商业许可，
本项目采用与其开源许可兼容的 AGPL 授权方式。

分发打包应用时，应按适用许可随附许可证并提供对应版本的完整源码和构建材料。
词典、词表和其他依赖保留各自的许可，详见 [第三方资源说明](THIRD_PARTY_NOTICES.md)。
