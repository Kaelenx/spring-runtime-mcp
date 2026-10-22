# 工具调用策略与统一错误契约

契约版本 2；协议目标为 MCP 2026-07-28。完整定义位于 [tools-contract.json](../../src/spring_runtime_mcp/schemas/tools-contract.json)。文件是包含八个 Tool 定义的本地发布包，不是整个文件都作为 JSON Schema，也不是完整 JSON-RPC 响应。服务端将 `tools` 数组原样放入 `tools/list` 的结果；包级 contractVersion/protocolVersion 不发送为 Tool 字段。

## 发布、排序与兼容

八个工具始终按 name 的 ASCII 字母序发布；不按使用频率、目标在线状态或端点可见性重排。工具定义不包含时间戳、实际目标、凭据、运行态示例或动态 description。能力不可用由调用结果报告，不在普通诊断后删除工具。需要改变授权可见集合时仍按名称排序，不把连接状态当授权依据。

每个 inputSchema/outputSchema 都显式使用 JSON Schema 2020-12，所有 `$ref` 均在该 Schema 自己的 `$defs` 内闭合。单独提取任一 Schema 仍可验证，客户端无需加载包级定义或请求外网。JSON Schema 的 default 是注解；服务端在验证后的规范化阶段显式应用默认值，不能假定验证器会填值。

readOnlyHint=true、destructiveHint=false、idempotentHint=true、openWorldHint=false 是声明。HTTP 方法、目标、路径、凭据和脱敏仍由确定性代码执行；幂等提示表示没有目标副作用，不保证运行中的实例两次返回相同数据。HTTP Method 查询字段仅用于匹配已注册业务路由，不能决定实际出站方法。

