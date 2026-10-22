# 项目真实状态与架构审计报告

审计日期：2026-10-18。

本报告基于源码、文档、配置与测试代码的静态检查。审计期间没有修改文件、运行测试、启动服务或连接目标；随后按用户要求新增本报告文件，未修改被审计源码。下文“测试覆盖”指仓库中的测试实现，不代表本次验证通过；`CHANGELOG.md:21` 的“639 passed”属于历史记录。路径均相对于仓库根目录，行号对应审计时的文件内容。

## 1. 一句话残酷结论

**这是一个已经接通 STDIO → 八工具处理器 → HTTP 的 v0.1.0 诊断原型，但安全授权控制和部分诊断语义仍未闭环，不能认定为生产可用，也不能贬成“只有离线 Fixture 重放”。** 八工具注册见 `src/spring_runtime_mcp/tools/registry.py:20`，真实服务装配见 `src/spring_runtime_mcp/server.py:29`，已知交付边界见 `CHANGELOG.md:29`。

“唯一能跑通的入口”这一前提需要纠正：**唯一长期运行的 MCP 宿主入口是 `spring_runtime_mcp.cli`，另外存在独立的一次性 `replay` 入口。** 两者分别调用 STDIO 循环和固定场景处理器，见 `src/spring_runtime_mcp/cli.py:35`、`src/spring_runtime_mcp/replay.py:165`。

仓库根目录的宿主启动命令如下，前提是依赖已缓存；**本次未执行**：

```bash
PYTHONPATH=src uv run --offline --no-project --with jsonschema==4.25.1 \
  python -m spring_runtime_mcp.cli --base-url http://127.0.0.1:18081
```

该命令进入 MCP 请求循环，不会自行完成一次诊断；工具调用才触发 HTTP。对应入口与延迟连接装配见 `src/spring_runtime_mcp/cli.py:13`、`src/spring_runtime_mcp/server.py:32`。安装后的脚本别名为 `spring-runtime-mcp`，见 `pyproject.toml:8`。

## 2. 架构全景与真实数据流

```text
[在线 MCP 链路：源码已接通]

MCP 客户端 stdin
    |
    v
cli.main → build_services → run_stdio
    |       [启动时登记一个 origin / targetId]
    v
逐行 JSON 解析 → dispatch
    |
    +-- initialize / ping / tools/list
    |      [自实现协议；非官方 SDK]
    |
    +-- tools/call
           |
           v
    Draft202012Validator(inputSchema)
           |
           v
    registry.HANDLERS：八工具全部挂载
           |
           v
    Handler → validate_arguments → Query
           |
           +-- cursor → SnapshotStore 内存续页
           |
           v
    RuntimeReader / read_checked
           |
           v
    ResponseGateReader
    [目标/端点检查、GET、超时、状态码、媒体类型、有界 JSON]
           |
           v
    ActuatorHttpTransport → http.client → 已登记 origin
    [固定 /actuator/* 路径；不跟随重定向]
           |
           X 缺 DNS 全组策略、数字 IP 固定连接、peer 校验
           X 缺目标凭据绑定
           X 实际装配启用全部逻辑端点
           |
           v
    Adapter / Analyzer
           ^
           |
    SourceCatalog = 空目录
           X 线上缺少源码类型/工厂事实接入入口
           |
           v
    SanitizerPipeline → 分页/裁剪 → SnapshotStore
           |
           v
    serialize_response → output_guard
    [structuredContent + 同内容文本；完整工具帧 ≤ 16,384 字节]
           |
           v
    stdout JSON-RPC


[离线重放链路：源码已接通]

replay.main
    → 固定 scenario
    → 本地 JSON fixtures / 可选 source-catalog.json
    → FixtureReader
    → 同一 registry / Handler / Adapter / Analyzer
    → outputSchema 校验 → 同一序列化出口 → 一次输出后退出
```

图中调用链依据：

