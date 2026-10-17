# ADR 0001：项目定位与永久非目标

## 1. 状态与背景（Status & Context）

- **状态**：已采纳；本文约束后续实现，不表示相关能力已经交付。
- **日期**：2026-10-16。
- **设计依据**：[项目计划](../../Spring-Boot-Safe-Runtime-MCP-Project-Plan.md)。

静态源码能够说明 Bean、路由和配置的声明意图，无法独立证明某个运行实例实际采用了这些声明。Profile、外部属性覆盖、classpath、自动配置顺序、`@ConditionalOn*` 判断和部署产物版本共同决定最终的容器状态。源码中的 `OrderService` 可能没有实例化，Controller 可能没有注册，数据库中的迁移记录也可能与当前工作区不同。编码 Agent 只读取源码时，缺少验证这些假设的运行态证据。

本项目采用**独立运行的本地只读诊断 MCP Server**，以 CLI 或 Sidecar 形态连接开发者本地或明确获权访问的 Spring Boot 实例。核心实现使用 Python 与官方 MCP Python SDK，默认通过 STDIO 接入 Codex、Claude Code、Cursor 和 VS Code 等客户端。目标连接通过受控 HTTP(S) 读取 Actuator、可选的 `/v3/api-docs`，以及 Actuator 暴露的 Flyway/Liquibase 状态。

架构链路为：固定语义工具 → 本地权限策略 → 受控 HTTP 客户端 → 版本适配与脱敏 → 确定性诊断 → 有界结构化结果。服务不要求目标应用新增业务工具代码，不依赖模型 API 完成解析、过滤或安全判断。

诊断能力服从数据可见性：Bean 列表不能单独解释条件未命中的原因；条件分析需要已授权的条件报告。启动分析需要目标提前配置原生启动步骤采集；迁移分析需要可用的迁移端点。缺少证据时返回不可用或证据不足，不推断为正常，也不自动扩大权限。

## 2. 生态方案深度对比矩阵（Ecosystem Comparison Matrix）

比较限定为架构侵入性、最小权限安全边界、跨版本契约稳定性、上下文 Token 效率和排障结果可复现性。本项目一行描述设计约束，其余行描述相应方案的职责与公开行为，不代表性能基准。社区脚本以本文引用的具体仓库为样本，不外推至全部社区实现。

