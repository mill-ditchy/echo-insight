# 贡献指南

欢迎改进代码、文档和翻译，也欢迎提供问题复现、测试反馈或更清晰的使用说明。无需先提交代码才能参与。

## 报告问题与提出建议

先查看 [README](README.md)、[排查说明](docs/TROUBLESHOOTING.md)和[已有 Issues](https://github.com/mill-ditchy/echo-insight/issues)，避免重复报告。

- 错误报告请使用[错误模板](https://github.com/mill-ditchy/echo-insight/issues/new?template=bug_report.yml)，提供版本或提交号、Windows/Python 环境、入口、复现步骤、预期结果和实际结果。
- 功能建议请使用[建议模板](https://github.com/mill-ditchy/echo-insight/issues/new?template=feature_request.yml)，说明真实使用场景、现有方式的困难和期望结果。较大的平台或架构改动建议先讨论范围。
- 文档错误、翻译和排查经验可以使用功能建议模板或直接提交小型 PR。没有复现成功也可以反馈，但请区分事实与猜测。
- 安全漏洞请先阅读 [SECURITY.md](SECURITY.md)，不要在普通 Issue 或 PR 中公开利用步骤。

可在项目根目录生成环境报告：

```powershell
.\.venv\Scripts\python.exe scripts/doctor.py --json
```

工具不读取账号凭证，但附件、日志和截图仍需人工脱敏。不要提交 Cookie、API Key、登录二维码、私人配置、历史或媒体。

## 准备开发环境

开发环境为 Windows 10/11、Python 3.11 x64、WebView2 和 Node.js 22；网页采集还可能需要 Microsoft Edge。运行依赖与开发依赖使用各自的 Windows/Python 3.11 锁文件。

先在 GitHub Fork 仓库，再克隆自己的 Fork。以下命令也可用于克隆上游进行本地阅读和开发；最终改动应推送到自己的 Fork：

```powershell
git clone https://github.com/mill-ditchy/echo-insight.git
cd echo-insight
git switch -c codex/improve-docs
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1 -Development
.\.venv\Scripts\python.exe scripts/verify_project.py
```

分支名按实际任务调整。安装脚本不会安装 Node.js，请自行准备。桌面调试可执行 `.\.venv\Scripts\python.exe desktop.py`。自动回归使用隔离数据与模拟依赖，不需要真实账号、付费 AI 密钥或角色模型；联调真实平台则需要自己的配置，并可能产生费用。

安卓和手机服务尚未包含在公开仓库；默认验证 Windows 与共用网页，不要求安卓 SDK 或伴侣测试。

## 修改与必要验证

一次改动围绕一个明确问题展开，保留既有行为和数据兼容性。行为变化增加能防止回归的测试；避免仅为了拆函数或套用模式而扩大范围。模块边界、平台接口、依赖升级和实际联网验证见[开发指南](docs/DEVELOPMENT.md)。

按改动选择检查：

| 改动 | 必要验证 |
| --- | --- |
| 文档、翻译或模板 | 检查相对链接、文件路径、命令与界面名称；确认未公开私人数据；模板 YAML 能正确解析 |
| Python 逻辑或前端行为 | 相关回归测试；提交前运行完整验证，记录未能执行的检查 |
| 配置、存储、任务与平台接入 | 空配置启动、兼容数据和失败路径；真实平台联调单独记录，不把模拟测试视为联网验证 |
| 依赖或发布范围 | 同步锁文件或公开路径规则，执行完整验证；不要放宽凭证与私人目录限制 |

代码改动的完整验证入口：

```powershell
.\.venv\Scripts\python.exe scripts/verify_project.py
```

审核需要提交的文件后，在项目根目录执行以下提交前检查。示例把全部未忽略改动加入暂存区，执行前确认工作目录没有无关文件：

```powershell
git add .
git --no-pager diff --cached --name-only
.\.venv\Scripts\python.exe scripts/release.py check
git diff --cached --check
```

需要验证公开源码能独立运行时，再执行：

```powershell
.\.venv\Scripts\python.exe scripts/verify_project.py --clean-source
```

源码检查读取暂存区的实际字节，修改后须重新暂存；干净源码验证会创建并清理 `.test-tmp/` 内的隔离副本。检查通过不替代人工审核 diff。完整检查未执行时如实说明原因，不填写“通过”。

## 提交 Pull Request

1. 检查变更只包含本次任务，不包含个人配置、模型、媒体、历史或生成文件。
2. 用简短提交说明描述问题和结果，将分支推送到自己的 Fork，再向本仓库 `main` 提交 PR。
3. 填写 PR 模板：修改目的、行为变化、实际验证、兼容性和未验证部分。仅文档改动可以标注代码测试“不适用”。
4. 行为或用户可见变化写入 [CHANGELOG.md](CHANGELOG.md) 的“未发布”部分；必要时更新 README 或 docs。
5. 查看 [Actions](https://github.com/mill-ditchy/echo-insight/actions) 与审查反馈，补充验证后再合并。CI 不会替代真实登录、平台风控或付费模型测试。

请尽量用自然、准确的中文说明；保留必要的英文 API 和命令名称。引用代码或平台内容时提供来源，并确认可公开。

## 署名与许可

[CONTRIBUTORS.md](CONTRIBUTORS.md)是可核实的贡献者名单，本文件是贡献流程。新增署名应引用真实提交或已合并 PR；不推断真实姓名、不编造贡献，也不自动把工具或模型列为贡献者。

你有权提交的原创代码和文档将按项目 [MIT License](LICENSE)分发。引入第三方内容须保留原授权和署名，模型、角色图片及平台内容不会自动转为 MIT；详见[许可证说明](docs/LICENSING.md)。
