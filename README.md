# 赛博情报局

一个在 Windows 本机运行的个人情报工作台：关注 B站、知乎等信息源，收集最近更新，用字幕、语音和视频画面生成 AI 简报，并记录未读状态。

当前版本 **0.1.0**，源码采用 [MIT 许可证](LICENSE)。本机配置、账号和下载内容均与发布源码分开。[更新记录](CHANGELOG.md) · [安装与排查](docs/TROUBLESHOOTING.md) · [参与开发](CONTRIBUTING.md) · [安全报告](SECURITY.md) · [发布操作](docs/RELEASING.md)。

## 功能与运行环境

- 桌面界面、账号登录、关注管理、简报阅读与 Windows 自动任务。
- B站字幕、Whisper 语音识别、分段画面分析、OCR 和可选的本地角色识别。
- 知乎公开原创动态；文章和视频使用 AI 简报，回答、想法等按内容类型展示。
- 命令行支持 RSS 与 RSSHub 来源；第三方路由是否可用取决于 RSSHub 实例。

推荐 Windows 10/11、64 位 Python 3.11。桌面窗口需要 Microsoft Edge WebView2 Runtime。CPU 可以运行，语音模型及可选角色模型需要额外磁盘空间；首次下载模型需要联网。视频解码使用 PyAV，无需另装 FFmpeg 命令行工具。运行前端回归检查另需 Node.js 22。其他操作系统和 Python 版本尚未作为发行环境验证。

## 首次安装与启动

将源码解压到当前用户可写的目录，安装 Python 3.11 x64 和 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。在项目目录打开 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
.\.venv\Scripts\python.exe desktop.py
```

安装脚本使用固定依赖快照，可以重复执行，不覆盖自己的配置、不自动开启计划任务或下载模型。需要指定 Python 路径时添加 `-PythonPath "C:\路径\python.exe"`。以后可双击 `启动赛博情报局.bat`。如果窗口没有打开，用上面的 `desktop.py` 命令查看错误，或运行 `python scripts/doctor.py --strict` 检查环境。命令行入口为：

```powershell
.\.venv\Scripts\python.exe app.py
```

首次启动可以直接使用空关注列表，无需复制他人的配置或数据。在“账号管理”登录需要的平台，在“设置 → AI 接入”填写自己的接口地址、模型和 Key，再添加关注。支持图片的模型通过连接测试后再开启画面分析。

只使用浏览器时，可启动本地网页服务：

```powershell
.\.venv\Scripts\python.exe -m uvicorn webapp:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

然后访问 <http://127.0.0.1:8000>。知乎独立登录窗口需要桌面入口；浏览器入口可手动提供 Cookie。

`config/*.example.json` 是无私人数据的格式示例，应用不会自动加载它们。其他配置缺失时使用代码默认值；关注、AI 接入和资料库通过界面保存到本机。自己的已有配置和登录状态保留，不需要覆盖。可选角色模型安装方式：

```powershell
.\.venv\Scripts\python.exe scripts/setup_character_models.py
```

需要时可加 `--mirror` 使用镜像，或加 `--without-wd` 只安装 CCIP。运行 `scripts/seed_character_gallery.py` 会联网下载起始角色参考图并写入本机资料库；图片及模型权利说明见 `LICENSES/`。

## 目录说明

```text
config/     本机配置、加密凭证及可提交的 *.example.json
data/       关注检测状态、更新历史和阅读状态
output/     简报、媒体缓存与视频分析证据
knowledge/  本机角色参考图片
models/     可选模型文件
src/        抓取、处理与安全模块
web/static/ 桌面和浏览器共用的前端
tests/      Python 与 JavaScript 回归检查
desktop.py  桌面入口
webapp.py   本地 FastAPI 服务
app.py      命令行入口
scheduler.py Windows 自动更新入口
```

## 上传 GitHub 与发布准备

上传代码仓库和部署在线服务是两件事。本项目当前是 Windows 本地应用：GitHub 可以托管源码和发行包，GitHub Pages 只能托管静态文件，无法运行这里的 Python 后端、扫码登录与自动抓取。

源码上传前：

