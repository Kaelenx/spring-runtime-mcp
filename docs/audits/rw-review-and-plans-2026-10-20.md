# RW-02 / RW-06 复核与实施计划

日期：2026-10-20。依据：[项目剩余工作交接](remaining-work-handoff-2026-10-20.md)、[产品规格](../../SPEC.md)、[调用策略](../contracts/tool-calling-policy.md)、[跨版本归一化设计](../architecture/cross-version-normalization.md)。

状态：**仅完成复核与“正确行为失败回归”，未修改任何业务源码、Schema、依赖或目标配置。** 全量由交接时的 706 passed / 0 failed 变为 **706 passed / 18 failed**；18 项全部是本计划新增的 RW-02/RW-06 入口回归，静态门禁（mypy、契约、Ruff、`git diff --check`）不受影响。修复前这 18 项必须保持红色，不得以 skip/xfail 替代。

> 2026-10-20 追加：第 6 节记录落地与验证结果；确认缺陷的 5 个 RW-06 子项、RW-02 3.2 及 RW-06-1 已修复，**新增 23 项回归全部转绿，全量 729 passed / 0 failed（exit 0）**。第 4 节红基线作为历史证据保留。

新增回归：

- `tests/unit/test_rw06_architecture_regressions.py`（5 项）
- `tests/unit/test_rw02_protocol_regressions.py`（13 项）

## 1. 复核结论一览

| 项 | 现状定位 | 失败回归 | 判定 |
| --- | --- | --- | --- |
| RW-06 函数式路由 | `adapters/mappings.py:163-164` | `test_rw06_functional_route_survives_a_path_filter` | 确认缺陷 |
| RW-06 Liquibase 身份 | `adapters/migrations.py:201` | `test_rw06_liquibase_identity_includes_changelog_and_author` | 确认缺陷 |
| RW-06 迁移稳定排序 | `adapters/migrations.py:319` | `test_rw06_migration_order_puts_attention_first_then_engine_order` | 确认缺陷 |
| RW-06 启动截断/计数 | `adapters/startup.py:55`、`tools/analyze_startup.py:50` | `test_rw06_startup_node_limit_must_null_total_matched` | 确认缺陷 |
| RW-06 嵌套 configprops | `adapters/config.py:183` | `test_rw06_nested_config_props_keep_registered_leaves` | 确认缺陷 |
| RW-06 线上 SourceCatalog | `server.py:50`、`replay.py:105` | 无（能力缺失，无错误输出可回归） | 需新增可信入口，待批 |
| RW-02 描述器 outputSchema | `server.py:98` | `test_rw02_tools_list_publishes_the_committed_tool_contract` | 确认缺陷 |
| RW-02 协议封装/未知工具 | `server.py:112`、`:134` | 6 个参数化用例 | 确认缺陷 |
| RW-02 版本协商 | `server.py:150-160` | `test_rw02_initialize_never_echoes_an_unsupported_version` | 确认缺陷 |
| RW-02 在线输出契约门禁 | `server.py:112-130` | `test_rw02_output_contract_violation_becomes_a_safe_failure` | 确认缺陷 |
| RW-02 取消贯通 | `server.py:178` | 无（需并发设计后才能写回归） | 方案待批 |

## 2. RW-06 逐项计划

### RW-06-2 函数式路由（确认缺陷）

- **现状**：`dispatcherHandlers` 记录从不读取 `predicate`；没有 `handlerMethod`/`requestMappingConditions` 时 `complete=False`、`patterns=[]`，`path_pattern` 过滤在 `mappings.py:164` 直接 `continue`，函数路由被静默丢弃；只留下 `MAPPING_CONDITIONS_UNOBSERVED`。
- **失败证据**：`gaps=['MAPPING_CONDITIONS_UNOBSERVED']`、`items=[]`。
- **正确行为**（SPEC 2.3 路由、设计文档 3.2 表格与 C02）：处理 `predicate`/`handlerFunction` 形状；文本 predicate 允许降级解析并标记 `predicateParsed` 语义；信息不足 `matchingComplete=false`；定点路径筛选不得误丢可解析路由。
- **最小修复**：新增有界解析器（输入 ≤4096 字符、路径/条件各 ≤16、仅 `_METHODS` 白名单与 `/` 开头 token；覆盖 `{GET [/x], produces [application/json]}` 与 `(GET && /x)` 形态；`details.handlerFunction` 或 lambda 型 handler → `handlerKind="function"`）。无法解析 → 新增 gap `MAPPING_PREDICATE_UNPARSED` 且 `matchingComplete=false`，不伪造匹配。不执行 predicate、不请求任何路径。
- **影响**：仅 mappings 适配器内部；`handlerKind` 枚举已含 `function`，无需公开 Schema 变更。

