# Agent 使用与验收

## 入口与前提

LetsAIGC 的 Agent 是产品；Speckit/Codex 的 Development Harness 只约束开发。
保留现有专家命令。以下命令从仓库根目录在 PowerShell 执行。

```powershell
mamba run -n letsaigc-core python -m pip install -e .
mamba run -n letsaigc-core letsaigc --json doctor
mamba run -n letsaigc-core letsaigc agent --help
```

用户自行在进程环境或忽略的 `.env` 配置 `OPENAI_API_KEY`。`doctor` 只报告是否配置；
不会验证账户、接受条款或发起付费请求。默认 Responses 模型 `gpt-5.6-luna`，
`medium` reasoning、`store=false`，标准服务层、关闭 SDK 自动重试。
模型名称可通过 `LETSAIGC_AGENT_MODEL` 覆盖，但必须同时维护匹配的已审查定价；
缺少价格时拒绝请求，不会擅自用 Luna 费率结算其他模型。

规划也调用 Responses，可能计费。传入预算文件即限定此次规划费用：
预留为 `$0.03 + 每张输入 $0.01`，同时受单轮和总预算限制。
生成前显示包括初次生成和全部允许修订在内的最坏预留。

## 本地图片与编辑

先自行启动锁定 ComfyUI；Agent 不启动服务、不下载模型。

```powershell
mamba run -n letsaigc-core letsaigc --json agent plan `
  "一个居中的蓝色药水图标" --backend comfy --budget configs/agent/budget-local.yaml

mamba run -n letsaigc-core letsaigc --json agent execute TASK_ID --approve PLAN_FINGERPRINT

mamba run -n letsaigc-core letsaigc --json agent plan `
  "保留轮廓，改成水彩风格，denoise 0.45" --image C:/assets/potion.png `
  --backend comfy --budget configs/agent/budget-local.yaml
```

默认本地图片路线是 SDXL。单输入 img2img 使用 `LoadImage → VAEEncode → KSampler`。
SD1.5 recipe 已提供给编译器扩展使用，默认 Agent 路由不降级到 SD1.5。
本地 recipe 不会合并多参考图、不保证直接输出透明背景；这类请求会提出 OpenAI 计划，
显式指定不支持的 backend 则失败，不会悄悄替换。

## 远端图片

```powershell
mamba run -n letsaigc-core letsaigc agent run `
  "透明背景的药水图标，低质量 1024x1024" --backend openai `
  --budget configs/agent/budget-remote-low.yaml

mamba run -n letsaigc-core letsaigc --json agent plan `
  "保持形状，把药水改成紫色" --image https://example.org/potion.png `
  --backend openai --budget configs/agent/budget-remote-low.yaml
```

URL 是占位示例，需换成用户有权使用的图片。支持本地/HTTPS PNG、JPEG、WebP，最多 8 张，
每张 25 MiB；原图不修改。编辑使用本地验证后的派生 PNG，最长边 1536px，删除元数据。
隐私边界：`store=false` 不是零数据保留声明；图像/提示词仍会传输给 OpenAI，
适用其 API 条款与账户数据控制。原始 URL 的 query/fragment 不进入资产记录。

Image API 固定 `gpt-image-2-2026-04-21`，每轮一张；支持三种 1024/1536 尺寸、
low/medium/high、auto/opaque/transparent。定价版本过期后必须人工复核更新。
规划尺寸/质量由自然语言提出，并以返回计划为准；提高这些上限必须重新规划批准。

## 视频与后处理

```powershell
mamba run -n letsaigc-core letsaigc --json agent plan `
  "生成绿色背景上的史莱姆跳跃视频，512x512，17帧，16 FPS" `
  --backend comfy --budget configs/agent/budget-local.yaml

mamba run -n letsaigc-core letsaigc --json agent plan `
  "把运行 VIDEO_RUN_ID 制作成序列帧，profile 使用 configs/sprites/general-rgba-512.yaml" `
  --budget configs/agent/budget-local.yaml
```

Wan2.1 T2V 是稳定 recipe；Wan2.2 5B I2V 为 experimental，不作为发布阻断项。
视频评审使用 ffprobe 与固定间隔联系表，不向 Luna 上传视频二进制。
Sprite/短剧作为**单独批准的派生任务**，不在未批准的生成任务内隐式启动。
短剧 Agent 工具只拼接已记录镜头；含内联 workflow 的项目必须先分别批准并生成镜头，
或由用户显式使用保留的专家 `drama render` 命令。该限制防止后处理绕过 GPU 预算。
媒体配置、输入 run、音频/字幕与参考文件均需位于仓库内并以哈希固定。

## 修订、恢复与生产门禁

- `agent run` 人类模式展示计划后确认；`--json` 只规划，不询问、不执行。
- `agent chat --session SESSION_ID` 恢复本地文本会话；带新图片的任务使用
  `agent plan --session SESSION_ID --image SOURCE`。每个媒体任务分别批准。
- `agent inspect TASK_ID` 或 `agent inspect SESSION_ID` 不访问 provider。
- 待批准任务可在新进程执行。正在执行时崩溃的任务不自动重放，防止重复扣费；
  检查 task、manifest、provider request ID 和 Comfy 队列后，再新建计划。
  遗留 `execution.lock` 不会被自动删除，需确认没有同任务进程后由操作者处理。
- 指纹绑定模型、recipe、关键配置、原图和派生图哈希；更改后旧批准失效。
- 一次初始生成加最多 10 次修订。每轮先预留费用/GPU 时间，返回后按 usage 结算；
  通信中断或缺少 usage 会转入 `unsettled_*`，占用总预算且不自动重试。
- 本地超时只对已知的本任务 prompt ID 请求取消，不全局中断或终止 GPU 进程。
  取消未确认会记入 manifest，需操作者检查队列。
- 自动评审通过不等于人工批准。生产导出仍要求许可、已提交的 recipe、有效哈希、
  完整溯源、全部门禁与人工批准；实验模型不能借 Agent 父运行绕过资格检查。

## 验证证据与尚待验收

自动测试涵盖真实 OpenAI SDK + MockTransport（无网络）、fake provider/Comfy、
批准/越权/预算/十次修订上限、输入安全、动态工作流、媒体谱系及旧接口回归。
截至 2026-09-03，本次没有调用付费生成、运行满载 GPU 烟测、下载模型或创建 Git commit。

以下是仍需用户预算和空闲 GPU 的实机验收，不能用离线测试代替：

1. OpenAI 低质量文生图和图片编辑，核对 request ID、usage、费用及输出哈希。
2. SDXL 文生图/图生图，比较 denoise 对原图保留程度的影响。
3. Wan2.1 512×512/17 帧视频与至少一次视觉评审修订。
4. Wan2.2 5B I2V 只记实验结果，不阻断稳定链。

官方参考：[Responses](https://developers.openai.com/api/docs/guides/text)、
[严格结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)、
[GPT Image 2](https://developers.openai.com/api/docs/models/gpt-image-2)、
[定价](https://developers.openai.com/api/docs/pricing)。
