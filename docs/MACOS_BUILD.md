# macOS DMG 构建说明

Release 工作流会生成：

```text
AnythingMindmap-macos-<arch>.dmg
AnythingMindmap-source-<tag>.zip
AnythingMindmap-windows-x64.zip
```

DMG 中包含：

```text
AnythingMindmap.app
Applications -> /Applications
README-MACOS.txt
```

`.app` 内置：

- 独立的 Python 3.12 运行时
- `requirements.txt` 对应的全部依赖
- 运行所需的 `knowledge-compiler/` 与 `start.py`
- 模型服务和 MinerU Token 仍由应用内设置页或环境变量配置，不内置 Key

应用首次启动时会把运行文件复制到：

```text
~/Library/Application Support/AnythingMindmap/
```

日志位于：

```text
~/Library/Logs/AnythingMindmap/launcher.log
```

## 本地构建

在 macOS 上准备一个可迁移的 Python 3.12 运行时后执行：

```bash
python3 scripts/build_macos_dmg.py \
  --runtime-source /path/to/python-runtime \
  --version 1.0.0 \
  --output dist
```

## 本地冒烟测试

```bash
python3 scripts/smoke_test_macos_dmg.py \
  --dmg dist/AnythingMindmap-macos-<arch>.dmg
```

冒烟测试会挂载 DMG，在隔离的临时 `HOME` 中启动应用，并检查：

- `/api/health`
- `/ui/client.html`
- 应用数据目录
- ad-hoc 签名有效性

## 签名与公证

当前构建使用 ad-hoc 签名，适合公开测试版。未使用 Apple Developer ID 签名和
notarization 时，用户首次打开可能需要右键选择“打开”。正式商用发布前应接入
Developer ID、`codesign --options runtime` 和 `notarytool`。
