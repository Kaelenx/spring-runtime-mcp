# 安全红队审计报告 — 2026-10-17

> 历史审计快照：本文及同目录机器清单记录当时的 29 模块基线，不覆盖后来新增的七个工具处理器、STDIO 宿主和 HTTP 连接器。v0.1.0 当前交付、重新执行的检查与未解决边界见 [版本记录](../../CHANGELOG.md)，不能将本报告视为当前全部代码的安全验收。

本报告覆盖当前工作树的全部 29 个 Python 生产模块、73 个显式函数/方法，以及测试入口与维护脚本。审计日期按 Asia/Shanghai 记录，代码清单生成时间为 2026-10-16T16:21:50Z。逐函数、行号及源文件 SHA-256 见 [security-callpoints.json](security-callpoints.json)。该哈希只针对代码文件，不针对诊断数据或凭据。

## 结论与验证边界

已修复下表所列可复现缺陷，保留并运行全部攻击回归。当前 CLI 仅提供离线重放；Bean 工具是唯一实际处理器。仓库没有套接字 HTTP 连接器、凭据装配器或官方 MCP SDK/STDIO 宿主。因此，“未登记主机输入不会触发 DNS/请求”和“逻辑请求只能固定 GET”已有生产路径测试；“DNS 后实际 peer IP 校验、TLS、代理禁用、流式解压限制”不能以 Mock 通过冒充已实现。

报告中的穷举范围是当前源代码与可达数据流，不构成对未来代码、任意第三方插件或模型行为的形式化安全证明。未发现当前 CLI 可达的真实 SSRF 网络出口；未来上线的连接器仍受文末阻断条件约束。

## 按严重度记录的发现与补丁

| 编号 / 严重度 | 调用点与触发条件 | 已落地修复 | 回归证据 |
| --- | --- | --- | --- |
| RT-01 / 高 | `ResponseGateReader.__init__/read` 保存外部 mutable set；后续 `update` 或属性重赋值能开启原未批准端点/目标 | frozen dataclass；启动时复制 frozenset；实例 ID 严格校验；每次调用同时检查不可变内建端点表和已批准集合 | `test_network_policy_config_is_frozen_and_validated`、`test_readonly_in_place_configuration_merge_cannot_bypass_hard_allowlist`、属性重赋值和非法 ID 用例 |
| RT-02 / 高，防御纵深 | `serialize_response`、`SnapshotStore.put/get` 只信任“调用者已脱敏”，直接调用 helper 可输出/缓存连接串、Bearer、JWT 或 PEM | 新增独立 `ensure_safe_output`，在返回 wire bytes、写缓存和读缓存时拒绝危险树；新策略识别到不安全旧页时立即淘汰；支持显式注入已知凭据 Sanitizer；结构、字节及二次 JSON 均受限 | `test_leakage_serializer_is_a_mandatory_guard`、`test_leakage_cache_is_a_mandatory_guard`、`test_leakage_sinks_use_registered_opaque_credentials`。此项不声称原 Bean 正常路径已存在任意 raw-body 透传 |
| RT-03 / 高 | `EndpointReadError → inspect_beans → failed/error` 将 exception.endpoint_id/http_status 直接放入结果；未知异常可逃逸到上层 | 仅允许固定端点枚举、固定错误码和有效整数 HTTP 状态；未知异常变为本地 INTERNAL_ERROR；不回显 repr/cause/stack/Location | `test_leakage_endpoint_error_metadata_is_not_echoed`、`test_leakage_unknown_exception_is_structured_and_quiet` |
| RT-04 / 高 | `replay.main` 中 argparse 报错、库 print/os.write、缓存的 stdout buffer、atexit 日志及取消异常可污染 stdout/stderr | 固定 prog 和无回显解析器；计算期间同时隔离 Python 流与 FD 1/2；恢复前刷新旧流到丢弃出口；只写最终 JSON-RPC；退出阶段继续封闭 FD；取消返回安全错误 | `test_redteam_stdio.py` 覆盖正常行、参数错误、未知异常、Python/原生写入、缓存流、atexit、取消和 fixture 路径 |
| RT-05 / 中 | `SanitizerPipeline.walk` 可保留 message、nextAction 字符串及通用 scalar 自由文本，辅助函数复用时可恢复提示注入 | message/steps 仅保留本地固定配方；原始 description/comments/details/stack 等不投影；未知自由文本整叶遮蔽；最终出口拒绝未封装自由文本和二次序列化的指令 | `test_injection_raw_runtime_text_cannot_survive_projection`、`test_injection_generic_scalar_fields_do_not_reintroduce_free_prose`、嵌套 JSON 与长文本用例 |
| RT-06 / 中 | `inspect_beans` 在脱敏前对 aliases/type/context 做过滤；被遮蔽的值仍影响 totalMatched，形成前缀探测信号 | 选择器仅匹配可见的脱敏标识；任何隐藏标识使覆盖率 partial、totalMatched=null；不对隐藏值提供精确缺失结论 | `test_leakage_redacted_alias_cannot_be_recovered_through_filter_counts`；既有 alias/parent/bidirectional 测试继续保留 |
| RT-07 / 中 | 高危值检测漏掉多重编码、部分 Unicode 分隔/格式字符、小写 PEM 和本地 file/jar URI | 有限轮百分号、HTML/NFKC 视图、受限 Base64 文本检查；残余多重转义失败关闭；私钥标记忽略大小写；本地 URI 整叶遮蔽；限制扫描和递归预算 | `test_leakage_obfuscated_credential_patterns_are_detected`、`test_leakage_deep_percent_unicode_and_base64_wrapping` 及原 JWT/PEM/URI 单元测试 |
| RT-08 / 中，需本地文件控制 | `services_for` 固定文件名仍可跟随 fixture 符号链接；先 read_bytes 后解析也不能限制读取阶段大小 | 新增 `read_fixture`：目录 FD 逐段 O_NOFOLLOW、仅普通文件、无路径穿越、读取时字节上限和确定关闭；不提供公开文件路径参数 | fixture 外部链接、根目录链接和读取预算用例 |
| RT-09 / 中，防御纵深 | `load_source_catalog` 在严格检查前调用 json.dumps，循环、超大整数或畸形 Unicode 可产生非领域异常或额外分配 | 在序列化前用共享 bounded-tree 校验器检查节点、深度、类型及字节预算；源码路径仅为元数据，分析器不读取/执行该路径 | `test_leakage_source_catalog_preflights_invalid_trees`、既有 source/path/type/factory 测试 |

