# Windows 便携包构建说明

## 产物

GitHub Actions 的 `build-release-assets` 工作流会生成：

```text
AnythingMindmap-windows-x64.zip
```

ZIP 解压后包含：

```text
AnythingMindmap-windows-x64/
├── runtime/                 # 包内 Python 3.12
├── app/
│   ├── launcher.py
│   └── knowledge-compiler/  # 仅运行所需代码和 UI
├── start.bat
├── README-WINDOWS.txt
├── VERSION.txt
└── BUILD_INFO.txt
```

发布包不包含测试、benchmark、debug、演示 PPT、历史任务、`settings.json` 或内置凭证。

## 首次推送前

1. 确认 `knowledge-compiler/app_settings.py` 中的 `BUILTIN_CHAT` 和 `BUILTIN_MINERU_TOKEN` 为空。
2. 如果曾使用过临时演示凭证，请在模型和 MinerU 平台撤销对应凭证。
3. 确认 `knowledge-compiler/data/` 不会进入 Git。
4. 初始化仓库并推送到 GitHub：

```bash
git init
git add .
git commit -m "Add Windows portable build"
git branch -M main
git remote add origin https://github.com/<你的账号>/<仓库名>.git
git push -u origin main
```

## 手动触发构建

1. 打开 GitHub 仓库的 **Actions** 页面。
2. 选择 **build-release-assets**。
3. 点击 **Run workflow**。
4. 等待 Windows job 完成。
5. 在本次运行页面底部下载 **AnythingMindmap-windows-x64** artifact。

Artifact 下载后通常还是一层 ZIP，里面才是真正的 `AnythingMindmap-windows-x64.zip`。

## 生成正式 Release

```bash
git tag v1.0.0
git push origin v1.0.0
```

标签推送后工作流会：

1. 构建源码 ZIP、Windows 便携包和 macOS DMG。
2. 解压并运行 smoke test。
3. 上传 artifact。
4. 自动创建 GitHub Release，并把三个产物作为 Release assets 附加。

## 构建脚本

- `scripts/build_windows_portable.py`：复制 Windows Python 运行时、安装依赖、裁剪运行文件、校验并清空内置凭证占位、生成 ZIP。
- `packaging/launcher.py`：启动 Uvicorn，等待 `/api/health`，成功后打开浏览器。
- `packaging/start.bat`：Windows 双击入口，调用包内 Python 和 launcher。
- `scripts/smoke_test_windows.py`：解压最终 ZIP，运行导入检查，双击入口启动检查，访问 `/api/health` 和 UI。

## 本地干跑

在 macOS/Linux 上可以只验证文件裁剪结构：

```bash
python3 scripts/build_windows_portable.py --skip-runtime --output /tmp/mindmap-build
```

该命令不会生成可运行的 Windows 包，因为 Windows Python 运行时必须在 Windows runner 上准备。完整构建和启动验证由 `windows-latest` 完成。

## 注意事项

- 不要把构建机上通过 `python -m venv` 创建的 `.venv` 直接打进 ZIP；venv 的路径信息不可迁移。
- 发布包使用包内 `runtime/python.exe`，不要依赖系统 Python。
- 运行目录必须有写权限，不要解压到 `C:\Program Files`。
- 模型服务和 MinerU 需要网络或内网兼容端点；当前前端部分资源仍可能依赖 CDN。
- 应用按单实例运行，不要启动多个副本共用同一份 `data/`。
