# ExecPlan: Bidirectional Bean Dependency Graph, Multi-Hop Condition Failure Analyzer & Runtime Drift Detector

> 历史实施计划：下文的阶段状态与验证计数保留原始记录。v0.1.0 已包含八个处理器、自实现 STDIO 宿主和 HTTP 连接器；当前交付与未完成范围以 [版本记录](../../CHANGELOG.md) 为准。


## Purpose / Big Picture


完成后，编码 Agent 可以从接口类型找到实际 Bean 或未创建的候选，沿别名和父容器还原正向、反向及传递依赖，并解释“PaymentGateway 未出现，是因为 StripePaymentGateway 需要 StripeClient，而 StripeClient 的 stripe.enabled 条件未满足”。同一份运行证据还可以与调用者提供的 Bean、路由、迁移版本和 Profile 预期比较，区分可证实差异、证据缺口与“可能未重启”等候选解释。

本计划交付两个真实工具处理器、共享分析内核、受限响应出口和离线重放命令。重放必须调用同一处理器，不能用预制答案代替算法。仓库没有安全 HTTP 连接器或 MCP 宿主实现，因此本计划通过明确的读取接口接入运行证据，不同时实现整个网络栈、STDIO 宿主或其余六个诊断工具。离线重放的完整请求—证据—分析—JSON-RPC 结果闭环是本次可独立验证的交付；生产连通性和 SDK 绑定必须明确标为后续集成，不能把本计划通过说成整台 Server 可运行。

完成 Milestone 4 后，在仓库根目录运行下面的命令，会得到经 Schema 验证的 JSON-RPC 工具结果；执行步骤部分给出其他场景和自动断言入口。

    PYTHONPATH=src uv run --no-project --with jsonschema==4.25.1 python -m spring_runtime_mcp.replay --scenario stripe-missing --tool springboot_inspect_beans --arguments-json '{"typePattern":"example.PaymentGateway","contextId":"application","conditionDepth":4,"maxResults":10}'

本文件是活文档。每次暂停、恢复或改变设计，都更新 Progress、Surprises & Discoveries、Decision Log、Outcomes & Retrospective，并在文末记录修改原因。恢复执行时从当前工作树核对已完成项，不依赖旧会话、外部网页或未落盘的解释。2026-10-16 用户选择实施范围 2：只完成 Bean 图、多跳条件归因及其离线处理器；漂移检测器继续待办。本文采用 OpenAI ExecPlan 的自包含、可观察验收和持续更新约定；必要知识均在文内，不要求执行者另外阅读规范。

## Progress


- [x] (2026-10-16T08:10:09Z) 核对当前仓库、两个工具的版本 2 Schema、现有安全与归一化约束；确认没有运行实现、测试目录或 .agent/PLANS.md。
- [x] (2026-10-16T08:23:59Z) 完成四个实施里程碑、内联样例、类型签名及验收设计；核验章节顺序、10 个 JSON 样例、4 段类型/签名代码和 3 个 CLI 参数示例。本项表示计划验证，不表示功能代码已实现。
- [x] (2026-10-16T09:09:45Z) Milestone 1：Python 包、离线 fixtures、容器索引和双向图完成；6 项图测试通过。
- [x] (2026-10-16T09:09:45Z) Milestone 2：源码生产者映射、嵌套条件和两跳失败归因完成；30 项条件测试通过，未知/冲突证据保留 partial/unknown。
- [ ] Milestone 3：四维漂移比较、影响传播与证据分级未实现。
- [ ] Milestone 4：仅 Bean 处理器、契约加载、脱敏、游标、预算和离线 CLI 完成；其余七个工具、MCP Server/SDK 注册、真实安全 HTTP 连接器和 STDIO 宿主未实现。
- [x] (2026-10-16T09:09:45Z) 本次选定范围：116 项离线测试、25 个源模块严格 mypy、Ruff 与既有 144+ Schema 断言通过；公开 Schema 未修改。
- [x] (本次 Review) 修正省略 `parentId` 的证据语义：未由适配器明确证明根容器时保留 unknown/partial；为该边界增加回归测试并为现有根 fixture 显式写入 `parentId: null`。

## Surprises & Discoveries

Observation（2026-10-16 实施）：Stripe fixture 的源码目录仅覆盖相关三个类型；其他实际 Bean 的继承关系未知。接口查询可还原该源码生产者的失败链，但全局 absence 不可证明，因此保留 `state=explained`、外层 partial、`totalMatched=null`。补全运行类型的 TypeFact 后同一查询得到 ok、totalMatched=0；两种情形均有集成回归。

Observation：同一条依赖边用于正向和反向索引；both 必须独立遍历两种方向再合并，不能交替反转制造并不存在的传递路径。游标缓存只保存脱敏投影，不保存原始观察、异常文本或源码目录。


Observation：本仓库目前只有文档、完整工具 JSON 与 scripts/validate_tool_contract.py，没有可复用的运行服务、构建元数据或网络连接器。Evidence：检查时 rg --files 返回上述文件，未发现 pyproject.toml、服务 Python 包或 tests/。所以不能直接给出 uv run pytest 或启动已存在服务器的指令。

Observation：标准 beans 只报告 aliases、scope、type、resource、dependencies；不存在通用 factoryMethod 或 implements 字段。conditions 的配置源可能是配置类名，也可能是 Configuration#method。Evidence：当前归一化文档及官方结构均如此。接口查询与工厂关联需要额外的、可追溯的源码事实；仅按短类名或 Bean 名猜测不能满足验收。

Observation：嵌套条件写在 message 字符串中，不是标准 children JSON 树。Evidence：negativeMatches 的每项只有 matched/notMatched 条件列表，嵌套类、运算符和计数编码在消息里。解析器必须限定 grammar，无法恢复完整子树时保留 unknown。

Observation：公开 drift 输入只接收 expectedBeans、expectedRoutes、expectedMigrationVersion、expectedProfiles 等字段；Bean 工具没有源码目录或 catalog 参数。Evidence：src/schemas/tools-contract.json 的 additionalProperties=false。本计划不增加这些公开字段，而通过内部服务依赖注入源码事实。

Observation：公开漂移输出没有任意详细事实扩展槽，nextAction.filterSuggestions 也不能承载对象型预期。Evidence：差异项只允许 identity、expected/actual 字符串、evidenceLevel 等固定字段。详细解释保留在有界内部证据索引；复杂输入修正由本地 Recipe 说明，不构造非法建议参数。

Observation（本次 Spec Conformance Review）：八工具目录与其余七个 JSON golden 只证明契约可校验，不证明有工具处理器或 MCP 路由。源码仅包含 `inspect_beans` handler，`replay.py` 的 `--tool` 也只允许该工具；没有 SDK Server、`tools/list` / `tools/call` 宿主。OpenAPI 与 mappings profile 测试只检查 raw fixture 形状，没有执行 normalizer。验证依据：`rg --files src/spring_runtime_mcp` 与 `tests/conformance/test_multiversion_fixtures.py`。

Observation（本次 Review）：beans normalizer 曾把省略 `parentId` 解释为已知根节点，这与架构约束“只有适配器能证明时才可视为根”冲突。现改为 unknown，显式 `parentId: null` 才代表已知根；相关根 fixture 已显式化并新增遗漏字段回归测试。


## Decision Log

Decision（本次 Review）：不把八工具契约目录或 synthetic goldens 包装成处理器实现。新建 SDK runtime dependency 及扩大本阶段实施范围需先获得明确授权；因此本次只修复已实现内核的可证缺陷，并把其余七工具和 Server 宿主保持为未完成。

Decision（本次 Review）：beans 中显式 `parentId: null` 才证明根 Context；缺少字段按 unknown/partial 处理。Rationale：符合跨版本架构文档，避免把不可见父节点误当不存在。


Decision：生产分析使用标准库；输入校验实现既有 Bean inputSchema 的有限子集，未知 Schema 关键字失败关闭；离线集成及 CLI 通过独立 Draft202012Validator 校验 outputSchema。Rationale：不引入未授权运行依赖，同时用独立验证覆盖实际响应。未来 SDK 适配器仍须在写出前验证输出。

Decision：当前落地接口以实际源码为准：`tools.inspect_beans.inspect_beans(arguments, services)`、`transport.responses.serialize_response(envelope, request_id=None)`、`snapshots.SnapshotStore.put/get`；规划中的通用双工具适配器留待 drift 集成。Rationale：保持本次可执行范围清晰，避免空泛通用接口。


Decision：保持 src/schemas/tools-contract.json 契约版本 2 和八个工具名称不变。Rationale：本工作实现既有公开契约；增加 sourceCatalog、configurationClass 等公开参数会产生额外兼容性与授权问题。Date/Author：2026-10-16，Codex。

Decision：内部领域模型使用 Python 3.11 标准库 dataclasses、Enum、typing；pytest 和 jsonschema 只用于开发、重放及契约验证。Rationale：当前不存在运行依赖，不能为了纯分析内核引入 Java 反射、字节码或源码解析器。MCP 宿主后续把其已验证输入映射为这些类型。Date/Author：2026-10-16，Codex。

Decision：源码事实是显式注入的、目标绑定的 SourceCatalog；默认空目录。离线重放只能读取固定 scenario 对应的仓库 fixture。Rationale：Actuator 无法独自证明接口实现和工厂来源，但工具也不能接收任意文件路径、扫描工作区或执行 Java。源码提取与生产启动配置接线不在本次范围。Date/Author：2026-10-16，Codex。

