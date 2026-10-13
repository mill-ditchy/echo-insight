# 开发与验证说明

开发环境、Issue 与 PR 流程见[贡献指南](../CONTRIBUTING.md)。以下约定用于保持当前 Windows 行为、平台边界及本机数据兼容性，不要求为简单修改引入额外框架。

## 入口与模块

| 模块 | 主要职责 |
| --- | --- |
| `desktop.py` | 本机服务与 pywebview 窗口、平台官方登录桥接 |
| `webapp.py` / `web/static/` | 本机 FastAPI 接口、桌面与浏览器共用页面 |
| `app.py` | 交互式命令行与 Markdown 简报 |
| `scheduler.py` | Windows 定时检查、初始基准与失败通知 |
| `src/platforms/` | 平台读取、内容类型解析、增量范围与浏览器状态 |
| `src/processors/` | 字幕、转写、画面、图文、OCR 与摘要 |
| `src/task_queue.py` / `src/task_worker.py` | 持久任务、领取代次、重试和独立执行 |
| `src/update_history.py` / `src/history_state.py` | 历史公开操作与内容状态转换 |
| `src/security/` | 凭证、请求、下载和缓存边界 |

### 新增或修改平台

继承 `src/platforms/base.py` 的 `PlatformFetcher`，并在 `src/platforms/registry.py` 注册。`fetch(uid)` 提供基础读取；日常增量采用 `scan(uid, FetchContext) -> FetchResult`。平台适配器解释自己的游标、关注边界和检查点，明确报告完整、部分范围、继续位置及警告；不要把“读到一页”当作全部历史。

已收录 ID 与冻结补抓锚点承担不同职责，不能提前确认尚未持久化的内容。注册抓取器不等于新增网页支持，还需核对 UI 选项、接口允许的平台与对应分析路径。

复用既有安全模块，保持凭证域名与重定向边界；不要把平台 Cookie、私人账号配置或密钥放入 AI 提示词。模拟回归关注分页、置顶、去重、空数据、限流和失效会话；真实平台联调记录日期与限制，避免提交私人样本。

### 查询、任务与数据

历史读取接口只查询；恢复缓存或提交任务从启动、定时检查和显式命令进入。SQLite 状态变更使用公开事务与状态操作，成功结果和任务完成保持一致；失败重分析继续保护旧成功结果及其配置。

任务在每次执行尝试开始时捕获配置，尝试期间固定；重试可采用新配置。不要修改旧任务指纹含义或公开凭证引用。JSON 兼容路径和旧数据迁移需保留并测试，维护命令见[可靠性说明](RELIABILITY_PLAN.md)。

## 验证入口

```powershell
.\.venv\Scripts\python.exe scripts/verify_project.py
```

该入口检查依赖一致性、Python 回归与 7 个前端测试脚本。可以先运行受影响的独立测试，例如：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_ai_protocol.py -v
node tests/test_source_navigation_frontend.js
```

公开源码独立性验证读取已暂存文件，使用 `.test-tmp/` 隔离副本并包含空配置启动检查：

```powershell
.\.venv\Scripts\python.exe scripts/verify_project.py --clean-source
```

不要对自己的实际配置直接运行隔离测试脚本。新增测试应使用临时目录、模拟外部请求和虚构凭证。测试覆盖不代表真实平台可用、免费模型长期可用或桌面 GUI 已实际操作；具体记录见 [VALIDATION.md](VALIDATION.md)。

当前公开仓库不含安卓和手机服务。源码内 `--include-companion` 为后续配套源码准备的入口，公开仓库不具备其所需全部文件，不用于默认 Windows 验证。

## 依赖更新

依赖范围在 `requirements.txt` 与 `requirements-dev.txt`，已验证快照在 `requirements-windows-py311.lock` 与 `requirements-dev-windows-py311.lock`。升级前核对 Python/Windows 兼容和上游变更；同步范围，在已安装并测试的新环境导出快照：

```powershell
.\.venv\Scripts\python.exe scripts/lock_dependencies.py
.\.venv\Scripts\python.exe scripts/lock_dependencies.py --check
```

导出会修改锁文件；检查不会代替在新虚拟环境安装并运行完整验证。不要在 PR 中悄悄更新无关依赖。Dependabot 提议更新不代表已确认兼容或无安全问题。

## CI 与发布

[ci.yml](../.github/workflows/ci.yml)使用 Windows、Python 3.11 与 Node.js 22，校验依赖快照、公开源码、PowerShell 语法、回归和空配置启动。`verify_project.py` 并不单独包含全部 CI 步骤。

版本标签触发检查并创建草稿源码 Release；公开发布仍是单独步骤。不要覆盖已公开版本或用放宽白名单解决发布扫描失败，操作见[发布说明](RELEASING.md)。