### RW-06-3 Liquibase 身份与迁移排序（确认缺陷）

- **现状**：`identity = f"{bean_name}:{change_id}"`（`migrations.py:201`），省略 changelog/author；`normalize_migrations` 过滤后按输入顺序编号（`:319`），未执行调用策略规定的排序。
- **失败证据**：同 Bean 同 id 不同 changelog/author 得到相同 identity `['ordersLiquibase:001', 'ordersLiquibase:001']`；状态顺序为输入序 `['APPLIED','APPLIED','FAILED']`。
- **正确行为**（SPEC 2.3 迁移、调用策略“迁移异常”）：Liquibase 身份包含 changelog/id/author；先 FAILED、MISSING_FAILED、OUT_OF_ORDER、PENDING，再按引擎报告的顺序（Flyway `installedRank`、Liquibase `orderExecuted`，null 在后），再按稳定身份平局键排序；不按版本字符串排序。
- **最小修复**：identity 改为 `changelog:id:author`（缺失分量用显式占位并加 `MIGRATION_IDENTITY_INCOMPLETE`）；`apply_expected` 的比较键自然继承更精确身份；在编号前执行确定性排序（状态优先级 → `orderExecuted` → identity），全部字段有界。
- **影响**：Flyway identity 不变；Liquibase identity 取值变化属内部修复，契约仅要求 string ≤256；基线比较与分页稳定性提高。

### RW-06-4 启动截断与完整计数（确认缺陷）

- **现状**：500 事件上限只写入 gap `STARTUP_NODE_LIMIT`（`startup.py:55`）；处理器仍 `totalMatched=len(items)`（`analyze_startup.py:50`），`truncated` 仅在 `finalize` 因 `MAX_RESULTS` 触发。
- **失败证据**：`totalMatched=500`、`truncationReasons=['MAX_RESULTS']`。
- **正确行为**（SPEC 2.2 统计、调用策略）：枚举不完整时 `totalMatched=null`；预算省略标 `truncated=true` 并写入 `truncationReasons`。
- **最小修复**：适配器区分“枚举上限”与其它缺口；处理器在 `STARTUP_NODE_LIMIT` 出现时 `totalMatched=None`、`truncated=True`、reason 去重入列（≤8）；其它缺口仍只标 partial。
- **影响**：仅 startup 工具；契约允许 `totalMatched=null`。

### RW-06-5 嵌套 configprops（确认缺陷）

- **现状**：只遍历 `properties` 第一层（`config.py:183`）；`_project` 对 dict/list 返回 `(None, "notReported")`，既无行也无缺口。
- **失败证据**：keys=`['app.mode','app.nested']`，嵌套叶子标记值消失，`gaps=[]`。
- **正确行为**（交接完成标准）：登记属性与嵌套叶子语义一致；受深度/节点/授权预算约束；不支持结构必须留缺口，不输出原始值树。
- **最小修复**：对 dict 递归展开为点号键（深度 ≤4、叶 ≤500，逐叶执行 `policy.allows` 与 `_project`）；list、超深或超叶 → `CONFIGPROPS_NESTED_UNSUPPORTED` / `CONFIGPROPS_NESTED_LIMIT`，后者同时 `truncated`；`includeValues=false` 仍为 `notReported`。
- **影响**：仅 config 适配器；无原始值出站。

### RW-06-1 线上 SourceCatalog 事实接入（能力缺失，方案待批）

- **现状**：`build_services` 恒注入空目录（`server.py:50`）；离线 `replay` 从 `scenario/source-catalog.json` 加载（`replay.py:105`）并用 `FIXTURE_SUBSTITUTED` 标注。线上多跳阻塞链只能 unknown/partial。
- **判定**：这不是错误输出，而是缺少可信事实入口；按交接要求不能把离线工厂推导声称为线上自动发现。
- **方案**：新增公开入口 `--source-catalog PATH` 与对应配置键，复用现有 `load_source_catalog`（已实现 256 KiB、target 绑定、路径/行号/标识符校验与预算）；未提供事实时在 catalog gaps 增加 `SOURCE_CATALOG_UNPROVIDED`，让 unknown 可归因；禁止自动发现、禁止读取源码文件、禁止从响应推导事实。
- **审批**：属于新增公开 CLI/配置入口，需确认后再实施。
- **验收**：回环服务 + 合成 catalog 复现工厂/多跳链；无 catalog 时保持 unknown 且带 gap；越界路径、超限、targetId 不匹配全部拒绝。

## 3. RW-02 复核与方案

### 3.1 协议时代决策（最高优先，需批准）

已取证 MCP 2026-07-28 规范（`basic/versioning`、`server/tools`）：