Decision：解释“缺失”的前提是对应容器和类型范围可完整观察；推导链每条边都需要报告或源码关联证据。Rationale：无法访问、版本不认识和预算截断都不能等价为对象不存在。Date/Author：2026-10-16，Codex。

Decision：本次提供实际处理器和离线闭环，真实网络只能由未来通过安全验收的 RuntimeReader 注入。Rationale：重写 SSRF/TLS 连接器会扩大范围；也不能用临时 httpx 客户端绕过现有安全架构。无安全读取器时应禁用线上读取，而不是宣称功能可连接实例。Date/Author：2026-10-16，Codex。

Decision：Flyway 的 expectedMigrationVersion 比较“installedRank 最大的成功且有 version 的安装记录”，不宣称是引擎语义上的最高版本。Liquibase 没有标准 version 字段，收到其版本预期时返回 MIGRATION_VERSION_UNSUPPORTED 与 partial。Rationale：不能按字符串排序猜最新版本，也不能把 Liquibase id/orderExecuted 偷换为 version。Date/Author：2026-10-16，Codex。

## Outcomes & Retrospective

2026-10-16 实施交付：Bean 分析内核与真实处理器完成；完整 JSON-RPC 预算包含兼容文本副本，最多 16,384 字节。分页绑定所有非 cursor 查询参数，缓存 60 秒、32 MiB LRU；继续翻页不重读目标。归因覆盖 aliases、父子遮蔽、传递类型、精确 factory source 及嵌套条件；多类型/名称组合 OnBeanCondition 暂保守返回 `MULTI_BEAN_REQUIREMENTS_UNSUPPORTED`。

实际验证：`uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q` 得到 116 passed；`uv run --offline --no-project --with mypy==1.18.2 python -m mypy src/spring_runtime_mcp` 得到 25 个源模块无问题；Ruff check/format 和既有 Schema 校验通过。SPEC.md §5.1 给出同样的完整复现命令。tests/integration 仅为离线处理器集成，不表示真实 Spring Boot 2.7/3/4 兼容已验证。

未完成范围：drift、真实安全 HTTP 连接器、STDIO/官方 SDK 宿主、打包及真实实例兼容性。源码提取不自动运行，SourceCatalog 仅接受内部显式注入。以下“计划编写阶段”记录为历史基线，不能覆盖上述实际交付状态。


计划编写阶段的结果是可交接的设计与验收流程，尚未实现新模块，也未执行未来测试。已核验本文件的 11 个二级章节及四个里程碑，解析 10 个 JSON 样例，实际加载四段类型/签名代码，并用现有 inputSchema 校验三个 CLI 参数示例及 Bean data 输出片段。现有 scripts/validate_tool_contract.py 可验证发布 Schema，但这些文档检查不能证明依赖图、多跳归因或漂移算法正确。

本次 Review 基线完整执行契约校验（8 个隔离 schema、144+ synthetic assertions）、pytest（595 passed）、Ruff check（通过）和 Ruff format check（52 files formatted）。修复 `parentId` 省略语义、相关 fixtures 与单元边界测试后，最终验证为 pytest **596 passed**、契约校验 **8 schemas / 144+ assertions passed**、Ruff check **passed**、Ruff format check **52 files already formatted**。

Review 结论：Bean 图和嵌套条件算法有执行级测试，但 OpenAPI、mappings、startup、migration、drift、health、config 的分析处理器均未在 `src/` 实现。raw fixture 检查与 schema golden 校验不能作为实现证据。当前不符合“8 工具真实注册与可调用”的完整规格；MCP SDK Server/stdio host 与安全 HTTP connector 也未实现。不得将该状态描述为完整服务交付。


## Context and Orientation


工作目录是 /home/neil/Projects/codcli/spring-runtime-mcp。已有 AGENTS.md 是工作边界，SPEC.md 版本 0.4 是产品规格，src/schemas/tools-contract.json 版本 2 是公开输入/输出契约，scripts/validate_tool_contract.py 是独立校验器。执行者可以按需查看这些文件，但本计划已内嵌本任务需要的路径、算法和默认值。本文不要求创建 .agent/PLANS.md，也不改变根目录 AGENTS.md。

ApplicationContext 是 Spring 管理 Bean 的容器。contextId 只在某个 targetId 中有意义；parentId 指向父容器。Bean 的 canonicalName 是 beans 字典键，aliases 是它的其他查找名称。依赖边 A → B 表示 A 的 dependencies 报告了 B；反向边是同一关系的逆向索引，不代表父容器可以访问子容器。逻辑快照是一次诊断内收集的多份观察，不保证跨端点事务一致；每份观察有自己的 observationId 和时间。

下面为符合官方路径的合成 beans.json，保存为 tests/fixtures/bean_graph/parent-child/beans.json。它不是从真实目标导出的兼容性证据。缺失 parentId 在识别到的标准 beans 形状中表示根；未知形状或丢失父节点不得自动视作根。

    {
      "contexts": {
        "infrastructure": {
          "beans": {
            "connectionFactory": {"aliases": [], "scope": "singleton", "type": "example.ConnectionFactory", "resource": "class path resource [example/InfrastructureConfiguration.class]", "dependencies": []},
            "dataSource": {"aliases": [], "scope": "singleton", "type": "example.DataSource", "resource": "class path resource [example/InfrastructureConfiguration.class]", "dependencies": ["connectionFactory"]},
            "orderRepository": {"aliases": ["orderRepoAlias"], "scope": "singleton", "type": "example.OrderRepository", "resource": "class path resource [example/InfrastructureConfiguration.class]", "dependencies": ["dataSource"]},
            "client": {"aliases": [], "scope": "singleton", "type": "example.ParentClient", "resource": null, "dependencies": []},
            "auditService": {"aliases": [], "scope": "singleton", "type": "example.AuditService", "resource": null, "dependencies": ["client"]}
          }
        },
        "application": {
          "parentId": "infrastructure",
          "beans": {
            "client": {"aliases": [], "scope": "singleton", "type": "example.ChildClient", "resource": null, "dependencies": []},
            "orderService": {"aliases": ["orders"], "scope": "singleton", "type": "example.OrderService", "resource": null, "dependencies": ["orderRepoAlias", "client"]},
            "orderController": {"aliases": [], "scope": "singleton", "type": "example.OrderController", "resource": null, "dependencies": ["orders"]},
            "stripeConfiguration": {"aliases": [], "scope": "singleton", "type": "example.StripeConfiguration", "resource": null, "dependencies": []}
          }
        }
      }
    }

实际路径为 contexts.{contextId}.beans.{canonicalName}.{aliases,scope,type,resource,dependencies} 与 contexts.{contextId}.parentId。标准 resource 是来源线索，不能证明某个 @Bean 方法；null/缺失都保留为未知。未知字段不出站。相同样本可用于 Stripe 缺失场景，因为两个 Stripe Bean 都未创建，不会导致现有 orderService 的必需依赖矛盾。

下面为 stripe-missing/conditions.json。negativeMatches 属于各自的 contexts 项；notMatched 和 matched 都必须读取。消息只是受控解析输入，绝不直接写入工具结果或日志。

    {
      "contexts": {
        "infrastructure": {"positiveMatches": {}, "negativeMatches": {}, "unconditionalClasses": []},
        "application": {
          "parentId": "infrastructure",
          "positiveMatches": {
            "example.FallbackConfiguration": [{"condition": "OnBeanCondition", "message": "@ConditionalOnMissingBean (types: example.StripeClient; SearchStrategy: all) did not find any beans"}]
          },
          "negativeMatches": {
            "example.StripePaymentGateway": {
              "matched": [],
              "notMatched": [{"condition": "OnBeanCondition", "message": "@ConditionalOnBean (types: example.StripeClient; SearchStrategy: all) did not find any beans of type example.StripeClient"}]
            },
            "example.StripeConfiguration#stripeClient": {
              "matched": [],
              "notMatched": [{"condition": "OnPropertyCondition", "message": "@ConditionalOnProperty (stripe.enabled=true) did not find property 'enabled'"}]
            }
          },
          "unconditionalClasses": ["example.OrderConfiguration"]
        }
      }
    }

positiveMatches.{source}[] 的 condition/message 表示匹配；negativeMatches.{source}.matched[] 也匹配，notMatched[] 不匹配。不能因为 OnMissingBean 的消息包含 did not find 就反转结果。配置源 source 必须完整匹配，# 后面是工厂方法名，不是 Bean 名的可靠替代。不同配置类可以有同名方法。