| 环节 | 源码定位 |
| --- | --- |
| CLI、装配与 STDIO 循环 | `src/spring_runtime_mcp/cli.py:30`；`src/spring_runtime_mcp/server.py:169` |
| 分发、Schema 验参、处理器调用 | `src/spring_runtime_mcp/server.py:104`；`src/spring_runtime_mcp/server.py:125` |
| 八工具注册 | `src/spring_runtime_mcp/tools/registry.py:20` |
| 默认值与参数规范化 | `src/spring_runtime_mcp/contracts/definitions.py:257` |
| HTTP 策略门与真实请求 | `src/spring_runtime_mcp/transport/response_gate.py:96`；`src/spring_runtime_mcp/transport/actuator_http.py:109` |
| 脱敏、分页与缓存 | `src/spring_runtime_mcp/tools/support.py:74`；`src/spring_runtime_mcp/snapshots.py:67` |
| 最终双副本信封与字节检查 | `src/spring_runtime_mcp/transport/responses.py:39`；`src/spring_runtime_mcp/transport/responses.py:60` |
| 离线场景与源码事实加载 | `src/spring_runtime_mcp/replay.py:80`；`src/spring_runtime_mcp/replay.py:99` |

## 3. 功能真伪核对矩阵

八工具均有经过真实连接器的回环 HTTP 测试代码：测试自行启动 Python HTTP 服务，返回合成 JSON，再经 `build_services` 和 `dispatch` 调用，见 `tests/integration/test_mcp_host_and_http.py:154`、`tests/integration/test_mcp_host_and_http.py:224`。**这验证的是传输链路设计，不是真实 Spring Boot 兼容性。**

下表中的“已闭环”仅表示实现调用链闭环。

| 模块/功能名称 | 契约/Schema 定义 | 核心业务处理器 | 真实传输/IO 接入 | 测试覆盖真实度 | 真实状态判定 |
| --- | --- | --- | --- | --- | --- |
| `springboot_inspect_beans` | `src/schemas/tools-contract.json:907` | 建图、筛选、依赖与缺失分析：`src/spring_runtime_mcp/tools/inspect_beans.py:382` | beans；无匹配时 conditions：同文件 `:406`、`:593` | 实际处理器及多跳条件：`tests/test_e2e_diagnostic_scenarios.py:38` | **链路已闭环；线上源码关联未挂载** |
| `springboot_verify_mappings` | `src/schemas/tools-contract.json:1674` | `src/spring_runtime_mcp/tools/verify_mappings.py:18` | 读取 mappings：同文件 `:27` | 合成 MVC/WebFlux 处理器测试：`tests/conformance/test_multiversion_fixtures.py:20` | **结构化路由已闭环；函数路由解析不足** |
| `springboot_diagnose_health` | `src/schemas/tools-contract.json:717` | `src/spring_runtime_mcp/tools/diagnose_health.py:24` | 三个固定健康端点：同文件 `:17` | Fixture 中能识别 DOWN：`tests/conformance/test_multiversion_fixtures.py:94` | **链路已闭环；503 健康证据被拒绝** |
| `springboot_check_migrations` | `src/schemas/tools-contract.json:212` | `src/spring_runtime_mcp/tools/check_migrations.py:18` | 分别读取两个迁移端点：同文件 `:32` | 实际归一化、多迁移 Bean：`tests/conformance/test_multiversion_fixtures.py:70` | **库存诊断已闭环；排序、身份及统计存在偏差** |
| `springboot_analyze_startup` | `src/schemas/tools-contract.json:6` | `src/spring_runtime_mcp/tools/analyze_startup.py:18` | 固定 GET startup：同文件 `:27` | 父子链和单步耗时：`tests/conformance/test_multiversion_fixtures.py:48` | **链路已闭环；总耗时算法错误** |
| `springboot_inspect_config` | `src/schemas/tools-contract.json:1182` | `src/spring_runtime_mcp/tools/inspect_config.py:18` | 全量 GET env/configprops：同文件 `:36` | 标量投影与秘密屏蔽：`tests/conformance/test_multiversion_fixtures.py:183` | **读取已闭环；授权范围控制未闭环** |
| `springboot_search_openapi` | `src/schemas/tools-contract.json:1406` | `src/spring_runtime_mcp/tools/search_openapi.py:18` | 固定 openapi：同文件 `:27` | 实际响应 Schema 二阶引用展开：`tests/conformance/test_multiversion_fixtures.py:218` | **部分实现；请求体空壳、参数类型硬编码、候选回退未实现** |
| `springboot_detect_drift` | `src/schemas/tools-contract.json:449` | `src/spring_runtime_mcp/tools/detect_drift.py:151` | 按预期维度生成读取计划：同文件 `:172` | 实际处理器与合成预期比较：`tests/test_e2e_diagnostic_scenarios.py:183` | **显式预期比较已闭环；无自动源码采集，Liquibase 版本比较不支持** |

