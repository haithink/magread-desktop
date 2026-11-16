# 刊读 Web 版 v0.1.0

在服务器集中保存书库、文章摘要、生词与学习记录，电脑、Android、iPhone 用浏览器访问同一份数据。当前先支持本机与可信局域网，域名与 Cloudflare Tunnel 尚未接入。

Web 服务和桌面程序使用同一套 Python 阅读核心，代码暂放在桌面仓库的 `webapp/`，不依赖桌面窗口或浏览器插件。桌面 exe 版本仍为 v0.8.18；Web 版有独立版本号、启动入口和数据目录。

## 本机使用

1. 双击根目录 `start_web.bat`，或桌面「刊读 Web」快捷方式。
2. 浏览器打开 `http://127.0.0.1:8766`。
3. 输入启动窗口显示的访问密码。密码也保存在 `%LOCALAPPDATA%\KanduWeb\access-code.txt`。

服务在后台运行，关闭启动窗口不会停止服务；电脑关机、休眠或断网期间其他设备无法使用。重启电脑后再次双击启动入口。首次启动随机生成访问密码和会话签名，不使用固定默认密码；浏览器保存登录状态。

## 手机及其他电脑访问

1. 设备连接同一可互通的局域网。
2. 在服务器上**右键 `enable_web_lan.bat`，选择「以管理员身份运行」**。该规则仅允许专用网络的本地子网访问 TCP 8766，不开放所有公网来源。
3. 手机浏览器输入 `http://服务器局域网IP:8766`，使用同一个访问密码登录。

服务监听 `0.0.0.0:8766`，启动日志列出候选 IPv4 地址。使用实际有线或 Wi-Fi 网卡的地址，忽略虚拟机、VPN 和代理网卡地址；地址可能随网络变化。可用 `ipconfig` 查看当前网卡地址。

本轮部署时实际网卡「以太网 2」地址为 `172.28.249.164`，对应 `http://172.28.249.164:8766`。本机调用此地址通过，不代表手机到电脑的链路已经实测。当前执行环境无管理员权限，添加防火墙规则返回 `Access is denied`，因此需由用户完成上述管理员操作；不会自动修改网络类别或路由器设置。

## 首次导入桌面数据

开发环境安装：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-web.txt
```

先选择独立的 Web 数据目录，只在第一次导入时执行：

```powershell
.venv\Scripts\python.exe run_web.py --import-desktop "$env:LOCALAPPDATA\KanduDesktop"
```

后续启动用 `start_web.bat`，或：

```powershell
.venv\Scripts\python.exe run_web.py
```

首次导入复制正文和摘要、生词、句解、封面、个人生词本、文章标记、阅读历史、讨论及服务器端模型配置；SQLite 文件使用一致性快照。原始 EPUB/PDF 仍引用原文件，不重复复制整套书库。本机原书库存放在 `I:\KanduLibrary`，这些文件需保留。Web 后续上传的刊物保存在 Web 数据目录的 `books/`。

导入操作拒绝覆盖已有 Web 目录，也拒绝与桌面目录重叠。导入前应等待桌面的生成任务完成。默认 Web 数据位置是 `%LOCALAPPDATA%\KanduWeb`，可用 `--data-dir` 或 `KANDU_WEB_DATA_DIR` 修改。

这是**一次性快照导入**，不是桌面与 Web 双向同步。之后各浏览器通过服务器共享数据，桌面和原生 Android 的后续编辑不会自动进入 Web；也不要让桌面和服务器同时直接写同一套 JSON。多位使用者目前共用一个个人书库，不提供独立用户资料。

## 首版功能

- 按刊物分类展示封面和书库，刊物搜索、最近阅读及继续阅读。
- 文章目录展示已有中英文标题与摘要，支持搜索、全部/未读/已读/不感兴趣筛选及文章标记；标记后复用卡片。
- 响应式正文阅读、全文词数、字号、本文搜索、进度自动保存及恢复上次段落；PDF 可打开原文件，EPUB 可下载。
- 全文及逐段生词窗口，区分个人收藏、未生成和缺段状态；生成、补缺和短语解释复用桌面核心及服务器模型配置。
- 选中单词或短语后查本地词典并自动收藏；生词与短语可加入个人生词本，支持查询、编辑和删除。
- 选中文本后句子讲解；电脑端双击正文可按句提取，语法成分按对应原文片段成对展示。
- 每篇文章保存 AI 多轮讨论，可引用原文，回答显示字数和进度，可停止并保留已收到内容。
- 浏览器上传 EPUB/PDF（应用上限 150 MB），后台解析索引和封面；摘要按需生成。
- 设置页可选择默认模型、粘贴完整配置 JSON、修改词汇级别。模型密钥只留在服务器；设置接口不回传密钥。

Web 后台最多两个任务同时运行，队列有界，同一本刊物生成任务串行；重复发起相同任务复用任务 ID。页面每约 650 毫秒读取任务进度，模型请求仍为核心的流式接收。打开文章不自动产生付费 LLM 请求。服务关闭后正在运行的任务会中断，已经落盘的分块结果保留，可再次补缺；未落盘的任务队列不跨重启恢复。

## 将来接入 `138272.xyz`

这一轮没有改动域名或 Cloudflare 设置。局域网确认可用后，可将例如 `read.138272.xyz` 的固定隧道映射到 `http://127.0.0.1:8766`。公网运行时服务建议仅监听回环地址，显式允许实际域名并启用 HTTPS 会话 cookie：

```powershell
.venv\Scripts\python.exe run_web.py --host 127.0.0.1 --allowed-host read.138272.xyz --secure-cookie
```

这是后续部署用的启动参数，不会创建隧道或修改 DNS。应用不自动信任转发身份头；访问密码仍需保留，可再由 Cloudflare Access 增加入口验证。当前 LAN HTTP 版本只用于可信局域网。

## 开发与验证

服务使用 Flask API 和 Waitress，静态页面原生 JavaScript/CSS，无 Node 构建和外部 CDN。浏览器输入与模型文本均通过文本节点展示，登录、原刊下载、封面和 API 受会话保护，写操作校验 CSRF 和请求来源；不接受任意路径的文件读取。

```powershell
.venv\Scripts\python.exe tests\run_web_tests.py
# 浏览器测试另外需要 playwright；本机使用已有 chrome-win64/chrome.exe
.venv\Scripts\python.exe -m pip install -r requirements-web-test.txt
.venv\Scripts\python.exe tests\run_web_browser_tests.py
```

API 与浏览器测试使用临时目录和模拟模型，不接触真实桌面数据，也不调用收费接口。真实书库浏览仅在 Web 快照中产生测试阅读记录。尚未用实体手机或实际 Cloudflare Tunnel 验证。

备份 Web 数据时应同时保留原刊文件所在目录；仅复制 `KanduWeb` 不会带上仍引用在外部的 EPUB/PDF。不要将模型配置、访问密码、个人刊物和生成缓存提交 Git。原项目许可证继续适用，见 [LICENSE](LICENSE) 和 [第三方资源说明](THIRD_PARTY_NOTICES.md)。