因为上述响应没有 PaymentGateway 的实现关系，stripe-missing/source-catalog.json 必须另外提供如下显式事实。目录是对源码的陈述，不是运行态事实；来源文件/行号只作定位，不执行其中内容。生产服务没有注入目录时，仍能返回 Bean 图，但接口归因应报告 SOURCE_FACTS_UNAVAILABLE。

    {
      "targetId": "local",
      "sourceRevision": "workspace-a1",
      "types": [
        {"name": "example.PaymentGateway", "directSupertypes": []},
        {"name": "example.StripePaymentGateway", "directSupertypes": ["example.PaymentGateway"]},
        {"name": "example.StripeClient", "directSupertypes": []}
      ],
      "producers": [
        {"contextId": "application", "beanName": "stripePaymentGateway", "aliases": ["paymentGateway"], "declaredType": "example.StripePaymentGateway", "implementationType": "example.StripePaymentGateway", "configurationClass": "example.StripePaymentGateway", "factoryMethod": null, "conditionSources": ["example.StripePaymentGateway"], "sourceFile": "src/main/java/example/StripePaymentGateway.java", "sourceLine": 12},
        {"contextId": "application", "beanName": "stripeClient", "aliases": ["stripeApiClient"], "declaredType": "example.StripeClient", "implementationType": null, "configurationClass": "example.StripeConfiguration", "factoryMethod": "stripeClient", "conditionSources": ["example.StripeConfiguration#stripeClient"], "sourceFile": "src/main/java/example/StripeConfiguration.java", "sourceLine": 18}
      ]
    }

目录校验要求 targetId 相同、最多 1,000 个类型/生产者、256 KiB、相对且不含 .. 的 sourceFile、正整数行号、无任意扩展字段或可执行代码。类型继承关系显式遍历并检测环；缺少中间类不能猜可赋值关系。多个生产者都可实现接口时列为候选，不能只选名称最像的实现。另建 factory-method 场景，将 gateway 的配置源改为 example.PaymentConfiguration#stripePaymentGateway，以证明按方法源和返回接口也能关联；不能只支持组件类条件。

漂移的其他证据也要有界归一化。mappings 的 MVC 路径为 contexts.*.mappings.dispatcherServlets.*[]，WebFlux 为 dispatcherHandlers.*[]；对每个 dispatcher 读取 details.handlerMethod 与 details.requestMappingConditions 的 patterns/methods。下面是 drift-basic/mappings.json 的最小 MVC 样例；Reactive 变体只把 dispatcherServlets/dispatcherServlet 改为 dispatcherHandlers/webHandler，必须得到等价注册事实。

    {"contexts":{"application":{"parentId":"infrastructure","mappings":{"dispatcherServlets":{"dispatcherServlet":[{"details":{"handlerMethod":{"className":"example.OrderController","name":"findOrder","descriptor":"()Ljava/lang/String;"},"requestMappingConditions":{"patterns":["/orders/{id}"],"methods":["GET"],"params":[],"headers":[],"consumes":[],"produces":[]}}}]}}}}}

Flyway 路径为 contexts.*.flywayBeans.*.migrations[]。drift-basic/flyway.json 的脚本如下；last-successful-version 的选择依据 installedRank，而不是版本字符串字典序。

    {"contexts":{"application":{"parentId":"infrastructure","flywayBeans":{"flyway":{"migrations":[{"version":"1","script":"V1__orders.sql","state":"SUCCESS","installedRank":1,"checksum":101,"executionTime":8},{"version":"2","script":"V2__index.sql","state":"SUCCESS","installedRank":2,"checksum":102,"executionTime":5}]}}}}}

Liquibase 结构为 contexts.*.liquibaseBeans.*.changeSets[]，记录含 changeLog、id、author、execType、orderExecuted、checksum，没有标准 version。它的缺失或版本比较不支持不能抹掉其他维度结果。Profile 从已启用的 env 单属性响应 activeProfiles 读取，不能从 spring.profiles.active 的字符串推导；用于 drift-basic/env-property.json 的最小样例为：

    {"activeProfiles":["dev"],"defaultProfiles":["default"],"property":{"source":"systemEnvironment","value":"dev"},"propertySources":[{"name":"systemEnvironment","property":{"value":"dev","origin":"System Environment Property SPRING_PROFILES_ACTIVE"}}]}

漂移可选读取已有 info 中明确登记的应用构建 revision；这不是 Boot 版本。只有用户提交 sourceModifiedAt，且 startup 能力已授权时才读取 startup.timeline.startTime 作为进程启动线索；不为推断未重启自动启用端点。startup 时间线起点不保证等于 JVM 启动时间，不能据此证实因果。

公开输入采用 camelCase。Bean 工具至少需要 beanNamePattern/typePattern/packagePrefix，默认 targetId=local、direction=both、maxDepth=2、conditionDepth=4、maxResults=50；深度最多 16。Pattern 仅支持大小写敏感的 * 和 ?，最多 128 字符；不用用户正则。Drift 至少提交一个预期维度，缺省维度不比较，空数组表示空预期；预期实体合计最多 100，参数整体最多 16 KiB，expectedScopeComplete 默认 false。两个工具都接受绑定同一查询和快照的 cursor；不得增加源码路径或 URL 参数。

统一回包保留 schemaVersion="2"、status、source、data、totalMatched、truncated、truncationReasons、coverage、warnings、redactions、nextCursor、nextAction、errors。Bean data 包含 items 和 missingBeanAnalysis；drift data 包含 missingInRuntime、unexpectedInRuntime、profileMismatch、rootCauseInferences。ok/partial 使用 isError=false，完整零匹配和发现漂移不是执行错误。整体失败 data=null、totalMatched=null、coverage.state=unavailable，errors 至少一项，isError=true。

每份输出都要先安全投影、再独立脱敏、最后测量完整 JSON-RPC 包；structuredContent 与唯一 TextContent 的 JSON 副本一起最多 16,384 UTF-8 字节。主记录上限 50，整页关联记录也上限 50；Bean 每类依赖边最多 16，条件最多 16，阻塞链最多 8 条且每条最多 16 个 ID。不得截断 JSON 或把原文放进错误、建议和资源链接。未知总数用 null，不用 0。

## Plan of Work


### Milestone 1 — 建立容器身份、别名索引和双向依赖图


创建 src/spring_runtime_mcp/__init__.py、models/bean_graph.py、adapters/beans.py、analyzers/bean_graph.py，以及对应 __init__.py。新增 tests/conftest.py、tests/unit/test_bean_graph.py、tests/fixtures/bean_graph/parent-child/beans.json。先写能暴露父容器误查和分页后反转错误的测试，再实现解析；不创建空实现伪装通过。

normalize_beans 遍历完整受限 contexts，身份包含 targetId、snapshotId、contextId、canonicalName、beanKind。建立每个 Context 的 nameToCanonical: str→set[BeanKey]、canonicalToNames: BeanKey→set[str]。aliases 元素直接指向所属字典项；重复值去重，冲突保留候选，不能最后写入覆盖。parentId 缺失在已识别标准形状中为已知根，指向缺失 Context 则 unresolved，多个端点的父关系冲突不能合并覆盖。

解析依赖从依赖所属 Bean 的定义 Context 开始，先查本地名称/别名，再沿 parentId 向上；父容器从不向下查找。若索引不完整、别名冲突或父链循环，返回有理由的 unresolved/ambiguous，不能继续找父级以掩盖本地冲突。FactoryBean 的 & 前缀只作为已有明确证据处理，不能网络读取或实例化来补证。

所有依赖先解析成边，再一次构建 reverse 索引，最后按查询做 BFS。BFS 是逐层遍历；用完整 BeanKey 去重，保存最短距离，边始终保持原始依赖方向。direction=dependents 时沿 reverse 走，但仍输出“原依赖者 → 被依赖者”。根深度为 0；深度 2 包含两跳关系。循环可以保留为已知边，但不能无限扩展。

上游 JSON 最多 8 MiB（部署硬上限 16 MiB），结构深度 64、解析节点 100,000，单次分析最多访问 20,000 个节点/边；不得先分配无限对象再检查。离线读取也先限字节，再用识别字符串与转义的单次扫描检查嵌套深度和保守 token 数，最后 json.loads；拒绝重复对象键和 NaN/Infinity，不能仅在 json.loads 后递归检查。纯归一化函数接收已受上游限制的 ParsedObservation，所有阶段共享同一可消耗的分析预算。超限记录 GRAPH_LIMIT_REACHED，不宣称闭包完整。排序使用 contextId、canonicalName、beanKind、边身份作稳定平局键；只在图和反向索引建立后分页。

验收运行 Concrete Steps 的第一组 pytest。orderService 的 orderRepoAlias 必须解析为 infrastructure.orderRepository；其 client 指向 application.client，而 auditService 的 client 仍指向 infrastructure.client。查询父 repository 的反向关系应看见 child 的 orderService 和二跳 orderController；查询深度 3 时应出现 service→repository→dataSource→connectionFactory。完成时更新四个活文档章节，不等待下一步确认。

### Milestone 2 — 恢复条件结构并归因未创建的生产者


新增 models/conditions.py、models/source_catalog.py、adapters/conditions.py、adapters/source_catalog.py、analyzers/condition_failures.py；加入 tests/unit/test_condition_failures.py 和 stripe-missing、factory-method、nested-conditions fixture。源码目录读取器仅用于固定离线 fixture；工具处理器接收已注入目录，不读取任意路径。

为生产者建立按 Bean 主名/aliases、declaredType、implementationType、完整 configurationClass#factoryMethod、conditionSources 的索引。通过显式 directSupertypes 的传递闭包回答接口可赋值性。默认 @Bean 方法名等于 Bean 名只是源码规则线索；目录有显式 beanName/aliases 时以其为准，不能把方法名直接登记为运行态 alias。类型筛选先匹配实际报告类型，再匹配目录证明的上级类型；目录缺失时只能精确匹配报告类型，接口是否存在可能未知。