1. 在 GitHub 创建仓库，首次可以选择私有；使用下方命令时，不勾选自动生成 README、LICENSE 或 `.gitignore`。
2. 本项目源码使用根目录 `LICENSE` 中的 MIT 许可证；保留 `LICENSES/` 的第三方说明。模型、角色图片和平台内容不因源码许可证改变授权，发行包不包含这些文件。
3. 检查待提交文件。本机 `config/` 中仅 `*.example.json` 可以进入仓库；Cookie、Key、个人接口设置、关注列表、资料库、参考图、历史、简报、缓存、模型和临时文件均已加入忽略规则。本机文件仍保留。
4. 使用下方发布检查，并在首次 push 后确认 GitHub Actions 成功。CI 自动执行依赖快照校验、私人文件扫描、PowerShell 语法、回归和空配置启动；真实账号登录、付费 AI 调用与平台风控需人工核对。

在项目根目录依次执行，先审核文件再提交；替换命令中的仓库地址：

```powershell
git init -b main
git add .
.\.venv\Scripts\python.exe scripts/release.py check
.\.venv\Scripts\python.exe scripts/verify_project.py --clean-source
git diff --cached --name-only
git diff --cached --check
git commit -m "Initial release"
git remote add origin https://github.com/YOUR_NAME/YOUR_REPO.git
git push -u origin main
```

如果已有有效 Git 仓库，保留其分支和远端配置，跳过初始化与重复添加远端。`.gitignore` 不会移除已跟踪的文件；如果审核列表仍包含私人文件，先用 `git rm --cached -- 文件路径` 取消跟踪。若凭证曾上传过，应先撤销或更换凭证，再清理历史。

本版提供可安装源码包：提交源码后，执行 `.\.venv\Scripts\python.exe scripts/release.py build`，在 `dist/` 得到 ZIP、SHA256 和记录提交号的构建清单。推送与 `VERSION` 一致的 `v0.1.0` 标签后，自动检查通过会生成 GitHub 草稿 Release。具体步骤见 [发布操作](docs/RELEASING.md)，完整方案见 [发布方案](docs/RELEASE_PLAN.md)。

免 Python 的 Windows EXE 安装包尚未包含在本版范围；本版源码包通过 `setup.ps1` 安装。将来提供 EXE 前需验证资源打包、可写的数据目录、WebView2 和计划任务，并在未安装开发环境的 Windows 电脑实际测试。

如果希望通过公网网址使用，仍需实现用户登录和权限、每个用户独立的数据与凭证、服务器端凭证存储、后台任务服务及 HTTPS 部署。现有 API 仅接受本机访问，凭证依赖 Windows DPAPI，计划任务依赖 Windows；修改监听地址不会完成这些改造。