- 2026-07-28 是 **modern** 时代：**没有 initialize 握手**，每个请求在 `_meta` 携带 protocolVersion/clientInfo/clientCapabilities；不支持版本 **MUST** 返回 `-32022 UnsupportedProtocolVersionError` 并列出 `supported`；服务器 **MUST** 实现 `server/discover`。
- `initialize` 握手属于 **legacy**（2025-11-25 及更早）。
- 当前实现的 `initialize` **原样回显**任意 ≤64 字符版本，并把 `2026-07-28` 当默认值——正好违反 SPEC“SDK 必须实际支持所宣称协议及必需请求元数据，不能仅更换版本标签”。

可选路线：

| 选项 | 内容 | 代价与风险 |
| --- | --- | --- |
| A（推荐为最终路径） | 接入官方 MCP Python SDK，由其处理 modern `_meta`、`server/discover` 与错误映射 | 新增运行依赖，需要依赖审批与迁移回归 |
| B | 自实现 modern 所需面（`server/discover`、`_meta` 校验、-32022、resultType） | 无新依赖，但重复 SDK 行为、易漂移 |
| C（过渡） | 明确声明为 legacy-only：supported 集合只列实际实现的版本（如 2025-06-18 语义），拒绝未知版本；文档撤下 2026-07-28 宣称 | 立即纠正不实声明；modern 客户端需等 A |

建议：先按 C 收口正确性，A 作为 RW-02 主路径待依赖批准，B 仅在 A 批准前用于必要兼容修复。

### 3.2 与时代无关的最小修复（3.1 决策后可直接实施）

1. **描述器逐字发布契约**：`_descriptors()` 原样发布 `list_tools()` 条目（含 `outputSchema`）；静态 tools/list 不参与 16 KiB 回包裁剪。
2. **封装门禁前移**：`jsonrpc` 必须为字符串 `"2.0"`（否则 `-32600`）；`id` 仅允许 string/int/null，bool 与 list/dict/float 拒绝（`-32600`）；未知工具与 `params` 非对象 → `-32602`；未知名方法保持 `-32601`。以上全部发生在 Handler 与任何 IO 之前。
3. **在线输出契约门禁**：`sanitize` 之后、序列化之前用对应 `outputSchema` 校验 `structuredContent`；违规返回安全失败信封（建议 `OUTPUT_CONTRACT_VIOLATION`、`status=invalid`、`data=null`、`isError=true`），不回传校验器原文。
4. **版本协商**：按 3.1 决策，legacy 回复受支持版本，modern 回复 `-32022` + `supported`。

### 3.3 生命周期与取消（需设计批准）

- legacy 时代：`initialize` 之前的 `tools/*` 按选定 legacy 版本拒绝（如 `-32002`），`run_stdio` 维护会话状态。
- 取消：把 STDIO 循环改为“单读取任务 + 在途执行任务注册表”，`notifications/cancelled` 按 request id 取消对应工具调用；保持 stdout 单写者与 `quiet_console` 的 fd 隔离，并发上限 1–2，不引入全局状态竞争。方案获批后再补并发取消回归（当前无红用例，避免以猜测固化设计）。

### 3.4 验收

- 13 项 RW-02 回归全绿，且不新增 skip/xfail。
- 进程级补充：初始化顺序、非法封装零 IO、未知工具、取消、双副本 16,384 字节上限。
- 第三方 MCP 客户端互操作单独记录客户端与版本（RW-05/后续），不与离线门禁混报。

## 4. 当前红基线与验证命令

| 命令 | 当前结果 | 退出码 |
| --- | --- | --- |
| 两个新回归文件 | **18 failed, 0 passed** | 1 |
| 全量 pytest（无 cache） | **706 passed, 18 failed** | 1 |
| mypy / 契约 / Ruff check / Ruff format / `git diff --check` | 与交接一致全部通过 | 0 |

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 \
  python -m pytest -q -p no:cacheprovider \
  tests/unit/test_rw06_architecture_regressions.py tests/unit/test_rw02_protocol_regressions.py --tb=short