没有把静态扫描的误报算成安全发现：`services.snapshots.put` 是本地缓存写入，不是 HTTP PUT。静态回归只对此精确调用点作例外，仍禁止任何其他 put/post/patch/delete 网络构造。

## 全链路数据流核查

### 目标与 HTTP 边界

`inputSchema → validate_arguments → registered target check → RuntimeReader.read → ResponseGateReader.read → 唯一 transport.request`。工具输入不存在 url/origin/baseUrl/proxy/header/method 透传；额外字段在读取前失败。工具名称及 annotations 来自本地静态契约，OpenAPI 内容不能注册工具。

唯一请求构造点将 method 固定为 GET、follow_redirects 固定为 False，传递 5 秒及 8 MiB 预算。逻辑端点只能为 beans/conditions/health/mappings/flyway/liquibase/startup/openapi。shutdown、heapdump、logfile、sessions、caches、loggers、quartz 及编码、点段、分号、查询、路径拼接变体均不能成为该集合成员。startup 不存在 POST 回退。此实现不接受 HEAD override；未来 HEAD 需要单独实现受控读取。

301/302/303/307/308、401/403、404 先于 body JSON 解析处理；不将 HTML 或 Location 放入结果。响应模型不保留任意 HTTP status text/headers。传输异常仅映射本地代码；布尔/越界状态码和异常 response 类型被拒绝。OpenAPI servers/$ref、迁移文本和健康 details 不触发第二次请求。

地址矩阵覆盖 `169.254.169.254`、mapped IPv6、0177 八进制、十进制整数、十六进制、10/172.16/192.168/fc00 私网和重绑定域名。当前结果均为输入/登记拒绝、DNS 与 HTTP 次数为零。它们验证的是**不存在动态主机输入面**，不表示已经运行“DNS 解析后实际套接字 IP 比对”。

### Bean、条件与源码事实

`ParsedObservation → validate_tree → normalize_beans → aliases/parent indexes → forward/reverse graph → visible selectors → projection → SanitizerPipeline`。

canonical name、aliases、parentId、type、scope、resource、requested dependency 和 observationId 都经标识/资源投影。没有用原始名字构造公开 ID、redactions path 或游标；Bean/Condition ID 为本地序号。源码类型与精确配置源关联只产生有界结构事实。conditions message 的嵌套语法必须完整匹配；未知、注入后缀、冲突和传递证据缺失保留 unknown/partial，不以原文替代归因。

