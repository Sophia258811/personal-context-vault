# Personal Context Vault

Windows 用户可双击 `Start-PCV.cmd` 合并启动接收和入库；首次安装与配对见 [一键启动说明](docs/QUICK_START.md)。

**Experimental prototype v0.2.0 — not production-ready.** Python 3.11+，无 Python 第三方运行依赖。

本项目探索跨平台、多来源的个人信息理解：本地导入与浏览器伴随采集 → 证据库 → Agent 摘要 → 待确认画像。代码公开，用户数据保留在本机。

安装：`python -m pip install .`；运行：`pcv --help`。开发测试：`python -m unittest discover -s tests -v`；扩展逻辑测试：`node tests/browser_background.cjs`。

[2026-11-01 开发总结](docs/PROGRESS_2026-11-01.md) · [浏览器扩展](docs/BROWSER_COMPANION.md) · [Agent 接口](docs/AGENT_HANDOFF.md) · [跨来源设计](docs/MULTISOURCE_DESIGN.md)

Personal Context Vault（PCV）是一个本地优先的个人上下文原型。目标不是自动抓取全部账号，而是建立可授权、可追溯、可复核、可删除的个人记忆。

当前 v0.2 已能跑通证据闭环，并从多个平台的本地导出目录增量采集：

`来源授权 -> 原始证据 -> 事实/推断账本 -> 人工复核 -> 已确认画像导出`

## 核心约束

- 没有授权来源，不允许写入证据。
- 没有证据，不允许创建事实声明。
- 直接事实、观察、推断、偏好和关系严格分型。
- 只有已确认、且仍有关联有效证据的声明进入画像。
- 撤销或删除来源后，失去证据支持的声明自动转为 `needs_review`。
- 审计日志不记录证据正文或密钥。
- 文件连接器读取授权的本地导出；浏览器扩展将主动开启页面的可见文字发送至本机接收端，不读取 Cookie。

## 快速开始

需要 Python 3.11 或更高版本。仓库内的 `pcv.ps1` 会自动查找系统 Python
或当前 Codex 运行时 Python。

```powershell
cd personal-context-vault
.\pcv.ps1 --db .pcv/vault.db init
.\pcv.ps1 --db .pcv/vault.db source add `
  --name "本人导出的聊天记录" `
  --kind chat_export `
  --scope "本人对话" `
  --basis user_consent
.\pcv.ps1 --db .pcv/vault.db evidence add `
  --source 1 `
  --title "个人偏好陈述" `
  --content "我更喜欢先给结论，再给细节。" `
  --sensitivity low
.\pcv.ps1 --db .pcv/vault.db claim add `
  --statement "用户偏好先结论后细节" `
  --type preference `
  --evidence "1:supports" `
  --confidence 0.9 `
  --sensitivity low
.\pcv.ps1 --db .pcv/vault.db claim review --id 1 --status confirmed
.\pcv.ps1 --db .pcv/vault.db export --format markdown
```

## 多平台持续采集

新增 [Agent 文件任务接口](docs/AGENT_HANDOFF.md)：将指定证据交给当前 Agent，总结结果经哈希及来源校验后入库，画像候选保持待确认。

新增实验性 [浏览器伴随采集](docs/BROWSER_COMPANION.md)：Chrome/Edge 扩展按用户选择记录可见页面片段，本机接收端将事件送入现有导入流程。

支持 `ChatGPT conversations.json`、现有抖音快照、通用 JSON、Markdown 和纯文本。
`auto` 会按文件名和结构识别格式；同步使用外部 ID 与内容哈希去重。

```powershell
.\pcv.ps1 --db .pcv/vault.db source add `
  --name "本人授权的平台导出" `
  --kind platform_export `
  --scope "本人提供的本地导出文件" `
  --basis user_consent

.\pcv.ps1 --db .pcv/vault.db connector add `
  --source 1 `
  --name "导出收件箱" `
  --platform multi `
  --format auto `
  --access-mode local_export `
  --content-scope self_authored `
  --path "C:\personal-context-inbox"

.\pcv.ps1 --db .pcv/vault.db connector sync
.\pcv.ps1 --db .pcv/vault.db connector watch --interval 60
```

`watch` 是前台轮询进程，可用 `Ctrl+C` 停止。新增或修改文件会形成新证据；未变化内容会跳过。
ChatGPT 适配器默认只导入用户消息，避免把模型回答误认为用户事实。抖音适配器保留
`recommendation/history/likes/favorites/comments` 事件类型，推荐曝光不会被当成主动偏好。

采集权限由用户逐连接器选择：

- `--access-mode local_export`：默认，只读取本地导出文件。
- `--access-mode official_api`：为后续官方 API/OAuth 适配器记录授权方式。
- `--access-mode browser_assisted --allow-session-use`：允许适配器临时使用用户已登录的浏览器会话；不得导出或保存 Cookie。
- `--content-scope self_authored`：默认，只导入本人内容。
- `--content-scope all_authorized`：导入用户确认有权处理的全部内容，并保留发言者角色，不能把他人或 AI 内容归属于用户。

即使选择全部内容，密码、验证码、账户恢复信息、支付凭证和身份认证材料也不进入系统。
当前文件连接器已实现，browser_assisted 可配合实验性浏览器扩展与本机接收端使用。official_api 目前仅为配置字段，没有 API 采集实现；不要将选中此字段视为已接入平台。

命令帮助：

```powershell
.\pcv.ps1 --help
.\pcv.ps1 claim --help
```

## 测试

```powershell
.\test.ps1
```

## 项目文档

- [架构](docs/ARCHITECTURE.md)
- [技术需求](docs/TECHNICAL_REQUIREMENTS.md)
- [实施路径](docs/ROADMAP.md)
- [隐私与安全](docs/PRIVACY_AND_SECURITY.md)
- [示例 Skill](skills/personal-context-vault/SKILL.md)

## 当前限制

- SQLite 中的证据正文尚未做应用层加密，只适合个人机器上的受控原型。
- 已有本地文件轮询、浏览器文字采集、经用户启动的短段音频和关键帧采样，以及 Agent 文件交接。音画采样为实验版，完整视频录制、OAuth/官方 API 采集、系统后台服务、自动模型调用、向量检索及完整复核界面尚未实现。
- 当前内容去重按连接器及外部 ID/哈希执行；相同外部 ID 的更新可能被跳过。watch 遇到部分连接器错误可能退出，尚无可靠任务队列。
- 可见正文可能包含第三方评论与敏感信息；表单排除不能保证正文脱敏。数据库和原始文件尚未加密。
- 来源删除不会清理原始收件箱或 Agent 文件副本；公开仓库不含这些数据。
- `confidence` 是人工填写的整数/浮点值，不代表经过统计校准。
- 删除和审计机制需要经过独立安全审查后才可用于多用户或商业环境。
