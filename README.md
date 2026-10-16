# 赛博情报局

赛博情报局是在 Windows 本机运行的个人信息工作台。它把你关注的创作者更新集中到一个界面，用正文、字幕、语音和采样画面生成 AI 简报，帮助你减少逐个平台查看和重复阅读的时间。

适合需要持续关注 B站、知乎、小红书或抖音作者、希望保留本机阅读记录的个人用户。内容采集与语音识别在电脑上执行；使用远程 AI 服务时，分析素材会发送给你选择的服务商。

## 核心功能

- **关注与阅读**：管理信息源，查看最近作品、分类历史和未读状态；支持不再关注并保留历史阅读入口，或删除博主并清理其历史与专属缓存。
- **文字与视频简报**：B站优先读取字幕，必要时本地 Whisper 转写；支持分段画面采样、OCR 和可选的角色识别。
- **图文分析**：小红书与抖音结合正文、图片文字和可选视觉理解生成简报，阅读界面以文字为主。
- **后台处理**：SQLite 保存历史和分析任务；支持失败重试、保留旧成功简报、每日分析尝试预算和媒体清理预览。
- **自选 AI 服务**：保存多个供应商、接口与模型组合，一键切换启用项，分别测试文字连接和图片输入。
- **自动检查**：可在界面中启用 Windows 计划任务，默认每 3 小时检查一次；安装不会自动启用。

### 平台支持与限制

| 平台 | 当前可读取的内容 | 主要限制 |
| --- | --- | --- |
| B站 | UP 主公开视频，字幕、语音与可选画面分析 | 不承诺读取全部动态类型；API 风控可能要求扫码登录，分页补抓有预算 |
| 知乎 | 个人或机构的原创文章、回答、想法、提问和视频动态 | 过滤点赞、关注等互动；文章与视频基于可读正文、简介或字幕生成简报，回答等展示原文；不做视频下载和分段画面分析 |
| 小红书 | 博主公开视频与图文，自动区分作品类型 | 不收录收藏、评论和私密内容；正文、媒体及历史可见范围受登录和页面加载限制 |
| 抖音 | 博主公开视频与图文 | 只读取官方页面可见的目标作者作品；不收录广告、推荐作者或私密作品，不能保证补齐历史 |
| RSS / Atom | Feed 返回的文章与摘要 | 主要通过命令行添加；只覆盖 Feed 当前返回的窗口，不自动为所有条目生成 AI 简报 |
| RSSHub 路由 | 微博、快手、X、YouTube、微信公众号等已注册路由 | 主要通过命令行使用，需另行运行 RSSHub；路由是否可用取决于实例、平台和访问条件，不等于原生平台支持 |

四个直接接入的平台首次关注通常展示最近 3 条内容；后续补抓受到分页、时间和可见范围限制。遇到登录、验证码或限流时停止或提示范围有限，不绕过访问控制。AI 输出和采样识别可能遗漏或误解内容，请结合原文核对。细节见[使用说明](docs/USAGE.md)。

## 当前状态

- **Windows 桌面端**：已实现，推荐入口；提供独立的平台登录窗口。
- **本机网页**：已实现，与桌面共用界面和本机数据。B站扫码可用；知乎、小红书和抖音的独立登录窗口需要桌面入口，网页可复用已保存会话或手动填写自己的 Cookie。
- **安卓配套客户端（计划中）**：用于在安卓手机上配合电脑端使用；安卓源码、手机连接服务和专用构建内容尚未包含在这个公开仓库中。Windows 安装和运行不依赖安卓。
- **版本与分发**：当前可获取的是 `main` 分支源码，`VERSION` 为 `0.1.0`；分支已包含[更新记录](CHANGELOG.md)中的未发布改动。截至 2026-10-13，仓库尚无版本标签或正式 Release，也未提供免 Python 的 EXE 安装包。

本项目是单用户本机应用。GitHub 托管源码，GitHub Pages 无法运行 Python 后端；公网多用户部署尚未实现。

## 运行环境