`SourceCatalog` 只由内部注入或固定 scenario 文件提供；sourceFile 不被执行或读取。别名和父容器关系只影响图查找，不能影响目标地址、端点、请求方法或工具注册。图与输出均有预算，预算耗尽不回退原文。

### 脱敏、缓存与二次序列化

`typed projection → SanitizerPipeline → _page → ensure_safe_output → canonical JSON-RPC`；续页为 `sanitized continuation → cache guard → opaque cursor → query digest check → cache read guard → fresh sanitization → wire guard`。

实际扫描覆盖 inputs/value/origin、键名变体、连接 URI、赋值参数、JWT/JWE、Bearer/Basic、PEM、已知凭据、嵌套 JSON 及可识别编码。敏感属性的 value/origin/inputs 共享 taint。未知动态键被省略，resource 仅保留安全 classpath 相对标识。错误与配方来自本地固定库，不采纳远端“修复命令”。

完整回包预算包含 structuredContent 和兼容 text 副本；先确定合法树与完整长度，成功返回前再独立检查安全性。拒绝时不返回部分序列化文本。缓存仅保存脱敏结果的 canonical bytes，查询绑定不包括 cursor 但包括其余所有公开参数；不能跨查询/目标取页。已知凭据策略传递到写缓存、读缓存及 wire guard；原始响应与凭据不写入日志。

Opaque 秘密没有通用识别格式。安全结论依赖字段投影、敏感键上下文、已登记凭据和格式检测，不能承诺识别任意编码的任意未知字符串。今后实现 config/OpenAPI/health 等处理器时必须继续采用显式字段规则，不能把本次 helper 当成“任意 JSON 已安全”的证明。

### 提示注入与 STDIO

当前实现选择省略原始自由文本；不依赖提示词让模型“不要执行”。原始 description/comments/stack/details 不出站；运行事实仍属不可信来源，由 source/endpoint/observation 标识来源。未扩充公共 Schema 加入伪造的 trust 标签。模型仍可能对合法业务标识赋予语义；本服务不能约束客户端自身的 Shell/文件/其他 MCP 工具权限。

实际 replay 进程只在所有检查后写一行 JSON-RPC。argparse、第三方库、native FD 写入、缓冲流和退出日志均不能另写协议通道。取消、Schema 错误和未知异常不带 traceback 或原始消息。此封闭覆盖离线 CLI 的进程生命周期；尚不存在 SDK STDIO 宿主，不能把 CLI 测试描述为 SDK 生命周期已验收。

`tests/security/redteam-audit.test.ts` 的 TAP 输出和 `scripts/validate_tool_contract.py` 的校验摘要属于显式测试/维护命令，未被注册为 MCP 运行入口。

## 回归执行

```bash
node --test tests/security/redteam-audit.test.ts
uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q tests/security
uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q
uv run --offline --no-project --with mypy==1.18.2 python -m mypy src/spring_runtime_mcp
uv run --offline --no-project --with ruff==0.14.1 ruff check src tests
uv run --offline --no-project --with ruff==0.14.1 ruff format --check src tests
uv run --offline --no-project --with jsonschema==4.25.1 python scripts/validate_tool_contract.py
```

测试入口使用 Node 内置 test/child_process 驱动真实 Python 测试，不新增 npm 包或运行依赖。网络默认由父级 conftest 阻断；全部攻击凭据均在测试内合成，不接触真实秘密、生产目标或 SSH 文件。

首轮为 219 项中 33 项失败；扩充后的第二轮又复现 6 项间接路径失败，随后补充并修复缓冲退出、取消和根目录链接用例。所有失败攻击用例均保留，没有删除或弱化安全断言来通过测试。契约示例只把原始迁移 prose 改为 null、把 coverage scope 改为安全标识；仍保留非空 typed records 和全部 Schema 正反例，公开工具 Schema 未修改。

最终验收：指定 TypeScript 入口 **5/5 组通过**，执行 **257** 项真实 Python 红队用例；全量 **595 passed / 0 failed / 0 skipped，17.04 秒**。严格 mypy 29 模块、Ruff lint/51 文件格式及既有 144+ 契约断言全部通过。各向量计数、工具版本及命令见 [security-verification.json](security-verification.json)。

## 后续上线阻断项

1. **真实网络连接器尚不存在。** 在启用前必须按安全规格证明严格 origin 注册、IP 规范化、DNS 全组校验、数字 IP 固定建连、实际 peer IP/port 比对且在认证/HTTP 写入前完成；覆盖 DNS Rebinding/TOCTOU、TLS/SNI/证书、代理和 IPv4-mapped IPv6。不允许以本报告的零 DNS Mock 计数替代这些证据。
2. **SDK 宿主与其余七个工具尚不存在。** 接入时必须验收进程级 STDIO 生命周期、输出 Schema 验证、真实流式/解压预算及所有新增字段的数据流。不得动态接纳远端工具定义、URL、凭据或修复命令。

