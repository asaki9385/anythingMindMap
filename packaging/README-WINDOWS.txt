AnythingMindmap Windows 便携版
==============================

一、启动
1. 将整个 ZIP 解压到一个可写目录，例如：
   D:\AnythingMindmap
2. 双击 start.bat。
3. 启动成功后浏览器会自动打开：
   http://localhost:8000

如果 8000 端口被占用，可以在命令行中先设置：
   set PORT=8010
然后再运行 start.bat。

二、首次配置
打开页面右上角的设置，配置：
- OpenAI chat-completions 兼容模型服务
- MinerU Token（只有扫描版 PDF 需要）

发布包中没有内置模型或 OCR 凭证。

三、局域网访问
在命令行中设置：
   set HOST=0.0.0.0
   set KT_AUTH_PASSWORD=换成你的密码
   start.bat

然后在同一局域网的电脑访问：
   http://这台Windows电脑的IP:8000

需要在 Windows 防火墙中放行对应 TCP 端口。

四、数据位置
上传记录、知识树、复习进度和设置都保存在：
   app\knowledge-compiler\data

请定期备份该目录。不要单独移动 runtime 或 app 文件夹。

五、运行要求
- 文件夹必须位于有写权限的位置，不建议放进 C:\Program Files。
- 模型服务、MinerU 和部分前端资源可能需要网络。
- 当前版本按单实例运行，不要启动多个副本共用同一份 data。