| 项目 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11，64 位 |
| Python | Python 3.11 x64，安装脚本会检查版本和位数 |
| 桌面窗口 | [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) |
| 平台网页采集 | Microsoft Edge 浏览器；小红书、抖音以及知乎浏览器回退需要它 |
| 网络与模型 | 下载依赖和模型、读取平台与调用远程 AI 时需要联网；CPU 可运行，转写和画面分析可能较慢 |
| 开发检查 | 另需 Git 和 Node.js 22；普通使用不需要 Node.js |

音视频解码使用 PyAV，无需单独安装 FFmpeg 命令行工具。其他系统和 Python 版本尚未作为发行环境验证。

## 安装与启动

先安装 Python 3.11 x64 和 WebView2，在自己有写入权限的目录打开 PowerShell。已安装 Git 时执行：

```powershell
git clone https://github.com/mill-ditchy/echo-insight.git
cd echo-insight
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
.\.venv\Scripts\python.exe desktop.py
```

没有 Git 时，可以在仓库页面选择 **Code → Download ZIP**，解压后在项目根目录执行上面最后两条命令。

安装脚本创建 `.venv`，安装固定版本的运行依赖并检查环境；不会覆盖已有个人设置、启用计划任务或下载角色模型。首次语音转写可能下载 Whisper 模型。以后可双击 `启动赛博情报局.bat`。

### 本机浏览器入口

不打开桌面窗口时，在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn webapp:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

打开 <http://127.0.0.1:8000>，保持终端运行；按 `Ctrl+C` 停止网页服务。已有分析任务可能继续由独立后台进程执行。不要把监听地址改成公网地址。

命令行入口及可选模型安装见[使用说明](docs/USAGE.md)，更新、备份与卸载见[安装与排查](docs/TROUBLESHOOTING.md)。

## 首次配置

1. **打开应用**：空关注列表可以正常启动，不需要复制示例配置或他人的数据。
2. **配置 AI**：在“设置 → AI 接入”新增配置，填写名称、供应商、接口地址和 API Key，点击“读取可用模型”，从下拉列表选择文字模型和视觉模型。远程接口使用 HTTPS，本机兼容服务可用 `http://127.0.0.1:端口/v1`。内置默认地址不代表免费额度或可用性保证。
3. **测试并保存**：点击“测试连接”；需要分析画面或图片时选择支持图片的模型，并完成图片输入测试。测试会实际调用服务，可能计费。可提前保存多套配置，再用列表中的“启用”开关切换；原有配置会保留。
4. **登录平台**：在“账号管理”完成需要的平台登录。知乎、小红书和抖音在官方窗口登录后，回到应用保存状态；B站使用手机客户端扫码。
5. **添加关注**：B站填写数字 UID；其他三个平台粘贴博主主页链接。先检查读取范围与分析状态，再按需启用自动抓取。

凭证通过 Windows DPAPI 加密保存在本机，绑定当前 Windows 用户和电脑。不要分享 Cookie、Key 或 `config/secrets/`。分析次数预算不是金额预算，一次分析可能调用模型多次。

## 常见问题

**窗口没有打开？** 用 `.\.venv\Scripts\python.exe desktop.py` 查看错误，再运行：

```powershell
.\.venv\Scripts\python.exe scripts/doctor.py --strict
```

检查 Python 版本、WebView2 和完整源码资源，详见[排查说明](docs/TROUBLESHOOTING.md)。

**平台读取失败或没有新内容？** 先核对主页链接和登录状态；限流、验证码或页面改版不代表没有更新。稍后重试并查看读取范围，不要反复提高检查频率。

**文字模型能连接，图片分析却失败？** 服务需要支持所选的图片输入格式；单纯兼容文字 Chat Completions 不保证支持视觉或 `video_url`。只需要语音摘要时可关闭画面分析。

**为什么关闭窗口后还在处理？** 已提交的分析任务使用独立进程。退出前在“设置 → 后台任务”查看状态；备份、更新或卸载前先关闭自动抓取，并等待后台处理结束。

**需要上传个人配置或安装安卓吗？** 都不需要。配置、历史、模型和媒体留在本机，安卓尚未随公开源码提供。

