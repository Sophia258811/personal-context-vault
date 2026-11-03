# 浏览器伴随采集（实验版）

推荐使用 [一键启动流程](QUICK_START.md)。新版扩展保存站点范围与配对，授权范围内的新标签自动续采；下文逐进程命令为高级手动运行方式，临时配对码只适用于手动接收端。

扩展 0.3.0 新增平台内容 ID、页面分区与播放器时间字段，详见 [整合设计](MULTISOURCE_DESIGN.md)。升级时先暂停采集，在扩展管理页面重新加载扩展，再刷新待采集网页、重启本机接收端并使用新配对码开启采集。旧接收进程不会自动加载新 Python 代码。

本机接收端在后台运行，扩展随用户浏览保存当前可见正文。每 15 秒检查一次；内容未变则不发送。页面暴露不等于阅读、赞同或偏好，不自动创建画像声明。

## 启动

在项目目录运行（Python 3.11+）：

```powershell
python -m pcv.companion --inbox .pcv/browser-inbox
```

保持此终端运行。接收端只监听 127.0.0.1:8765；每次启动生成新的随机配对码。

在 Chrome 的 chrome://extensions 或 Edge 的 edge://extensions 开启开发者模式，选择“加载解压缩的扩展程序”，选择项目的 browser-extension 文件夹。

打开网页，点击扩展，首次粘贴配对码，选择当前站点或所有网站，点击“开始／添加范围”并确认权限。授权范围内的新标签、导航和刷新自动续采。仅采集可见且有焦点的文档，不采集隐藏标签或隐身窗口。点击“暂停全部”停止发送；关闭接收端停止接收。扩展保存配对与暂停状态；手动接收端重启仍需更新临时配对码，一键启动器则复用本机加密保存的配对码。

## 接入现有增量导入

```powershell
.\pcv.ps1 init
.\pcv.ps1 source add --name "伴随浏览" --kind browser --scope "主动开启的网页可见内容" --basis user_consent
# 用上一条返回的 source_id 替换下面的 1。
.\pcv.ps1 connector add --source 1 --name "浏览器片段" --platform browser --format json --path .pcv/browser-inbox --access-mode browser_assisted --allow-session-use --content-scope all_authorized
.\pcv.ps1 connector watch --interval 15
```

接收端与 watch 各在一个终端运行。前者写原子 JSON 文件，后者去重后加入证据库。当前后台指独立运行的本机进程，不包含开机自启或系统服务安装。

## 当前边界

- 不读取表单、密码框、可编辑区域、Cookie、浏览器存储；URL 查询参数和片段被移除。普通正文仍可能包含敏感信息，应只在愿意记录的页面开启。
- 原始片段与数据库尚未加密；配对码仅用于本机接收端认证，不能替代磁盘加密。
- 支持当前标签在同一站点跨页面连续记录；暂不覆盖跨站自动授权、iframe、视频声音、图片 OCR、浏览器历史、AI 实时陪聊或主动建议。
- 保存失败会显示在扩展弹窗；无持久化发送队列，下次采样会重试当前内容，离开页面可能丢失未发送片段。
- 删除来源只删除数据库中的证据；原始 inbox 文件需要另行删除，否则重新注册来源可能再次导入。
- 代码可在不发布私人数据的情况下分享；.pcv 下资料保持本地。

## 验证

真实 HTTP 集成测试：`python -m unittest discover -s tests -p test_companion.py -v`。
扩展后台逻辑模拟测试：`node tests/browser_background.cjs`。
这些检查覆盖认证、输入验证、原子落盘、增量导入、撤销、跳转和暂停。模拟测试不等于实际浏览器验收；安装后仍需检查站点权限弹窗、页面 DOM 采集和扩展生命周期。
