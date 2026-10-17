# 人类架构掌控手册

这份手册只描述仓库里已经落地的代码。离线测试覆盖八个工具、MCP stdio 宿主和回环 HTTP 连接器；它没有对真实 Spring Boot 进程做过版本矩阵验收。

## 1. 五层调用链

```text
stdio 客户端
    │  JSON-RPC 一行一帧
    ▼
cli.py                         解析 --base-url / --target-id / --timeout，不接受工具参数里的 URL
    ▼
server.py                      initialize、tools/list、tools/call；stdout 只写 JSON-RPC
    ▼
tools/registry.py              八个名字按字母序绑定 HANDLERS；未知名字 → UNKNOWN_TOOL
    ▼
tools/*.py                     校验参数、按维度读端点、组信封、失败则固定错误码
    ▼
adapters/*.py + analyzers/     把 Actuator JSON 归一化成事实；不发网络请求
    ▼
transport/response_gate.py     只允许已登记 target 与端点；GET、不跟随重定向、5 秒、8 MiB
    ▼
transport/actuator_http.py     唯一允许 import http.client 的生产模块；路径表写死
    ▼
policy/sanitization.py         字段白名单 + 凭据/连接串整叶打码
policy/output_guard.py         出站前再拒自由文本、秘密键和嵌套 JSON
transport/responses.py         structuredContent 与一份文本副本，整帧 ≤ 16384 字节
```

离线重放不走 `cli.py`。`replay.py` 用 `FixtureReader` 把固定 JSON 交给同一个 `call_tool`。

| 你要改的事情 | 先打开 |
| --- | --- |
| 启动参数、环境变量 | `src/spring_runtime_mcp/cli.py` |
| MCP 方法与 isError | `server.py`、`transport/responses.py` |
| 工具有没有实现 | `tools/registry.py` 的 `HANDLERS` |
| 某个工具的读端点顺序和失败是否整单失败 | 对应 `tools/*.py` |
| Actuator 形状差异 | 对应 `adapters/*.py` |
| Bean 别名、父容器、反向依赖 | `analyzers/bean_graph.py`、`adapters/beans.py` |
| 条件三值与阻塞链 | `analyzers/condition_failures.py` |
| 允许的逻辑端点与 HTTP 状态映射 | `transport/response_gate.py` 的 `ENDPOINTS` |
| 真实路径 `/actuator/...` | `transport/actuator_http.py` 的 `_PATHS` |
| 公开参数与输出字段 | `src/schemas/tools-contract.json` |
| 参数默认值何时生效 | `contracts/definitions.py` 的 `validate_arguments` |
| 脱敏键、标识符正则 | `policy/sanitization.py` |
| 带空格的句子能否出站 | `policy/output_guard.py` 的 `LOCAL_PROSE` |
| 游标续页 | `snapshots.py`；分页入口是 `tools/support.py` 的 `finalize` |
| 固定样本 | `tests/fixtures/`；`replay.py` 的 `services_for` |

依赖方向不要反过来。adapter 不能自己 new HTTP 客户端。工具不能把用户字符串拼进 URL。

## 2. 八个工具

公共规则：`targetId` 默认 `local`，`maxResults` 默认且上限 50，参数整体最多 16 KiB。续页重发同一组参数并带上 `cursor`。`ok`/`partial` 的 `isError=false`；`denied`/`unavailable`/`invalid`/`too_large` 时 `data=null`、`totalMatched=null`。单域读失败不会去试 POST 或另一个主机。