## 文件与目录说明

GitHub 文件列表中，文件名旁的文字是最近一次修改该文件的提交说明。同一次提交涉及多个文件时，这些文字会相同。各文件与目录的用途如下：

| 文件或目录 | 内容与用途 |
| --- | --- |
| [desktop.py](desktop.py) | Windows 桌面入口，启动本机服务、应用窗口和平台登录窗口 |
| [webapp.py](webapp.py) | 本机网页后端，提供关注、阅读、设置和分析任务等接口 |
| [app.py](app.py) | 交互式命令行入口，管理关注并生成 Markdown 简报 |
| [scheduler.py](scheduler.py) | 定时检查入口，供 Windows 任务计划程序调用 |
| [启动赛博情报局.bat](启动赛博情报局.bat) | 使用已安装的本机环境启动桌面应用 |
| [src/](src/) | 核心代码，包括平台采集、AI 分析、账号、数据存储与后台任务 |
| [web/](web/) | 桌面和浏览器共用的页面、样式与前端交互代码 |
| [scripts/](scripts/) | 安装、环境诊断、验证、数据维护与源码打包工具 |
| [tests/](tests/) | Python 与前端回归测试 |
| [config/](config/) | 仓库提供配置格式示例；运行时的个人配置保存在本机且不提交 |
| [data/](data/) | 本机历史与任务数据的存放目录，仓库仅保留目录占位 |
| [output/](output/) | 本机简报、分析依据与媒体缓存的输出目录，仓库仅保留目录占位 |
| [knowledge/](knowledge/) | 本机资料参考图的存放目录，仓库不附带预置图库 |
| [docs/](docs/) | 使用、排查、开发、发布及验证记录等详细文档 |
| [requirements.txt](requirements.txt) / [requirements-windows-py311.lock](requirements-windows-py311.lock) | 运行依赖声明及 Windows / Python 3.11 的固定版本依赖清单 |
| [requirements-dev.txt](requirements-dev.txt) / [requirements-dev-windows-py311.lock](requirements-dev-windows-py311.lock) | 开发工具依赖声明及固定版本清单 |
| [.github/](.github/) | GitHub 自动检查、发布工作流、Issue 与 PR 模板等协作配置 |
| [.gitignore](.gitignore) / [.gitattributes](.gitattributes) | 指定不纳入 Git 的本机文件，以及文件换行等仓库规则 |
| [README.md](README.md) | 项目介绍、安装入口、首次配置与文件导航 |
| [VERSION](VERSION) / [CHANGELOG.md](CHANGELOG.md) | 当前版本号及功能、修复的更新记录 |
| [CONTRIBUTING.md](CONTRIBUTING.md) / [CONTRIBUTORS.md](CONTRIBUTORS.md) | 贡献流程与贡献者署名 |
| [SECURITY.md](SECURITY.md) | 安全问题的报告方式与数据保护说明 |
| [LICENSE](LICENSE) / [LICENSES/](LICENSES/) | 项目 MIT 许可及第三方组件的授权、署名说明 |

代码模块的进一步划分见[开发指南](docs/DEVELOPMENT.md)。

## 参与、报告与许可

文档改进、翻译、复现问题和代码贡献都欢迎。请先阅读[贡献指南](CONTRIBUTING.md)，再提交[问题报告或功能建议](https://github.com/mill-ditchy/echo-insight/issues/new/choose)。可核实的署名见[贡献者名单](CONTRIBUTORS.md)。

安全漏洞按[安全报告流程](SECURITY.md)私密报告，不在普通 Issue 公开利用细节或凭证。

源码采用 [MIT License](LICENSE)，允许依照条款使用、修改和分发。模型权重、第三方代码、角色参考图片和平台内容遵循各自授权；见[许可证说明](docs/LICENSING.md)与[第三方署名](LICENSES/character-models.txt)。

进一步阅读：[开发指南](docs/DEVELOPMENT.md) · [发布操作](docs/RELEASING.md) · [验证记录](docs/VALIDATION.md) · [后台任务与存储说明](docs/RELIABILITY_PLAN.md)。