## 开发检查

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1 -Development
.\.venv\Scripts\python.exe scripts/verify_project.py
git add .
.\.venv\Scripts\python.exe scripts/release.py check
.\.venv\Scripts\python.exe scripts/verify_project.py --clean-source
```

`.github/workflows/ci.yml` 在分支 push 和 Pull Request 时使用 Windows、Python 3.11 与 Node.js 22 运行检查；官方 Actions 固定到已核对的提交，由 Dependabot 提议更新。配置和模型不需要提交，也不需要为自动回归设置账号密钥。发布扫描只覆盖明确规则，提交前仍应审核文件与 diff。

## B站抓取遇到风控时

B站可能拒绝匿名 API 请求，并返回 `-352`、`-412` 或 `-799`。此时可以让程序使用你自己的登录会话：

在桌面应用的“账号管理”中点击哔哩哔哩，然后使用手机客户端扫码登录。

Cookie 和刷新令牌会通过 Windows DPAPI 加密后保存在 `config/secrets/`，只能由当前 Windows 用户在当前电脑上解密。旧版明文凭证会在首次读取时自动迁移并删除。Cookie 相当于临时登录凭证，仍然不要发送给别人或截图分享。

## 添加知乎关注

在“添加关注”中选择“知乎”，可以直接粘贴个人主页或机构主页链接，例如：

```text
https://www.zhihu.com/people/excited-vczh
https://www.zhihu.com/org/zhi-hu-ri-bao-51-41
```

也可以只输入网址中 `/people/` 或 `/org/` 后面的英文标识，例如 `zhi-hu-ri-bao-51-41`。这里不能填写页面上的中文昵称“知乎日报”。程序会读取该账号的公开动态，并只保留账号自己创作的内容；点赞、关注、收藏等互动行为不会进入简报。

详情页和 Markdown 简报按“文章、回答、想法、提问、视频”分区展示。文章和视频会基于读取到的公开正文、简介或字幕生成 AI 简报；回答、提问和想法直接展示公开原文。知乎视频暂不使用 B站的下载与分段画面分析流程。

知乎通常会拒绝未登录请求。推荐在桌面应用的“账号管理”中点击“知乎”：程序会打开独立的知乎登录窗口，你在知乎页面完成登录后回到主窗口点击“保存登录状态”。程序不会读取或保存密码，只提取知乎域名的会话 Cookie，并通过 Windows DPAPI 加密保存在 `config/secrets/`。临时登录窗口的浏览器 Cookie 随后会被清理。

如果桌面登录窗口不可用，添加关注时仍可手动粘贴 Cookie 的值或以 `Cookie:` 开头的完整请求头作为备用。Cookie 相当于登录凭证，不要发送给别人或截图分享；不再使用知乎功能时，可以在“账号管理”点击“删除登录状态”。

程序只低频读取你主动关注账号的公开页面；遇到限流或安全验证会停止，不会绕过验证码或访问控制。建议保持较长的自动检查间隔，并遵守知乎服务条款和适用法律。

## B站字幕读取

运行“抓取并生成简报”时，程序会自动读取每个B站关注源最新一条视频的字幕。

完整字幕按 `BVID` 缓存在：

```text
output/transcripts/BVxxxxxxxx.json
```

缓存中会保留字幕语言、字幕类型、完整文字以及每段字幕的开始和结束时间。再次运行时会直接读取缓存，避免重复请求B站接口。

## 自定义 AI 服务与模型

在应用“设置 → AI 接入”中填写接口 URL、API Key 和文字模型名称，即可接入兼容 Chat Completions 的服务。OpenCode 是保留的默认配置，也可以换成其他服务商或本地模型。

1. 填写服务商提供的基础地址，例如 `https://api.example.com/v1`，或完整的 `https://api.example.com/v1/chat/completions`。仅填写域名时会使用 `/v1/chat/completions`。本地服务可填写 `http://127.0.0.1:11434/v1`；远程服务必须使用 HTTPS。
2. 填写该服务的 API Key 和模型 ID。API Key 会使用 Windows DPAPI 加密保存，页面不会回显。
3. 需要分析视频画面时，启用“视频画面分析”并填写支持图片输入的视觉模型。视觉模型留空会使用文字模型；只支持文字的模型请关闭画面分析。
4. 点击“读取可用模型”，从服务商返回的列表中选择准确的模型 ID。若服务商没有模型列表接口，可手动填写。
5. 点击“测试连接”。程序会调用所填模型实际生成少量文字；启用画面分析时还会测试图片输入。测试可能消耗少量额度，不会保存草稿或密钥；URL 不变时 Key 留空可使用已保存的密钥。
6. 测试通过后点击“保存 AI 设置”，下一次生成简报会使用新的服务和模型。已经生成的历史简报保留；需要更新旧简报时，在博主详情中重试或重新分析。

修改模型时，API Key 留空可以保留已保存的密钥；切换接口地址时必须填写新服务的 Key。正在生成的简报继续使用启动时对应的地址和凭证，不会把旧服务的密钥发送给新服务。

配置保存在 `config/ai_provider.json`，格式如下（模型 ID 以服务商返回的列表为准）：

```json
{
  "api_url": "https://api.example.com/v1/chat/completions",
  "model": "your-model-id"
}
```

URL 和模型设置保存在 `config/ai_provider.json`，该文件只保存加密凭证的标识，不保存 API Key 明文。旧版 OpenCode 加密凭证和明文凭证迁移仍兼容。

AI总结会缓存到：

```text
output/summaries/BVxxxxxxxx.json
```

