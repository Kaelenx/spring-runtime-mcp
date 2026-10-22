# 综合代码审查：官方规范、架构、安全与工程稳定性

审查日期：2026-10-22（Asia/Shanghai）。代码基线：`fe34b001c29bbc474c8d5235ecfa5f1dda41ead2`，版本 `0.2.0`。开始审查时工作区干净。本次只新增此报告，没有修改实现、测试、公开 Schema、运行依赖或目标配置。

**结论：建议暂不通过稳定发行验收。** 确认 3 项 P1、10 项 P2；其中 11 项实现问题均有本次运行的合成样本或真实 STDIO/回环 fixture 证据，另有 1 项重复复现的测试竞争、1 项由 workflow 代码直接确认的发布门禁缺口。项目已具备可构建、可安装、可握手的本地诊断基础，但回包、并发、总时限和若干核心诊断结论尚不满足自身约束。

P1 表示应优先修复的资源或安全边界问题；P2 表示需要修复的诊断正确性、互操作或工程门禁问题。本报告不把尚未交付且已经声明的完整产品目标自动计作新增缺陷，也不构成某项法律或行业认证的合规结论。

## 1. 必须优先处理的发现

### CR-01 / P1：长请求 ID 绕过最终 16 KiB 回包预算

- 位置：`src/spring_runtime_mcp/server.py:126–134`；`transport/responses.py:16–24`。
- 触发：完成握手后，发送 `id="x" * 20000` 的 `tools/call`，工具选 `springboot_diagnose_health`，参数 `{"maxResults":0}`。请求在 64 KiB 输入帧限制以内，参数错误也保证了零目标 IO。
- 实测：真实子进程 stdout 收到 **22,078 UTF-8 字节**的单个结果帧（不计换行）；信封错误是 `RESPONSE_TOO_LARGE`，外层仍原样带着 20,000 字符 ID。
- 原因：`serialize_response` 能拒绝长 ID，但 `_result` 捕获异常后只替换信封，再把结果交给 SDK；SDK 使用原请求 ID 序列化。fallback 没有再次经过等效的完整帧限制，错误路径本身超限。
- 影响：违反 AGENTS、README 和 SECURITY 中对完整工具结果的硬边界；现有 `test_request_id_rejected` 只验证内部序列化函数，未覆盖真实 SDK 出口。
- 修复建议：在工具分派前落实请求 ID 限制，为拒绝分支定义有界协议行为；最终结果及 fallback 都按 SDK 实际出站格式计量。回归必须测真实管道的 UTF-8 字节数，包括 ID、两份内容和元数据。

### CR-02 / P1：同一目标没有落实最多 2 个并发请求

- 位置：`src/spring_runtime_mcp/server.py:107–118`；`transport/actuator_http.py:188–214`。
- 触发：真实 STDIO 会话同时发送 8 个 `springboot_diagnose_health` 请求；上游为本次自行启动的 `127.0.0.1` fixture，每次 GET 延迟 0.7 秒并返回合成健康 JSON。
- 实测：8 个工具结果全部成功，fixture 记录到 **8 个同时在途的上游请求**。
- 原因：SDK 可以并发分派；项目没有按 target 设置 semaphore/队列或统一请求调度，连接器每次直接建立线程任务。
- 影响：SPEC 3.2 的每目标最多 2 请求未被执行；8 MiB 单响应上限不能限制并发情况下的聚合内存和目标负载。
- 修复建议：在统一读取边界实现每目标并发门禁，排队时间纳入工具总时限；测试同时覆盖多工具共享额度、排队取消和异常释放。不要在八个处理器中分别复制限流器。

### CR-03 / P1：15 秒限制被重复应用于每次读取，未约束整个工具调用

