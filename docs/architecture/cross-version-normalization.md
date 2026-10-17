# Spring Boot 跨版本探测与响应归一化架构设计

## 1. 目标实例能力握手与版本探测流程（Capability Probing Flow）

### 1.1 范围与事实边界

本文细化 [SPEC.md](../../SPEC.md)，所有读取先经过[安全架构规格书](security-and-sanitization.md)的注册表、连接守卫、只读和脱敏管道。当前仓库尚无运行实现；接口和验收项是实施要求，不代表已验证全部版本。设计覆盖 Boot 2.7.x、3.0–3.4.x 和 4.x；正式支持必须对应具体小版本、MVC/WebFlux、媒体类型和真实脱敏 fixture。未验证版本可以做结构识别，但不得标为正式兼容。

八个**源诊断域**为 health、beans、conditions、mappings、flyway、liquibase、startup、OpenAPI。Drift Detection 消费这些模型，不另算一个源域。契约版本 2 另增配置支持域及 springboot_inspect_config：默认关闭、显式登记后允许受控读取 env/configprops，禁止完整原始值树出站；其投影见[调用策略](../contracts/tool-calling-policy.md)。此变更替代先前网络读取禁令，不改变下表八个核心源域。

来源依据包括 [Boot 2.7.18 Web API](https://docs.spring.io/spring-boot/docs/2.7.18/actuator-api/htmlsingle/)、[Boot 3.0.13 Web API](https://docs.spring.io/spring-boot/docs/3.0.13/actuator-api/htmlsingle/)、[Boot 3.4 Web API](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/index.html)及下文各端点的 4.x 官方结构。协议版本、框架版本和部署应用版本必须分开记录。

### 1.2 惰性能力握手

“握手”指本服务内部的诊断准备流程，不是新的 MCP 方法或额外公开工具，不要求目标安装额外依赖，也不在 MCP 启动时扫描全部端点。

```text
登记目标与批准能力
  → 校验工具参数、目标、端点与预算
  → 复用本次诊断已经需要的受控 GET
  → 分类 HTTP/Content-Type/JSON 形状
  → 记录 EndpointCapability 与版本证据
  → 选择形状适配器，构造归一化模型
  → 解析本次所需关系
  → 脱敏、过滤、分页与统一响应

策略/授权/网络拒绝 → 结构化 DiagnosticRemediation，终止相关分支
已知字段缺失/局部格式问题 → partial + 问题定位，保留可解释记录
根结构无法识别 → invalid + Recipe；不猜测空成功
```

能力状态为 notProbed、disabled、readable、denied、unavailable、invalid、tooLarge。只允许请求已启用的固定端点；health 可读不意味着 beans/conditions 可读，404 不等于版本不支持，401/403 也不证明端点存在。默认不请求 Actuator 根 discovery，也不追随 `_links` 中的 URL。

单次工具总时限 15 秒、每上游请求绝对时限 5 秒、每目标最多 2 个并发读取。探测复用实际诊断读取，不额外扫描版本；OpenAPI 使用第 3.4 节最多三个预登记候选。请求预算耗尽时保留 notProbed/partial，不能以探测名义突破时限、大小或权限。

### 1.3 媒体协商与版本证据

Actuator 首次请求发送：

```http
Accept: application/vnd.spring-boot.actuator.v3+json, application/json;q=0.9
```

只有明确收到 406 时，才允许对**相同已批准端点**追加一次 `Accept: application/vnd.spring-boot.actuator.v2+json`，计入同一时限；不能对 401/403/404/405 改 Accept 重试。OpenAPI 使用 `application/json`，不发送 Actuator vendor media type。忽略 Content-Type 的 charset 参数比较基本媒体类型；不接受 HTML 登录页作为 JSON。返回 application/json 时，按已验证的结构判断表示，不凭响应头虚构版本。

Boot 2.7 支持 V2/V3；3.4 与 4.x 官方 health 文档同样说明可请求 V2。**V3 不表示 Boot 3，V2 也不表示 Boot 2**。框架主版本不能决定 health 必然使用哪棵树。[Boot 3.4 health](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/health.html)、[Boot 4.0 health](https://docs.spring.io/spring-boot/4.0/api/rest/actuator/health.html)。

| 证据 | 记录方式 | 不允许的推断 |
| --- | --- | --- |
| 已获准 startup 响应的 springBootVersion | reported，保留 endpoint/观察时间；仅在该诊断本来读取 startup 时收集 | 不为了版本探测自动启用 startup，更不能 POST |
| 操作者登记的 Boot 版本 | declared；与运行证据矛盾时保留冲突 | 声明不等于实际实例版本 |
| 可选 info 中的 build.version/git | 应用构建/源码证据，供漂移比较 | build.version 通常是应用版本，不能当 Boot 版本 |
| vendor media type、javax/jakarta、字段形状 | 表示/命名空间/shapeProfile；仅作适配线索 | 不能确定 Boot 小版本或迁移引擎版本 |
| 没有可靠版本证据 | version=unknown；按已识别结构适配 | 不读 env、configprops、日志或 banner 补齐 |

每个端点单独选择表示和适配器；同一目标出现结构冲突时记录 SHAPE_CONFLICT/VERSION_EVIDENCE_CONFLICT，不把整个实例强制塞入一个按版本号选择的反序列化器。shapeProfile 只由固定字段和类型特征构成，不返回原始响应哈希或包含秘密的动态键摘要。

## 2. 八大诊断域跨版本 JSON 路径映射与归一化对照表

### 2.1 官方路径映射

下表路径是结构说明，`*` 表示遍历字典项，不是可执行的用户 JSONPath；`[]` 表示遍历数组。默认管理前缀记作 `/actuator`，实际路径由启动配置确定。所有同名记录保留 target、context、dispatcher/迁移 Bean 和记录身份，不做全局名称合并。

| 源诊断域 | 官方原始 JSON 路径 | Boot 2.7.x / 3.0–3.4.x / 4.x 差异与处理 | 归一化接口 |
| --- | --- | --- | --- |
| Health | `status`；V3：`components.*.status`、递归 `components.*.components`、叶子 `details`；V2 聚合：`details.*.status`，子组件 details 继续递归 | 表示由媒体协商决定；V3 的 details 也是合法叶子诊断对象，不能见到 details 就判 V2。权限可能只留下 status。健康 HTTP 503 的合法 JSON 仍适配。 | NormalizedHealthNode |
| Beans | `contexts.*.parentId`；`contexts.*.beans.*.{aliases,scope,type,resource,dependencies}` | 三代共享该主形状；resource 可缺失。type 是报告的类型，不保证可还原创建配置类、FactoryBean 或工厂方法。 | NormalizedBean |
| Conditions | `contexts.*.positiveMatches.*[]`；`negativeMatches.*.matched[]` / `notMatched[]`；每项 condition/message；`unconditionalClasses[]`、parentId | 外层结构稳定；嵌套条件通常编码在 message 字符串，不存在官方通用 children JSON 树。条件类、包和消息 grammar 可变化。 | NormalizedCondition |
| Mappings | `contexts.*.mappings.dispatcherServlets.*[]` / `dispatcherHandlers.*[]`；`details.handlerMethod.{className,name,descriptor}`、`handlerFunction`、`requestMappingConditions`、predicate | MVC/WebFlux 容器键分别遍历；dispatcherServlet/webHandler 只是常见名字。结构化字段可能缺失，函数路由可能只有 predicate/handlerFunction；javax/jakarta 保留原名并生成比较键。 | NormalizedMapping |
| Flyway | `contexts.*.flywayBeans.*.migrations[]`；version、script、state、checksum、executionTime、installedRank、installedOn；context parentId | 多个 Flyway Bean 分开；checksum 是可空 Number，executionTime 单位 ms，version 可空。state 随引擎演进，未知枚举保留分类缺口；不能以 Boot 版本猜引擎算法。 | NormalizedFlywayMigration |
| Liquibase | `contexts.*.liquibaseBeans.*.changeSets[]`；changeLog、id、author、checksum、execType、dateExecuted、orderExecuted；context parentId | 注意 changeSets 的 S 大写与 changeLog 的 L 大写；checksum 为 String，可能包含版本前缀。接口不承诺待执行列表或执行耗时；缺失不能补 0。 | NormalizedLiquibaseChangeSet |
| Startup | `springBootVersion`、`timeline.startTime`、`timeline.events[]`；事件 startTime/endTime/duration；startupStep 的 id/parentId/name/tags[] | 不使用 contexts。duration 为 ISO-8601 duration；tags 是 key/value 数组，不能无声转字典丢掉重复项。GET 快照依赖原生 BufferingApplicationStartup，三代都不能 POST 回退。 | NormalizedStartupStep |
| OpenAPI | `openapi`、`paths.*.<method>`、operationId；parameters/requestBody/responses；`components.schemas.*` 及本地 `$ref` | 这是 springdoc/OpenAPI 契约，不是 Actuator vendor JSON；文档版本取决于 springdoc/配置，不由 Boot 主版本推断。自定义路径与管理端口模式见 3.4。 | NormalizedOpenApiFragment |

端点字段分别依据 [beans](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/beans.html)、[conditions](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/conditions.html)、[mappings](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/mappings.html)、[flyway](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/flyway.html)、[liquibase](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/liquibase.html)与 [startup](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/startup.html) 官方表定义；4.x 对照包含 [mappings](https://docs.spring.io/spring-boot/api/rest/actuator/mappings.html)、[flyway](https://docs.spring.io/spring-boot/api/rest/actuator/flyway.html)、[liquibase](https://docs.spring.io/spring-boot/api/rest/actuator/liquibase.html)和 [conditions](https://docs.spring.io/spring-boot/4.0/api/rest/actuator/conditions.html)。这些引用是结构依据，不能替代具体版本的 fixture/集成验证。

### 2.2 公共模型约定

以下使用 TypeScript 描述语言无关接口，Python 实现映射为 Pydantic 模型，不增加 TypeScript/Java 运行依赖。归一化模型和公开工具响应是两层：模型可表达完整受限图，出口仍须执行脱敏、字段投影、50 条分页和完整 16,384 字节预算；不能一次返回整个模型集合。

```typescript
type EndpointId = "health" | "beans" | "conditions" | "mappings" |
  "flyway" | "liquibase" | "startup" | "openapi" | "info" | "env" | "configprops";
type ToolName = "springboot_analyze_startup" |
  "springboot_check_migrations" |
  "springboot_detect_drift" |
  "springboot_diagnose_health" |
  "springboot_inspect_beans" |
  "springboot_inspect_config" |
  "springboot_search_openapi" |
  "springboot_verify_mappings";
type Fact<T> = { state: "known"; value: T } |
  { state: "unknown" | "notReported" | "redacted"; reasonCode: string };
type Outcome = "matched" | "notMatched" | "unknown";
type SafeScalar = string | number | boolean | null;
type SafeJson = SafeScalar | readonly SafeJson[] |
  { readonly [key: string]: SafeJson };

interface SourceRef {
  targetId: string;
  snapshotId: string;               // 多端点诊断的逻辑快照，不承诺原子性
  observationId: string;            // 该端点本次响应
  observedAt: string;               // ISO-8601，保留原有时区/精度
  endpointId: EndpointId;
  representation: "actuatorV2" | "actuatorV3" | "json" | "openapi";
  shapePath: string;                // 固定路径模板，不拼接原始动态键
  recordOrdinal: number;
  adapterId: string;
}
interface Issue {
  code: string;                     // 本地定义，不复制目标自由文本
  severity: "info" | "warning" | "error";
  source: SourceRef;
}
interface Entity { id: string; source: SourceRef; issues: readonly Issue[]; }
interface ContextKey { targetId: string; snapshotId: string; contextId: string; }
interface NormalizedContext {
  key: ContextKey;
  parent: Fact<ContextKey | null>;
  relationState: "resolved" | "unobserved" | "conflicting" | "cycle";
}
interface BeanKey {
  context: ContextKey;
  canonicalName: string;
  beanKind: "bean" | "factory" | "product" | "unknown";
}
interface BeanReference {
  requestedName: Fact<string>;
  resolution: "resolved" | "ambiguous" | "unresolved";
  target: Fact<BeanKey>;
}
interface BootEvidence {
  version: Fact<string>;
  kind: "reported" | "declared" | "unknown" | "conflicting";
  supportLevel: "verified" | "experimental" | "unverified";
  source: Fact<SourceRef>;
}
interface EndpointCapability {
  endpointId: EndpointId;
  state: "notProbed" | "disabled" | "readable" | "denied" |
    "unavailable" | "invalid" | "tooLarge";
  httpStatus: Fact<number>;
  versionEvidence: BootEvidence;
  remediation: readonly DiagnosticRemediation[];
}
interface DomainResult<T> {
  items: readonly T[];
  contexts: readonly NormalizedContext[];
  completeness: "complete" | "partial" | "unavailable";
  issues: readonly Issue[];
  remediation: readonly DiagnosticRemediation[];
}
```

内部关系键包含 targetId、逻辑 snapshotId 和源 contextId，不以裸 Bean 名或裸 contextId 合并实例。原始键只用于受限关系解析，不直接作为公开路径；出口使用内部 opaque ID 和通过脱敏的标签。不同端点的时间、parentId 或实例标识冲突必须保留，不能由最后一个响应覆盖。缺失父节点表示未观察到，不自动提升为根。

已知数组为空与字段未报告不同；Fact 不用空字符串、0 或空数组表示 unknown。JSON 数字必须验证范围；64 位步骤 ID 和纳秒值用十进制字符串，禁止经 JS Number 丢失精度。输出字符串、状态原值和引用名称仍须遵守安全规格的类型白名单，不因进入 Fact 就可信。

### 2.3 八个领域接口

```typescript
interface SafeDetail { key: string; value: Fact<SafeScalar>; }
interface NormalizedHealthNode extends Entity {
  group: "application" | "liveness" | "readiness";
  componentPath: readonly string[];
  parentNodeId: Fact<string | null>;
  status: Fact<string>;             // 保留自定义状态，不强制当 UP
  nodeKind: "aggregate" | "component" | "unknown";
  details: readonly SafeDetail[];
  errorType: Fact<string>;          // 类型/本地错误码，不返回原始异常文本
  childrenComplete: boolean;
}
interface Producer {
  kind: "configuration" | "factoryMethod" | "factoryBean";
  configurationClass: Fact<string>;
  methodName: Fact<string>;
  evidenceLevel: "observed" | "correlated";
}
interface NormalizedBean extends Entity {
  key: BeanKey;
  aliases: readonly string[];
  reportedType: Fact<string>;
  portableTypeKey: Fact<string>;
  scope: Fact<string>;
  resourceId: Fact<string>;
  producer: Fact<Producer>;
  dependencies: readonly BeanReference[];
  dependents: readonly BeanReference[];
}
interface NormalizedCondition extends Entity {
  context: ContextKey;
  configurationSource: Fact<string>;
  conditionType: Fact<string>;
  operator: "leaf" | "any" | "all" | "none" | "unknown";
  reportedOutcome: Outcome;
  derivedOutcome: Outcome;
  childIds: readonly string[];
  childrenComplete: boolean;
  requiredBeans: readonly BeanReference[];
  searchStrategy: Fact<"CURRENT" | "PARENTS" | "ALL">;
}
interface TypeIdentity { original: string; portableKey: string; }
interface MethodSignature {
  originalDescriptor: string;
  parameters: readonly TypeIdentity[];
  returnType: TypeIdentity;
}
interface RouteCondition {
  kind: "path" | "method" | "header" | "param" | "consumes" | "produces";
  name: Fact<string>;
  value: Fact<string>;
  negated: Fact<boolean>;
}
interface NormalizedMapping extends Entity {
  context: ContextKey;
  stack: "mvc" | "webflux";
  dispatcherName: Fact<string>;
  handlerKind: "method" | "function" | "resource" | "unknown";
  handlerClass: Fact<string>;
  handlerMethod: Fact<string>;
  signature: Fact<MethodSignature>;
  conditions: readonly RouteCondition[];
  methodsUnrestricted: Fact<boolean>;
  predicateParsed: boolean;
  matchingComplete: boolean;
}
type MigrationState = "APPLIED" | "PENDING" | "FAILED" |
  "OUT_OF_ORDER" | "MISSING_FAILED" | "UNKNOWN";
interface Checksum {
  engine: "flyway" | "liquibase";
  value: Fact<string>;
  originalType: "number" | "string" | "absent";
  algorithmVersion: Fact<string>;
}
interface NormalizedFlywayMigration extends Entity {
  context: ContextKey;
  migrationBean: string;
  version: Fact<string | null>;
  script: Fact<string>;
  rawState: Fact<string>;
  state: MigrationState;
  stateEvidence: "reported" | "baselineInferred" | "unknown";
  checksum: Checksum;
  executionTimeMs: Fact<number>;
  installedRank: Fact<number>;
  installedOn: Fact<string>;
}
interface NormalizedLiquibaseChangeSet extends Entity {
  context: ContextKey;
  migrationBean: string;
  changeLog: Fact<string>;
  changeSetId: Fact<string>;
  author: Fact<string>;
  execType: Fact<string>;
  state: MigrationState;
  stateEvidence: "reported" | "baselineInferred" | "unknown";
  checksum: Checksum;
  orderExecuted: Fact<number>;
  dateExecuted: Fact<string>;
  executionTimeMs: Fact<number>;      // 标准响应未提供时 notReported
}
interface StartupTag { key: string; value: Fact<string>; }
interface NormalizedStartupStep extends Entity {
  stepId: string;
  parentStepId: Fact<string | null>;
  name: Fact<string>;
  startedAt: Fact<string>;
  endedAt: Fact<string>;
  durationNanos: Fact<string>;
  tags: readonly StartupTag[];
  relationState: "resolved" | "orphan" | "cycle" | "duplicate";
}
interface SchemaEdge {
  keyword: "properties" | "items" | "additionalProperties" |
    "allOf" | "oneOf" | "anyOf" | "$ref";
  member: Fact<string>;
  targetNodeId: Fact<string>;
  state: "resolved" | "cycle" | "depthLimit" | "externalBlocked" | "unknown";
}
interface NormalizedSchemaNode extends Entity {
  schemaName: Fact<string>;
  types: readonly string[];
  required: readonly string[];
  enumValues: readonly Fact<SafeScalar>[];
  edges: readonly SchemaEdge[];
}
interface NormalizedOpenApiFragment extends Entity {
  documentVersion: Fact<string>;
  candidateId: string;               // 预登记候选，不是目标提供的 URL
  kind: "operation" | "schema";
  path: Fact<string>;
  method: Fact<string>;
  operationId: Fact<string>;
  schemaNodes: readonly NormalizedSchemaNode[];
  refDepth: number;
  expansionComplete: boolean;
}
```

Health V3 只从 components 生成子节点，details 只经安全提取生成批准属性。V2 只有在表示证据和已知 health grammar 支持时，才将 details 中的健康节点递归展开；自定义对象也可能有 status，存在歧义时返回 AMBIGUOUS_HEALTH_DETAILS，不把所有 details 当子组件。只返回整体 status 时，子组件可见性为 unknown，不能认定没有 db/redis 等组件。

Flyway SUCCESS 映射 APPLIED、PENDING 映射 PENDING、FAILED 映射 FAILED、OUT_OF_ORDER、MISSING_FAILED 单独保留；其他原生状态只在有明确、验证过的映射时转换，否则 UNKNOWN。Liquibase EXECUTED/RERAN/MARK_RAN 可映射为记录层面的 APPLIED，但 MARK_RAN 不证明执行过脚本；SKIPPED 不等于 APPLIED，FAILED 映射 FAILED。Liquibase 标准端点没有完整 pending 集合；缺少可信完整基线时不能输出“无待执行”。原始 description/comments 不输出。

Flyway 数字 checksum 以无损十进制字符串存储，Liquibase 字符串含前缀时原样保留合法值。算法版本缺失为 notReported，不能用 Boot 版本替代。startup duration 转换为精确纳秒字符串，不用二进制浮点计算；不将并发/嵌套耗时相加冒充总启动时间，重复 tag 不丢弃。`GET /actuator/startup` 读取非破坏性快照；`POST /actuator/startup` 排空启动步骤缓冲区，会改变后续观察结果，永久禁止调用。

### 2.4 javax/jakarta 与方法描述符

`handlerMethod.descriptor` 按 JVM 方法描述符 grammar 解析：支持原始类型、`L...;` 对象、数组和仅允许作为返回值的 V；解析完整字符串并验证结尾，不能按分号或逗号随意拆分。[JVM 方法描述符规范](https://docs.oracle.com/javase/specs/jvms/se17/html/jvms-4.html#jvms-4.3.3)。

保留真实 descriptor 和原始类型身份；只为显式迁移目录创建 portableKey，例如 javax.servlet.http.HttpServletRequest 与 jakarta.servlet.http.HttpServletRequest 都映射为 `ee:servlet.http.HttpServletRequest`；javax.persistence.* 与 jakarta.persistence.* 同理。数组维度和基础类型不变。javax.sql、javax.crypto、javax.xml、javax.annotation.processing 不转换，禁止全局 `javax → jakarta` 替换。

portableKey 用于跨版本比较，不表示 Java 二进制兼容，也不能覆盖真实 Handler 类型。Boot 4 自动配置包或类名改变时，保留实际 configurationSource；只有经过验证的显式目录可关联，不能按短类名强行合并。无效 descriptor、未知 predicate 或不能确定的条件类型形成结构化 issue，而不是抛出未捕获解析异常。

## 3. 间接引用与多跳关系解析算法（Aliases, Parent Contexts & Nested Conditions）

### 3.1 Context 图与别名双向索引

每次逻辑快照先遍历完整、受预算限制的 contexts，再建立关系；不得先分页再查父容器或构建逆向依赖。parentId 冲突、缺失节点、循环独立记录，来源为各 endpoint 的 observationId。只有对应已知适配器能证明缺失 parentId 表示根时才记录已知 null，否则保留 unknown。不同 targetId 永远不自动合并，即使 contextId、git revision 或 Bean 名相同。

每个 Context 建立 `nameToCanonical: name → candidate canonical names` 与 `canonicalToNames: canonical → canonical+aliases`。aliases 元素直接指向其所属 Bean 的字典键；去重、保留来源，别名冲突或与另一个实际 Bean 名冲突时不采用最后写入者。标准 aliases 已提供归属关系；额外结构化基线若含别名链，沿链递归，检测循环，不凭字符串猜中间别名。

```python
def resolve_bean_reference(defining_context, requested_name, snapshot):
    current = defining_context
    seen_contexts = set()
    while current is not None:
        if current in seen_contexts:
            return unresolved("CONTEXT_CYCLE")
        seen_contexts.add(current)
        index = snapshot.index_for(current)
        if index is None or not index.complete:
            return unresolved("BEAN_INDEX_INCOMPLETE")
        candidates = index.resolve_local_names_and_aliases(requested_name)
        if candidates.ambiguous:
            return unresolved("ALIAS_AMBIGUOUS", candidates.safe_ids)
        if candidates.one:
            return resolved(candidates.one)
        parent = snapshot.parent_relation(current)
        if not parent.proven:
            return unresolved("PARENT_CONTEXT_UNOBSERVED")
        current = parent.value
    return unresolved("NOT_FOUND_IN_COMPLETE_VISIBLE_HIERARCHY")
```

完整可见层级中的 notFound 与未观察父层级不同。查找始于**依赖所属 Bean 的定义 Context**，而不是当前查询的子 Context。子容器同名 Bean 遮蔽父容器；父容器不向子容器寻找依赖；本地别名冲突不通过查询父容器“解决”。FactoryBean 与产品只有明确证据时区分，不能读取或实例化 `&name` 来补证。

### 3.2 正向、反向与传递依赖

对每个已观察 Bean 的 dependencies[] 执行上述解析，先构建有向边，再在同一个完整图上反转得到 dependents。反向边保留原依赖所属 Context；不得把父 Bean 被子 Bean 依赖误解为父容器可查找子 Bean。

BFS 使用完整 BeanKey 去重，保存边和到起点的最短路径；对跨 Context、别名和环保持稳定身份。默认深度 2，允许到 16；达到深度/20,000 遍历预算时返回边界标记，不能声明完整传递闭包。一次逻辑快照内的逆向索引共享，不重复做每个 Bean 的全图扫描。

验收图至少包含：parent 定义 repository；child 定义 service 并通过 repositoryAlias 依赖它；child 同时定义与 parent 同名的 client；service → repository → datasource → connectionFactory 三跳；不同 target 上同名图；别名冲突和 Context 环。断言父 Bean 的反向列表可以出现 child.service，但它的正向查找不能进入 child。

### 3.3 条件树、嵌套组合与阻塞链

先用所在 JSON 分支确定顶层 reportedOutcome：positiveMatches 和 negativeMatches.matched 是匹配，negativeMatches.notMatched 是未匹配；unconditionalClasses 只表示无条件配置，不伪造叶子条件。不能用消息中的“did not find”直接判失败，OnMissingBean 的匹配可能正是未发现 Bean。

message 仅在受限内存中由版本化 grammar 解析为条件节点。识别 AnyNestedCondition=ANY、AllNestedConditions=ALL、NoneNestedConditions=NONE，保留类/配置源和有证据的子关系。官方没有通用 children 字段；分号、类名层级和计数只能在已验证 grammar 中联合使用，不能用一个 split/regex 宣称恢复任意条件树。无法唯一确定的子节点保持 unknown，原文省略。

| 组合 | 三值计算 |
| --- | --- |
| ANY | 任一 matched 即 matched；全部已知 notMatched 才为 notMatched；否则 unknown |
| ALL | 任一 notMatched 即 notMatched；全部已知 matched 才为 matched；否则 unknown |
| NONE | 任一 matched 即 notMatched；全部已知 notMatched 才为 matched；否则 unknown |

未完整解析的 children 不按空集合处理。derivedOutcome 与 reportedOutcome 冲突时记录 CONDITION_PARSE_CONFLICT，保留报告事实，不能覆盖为猜测结果。

条件→Bean/配置关联需要明确名称、类型、配置/工厂基线或报告引用；多个同短名配置只列候选。对 `@ConditionalOnBean` 等已解析的 SearchStrategy，CURRENT 只查当前、PARENTS 只查祖先、ALL 查当前与祖先；未知策略不默认为 ALL。继续关联缺失 Bean 的条件，形成 A 依赖 B、B 被 C 条件阻止的有来源阻塞链；普通 Bean 依赖环与条件引用环分别检测。深度默认 4、上限 16；缺失引用、未解析消息或预算触顶均返回 partial 和缺口，不制造因果边。

### 3.4 OpenAPI 自定义路径与管理端口回退

OpenAPI 能力显式启用后，启动配置生成并冻结最多三个候选，全部属于**同一已登记 targetId/authority**。`customPath` 由操作者提供，可取自其确认的 springdoc.api-docs.path；不能从目标响应、env、configprops、Swagger UI 或工具参数动态获取新路径。

| 本地模式 | 预登记顺序 | 行为 |
| --- | --- | --- |
| auto（启用 OpenAPI 后的默认模式） | customPath（若有）→ `/v3/api-docs` → `<actuatorBase>/openapi` | 去重后最多三个；仅 404 允许请求下一个 |
| application | customPath（若有）→ `/v3/api-docs` | 不探测管理模式路径 |
| management | `<actuatorBase>/openapi` | 不使用 customPath；已知管理端口模式时不做无意义回退 |

每个候选都在启动时通过端点模板和路径检查登记；同一工具调用只能迭代这个有限集合。每个 GET 均独立执行安全状态机，不能跟随重定向。成功且通过 OpenAPI 3.0/3.1 根结构验证后停止；401/403、TLS/网络错误、200 非法文档、405、406、超限或预算耗尽均停止，生成 Recipe，不能借换路径绕过鉴权。候选 404 表示该路径当前不可用，不证明未安装 springdoc。

不同 application/management 端口是不同 authority，必须分别登记并授权目标 ID；不从 management.server.port 的响应值或猜测端口扫描。若指定 targetId 不能提供文档，Recipe 可建议操作者选择另一个已登记目标；服务不自动切换或转发本目标凭据，也不因相同 build revision 合并实例证据。

springdoc 默认 JSON 路径为 `/v3/api-docs`；设置 springdoc.use-management-port=true 后，官方示例在管理端口的 `/actuator/openapi` 暴露，并说明此模式下 springdoc 的路径配置不适用。管理基路径仍使用操作者登记值，不硬编码 actuator；只建议暴露 openapi，不额外开放 swagger-ui。[springdoc 属性](https://springdoc.org/properties.html)、[管理端口集成](https://springdoc.org/modules.html)。

### 3.5 Schema 引用与层级验收

同文档 `$ref` 解析先处理 PathItem/Parameter/RequestBody/Response 包装，最多 8 跳；Schema 首次引用为深度 1，默认展开到 2，最大 8。JSON Pointer 正确处理 `~0`/`~1`；路径递归栈识别环，memo 只缓存解析节点，不能让某个深度受限查询污染另一深度的展开。allOf/oneOf/anyOf 保留边语义，不做不安全拍平；远程/file 引用标为 externalBlocked，绝不请求。

Startup 的 parentId 是步骤关系，不是 ApplicationContext；health/startup/OpenAPI 不能人为加入默认 application Context。迁移聚合必须遍历每个 Context 的全部 flywayBeans/liquibaseBeans，保留各自记录顺序；Flyway version=null 的 repeatable 记录不能按空版本相互覆盖，Liquibase 同名 id 必须结合 changelog/author/迁移 Bean 与 Context 区分。

| 编号 | 最低验收 |
| --- | --- |
| C01 | Boot 2.7 V3、3.4 V2、4.x V3 表示均归一化；不从 media type 反推主版本 |
| C02 | 多 dispatcher、自定义 webHandler 名、MVC/WebFlux、函数路由；缺少 descriptor/predicate grammar 时 partial |
| C03 | javax/jakarta 对应迁移类型比较键一致；javax.sql 不变；原 descriptor 保留；无效描述符安全拒绝 |
| C04 | 多目标同名 Context/Bean、多父层、别名双向索引、遮蔽和至少三跳依赖；父容器不访问子容器 |
| C05 | ANY/ALL/NONE 嵌套、未知子节点、OnMissingBean 正向匹配、SearchStrategy、传递阻塞及环 |
| C06 | 多 Flyway/Liquibase Bean、可空版本、不同 checksum 类型、MARK_RAN/OUT_OF_ORDER、缺失耗时和未知状态 |
| C07 | OpenAPI 三候选 404 回退、首个成功停止、401/403 不回退；管理模式忽略自定义路径且不切换端口 |
| C08 | 共享/循环/失效/外部 refs、两阶与更深引用、JSON Pointer 转义；深度和 memo 不混淆 |
| C09 | Context 缺失/冲突/环、局部错误、多端点观察时间不同；原始消息不出站 |
| C10 | 接口/Recipe/错误均通过脱敏与 16KB 完整回包预算；无真实服务时离线 fixture 可运行 |

fixture 按具体 Boot 小版本、表示和 Web 栈登记，至少覆盖 2.7、3.0、3.1、3.2、3.3、3.4 和选定 4.x。未取得样本或集成未通过的组合保持 unverified/experimental；文档结构核验不是运行时兼容性测试。

## 4. 端点缺失与权限拦截的结构化自愈配方表（Remediation Recipes Matrix）

### 4.1 DiagnosticRemediation 接口与错误边界

传输层只提供安全错误代码与状态；适配器返回 DomainResult，不对预期的拒绝、缺字段或不支持结构抛出未捕获异常。编程缺陷由最外层映射为 INTERNAL_ERROR 并记录无原文的内部事件，不伪装成可重试 404。已经成功的域继续返回 partial，不能因为 Liquibase 不可用而丢弃可用 Flyway 证据。

```typescript
type ConfigKey = "management.endpoints.web.exposure.include" |
  "management.endpoints.web.exposure.exclude" |
  "management.endpoints.web.base-path" | "management.server.port" |
  "management.endpoints.access.max-permitted" |
  "management.endpoint.health.show-details" |
  "management.endpoint.health.show-components" |
  "management.endpoint.health.roles" |
  "springdoc.api-docs.path" | "springdoc.api-docs.enabled" |
  "springdoc.use-management-port" |
  `management.endpoint.${EndpointId}.enabled` |
  `management.endpoint.${EndpointId}.access`;
interface ConfigEdit {
  key: ConfigKey;
  operation: "mergeInclude" | "removeRequestedExclusion" | "setIfConfirmed" | "review";
  suggestedValue: SafeScalar | readonly string[];
  onlyIf: string;                   // 本地规则模板的前置条件
}
interface CodeEdit {
  kind: "startupBuffering" | "existingSecurityRule";
  anchor: string;                   // 语义锚点，不伪造文件行号
  snippet: string;                  // 本地固定模板，无目标自由文本
}
interface DiagnosticRemediation {
  recipeId: string;
  endpointId: EndpointId;
  errorCode: string;
  certainty: "observed" | "suspected" | "unknown";
  configFileHint: "application.yml|properties" | "existingSecurityConfiguration" |
    "mainApplicationClass" | "localMcpConfiguration";
  lineNumber: number | null;
  configEdits: readonly ConfigEdit[];
  codeEdits: readonly CodeEdit[];
  credentialEnvNames: readonly string[];
  approval: "requiredUnlessPreviouslyAuthorized";
  restartRequired: "yes" | "no" | "unknown";
  retry: { tool: ToolName; arguments: { readonly [key: string]: SafeJson } };
  verification: readonly string[];  // 本地生成的具体只读验收步骤
}
```

Recipe 告诉 Agent 精确配置键、修改方式及前提。只有在获权应用工作区实际定位后才能填入真实 lineNumber；未知时为 null，不能编造 application.yml:42。configFileHint 也不表示应用一定使用该文件，配置可能来自 properties、Profile 文档、环境变量或平台配置。

configEdits 只由固定键模板构造；不包含秘密，不输出原始安全配置。变更由维护者或已获准的编码 Agent 在相应目标工作区审阅执行，本服务不写应用、不重启、不自动提权。重复授权不重复询问；无现有授权时按 SPEC 的 Ask First 处理。

### 4.2 配方矩阵

| 观察结果 | 错误/确定性 | 指向的配置行或代码锚点 | Recipe 与安全复验 |
| --- | --- | --- | --- |
| 本地未启用能力 | CAPABILITY_DISABLED / observed | 本地 MCP 启动能力配置 | 仅申请指定 endpointId；不发网络请求，不修改目标暴露 |
| 404，任意 Actuator 域 | ENDPOINT_UNAVAILABLE / 原因为 unknown | `management.endpoints.web.base-path`、`management.server.port`、具体端点的 include/exclude、启用/access 配置 | 先核对已登记 authority/路径，再定位目标配置；只在确认缺暴露时合并指定 ID。404 也可能是安全隐藏，不断言未安装 |
| Boot 2.7 / 3.0–3.3，工作区证实指定端点被禁用 | ENDPOINT_DISABLED / 配置证据支持时 observed | `management.endpoint.<id>.enabled=true` | 仅针对该 ID，保留既有其他配置；仍需 include 和读取授权，启用不等于网络可读 |
| Boot 3.4 / 4.x，证实 access 为 none | ENDPOINT_ACCESS_DISABLED / 配置证据支持时 observed | `management.endpoint.<id>.access=read-only`；检查 `management.endpoints.access.max-permitted` | 全局 max-permitted=none 不能被单端点覆盖；交维护者决定，不自动放宽全局上限、不建议 unrestricted |
| 401 | AUTH_REQUIRED / HTTP observed | 本地 credentialRef 对应的环境变量名称；目标已有认证入口 | 检查该目标 Basic/Bearer 绑定与凭据有效性；不请求用户粘贴值，不换路径或匿名探测 |
| 403 | AUTH_FORBIDDEN / HTTP observed | 已有 SecurityFilterChain 中对应 Actuator GET/HEAD 匹配规则及专用读取角色 | 在既有规则中审阅最小权限补丁，不替换整条链，不 permitAll、不关闭 CSRF 或认证 |
| health 只返回 status 或缺少 details/components | HEALTH_DETAILS_NOT_VISIBLE / unknown | `management.endpoint.health.show-details=when-authorized`、`show-components=when-authorized`、`roles=<既有专用读取角色>` | 先确认角色映射；不给所有用户 always。保留整体状态，组件可见性 unknown |
| startup 404，或已确认缺少采集器 | STARTUP_DATA_UNAVAILABLE / suspected；源码证实时 observed | 主入口的 SpringApplication 构造后、run 前设置 BufferingApplicationStartup；另核对 startup 的暴露/访问 | 不能只写 enabled=true 就声称已有时间线；需要目标重启并重新采集，绝不 POST 恢复 |
| startup 时间线为空 | STARTUP_TIMELINE_EMPTY / observed；原因 unknown | 主入口采集配置、容量/过滤策略；是否被其他消费者排空 | 不断言没有慢启动；不自动改容量或清缓冲区 |
| OpenAPI 候选全部 404 | OPENAPI_NOT_FOUND / observed；根因 unknown | `springdoc.api-docs.enabled`、`springdoc.api-docs.path`；管理模式的 `springdoc.use-management-port` 与 exposure include=openapi | 对照已登记候选和模式；不同端口需独立登记。不能假定未安装或自动加依赖 |
| OpenAPI 401/403 或 200 HTML | AUTH_REQUIRED/AUTH_FORBIDDEN 或 INVALID_OPENAPI_DOCUMENT | 已有安全规则、固定文档路径 | 授权拒绝停止回退；HTML 不当作不存在，也不抓取 Swagger UI 配置 |
| 406 / 415 或未知媒体结构 | MEDIA_TYPE_UNSUPPORTED / observed | 同端点 Accept 协商与已登记适配器 | 只允许 1.3 的同端点 406 表示回退；未知根结构返回 invalid，不读 env 猜版本 |
| 405、重定向、TLS/peer 拒绝 | METHOD_UNAVAILABLE/REDIRECT_BLOCKED/TLS_VERIFICATION_FAILED/PEER_MISMATCH | 已登记路径、TLS 信任与网络策略 | 不改写方法、跟随 Location、关闭验证或扩大 host 范围 |
| Flyway/Liquibase 之一缺失或记录字段不全 | MIGRATION_CAPABILITY_PARTIAL / observed | 各框架既有启用状态及具体 flyway/liquibase 暴露/读取规则 | 分域保留可用证据；不直连数据库、不补 PENDING、不执行 validate/migrate/repair |
| 字段/父 Context/条件 grammar 不完整 | NORMALIZATION_PARTIAL / observed | 明确缺失的 source shape、configurationClass 或 fixture/适配器 | 返回可用项、unknown 与精确过滤；缺口不自动生成应用配置更改 |
| 超时、body/深度/节点/输出超限 | TARGET_TIMEOUT/RESPONSE_TOO_LARGE/NORMALIZATION_LIMIT | 本地固定预算与查询条件 | 优先缩小查询/展开深度；上游不支持过滤时明确无法减小原 body，不默认提高上限 |

Boot 3.4 的 access/read-only/max-permitted 机制及优先级依据[官方访问控制说明](https://docs.spring.io/spring-boot/3.4/reference/actuator/endpoints.html)。它与 HTTP exposure、安全认证是三层独立条件；Recipe 必须先区分，不能只返回“开启 Actuator”。低版本仍按对应启用配置处理；版本未知时列出有前提的候选，不同时无条件写入新旧键。

### 4.3 最小配置与原生启动采集示例

已确认缺少 beans/conditions 暴露时，生成针对配置键的**合并建议**，不是直接覆盖用户配置：

```json
{
  "recipeId": "actuator.exposure.merge-requested.v1",
  "endpointId": "conditions",
  "errorCode": "ENDPOINT_UNAVAILABLE",
  "certainty": "unknown",
  "configFileHint": "application.yml|properties",
  "lineNumber": null,
  "configEdits": [
    {
      "key": "management.endpoints.web.exposure.include",
      "operation": "mergeInclude",
      "suggestedValue": ["beans", "conditions"],
      "onlyIf": "Confirmed missing exposure; preserve other approved entries and review exclusions."
    }
  ],
  "codeEdits": [],
  "credentialEnvNames": [],
  "approval": "requiredUnlessPreviouslyAuthorized",
  "restartRequired": "unknown",
  "retry": {
    "tool": "springboot_inspect_beans",
    "arguments": {"targetId": "local", "beanNamePattern": "orderService", "maxResults": 10}
  },
  "verification": ["Repeat the same authorized GET and validate the conditions shape."]
}
```

include/exclude 必须检查优先级；既有 exclude 包含目标 ID 时，需要对该项单独审阅。禁止生成 include=*、把所有端点改为 unrestricted 或通过全局配置恢复本产品永久禁止的读取。健康组和管理路径的建议也只定位必要键，不覆盖用户安全链。

已由工作区证实未配置原生启动采集时，代码锚点为 `SpringApplication` 创建后、`run` 前：

```java
import org.springframework.boot.SpringApplication;
import org.springframework.boot.context.metrics.buffering.BufferingApplicationStartup;

// 合并到已有 main；容量 2048 是需维护者评估的示例，不是服务端强制值。
SpringApplication application = new SpringApplication(MyApplication.class);
application.setApplicationStartup(new BufferingApplicationStartup(2048));
application.run(args);
```

这属于原生采集配置，不需要本项目自定义 Agent/Starter。重启后再读取 GET 快照；没有记录或已被排空的数据不能补回。采集前提依据 [Spring Boot startup 说明](https://docs.spring.io/spring-boot/reference/actuator/endpoints.html)。

### 4.4 完成判据

每条 Recipe 都必须经过字段白名单、秘密检测和完整回包预算；建议与代码来自本地模板，不能把目标 message、HTML、Location 或 OpenAPI 描述变成修复指令。公开错误以工具契约的 errors[]/DiagnosticError 为准；本节 DiagnosticRemediation 是内部更详细的建议模型，公开时投影为固定模板的 remediation。API 返回保留 status、coverage、totalMatched、truncated 和 nextAction；未知总数为 null，不能用空成功替代拒绝。

文档验收需验证四个指定章节、八域映射、接口引用可编译、JSON 配方合法、别名/父容器/三值逻辑例子一致，以及 SPEC/安全文档的回退规则一致。生产验收另需 C01–C10 的 fixture、适配器和安全连接器测试；在运行实现缺失时不得宣称跨版本兼容性已经通过。
