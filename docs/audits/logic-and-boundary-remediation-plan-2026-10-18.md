# 逻辑漏洞与边界条件缺陷修复计划

日期：2026-10-18。

状态：**计划已形成；尚未开始业务源码修复。** 本次请求仅授权写入计划文档，不撤销此前禁止修改 `src/` 的限制。执行下述修复前，需要用户明确授权业务源码编辑。计划中的行为、守卫和测试扩展均不代表已经实现。

## 1. 依据与已验证基线

- 缺陷依据：[逻辑漏洞与边界条件审计报告](logic-and-boundary-audit-2026-10-18.md)。本计划的 TC-BUG 编号仅对应该报告，不对应历史测试中的同名编号。
- 当前状态依据：[项目真实状态与架构审计报告](project-state-and-architecture-audit-2026-10-18.md)。
- 专项测试：[test_logic_boundary_audit_regressions.py](../../tests/unit/test_logic_boundary_audit_regressions.py)，15 个测试函数、19 个参数化用例。
- 上一轮已实际执行：新增前全量 **664 passed，退出码 0**；专项 **19 failed，退出码 1**；新增后全量 **19 failed、664 passed，退出码 1**。新增文件 lint/format 通过。
- 上一轮执行后，`git diff -- src` 为空，`src/` 文件 SHA-256 与该轮开始时一致。这些是上一轮证据，本计划编写期间未重新执行测试。
- 回归失败是当前修复入口，不能通过删除用例、标记 xfail/skip、捕获后忽略异常或断言错误输出来消除。

## 2. 实施范围与授权边界

### 2.1 本计划拟修复的范围

仅修复已复现的 TC-BUG-01 至 TC-BUG-15，以及为这些修复必需的内部模型、边界检查和专项测试。公开八工具名称、inputSchema/outputSchema、固定 GET 约束、双层脱敏、16,384 字节完整工具回包预算保持现有契约。

默认只使用标准库与已有依赖。真实 Spring Boot、生产目标、部署、重启和目标暴露配置不属于修复验证范围。

### 2.2 需要先准备补丁和影响说明的事项

根目录 `AGENTS.md` 的 Ask First 规则适用于公开 Schema、新增运行依赖、扩大目标/IP 白名单及目标暴露/采集配置变更。

- TC-BUG-02 如需新增操作者配置格式或 CLI 参数，先明确字段、默认值、允许值和兼容影响。不能因为工具调用包含 prefix/includeValues 就推导操作者授权。
- TC-BUG-03 首先实现默认回环场景的安全拒绝；远程目标支持需要单独明确登记机制、HTTPS 与非空地址白名单，不自动扩大允许范围。
- TC-BUG-09 优先用标准库实现可取消读取；如必须引入第三方 HTTP 依赖，先提交依赖与实现方案供审批。
- 如发现修复必须改变公开 inputSchema/outputSchema，停止该分支，准备具体契约补丁；其他独立缺陷继续处理。

以上是后续执行边界，不是本次要求用户逐项审批；本次只保存计划。

### 2.3 工作区保护

执行前记录 `git status --short`、现有差异及待编辑文件内容指纹。不得覆盖已有 integration 测试、未跟踪测试、审计文档或 `.vscode/` 内容。优先继续使用独立专项文件，不改历史同名编号测试的含义。

## 3. 分批顺序与依赖

| 批次 | 缺陷 | 目的与依赖 | 当前专项用例数 |
| --- | --- | --- | --- |
| A：资源上限与输入封装 | 01、04、12 | 先阻断无界展开、无界输入和错误类型异常；可独立修复 | 4 |
| B：配置授权与证据类型 | 02、05、08 | 先恢复默认拒绝，再修脱敏类型与 Context 合并；授权关闭不能掩盖离线归一化缺陷 | 4 |
| C：网络安全与取消 | 03、09 | 在真实连接层完成地址守卫和连接生命周期；保留 GET、TLS、无代理、无重定向约束 | 2 |
| D：诊断语义与降级 | 06、07、10、11、13、15 | 按健康、OpenAPI、启动、目标身份和多域隔离分别修复 | 8 |
| E：快照生命周期 | 14 | 统一原快照过期时间与 continuation 行为 | 1 |

