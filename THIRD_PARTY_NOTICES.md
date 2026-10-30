# 第三方资源说明

本项目原创代码版权归 haithink 及贡献者所有，采用 AGPL-3.0-only。
第三方依赖和资源保留各自的版权及许可；根目录 LICENSE 不替代其上游声明。

## PyMuPDF

- 依赖：`requirements.txt` 中的 `pymupdf==1.24.14`。
- 项目：https://github.com/pymupdf/PyMuPDF/tree/1.24.14
- 上游提供 GNU AGPL 和商业许可两种授权方式；本项目使用其开源许可。
- 上游许可说明：https://github.com/pymupdf/PyMuPDF/tree/1.24.14#license-and-copyright
- 本项目采用 AGPL-3.0-only。分发包含此依赖的应用时，需要履行适用的 AGPL 义务，
  包括提供对应版本的完整源码、构建材料和许可证。

## ECDICT 精简词典

- 文件：`kandu/assets/ecdict_compact.tsv.zip`，包含 `ecdict_compact.tsv`。
- 上游项目：https://github.com/skywind3000/ECDICT
- 当前上游 MIT 许可证：https://github.com/skywind3000/ECDICT/blob/master/LICENSE
- 已随附上游许可证原文：[LICENSES/ECDICT-MIT.txt](LICENSES/ECDICT-MIT.txt)。
- 本地代码将该资源标记为 ECDICT 精简版，但未记录导出时的上游版本或提交。
  公开分发前应补齐该版本信息，并核对适用于原始数据的许可声明。

## CET-4 词表

- 文件：`kandu/assets/cet4_words.txt`。
- 内容：CET-4 单词及音标，与 Android 版、Chrome 版使用的词表相同。
- 当前仓库未记录原始来源、版本或许可证，不能据此认定该资源已获得 AGPL 授权。
  公开分发该词表前，需要核实来源和再分发条款，或替换为来源与许可明确的词表。

## 其他依赖与打包组件

`requirements.txt` 中的其他依赖，以及打包时包含的 Python、Tcl/Tk、SQLite 等组件，
按各自的上游许可证使用。分发应用时应保留所包含第三方组件要求的版权、许可证及声明。