基础设施也并非只有接口：

| 模块/接口 | 实际实现与验证位置 | 判定 |
| --- | --- | --- |
| STDIO MCP 宿主 | 分发：`src/spring_runtime_mcp/server.py:125`；真实子进程测试：`tests/integration/test_mcp_host_and_http.py:379` | **已挂载；协议实现有限** |
| 输入契约与默认值 | Schema 加载：`src/spring_runtime_mcp/contracts/definitions.py:35`；默认值应用：同文件 `:279` | **有真实逻辑** |
| HTTP 连接器 | 建连：`src/spring_runtime_mcp/transport/actuator_http.py:99`；发送 GET：同文件 `:137` | **有真实网络 IO；安全规格未完成** |
| 脱敏与独立出口检查 | `src/spring_runtime_mcp/policy/sanitization.py:404`；`src/spring_runtime_mcp/policy/output_guard.py:37` | **有真实逻辑；不能替代授权白名单** |
| 错误信封与自愈建议 | `src/spring_runtime_mcp/tools/inspect_beans.py:125`、`:239` | **已实现通用信封；建议较粗粒度** |
| 快照与游标 | TTL/内存上限：`src/spring_runtime_mcp/snapshots.py:30`；查询绑定：同文件 `:46` | **有真实内存存储；非持久数据库** |
| SourceCatalog | 加载器：`src/spring_runtime_mcp/adapters/source_catalog.py:29`；重放接入：`src/spring_runtime_mcp/replay.py:99` | **离线可用；线上装配为空** |
| Golden 契约样本 | `tests/conformance/test_tools_contract.py:15` 直接读取输出 JSON 验证 | **这部分仅证明样本符合契约，不能证明业务实现** |

**没有发现“八个工具只有 Schema、没有 Handler”的情况。真正的局部空壳是 OpenAPI 的部分输出字段；真正的未挂载组件是线上 SourceCatalog；Golden 测试与真实处理器测试同时存在。** 定位分别为 `src/spring_runtime_mcp/tools/registry.py:20`、`src/spring_runtime_mcp/adapters/openapi.py:163`、`src/spring_runtime_mcp/adapters/openapi.py:274`、`src/spring_runtime_mcp/server.py:43`。

## 4. 核心缺失与架构断点

### 高影响：安全与授权边界

#### ① 启动时登记 origin，没有完成地址安全登记

规格要求校验全部 DNS 结果、连接选定数字 IP，并在写出 HTTP 前验证真实 peer，见 `docs/architecture/security-and-sanitization.md:104`、`:117`。实际只解析 URL，然后把原 hostname 交给 `HTTPConnection/HTTPSConnection`，见 `src/spring_runtime_mcp/transport/actuator_http.py:32`、`:99`。

因此，**工具参数不能任意换 URL 已落实，但获准 hostname 的解析结果和实际连接地址仍无独立约束**；也未执行规格中的远程 HTTP 禁止及永久拒绝地址策略。该限制已在 `CHANGELOG.md:32` 承认。

#### ② 配置诊断“默认关闭、登记范围取交集”没有在宿主落地

文档明确要求配置能力默认关闭，并与操作者登记的安全属性/前缀取交集，见 `docs/contracts/tool-calling-policy.md:24`、`:44`。