- 位置：`src/spring_runtime_mcp/tools/support.py:61–75`；`tools/detect_drift.py:212–245`；`tools/inspect_beans.py:418–423,612–617`。
- 触发：使用合成 `RuntimeReader`，每次读取 `await asyncio.sleep(4.1)` 后返回有效证据；调用漂移工具同时提交空 Bean/路由预期、Flyway 版本及 `sourceModifiedAt`，依次读取 beans、mappings、flyway、startup。
- 实测：工具执行 **16.42 秒**后返回 `status=ok`，没有超时代码；四次读取都在各自单请求默认 5 秒以内。
- 原因：`read_checked` 每次重新启动 `timeout=15`。漂移、配置和迁移的多端点流程没有共享绝对截止时间；多数分析器也没有调用级 deadline。Bean 工具虽建立 `AnalysisBudget`，第二次读取仍单独获得 15 秒等待。
- 影响：排队、全部读取、分析和序列化合计 15 秒的产品约束未成立；设置较大 `--timeout` 时差距更大。
- 修复建议：由调用入口创建共享 deadline，下传剩余预算；网络、排队及分析消费同一预算，并让取消到达在途 socket。同步 CPU 分析也需要协作检查，单独增加 `asyncio.timeout` 不能保证其及时中断。

## 2. 诊断正确性与官方语义

### CR-04 / P2：OpenAPI 组合与字典 Schema 被静默删除，却标为完整

