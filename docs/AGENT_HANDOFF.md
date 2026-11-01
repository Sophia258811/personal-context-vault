# Agent 文件任务协议 v1

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

当前支持文字任务。input_modalities 与 available_media 明确表示实际输入；视频画面、声音的录制、上传与转写尚未实现。不能用页面已有摘要冒充音视频分析。

文件任务副本不受数据库删除级联控制：撤销来源会阻止结果入库，但已生成的任务/结果文件仍需用户清理；已提交给外部 Agent 的上下文无法由本程序撤回。输入中的提示和代码只作为数据，结果经结构校验；语义真实性仍需复核。

验证：`python -m unittest discover -s tests -p test_agent_tasks.py -v`。