实际 `build_services` 设置 `enabled_endpoints=ENDPOINTS`，其中包括 env/configprops；配置处理器直接请求两个完整端点，筛选只依赖调用者的 prefix/pattern，见：

- `src/spring_runtime_mcp/server.py:38`
- `src/spring_runtime_mcp/transport/response_gate.py:33`
- `src/spring_runtime_mcp/tools/inspect_config.py:36`
- `src/spring_runtime_mcp/adapters/config.py:48`

**目标端默认关闭不能代替 MCP 本地授权。** 目标一旦开放，当前宿主没有第二道已登记字段范围限制；`includeValues=true` 的标量输出也没有非敏感字段登记表，见 `src/spring_runtime_mcp/adapters/config.py:56`。

#### ③ 没有目标凭据接入，受保护 Actuator 的接入路径中断

目标配置示例使用 Basic 认证和诊断角色，见 `docs/target-app-setup.md:92`、`:94`；CLI 仅接受 base-url、target-id、timeout，见 `src/spring_runtime_mcp/cli.py:15`；连接器仅构造 Accept/Connection 请求头，见 `src/spring_runtime_mcp/transport/actuator_http.py:137`。

因此，当前程序无法按该示例提供认证材料，401/403 只能返回错误。`SanitizerPipeline` 支持已知秘密匹配，但线上构造时未传入秘密，见 `src/spring_runtime_mcp/policy/sanitization.py:252`、`src/spring_runtime_mcp/server.py:44`。

### 高影响：业务结果失真或证据丢失

#### ④ 健康 DOWN 最关键的 503 响应进不了健康分析器

规格明确有效 503 健康 JSON 应作为诊断证据，见 `SPEC.md:38`、`:85`。实际 gate 拒绝所有非 200 状态，健康处理器没有例外，见 `src/spring_runtime_mcp/transport/response_gate.py:152`、`src/spring_runtime_mcp/tools/diagnose_health.py:34`。

这会把“应用已报告不健康”变成“端点不可用”。FixtureReader 默认构造成功观察，见 `src/spring_runtime_mcp/replay.py:52`；回环八工具正常测试也统一返回 200，见 `tests/integration/test_mcp_host_and_http.py:231`，两者都绕开了此问题。

#### ⑤ OpenAPI 请求体未实现，参数类型被伪造

契约定义请求体输出，见 `src/schemas/tools-contract.json:1494`；源码却固定：

- 参数 schema 为 `{"type":"string"}`：`src/spring_runtime_mcp/adapters/openapi.py:163`
- 操作 requestBody 为 `[]`：同文件 `:274`
- 完整性计算没有检查请求体是否被遗漏：同文件 `:277`

这不是缺少优化，而是会把 integer/array/引用参数写成 string，把存在的请求体写成空，并可能保留完整标记。现有多版本测试集中检查响应引用展开，见 `tests/conformance/test_multiversion_fixtures.py:230`。

#### ⑥ 线上无法复现重放中的源码工厂多跳关联

重放加载 source-catalog.json，见 `src/spring_runtime_mcp/replay.py:99`；线上始终注入空 types/producers，见 `src/spring_runtime_mcp/server.py:43`，CLI 没有事实目录接入参数，见 `src/spring_runtime_mcp/cli.py:15`。

类型可赋值判断和工厂关联算法真实存在，但依赖该目录，见 `src/spring_runtime_mcp/analyzers/condition_failures.py:18`、`:53`。所以线上只能保守返回 unknown/partial，不能把 Stripe 演示中的关联链视为线上现成功能。README 对源码事实依赖已有提示，见 `README.md:40`。

#### ⑦ 启动“时间线跨度”实际算成最长单步耗时

源码取 `max(durationMs)`，却标记 `durationBasis="timelineSpan"`，见 `src/spring_runtime_mcp/adapters/startup.py:158`。这是最长步骤，不是最早开始至最晚结束的跨度。

