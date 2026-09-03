# Agent 使用与验收

## 入口与前提

LetsAIGC 的 Agent 是产品；Speckit/Codex 的 Development Harness 只约束开发。
保留现有专家命令。以下命令从仓库根目录在 PowerShell 执行，Miniforge/Mamba
需已在 PATH。首次安装先创建核心环境：

```powershell
.\scripts\bootstrap.ps1 -Component core
mamba run -n letsaigc-core letsaigc --json doctor
mamba run -n letsaigc-core letsaigc agent --help
```

已有核心环境时，可用 `mamba run -n letsaigc-core python -m pip install -e .`
更新可编辑安装，无需安装到全局 Python。

用户自行在进程环境或忽略的 `.env` 配置 `OPENAI_API_KEY`。`doctor` 只报告是否配置；
不会验证账户、接受条款或发起付费请求。默认 Responses 模型 `gpt-5.6-luna`，
`medium` reasoning、`store=false`，标准服务层、关闭 SDK 自动重试。
模型名称可通过 `LETSAIGC_AGENT_MODEL` 覆盖，但必须同时维护匹配的已审查定价；
缺少价格时拒绝请求，不会擅自用 Luna 费率结算其他模型。

本地和远端路线的规划都调用 Responses，可能计费。传入预算文件即限定此次规划费用：
预留为 `$0.03 + 每张输入 $0.01`，同时受单轮和总预算限制。
规划阶段会受控地暂存已验证的输入和保存会话/任务记录；这不赋予 Agent 任意写入权限，
也不批准媒体生成。生成前显示包括初次生成和全部允许修订在内的最坏预留。

随附 `budget-local.yaml` 和 `budget-remote-low.yaml` 都设置 `max_revisions: 3`，
即初始生成后最多修订 3 次。`TaskBudget` 类型在该字段省略时默认 10，允许的最大值
也是 10；实际执行受本次批准的修订次数及费用/GPU 预算限制。

## 规划结果与批准

成功的 `agent plan` 返回 `awaiting_approval`。检查计划中的模型、输入、参数、
输出上限和预算后，使用返回 JSON 的以下顶层字段：

| 字段 | 用途 |
|---|---|
| `id` | 任务标识，用于 `execute`、`inspect`，替换示例中的 `TASK_ID` |
| `plan_fingerprint` | 完整计划的 SHA-256 指纹，替换 `PLAN_FINGERPRINT` |
| `session_id` | 会话标识，用于后续 `plan --session` 或 `chat --session` |

`agent run` 的人类模式会显示计划并询问是否执行；JSON 模式的 `agent run` 只规划。
`agent execute TASK_ID --approve PLAN_FINGERPRINT` 在 JSON 模式下也会执行已批准的
任务。`agent chat` 是交互入口，不支持 `--json`。每个新媒体任务都须分别批准。

## 本地图片与编辑

先由操作者部署锁定 ComfyUI、同步 SDXL，再启动服务：

```powershell
.\scripts\bootstrap.ps1 -Component comfy
mamba run -n letsaigc-core letsaigc models sync production-sdxl
mamba run -n letsaigc-core letsaigc comfy serve
```

保持服务终端运行，在另一个终端从仓库根目录执行以下步骤。Agent 不启动服务、
不下载模型。图片编辑示例中的本地路径需替换为用户有权使用的文件。

```powershell
mamba run -n letsaigc-core letsaigc --json agent plan `
  "一个居中的蓝色药水图标" --backend comfy --budget configs/agent/budget-local.yaml
```

先按“规划结果与批准”检查返回值并替换占位符，再执行：

```powershell
mamba run -n letsaigc-core letsaigc --json agent execute TASK_ID --approve PLAN_FINGERPRINT
mamba run -n letsaigc-core letsaigc --json agent inspect TASK_ID
```

以下图片编辑会创建另一个待批准任务：

```powershell
mamba run -n letsaigc-core letsaigc --json agent plan `
  "保留轮廓，改成水彩风格，denoise 0.45" --image C:/assets/potion.png `
  --backend comfy --budget configs/agent/budget-local.yaml
