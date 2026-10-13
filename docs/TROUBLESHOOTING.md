# 安装、更新与排查

## 安装

使用 Python 3.11 x64，将完整源码解压到用户可写的文件夹，避免 `Program Files`。在项目根目录执行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1`。脚本只建立环境与安装依赖，不覆盖已有账号配置、不安装计划任务、不下载角色模型。

没有 `py` 命令时可用 `-PythonPath "C:\路径\python.exe"` 指定解释器。安装途中网络失败可以重跑同一命令。依赖锁针对 Windows/Python 3.11，其他版本尚未作为发布环境验证。

## 桌面窗口没有打开

执行 `.\.venv\Scripts\python.exe desktop.py` 查看启动错误，再运行 `.\.venv\Scripts\python.exe scripts/doctor.py --strict`。缺少 WebView2 时，从 Microsoft 官方网站安装 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。报错显示缺失资源时重新解压整个源码包，保留个人目录。

## 抓取与 AI 失败

先在“账号管理”检查登录，再在“设置 → AI 接入”测试连接。切换服务地址需要该服务自己的 Key。连接成功不代表服务支持图片输入，画面分析须使用视觉模型并通过对应测试。平台返回限流或验证时稍后重试；项目不绕过验证码或访问控制。

首次语音转写需要下载 Whisper 模型；可选角色识别还需执行 `scripts/setup_character_models.py`。下载中断时重新执行，角色模型文件会验证 SHA256。CPU 转写和画面识别耗时较长，可降低分析预算。小红书、抖音和知乎浏览器回退需要 Microsoft Edge 浏览器，它与桌面窗口使用的 WebView2 Runtime 是不同组件。扫码、通知和计划任务需要真实 Windows 环境验证，详细设置见[使用说明](USAGE.md)。

## 更新与备份

先关闭自动抓取，等待已提交的分析任务结束，再退出应用。关闭主窗口不会必然停止独立分析进程，不能立即把仍在写入的数据库当作完整备份。数据库备份、迁移和回退见[后台任务与存储说明](RELIABILITY_PLAN.md)。备份 `config/`、`data/` 和希望保留的 `output/`；角色参考图保存在 `knowledge/images/`，模型保存在 `models/`。在同一 Windows 用户下更新源码并重跑安装脚本，保留这些个人目录。依赖变化时重新安装 `.venv` 中的依赖。登录凭证绑定 Windows 用户和电脑，换电脑后建议重新登录、重新填写 Key。

移动项目目录后，在应用里重新关闭再开启自动抓取，以更新计划任务中的绝对路径。确认任务状态和下一次运行时间；若该目录已不再使用，应先关闭自动抓取。

## 卸载

先在应用里关闭“自动抓取”，等待后台任务结束，退出应用并完成需要的备份，然后删除自己的项目目录。若应用不能打开，可在 Windows 任务计划程序删除名为 `CyberIntelligenceAgency` 的项目任务；不要删除其他任务。环境与模型都位于项目目录，不需要修改系统 Python。

## 提交错误报告

运行 `.\.venv\Scripts\python.exe scripts/doctor.py --json` 取得不读取凭证的环境报告。填写版本或提交号、使用入口、复现步骤和已脱敏错误；不要上传完整个人配置、Key、Cookie、登录二维码或媒体缓存。使用[贡献指南](../CONTRIBUTING.md)中的问题模板；安全问题通过 [SECURITY.md](../SECURITY.md) 中的私密流程报告。