条件适配器先以 positiveMatches、negativeMatches.matched/notMatched 确定 reportedOutcome。解析消息只支持已登记、测试过的 grammar：OnBeanCondition 的 types/names/SearchStrategy 与缺失结果，OnPropertyCondition 的 prefix/name/havingValue/matchIfMissing 与缺失/不匹配结果，以及 AnyNestedCondition、AllNestedConditions、NoneNestedConditions 的嵌套文本。处理消息最多 8 KiB，累计仍受分析预算约束；超长或未知表达只生成 UNKNOWN_CONDITION_GRAMMAR，不回显原文。

嵌套文本使用扫描器识别运算符、完整条件类路径、计数和子条件边界。不能仅按分号 split，因为分号也出现在参数内部。未唯一恢复的子集合保持 childrenComplete=false。ANY 任一真则真、全部已知假才假；ALL 任一假则假、全部已知真才真；NONE 任一真则假、全部已知假才真；其他情况 unknown。reportedOutcome 与推导冲突时保留报告并产生 CONDITION_PARSE_CONFLICT。空子集合只有被证明确实为空时才能使用空集合逻辑。

最小嵌套 grammar 原型把下列条目放入 application 的 negativeMatches.example.PaymentModeCondition.notMatched 数组，matched=[]；保持父容器声明，并在该场景 beans 中加入 example.RemoteClient 类型的 remoteClient。明确类路径用于推导父子边，不把缩进或分号本身当层级。预期 Card=notMatched、Remote=notMatched、根 ANY=notMatched；将任一无法解析的子项改为未知时，必须重新按三值逻辑计算。

    {"condition":"example.PaymentModeCondition","message":"AnyNestedCondition 0 matched 2 did not; NestedCondition on example.PaymentModeCondition.Card AllNestedConditions 0 matched 1 did not; NestedCondition on example.PaymentModeCondition.Card.Enabled @ConditionalOnProperty (card.enabled=true) did not find property 'enabled'; NestedCondition on example.PaymentModeCondition.Remote NoneNestedConditions 1 matched 0 did not; NestedCondition on example.PaymentModeCondition.Remote.Client @ConditionalOnBean (types: example.RemoteClient; SearchStrategy: all) found bean 'remoteClient'"}

对缺失查询先找候选生产者，再准确关联其 conditionSources。ConditionalOnBean 的 CURRENT 只查当前、PARENTS 只查祖先、ALL 查当前与祖先；策略未知时不能默认 ALL。多个 required types/names 按真实条件语义组合，不把任一缺失当作所有条件都缺失；对已知 @ConditionalOnMissingBean 不反向生成缺失失败链。涉及注解、泛型或 ignored 参数等未支持语义时保留 unknown。

Stripe 场景的来源路径为“请求 PaymentGateway → 目录中的 StripePaymentGateway → 报告的 OnBeanCondition 缺少 StripeClient → 目录中的 stripeClient 工厂 → 报告的 OnPropertyCondition 未找到 stripe.enabled”。公开 blockingPaths 可表示为三个条件节点 c_gateway、c_client_factory、c_stripe_property；中间节点 conditionType=null、reasonCode=SOURCE_FACTORY_NOT_CREATED，用于明确标识源码关联，不能冒充运行条件。连接它们的两条阻塞边为 c_gateway→c_client_factory→c_stripe_property，conditionDepth=2 足以保留这条两跳路径；查询入口的接口匹配不额外消耗条件边深度。嵌套条件子边与生产者阻塞边都消耗同一深度预算，默认 4、最大 16。内部 Condition.child_ids 仅保存布尔嵌套子边，FailureAnalysis.blocking_edges 单独保存归因边，三值求值绝不读取归因边；公开投影把两类展开关系合入 childIds，operator=leaf 的 childIds 可以表示因果展开，不代表布尔子操作数。

requiredBeanIds 中未创建对象使用独立 source-producer ID 命名空间，不能把它们塞进 runtime items。每条内部边携带 observation/source evidence；对应配置类、条件类型和 reasonCode 在公开节点中可验证。只有链路都能定位且末端是确定的未匹配条件时 missingBeanAnalysis.state=explained；这解释当前报告的阻塞路径，不宣称已经排除所有其他原因。候选不唯一、目录缺失、父层级不可见或 grammar 不完整时 state=partial/unknown，并返回缺口。

先完成一次显式 grammar 原型：Stripe 的叶子条件加一条 ANY 包含 NONE/ALL 的嵌套样例。原型通过严格正反例后保留为正式适配器；否则保留有用的报告事实并降级，不能为了达到 explained 而宽松猜测。Condition 引用环与 Bean 依赖环分别检测。测试应证明移除目录后仍返回 Bean 图，而 Stripe 确切归因降为 unknown。Milestone 2 的单元测试通过即为独立交付。

### Milestone 3 — 比较运行快照与调用者预期


新增 models/drift.py、adapters/drift_support.py、analyzers/drift.py 与 tests/unit/test_drift.py。drift_support 仅提供本任务所需的 MVC/WebFlux 路由、Flyway 安装记录、实际 Profile 和可选构建/时间线元数据，不同时实现其他公开诊断工具。新增 drift-basic 与 drift-partial fixtures。drift-basic 的目录在 Stripe 目录上明确添加 {"name":"example.OrderRepository","directSupertypes":[]} 与 {"name":"example.NewOrderRepository","directSupertypes":[]}，将它们登记为本 fixture 中已知不兼容的具体类型；否则影响传播测试中的类型不匹配只能为未知，不能按名字猜测。RuntimeSnapshot 按域记录 coverage 和 observation，不能用一个全局成功标记替代逐域可见性。

Bean 比较复用同一主名、alias、父容器与类型索引。expectedBeans 的 name/type/contextId 同时提供时按 AND；aliases 表示要求存在的别名。未指定 contextId 且匹配多个上下文时，存在性查询可以确认至少一个，但需要精确漂移归属或影响分析时返回 BEAN_SCOPE_AMBIGUOUS，不混合图。source expected 类型只是调用者预期，不能独自证明真实继承关系。Bean 同身份但类型/别名不符合时生成一条 missingInRuntime 差异并在 actual 中保留安全的实际摘要，不再把同身份对象重复算 unexpected。

缺省维度不读取对应端点，也不比较；空数组显式比较空集合。missingInRuntime 仅在该维度和所需层级完整可见时生成。unexpectedInRuntime 还要求 expectedScopeComplete=true。Bean 完整范围为明确限定的目标及已观察 contexts；未指定预期 context 不能把任意未观察父容器包含进范围。返回项的 expected/actual 是最多 256 字符的安全身份摘要，不是任意对象 JSON；信息不足时 unknown，不截断成相同字符串后比较。

路由以注册模板而非外部 URL 比较，至少包含 context、path、httpMethod，提供 controllerClass 时也精确核验。遍历两个栈下所有 dispatcher，methods 为空表示未限制方法；不补造隐式 HEAD/OPTIONS。没有支持的结构化路径/方法信息时该部分 coverage=partial。不要随意删除尾斜杠、参数名称或管理/应用前缀来制造等价，也不要执行业务请求确认。

Flyway 多 context/Bean 时，expectedMigrationVersion 未限定到唯一集合则报告 MIGRATION_SCOPE_AMBIGUOUS，不能取第一个。先选明确成功的、有 version 的记录，再用安装顺序选最后一个；repeatable 的 version=null 不参与该比较。缺少 installedRank、未知状态或排序冲突时 unknown。具体源码预期 version 与实际原生字符串作精确比较。Liquibase 的版本预期按 Decision Log 安全降级，不借 id 模拟版本。引擎端点缺失不阻断 Bean/路由比较。

Profile 只比较实际 activeProfiles 的集合，排序不造成差异；未提供 expectedProfiles 时 profileMismatch=null，未获准读取时 actual=null、state=unknown。期待空数组与实际空 activeProfiles 可以匹配，但不表示 defaultProfiles 没有参与运行。只有显式有效值和胜出的环境变量来源证据，才允许 ENVIRONMENT_OVERRIDE；仅 Profile 不符不证明环境变量覆盖。

影响分析复用完整反向图，从存在身份但不符合预期的节点或明确可关联的差异节点继续向上遍历两阶/传递依赖。不存在的 Bean 在运行图中没有边时 affectedDependencyIds=[]，除非内部目录明确提供预期影响，且此时标为 inferred；不能凭名字构造运行依赖。

根因推断保持 supported/candidate/unknown。sourceModifiedAt 晚于可观察启动线索且实例关联充分时，可以输出 POSSIBLE_NOT_RESTARTED、confidence=candidate；它不能排除错误实例、未部署构建、时钟偏差或热加载。sourceRevision 是调用者声明，info 构建 revision 属于目标应用；缺一则未知，不输出伪造的时间根因。每条 evidenceIds 都指向同一快照中真实保存的安全证据或已输出差异，不能引用不存在 ID。

验证 drift-basic 的预期 Bean、路由、Flyway 版本及 prod Profile 会分别显示缺失/不符；drift-partial 的 403 只能产生 unknown 与错误配方，不能生成对应域 missing。单元测试通过后更新 Progress 和实际发现，再进入工具集成。

### Milestone 4 — 工具处理器、受限出口与离线端到端验收