每修复一个编号，先运行该编号的专项用例与受影响已有测试。批次完成后运行该批涉及的测试和静态检查。全部批次完成后运行一次全量门禁；无新改动、失败或未解决问题时不重复扩大验证。

## 4. 逐项实施计划

### TC-BUG-01：OpenAPI 共享引用展开预算

- **修改位置**：`src/spring_runtime_mcp/adapters/openapi.py` 的 `_Expander.__init__`、`expand`、`_resolve`、`_project`；必要时由 `tools/search_openapi.py` 传入内部分析预算。
- **实现步骤**：
  1. 每次查询创建独立预算，累计所有操作和共享引用的展开成本，不能在每个属性或每个引用重新计时/计数。
  2. 在递归、构造结果节点前扣减预算，检查截止时间；优先复用已有 `AnalysisBudget` 约定。现有测试的 100,000 次观察上限是安全中止线，不是要求放宽为该数值。
  3. 到达预算时停止继续构造子树，保留有界引用占位、coverage gap、`expansionComplete=false` 及相应截断元数据。
  4. 如加入共享引用缓存，缓存必须有界且不能绕过深度、祖先链环检测或累计输出预算。
- **回归入口**：`test_tc_bug_01_shared_refs_must_obey_global_expansion_budget`。
- **补充验证**：共享 DAG、引用环、直接/二阶引用、Pointer 转义、深度上限；两次调用之间预算不共享。
- **验收标准**：现有安全观察器不再触发；在构建超大结果之前主动返回不完整证据，完整工具回包仍有界。

### TC-BUG-02：配置默认关闭及操作者范围交集

- **修改位置**：`server.build_services`、`tools/inspect_beans.py` 中 `Services` 的内部策略依赖、`tools/inspect_config.py`；必要时增加独立内部授权策略对象。
- **实现步骤**：
  1. 默认 `enabled_endpoints` 排除 env/configprops，配置工具联网前返回 `CAPABILITY_DISABLED`。
  2. 显式启用时要求操作者登记安全属性或前缀；授权对象与查询对象分离，并在服务生命周期内冻结。
  3. 查询筛选与登记范围取交集；值读取权限另行判断，`includeValues=true` 仅缩小已授权行为，不能新增权限。
  4. 全端点读取仍可能取得完整上游响应，必须保留上游预算和本地投影；不能声称 prefix 缩小了 HTTP 响应。
  5. 清查漂移 Profile 读取等其他 env 调用入口，确保共享能力门禁，不能通过另一工具绕开默认关闭策略。
- **回归入口**：`test_tc_bug_02_config_must_be_disabled_before_any_default_http_read`。
- **补充验证**：默认零请求；允许一个前缀时请求其他前缀、`*`、includeValues 的组合；显式授权测试不得使用默认装配假冒授权。
- **验收标准**：默认无配置 HTTP 读取；授权范围外的字段不出站。已有配置成功场景如需调整装配，只能显式登记其合成范围，不能削弱安全断言。

### TC-BUG-03：DNS、地址与真实 peer 守卫

- **修改位置**：`transport/actuator_http.py` 的 origin 解析、连接创建和请求写出边界；为内部登记地址策略提供可信来源。
- **实现步骤**：
  1. 严格规范化 authority 和字面 IP，拒绝非规范数字主机、zone ID 及永久禁止地址。
  2. 对已登记 hostname 的全部 A/AAAA 结果执行同一目标策略；混合允许/禁止地址整组拒绝。
  3. 固定获准数字 IP 建连，禁止 HTTP 库再次解析原 hostname。
  4. HTTP 字节写出前核对真实 peer IP/port；HTTPS 在同一受验证连接上执行证书和原 hostname 身份校验，保持正确 SNI/Host。
  5. 默认回环策略不得因目标名为 local 而跳过检查；不引入工具级 URL/请求头透传。
