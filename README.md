# Kiro API Connector

把 OpenAI 兼容中转站的模型接入 **Kiro 原生聊天**。每台电脑使用自己的配置与 key，不依赖作者电脑、CC Switch 或作者的数据库。

## 普通用户：使用便携包

下载项目提供的 Windows x64 便携包并完整解压，双击 **打开配置向导.cmd**。便携包包含 Python、Node.js 和固定版本 kRouter，无需另外安装这些运行环境，也无需 skill。

首次配置：

1. 安装并正常登录 Kiro。
2. 打开向导，输入中转站 HTTPS Base URL（例如 `https://example.com/v1`）和 API key。
3. 点击“读取可用模型”，勾选模型并选择默认模型。不支持 `/models` 的服务可以手动填写 ID。
4. 点击“保存并测试连接”。工具会对默认模型发送一次短请求，检查真实回复和 Kiro 协议；可能产生少量费用。
5. 测试通过后点击“启用到 Kiro”，可选登录 Windows 后自启。
6. 在 Kiro 中检查模型列表，发送一句短提示确认；必要时重新加载窗口。向导协议测试通过不等于所有 IDE 工具、MCP 或长任务已经验收。

关闭网页不会停止服务。“查看用量”使用本工具的 kRouter 页面；管理密码在向导中查看。“停用并恢复设置”恢复本工具修改的 endpoint 并停止自己的服务。

## 从 GitHub 克隆或复制源码

源码版需要 Windows、Python 3.11+ 和支持 `node:sqlite` 的 Node.js（建议 22.17+）。先安装这两个运行环境；然后克隆仓库或解压干净的源码包，双击 **打开配置向导.cmd**。也可以执行：

```powershell
python app.py
```

向导会检查 kRouter。未安装时点击“安装 kRouter 依赖”，下载项目固定的 `@sifxprime/krouter@0.5.163` 到项目自己的 runtime 目录，不改全局 npm 安装。依赖就绪后按上面的配置步骤操作。

源码包与便携包是两种交付：源码适合开发者和 AI 助手；普通用户用便携包才能实现“解压后只填连接信息”。不要把一个不含运行环境的源码目录称为免安装工具。

仓库地址：[peerlessHerowu/kiro-api-connector](https://github.com/peerlessHerowu/kiro-api-connector)。已安装 Git、Python 和 Node.js 的用户可以直接执行：

```powershell
git clone https://github.com/peerlessHerowu/kiro-api-connector.git
cd kiro-api-connector
python app.py
```

## 更换配置、已有桥接与适用范围

- 更换地址、模型或 key 时在向导中重新保存；key 留空保留当前值。实际推理失败会尝试恢复此前配置，恢复失败明确报告。
- key 仅保存在本工具的 kRouter 数据库。选定模型目录由向导统一保存，桥接不再单独读取 CC Switch key。
- 当前使用独立端口 20147/20148/20149；如果 Kiro 已接入其他自定义桥接，启用会拒绝覆盖。先在当前任务结束后停用原桥接、恢复设置，再启用此工具。
- 需要正常登录 Kiro；这不是登录绕过工具。当前主要实现 OpenAI Chat Completions 兼容接口，不能保证任意中转站或任意 Kiro 版本兼容。
- 当前自动写设置仅支持严格 JSON。Kiro settings.json 含 JSONC 注释或尾逗号时会拒绝修改并保留原文件。
- 思考档位需供应商支持。仅为确认支持的模型填写档位设置；不能凭模型名称推断能力。
- 不需要复制或修改正常 Kiro 客户端。本项目不包含独立测试客户端的排队或模型分组补丁。

每台电脑在自己的 Windows 用户目录保存数据：`%LOCALAPPDATA%\KiroApiConnector`。日志、数据库、key 和管理密码都不能作为分享内容。

## 分享与发布

GitHub 仓库提供源码及固定版本信息，不上传 `.local`、运行数据或已配置的安装目录。便携 zip 可单独作为 Release 附件；目前尚未发布便携版 Release，首次从 GitHub 获取项目请使用上面的源码部署步骤。

复制源码给他人时，生成干净源码包，避免把调试目录一起复制：

```powershell
python build_source.py --output C:\output\kiro-api-connector-source.zip
```

源码导出使用文件白名单，不包含本机运行环境、缓存、数据库和凭据。分享原始便携 zip 也可以；接收者在自己电脑填写配置，不需要作者的登录状态或配置文件。不要移动已经注册自启的安装目录，移动后应重新注册。

在本机便携 Python/Node/kRouter 中已验证真实推理、6.1 high 档位、默认模型更新、key 保留、服务重启和假 Kiro 设置的启用/恢复。**尚未在另一台干净 Windows 电脑完成原生 IDE 验收**，发布时应保留这个说明。

## 开发实现

Windows local configuration UI and service owner. Python 3.11+ standard library; Node.js with `node:sqlite` (22.17+ recommended); kRouter pinned to 0.5.163. Open `打开配置向导.cmd` or run `python app.py`. Wizard listens on 127.0.0.1:20147; its router/bridge use 20148/20149 with independent data under LOCALAPPDATA/KiroApiConnector. It does not mutate an installed Kiro client or npm bundle.

`runtime/kiro_local_bridge.js` converts Kiro AWS EventStream and reuses the installed kRouter converter. The wizard stores one upstream credential in its own router provider; bridge uses only a local router key. Model selection is stored in config.json. The host-specific compatibility header remains limited to the verified iohub endpoint. Other providers and client versions need real protocol and tool validation.

`runtime/stability-preload.cjs` applies guarded changes in memory: GPT-6 tool continuations preserve effort, response-header timeout is 120 seconds, network retries are limited, the exact verified 6.1 rejection gets at most one retry before stream output. This is not an overall IDE turn deadline or a universal upstream fix.

All writes require a per-process token and localhost Host/Origin. Dashboard password is generated and protected by Windows DPAPI. Logs/router database can contain sensitive data; never distribute them. Kiro endpoint writes require inference success and refuse existing foreign bridges; restoration preserves unrelated settings and refuses endpoint changes made after activation. Closing the browser leaves the service owner running; explicit Stop restores endpoints and stops only owned child processes.

Run `python -m unittest discover -s tests -v`. Live tests require deliberately supplied upstream credentials and must use an isolated data directory and fake settings. Portable build: `python build_portable.py --output <fresh-folder> --node <node.exe> --router <package-root>`; retain third-party licenses. This is a source project, not a signed installer.