相同字幕、接口地址和模型会直接使用缓存，不会重复调用API和重复计费。

未在页面保存自定义配置前，接口与模型仍可以通过以下旧版环境变量调整；保存页面设置后，以页面设置为准：

```text
DEEPSEEK_API_KEY
DEEPSEEK_API_URL
DEEPSEEK_MODEL
```

连接测试使用与简报生成相同的请求逻辑，设置连接和读取超时，只尝试一次；拒绝重定向并隐藏错误中的密钥。已保存的密钥不会发送到草稿中修改后的新 URL。模型名称在不同服务商之间不通用，更换服务时请重新读取模型并测试。

启用画面分析后，没有字幕且语音识别未得到文字的视频会改用关键帧生成简报，并明确限定仅依据可见画面。字幕和画面均不可用时不会虚构摘要。B站下载会筛选可信 HTTPS 地址，并在首选地址不可用时尝试可信备用地址；不完整的下载不会写入缓存。

## 视频理解与多作品资料

视频画面分析启用时，默认先分段读取有时间标签的画面、屏幕文字与对应对白，再生成全片摘要。标题、简介和作者注明的原片来源会一起传入模型；作者背景只提供线索，不能决定作品归属。流程支持不同动漫、游戏、混剪、描改和虚拟主播内容，允许多个作品候选与未知角色；实际识别效果仍需按题材验证。

在“设置 → 视频理解”调整分析预算、屏幕文字识别和联网资料查询。默认每段目标 20 秒、最多 10 段、每段最多 8 张采样画面；超过段数预算时延长每段以覆盖全片。模型调用次数会增加，各段结果独立缓存。实际覆盖是采样覆盖，快速动作和短暂画面仍可能遗漏。

图片输入兼容当前图片模型。视频输入选项将采样画面编码为无声 MP4，通过 `video_url` 发送，需要服务明确支持该格式；它不等于发送未经采样的原始音视频。点击“测试画面输入方式”可用已保存的视觉模型实际测试一次，测试可能计费。文字模型与视觉模型仍在“AI 接入”中分别设置。

“设置 → 多作品资料”可添加作品、角色、别名、可靠说明、来源链接及角色参考图。资料可以来自不同作品，不需要按博主绑定游戏。未识别的作品或角色可以按需查询中英文维基百科，查询最多两次，结果缓存并保留出处，作为待核对候选使用；百科覆盖和网络可达性会限制补充效果。自动检索结果不会写进人工资料库，旧 AI 摘要也不会自动成为事实资料。

本地 OCR 使用 RapidOCR 的中文与英文识别模型；日文等文字由视觉模型辅助读取，错字仍需结合画面核对。角色参考图与视频画面分别标注，避免把参考图写成视频剧情。识别失败时保留未知身份，仍可概括可见动作、人物互动和创作形式。

简报会显示分段数、采样帧数及分析是否不完整，并可展开“分段分析依据与资料出处”。片段遇到网络超时或服务端错误时最多重试一次，重试仍失败则记录缺失区间。依赖画面的二创等内容在视觉失败后会报错，保留旧简报供查看；普通讲解类视频允许文字回退，但会标明分析不完整。重新分析会保留旧摘要备份，每份新摘要保存对应证据快照。

配置与记录：

```text
config/video_analysis.json       分段、输入方式、OCR与联网检索设置
config/media_knowledge.json      多作品资料，可人工编辑或通过页面添加
knowledge/images/               上传的角色参考图
output/video_evidence/<BVID>/    每段画面证据、OCR、检索出处与失败区间
                                evidence_<hash>.json 是摘要对应的证据快照
output/knowledge_lookups/        公开百科检索缓存
output/summary_history/<BVID>/   被新摘要替换的旧摘要备份
```

对已收录视频执行实际对照验证：

```powershell
.\.venv\Scripts\python.exe scripts/verify_video_understanding.py BVxxxxxxxx
```

加入 `--publish` 将成功结果同步到应用历史记录；`--vision-model 模型ID` 可在当前服务临时测试另一个可用视觉模型，不修改已保存的接入设置。脚本会实际下载视频并调用模型，生成前后对照 JSON。普通“重新视觉分析”会重新调用模型；修复不完整结果时复用未变更的成功片段，仅补分析失败片段。更改模型或资料后，相应片段缓存会失效。