新增 tools/inspect_beans.py、tools/detect_drift.py、contracts/registry.py、contracts/serialization.py、snapshots.py、policy/sanitization.py、replay.py，以及 tests/contracts/test_bean_drift_tools.py、tests/security/test_bean_drift_output.py、tests/contracts/test_bean_drift_replay.py。共享模型与接口的位置和签名见最后一节。不得创建第二个绕过安全策略的 HTTP 客户端。

InspectBeans 先读取 beans；必要时读取已启用 conditions，没有授权则不请求。Drift 根据出现的预期维度惰性读取 beans/mappings/flyway/env-property；条件和源码目录仅用于已有差异的解释，info/startup 只在相应能力已启用且推断需要时读取。读单域失败保留其他有效证据。RuntimeReader 在调用前检查已登记 target 与能力；测试实现记录 logical endpoint ID，不接收 URL、headers、method 或任意路径。线上实现缺失时不启用网络入口。

整个工具调用绝对时限为 15 秒；读取器的单请求绝对时限 5 秒、连接 2 秒，同目标最多 2 个并发。执行者通过注入时钟测试超时和取消，不以重试重置总预算。env-property 在本任务只代表启动时登记的 spring.profiles.active 单属性模板；模型不能指定另一个属性名。条件解释缺少该属性时不自动读取其真实值补证。

registry 从现有 JSON 加载八个静态工具，保持字母序，给这两个名称绑定实现；其他名称保持既有定义但返回 CAPABILITY_NOT_IMPLEMENTED，不新增成功占位结果，不把模块导入顺序当注册顺序。提供与宿主无关的 ToolHandler/ToolRegistrar 接口；未来官方 MCP Python SDK 宿主仅负责调用同一处理器和协议封装。本计划的 replay 是测试/开发入口，不是第九个 MCP 工具，也不实现不完整的 STDIO 协议来冒充宿主。

先做上下文敏感的安全提取，秘密键与其 value/origin 共同标记；再对将出站的结构做独立脱敏与白名单投影。共享安全规格要求的连接串、Bearer/JWT/PEM、已知凭据、嵌套 JSON、资源 origin 和动态键泄露都必须覆盖。原始 conditions.message 在有界处理期间解析，缓存只保留提取事实。resource 只保留批准的相对资源标识，无法安全表示则 null；不能给原始异常做 repr 后发出。内部目录、参数和外部响应都是待校验数据，不能变为权限、命令或模板。

本次数据面不输出任意属性树或自由文本。输出标识只允许有界 Java/Bean/路由标识；资源解析出 classpath 相对标识后，拒绝绝对路径、..、userinfo、query 和 fragment。键检测先 NFKC/casefold 并去除非字母数字，再匹配 password/passwd/pwd/secret/token/credential/privatekey/accesskey/clientsecret/authorization；敏感性传递到其值与 origin。字符串包含连接串内联凭据、带秘密名的 query/赋值、Bearer、三段 JWT、PEM 私钥或本地注入的已知凭据字面变体时，整叶替换为 [REDACTED_BY_MCP]；不能只删除其中一段后宣称安全。只对树做替换，再 json.dumps，不对序列化 JSON 全局正则替换。unknown 动态字段和提示性自由文本一律省略；被省略的标识用安全序号维持关系，并给 coverage 缺口。正式接入更多数据面前仍须共享安全管道的完整验收，不能把本次最小投影当成全端点通用脱敏器。

预算器先按稳定顺序裁剪主记录，再裁剪关联展开，标注 ITEM_TOO_LARGE/OUTPUT_BUDGET/GRAPH_LIMIT_REACHED 等固定原因。主记录有剩余时发 cursor；仅关联深度不足时用 nextAction=refine 建议 maxDepth/conditionDepth，不能签发一个会重复返回相同主记录的无进展游标。输出先按对应 outputSchema 校验，再构造 resultType=complete、structuredContent、唯一 TextContent 和 isError；最后测量包括 JSON-RPC id 的实际 UTF-8 字节，过大则再次收缩并重新校验，仍失败返回预留的最小 too_large 结果。

快照只缓存脱敏事实，TTL 60 秒，总量 32 MiB，LRU 淘汰。cursor 为服务端保存状态的不透明随机句柄，绑定 target、tool、完整规范化参数、expected 输入、策略版本、snapshotId 和主记录 offset；对外不编码源码、秘密或查询哈希。源码目录在服务初始化后不可变；若宿主显式重载目录或策略，必须失效关联快照/游标，不能用新目录重算旧页。续页只能改变 cursor，本计划连 maxResults 的变化也视为 CURSOR_QUERY_MISMATCH。同一快照重试可复用；新诊断必须生成新观察，不能静默拿旧健康/配置结果冒充实时。

replay 只接受固定 scenario 名和两个工具名称，--arguments-json 接收公开参数，不接受 URL。scenario 注册表映射到固定 fixture 文件，禁止 ..、符号链接逃逸或 arbitrary path。stripe-missing 与 drift-basic 的 beans 复用 parent-child/beans.json，不要求复制；Stripe 目录映射到内联 catalog，drift-basic 的缺省目录也复用它。其他缺省域明确标为未请求，不生成空成功。默认 stdout 输出唯一 JSON-RPC 行，stderr 只写安全诊断；所有场景中的时钟和 observation ID 固定以便重放。程序必须从 fixture 经过同一适配器/分析器/处理器得到结果，不能按 scenario 名直接拼预期结果。

Milestone 4 验收要求命令行看到完整 Stripe 阻塞链、父子容器反向依赖和实际漂移结果，并通过 Schema、字节、无生产网络和秘密不可见断言。默认测试 monkeypatch socket.create_connection、socket.socket.connect/connect_ex、socket.getaddrinfo 为立即失败，内存 Reader 仍可运行；未来真实连接器测试不混进这套离线测试。只在这些验收实际通过后勾选完成。

## Concrete Steps


所有命令的工作目录均为 /home/neil/Projects/codcli/spring-runtime-mcp；其他机器先 cd 到含现有 SPEC.md 的同一仓库根。当前只有第一条独立校验已具备实现，后续命令是里程碑完成后必须提供的入口。代码、测试和目录在实现对应里程碑时创建，本计划编写阶段不提前运行缺失模块。

    cd /home/neil/Projects/codcli/spring-runtime-mcp
    uv run --no-project --with jsonschema==4.25.1 python scripts/validate_tool_contract.py

该校验当前应退出 0 并打印八个 Schema 验证成功。依赖首次缓存可以访问包索引，测试本身不访问目标；缓存齐备后所有下列 uv 命令都可以加 --offline。Python 版本至少 3.11。无需 npm、Maven、JDK、数据库、真实凭据或模型 API。

Milestone 1 先创建包和 fixture，再创建、运行失败的回归测试并实现相应函数；测试能导入实际模块后，缺少父容器/别名行为应以断言失败体现，而不是只证明模块尚不存在。完成后执行：

    PYTHONPATH=src uv run --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q tests/unit/test_bean_graph.py

Milestone 2 保存内联 beans/conditions/catalog 为固定 fixture，并为别名冲突、父链环、factory-method 和嵌套未知条件建立变体，然后运行：

    PYTHONPATH=src uv run --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q tests/unit/test_condition_failures.py

Milestone 3 创建 drift-basic、drift-partial 的 mappings/flyway/env-property 及元数据，使用同一 Reader 协议构造 RuntimeSnapshot，然后运行：

    PYTHONPATH=src uv run --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q tests/unit/test_drift.py

Milestone 4 为 JSON Schema 校验器实现独立的开发适配器，使用 fixture Reader 和同一两个 handler 构造 replay CLI，然后运行：

    PYTHONPATH=src uv run --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q tests/contracts/test_bean_drift_tools.py tests/contracts/test_bean_drift_replay.py tests/security/test_bean_drift_output.py
    uv run --no-project --with ruff==0.14.1 ruff check src/spring_runtime_mcp tests
    uv run --no-project --with ruff==0.14.1 ruff format --check src/spring_runtime_mcp tests
    uv run --no-project --with jsonschema==4.25.1 python scripts/validate_tool_contract.py

完成后可用以下两条命令直接观察两个真实处理器。输出示意只是验证方向，不是预制终端证据；以实际测试输出更新 Progress 与 Outcomes。

    PYTHONPATH=src uv run --no-project --with jsonschema==4.25.1 python -m spring_runtime_mcp.replay --scenario stripe-missing --tool springboot_inspect_beans --arguments-json '{"typePattern":"example.PaymentGateway","contextId":"application","conditionDepth":4,"maxResults":10}'
    PYTHONPATH=src uv run --no-project --with jsonschema==4.25.1 python -m spring_runtime_mcp.replay --scenario drift-basic --tool springboot_detect_drift --arguments-json '{"expectedBeans":[{"name":"stripePaymentGateway","contextId":"application"}],"expectedRoutes":[{"path":"/payments","httpMethod":"POST","contextId":"application"}],"expectedMigrationVersion":{"engine":"flyway","version":"3","contextId":"application","migrationBean":"flyway"},"expectedProfiles":["prod"],"maxResults":10}'

第一条应出现 missingBeanAnalysis.state="explained"，但 data.items 仍为空；第二条应显示 Bean、路由、迁移三个差异与 Profile mismatch，不能因为报告了差异而设置 isError=true。所有 CLI 输出必须能被 json.loads 解析，不能混入普通日志。将所需关键值和真实测试结论写入本计划，不保存原始目标数据。