例如两个不重叠的 1 秒步骤，该算法仍返回 1 秒。当前 Fixture 的根步骤包住全部子步骤，使最长步骤恰好等于跨度，见 `tests/fixtures/conformance/boot-3.4-servlet/startup.json:7`、`:17`，容易掩盖错误。

### 中影响：能力范围与契约偏差

#### ⑧ 固定路径连接器没有接上规格中的路径配置与 OpenAPI 回退

规格允许管理基路径配置和最多三个预登记 OpenAPI 候选，见 `SPEC.md:32`、`:41`。实际 base-url 禁止带路径，端点固定为 `/actuator/*`，OpenAPI 仅请求 `/actuator/openapi`，见 `src/spring_runtime_mcp/transport/actuator_http.py:44`、`:16`、`src/spring_runtime_mcp/tools/search_openapi.py:27`。

因此自定义管理路径、普通 `/v3/api-docs`、404 候选回退均未实现；README 对固定 OpenAPI 路径的当前限制是准确的，见 `README.md:46`。

#### ⑨ 函数式路由只有容器遍历，没有 predicate/handlerFunction 解析

设计包含函数路由证据，见 `docs/architecture/cross-version-normalization.md:69`。实际仅从 `handlerMethod`、`requestMappingConditions` 或带 `#` 的 handler 文本提取信息；缺少结构化条件时 patterns 为空，带路径筛选会直接排除记录，见 `src/spring_runtime_mcp/adapters/mappings.py:107`、`:127`、`:163`。

缺口会标 partial，但无法完成宣称的函数路由定点核验。

#### ⑩ 迁移排序与身份定义未完整落实

调用策略要求先展示异常状态，再按执行顺序与稳定身份排序，见 `docs/contracts/tool-calling-policy.md:20`；实现过滤后直接编号，没有执行该排序，见 `src/spring_runtime_mcp/adapters/migrations.py:305`。

Liquibase identity 只包含 beanName/id，省略 changelog/author，见同文件 `:200`；规格要求包含 changelog/id/author，见 `SPEC.md:87`。同一 Bean 中重复 id 的不同 changeSet 会发生公开身份碰撞，基线索引还会按该 identity 覆盖，见 `src/spring_runtime_mcp/adapters/migrations.py:243`。

漂移工具明确不支持 Liquibase 版本比较，见 `src/spring_runtime_mcp/adapters/drift.py:556`；这一分支应归为“显式不支持”，不能当作已实现版本诊断。

#### ⑪ 不完整枚举仍可能返回确定 totalMatched，截断标记也不统一

规格要求无法完整枚举时 totalMatched=null，预算省略需标 truncated，见 `SPEC.md:58`、`docs/contracts/tool-calling-policy.md:30`。

启动适配器在 500 节点截断后仅产生 gap，处理器仍使用 `len(items)`，也不据此设置 truncated，见 `src/spring_runtime_mcp/adapters/startup.py:53`、`src/spring_runtime_mcp/tools/analyze_startup.py:48`。迁移端点部分失败时也返回当前可见 items 数量，见 `src/spring_runtime_mcp/tools/check_migrations.py:42`、`:71`。这些计数无法代表完整声明范围。

配置归一化还只遍历 configprops.properties 第一层，嵌套对象既不展开也不报告专门缺口，见 `src/spring_runtime_mcp/adapters/config.py:172`、`:73`。

### 中影响：协议与资源治理

#### ⑫ Schema 发布与运行时校验链不完整

调用策略称 tools 数组原样发布，见 `docs/contracts/tool-calling-policy.md:3`；宿主描述器却省略 outputSchema，见 `src/spring_runtime_mcp/server.py:90`。在线路径只验证 inputSchema；outputSchema 验证存在于 replay，见 `src/spring_runtime_mcp/server.py:115`、`src/spring_runtime_mcp/replay.py:173`。

这意味着契约文件中定义了输出，不等于线上工具发现和执行出口完整执行了输出契约。

#### ⑬ 自实现协议宿主缺少严格的协议状态与封装校验

静态可确认的问题包括：

