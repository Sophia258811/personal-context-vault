# Agent 文件任务协议 v1

## 任务上下文与修订

新会话可调用 `pcv context --term "数组边界" --limit 5`。重复传入 `--term` 可补充关键词。该只读入口返回任务相关的已确认条目及证据标识，排除高敏、过期和无有效支持的条目。证据正文不随查询返回。

Agent 用现有任务接口或 `claim add` 提交待确认候选。用户检查新旧条目和证据后执行 `pcv claim review --id NEW --status confirmed --replaces OLD`。确认与将旧条目标记为 stale 在同一事务中完成。失败会保留旧条目。之后重新调用 context 即可取到修订结果。Agent 需要显式接入此命令，当前不会自动注入新对话。

## Agent 自助查询

本地 stdio MCP 服务只提供 `get_task_context`。调用参数为 `terms`（1–10 个关键词）和可选的 `limit`（1–5 条，默认 5）。启动参数固定资料库，调用者不能改路径。返回任务匹配的已确认条目和证据 ID，沿用 CLI 的敏感度、有效期和来源检查。空列表表示没有匹配的有效记忆。

在 Codex 的项目级 `.codex/config.toml` 配置以下内容，将占位路径替换为实际绝对路径。保留调用确认，以便控制哪些上下文进入 Agent 会话。

```toml
[mcp_servers.pcv]
command = 'C:/path/to/python.exe'
cwd = 'C:/path/to/personal-context-vault'
args = ["-m", "pcv.context_mcp", "--db", "C:/path/to/vault.db"]
default_tools_approval_mode = "prompt"
```

另开会话，要求调用 PCV 工具查询某个明确主题。随附技能优先调用 MCP。已登录 Codex CLI 的用户可在项目目录运行 `python -m tests.agent_context_smoke`，用合成资料库验收临时会话是否实际调用工具并采用修订结果。真实资料库路径和用户配置应留在本机。

用户选择使用当前 Agent 处理已采集内容。PCV 不读取 Agent 的登录凭证，也不假定桌面订阅能作为 API 调用。不同 Agent 只要能读取任务 JSON、写入结果 JSON，即可复用此协议。当前交接由用户或 Agent 调用命令推进，尚无自动唤醒或后台模型调用。

在项目目录使用 Python 3.11+（或配置 PCV_PYTHON 指向解释器）：

```powershell
python -m pcv.agent_tasks prepare --evidence 11
# Agent 读取返回的 .task.json，按其中 schema 写入对应 .result.json。
python -m pcv.agent_tasks accept --result .pcv/agent-tasks/TASK_ID.result.json
python -m pcv.agent_tasks status
```

prepare 包含证据正文，会交给所选 Agent 处理；使用云端 Agent 意味着正文进入该 Agent 的处理上下文。请仅交接授权范围内的来源。任务文件默认位于被 Git 忽略的 .pcv 内，包含私人数据，不应发布。

accept 核验任务、证据哈希及来源状态，然后在单个事务内保存摘要和候选推断。重复提交不会重复写入。摘要与原始证据使用同一来源，撤销/删除来源沿用原有治理机制。推断始终 pending、high，confidence=0 表示尚未校准，不能将模型自报分数当成统计概率。

## 主动分享图片与短音频

在 Windows 的项目目录使用下列命令。先在 PCV 注册一个明确授权的来源，用实际来源 ID 替换 SOURCE。每次导入一个你选定的文件。

```powershell
python -m pcv.media add --source SOURCE --file "C:/path/to/sample.png" --title "练习截图"
python -m pcv.media analyze --evidence ORIGINAL_ID --culture zh-CN
python -m pcv.agent_tasks prepare --evidence DERIVED_ID
```

`add` 保存 PNG/JPEG（最多 10 MiB）或短 WAV（16 位 PCM，单声道或立体声，最高 48 kHz，最长 120 秒），返回原件证据 ID。`analyze` 调用已安装的 Windows OCR 或语音识别语言，返回派生证据 ID。英文 OCR 可指定 `en-US`，中文 OCR 或转写可指定 `zh-CN`，省略时由本机语言配置选择。脚本以当前子进程的 RemoteSigned 策略执行，识别在本机完成。

用派生证据创建 Agent 任务，再按上述 `accept` 流程导入总结。任务包含机器生成的文字、原件 ID、SHA-256、处理器和定位。OCR 框为原图归一化的 `[left, top, right, bottom]`，转写段为从录音开始计算的秒数。Agent 收到的是文本和定位信息，`available_media` 描述原件，`input_modalities` 仍为 `text`。OCR 和转写可能出错，按原图或录音核对。

原件在本机资料库中保存，撤销来源会阻止继续处理，删除来源会移除库中的原件、派生证据和摘要。已交接的任务文件与外部副本需另外管理。

## 流媒体片段理解

浏览器音画片段由 PCV 窗口自动接收和识别。Windows 本地识别完成后，用下列命令查找 `analysis_id`，再为指定片段准备 Agent 任务。

```powershell
python -m pcv.stream_processing --limit 10
python -m pcv.agent_tasks prepare --evidence ANALYSIS_ID
```

任务含视频 ID、播放范围、关键帧 OCR、音频转写和缺失说明。关键帧定位是视频时间，音频转写时间是录音片段内的偏移。自动识别在本机运行，Agent 总结由用户或已授权的任务运行器触发。

如需理解画面本身，从任务的 `available_media` 选择一个仍可用的 image 证据 ID。运行 `python -m pcv.agent_tasks prepare --evidence ANALYSIS_ID --frame FRAME_ID`，把生成的任务 JSON 与相邻的 `.frame.jpg` 一并交给支持视觉的 Agent。Agent 实际查看后在结果里附 `observed_frame_id`，入库时会核对它是该任务指定的帧。音频仍通过转写理解，关键帧不是连续视频。任务目录里的显式导出副本不随数据库的 24 小时清理删除。

文件任务副本不受数据库删除级联控制：撤销来源会阻止结果入库，但已生成的任务/结果文件仍需用户清理；已提交给外部 Agent 的上下文无法由本程序撤回。输入中的提示和代码只作为数据，结果经结构校验；语义真实性仍需复核。

验证：`python -m unittest discover -s tests -p test_agent_tasks.py -v`。