| 工具 | 实际读取 | 关键输入 | 输出里先看 | 降级 |
| --- | --- | --- | --- | --- |
| `springboot_analyze_startup` | `startup` | `stepNamePattern`、`beanNamePattern`、`minDurationMs`、`maxDepth` | `springBootVersion`、`items[].durationMs`、`parentStepId`、`ancestorIds` | 404 是 `STARTUP_DATA_UNAVAILABLE`，不是普通未暴露。不 POST。空快照不证明没有瓶颈 |
| `springboot_check_migrations` | `engine=auto` 时 flyway 和 liquibase；否则一个 | `engine`、`contextId`、`migrationBean`、`state`、`version` | `pendingVisibility`、`items[].state`、`attentionRequired` | 一个引擎失败、另一个有数据时保留已读到的记录并标 partial。未报告的 pending 保持 `unknown`，不编造 |
| `springboot_detect_drift` | 只读提交了的维度：beans、mappings、flyway 或 liquibase、env；给了 `sourceModifiedAt` 才读 startup | 至少一项 `expectedBeans` / `expectedRoutes` / `expectedMigrationVersion` / `expectedProfiles`；`expectedScopeComplete` 默认 false | `missingInRuntime`、`unexpectedInRuntime`、`profileMismatch`、`rootCauseInferences` | 省略的维度不读、不比较。空数组才是“预期为空”。scope 未声明完整时不把多出来的运行对象判成 unexpected。Liquibase 版本预期返回 `MIGRATION_VERSION_UNSUPPORTED`。多个 Flyway Bean 未限定返回 `MIGRATION_SCOPE_AMBIGUOUS`。类型关系没有目录证据时不判接口不匹配。`POSSIBLE_NOT_RESTARTED` 最高只是 candidate。`sourceRevision` 不会冒充已核对的构建号，连接器没有 info 路径 |
| `springboot_diagnose_health` | `group=application` → `health`；liveness/readiness 走对应探针 | `componentPath`、`includeDetails`、`includeHealthy`、`maxDepth` | `overallStatus`、`items[].componentPath`、`remediationCodes` | 详情被隐藏时 `HEALTH_DETAILS_NOT_VISIBLE`，不说“没有底层错误”。503 的合法健康 JSON 仍保留 |
| `springboot_inspect_beans` | 先 `beans`；要解释缺失时再读已启用的 `conditions` | 名称、类型或包前缀至少一个；`direction`、`maxDepth`、`conditionDepth` | `items` 的别名与双向边；`missingBeanAnalysis.blockingPaths` | 条件端点失败时保留已读到的 Bean，状态 partial。父容器不向子容器查依赖 |
| `springboot_inspect_config` | `env` 与 `configprops` | `prefix` 或 `propertyKeyPattern`；`includeValues` 默认 false | `activeProfiles`、`items[].valueState`、`origin` | 两个都失败才整单失败。敏感值和 `show-values` 掩码仍打码。activeProfiles 不从属性字符串猜测 |
| `springboot_search_openapi` | 只请求固定的 `/actuator/openapi` | `pathPattern`+方法、`operationId` 或 `schemaName`；`maxRefDepth` 默认 2、最大 8 | `items[].schema`、`referenceState`、`expansionComplete` | 只展开同文档 `$ref`。远程引用、文件引用不跟随。深度或环写入 gaps，不把截断说成解析完成 |
| `springboot_verify_mappings` | `mappings` | `pathPattern`、`httpMethod`、`controllerClassPattern`、`contextId` | `stack`（mvc/webflux）、`paths`、`httpMethods`、`descriptor` | 空 methods 表示未限制，不补 HEAD/OPTIONS。文本 predicate 不够时 `matchingComplete=false`。不请求业务 URL |

漂移和配置诊断读的是已登记的 `env`，不是契约里作为优化项提到、但连接器未实现的 `env-property`。

## 3. 不能破的安全规矩

改 `src/spring_runtime_mcp/` 时，`tests/security/test_redteam_readonly.py` 会做 AST 扫描：

- 只有 `actuator_http.py` 可以 `import http.client`。`socket`、`urllib.request`、`requests`、`httpx`、`aiohttp`、`urllib3`、`subprocess` 在生产包里都禁止。
- 生产代码不能调用 `print`、`eval`、`exec`、`compile`。
- 方法名 `post`、`put`、`patch`、`delete`、`system`、`popen` 会被当成写操作。唯一例外是 `snapshots.put`，而且只允许出现在 `inspect_beans.py` 和 `support.py`。新工具要分页，调用 `support.finalize`，不要自己 `snapshots.put`。
- 名为 `request` 的调用只能出现在 `response_gate.py` 和 `actuator_http.py`，各一处。门上的 `method` 必须是常量 `"GET"`，`follow_redirects` 必须是常量 `False`。
- 对 reader 传入 POST/PUT/PATCH/DELETE/HEAD 应在发请求前失败。

出站还有两道门：

