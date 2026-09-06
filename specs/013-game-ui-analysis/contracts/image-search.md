# 图片搜索、额度及恢复契约

状态：待实现。核心映射 FR-030—048、U-V11—U-V14；公开资料和选择依据见 [research.md](/C:/Programs/LetsAIGC/specs/013-game-ui-analysis/research.md)。

本合同随首版 T011—T018 实现双后端单图搜索和额度切换，不等待分割、补图或批次。首版必须分别取得两家真实搜索图片并送入同一 OCR/VLM 解析链路，供应商单独可用不代表双后端预览已验收。

## 请求和内部候选

`ImageSearchProvider` 暴露 `search(SearchRequest) -> SearchReceipt`、`quota(QuotaRequest) -> QuotaObservation`；仅 acquisition/额度 Activity 使用。请求携带已固定 operation_id、logical_query_id、供应商/策略版本、上限和计价引用。适配器从当前进程环境取得凭据，进程环境优先于运行仓库的本地 `.env`；不复制其他工作区凭据，不改变 LETSAIGC_ROOT。

| 供应商 | 请求 | 参数与凭据 |
|---|---|---|
| SerpApi | GET `https://serpapi.com/search.json` | `engine=google_images`、`q`、`ijn=0`、`no_cache=true`；`SERPAPI_API_KEY`；本地截断最多20候选 |
| Tavily | POST `https://api.tavily.com/search` | Bearer `TAVILY_API_KEY`；`query`、`search_depth=basic`、`auto_parameters=false`、`include_images=true`、`include_image_descriptions=true`、`include_answer=false`、`include_raw_content=false`、`include_usage=true` |

Tavily `max_results` 只约束网页结果，本地另行限制图片候选。查询条件中的尺寸和宽高比冻结，不全局丢弃竖屏截图。只请求第一页，不自动升级深度。实施时明确：首版将SerpApi的`serpapi_no_cache=true`冻结到路由策略及批准摘要，以已知的成功请求一次搜索计费；不凭响应耗时猜测缓存命中。若后续启用缓存，须先补可验证的缓存用量证据合同并形成新计划。

内部候选字段：candidate_id、backend、瞬时 original_url、安全 source_page?、title/description 及其声明来源、declared_width/height?、查询及供应商 request_id。下载 URL 不是可持久返回字段。SerpApi 优先 `images_results[].original/link/original_width/original_height`；Tavily 接收顶层 images 及来源 results 中的 images，缺失关联保留 unknown。

只将经过受控下载、格式/尺寸检查、精确及近似去重、相关性筛选的图片转为 UISource。精确去重用 SHA-256；搜索近似去重使用固定版本的 64-bit 感知哈希、Hamming 距离不大于6，阈值和排除记录进策略 hash。manual 不使用此删除规则。

## 额度读取与归一化

| 供应商 | 接口与范围 | 白名单/解释 |
|---|---|---|
| SerpApi | GET `https://serpapi.com/account.json`，账户范围 | plan_searches_left、extra_credits、total_searches_left、searches_per_month、this_month_usage、this_hour_searches、account_rate_limit_per_hour、plan_renewal_date及必要套餐标识；不存api_key/email/完整响应 |
| Tavily | GET `https://api.tavily.com/usage`，不发送 X-Project-ID | account.plan_usage/plan_limit/paygo_usage/paygo_limit、key.usage/limit、必要套餐及版本；若未来增加项目范围必须同步实际请求与账本scope |

SerpApi Account 官方明确不消耗搜索额度；额外 credits 与套餐池分别标记，不默认称免费。Tavily 已知数值时 `plan_remaining=max(0,plan_limit-plan_usage)`，再与有效 key 剩余取最小值；PAYGO 池默认排除。key.limit 的零/null 特殊语义没有经过验证时保持 unknown，不当无限。可靠的明确余额零为 exhausted；字段语义未知与余额零必须区分。