- **回归入口**：`test_tc_bug_03_registered_host_must_reject_forbidden_dns_before_connect`。
- **补充验证**：混合 DNS 集合、解析变化、peer 不匹配、IPv4-mapped IPv6、非规范数字地址、HTTPS hostname 校验。全部使用离线连接边界或自行启动的回环服务。
- **验收标准**：禁用地址连接次数为零；peer/TLS 拒绝时 HTTP 写出次数为零。测试不能被全局断网夹具提前拒绝而假通过。

### TC-BUG-04：STDIO 有界读取

- **修改位置**：`server.run_stdio` 的逐帧读取路径。
- **实现步骤**：
  1. 用有界读取取代无 size 的 readline，读取量不超过 `MAX_FRAME_BYTES + 1`。
  2. 明确定义换行是否计入帧预算并测试边界。
  3. 对超长未换行输入立即拒绝；优先选择发出固定错误后终止会话，避免无限丢弃等待或将同一超长帧拆成后续请求。
  4. 保留正常 EOF 和有效逐行 JSON-RPC 行为，stdout 不输出诊断文本。
- **回归入口**：`test_tc_bug_04_stdio_must_bound_read_before_allocating_a_frame`。
- **补充验证**：恰好上限、超一字节、无换行、UTF-8 多字节和普通连续帧；新增子进程测试必须设硬超时并清理。
- **验收标准**：输入边界观察到正数有界 size；超限不会先分配任意长度帧或无限等待换行。

### TC-BUG-05：类型感知配置脱敏

- **修改位置**：`policy/sanitization.py` 的 descriptor taint 分支、`adapters/config.py` 的配置字段投影；保留独立 `output_guard`。
- **实现步骤**：
  1. 对配置记录使用类型感知处理，origin 保留 Schema 要求的对象结构，将敏感叶子置空或规范化为 unknown。
  2. `notReported` 的值保持 null；实际屏蔽标量时同步设为 redacted。不能用一个字符串标记替换所有对象和值状态。
  3. 检查敏感属性键、value、bindingValue、origin 之间的传播关系，并保留有界 redactions。
  4. 对最终结果执行现有 outputSchema 校验测试；若增加线上校验，必须在脱敏后校验并使用安全失败信封，不能输出 validator 中的原始数据。
- **回归入口**：`test_tc_bug_05_sensitive_config_must_preserve_output_schema` 的两个参数分支。
- **补充验证**：includeValues=false/true、嵌套敏感属性、origin 有值/缺失、重复脱敏幂等性；现有普通配置功能保持可用。
- **验收标准**：敏感结果通过完整 outputSchema；秘密不出站；未报告和已屏蔽不混淆。

### TC-BUG-06：健康 503 语义

- **修改位置**：`transport/response_gate.py` 状态码判断与观察对象构造；`tools/inspect_beans.py` 的 `_check`。
- **实现步骤**：
  1. 仅对登记的 health、liveness、readiness 逻辑端点允许 503 作为待验证健康证据。
  2. 仍执行媒体类型、有界 JSON 和健康结构检查；保留真实 HTTP 状态，不伪装为 200。
  3. `_check` 同步支持健康端点的已允许状态，其他端点维持现有拒绝。
- **回归入口**：`test_tc_bug_06_health_503_must_remain_usable_down_evidence`。
- **补充验证**：三个健康域的 503 JSON、503 HTML/非法 JSON、401/403、非健康端点 503。
- **验收标准**：有效 DOWN 证据保留且工具 `isError=false`；无效或未授权响应不误放行。

### TC-BUG-07：OpenAPI 输入契约真实投影