- initialize 直接回显任意长度合规的版本字符串，没有支持版本集合协商：`src/spring_runtime_mcp/server.py:138`。
- dispatch 不检查 `jsonrpc=="2.0"`，也没有初始化完成状态门禁：同文件 `:125`。
- request id 的严格检查发生在工具执行后的序列化阶段：同文件 `:121`；`src/spring_runtime_mcp/transport/responses.py:23`。
- 未知工具返回工具领域错误，而规格要求协议错误：`src/spring_runtime_mcp/server.py:110`；`SPEC.md:67`。

因此不能用一次握手测试推导完整 MCP 合规；项目自己也已声明这一限制，见 `CHANGELOG.md:31`。

#### ⑭ 64 KiB 输入限制在无界读取之后检查；取消无法完整终止在途请求

STDIO 使用无 size 参数的 readline，再检查帧长度，见 `src/spring_runtime_mcp/server.py:172`。超长且不换行的输入可以先占用内存和等待资源。

HTTP 使用 `asyncio.to_thread` 执行同步交换，外层 wait_for 超时不会直接关闭工作线程中的 socket，见 `src/spring_runtime_mcp/transport/actuator_http.py:89`、`src/spring_runtime_mcp/transport/response_gate.py:104`。宿主逐请求等待 dispatch，也无法在执行期间消费取消通知，见 `src/spring_runtime_mcp/server.py:181`。

单工具缺少统一总截止时间，多端点依次读取各自重新计时，见 `src/spring_runtime_mcp/tools/inspect_config.py:36`、`src/spring_runtime_mcp/tools/support.py:66`。响应对象还未保留 Content-Encoding，无法执行文档要求的编码/解压策略，见 `src/spring_runtime_mcp/transport/response_gate.py:49`、`docs/architecture/security-and-sanitization.md:167`。

## 5. 文档、测试与交付可信度

| 核对项 | 审计结论与证据 |
| --- | --- |
| README 是否虚构 Server/HTTP | **没有。** 声明见 `README.md:7`，实现见 `src/spring_runtime_mcp/server.py:29`、`src/spring_runtime_mcp/transport/actuator_http.py:137`。 |
| 是否所有测试都只是 Golden | **不是。** Golden 校验见 `tests/conformance/test_tools_contract.py:15`；真实处理器测试见 `tests/conformance/test_multiversion_fixtures.py:24`；真实子进程/回环 HTTP 测试见 `tests/integration/test_mcp_host_and_http.py:379`。 |
| 是否已经证明真实版本兼容 | **没有。** Fixture 清单明确写 synthetic、真实集成未验证，见 `tests/fixtures/conformance/manifest.json:2`。 |
| “DNS rebinding 测试”是否验证真实连接器 | **没有。** 该测试使用 SpyTransport，并通过拒绝未登记 targetId 保证 DNS 零调用，见 `tests/security/test_redteam_network.py:85`；它不能证明获准 hostname 的安全建连。 |
| 文档是否存在过期状态 | **存在。** `docs/target-app-setup.md:3` 仍称“仅离线 Bean 重放、不能在线连接”，与 `README.md:71` 及 `src/spring_runtime_mcp/cli.py:35` 矛盾。 |
| 是否具备可复现分发闭环 | **尚未验收。** 项目元数据有依赖和入口，见 `pyproject.toml:1`；缺显式构建后端、锁文件及已验证产物的限制见 `CHANGELOG.md:35`，构建和客户端互操作未执行见 `CHANGELOG.md:27`。 |

达到完整产品验收所缺的关键组件，是**地址与凭据安全控制面、默认关闭且限定字段的采集策略、线上 SourceCatalog 接入、严格协议及资源生命周期管理，以及上述诊断语义修正与真实实例验收**；不是补一个不存在的 Server 或 HTTP 客户端。对应缺口已有明确源码定位：`src/spring_runtime_mcp/server.py:38`、`:43`，`src/spring_runtime_mcp/transport/actuator_http.py:99`，`src/spring_runtime_mcp/server.py:125`，`tests/fixtures/conformance/manifest.json:3`。