| 方案 | 架构侵入性 | 最小权限安全边界 | 跨版本契约稳定性 | 上下文 Token 效率 | 排障结果可复现性 |
| --- | --- | --- | --- | --- | --- |
| **本项目** | 外部 CLI/Sidecar；复用标准观测接口，允许必要的端点授权和原生采集配置，不增加业务工具依赖。 | 固定目标、端点和方法白名单；默认本地访问；永久拒绝写操作和高危读取，强制二次脱敏。 | 将 Boot 2.7/3.x/4.x 响应适配为版本化诊断模型；仅对验证过的组合声明支持。 | 在本地筛选、投影、排序和限量；返回诊断所需字段及完整性标记。 | 保留实例关联、观察时间、来源和适配器版本；允许对脱敏证据重放确定性分析。 |
| **Spring AI MCP Server Starter** | 嵌入业务应用时，需要增加 Starter 依赖并编写工具注册或业务包装代码，例如 `@McpTool`。 | HTTP 传输自身默认不实施鉴权；应用另配安全层。工具可包含业务写操作，不天然限定为诊断读取。 | 提供 MCP 协议与工具注册基础设施；业务契约由应用维护，不自动归一化跨版本 Actuator。 | 提供工具 Schema；返回内容的字段选择、长度和摘要由工具作者负责。 | 可执行已定义业务工具；诊断证据采集、实例关联和快照重放需要另行实现。 |
| **springdoc-openapi MCP** | 在目标应用内增加相应集成依赖并启用 `springdoc.ai.mcp.enabled=true`，复用 Controller 定义。 | 将业务 REST 操作映射为工具；具有安全/变更分类和审批机制，但不能等同于永久禁止写操作。 | 契约来自业务 OpenAPI；Controller 映射本身不提供 Bean、条件报告、启动时间线或迁移状态适配。 | 从接口描述生成工具元数据；业务工具数量及响应体仍影响上下文开销。 | 可重现接口调用过程；不能仅凭业务 API 契约重建容器装配和迁移证据。 |
| **Spring Boot Admin / APM UI** | SBA 需要管理服务和应用注册/发现；APM 的部署、SDK 或 Agent 要求随实现变化。 | 支持运维授权与配置；SBA 的可选 MCP 集成同时提供读取和变更工具，需单独收敛权限。 | SBA 处理受支持版本的管理接口；APM 维护遥测模型。这些契约不等同于本文的诊断证据模型。 | UI、时序和链路面向人工或平台消费；SBA 已有 MCP 与过滤能力，仍需核对输出预算和字段边界。 | 历史指标、日志和链路有助于回溯；跨端点证据关联、脱敏重放能力取决于具体平台与配置。 |
| **早期社区 Actuator 脚本** | 样本通过手动 `curl` 保存 JSON，再向分析工具提供数据；目标无需业务工具代码。 | 手工采集方法和数据范围由用户承担；样本 README 使用 `POST /actuator/startup`，会消耗启动缓冲区。 | 样本提供部分输入格式兼容，未形成本文要求的跨版本诊断契约与支持矩阵。 | 人工粘贴 JSON 增加上下文搬运；脚本摘要可减少输出，但不能约束采集和粘贴阶段的数据暴露。 | 保存文件可供重放；实例身份、采集时间、采集副作用及脱敏策略仍需额外约束。 |