- 位置：`src/spring_runtime_mcp/adapters/openapi.py:144–189`。
- 触发：分别查询仅含 `allOf`、`oneOf`、`anyOf` 或 `additionalProperties: {"type":"integer"}` 的命名 Schema。
- 实测：四组查询均得到 `schema={}`、`expansionComplete=true`、`status=ok`、`coverage.state=complete`、空 gaps。补充复核的文档均带有根级 `openapi`/`info`，且结果通过本项目对应 outputSchema 校验。
- 原因：投影仅复制 `_SCHEMA_KEYS`，递归仅处理 `properties`/`items`，没有处理组合数组或 `additionalProperties`。
- 影响：合法的继承、多态和 Map 契约变成没有约束的空 Schema，Agent 可能生成不符合接口的代码；这不是正常的脱敏省略。OpenAPI 对组合与字典结构有明确语义。[OpenAPI 3.1.1 Schema Object](https://spec.openapis.org/oas/v3.1.1.html#schema-object)
- 修复建议：在共享遍历预算内保留并递归处理这些结构；不能支持的结构至少明确 `partial` 和 omission code，不能返回完整。组合关系不可无条件拍平。无需先扩大公开 Schema：现有契约已预留相关结构。

### CR-05 / P2：PathItem 和 Response 的本地引用没有进入对象解析器

- 位置：`src/spring_runtime_mcp/adapters/openapi.py:316–320,339–348`；已有 `_object` 位于 `192–213`。
- 触发 A：`paths["/orders"]={"$ref":"#/components/pathItems/Orders"}`，引用目标含 GET 操作。
- 实测 A：定点查询 `/orders` 返回 `items=[]`、`totalMatched=0`、完整覆盖。
- 触发 B：GET 的 `responses["200"]={"$ref":"#/components/responses/Orders"}`，引用目标带 `application/json` Schema。
- 实测 B：输出响应的 `mediaType`/`schema` 均为 null，`expansionComplete=true`，无 gaps。
- 原因：路径对象直接枚举方法，Response 直接读 `content`；只有 Parameter/RequestBody 调用了 `_object`。这两种引用均属于合法 OpenAPI 结构。[Path Item Object](https://spec.openapis.org/oas/v3.1.1.html#path-item-object)、[Responses Object](https://spec.openapis.org/oas/v3.1.1.html#responses-object)
- 修复建议：选择并解析有界的同文档包装引用，再提取操作或媒体契约；与 Schema 引用深度分开计数。无法解析时保留未知/partial，继续禁止外部引用网络请求。

### CR-06 / P2：OpenAPI 3.1 的 `$ref` 相邻约束被丢弃

- 位置：`src/spring_runtime_mcp/adapters/openapi.py:112–115`；版本读取在 `277–283`。
- 触发：OpenAPI 3.1.0 的 `Result={"$ref":"#/components/schemas/Name","maxLength":3}`，Name 为 string Schema。
- 实测：只返回引用目标的 `type=string`，没有 `maxLength=3`，仍为 `resolved`、完整展开。
- 原因：遇到 `$ref` 立即返回 `_resolve`，文档版本没有参与分支；将部分 3.0 Reference Object 的习惯用于 3.1 Schema Object。
- 影响：实际接口约束被放宽。JSON Schema 2020-12 的 `$ref` 允许同对象内其他关键字共同生效。[JSON Schema Core 8.2.3.1](https://json-schema.org/draft/2020-12/json-schema-core#section-8.2.3.1)
- 修复建议：区分 OAS 3.0/3.1 及 Schema/Reference Object 的语义，保留相邻约束。对尚未支持的解析基准或方言显式降级，不要通过简单覆盖来错误合并冲突约束。

### CR-07 / P2：迁移端点拒绝访问后仍从基线生成确定的 PENDING 行

- 位置：`src/spring_runtime_mcp/adapters/migrations.py:283–310,327–341`；调用处 `tools/check_migrations.py:38–68`。
- 触发：`engine=auto`，Flyway 读取返回 403，Liquibase 返回有效空集合；提交 Flyway 版本 2 的 `expectedMigrations` 并设置 `expectedSetComplete=true`。
- 实测：结果虽有 `ENDPOINT_UNAUTHORIZED` 和 partial，却新增 Flyway `state=PENDING`、`stateEvidence=baselineInferred`、`observationId=observation_baseline`。
- 原因：只有解析失败的引擎进入 `invalid_engines`；未读取到或因 HTTP/能力失败而缺失的引擎不在排除集内。`apply_expected` 将其当作已完成的空运行集合。
- 影响：403/超时/能力关闭不能证明未执行迁移；调用者基线完整也不表示运行证据完整，当前调用策略对此已有明确禁止。
- 修复建议：把每引擎读取状态及枚举完整性传入比较；仅在相关运行范围可证实时做基线集合推导。保留原来的授权错误，不生成无证据的 PENDING 行。

### CR-08 / P2：缺失 checksum 或算法版本不同也被报告为 CHECKSUM_MISMATCH

- 位置：`src/spring_runtime_mcp/adapters/migrations.py:311–313`。
- 触发 A：Flyway 有 SUCCESS 记录但未报告 checksum，基线为 `123`。
- 实测 A：`actual checksum=null`，gaps 却为 `CHECKSUM_MISMATCH`。
- 触发 B：同一 Liquibase changeSet 的运行 checksum 为 `9:abcd`，基线为 `8:abcd`。
- 实测 B：同样返回 `CHECKSUM_MISMATCH`；没有先检查算法兼容性。
- 原因：直接比较原始字符串/空值，未利用已经解析出的 checksum 类型与算法版本。
- 影响：缺证据或算法升级被误报为内容漂移；SPEC 2.3 要求仅比较同引擎、兼容算法的可信校验和。
- 修复建议：只有双方均有值且表示/算法可比较时返回 mismatch；缺失、版本不兼容和实际不一致应分别保留 unknown/gap。

### CR-09 / P2：按类型比较 Bean 时没有屏蔽子容器同名覆盖的父 Bean

- 位置：`src/spring_runtime_mcp/adapters/drift.py:280–306`，尤其 `304`。
- 触发：parent 中 `gateway: example.ParentType`；child 的 parentId 指向 parent，child 自己声明同名 `gateway: example.ChildType`。两种类型均在完整合成类型目录中登记且无继承关系；预期为 child 中可见的 `example.ParentType`。
- 实测：漂移结果 `status=ok`、完整覆盖、`missingInRuntime=[]`。
- 原因：`_beans_by_type` 沿父链直接收集全部 Bean，没有维护已被低层上下文占用的名字/别名；随后父 Bean 的精确类型匹配消除了差异。
- 影响：按名称的依赖解析与按类型的漂移比较使用不同可见性语义。Spring 明确说明同名子 Bean 会遮蔽父 Bean，包括按类型查询。[BeanFactoryUtils](https://docs.spring.io/spring-framework/docs/current/javadoc-api/org/springframework/beans/factory/BeanFactoryUtils.html#beansOfTypeIncludingAncestors(org.springframework.beans.factory.ListableBeanFactory,java.lang.Class))
- 修复建议：建立共享的父链可见性投影，覆盖 canonicalName/aliases、遮蔽与缺失父节点；类型匹配只使用当前上下文实际可见的候选，继续禁止父容器向子容器查找。

### CR-10 / P2：已成功应用的 Flyway 非 SUCCESS 状态被漂移工具说成 absent

- 位置：`src/spring_runtime_mcp/adapters/drift.py:629–646`；对照 `adapters/migrations.py:11–19`。
- 触发：运行记录版本为 3，分别设置 `OUT_OF_ORDER`、`MISSING_SUCCESS`、`FUTURE_SUCCESS`，带有效 installedRank；预期同一 Bean 的版本为 3。
- 实测：三例均返回完整覆盖的差异：`expected="3"`、`actual="absent"`、`evidenceLevel="observed"`。
- 原因：`_installed_version` 只认 `rawState=="SUCCESS"`，与已有归一化器及 Flyway 自己的状态语义不一致。Flyway 将这些成功状态与失败明确区分。[Flyway MigrationState 官方源码](https://github.com/flyway/flyway/blob/main/flyway-core/src/main/java/org/flywaydb/core/api/MigrationState.java)
- 修复建议：复用应用状态语义，并明确定义当前版本选择规则；顺序异常、源文件缺失或未来版本可以单独提示，不能据此断言数据库未应用该版本。证据不足时返回 unknown。

### CR-11 / P2：隐藏详情的 UP 响应被当作组件枚举完整

- 位置：`src/spring_runtime_mcp/adapters/health.py:96–107`。
- 触发：健康响应只有 `{"status":"UP"}`，查询 `componentPath=["db"]`。这也是 README 推荐隐藏详情配置可能得到的合法形状。
- 实测：输出 `overallStatus=UP`、`items=[]`、`totalMatched=0`、`coverage.state=complete`，没有可见性缺口。
- 原因：`hidden` 强制要求 `status!="UP"`；聚合状态正常时，代码无法表达组件列表其实不可见。
- 影响：可以保留观察到的整体 UP，但不能据此证明 db 不存在或组件查询覆盖完整。Spring 的响应结构允许按授权隐藏组件和详情。[Spring Boot Health API](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/health.html)
- 修复建议：区分整体状态与组件枚举可见性；组件不可见时返回 partial/unknown 和可见性 gap，计数无法确定时为 null。不要把所有合法叶节点一律标成异常。

## 3. 工程门禁与稳定性

### CR-12 / P2：进程测试在读取最后一条回复前关闭 stdin，导致 CI 随机失败

- 位置：`tests/integration/test_stdio_host_process.py:81–90`；使用点 `408–415`。
- 本次全量结果：**729 passed、1 failed**，失败为 `test_stdio_host_process_does_not_open_unregistered_endpoints`，只收到 initialize 回复。
- 单项复核：相同环境独立运行 8 次，**7 次通过、1 次失败**；另一个保持输入打开、逐条等回复的真实管道会话连续发送 8 个未知工具请求，全部收到对应 ID 的 `-32602`，进程正常退出且 stderr 为空。
- 原因：`host_in_stages` 只等前几个阶段的单条回复，写完最后一个阶段立即关闭 stdin。SDK 对 EOF 的关闭流程可与最后一个请求的处理竞争；测试把客户端主动断开后仍必须收到回复作为断言。
- 影响：不能把此失败直接归为“未知工具处理器丢响应”，但默认验收和 CI 当前确实不稳定。`readline()` 也不受稍后 `process.wait(timeout=...)` 的时间限制。
- 修复建议：读取最后阶段所有预期响应后关闭输入，按 request ID 对齐，使用有时限的读取；不要降低断言、允许任意缺帧或仅增加 sleep。CI 构建握手的批量管道也应检查同类顺序问题。

### CR-13 / P2：发布 workflow 只依赖构建，没有继承安全、契约和单元测试门禁

- 位置：`.github/workflows/release.yml:11–34`；对照 `.github/workflows/ci.yml`。
- 证据：release 的 build 只执行构建、安装、`--help` 和工具数量断言，publish 仅 `needs: build`。CI 的 push 触发限于 master，独立发布 workflow 不检查相同 SHA 的 CI 状态。
- 触发：为未经过绿色 CI 的提交创建符合 `v*` 的标签，在 PyPI environment 的外部审批条件满足后即可进入发布；仓库文件没有让测试失败阻断该产物的依赖关系。
- 影响：安全测试、契约或进程测试失败的代码仍有发布路径；当前全量红色已经说明这不是可忽略的门禁问题。是否另有 GitHub 标签保护或 environment 人工规则，本次未读取，不能当成已有控制。
- 修复建议：提取可复用校验 workflow，让发布直接依赖相同 SHA 的测试、类型、契约与构建验收，并检查标签版本与包元数据一致。不要依赖维护者记忆或另一个 workflow 恰好运行成功。

## 4. 官方代码与语法对齐结论

- 实际审查环境：Python **3.12.15**、`mcp` **2.3.0**、uv **0.13.1**。核查了本地已安装官方 SDK 的 `lowlevel.Server`、`MCPError`、STDIO 和 dispatcher 实现，另对照版本化官方迁移文档；`Server(on_list_tools=..., on_call_tool=...)`、上下文/参数回调和显式构造 `CallToolResult` 的方式与 v2 API 一致。没有发现需要退回旧 v1 装饰器语法的依据。[官方 Python SDK v2.3.0 迁移文档](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md)
- MCP 工具结果发布 outputSchema 并在入口校验输出，提供 structuredContent 和兼容文本副本，方向符合协议；SDK 负责握手/分派，不会自动落实本项目自定义的回包、目标并发和调用预算。[MCP 工具结果规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#output-schema)
- Python 命名、格式、类型检查的现有门禁均通过。通过这些检查证明当前配置下的语法/静态检查成立，不能替代 Spring 与 OpenAPI 运行语义，CR-04 至 CR-11 正是此类缺口。
- 使用标准库 HTTP 连接器是当前已经明确的实现选择。没有因其不同于早期 SPEC 中的 httpx/Typer/Pydantic 目标而单独判错；但覆盖范围及采用私有 `_create_connection` 接缝的兼容性风险需要真实回归约束。

## 5. 架构、范围与可用性判断

| 维度 | 当前成立的部分 | 尚需收口 |
| --- | --- | --- |
| 分层 | SDK 适配、只读传输、字段脱敏、分析模型和工具注册可辨认；网络导入集中 | `Services`、错误模板和通用校验集中在 Bean 工具，其他工具通过 support 反向依赖它；宜下沉到共享应用/模型层 |
| 安全默认 | 固定 GET/固定路径、回环校验、DNS 全组检查、数字 IP 建连、peer 检查、默认 TLS 校验和出口检查有实现 | CR-01 至 CR-03；现有单请求边界不能替代聚合资源治理 |
| 归一化 | Bean 名称/别名图与条件三值模型较完整，保留 unknown/partial 的设计方向正确 | OpenAPI、按类型父链、迁移状态和健康可见性仍有错误的完整/确定结论 |
| 分析职责 | 核心图遍历放在 analyzers，工具大体负责读取与封装 | 多个 adapters 同时查询、比较和给结论，尤其 795 行 drift；重复的状态/可见性语义已经导致 CR-09/10 |
| 打包 | wheel/sdist 能构建，Schema 随 wheel 分发，隔离安装可握手 | 无依赖锁；开发/CI 的裸 `--with mcp` 与运行时 `mcp>=2.3,<3` 未统一约束 |
| 类型交付 | 仓库内 strict mypy 通过 | 声明 `Typing :: Typed`，本次 wheel 未发现 `py.typed`，应按 [PEP 561](https://peps.python.org/pep-0561/#packaging-type-information) 补齐类型分发标记或调整承诺 |
| 验证 | 离线合成 fixture、回环 HTTP、真实 STDIO 和契约门禁覆盖面较广 | 默认测试有竞争；真实 Boot 跨版本、认证、TLS/SNI 及第三方客户端矩阵未在本次重新验收 |
| 文档 | 清楚说明远程目标、认证绑定、自定义路径等未实现限制 | README 仍称 v0.1.0/729 tests，CHANGELOG 当前为 v0.2.0/730；SPEC 的当前依赖和部分入口说明也滞后 |

以下是已声明的交付限制，不应被当作已实现能力：远程 HTTPS 目标白名单、Basic/Bearer 凭据绑定、逐端点可信配置入口、自定义 Actuator 基路径、`/v3/api-docs` 有限回退、完整压缩/响应头预算和真实版本矩阵。配置分析处理器有内部策略对象，现有 CLI 没有相应登记入口；普通 CLI 用户无法仅靠工具参数合法启用它。真实 Boot 3.4.5 演示和已提交 transcript 属于仓库证据，本次没有启动、连接或重新验收该应用。

## 6. 本次实际验证

除包索引/官方文档阅读外，未访问外部诊断目标；测试及补充探测只使用合成内存数据或自行启动并销毁的回环 fixture。没有触发发布、重启或目标配置修改。

| 检查 | 实际命令/方法 | 结果 |
| --- | --- | --- |
| 全量离线测试 | `uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 --with mcp python -m pytest -q` | **729 passed / 1 failed，32.86 秒** |
| 失败项定点复核 | 同一环境独立运行指定测试 8 次 | 7 passed / 1 failed；CR-12 |
| 类型检查 | `uv run --offline --no-project --with mypy==1.18.2 --with jsonschema==4.25.1 --with mcp python -m mypy src/spring_runtime_mcp` | 49 个源文件通过 |
| 契约 | `uv run --offline --no-project --with jsonschema==4.25.1 python scripts/validate_tool_contract.py` | 8 组独立 Schema、144+ 断言通过 |
| Lint | `uv run --offline --no-project --with ruff==0.14.1 ruff check src tests scripts` | 通过 |
| Format | `uv run --offline --no-project --with ruff==0.14.1 ruff format --check src tests scripts` | 85 个文件通过 |
| 构建 | `uv build` | wheel、sdist 均成功 |
| wheel 内容 | ZIP 读取资源和元数据 | 含 `spring_runtime_mcp/schemas/tools-contract.json`，其中 8 个工具；不含 `py.typed` |
| 隔离安装 | 临时 venv，`uv pip install --offline --python ... dist/*.whl`；从仓库外运行已安装入口 | initialize 协商 2025-06-18；tools/list 返回按字母序排列、均有 outputSchema 的 8 工具；exit 0，stderr 0 字节 |
| 补充实现复现 | CR-01 至 CR-11 所述合成脚本；真实管道、受控回环或内存 Reader | 均复现所述结果；不计入现有 730 项测试集合 |
| 差异空白检查 | `git diff --check` | 通过 |

本次没有将重复执行的单项检查混入全量通过数量，也没有把历史演示、未执行的 Maven 命令、其他 Python 版本或真实 TLS 测试写作本次验证。

## 7. 建议修复顺序与验收条件

1. 先关闭 CR-01/02/03：真实 SDK 出口所有结果路径受限；同目标并发不超过 2；多端点组合诊断、排队和分析服从一个 15 秒 deadline。
2. 修复 CR-12 并让默认门禁稳定，然后让发布依赖该门禁（CR-13）；保留原有安全断言，不用 skip/xfail 或允许缺帧替代正确协议交互。
3. 按独立最小样本修复 CR-04 至 CR-11。优先共享 Bean 可见性、迁移应用状态和证据完整性模型，减少工具间同事实不同结论；每项增加能区分已知、未知和截断的回归。
4. 同步当前版本说明、锁定可复现的开发/发布依赖，并明确实际支持矩阵。真实实例、目标配置、扩权及发布继续按仓库授权边界独立处理。

上述已确认逻辑问题大多可在现有公开 Schema 内修复。若后续方案需要修改公开 inputSchema/outputSchema、新增运行依赖或改变目标采集/权限，应先准备可审阅补丁及影响说明，再按 AGENTS 的对应边界取得授权。本次审查未执行这些变更。