- `SanitizerPipeline` 丢掉不在 `ALLOWED_KEYS` 里的字段。新的公开字段要同时改契约和这个白名单，否则结果会在消毒后缺键。带空格、连接串、Bearer、PEM、用户信息的字符串会变成 `[REDACTED_BY_MCP]` 或直接被拒。
- `output_guard.ensure_safe_output` 在缓存和最终帧上再查一次。带空格的句子必须在 `LOCAL_PROSE` 里，否则是 `UNTRUSTED_OUTPUT`。`message` 和 `steps` 只能是本地模板，不能拼上游正文。
- `serialize_response` 把 structuredContent 和同一对象的文本副本放进一个 JSON-RPC 帧，UTF-8 上限 **16384**。超限先减 `maxResults`，仍超则 `RESPONSE_TOO_LARGE`。不要把上限改大来让测试变绿。

其他会让红队失败的改法：跟随重定向、把用户输入写入路径或请求头、在错误信息里保留 `Bearer` 或异常 `repr`、为了通过测试删掉失败的安全断言、stdout 上写日志。

## 4. 本地接入

### 目标应用

在被诊断应用里使用 Profile `local-mcp`，不要改业务端口的对外暴露。Boot 3.4 和 4.x 的最小模板在 `docs/target-app-setup.md`：管理端口 `127.0.0.1:18081`，`access.max-permitted: read-only`，exposure 只包含 `health,beans,conditions,mappings,flyway,liquibase,startup,info,configprops,env`，并排除 `shutdown,heapdump,logfile,sessions,loggers,caches,quartz`。`env` 和 `configprops` 即使出现在 include 里，模板仍把它们设为 `access: none`；只有明确要做配置诊断时才改成 `read-only`，并保持 `show-values: never`。

启动采集要在 `SpringApplication.run` 之前设置 `BufferingApplicationStartup`。不要用 POST `/actuator/startup` 补数据。2.7 与 3.0–3.3 的属性名不同，沿用那份指南，不要把 3.4 的 `access` 键原样拿去。

本仓库的集成测试打的是回环 fixture 服务器，不是这个应用。接上真实进程后，401/403 应停在授权错误，不要关掉安全链。

### 启动本服务

仓库根目录、Python 3.11+。下面这条不安装控制台脚本，使用已缓存的 `jsonschema`：

```bash
PYTHONPATH=src uv run --offline --no-project --with jsonschema==4.25.1 \
  python -m spring_runtime_mcp.cli --base-url http://127.0.0.1:18081
```

等价环境变量：`SPRING_RUNTIME_BASE_URL`、`SPRING_RUNTIME_TARGET_ID`（默认 `local`）、`SPRING_RUNTIME_TIMEOUT`（默认 `5` 秒）。没有 `--url`。`--base-url` 只接受没有用户信息、查询串和路径的 origin。

`pyproject.toml` 登记了脚本名 `spring-runtime-mcp`。依赖已缓存时可以用 `uv run --project <仓库绝对路径> spring-runtime-mcp --base-url http://127.0.0.1:18081`。首次创建项目虚拟环境可能要访问包索引；诊断流量本身只去 `--base-url`。

`replay` 不是 MCP 服务。它读一次 fixture 就退出，不能写进客户端的 MCP 启动命令。

### 客户端

把 `<仓库绝对路径>` 换成这台机器上的检出目录。三个客户端都不要导入会自动批准工具调用的配置。

Codex，合并进 `~/.codex/config.toml`：

```toml
[mcp_servers.spring_runtime]
command = "uv"
args = ["run", "--offline", "--project", "<仓库绝对路径>", "spring-runtime-mcp", "--base-url", "http://127.0.0.1:18081"]
startup_timeout_sec = 20
tool_timeout_sec = 30
```

Claude Code，项目 `.mcp.json`：

```json
{
  "mcpServers": {
    "spring-runtime": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--offline", "--project", "<仓库绝对路径>", "spring-runtime-mcp", "--base-url", "http://127.0.0.1:18081"]
    }
  }
}
```

Cursor 的 `.cursor/mcp.json` 用同一个 `mcpServers` 对象，但不需要 `"type": "stdio"`。

进程起来后应完成 `initialize`，`tools/list` 返回按名字排序的 8 个工具。随后用一个只读调用验证，例如 beans 查询。`ENOENT` 先查 `uv` 和仓库路径。目标返回 401/403 时查应用自己的授权，不要改成 `permitAll`。