Spring AI 的 `spring-ai-starter-mcp-server`、`spring-ai-starter-mcp-server-webmvc` 和 `spring-ai-starter-mcp-server-webflux` 为应用提供 MCP 服务基础设施；HTTP 鉴权需要应用额外配置。上述比较针对其嵌入业务应用的用法，不限制开发者将其用于其他部署形态。[Spring AI MCP Server Boot Starter 官方说明](https://docs.spring.io/spring-ai/reference/api/mcp/mcp-server-boot-starter-docs.html)。

springdoc 的 MCP 集成将文档化的 REST Controller 转换为业务工具，并提供变更工具审批机制；它不因启用该配置而获得容器内部诊断能力。[springdoc-openapi MCP 集成说明](https://springdoc.org/features.html)。

**互补关系**：Spring AI 与 springdoc 负责向 Agent 提供应用业务能力，本项目负责检查这些能力所在实例的运行态证据。两者可以并存于同一个客户端。本项目读取 OpenAPI 描述和路由映射，不执行其中的业务操作，也不代理其他 MCP Server 的工具。

SBA 提供集中管理，APM 提供持续遥测；它们可承担历史观测与人工运维。本项目承担开发现场的按需诊断。SBA 4.1.3 已公开实验性 MCP 集成，因此“支持 MCP”不能作为独有能力；区别必须落在只读边界、证据契约和数据预算上。[SBA 架构说明](https://docs.spring-boot-admin.com/4.1.3/docs/getting-started/)、[SBA MCP 集成说明](https://docs.spring-boot-admin.com/4.1.3/docs/mcp/)。

社区样本 `Dmitriusan/mcp-spring-boot-actuator` 于 2026-05-13 归档。其 README 展示手动采集与粘贴 JSON 的流程，并使用 POST 采集启动数据；这些可验证行为说明了本项目需要约束的采集风险，不构成对全部社区工具的判断。[样本仓库与 README](https://github.com/Dmitriusan/mcp-spring-boot-actuator)。

## 3. 核心架构价值主张（Core Value Propositions）

### 3.1 最小权限只读诊断

工具表达诊断意图，例如 `springboot_inspect_beans`、`springboot_verify_mappings`、`springboot_check_migrations` 和 `springboot_analyze_startup`。模型只能提交受校验的查询条件，不能提交目标 URL、任意端点路径、HTTP 方法或认证头。目标地址、管理端口、管理基路径和凭据来源由本地可信配置决定。

默认目标限于回环地址；访问其他获权实例必须显式配置目标及主机白名单。HTTP 客户端统一执行协议、主机、端口和解析地址校验，默认禁止重定向并验证 TLS，限制请求耗时、响应体大小、解析深度和输出条数。读取 OpenAPI 时不自动请求 `servers` 地址或远程 `$ref`。

默认端点集合限于 `health`、`beans`、`mappings`、`flyway` 和 `liquibase`。OpenAPI、`startup` 和后续条件报告适配器按需显式启用；所有新增读取都必须具备固定解析器、字段策略和预算。HTTP GET 仅是必要条件，还必须确认端点语义与数据风险。

启动步骤必须区分以下两种操作：

| 操作 | 目标端语义 | 本项目决策 |
| --- | --- | --- |
| `GET /actuator/startup` | 返回当前缓冲步骤的非破坏性快照，不移除步骤。 | 仅在显式启用、已授权且目标具备采集能力时读取。 |
| `POST /actuator/startup` | 返回并排空已缓冲的步骤，改变后续观察者能读取到的证据。 | 永久禁止，不作为 GET 失败后的兼容回退或重试手段。 |

POST 即使返回 JSON，也会改变缓冲区状态，破坏诊断采集的幂等性和重复观察条件。GET 保留已缓冲证据，但不保证不同时间的响应完全相同；新增事件或其他消费者排空缓冲区仍可能改变结果。该读写区分在 Boot 2.7 的端点 API 和后续 Web API 中均有依据。[Boot 2.7 StartupEndpoint API](https://docs.spring.io/spring-boot/docs/2.7.16/api/org/springframework/boot/actuate/startup/StartupEndpoint.html)、[Startup Web API](https://docs.spring.io/spring-boot/api/rest/actuator/startup.html)。

`startup` 依赖目标预先配置 `BufferingApplicationStartup` 并暴露端点。服务不能事后恢复未采集或已被排空的步骤，也不能仅凭空列表证明启动没有慢步骤。[Actuator 启动采集前提](https://docs.spring.io/spring-boot/reference/actuator/endpoints.html)。

### 3.2 跨版本契约归一化

Boot 2.7、3.x 和 4.x 是适配维度，不是未经验证的全版本支持声明。首期验证 Boot 2.7/3.x；4.x 在真实样本、安全测试和集成验证完成前标为实验性。适配覆盖 MVC/WebFlux、多 ApplicationContext、字段缺失以及迁移框架差异；版本差异集中在适配层。

工具返回带 `schema_version` 的固定响应信封，包含 `status`、`source`、`data`、`warnings`、`redactions` 和 `truncated`。来源记录目标别名、逻辑端点、观察时间及可获知的运行版本；未知值必须显式表达。错误区分授权拒绝、端点不可用、超时、非法响应和大小超限，不将原始异常直接交给模型。

`data` 使用类型化的 Bean、路由、迁移和启动步骤模型。稳定排序、按需字段投影、匹配过滤和有界分页减少无关上下文；结果同时保留匹配数、截断原因和证据来源。低 Token 消耗通过这些机制实现，不承诺未经测量的压缩比例，也不通过丢弃完整性标记制造确定结论。

固定响应和适配器版本允许对同一份脱敏证据重复运行分析。版本兼容必须由真实响应 fixture 和集成测试证明，不能仅按版本号猜测 JSON 结构。

### 3.3 双层敏感信息脱敏

第一层保留目标端已有的认证授权、最小端点暴露和适用端点的敏感值屏蔽。不要求用户关闭屏蔽或设置 `show-values=always`，也不假设所有 Actuator 响应均已脱敏。Spring Boot 的内建屏蔽具有端点与配置范围，不能替代本服务的数据边界。[Actuator 敏感值处理说明](https://docs.spring.io/spring-boot/reference/actuator/endpoints.html)。

第二层由 MCP Server 强制执行，独立于目标端配置：仅允许已定义字段进入结果，对嵌套字段及字符串执行凭据和连接信息过滤。覆盖认证头、URL 凭据与查询参数、健康详情、条件消息、迁移描述、OpenAPI 示例以及异常文本；无法安全分类的自由文本默认省略或遮蔽。序列化输出、错误、日志与显式导出的本地证据均受同一策略约束。

认证材料只从环境变量读取，仅供 HTTP 客户端使用。默认不持久化原始响应；诊断结果只报告脱敏位置和原因，不附原值。脱敏不能只依赖字段名正则，也不能将目标返回的 `******` 视为整个响应安全的证明。目标文本始终属于不可信数据，其中的指令不得触发工具调用或策略变更。

### 3.4 运行态与源码漂移检测（Drift Detection）

服务将归一化运行证据与客户端从指定源码版本、测试或构建产物提取的结构化预期进行比较。比较输入必须携带基线标识；运行结果记录目标、ApplicationContext 和观察时间，并在可获得时关联构建版本。版本或实例归属不明时标记证据缺口，不直接认定部署错误。

漂移证据包括预期 Bean 缺失、依赖关系变化、HTTP method/路径/处理器不一致、OpenAPI 与已注册路由不一致，以及迁移记录与可信基线不一致。查询结果受权限、过滤或截断影响时，不得仅凭“未返回”判定对象不存在。

`@ConditionalOn*` 未命中分析需要显式启用的 `conditions` 适配器读取实际匹配报告；报告未覆盖的自定义条件不得补写原因。条件消息需经过二次脱敏。[条件报告 Web API](https://docs.spring.io/spring-boot/api/rest/actuator/conditions.html)。

Flyway/Liquibase 诊断只使用各自端点可见的记录、状态和校验和。只有存在对应证据与同一引擎、兼容算法版本的可信基线时，才报告校验和差异；不以普通文件哈希替代迁移引擎算法，不把读取迁移记录描述为完整 `validate`。端点未暴露、字段缺失或应用已启动失败时，明确说明无法验证。[Flyway Web API](https://docs.spring.io/spring-boot/api/rest/actuator/flyway.html)、[Liquibase Web API](https://docs.spring.io/spring-boot/api/rest/actuator/liquibase.html)。

漂移检测输出差异、来源和可复现的查询条件，供 Agent 与维护者判断；它不自动修复源码、运行配置或数据库。

## 4. 永久非目标边界（Explicit Non-Goals）

以下边界适用于所有版本。用户确认、模型指令、调试模式或高级配置均不能解除这些限制。

| 非目标 | 明确拒绝的能力 | 工程拒绝理由 |
| --- | --- | --- |
| Spring Boot 脚手架或代码生成器 | 创建项目、生成业务代码、自动添加应用依赖。 | 代码生成不提供运行态证据，会引入独立的模板、源码写入与版本维护职责。 |
| 通用 HTTP/URL 透传 | `fetch_arbitrary_endpoint`、任意路径/方法/请求头转发、按 OpenAPI 地址自动发起请求。 | 绕过目标和端点白名单，扩大 SSRF、凭据外送与不稳定响应契约的风险。 |
| 自定义运行时侵入 | 要求目标安装非标准字节码 Agent、自定义诊断 Starter 或第三方业务依赖。 | 改变 classpath、装配和生命周期，将诊断接入耦合到应用构建与发布，甚至改变被观察行为。 |
| 写操作和运维修复 | 任何 POST/PUT/PATCH/DELETE 目标操作，包括 `POST /actuator/shutdown`、`POST /actuator/loggers`（含具体 logger）、`DELETE /actuator/caches`（含单个缓存），以及重启或配置刷新。 | 会改变业务或管理状态，扩大凭据权限，使诊断重试和批量调用产生副作用。 |
| 消耗式启动采集 | `POST /actuator/startup`，以及以兼容性为由进行的方法回退。 | 会排空缓冲步骤，删除后续诊断所需的观察证据，破坏采集幂等性。 |
| 高危全量数据提取 | `GET /actuator/heapdump`、完整日志下载、未经脱敏和字段投影的 `/env` 与 `/configprops` 值导出。 | 可能包含全量凭据、用户数据和对象状态；GET 方法无法抵消泄露、内存与上下文开销。 |
| 业务 API 执行器 | 将 Controller/OpenAPI 操作重新注册为可执行业务工具，包括任意业务 GET。 | 路径或方法名称不能证明业务无副作用；执行面也与 Spring AI、springdoc 的职责重复。 |
| 数据库客户端与迁移执行器 | 直连数据库、任意 SQL、执行迁移、`repair` 或清除校验和。 | 需要额外数据库凭据与引擎权限，可能持锁或修改迁移历史，超出只读观测接口边界。 |
| 完整 APM 或集中运维平台 | 持续全量采集、分布式链路存储、告警平台、集群控制台。 | 引入长期数据保留、集中权限和平台运维成本，偏离本地按需诊断。 |
| 云端诊断与模型依赖 | 本服务主动上传诊断数据，或要求模型 API 才能完成解析、脱敏和分析。 | 扩大数据出域和外部依赖，使安全判断、成本与结果复现受模型和服务状态影响。 |

标准 Actuator 暴露、访问授权以及目标维护者选择的原生启动采集配置属于观测前提，不构成安装自定义诊断 Agent 的例外。缺少这些前提时服务降级返回能力缺口。

## 5. 架构决策后果与工程权衡（Consequences & Trade-offs）

- **接入独立，观测能力受目标约束。** 外部进程减少业务应用的依赖与发布耦合，但无法读取未暴露、未授权或未采集的数据。应用启动失败、缓冲区已被其他消费者排空时，服务不能恢复缺失证据；维护者可能仍需借助本地日志、调试器或 APM。
- **只读减少状态风险，仍需限制资源开销。** 白名单与永久禁写约束使工具可安全重复请求，但健康检查或大规模 Bean 查询仍会消耗目标资源。实现须限制并发、频率、耗时与大小；只读不等于零开销。
- **稳定契约产生持续适配成本。** 团队需要维护 Boot 2.7/3.x/4.x、MVC/WebFlux、认证方式及迁移框架的样本和测试。未知响应应保守失败或返回部分结果；尚未验证的组合不能在文档中标为正式支持。
- **摘要与强脱敏降低上下文开销，也减少细节。** 字段投影可能隐藏对特定故障有用的信息。工具通过受控筛选、分页和明确的证据缺口补充信息，不通过返回原始大响应、关闭脱敏或开放高危端点补偿。
- **可复现的是采集条件与分析过程。** 多个端点的读取不构成全局原子快照，实时实例也会变化。结果必须记录每次观察时间和可用的实例关联信息；只有同一份脱敏证据在相同适配器、策略与分析版本下，才要求确定性重放一致。证据仅在用户显式选择本地导出时保留。
- **本地优先不控制客户端后续的数据流。** 本服务默认本地处理、不主动上传，向 MCP 客户端只返回受控脱敏结果；客户端是否将结果发送给远程模型，由客户端配置和用户的数据策略决定。
- **能力重叠需要持续核对工程差异。** Spring AI、springdoc、SBA 或社区工具演进后，应继续按本文五个维度评估复用机会。项目不以“首个”“唯一”或未经验证的采用率作为架构依据，也不以差异化为由放宽永久安全边界。