- **修改位置**：`adapters/openapi.py` 的 `_parameters` 与操作 requestBody 构建分支，使用 TC-BUG-01 的共享预算。
- **实现步骤**：
  1. 读取参数真实 schema 并经过同一引用展开器，删除固定 string 占位。
  2. 投影 requestBody 的媒体类型和 Schema；按既有 outputSchema 构造记录。
  3. 处理或明确降级 Path Item 参数、Parameter/RequestBody 引用及不支持的结构；未解析内容必须产生 gap 并使完整性为 false。
  4. 对媒体类型或记录裁剪显式报告，保留远程引用禁止请求的规则。
- **回归入口**：`test_tc_bug_07_openapi_must_preserve_parameter_and_request_body`。
- **补充验证**：integer/array/object 参数、本地引用、无请求体、多个媒体类型、非法结构、深度与总预算。
- **验收标准**：复现文档返回 integer 与真实请求体；不再用空数组或错误类型冒充完整契约。

### TC-BUG-08：配置 Context/Bean 身份隔离

- **修改位置**：`adapters/config.py` 中 env 与 configprops 的合并、绑定记录身份和排序。
- **实现步骤**：
  1. 绑定身份至少包含 `(contextId, configurationBean, propertyKey)`。
  2. 将全局 env 生效事实与 Context 绑定事实分离；关联可以复制已投影事实，但不能覆盖其他绑定记录。
  3. 保留各绑定 Context、父 Context 和 Bean 身份，不用 `old_context or new_context` 掩盖冲突。
  4. 多个 Bean 或 Context 报告同属性时稳定输出独立记录，再计数和分页。
- **回归入口**：`test_tc_bug_08_config_bindings_must_not_mix_contexts`。
- **补充验证**：同 Context 不同 Bean、父子 Context 同名前缀、遍历顺序反转、contextId 精确筛选。
- **验收标准**：a/cfgA/alpha 和 b/cfgB/bravo 同时保留；改变输入顺序不改变身份关联。

### TC-BUG-09：取消与真实连接生命周期

- **修改位置**：`transport/actuator_http.py` 的异步 request、连接所有权和有界读取；`transport/response_gate.py` 的超时清理边界。
- **实现步骤**：
  1. 以单次交换的绝对截止时间控制连接、TLS、响应头和响应体读取，不只依赖每次 socket 等待超时。
  2. 使取消方可以终止实际连接；若保留工作线程，必须具备可靠的中断和结束确认，不能仅取消 Future。必要时改用标准库异步连接实现。
  3. 所有路径统一关闭连接，在返回超时前完成规定的清理；重复超时不累积在途线程或连接。
  4. 不直接把宿主改为并发 dispatch：当前 console 隔离会操作进程级文件描述符，协议取消通知和宿主并发需要另行审查，不能借此引入全局状态竞争。
- **回归入口**：`test_tc_bug_09_timeout_must_close_the_underlying_http_exchange`。
- **补充验证**：真实回环慢响应头/慢分块、主动取消、TLS 阶段取消、连续多次超时；确认任务和连接结束，测试自身也必须清理。
- **验收标准**：TARGET_TIMEOUT 返回时底层连接已关闭；真实慢速读取不会因小流量无限延长生命周期。

### TC-BUG-10：非法及自定义健康状态

- **修改位置**：`adapters/health.py` 的 `_children`、状态过滤及 coverage gap 生成。
- **实现步骤**：
  1. 对 null、标量或非法 components/details 子节点记录缺口，不能在字典推导中过滤后静默丢失。
  2. 默认仅隐藏明确正常的 UP 组件；自定义状态保留原值，不能由于不在固定异常枚举中而被当作正常。
  3. 缺失或非法 status 返回 unknown/partial，保持整体状态与分页子项独立。