```

默认本地图片路线是 SDXL。单输入 img2img 使用 `LoadImage → VAEEncode → KSampler`。
SD1.5 recipe 已提供给编译器扩展使用，默认 Agent 路由不降级到 SD1.5。
本地 recipe 不会合并多参考图、不保证直接输出透明背景；这类请求会提出 OpenAI 计划，
显式指定不支持的 backend 则失败，不会悄悄替换。

本地编译发生在 `execute` 阶段：校验模型、上传派生输入、编译并验证图，然后提交
ComfyUI 生成。`plan` 不产生编译图，当前 CLI 没有独立“只编译”入口。
执行后通过 `agent inspect TASK_ID` 的 `iterations[].candidate.run_id` 找到对应子运行，
读取 `.local/runs/CHILD_RUN_ID/manifest.json` 的 `source.compiled_graph_path`、
`compiled_contract_path` 及对应 SHA-256 字段。编译文件位于
`.local/agent/sessions/SESSION_ID/tasks/TASK_ID/compiled/`；编译前失败可能没有这些产物。
完整步骤见 [编译与证据 quickstart](../specs/011-constrained-comfy-compiler/quickstart.md)。

## 远端图片

使用核心环境、已配置的 `OPENAI_API_KEY` 和远端预算即可；此路径无需本地 ComfyUI
或模型权重。下面 `agent run` 会在展示计划后询问是否执行，`agent plan` 则只返回计划。

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

本地视频另需用户维护的系统 `ffmpeg`、`ffprobe`，以及运行中的锁定 ComfyUI。
由操作者同步 Wan2.1 后再规划：

```powershell
mamba run -n letsaigc-core letsaigc models sync video-local-smoke
```

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

- `agent plan` 和 JSON `agent run` 只规划；JSON `agent execute --approve` 执行已批准任务。
- `agent chat --session SESSION_ID` 恢复本地文本会话；带新图片的任务使用
  `agent plan --session SESSION_ID --image SOURCE`。每个媒体任务分别批准。
- `agent inspect TASK_ID` 或 `agent inspect SESSION_ID` 不访问 provider。
- 待批准任务可在新进程执行。正在执行时崩溃的任务不自动重放，防止重复扣费；
  检查 task、manifest、provider request ID 和 Comfy 队列后，再新建计划。
  遗留 `execution.lock` 不会被自动删除，需确认没有同任务进程后由操作者处理。
- 指纹绑定模型、recipe、关键配置、原图和派生图哈希；更改后旧批准失效。
- 一次初始生成加批准预算内的修订次数，硬上限为 10，随附配置为 3。
  每轮先预留费用/GPU 时间，返回后按 usage 结算；
  通信中断或缺少 usage 会转入 `unsettled_*`，占用总预算且不自动重试。
- 本地超时只对已知的本任务 prompt ID 请求取消，不全局中断或终止 GPU 进程。
  取消未确认会记入 manifest，需操作者检查队列。
- 自动评审通过不等于人工批准。生产导出仍要求许可、已提交的 recipe、有效哈希、
  完整溯源、全部门禁与人工批准；实验模型不能借 Agent 父运行绕过资格检查。
- `.local` 中会话、批准记录、输入副本、编译图、运行 manifest 和输出共同组成恢复与
  审计证据；保留这些关联文件。可重建的运行时或缓存需与证据数据区别管理。

## 验证证据与尚待验收

状态基线为 2026-09-03 汇总到 `master` 的提交 `fb52511`。重构实施阶段的离线测试
记录覆盖真实 OpenAI SDK + MockTransport（无网络）、fake provider/Comfy、
批准/越权/预算/十次修订上限、输入安全、动态工作流、媒体谱系及旧接口回归；
这些历史记录不代表每次文档修订都重新完成了验证，也不代表新 Agent 链路的实机验收。

| 链路或验收项 | 状态与范围 | 依据 |
|---|---|---|
| 001–004 专家图片/LoRA 链路 | 2026-08-31 工程烟测已有记录；资产生产批准仍单独处理 | [历史任务](../specs/001-project-harness/tasks.md) |
| 005–007 专家视频/Sprite/短剧链路 | CPU/FFmpeg 与 Wan2.1 GPU 烟测已有记录；首个真实 Sprite 样例未通过资产质量门禁 | [视频实测记录](../specs/005-video-runtime-models/quickstart.md) |
| Agent 代码与离线回归 | 已交付并有离线验证记录；不等同于真实生成及视觉修订验收 | [009 任务](../specs/009-agent-core-approval/tasks.md)、[012 任务](../specs/012-agent-critique-media/tasks.md) |
| GPT Image 2 文生图/编辑 | 待真实请求，核对 request ID、usage、费用与输出哈希 | [010 T012 LIVE](../specs/010-generation-backends-assets/tasks.md) |
| Agent SDXL T2I/I2I、Wan2.1 512×512/17 帧 | 待批准预算下的本机验收，包括图生图效果 | [011 T011 LIVE](../specs/011-constrained-comfy-compiler/tasks.md) |
| 至少一次真实多模态评审与修订 | 待批准预算下完成；已有专家视频烟测不能替代 | [012 T013 LIVE](../specs/012-agent-critique-media/tasks.md) |
| Wan2.2 5B I2V | 可选实验验收，未完成，不阻断稳定链 | [011 T012 OPTIONAL LIVE](../specs/011-constrained-comfy-compiler/tasks.md) |

上述 LIVE 项须在用户批准预算和所需 GPU 可用后另行执行。只有取得相应实机证据后，
才更新任务勾选及本表状态；文档修订本身不改变验收结果。

官方参考：[Responses](https://developers.openai.com/api/docs/guides/text)、
[严格结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)、
[GPT Image 2](https://developers.openai.com/api/docs/models/gpt-image-2)、
[定价](https://developers.openai.com/api/docs/pricing)。