以上不是当前存在并被留着不修的活动网络绕过，而是未实现能力的发布门禁。本次审计不启用连接器、不扩大白名单、不引入运行依赖，也不声称客户端模型已经获得提示注入免疫。

## 逐模块调用点清单

下表列出所有含显式函数的生产模块；空的 package initializer 同样列在机器清单中。行号以同目录 JSON 清单为准。

| 模块 | 全部显式函数/方法 |
| --- | --- |
| `src/spring_runtime_mcp/adapters/beans.py` | `_text` (L18)、`_fact` (L22)、`normalize_beans` (L29) |
| `src/spring_runtime_mcp/adapters/conditions.py` | `combine` (L22)、`_leaf` (L41)、`normalize_conditions` (L118)、`normalize_conditions.parse` (L128) |
| `src/spring_runtime_mcp/adapters/source_catalog.py` | `_text` (L16)、`_strings` (L22)、`load_source_catalog` (L28)、`load_source_catalog.optional` (L88) |
| `src/spring_runtime_mcp/analyzers/bean_graph.py` | `resolve_bean_reference` (L18)、`build_dependency_graph` (L43)、`slice_dependencies` (L73) |
| `src/spring_runtime_mcp/analyzers/condition_failures.py` | `is_assignable` (L18)、`producer_matches` (L53)、`analyze_missing_beans` (L93)、`analyze_missing_beans.visible` (L118)、`analyze_missing_beans.producer_roots` (L141)、`analyze_missing_beans.add_edge` (L151)、`analyze_missing_beans.visit` (L156) |
| `src/spring_runtime_mcp/contracts/definitions.py` | `_definitions` (L20)、`list_tools` (L31)、`definition` (L36)、`_matches` (L43)、`validate_arguments` (L106)、`glob_matches` (L154) |
| `src/spring_runtime_mcp/models/common.py` | `NormalizationError.__init__` (L14)、`AnalysisLimit.__init__` (L22)、`AnalysisBudget.consume` (L41) |
| `src/spring_runtime_mcp/policy/output_guard.py` | `ensure_safe_output` (L31) |
| `src/spring_runtime_mcp/policy/sanitization.py` | `sensitive_key` (L60)、`SanitizerPipeline.__init__` (L67)、`SanitizerPipeline.high_risk` (L88)、`SanitizerPipeline.identifier` (L170)、`SanitizerPipeline.resource` (L179)、`SanitizerPipeline.sanitize` (L204)、`SanitizerPipeline.sanitize.walk` (L232)、`SanitizerPipeline.sanitize.markers` (L317) |
| `src/spring_runtime_mcp/replay.py` | `FixtureReader.__init__` (L26)、`FixtureReader.read` (L30)、`services_for` (L49)、`SafeArgumentParser.error` (L80)、`main` (L85) |
| `src/spring_runtime_mcp/snapshots.py` | `SnapshotStore.__init__` (L29)、`SnapshotStore._digest` (L45)、`SnapshotStore._remove` (L57)、`SnapshotStore._prune` (L60)、`SnapshotStore.put` (L66)、`SnapshotStore.get` (L98) |
| `src/spring_runtime_mcp/tools/inspect_beans.py` | `safe_code` (L46)、`RuntimeReader.read` (L55)、`error` (L69)、`envelope` (L130)、`partial` (L171)、`failed` (L183)、`_check` (L216)、`_page` (L248)、`inspect_beans` (L326)、`_failure` (L591) |
| `src/spring_runtime_mcp/transport/bounded_json.py` | `_pairs` (L12)、`_constant` (L21)、`_integer` (L25)、`loads_bounded` (L33)、`validate_tree` (L88) |
| `src/spring_runtime_mcp/transport/fixture_files.py` | `read_fixture` (L12) |
| `src/spring_runtime_mcp/transport/response_gate.py` | `EndpointReadError.__init__` (L36)、`RegisteredTransport.request` (L52)、`ResponseGateReader.__post_init__` (L72)、`ResponseGateReader.read` (L86) |
| `src/spring_runtime_mcp/transport/responses.py` | `serialize_response` (L16) |
| `src/spring_runtime_mcp/transport/stdio.py` | `quiet_console` (L12)、`seal_console` (L40) |