```

## 5. 待批准清单

1. **RW-02 协议时代路线**（A 接 SDK / B 自实现 modern / C 过渡 legacy-only）。
2. **RW-06-1** 新增 `--source-catalog` 公开入口。
3. **RW-02 3.3** 并发与协议取消设计（若先于 SDK 迁移实施）。
4. 若选 A：新增 `mcp` SDK 运行依赖。

获批前不修改公开 inputSchema/outputSchema、不新增依赖、不改变目标暴露与凭据行为；已确认的 RW-06-2/3/4/5 与 RW-02 3.2 属内部正确性修复，可在明确批次授权后按“先失败回归 → 最小修复 → 实际验证”落地。

## 6. 实施与验证结果（2026-10-20 追加）

### 6.1 已落地的最小修复

| 项 | 修复内容 | 文件 |
| --- | --- | --- |
| RW-06-2 函数式路由 | 新增有界 `_parse_predicate`（`{GET [/x], produces [...]}`、`(GET && /x)`）；`handlerKind=function`；无法解析 → `MAPPING_PREDICATE_UNPARSED` 且 `matchingComplete=false` | `adapters/mappings.py` |
| RW-06-3 Liquibase 身份 | identity 改为 `changelog:id:author`，缺失分量加 `MIGRATION_IDENTITY_INCOMPLETE` | `adapters/migrations.py` |
| RW-06-3 稳定排序 | 编号前按“异常状态 → 引擎顺序（null 后置）→ identity”排序 | `adapters/migrations.py` |
| RW-06-4 启动截断 | `STARTUP_NODE_LIMIT` 时 `totalMatched=null`、`truncated=true`、原因写入 `truncationReasons` | `tools/analyze_startup.py` |
| RW-06-5 嵌套 config | 嵌套 dict 按点号键展开（深度 ≤4、叶 ≤500，逐叶登记与投影）；list/超限 → 专用缺口，超限同时 truncated | `adapters/config.py`、`tools/inspect_config.py` |
| RW-02 描述器 | `tools/list` 原样发布含 `outputSchema` 的契约条目 | `server.py` |
| RW-02 封装门禁 | `jsonrpc` 必须 `"2.0"`；id 仅 string/int/null（长 ≤128 字节）→ 否则 `-32600`；`params` 非对象与未知工具 → `-32602`；全部在 Handler 与 IO 之前 | `server.py` |
| RW-02 输出契约门禁 | sanitize 后用 `outputSchema` 校验 `structuredContent`；违规 → `OUTPUT_CONTRACT_VIOLATION` 安全失败信封 | `server.py` |
| RW-02 版本协商 | `SUPPORTED_PROTOCOL_VERSIONS=("2025-06-18",)`；不再回显不支持的版本 | `server.py` |
| RW-06-1 可信事实入口 | `build_services(source_catalog=...)`（目标不匹配 → `SOURCE_TARGET_MISMATCH`）；缺省事实记录 `SOURCE_CATALOG_UNPROVIDED` 为 provenance；CLI 新增 `--source-catalog` / `SPRING_RUNTIME_SOURCE_CATALOG`，复用 `loads_bounded` + `load_source_catalog` 的 256 KiB/目标绑定/路径校验；非法文件统一固定 stderr + exit 2 | `server.py`、`cli.py`、`tools/inspect_beans.py` |

### 6.2 既有断言改写（仅 4 处，均为旧行为编码）

1. `tests/integration/test_mcp_host_and_http.py`：initialize 断言由“回显 2026-07-28”改为协商到 `SUPPORTED_PROTOCOL_VERSIONS[0]`；未知工具由工具信封改为 JSON-RPC `-32602`。
2. `tests/integration/test_stdio_host_process.py`：未知工具的进程级断言改为 `-32602`（两处）。
3. `tests/unit/test_logic_boundary_audit_regressions.py`：TC-BUG-12 未知字符串分支改为协议错误；非字符串 name 的 `INVALID_ARGUMENT` 工具错误保持不变。

改写理由：这四处断言固定的是 RW-02 明确要纠正的旧行为（未经 SDK 却宣称 modern 版本；未知工具作为工具执行结果）。核心不变量（非法 name 零 IO、受控错误、无 INTERNAL_ERROR）全部保留并加强，未删断言、未加 skip/xfail。

### 6.3 实际验证（2026-10-20）

| 检查 | 结果 | 退出码 |
| --- | --- | --- |
| RW-02 + RW-06 单元回归 | **21 passed**（18 原有 + 3 新增） | 0 |
| 进程级 CLI 事实入口（`test_stdio_host_process.py`） | **8 passed**（含 2 新增） | 0 |
| 最终全量 | **729 passed，0 failed**，24.90 秒 | 0 |
| mypy（`--cache-dir=/dev/null`） | 49 源文件通过 | 0 |
| 契约校验 | 8 组独立 Schema、144+ 断言通过 | 0 |
| Ruff check / format（`--no-cache`） | 通过 / 82 文件 | 0 |
| git diff --check | 通过 | 0 |

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 \
  python -m pytest -q -p no:cacheprovider tests/unit/test_rw02_protocol_regressions.py tests/unit/test_rw06_architecture_regressions.py --tb=short
```

### 6.4 仍未实施（等待批准）

1. **RW-02 3.3** `initialize` 生命周期门禁与协议取消贯通（并发设计评审）。
2. **RW-02 3.1** modern 时代路线：接入官方 MCP SDK（新增运行依赖）或等价实现；当前宿主只声明 legacy 支持集。
3. 文档同步（RW-08）：README/SPEC/CHANGELOG 与运行时可声明版本、`--source-catalog` 入口及已修控制的现状说明。