2026-09-06核实[官方Usage OpenAPI](https://docs.tavily.com/documentation/api-reference/endpoint/usage.md)：显式 `key.limit=null` 表示该key无独立上限；key.usage为有效非负整数时，仅以账户套餐剩余额度准入，key_remaining保持null，不伪造无限数值。字段缺失、零或类型异常仍为unknown；账户额度未知不准入，套餐用尽为exhausted，PAYGO仍排除。

每个适配器的 pricing 配置必须含搜索和 probe 计价依据、保守单次金额上界以及条款版本；未知不记零。Tavily Usage 不在本计划中假定免费；T011/T014 先核实并记录其计价依据，未就绪时该 probe 能力返回 pricing_unavailable，manual 与另一家已合格搜索仍可用。

首版的根预算身份就是单任务 task_id，五张额度表在账本 v3 加入，共享额度不依赖后续的父子预算表。

账户scope用本地随机稳定ID映射，不能把邮箱或 API key 当scope。不同key共享账户额度，明确的key上限另作约束。密钥变更使健康/快照失效，但既有操作的scope、供应商和金额记录保持不变。

## 共享快照、预留与限频

策略固定为 quota_aware，allowed_providers=[serpapi,tavily]、preferred_provider=serpapi、allow_payg=false：

- 75秒最短刷新间隔，快照300秒有效；每任务每家最多2次外部probe。共享缓存命中不计probe。
- 同scope刷新合并；Tavily Usage同scope每10分钟最多10次。记录在公共账本，跨Worker/重启仍限制；不能每个任务单独算一套账户限频。
- 有效可用量 = 最新可信余额 − 在途/未知/尚未证实已被快照覆盖的本地用量，按 search/credit 分开计数。
- 只有可定位的提供方证据或已验证的独占账户观测规则能推进 covered watermark；只凭时间更晚或账户总量变化不能证明具体本地费用已覆盖。无法证明时继续保守扣除。
- 选路、根预算/次数检查、route记录、额度预留及新操作准备在同一个账本事务完成，I/O在事务之外。
- 月度重置只更新额度观测，不清空任务费用、未知操作或原根预算。未知重置日期不得推断为月初。

轮询快照不等于供应商锁定额度；其他程序可同时消费账户。本功能不改变远端自动续费/PAYGO设置，也不承诺本地预留消除供应商账户全部并发风险。

## 自动选路

1. 排除不在批准集合、缺密钥、认证失败、冷却、unknown/stale、不足一次或超预算的供应商。
2. 唯一合格方直接选；两家正常优先SerpApi。
3. SerpApi余额比例低于10%，且Tavily比例更高并足够一次时提前切换；切换后保持当前合格方，SerpApi恢复20%以上才按水位回切。
4. 当前方失去资格可立即转向合格方；缺少可比较上限时只比较绝对可执行次数，不以未知比例触发切换。
5. 每根任务最多3个逻辑查询、3次外部search attempt、2次供应商改变；每查询最多20候选、5次下载，默认选1张；根max_images默认1、显式设置范围1—10，达到即停止后续供给。这些上限不因重试或换供应商增加。

RouteDecision 固定实际provider、snapshot_id、reason、策略hash、attempt_no；恢复沿用原决定。允许集合内的正常切换无需重复批准；扩大集合/范围/预算或启用PAYGO必须重新授权。

## 错误、结算和对账

| 情况 | 处理 |
|---|---|
| 缺凭据/认证失败 | 返回 missing_credentials/auth_failed；可以在剩余额度内安全选另一家 |
| 429 | 结合归一化错误和余额区分 rate_limited/quota_exhausted；遵守 Retry-After |
| Tavily 432/433 | 分别作为套餐/按量付费限制的提供方错误证据，不自动启用PAYGO |
| 无可靠冷却值 | 从60秒退避至本地600秒上限；服务器更长等待不得截短 |
| 明确未发出或被拒绝 | 结清已知费用及尝试状态，才可创建新操作切换；拒绝也消耗本地尝试计数 |
| 超时、断连、不明5xx、提交窗口崩溃 | outcome_unknown，保留预留；不为同逻辑查询另起替代search |
| 空结果 | 本次已发生的额度/金额照实结算，返回empty；查询改写不重置共同次数 |
| 单个下载失败 | 逐项记录，不把URL当已取得素材；复用其他已登记候选 |

SerpApi成功搜索通常消耗一个search，缓存或明确错误按实际接口依据结算；空结果不自动免费。Tavily basic 按一个credit预留并记录实际usage。HTTP失败不直接推定费用为零。实耗高于预留但仍在原批准逐次/总限额内时记录非失败 reservation_adjusted，据实结算并检查后续预留后自动继续；下一步余额不足则停止新消费并保留成果，扩大预算须新计划和批准。实际违反批准限额才 budget_exceeded 失败；未知费用保留预留。UI 判定不继承旧 ledger 同名字段的“超过预留”含义。

有SerpApi search ID时，可通过其搜索归档尝试恢复，记录归档额外读取和限额；保留期外或无确定ID时保持未决。Tavily首轮不依赖Logs恢复：它不能返回搜索输出，也不是所有账户都能使用。不为“清账”购买套餐或扩大权限。

## 脱敏边界

acquisition将响应解析和有界下载留在同一受控边界，瞬时URL仅内存存在；返回ref和白名单摘要。脱敏覆盖异常链、httpx/httpcore调试日志、重定向和请求日志，不只处理最终manifest。错误仅给安全码、操作ID及受限错误素材ref；原始响应、认证头和账号信息不能成为验收附件。

## 单图与批次接线

单图搜索由 ui_analysis 的 search acquisition 驱动，T016—T018 接线并以 --max-images 1 验收。max_images≥2 属于后续 T031—T034 的 ui_batch；首版明确返回 capability_not_ready，不降级单图。查询/路由由根计划冻结，搜索 child 不得再次获取同一输入。手动数量与搜索 max_images 的选路及拒绝规则见 [CLI契约](/C:/Programs/LetsAIGC/specs/013-game-ui-analysis/contracts/cli.md)。

## 验收安排

离线协议先于实现，覆盖空/null/异常额度、正常偏好、低水位与滞后、单方不可用、双方耗尽、限频/缓存、最后额度竞争、拒绝后切换及月度重置；未知操作通过调用公共机制的边界例验证。固定额度响应验证自动切换，不故意耗尽真实账户。

T017 对两家各执行一次获批真实有界搜索，最多两次 search、两个下载，真实 probe 每家最多一次；验收策略分别冻结可用供应商以取得每家的证据，双后端自动策略由固定额度场景检查。每家取得的图片必须进入同一真实 OCR/VLM 单图解析，分析另受每图最多4次VLM及批准金额限制；无GPU。凭据、计价、条款和精确批准就绪才运行。T018复用搜索/解析证据，不为汇总再发请求。
