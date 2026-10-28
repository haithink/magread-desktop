# 刊读 · Kandu Desktop

「刊读」Android App 的 Windows 桌面版：选择期刊目录，基于大模型辅助精读
经济学人 / Wired 等英文原刊（EPUB / PDF）。

## 功能（与 APK 一致）

1. **书架**：选择目录扫描 EPUB / PDF，提取封面缩略图（EPUB 内嵌封面 / PDF 渲染首页）
2. **摘要**：打开刊物分批调用大模型，生成每篇文章中英文标题 + 中文摘要，缓存到本地
3. **阅读**：双击句子 → 句解（疑难词汇 / 语法对齐图解 / 语境 / 参考译文）；选中后右键解析；段落前「词 N」徽标查看本段生词；顶栏「生词表」看全篇
4. **LLM 配置**：OpenAI 兼容 / Anthropic 原生；支持粘贴 cc-switch JSON 自动识别；连通性测试
5. **可靠性**：SSE 流式、推理模型额度自适应重试、截断 JSON 自动修补、分批落盘、生词表词条级段落锚点自愈

数据目录：`%LOCALAPPDATA%\KanduDesktop`（settings.json / vocab / covers / cache）

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

产物：`dist\Kandu\Kandu.exe`（整目录拷走即可用）。
tcl/tk DLL 从 anaconda（`D:\anaconda\Library`）手动打入，见脚本参数；
如 Python 安装位置不同，改脚本里的路径。

## 与 Android 版的关系

缓存/设置 JSON 字段与 Android 版同构（digest.json / vocab_*_level.json /
analysis_<sha1>.json），提示词、生成流程、修复策略保持一致，便于双端对照排错。
