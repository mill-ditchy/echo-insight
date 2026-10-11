# 发布操作

首次发布前先创建空 GitHub 仓库，仓库名建议 `cyber-intelligence-agency`。不要自动生成 README 或 LICENSE。在仓库设置中启用 Actions、Dependabot alerts 与 Private vulnerability reporting；首次上传可以选择私有。远端权限由仓库所有者配置，源码准备脚本不会擅自更改公开状态。

## 本地检查与源码包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1 -Development
git add .
.\.venv\Scripts\python.exe scripts/release.py check
.\.venv\Scripts\python.exe scripts/verify_project.py --clean-source
git diff --cached --check
git commit -m "Prepare v0.1.0 source release"
git tag v0.1.0
.\.venv\Scripts\python.exe scripts/release.py build --ref v0.1.0
```

已有对应提交和标签时跳过重复创建。构建前工作区须无未提交源码变化；个人配置等被忽略的文件允许保留。`release.py` 读取 Git 提交里的文件，不会将本机数据目录打进源码包。输出 ZIP、SHA256 和记录版本、提交号的 JSON 清单均位于 `dist/`。

## 推送并生成草稿

替换自己的仓库地址后执行：

```powershell
git remote add origin https://github.com/YOUR_NAME/cyber-intelligence-agency.git
git push -u origin main
git push origin v0.1.0
```

已有远端时先检查 `git remote -v`，不要重复添加。推荐先确认主分支 CI 成功，再推版本标签。标签触发完整 CI；全部通过后自动生成带源码包、校验值与清单的草稿 Release。确认真实环境登录、抓取、AI 简报、通知与自动任务可用后，通过 GitHub 页面发布草稿；失败时读取对应 Actions 日志修复，不覆盖已经公开发布的版本。

本机已完成提交和标签时，也可一次推送两者：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/publish.ps1 -RepositoryUrl https://github.com/YOUR_NAME/cyber-intelligence-agency.git
```

该脚本验证干净的 `main`、对应版本标签和源码扫描，再以原子推送提交与标签；远端不一致、认证失败或远端已有冲突历史时停止，不执行强制推送。它不会创建 GitHub 仓库或更改公开状态。首次 HTTPS 推送需要 Git 的 GitHub 认证；应用内 GitHub 连接不等同于命令行推送已登录。

## 后续版本

修改 `VERSION`、`CHANGELOG.md` 和 `docs/releases/vX.Y.Z.md`，用相同版本号创建标签。发布工作流要求标签与提交中的版本一致。依赖变化需更新锁文件并在独立虚拟环境验证。源码 MIT 授权与第三方说明都必须保留；模型和图片继续由用户自行安装。