- **回归入口**：`test_tc_bug_10_health_must_not_hide_invalid_or_custom_components` 的两个参数分支。
- **补充验证**：自定义状态、缺失 status、空组件集合、Boot v2 details 树和 v3 components 树。
- **验收标准**：null 组件产生 partial/gap；FATAL 组件在默认查询中可见。

### TC-BUG-11：启动时间线跨度

- **修改位置**：`adapters/startup.py` 的总耗时与 durationBasis 计算。
- **实现步骤**：
  1. 解析有时区的时间线起点和事件开始/结束时间，定义统一时间基准。
  2. 使用可信时间边界计算跨度，不能使用最长步骤或重叠步骤之和。
  3. 时间缺失、非法或反向时按证据完整性返回 unknown/partial；未知值不能填 0。
  4. 总耗时基于完整可见时间线，不能随 stepName/minDuration 等结果过滤变化。
- **回归入口**：`test_tc_bug_11_startup_span_must_include_separate_root_steps`。
- **补充验证**：分离根步骤、重叠步骤、起点早于首事件、跨时区表示、缺失结束时间与负跨度。
- **验收标准**：本轮两个分离步骤返回 3,000 ms；不能计算时不标记 timelineSpan 为确定值。

### TC-BUG-12：非法工具名称类型

- **修改位置**：`server._call_tool` 的 name 类型与查表分支。
- **实现步骤**：
  1. 先独立检查 name 为字符串，再执行任何字典 membership/get。
  2. 非字符串返回受控输入错误，未知字符串走明确的未知工具路径；不进入 Handler、不联网。
  3. 保留输入错误与内部故障的区分，不能依赖最外层 except 转成 INTERNAL_ERROR。
- **回归入口**：`test_tc_bug_12_non_string_tool_name_must_be_a_controlled_input_error` 的数组、对象分支。
- **补充验证**：null、缺失 name、布尔值、未知字符串和正常名称。
- **验收标准**：dispatch 不泄漏 TypeError；响应是允许的输入错误信封，且没有 IO。

### TC-BUG-13：失败信封目标身份

- **修改位置**：八个 Handler 的异常出口，特别是 `diagnose_health`、`check_migrations`、`inspect_beans`；必要时增加内部安全目标身份辅助函数。
- **实现步骤**：
  1. 参数验证成功后保存已验证 targetId，所有后续异常出口显式传入 failed。
  2. 参数尚未验证时不得盲目回显原始 targetId；非法输入继续使用安全占位身份。
  3. 遍历所有领域错误、超时、结构异常和内部错误出口，统一身份传递，不只修当前健康用例。
- **回归入口**：`test_tc_bug_13_failures_must_keep_the_registered_target_identity` 的授权失败、超时分支。
- **补充验证**：八工具非 local 目标失败；非法 targetId、未知 targetId、游标失败与部分端点失败。
- **验收标准**：已验证 demo 调用的失败仍归属于 demo；输入中的非法身份不被直接写回。

### TC-BUG-14：分页绝对 TTL

- **修改位置**：`snapshots.py` 的内部 entry/续页 API；`tools/support.py` 的 finalize/resume；Bean 专用 `_page` 的相同续页路径。
- **实现步骤**：
  1. 为同一观察快照保存固定 expires_at；新游标继承原截止时间，而不是重新使用 now+ttl。
  2. 可选择不可变快照加偏移游标，或 continuation 显式携带内部截止时间；不得把可信截止时间暴露为客户端可修改字段。
  3. 保留查询绑定、不可变复制、LRU、内存上限与脱敏出口；过期后所有派生游标失效。
- **回归入口**：`test_tc_bug_14_continuation_must_not_extend_snapshot_absolute_ttl`。
- **补充验证**：t=59 续页、t=60 边界、t=118 派生游标；Bean 与通用工具两种分页实现均覆盖。
- **验收标准**：第三次调用返回 CURSOR_EXPIRED，不因续页延长旧证据有效期。

### TC-BUG-15：多迁移引擎归一化隔离