## Validation and Acceptance


验收不是“文件已经存在”或“类型检查通过”。每个以下行为都必须有具名 pytest 测试，测试使用固定 fixture，调用实际函数/处理器并断言输出；在修复前可证明失败，在修复后通过。测试总数由实现实际记录，不能预填 passed 数。

Graph 验收 test_alias_parent_shadowing_and_reverse 查询如下参数：

    {"beanNamePattern":"orderService","contextId":"application","direction":"both","maxDepth":3,"maxResults":10}

items 恰有 application.orderService；dependencies 包含到 infrastructure.orderRepository 的深度 1 边，其 requestedName=orderRepoAlias、targetName=orderRepository，继续包含 dataSource、connectionFactory 的深度 2/3 边。client 必须解析为 child client；查询 auditService 时必须为 parent client。另用 repository 查询验证 dependents 的 fromId 包含 child orderService，并可在深度 2 找到 orderController。maxResults=1 不能导致反向图缺失。测试也覆盖两个 target 同名 Context、别名冲突、父层级缺失与环。

Stripe 验收 test_interface_to_factory_to_property_two_hops 使用 Purpose 中的参数和本计划内联样本。结构化 data 的可核验核心如下；实际节点 ID 可以是稳定不透明 ID，测试按 reasonCode 找到节点后验证边，而不是硬编码示例 ID。

    {
      "items": [],
      "missingBeanAnalysis": {
        "state": "explained",
        "conditions": [
          {"id":"c_gateway","configurationClass":"example.StripePaymentGateway","conditionType":"OnBeanCondition","operator":"leaf","outcome":"notMatched","reasonCode":"REQUIRED_BEAN_MISSING","childIds":["c_client_factory"],"requiredBeanIds":["source_stripe_client"],"childrenComplete":true},
          {"id":"c_client_factory","configurationClass":"example.StripeConfiguration","conditionType":null,"operator":"leaf","outcome":"notMatched","reasonCode":"SOURCE_FACTORY_NOT_CREATED","childIds":["c_stripe_property"],"requiredBeanIds":[],"childrenComplete":true},
          {"id":"c_stripe_property","configurationClass":"example.StripeConfiguration","conditionType":"OnPropertyCondition","operator":"leaf","outcome":"notMatched","reasonCode":"PROPERTY_NOT_FOUND","childIds":[],"requiredBeanIds":[],"childrenComplete":true}
        ],
        "blockingPaths": [["c_gateway","c_client_factory","c_stripe_property"]]
      }
    }

该结果的 totalMatched=0 只表示完整可见 beans 中没有匹配项，不表示没有解释；已成功解释且没有其他缺口时 status=ok。必须检查接口→实现的目录关联、精确 factory source、末端 property key=stripe.enabled 的内部事实，以及 publicly safe reasonCode，不以找到了两个字符串作为验收。Property key 不扩展现有公开字段，保留在内部安全条件事实；公开原因通过固定节点与配置源定位。

test_condition_depth_and_nested_unknown 将 conditionDepth 设为 1，必须 truncated=true、GRAPH_LIMIT_REACHED，不能仍返回完整两跳链。ANY/ALL/NONE 真/假/未知组合、OnMissingBean 正向匹配、CURRENT/PARENTS/ALL、两个同名工厂方法、泛型/注解等未支持条件、条件环都要有反例。删除目录时不能再 explained；conditions 返回 403 时保留已读取 beans、status=partial、对应 AUTH_FORBIDDEN，不能请求替代危险端点。

Drift 验收 test_drift_four_dimensions 使用 Concrete Steps 的 drift-basic 参数。missingInRuntime 必须包含 dimension=beans、identity 指向 application.stripePaymentGateway 的缺失项，dimension=routes、identity 包含 POST 与 /payments 的缺失项，以及 dimension=migrations、expected="3"、actual="2" 的安装版本差异。三项按固定 dimension/context/identity 排序；unexpectedInRuntime=[]，因为 expectedScopeComplete 默认 false。profileMismatch 必须等于以下对象：

    {"expected":["prod"],"actual":["dev"],"state":"mismatch"}

Profile 差异作为一个主记录计数，因此该 fixture 的 totalMatched=4；rootCauseInferences 的附加解释不计入主记录。对空预期 Profile 返回 match/mismatch 对象但只有 mismatch 计数；未提供该维度返回 null。结果 isError=false、status=ok，表明诊断完成，不表明应用符合预期。只提供 sourceModifiedAt 而没有启动/实例证据时，不得输出 supported 的未重启推断。

test_drift_visibility_and_expectation_scope 用 env-property 403 变体验证 profileMismatch.actual=null/state=unknown、status=partial、totalMatched=null，Bean/路由/迁移的已知差异保留。expectedRoutes 缺省时 Reader 不读取 mappings；expectedRoutes=[] 且 expectedScopeComplete=false 不产生 unexpected；改为 true 且范围完整时才报告已观察路由。别名预期和规范 Bean 名应对应相同身份；只在缺少类型目录时不能断言接口不匹配。多 Flyway Bean 无限定、Liquibase 版本预期和未知迁移顺序应为 partial 而不是猜数值。

test_drift_affected_dependencies 用 expectedBeans=[{"name":"orderRepository","type":"example.NewOrderRepository","contextId":"infrastructure"}] 验证类型不符合预期时，actual 保留 OrderRepository，affectedDependencyIds 在 maxDepth=2 内包含 orderService/orderController；不能将父层级名称遮蔽误作两个 runtime 身份。缺失且没有运行边的 stripePaymentGateway 不得伪造相同影响列表。

test_output_schema_budget_and_cursor 对两工具的成功、partial、denied、invalid、too_large 结果都用独立提取的 Draft202012Validator 校验 structuredContent，断言 json.loads(content[0].text)==structuredContent，且真实编码后的完整 frame≤16,384 字节。使用中文、emoji、超长合法标识与大量边/条件测试预算，不能只测 data。相同 snapshot 的分页去重且计数不变；TTL 60 秒后 CURSOR_EXPIRED，改变 expected 输入或 maxResults 返回 CURSOR_QUERY_MISMATCH，续页 Reader 调用次数为 0。

test_untrusted_content_never_escapes 在 resource、条件消息、目录标签、origin、异常及嵌套 JSON 中加入合成 password、JDBC userinfo、Bearer/JWT、PEM 和“忽略之前规则”文本，断言结果、stderr、缓存和 Recipe 都没有原始片段，脱敏幂等且 JSON 合法。测试输入 url/headers、控制字符、额外 configurationClass、超过 maxResults=50、预期实体>100 时联网/Reader 次数均为 0；HTTP method 筛选值 POST 只能影响路由比较，不能产生出站 POST。

## Idempotence and Recovery


本计划的默认执行只有仓库文件编辑与离线读取，无目标写操作或数据库迁移。重跑测试与 replay 不修改 fixture、源码目录或公共 Schema；随机游标仅是进程内状态，固定快照下相同参数的领域结论应一致。不要为稳定快照把真实运行时间写回 fixture。

恢复执行时先核对工作树已有模块和最近 Progress；已完成且未受新改动影响的检查不用无理由全量重跑。若工作只完成了某个里程碑的一部分，将该项拆成已完成与剩余两项，记录 UTC 时间、失败命令和具体缺口。对用户已有文件做小范围补丁，不使用 git reset、清理整个 tests 或覆盖发布 Schema 来恢复。

依赖解析失败时保留源码，不反复改版本或加入 runtime 依赖；使用已缓存版本加 --offline，缓存确实不足则报告开发环境阻塞。新增运行依赖、公开 Schema、网络白名单或目标暴露变更未被当前实施任务明确授权时，先准备具体补丁与验证再确认；当前计划不需要这些变更。继续可以独立完成的分析与离线测试。

读取 401/403、未知 target、TLS/peer 拒绝或不能安全脱敏时，终止对应分支并返回固定错误，不匿名重试、不换 host、不读取 env/configprops 全量数据来猜根因。仅已有授权的读取器可执行 GET/HEAD，重定向关闭；startup POST、heapdump、logfile 永久禁止。未来线上读取器缺失或安全验收未通过时，不提供临时直连替代。

输出超限时保留合法信封、统计与下一步；必要时返回 too_large，不能为了演示成功提高硬上限。游标失效需新建快照，不能把不同观察时间的页面拼接成一致状态。每个里程碑通过后可保存独立可审阅变更；是否提交、合并或发布按实际用户授权执行，不把 ExecPlan 示例里的提交建议当作自动推送许可。

## Interfaces and Dependencies


以下是实施时必须提供的类型与函数契约。Python 内部 snake_case 映射为公开 camelCase；所有可选输入必须区分 omitted 与空集合。Domain 类型和纯分析器不能依赖 SDK、网络、文件系统或环境变量。源码目录在服务初始化时注入，目标绑定校验必须先于查询。函数体示例故意拒绝调用，仅表示签名，不能作为里程碑成功实现。