相关验证：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node tests/test_video_understanding_frontend.js
```

## 自动更新与Windows桌面通知

自动任务默认每3小时检查一次关注列表，只处理以前没有见过的新内容。

主页面的“自动抓取”开关可以直接启用或禁用 Windows 计划任务。关闭后不会在开机时补跑，也不会按间隔自动抓取；“立即检查更新”仍然可以手动执行。修改自动检查间隔时，程序会保留当前开关状态。

每位博主每次检查只读取最新3条内容。暂停一段时间后重新开启时，不会补抓暂停期间的全部积压；只检查恢复时最新的3条，其中已经记录过的内容会自动跳过，不会重复展示。首次添加关注和首次建立检测基准也遵循相同的3条限制。

运行失败时会立即显示Windows桌面通知，等待5分钟后自动重试一次；如果再次失败，会提醒检查网络、API Key、配置或程序更新。

首次启用时先建立当前内容基准：

```powershell
.\.venv\Scripts\python.exe scheduler.py --initialize
```

测试桌面通知：

```powershell
.\.venv\Scripts\python.exe scheduler.py --test-notification
```

创建每3小时运行一次的Windows计划任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_scheduler.ps1
```

修改 `config/scheduler.json` 中的 `interval_hours` 后，重新运行上面的安装命令即可更新间隔。

相关文件：

```text
config/scheduler.json       定时间隔和重试设置
data/state.json             已处理内容记录
output/logs/scheduler.log   自动运行日志
output/updates/             仅包含新内容的更新简报
```

## 安全检查与回归验证（2026-10-07）

本轮修复了以下问题：

- 原文链接在列表和详情页都校验 URL 协议，拒绝脚本、本地文件和携带账号密码的地址；头像也复用校验。
- B站字幕、音视频及 API 请求只允许受信任平台域名的 HTTPS 地址，重定向逐跳校验。登录 Cookie 仅发送给 `api.bilibili.com` 的初始请求，不发送给 CDN 或重定向目标。
- AI 接口必须使用 HTTPS，拒绝自动重定向，避免请求正文被转发；错误消息隐藏接口回显的 API Key。
- 参数校验失败不回显 Cookie、API Key 或原始请求正文；畸形 Host、Origin 会被拒绝。服务还核验实际连接来源并关闭代理头信任。
- AI 摘要、字幕和路由缓存校验内容标识与目录边界，拒绝路径穿越；读取历史缓存时跳过非法标识。
- 已存在加密凭证时，继续清理上次迁移可能遗留的明文文件；知乎登录只提取已明确属于知乎域名的 Cookie。
- 自动任务设置的读改写加锁，避免并发设置覆盖；配置损坏时停止修改，保留原文件。

虚拟环境已升级至 `pip 26.2.1`、`setuptools 84.0.0`、`urllib3 2.8.0`；`urllib3` 的安全下限固定为 `2.8`。相关公开公告：[pip 地址处理漏洞](https://github.com/advisories/GHSA-qwm4-qh6w-59xr)、[setuptools 路径穿越漏洞](https://github.com/advisories/GHSA-5rjg-fvgr-3xxf)、[urllib3 TLS 配置漏洞](https://github.com/advisories/GHSA-8988-9cw3-xx77)。

修复后验证：106 项 Python 测试通过；前端 13 种 URL 输入通过列表及详情链接检查；依赖一致性检查通过。OSV 批量查询覆盖虚拟环境中的 48 个软件包，查询时未返回命中的已知漏洞。该结果不覆盖 Python 解释器、系统组件或尚未公开的漏洞。

重建开发环境及运行回归检查：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node tests/test_frontend_security.js
node tests/test_ai_provider_frontend.js
.\.venv\Scripts\python.exe -m pip check
```

前端检查需要 Node.js。应用源码和 Python 依赖的检查不替代真实平台的联网验证；扫码登录、实际视频下载和 AI 调用仍需在运行环境中确认。修改后请重新启动桌面应用，使后台与页面一起载入新代码。