固定排序有助于工具列表缓存和模型前缀复用，不保证任何模型价格、折扣或缓存命中。官方将确定性排序和兼容 TextContent 视为建议，本项目将两者规定为必须执行。[MCP Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)、[JSON Schema 与引用规则](https://modelcontextprotocol.io/specification/2026-07-28/basic#json-schema-usage)。

## 调用策略

| 排障入口 | 选择与停止条件 |
| --- | --- |
| 慢启动 | springboot_analyze_startup：默认按 duration 降序、stepId 作平局键。只 GET startup；时间线跨度不是并发步骤耗时之和，也不一定覆盖 JVM 全生命周期。 |
| 迁移异常 | springboot_check_migrations：engine=auto，分别读取两个已启用域。先展示 FAILED、MISSING_FAILED、OUT_OF_ORDER、PENDING，再按引擎报告的顺序和稳定身份排序；不按版本字符串排序。 |
| 源码与实际不一致 | springboot_detect_drift：直接提交从获权工作区提取的预期；仅比较提供的维度。expectedScopeComplete=false 时不声称额外运行对象均属异常。 |
| 健康异常 | springboot_diagnose_health：默认隐藏正常子节点与详情；componentPath 是本地树查询，不拼接网络路径。探针仅访问预登记的两个健康组。 |
| 注入、Bean 或条件不匹配 | springboot_inspect_beans：至少提供名称模式、类型模式或包前缀。先读 beans；无匹配或需要解释时才读已启用 conditions，条件权限失败仍保留 Bean 事实。 |
| 配置绑定或覆盖 | springboot_inspect_config：至少提供 prefix/propertyKeyPattern，includeValues=false。env/configprops 能力默认关闭，启用后仍与操作者登记的安全属性/前缀取交集。 |
| 接口契约 | springboot_search_openapi：schemaName 与操作查询互斥；httpMethod 不能单独查询。按既定有限路径回退，只展开同文档引用，默认两阶、最大八阶。 |
| 路由未注册 | springboot_verify_mappings：至少提供路径、方法或 Controller 模式。查询所有 dispatcher；不调用业务 URL，也不根据路由注册推断鉴权或业务执行成功。 |

Pattern 参数使用大小写敏感的有界 glob，仅 `*`、`?` 为通配符，其余字符按字面量匹配；最多 128 字符，不支持用户正则、脚本或 JSONPath。实现使用有界 glob 自动机/动态规划或等价非回溯算法，不能直接拼接未经限制的正则。多个筛选条件按 AND，packagePrefix 按包段边界匹配。路径输入只是匹配文本；不执行其指向的地址。

maxResults 默认及硬上限 50。先在完整可见快照过滤、计数、稳定排序，再分页；totalMatched 无法确定时为 null。嵌套关联记录整页总数也最多 50；各数组的 maxItems 不能相加成为放大预算的理由。达到字节、深度、图节点或记录预算时标记 truncated、truncationReasons 和 coverage，不截断 JSON。

游标绑定工具、目标、规范化参数、策略版本及脱敏快照，TTL 60 秒。续页重发同一查询参数和预期快照，仅增加 cursor；不能偷偷用新预期继续旧比较。nextAction.filterSuggestions 只包含当前工具 inputSchema 允许的参数和值，客户端合并到原调用后重新验证；page 使用 nextCursor，done 时建议数组为空。不得把上游文本作为筛选建议。

所有参数整体最多 16 KiB；漂移 expectedBeans/expectedRoutes/expectedMigrationVersion 合计最多 100 个预期实体。数组缺省表示不比较，空数组表示该维度预期为空；只有 expectedScopeComplete=true 且运行态覆盖完整时才能判定 unexpectedInRuntime。sourceRevision/sourceModifiedAt 是调用者声明，不是经服务端验证的源码事实；服务不执行源码、不读取任意文件路径。文件时间、构建 revision、实例身份和启动时间不充分时只生成 candidate/unknown，不确认“未重启”。

Bean 身份包括 target、context、canonicalName 和 beanKind；别名双向索引、父容器查找、子容器遮蔽及完整图反向索引在分页前完成。条件链覆盖 ANY/ALL/NONE 的递归三值计算及 ConditionalOnBean 的跨层关联；无法唯一解析的条件或创建者返回 unknown。自动穿透不代表授权扩大，也不保证标准端点提供确切创建失败原因。

迁移字段缺失返回 null/UNKNOWN，不能补 0、伪造 PENDING 或把所有乱序视为失败。MISSING_FAILED 仅在引擎明确报告或有足够、可追溯证据时使用；不能由一次 404 或列表缺项推导。expectedMigrations 仅用于显式的离线集合比较，expectedSetComplete 不声明运行端点完整。Liquibase 通常不报告完整 pending/执行耗时；MARK_RAN 仅表示记账。description 字段保留为可空兼容槽，生产默认不输出原始迁移描述，使用 null 或脱敏标记并记录省略。

## 配置读取的新增边界

本契约取代早期“完全禁止 env/configprops 网络读取”的设计，允许默认关闭、显式登记的固定 GET 模板；仍禁止把其完整值树交给 Agent。优先使用已验证版本支持的 `/configprops/{prefix}` 和 `/env/{property.name}`；其他受控读取仅可访问固定 `/configprops`、`/env`，在上游大小预算内本地筛选，不能声称 MCP 过滤缩小了上游响应。不从这些响应发现新 host、端口或 OpenAPI 路径。

SanitizerPipeline 在投影前处理敏感上下文传播，在输出前再次检查键、连接串、origin、已知凭据和嵌入值。includeValues=true 只允许输出登记为非敏感且满足值域的标量，不能关闭脱敏，也不能要求 show-values=always。秘密属性不返回实际值；值与其 inputs.*.value/origin 共享敏感性，统一使用 `[REDACTED_BY_MCP]` 或省略。redactions 只包含固定输出字段/数组索引，不包含原始键或秘密摘要。

origin 仅输出相对配置文件标识、可证实的行列号或合法环境变量名，不包含绝对用户路径、完整 propertySource 自由文本或连接串；不能确定时 null/unknown。activeProfiles 不从 spring.profiles.active 属性字符串猜测。绑定值与实际生效值分别标注，屏蔽值不能用于漂移等值比较。原始异常栈、OpenAPI 描述和条件消息不出站；健康 details 仅为批准的结构化事实。

## DiagnosticError & Remediation

每个 outputSchema 验证 structuredContent，而不是外层 CallToolResult。所有预期的执行失败也返回同一领域信封：status、source、data、统计、coverage、nextAction 和 errors。DiagnosticError 包含 code、endpointId、httpStatus、retryable、本地模板 message，以及 remediation。Recipe 返回 recipeId、action、configKeys、固定 steps、requiresApproval 和 retryAfterMs，不提供凭据、任意 URL 或可执行远端命令。

| 观察 | 代码与处理 |
| --- | --- |
| 未启用/未知目标 | CAPABILITY_DISABLED / TARGET_NOT_REGISTERED；不联网，申请精确能力或登记。 |
| 401/403 | AUTH_REQUIRED / AUTH_FORBIDDEN；检查环境变量名称及专用读取角色，停止该分支，不匿名重试或换路径。 |
| 404/405 | ENDPOINT_UNAVAILABLE / METHOD_UNAVAILABLE；核对管理路径、最小 include、版本及启用/access 条件。只有已登记 OpenAPI 404 链允许有限回退，绝不切换 POST。 |
| 启动采集缺失 | STARTUP_DATA_UNAVAILABLE；定位 SpringApplication.run 前的 BufferingApplicationStartup 配置，并提示重启需求；404 本身不证明缺少采集器。 |
| 隐藏配置/健康详情 | VALUE_NOT_VISIBLE / HEALTH_DETAILS_NOT_VISIBLE；保留 unknown，建议核对已有读取角色与 when-authorized，不建议 always。 |
| TLS/peer/重定向/秘密泄露风险 | TLS_VERIFICATION_FAILED / PEER_MISMATCH / REDIRECT_BLOCKED / SANITIZATION_FAILED；停止，不降低守卫。 |
| 超限/游标错误/未知结构 | RESPONSE_TOO_LARGE / CURSOR_EXPIRED / CURSOR_QUERY_MISMATCH / UNSUPPORTED_SHAPE；缩小查询或新建快照，不回传原文。 |

配置指引必须定位到准确键与适用版本：2.7/3.0–3.3 的具体 endpoint enabled、3.4/4.x 的 access=read-only、独立的 web exposure 和现有授权规则。configKeys/steps 只由本地模板生成；Schema 的 string 类型不能替代模板白名单。不生成 include=*、permitAll、关闭认证/TLS 或无条件覆盖整条安全链的建议。requiresApproval 依据是否已获同范围授权计算，不能要求重复批准。

ok/partial 的有效诊断使用 isError=false，包括健康 DOWN、发现迁移失败/漂移、以及完整范围内没有匹配项。无可用领域结果的 denied/unavailable/invalid/too_large 使用 isError=true、data=null、totalMatched=null，并至少提供一个 errors 项。未知工具或非法 JSON-RPC 封装由协议错误处理；工具参数不符合 inputSchema 属于可行动的工具执行错误，使用 INVALID_ARGUMENT、isError=true，不联网，不回显输入。

2026-07-28 的完整工具结果使用 resultType=complete；同时返回 structuredContent 与唯一 TextContent，其 text 是同一脱敏对象的紧凑 JSON。SDK 必须实际支持所宣称协议及必需请求元数据，不能仅替换版本字符串。整个 JSON-RPC 响应连同两份内容和 request id 不超过 16,384 UTF-8 字节；超限时先裁剪，再经最终序列化出口测量。此回包限制不适用于静态 tools/list 定义，不能因此截断 Schema。

服务仅缓存脱敏快照。Agent 每轮最多 4 次调用、累计结果不超过 64 KiB；触顶后先总结并收窄查询。授权、网络、安全或证据失败时停止对应分支，完成其余可独立诊断，不自动提权或执行修复。

## 离线验证

```bash
uv run --no-project --with jsonschema==4.25.1 python scripts/validate_tool_contract.py
```

该命令验证八个名称的排序、注解、独立 Schema、局部引用和正反例，不连接 Spring Boot，也不代表 HTTP 守卫、脱敏实现或跨版本集成已经通过。现阶段 JSON 文件为先行契约；后续 Python/Pydantic 模型必须与之逐字规范化比对，禁止悄悄重新生成改变公开行为。