在 src/spring_runtime_mcp/models/bean_graph.py、conditions.py、source_catalog.py、drift.py 中定义下列逻辑类型；可以拆分文件，但跨文件引用必须保持这些名字和语义。timestamp 字符串在构造边界验证为带时区 ISO-8601，安全 ID 最多 96 字符。Mapping 和 tuple 在构建后视为不可变，原始响应不进入这些缓存模型。

    from __future__ import annotations
    from dataclasses import dataclass
    from typing import Callable, Generic, Literal, Mapping, Protocol, TypeAlias, TypeVar, Union

    Json: TypeAlias = Union[None, bool, int, float, str, list["Json"], dict[str, "Json"]]
    Outcome = Literal["matched", "notMatched", "unknown"]
    CoverageState = Literal["complete", "partial", "unavailable"]
    Search = Literal["CURRENT", "PARENTS", "ALL", "unknown"]
    Endpoint = Literal["beans", "conditions", "mappings", "flyway", "liquibase", "env-property", "info", "startup"]
    T = TypeVar("T")

    @dataclass(frozen=True)
    class Fact(Generic[T]):
        state: Literal["known", "unknown", "notReported", "redacted"]
        value: T | None
        reason_code: str | None

    @dataclass(frozen=True, order=True)
    class ContextKey:
        target_id: str
        snapshot_id: str
        context_id: str

    @dataclass(frozen=True, order=True)
    class BeanKey:
        context: ContextKey
        canonical_name: str
        bean_kind: Literal["bean", "factory", "product", "unknown"]

    @dataclass(frozen=True)
    class Evidence:
        id: str
        target_id: str
        observation_id: str
        observed_at: str
        kind: Literal["runtime", "source", "inferred"]
        shape_path: str
        record_ordinal: int

    @dataclass(frozen=True)
    class Context:
        key: ContextKey
        parent: Fact[ContextKey | None]
        complete: bool

    @dataclass(frozen=True)
    class Bean:
        key: BeanKey
        id: str
        aliases: tuple[str, ...]
        reported_type: Fact[str]
        scope: Fact[str]
        resource: Fact[str]
        requested_dependencies: tuple[str, ...]
        evidence_ids: tuple[str, ...]

    @dataclass(frozen=True)
    class Resolution:
        state: Literal["resolved", "ambiguous", "unresolved"]
        candidates: tuple[BeanKey, ...]
        reason_code: str | None

    @dataclass(frozen=True)
    class DependencyEdge:
        from_key: BeanKey
        requested_name: str
        resolution: Resolution
        evidence_ids: tuple[str, ...]

    @dataclass(frozen=True)
    class BeanGraph:
        contexts: Mapping[ContextKey, Context]
        beans: Mapping[BeanKey, Bean]
        names: Mapping[ContextKey, Mapping[str, tuple[BeanKey, ...]]]
        canonical_to_names: Mapping[BeanKey, tuple[str, ...]]
        forward: Mapping[BeanKey, tuple[DependencyEdge, ...]]
        reverse: Mapping[BeanKey, tuple[DependencyEdge, ...]]
        coverage: CoverageState
        gaps: tuple[str, ...]

    @dataclass(frozen=True)
    class TypeFact:
        name: str
        direct_supertypes: tuple[str, ...]

    @dataclass(frozen=True)
    class SourceProducer:
        id: str
        context_id: str
        bean_name: str
        aliases: tuple[str, ...]
        declared_type: str
        implementation_type: str | None
        configuration_class: str
        factory_method: str | None
        condition_sources: tuple[str, ...]
        source_file: str
        source_line: int
        evidence_ids: tuple[str, ...]

    @dataclass(frozen=True)
    class SourceCatalog:
        target_id: str
        source_revision: str | None
        types: Mapping[str, TypeFact]
        producers: tuple[SourceProducer, ...]
        gaps: tuple[str, ...]

    @dataclass(frozen=True)
    class Condition:
        id: str
        context: ContextKey
        source: str
        condition_type: str | None
        operator: Literal["leaf", "any", "all", "none", "unknown"]
        reported_outcome: Outcome
        derived_outcome: Outcome
        search: Search
        required_types: tuple[str, ...]
        required_names: tuple[str, ...]
        property_key: Fact[str]
        child_ids: tuple[str, ...]
        children_complete: bool
        reason_code: str
        evidence_ids: tuple[str, ...]

    @dataclass(frozen=True)
    class ConditionReport:
        nodes: Mapping[str, Condition]
        roots_by_source: Mapping[tuple[ContextKey, str], tuple[str, ...]]
        coverage: CoverageState
        gaps: tuple[str, ...]

    @dataclass
    class AnalysisBudget:
        max_visits: int = 20_000
        max_depth: int = 16
        deadline_monotonic: float | None = None
        monotonic: Callable[[], float] | None = None
        consumed_visits: int = 0

        def consume(self, visits: int = 1) -> None:
            raise NotImplementedError

    @dataclass(frozen=True)
    class GraphSlice:
        roots: tuple[BeanKey, ...]
        edges: tuple[tuple[DependencyEdge, int], ...]
        truncated: bool
        gaps: tuple[str, ...]

    @dataclass(frozen=True)
    class BlockingEdge:
        from_id: str
        to_id: str
        reason_code: str
        evidence_ids: tuple[str, ...]

    @dataclass(frozen=True)
    class FailureAnalysis:
        state: Literal["notNeeded", "explained", "partial", "unknown"]
        conditions: tuple[Condition, ...]
        blocking_edges: tuple[BlockingEdge, ...]
        blocking_paths: tuple[tuple[str, ...], ...]
        truncated: bool
        gaps: tuple[str, ...]

    @dataclass(frozen=True)
    class Route:
        id: str
        context: ContextKey
        path: str
        methods: Fact[tuple[str, ...]]
        controller_class: Fact[str]
        evidence_ids: tuple[str, ...]

    @dataclass(frozen=True)
    class Migration:
        id: str
        context: ContextKey
        engine: Literal["flyway", "liquibase"]
        migration_bean: str
        identity: str
        version: Fact[str | None]
        installed_rank: Fact[int]
        state: str
        evidence_ids: tuple[str, ...]

    @dataclass(frozen=True)
    class Domain(Generic[T]):
        value: T | None
        coverage: CoverageState
        gaps: tuple[str, ...]
        error_codes: tuple[str, ...]

    @dataclass(frozen=True)
    class RuntimeSnapshot:
        target_id: str
        snapshot_id: str
        observed_at: str
        beans: Domain[BeanGraph]
        conditions: Domain[ConditionReport]
        routes: Domain[tuple[Route, ...]]
        migrations: Domain[tuple[Migration, ...]]
        profiles: Domain[tuple[str, ...]]
        build_revision: Fact[str]
        startup_time: Fact[str]
        environment_overrides: tuple[str, ...]
        evidence: Mapping[str, Evidence]

    @dataclass(frozen=True)
    class ExpectedBean:
        name: str | None
        type: str | None
        context_id: str | None
        aliases: tuple[str, ...]

    @dataclass(frozen=True)
    class ExpectedRoute:
        path: str
        http_method: str
        controller_class: str | None
        context_id: str | None

    @dataclass(frozen=True)
    class ExpectedMigration:
        engine: Literal["flyway", "liquibase"]
        version: str
        context_id: str | None
        migration_bean: str | None

    @dataclass(frozen=True)
    class InspectBeansQuery:
        target_id: str = "local"
        max_results: int = 50
        cursor: str | None = None
        bean_name_pattern: str | None = None
        type_pattern: str | None = None
        package_prefix: str | None = None
        context_id: str | None = None
        direction: Literal["dependencies", "dependents", "both"] = "both"
        max_depth: int = 2
        condition_depth: int = 4

    @dataclass(frozen=True)
    class DriftQuery:
        target_id: str = "local"
        max_results: int = 50
        cursor: str | None = None
        expected_beans: tuple[ExpectedBean, ...] | None = None
        expected_routes: tuple[ExpectedRoute, ...] | None = None
        expected_migration_version: ExpectedMigration | None = None
        expected_profiles: tuple[str, ...] | None = None
        expected_scope_complete: bool = False
        source_revision: str | None = None
        source_modified_at: str | None = None
        max_depth: int = 2

    @dataclass(frozen=True)
    class Difference:
        id: str
        category: Literal["missingInRuntime", "unexpectedInRuntime"]
        dimension: Literal["beans", "routes", "migrations"]
        identity: str
        context_id: str | None
        expected: str | None
        actual: str | None
        evidence_level: Literal["observed", "inferred", "unknown"]
        affected_dependency_ids: tuple[str, ...]

    @dataclass(frozen=True)
    class ProfileComparison:
        expected: tuple[str, ...]
        actual: tuple[str, ...] | None
        state: Literal["match", "mismatch", "unknown"]

    @dataclass(frozen=True)
    class Inference:
        code: Literal["POSSIBLE_NOT_RESTARTED", "PROFILE_MISMATCH", "POSSIBLE_WRONG_INSTANCE", "ENVIRONMENT_OVERRIDE", "INSUFFICIENT_EVIDENCE"]
        confidence: Literal["supported", "candidate", "unknown"]
        evidence_ids: tuple[str, ...]

    @dataclass(frozen=True)
    class DriftAnalysis:
        differences: tuple[Difference, ...]
        profile: ProfileComparison | None
        inferences: tuple[Inference, ...]
        coverage: CoverageState
        gaps: tuple[str, ...]