- **修改位置**：`tools/check_migrations.py` 的逐引擎读取/归一化边界；`adapters/migrations.py` 的引擎解析与结果合并。
- **实现步骤**：
  1. 每个引擎独立归一化并捕获预期结构错误，错误携带明确 endpointId。
  2. 合并有效引擎的记录后执行基线比较、过滤、排序和分页，避免对每个引擎重复生成基线推断。
  3. 单域结构缺失返回 partial；仅所有请求域都不可用时返回整体失败。
  4. 覆盖不完整时 totalMatched 不应冒充完整范围计数，错误不携带原始响应或异常文本。
- **回归入口**：`test_tc_bug_15_malformed_engine_must_not_discard_other_engine_evidence`。
- **补充验证**：交换 Flyway/Liquibase 正常与异常角色；两域均非法、一域未授权、一域空库存、显式单引擎模式。
- **验收标准**：Flyway version=1 保留，状态 partial，errors 明确指向 liquibase。

## 5. 验证命令与停机条件

以下是后续实施时的命令；**本次仅写入计划，未执行这些命令**。使用 `PYTHONDONTWRITEBYTECODE=1` 避免写入业务目录的字节码，禁用 pytest 缓存；测试仅使用合成数据和自身启动的回环服务。

### 5.1 单缺陷与专项门禁

修复某项后将下面的 tc_bug_01 替换为对应编号；该项修复后必须通过，尚未修复项维持正确行为断言，不人为变绿。

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q -p no:cacheprovider tests/unit/test_logic_boundary_audit_regressions.py -k tc_bug_01 --tb=short
```

全部修复后运行专项：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q -p no:cacheprovider tests/unit/test_logic_boundary_audit_regressions.py --tb=short
```

目标是现有 19 个专项用例全部通过，新增边界用例也通过；不允许 xfail/skip 替代修复。

### 5.2 最终完整门禁

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with mypy==1.18.2 --with jsonschema==4.25.1 python -m mypy src/spring_runtime_mcp
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with jsonschema==4.25.1 python scripts/validate_tool_contract.py
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with ruff==0.14.1 ruff check src tests scripts
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with ruff==0.14.1 ruff format --check src tests scripts
```

全量预期包含当前 664 个原有用例与 19 个专项用例；后续增加测试时数量相应增长，不能靠删除旧用例保持固定数字。确需改变旧测试装配时，在差异说明中列出其旧假设、契约依据和保留的正确行为断言。

### 5.3 失败处理

- 当前专项用例仍失败：继续定位并修复该编号，不提交“已解决”结论。
- 出现新基线失败：区分旧测试的错误授权装配与真实功能回归，保留安全断言，修复实现或显式测试装配。
- 出现权限、依赖或公开契约变更需求：停止对应分支，准备可审阅补丁和影响说明；继续其他独立项。
- 真实 Spring Boot、客户端互操作、真实 TLS/SNI 版本矩阵没有另行授权时不执行，也不以离线结果宣称通过。

## 6. 交付与关闭标准

每项缺陷关闭时记录：修改函数、修复后的可见行为、原始失败用例及通过结果、必要的新增边界用例、安全/兼容性影响和仍未解决限制。

最终交付应包含：

1. 15 项缺陷状态表，分别标明已修复、仍阻塞或待批准，不能只给一个总体“通过”。
2. 实际执行命令、退出码和测试统计；最终完整门禁通过后停止重复测试。
3. `git status --short`、`git diff --stat` 与业务源码差异说明，明确哪些变更由本次任务产生，哪些是已有用户修改。
4. 更新审计报告的修复状态时保留历史证据；不得把首次审计时的静态判断或回归红灯记录重写成当时已修复。

**本计划完成的标志是计划文档可供审阅；修复任务完成的标志是获得源码编辑授权后，15 项正确行为回归及相关完整门禁通过。两者不能混同。**
