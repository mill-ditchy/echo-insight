# 参与开发

推荐 Windows 10/11、Python 3.11 x64 和 Node.js 22。克隆仓库后执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1 -Development
.\.venv\Scripts\python.exe scripts/verify_project.py
```

新增信息源请继承 `src/platforms/base.py` 中的 `PlatformFetcher`，并在 `registry.py` 注册。涉及账号、外部请求与缓存时，复用 `src/security/` 的地址校验、凭证存储和路径边界检查。不要向 AI 请求传入登录凭证或私人账号设置。

针对行为变化增加有意义的回归测试；纯文案与样式修改以实际检查为主。提交前执行：

```powershell
git add .
.\.venv\Scripts\python.exe scripts/release.py check
.\.venv\Scripts\python.exe scripts/verify_project.py --clean-source
git diff --cached --check
```

检查扫描的是 Git 暂存区，修改文件后需要重新 `git add`。需要改动发布文件范围时，更新 `scripts/release.py` 的公开路径规则并增加回归测试；不要为了通过检查而绕过私人目录与凭证规则。

依赖范围保存在 `requirements.txt`，Windows/Python 3.11 的已验证快照保存在 `requirements-windows-py311.lock` 与开发锁文件。依赖升级应同步更新范围，在已安装并测试的新环境运行 `python scripts/lock_dependencies.py` 导出快照，再用新的虚拟环境安装锁文件并运行完整检查。CI 会核对实际安装的版本与快照是否一致。锁文件固定版本，不承诺永远没有安全问题；关注 Dependabot 与上游公告。

Pull Request 描述应包含问题、结果和实际验证。不要提交 `.venv`、自己的 `config/*.json`、账户凭证、下载媒体、参考图或历史；使用空的 `*.example.json` 表示配置结构。提交的原创源码按照项目 MIT 许可证分发，第三方内容需保留原授权并核对可分发范围。