Fact 的 known 可以携带 None，表示已知根/原生空版本；其他状态不能用 None 等价为已知缺失。内部构造器应校验 state/value/reason 的组合。AnalysisBudget 是唯一可变控制对象，所有分析阶段共享其 consumed_visits；consume 超限抛本地 AnalysisLimit，时钟由 Services 注入，纯分析器不自行读取系统时间。source-catalog JSON 不含 id/evidence_ids，载入时赋安全序号并构造 Evidence；不得把这两个内部字段当成缺少的外部必填字段。

以下协议放在 src/spring_runtime_mcp/models/services.py。ParsedObservation 是读取器已经执行字节、JSON 深度/节点限制后的短生命周期对象，仍可能含不可信 condition message，只能存在请求处理内存；不能缓存或日志打印。实际 RuntimeReader 必须先通过既有安全网络守卫，本计划只有固定离线实现。SanitizerPipeline 两次调用分别负责安全事实提取和最终出口，不允许 no-op 实现通过验收。

    from dataclasses import dataclass
    from typing import Callable, Mapping, Protocol

    @dataclass(frozen=True)
    class ParsedObservation:
        target_id: str
        snapshot_id: str
        observation_id: str
        observed_at: str
        endpoint_id: Endpoint
        body: Mapping[str, Json] | None
        http_status: int | None
        error_code: str | None
        representation: str

    class RuntimeReader(Protocol):
        def enabled(self, target_id: str, endpoint: Endpoint) -> bool:
            raise NotImplementedError
        async def read(self, target_id: str, endpoint: Endpoint, *, snapshot_id: str) -> ParsedObservation:
            raise NotImplementedError

    class SourceCatalogProvider(Protocol):
        def for_target(self, target_id: str) -> SourceCatalog:
            raise NotImplementedError

    class InputValidator(Protocol):
        def validate(self, tool: str, arguments: Mapping[str, Json]) -> InspectBeansQuery | DriftQuery:
            raise NotImplementedError

    class SanitizerPipeline(Protocol):
        def project_fact(self, kind: str, value: Json) -> Json:
            raise NotImplementedError
        def sanitize(self, value: Mapping[str, Json]) -> dict[str, Json]:
            raise NotImplementedError

    class SnapshotStore(Protocol):
        def create_id(self, target_id: str) -> str:
            raise NotImplementedError
        def put(self, snapshot: RuntimeSnapshot) -> None:
            raise NotImplementedError
        def get(self, snapshot_id: str) -> RuntimeSnapshot:
            raise NotImplementedError
        def issue_cursor(self, tool: str, arguments: Mapping[str, Json], snapshot_id: str, offset: int) -> str:
            raise NotImplementedError
        def resume(self, tool: str, arguments: Mapping[str, Json]) -> tuple[RuntimeSnapshot, int]:
            raise NotImplementedError

    @dataclass(frozen=True)
    class Services:
        reader: RuntimeReader
        sources: SourceCatalogProvider
        validator: InputValidator
        sanitizer: SanitizerPipeline
        snapshots: SnapshotStore
        now_utc: Callable[[], str]
        monotonic: Callable[[], float]

纯函数签名分别实现于 adapters/beans.py、adapters/conditions.py、adapters/source_catalog.py、adapters/drift_support.py、analyzers/bean_graph.py、analyzers/condition_failures.py、analyzers/drift.py。错误不能用空图假装成功；函数可返回带 coverage/gaps 的对象，无法识别根结构时使用本地 NormalizationError，由工具编排映射为错误信封。

    def normalize_beans(observation: ParsedObservation, *, budget: AnalysisBudget) -> BeanGraph:
        raise NotImplementedError

    def normalize_conditions(observation: ParsedObservation, *, budget: AnalysisBudget) -> ConditionReport:
        raise NotImplementedError

    def load_source_catalog(payload: Mapping[str, Json], *, target_id: str, budget: AnalysisBudget) -> SourceCatalog:
        raise NotImplementedError

    def normalize_routes(observation: ParsedObservation, *, budget: AnalysisBudget) -> Domain[tuple[Route, ...]]:
        raise NotImplementedError

    def normalize_migrations(observation: ParsedObservation, *, budget: AnalysisBudget) -> Domain[tuple[Migration, ...]]:
        raise NotImplementedError

    def normalize_profiles(observation: ParsedObservation) -> Domain[tuple[str, ...]]:
        raise NotImplementedError

    def build_dependency_graph(graph: BeanGraph, *, budget: AnalysisBudget) -> BeanGraph:
        raise NotImplementedError

    def resolve_bean_reference(graph: BeanGraph, defining_context: ContextKey, requested_name: str) -> Resolution:
        raise NotImplementedError

    def is_assignable(actual_type: str, expected_type: str, catalog: SourceCatalog, *, budget: AnalysisBudget) -> Fact[bool]:
        raise NotImplementedError

    def slice_dependencies(graph: BeanGraph, roots: tuple[BeanKey, ...], *, direction: str, max_depth: int, budget: AnalysisBudget) -> GraphSlice:
        raise NotImplementedError

    def analyze_missing_beans(query: InspectBeansQuery, graph: BeanGraph, report: ConditionReport, catalog: SourceCatalog, *, budget: AnalysisBudget) -> FailureAnalysis:
        raise NotImplementedError

    def compare_runtime(query: DriftQuery, snapshot: RuntimeSnapshot, catalog: SourceCatalog, *, budget: AnalysisBudget) -> DriftAnalysis:
        raise NotImplementedError

工具入口和协议出口分别实现于 tools/inspect_beans.py、tools/detect_drift.py、contracts/registry.py、contracts/serialization.py。这里 StructuredEnvelope、CallToolResult 与 RpcFrame 为 dict[str, Json] 的语义别名，最终键/约束由既有版本 2 outputSchema 逐项校验；这不是允许任意扩展字段。处理器必须先通过 InputValidator，不能让未经校验的 Mapping 流入纯分析器。

    from typing import Awaitable, Callable, Mapping, TypeAlias

    StructuredEnvelope: TypeAlias = dict[str, Json]
    CallToolResult: TypeAlias = dict[str, Json]
    RpcFrame: TypeAlias = dict[str, Json]
    ToolHandler: TypeAlias = Callable[[Mapping[str, Json], Services], Awaitable[StructuredEnvelope]]

    async def springboot_inspect_beans(arguments: Mapping[str, Json], services: Services) -> StructuredEnvelope:
        raise NotImplementedError

    async def springboot_detect_drift(arguments: Mapping[str, Json], services: Services) -> StructuredEnvelope:
        raise NotImplementedError

    class ToolRegistrar(Protocol):
        def register(self, definition: Mapping[str, Json], handler: ToolHandler) -> None:
            raise NotImplementedError

    def register_tools(registrar: ToolRegistrar, definitions: tuple[Mapping[str, Json], ...]) -> None:
        raise NotImplementedError

    def paginate_and_project(tool: str, analysis: GraphSlice | DriftAnalysis, *, query: InspectBeansQuery | DriftQuery, snapshot: RuntimeSnapshot, failure: FailureAnalysis | None, services: Services) -> StructuredEnvelope:
        raise NotImplementedError

    def serialize_tool_response(tool: str, envelope: StructuredEnvelope, *, request_id: str | int, sanitizer: SanitizerPipeline, validate_output: Callable[[str, Mapping[str, Json]], None], max_bytes: int = 16_384) -> bytes:
        raise NotImplementedError

register_tools 必须从名称选择两个真实处理器，其余六个绑定固定的 capability-not-implemented 处理器；不依赖隐式全局 services。serialize_tool_response 返回可直接写入传输的 UTF-8 JSON-RPC bytes，框架边界在写出前再次确认长度；不能把 bytes 解包后追加未经预算的文字。replay.py 的 main(argv: list[str] | None = None) -> int 使用 asyncio.run 驱动相同 handler；结果写 stdout 的唯一一行，退出 0 表示重放协议完成，即使领域诊断发现差异。输入/场景非法退出 2，内部异常退出 1 且 stderr 仅有本地安全代码。

实际开发依赖为 pytest==8.4.2、jsonschema==4.25.1、ruff==0.14.1，通过上述 uv --no-project 命令使用，不增加运行依赖或假设 pyproject.toml 已存在。离线 InputValidator/输出校验器使用 Draft202012Validator 与 FormatChecker；抽取每个 Schema 后仍须局部解析 $defs，禁止网络 $ref。未来官方 MCP SDK 与安全 RuntimeReader 的生产实现不在本次依赖列表，接入前必须独立证明协议版本和网络安全约束。

修订记录：2026-10-16，Codex 创建本计划；根据当前无服务实现的仓库状态明确离线闭环范围，补足接口/工厂来源证据、父子容器、两跳失败链、四维漂移、恢复规则和无新增公开 Schema 的实施路径。2026-10-16T08:23:59Z 复核后补充了布尔子边与归因边隔离、共享消耗预算、类型不兼容证据及目录重载后的游标失效，避免实现时产生错误因果或分页漂移。规划格式参考 [OpenAI ExecPlan 指导](https://developers.openai.com/cookbook/articles/codex_exec_plans)；JSON 结构依据官方 [Beans](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/beans.html)与 [Conditions](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/conditions.html)说明，本文样例为合成 fixture，不宣称是真实版本集成样本。

实施修订：2026-10-16T09:09:45Z，按用户选择范围 2 落地 Bean 模块，保留 drift 和宿主待办；实际命令见 SPEC.md §5.1。
