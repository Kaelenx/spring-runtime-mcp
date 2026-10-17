# Spring Boot 安全运行时诊断 MCP Server

> **文档类型**：项目立项、产品设计与开源建设方案<br>
> **文档状态**：建议进入验证阶段<br>
> **版本**：v0.1<br>
> **调研快照**：2026-10-15<br>
> **目标**：建设一个真实解决 Spring Boot 维护与排障问题的开源项目，以可验证的实际使用和生态价值为衡量标准。

---

## 1. 执行摘要

本项目拟建设一个**本地优先、只读、安全默认、面向编码 Agent 的 Spring Boot 运行时诊断 MCP Server**。

它不负责创建 Spring Boot 项目，也不替代 Spring AI 的 MCP Server starter。它连接到一个用户自己运行、且用户有权访问的 Spring Boot 应用，通过受控的 Actuator、OpenAPI 和数据库迁移状态读取，将运行时上下文转换成结构稳定、范围明确、适合 Codex、Claude、Cursor、VS Code 等 MCP 客户端调用的工具。

目标使用场景是：开发者让编码 Agent 排查一个正在运行的 Spring Boot 应用，Agent 可以在获得授权的前提下回答：

- 某个 Bean 是否存在、由哪个类型和配置创建、依赖了什么；
- 某个 HTTP 路由是否被注册、映射到哪个 Controller；
- 应用当前是否健康，哪个数据库、Redis、Kafka 或其他依赖异常；
- Flyway 或 Liquibase 的迁移状态如何；
- 应用启动慢在哪里；
- OpenAPI 中是否存在目标接口；
- 当前运行实例与源码、配置和测试预期之间可能有什么差异。

**核心判断**：

1. Spring AI 已经提供 MCP Java SDK 和 Spring Boot MCP starters，因此项目不能把“Spring Boot 支持 MCP”本身当作差异化。
2. Spring Boot Actuator 已经提供丰富运行时端点，但这些端点默认并未全部通过 HTTP 暴露，且若直接暴露可能泄露敏感信息。
3. 现有少数 Actuator-to-MCP 项目证明了需求方向，但已有项目规模小、部分已归档或停止维护，尚未形成一个安全默认、安装简单、可供编码 Agent 使用的成熟标准工具。
4. 真正的产品价值在于：**最小权限的运行时诊断、跨版本兼容、敏感信息保护、稳定响应契约和可复现排障结果**，而不是简单把任意 HTTP URL 包成 MCP tool。

---

## 2. 项目定位

### 2.1 一句话定位

> A read-only MCP server that gives coding agents safe, structured access to a running Spring Boot application's runtime context.

中文定位：

> 一个只读且安全默认的 MCP Server，让编码 Agent 能够理解正在运行的 Spring Boot 应用。

### 2.2 要解决的核心问题

Spring Boot 开发者使用 Codex 或其他编码 Agent 时，Agent 通常只能看到：

- Git 仓库中的 Java/Kotlin 源码；
- `pom.xml` 或 `build.gradle`；
- 静态配置文件；
- 测试代码和构建日志。

但许多排障问题只存在于运行时：

- Bean 没有被创建，或者创建了错误的实现；
- 条件自动配置没有生效；
- Controller 路由没有注册，或路径与预期不同；
- 数据库迁移没有执行或执行版本不一致；
- 依赖服务健康检查失败；
- 运行实例使用了与本地配置不同的 Profile；
- 应用启动步骤缓慢；
- 实际 OpenAPI 与源码中的预期不一致。

开发者当前通常需要手工执行 `curl`、查看 JSON、复制粘贴日志，再向 Agent 解释上下文。这种方式有四个问题：

1. **效率低**：人工充当运行时信息搬运层。
2. **上下文不稳定**：不同用户提供的 Actuator JSON 范围和格式不同。
3. **安全风险高**：直接把 `/env`、`/configprops`、`/heapdump` 或日志交给模型可能泄露秘密。
4. **难以复用**：每个团队都重新编写脚本、提示词和脱敏规则。

### 2.3 产品不是做什么

本项目首期明确不做：

- 新的 Java Agent 框架；
- 新的 Spring AI 替代品；
- 通用 APM、日志平台或监控平台；
- 任意 MCP Server 代理或万能 HTTP 转 MCP 网关；
- 直接执行任意 SQL、Shell、写操作或部署操作；
- 自动修改目标 Spring Boot 应用；
- 默认收集或上传用户日志、配置和源代码；
- 依赖 OpenAI API 才能运行的黑盒诊断服务。

项目的核心诊断逻辑应尽量是确定性的。模型只负责调用工具并解释结构化结果，不能成为项目运行的必要依赖。

---

## 3. 调研依据与事实边界

### 3.1 官方能力现状

Spring AI 官方文档显示，Spring AI 已通过 Spring Boot starters 和 MCP Java Annotations 支持 MCP 客户端及服务器，支持 STDIO、SSE、Streamable HTTP 等传输方式。这意味着本项目不应重复实现 Spring AI 的 MCP Server 基础设施。

Spring Boot 官方 Actuator 文档列出了多个可用于运行时诊断的端点，包括：

- `health`
- `beans`
- `mappings`
- `metrics`
- `flyway`
- `liquibase`
- `startup`
- `conditions`
- `caches`
- `loggers`
- `env`
- `configprops`

但官方同时说明：默认通过 HTTP 暴露的通常只有 `health`，而 `env`、`configprops`、`heapdump`、`logfile`、`loggers` 等端点可能包含敏感信息，需要谨慎暴露和保护。

### 3.2 现有项目与竞品

调研发现已有相关项目，因此本项目不应宣称“没有任何竞品”：

| 项目 | 观察 | 对本项目的启示 |
| --- | --- | --- |
| [Dmitriusan/mcp-spring-boot-actuator](https://github.com/Dmitriusan/mcp-spring-boot-actuator) | 实现了 Actuator 健康、指标、Bean、环境等分析；仓库已于 2026-05-13 归档，项目页面显示 1 Star | 需求方向存在，但维护连续性和安全默认仍有机会改善 |
| [sigachev/spring-ops-mcp](https://github.com/sigachev/spring-ops-mcp) | 早期 Spring Boot Actuator MCP 项目 | 说明“运行时运维上下文进入 Agent”已有自然需求 |
| [internet-dot/spring-inspector](https://github.com/internet-dot/spring-inspector) | 将 Spring Boot MCP 与构建、测试和 Actuator 工具结合 | 需要区分运行时诊断、构建操作和写操作边界 |
| [satya-anguluri/capstead](https://github.com/satya-anguluri/capstead) | 关注 Spring Boot AI 能力的治理、预算、执行记录和 MCP 导出 | 竞品覆盖治理与 AI capability，不等同于本项目的运行时诊断桥接 |
| [Spring AI MCP 官方支持](https://docs.spring.io/spring-ai/reference/api/mcp/mcp-overview.html) | 提供 MCP Java SDK 和 Spring Boot starters | 项目应复用标准协议和客户端生态，而不是造新协议 |

因此，本项目的合理差异化应表述为：

> 不是“第一个 Spring Boot MCP Server”，而是“一个安全默认、只读、面向编码 Agent 的 Spring Boot 运行时诊断工具”。

### 3.3 相关问题信号

公开生态中可以观察到以下问题类型：

- [Spring AI #2732](https://github.com/spring-projects/spring-ai/issues/2732)：Spring AI MCP Server 的 SSE 访问问题；说明 MCP 连接和传输配置仍有实际排障成本。
- [Spring AI #4458](https://github.com/spring-projects/spring-ai/issues/4458)：自定义 Advisor 无法获取 token 消耗信息；说明 AI 应用运行时上下文和观测信息存在需求。
- [LangChain #34328](https://github.com/langchain-ai/langchain/issues/34328)：OpenRouter、LiteLLM、OpenAI-compatible Provider 之间的协议和字段差异；说明“看起来兼容”并不等于运行行为一致。
- [browser-use #4579](https://github.com/browser-use/browser-use/issues/4579)：远程 CDP 连接半失效后调用可能无限等待；说明 Agent 工具必须明确超时、健康状态和恢复边界。
- [Open WebUI #23339](https://github.com/open-webui/open-webui/issues/23339)：reasoning 内容重新注入历史消息造成上下文污染；说明运行时数据不能未经建模就直接交给模型。

这些案例不能证明每个用户都遇到同样问题，但共同说明：Agent 能否稳定工作，取决于**运行时上下文的可见性、结构化、最小权限和错误边界**。

---

## 4. 目标用户与使用场景

### 4.1 目标用户

首要用户：

- 使用 Codex、Claude Code、Cursor、VS Code Agent 等编码 Agent 的 Java/Spring Boot 开发者；
- 使用 Spring AI、LangChain4j 或自建 LLM 服务的 Spring 团队；
- 需要本地调试服务的后端开发者；
- 维护 Spring Boot starter、内部平台或微服务模板的团队；
- 需要将运行时诊断能力提供给 Agent、但不希望把完整管理权限交给 Agent 的平台工程师。

次要用户：

- MCP 客户端和工具生态维护者；
- Spring Boot 教学、示例和脚手架项目维护者；
- 负责内部开发者体验和 CI 排障的团队。

### 4.2 典型任务

#### 场景 A：Bean 注入失败

用户提示：

> 这个服务启动时报 `NoSuchBeanDefinitionException`，请检查目标 Bean 是否在运行实例中注册，以及可能的 Profile 或条件配置问题。

Agent 调用：

1. `find_bean`
2. `get_application_health`
3. `get_condition_evaluation_summary`（后续版本）

工具返回：

- 是否存在目标 Bean；
- Bean 的类型、scope 和所在上下文；
- 相关警告；
- 不能确定时明确标注原因，而不是猜测。

#### 场景 B：接口 404

用户提示：

> `POST /api/orders` 返回 404，检查运行中的应用到底注册了哪些映射。

Agent 调用：

1. `find_request_mapping`
2. `get_openapi_summary`

工具返回：

- 是否存在匹配路径；
- HTTP method；
- Controller 和处理方法；
- context path；
- 与 OpenAPI 文档的差异。

#### 场景 C：数据库迁移不一致

用户提示：

> 本地应用能启动，但测试环境报缺列，检查 Flyway/Liquibase 的当前迁移状态。

Agent 调用：

1. `get_migration_status`
2. `get_application_health`

工具返回：

- 使用的迁移框架；
- 已执行、待执行或失败的版本摘要；
- 目标实例时间和应用版本；
- 不返回数据库密码或完整连接串。

#### 场景 D：启动变慢

用户提示：

> 最近启动从 8 秒变成 45 秒，找出运行时最慢的初始化步骤。

Agent 调用：

1. `get_startup_summary`
2. `get_application_health`

工具返回：

- 总启动耗时；
- 最慢步骤；
- 相关 Bean 或自动配置摘要；
- 采集时间和数据是否完整。

---

## 5. 产品范围与版本规划

### 5.1 MVP 目标

MVP 只解决一个清晰问题：

> 让编码 Agent 能够安全地读取 Spring Boot 运行时诊断信息，并把它用于定位常见后端问题。

MVP 必须具备：

- Python CLI；
- MCP STDIO Server；
- HTTP 访问 Spring Boot Actuator；
- 本地配置和环境变量认证；
- 只读工具；
- 默认安全端点白名单；
- 响应大小上限、超时和错误分类；
- 敏感字段脱敏；
- 结构化输出；
- 单元测试、模拟 Spring Boot 服务和集成测试；
- PyPI 发布和 `uvx` 启动方式；
- Codex、Claude Desktop、Cursor 的配置示例。

### 5.2 MVP 工具清单

| 工具 | 默认状态 | 数据来源 | 作用 |
| --- | --- | --- | --- |
| `get_application_health` | 开启 | `/actuator/health` | 查看应用和依赖健康状态 |
| `find_bean` | 开启 | `/actuator/beans` | 按名称、类型或包名查找 Bean |
| `get_bean_dependencies` | 开启 | `/actuator/beans` | 查看目标 Bean 的依赖关系摘要 |
| `find_request_mapping` | 开启 | `/actuator/mappings` | 查找 HTTP method、路径和处理器 |
| `get_migration_status` | 开启 | `/actuator/flyway`、`/actuator/liquibase` | 查看数据库迁移状态 |
| `get_openapi_summary` | 可选 | `/v3/api-docs` 或配置的 OpenAPI URL | 查询接口文档摘要 |
| `get_startup_summary` | 可选 | `/actuator/startup` | 查看启动步骤和耗时 |

MVP 不提供通用的 `call_actuator_endpoint(path)` 工具。通用端点调用会绕过权限边界、使模型获得不必要的数据，也会让工具契约失去意义。

### 5.3 第二阶段工具

在 MVP 有真实用户后，再考虑：

- `get_condition_evaluation_summary`：条件自动配置摘要；
- `get_metrics_summary`：JVM、HTTP、连接池等指定指标；
- `get_cache_summary`：缓存名称和基础状态；
- `get_scheduled_tasks`：定时任务摘要；
- `compare_runtime_snapshots`：比较两个经过脱敏的运行时快照；
- `diagnose_error_context`：将用户提供的异常栈与运行时信息关联；
- `export_diagnostic_report`：生成本地 Markdown/JSON 报告。

### 5.4 明确不进入首版的功能

- 修改 logger 配置；
- `/actuator/shutdown`；
- 任意 POST、PUT、PATCH、DELETE；
- 任意 SQL 查询；
- 远程 Shell；
- 自动重启应用；
- 自动执行数据库迁移；
- 读取完整 `/env` 和 `/configprops` 值；
- 读取 `/heapdump`、`/logfile`；
- 上传云端分析；
- 让模型自行决定是否绕过安全策略。

---

## 6. 用户体验设计

### 6.1 安装

目标安装方式：

```bash
uvx spring-runtime-mcp --url http://127.0.0.1:8080
```

也支持：

```bash
pipx install spring-runtime-mcp
spring-runtime-mcp --url http://127.0.0.1:8080
```

以及开发环境：

```bash
uv add spring-runtime-mcp
```

项目不要求目标 Spring Boot 应用添加 Java 依赖即可运行。这样可以降低接入成本，也避免一开始维护完整的 Java/Python 双实现。

### 6.2 Codex 配置示例

以下示例展示目标配置形态。实际字段应在实现时根据目标 Codex 版本的 MCP 配置文档进行验证：

```toml
[mcp_servers.spring-runtime]
command = "uvx"
args = [
  "spring-runtime-mcp",
  "--url",
  "http://127.0.0.1:8080",
  "--token-env",
  "SPRING_RUNTIME_TOKEN"
]
```

认证信息只从环境变量读取：

```bash
export SPRING_RUNTIME_TOKEN="..."
```

不应把真实 token 写入仓库中的配置示例、截图或公开日志。

### 6.3 CLI 参数草案

```text
spring-runtime-mcp [OPTIONS]

Options:
  --url TEXT                       Spring Boot application base URL
  --config PATH                    Local configuration file
  --auth-mode [none|bearer|basic|header]
  --token-env TEXT                 Environment variable containing bearer token
  --username-env TEXT              Environment variable containing basic auth username
  --password-env TEXT              Environment variable containing basic auth password
  --header-name TEXT               Custom auth header name
  --header-env TEXT                Environment variable containing custom header value
  --allow-host TEXT                Additional allowed target host
  --endpoint-allow TEXT            Additional read-only logical endpoint
  --timeout FLOAT                  Per-request timeout; default 5 seconds
  --max-response-bytes INTEGER     Response body limit; default 1048576
  --insecure                       Disable TLS verification; requires explicit warning
  --log-level [error|warn|info|debug]
  --version
  --help
```

`--insecure` 不应成为普通文档中的默认示例。使用时必须输出清晰警告，并且不应改变其他安全限制。

### 6.4 MCP 工具描述原则

每个工具的描述必须明确：

- 读取的数据来源；
- 需要的 Actuator 端点；
- 是否可能包含敏感信息；
- 是否只读；
- 目标应用不可用时的返回行为；
- 结果的观察时间；
- 数据是否被截断、脱敏或部分失败。

模型不应通过工具描述被暗示为“可以执行修复”。工具只提供事实和诊断证据。

---

## 7. 技术架构

### 7.1 总体架构

```text
┌────────────────────────┐
│ Codex / Claude / Cursor│
│        MCP Client      │
└───────────┬────────────┘
            │ MCP over STDIO
┌───────────▼────────────┐
│ spring-runtime-mcp     │
│ Python local process   │
│                        │
│  MCP tool layer        │
│  Policy / allowlist    │
│  Auth provider         │
│  HTTP client           │
│  Redaction             │
│  Response normalizer   │
│  Diagnostic analyzers  │
└───────────┬────────────┘
            │ HTTPS / HTTP
┌───────────▼────────────┐
│ Running Spring Boot    │
│                        │
│ Actuator               │
│ OpenAPI (optional)     │
│ Flyway / Liquibase     │
└────────────────────────┘
```

### 7.2 设计原则

1. **本地优先**：默认在开发者机器上运行，不上传数据。
2. **只读优先**：首版只允许 GET/HEAD 语义的诊断读取。
3. **最小权限**：工具层白名单优先，禁止任意路径透传。
4. **确定性核心**：解析、过滤、脱敏、超时和错误分类不依赖 LLM。
5. **协议标准化**：使用官方 MCP Python SDK，不自定义 MCP 方言。
6. **原始数据与摘要分离**：默认返回结构化摘要，原始响应仅在明确配置并满足限制时使用。
7. **失败可解释**：区分认证失败、端点未暴露、目标不可达、响应无效、响应过大和超时。
8. **版本兼容显式化**：不假设所有 Spring Boot 版本的 Actuator JSON 形状完全相同。

### 7.3 推荐技术栈

| 层 | 技术 | 选择理由 |
| --- | --- | --- |
| 语言 | Python 3.11+ | 适合 CLI、PyPI 和 MCP Python SDK，减少目标应用侵入 |
| MCP | 官方 MCP Python SDK | 复用标准协议和客户端生态 |
| CLI | Typer 或 Click | 参数、帮助和错误输出清晰 |
| HTTP | httpx | 同步/异步、超时、连接池和 TLS 控制完整 |
| Schema | Pydantic | 工具输入、配置和结构化输出可验证 |
| 配置 | Pydantic Settings + TOML/YAML 可选 | 环境变量优先，便于本地和 CI 使用 |
| 测试 | pytest、pytest-asyncio、respx 或 MockTransport | 可构造确定性 HTTP fixture |
| 静态检查 | Ruff、Pyright 或 mypy | 保持 CLI 和协议层可维护 |
| 构建 | uv、PEP 517/518 | 快速开发、锁定依赖和发布 |
| 文档 | Markdown + MkDocs Material 可选 | 便于 GitHub 和文档站发布 |
| 发布 | PyPI Trusted Publishing | 避免长期保存发布 token |

核心包不需要 `openai` 依赖，也不需要任何模型 API key。

### 7.4 项目结构草案

```text
spring-runtime-mcp/
├── src/spring_runtime_mcp/
│   ├── __init__.py
│   ├── cli.py
│   ├── server.py
│   ├── config.py
│   ├── errors.py
│   ├── models/
│   │   ├── common.py
│   │   ├── health.py
│   │   ├── beans.py
│   │   ├── mappings.py
│   │   ├── migrations.py
│   │   └── startup.py
│   ├── transport/
│   │   └── actuator_client.py
│   ├── policy/
│   │   ├── allowlist.py
│   │   ├── host_policy.py
│   │   └── redaction.py
│   ├── analyzers/
│   │   ├── health.py
│   │   ├── beans.py
│   │   ├── mappings.py
│   │   ├── migrations.py
│   │   └── startup.py
│   └── tools/
│       ├── health.py
│       ├── beans.py
│       ├── mappings.py
│       ├── migrations.py
│       ├── openapi.py
│       └── startup.py
├── tests/
│   ├── unit/
│   ├── fixtures/
│   │   ├── boot-2/
│   │   ├── boot-3/
│   │   └── malformed/
│   ├── integration/
│   └── security/
├── examples/
│   ├── spring-boot-demo/
│   ├── codex-config.toml
│   └── redaction-config.toml
├── docs/
│   ├── security-model.md
│   ├── compatibility.md
│   ├── troubleshooting.md
│   └── contributing.md
├── .github/
│   ├── workflows/ci.yml
│   ├── workflows/release.yml
│   ├── ISSUE_TEMPLATE/
│   └── pull_request_template.md
├── LICENSE
├── README.md
├── SECURITY.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── CHANGELOG.md
├── pyproject.toml
└── uv.lock
```

### 7.5 为什么首版选择外部 Python Bridge

首版不做 Spring Boot starter，原因如下：

- 不需要用户修改目标 Java 项目；
- 不需要同时覆盖 Spring Boot、Spring AI、WebMVC、WebFlux 和 Java 版本组合；
- 可以快速支持没有 Spring AI 的普通 Spring Boot 应用；
- 可以独立发布到 PyPI，通过 `uvx` 接入 MCP 客户端；
- 安全策略和敏感信息处理集中在一个边界；
- 更容易做本地 fixture 和跨版本 HTTP 测试。

未来只有在真实用户证明以下需求后，才考虑 Java starter：

- 需要进程内访问 `ApplicationContext`，而 Actuator 信息不足；
- 需要自定义 Bean 元数据或内部诊断；
- 需要无需额外 HTTP 暴露的本地连接方式；
- 有足够维护者共同承担 Java 版本矩阵。

---

## 8. 数据契约设计

### 8.1 通用响应结构

所有工具尽量返回统一的结构：

```json
{
  "status": "ok",
  "source": {
    "target": "http://127.0.0.1:8080",
    "endpoint": "health",
    "observed_at": "2026-10-15T12:00:00Z"
  },
  "data": {},
  "warnings": [],
  "redactions": [],
  "truncated": false
}
```

`status` 建议包括：

- `ok`：完整读取并解析；
- `partial`：部分字段缺失、被脱敏或某个可选端点不可用；
- `unavailable`：目标应用不可达或端点未暴露；
- `denied`：认证或安全策略拒绝；
- `invalid`：目标响应格式无法识别；
- `too_large`：响应超过配置限制。

### 8.2 错误结构

错误不能只返回 Python exception 文本，应返回可供 Agent 理解且不泄露凭据的结构：

```json
{
  "status": "unavailable",
  "error": {
    "code": "TARGET_TIMEOUT",
    "message": "The target application did not respond within the configured timeout.",
    "retryable": true,
    "next_action": "Check that the application is running and the management URL is reachable."
  },
  "source": {
    "target": "http://127.0.0.1:8080",
    "endpoint": "beans"
  }
}
```

禁止在错误信息中输出：

- `Authorization` header；
- Basic Auth 用户名和密码；
-完整连接字符串；
- API key；
- 完整环境变量值；
- 未脱敏的 URL 查询参数。

### 8.3 Bean 结果结构

```json
{
  "status": "ok",
  "data": {
    "matches": [
      {
        "name": "orderService",
        "type": "com.example.order.OrderService",
        "scope": "singleton",
        "dependencies": ["orderRepository", "paymentClient"],
        "context": "application",
        "source": "actuator.beans"
      }
    ],
    "total_matches": 1,
    "query": {
      "name": "orderService",
      "type": null,
      "package": null
    }
  },
  "warnings": []
}
```

默认不返回完整 Bean JSON，也不返回可能包含实例状态、内部字段或配置值的无关内容。

### 8.4 路由结果结构

```json
{
  "status": "ok",
  "data": {
    "matches": [
      {
        "path": "/api/orders/{id}",
        "methods": ["GET"],
        "handler": "com.example.order.OrderController#get",
        "patterns": ["/api/orders/{id}"],
        "source": "actuator.mappings"
      }
    ],
    "total_matches": 1
  },
  "warnings": []
}
```

### 8.5 脱敏结果结构

如果字段被脱敏，应该告知用户发生了脱敏，但不透露原值：

```json
{
  "redactions": [
    {
      "path": "data.components.database.details.url",
      "reason": "credential_or_connection_string"
    }
  ]
}
```

---

## 9. 安全模型

安全模型是项目的核心产品能力，不是附加文档。

### 9.1 默认安全策略

默认配置建议：

```yaml
target:
  url: http://127.0.0.1:8080
  allowed_hosts:
    - 127.0.0.1
    - localhost

endpoints:
  allow:
    - health
    - beans
    - mappings
    - flyway
    - liquibase
  deny:
    - env
    - configprops
    - heapdump
    - logfile
    - shutdown
    - loggers

security:
  redact_values: true
  timeout_seconds: 5
  max_response_bytes: 1048576
  follow_redirects: false
  verify_tls: true
```

### 9.2 威胁模型

| 威胁 | 可能后果 | 缓解措施 |
| --- | --- | --- |
| MCP 客户端被诱导访问任意 URL | SSRF、内网探测 | 默认仅允许 localhost；显式 host allowlist |
| Actuator 返回密码或 token | 敏感信息泄露给模型 | 端点白名单、字段脱敏、大小限制 |
| 目标返回 302 到外部地址 | 绕过 host 限制 | 默认不跟随重定向，或逐跳校验 |
| 模型请求危险工具 | 修改生产配置或关闭服务 | 首版只读；不注册写操作 |
| token 被写入日志 | 凭据泄露 | Header 过滤、异常过滤、测试 secret scan |
| 恶意响应注入模型 | Prompt injection | 将目标响应作为不可信数据标记；清晰字段边界；不执行响应中的指令 |
| 超大 `/beans` 或 `/mappings` | 内存和上下文耗尽 | 响应体限制、分页/过滤、摘要优先 |
| TLS 被关闭 | 中间人攻击 | 默认验证；`--insecure` 需要显式开启并警告 |
| 重试造成重复副作用 | 业务状态重复变更 | 首版不允许写操作 |
| 错误详情泄露内部结构 | 架构和凭据暴露 | 分级错误、脱敏和最小上下文 |

### 9.3 端点暴露策略

不允许通过一个通用工具让模型传入任意路径：

```text
不推荐：call_endpoint(path="/actuator/env")
```

推荐使用固定工具和固定解析器：

```text
推荐：find_bean(name="orderService")
推荐：find_request_mapping(path="/api/orders")
```

如果未来确实需要高级用户读取额外端点，也应通过本地配置显式启用，并且：

- 仅允许预注册的端点 ID；
- 每个端点声明响应分类和敏感级别；
- 仍然执行脱敏、超时和大小限制；
- 在工具结果中标记该端点是高级配置；
- 不支持任意 HTTP method。

### 9.4 数据保留策略

默认：

- 不写入远程服务；
- 不上传诊断数据；
- 不持久化完整响应；
- 不记录完整请求 body；
- 日志中只记录 endpoint ID、状态、耗时和错误代码；
- 用户显式开启 debug 时，也必须先进行 header 和字段脱敏。

---

## 10. 兼容性策略

### 10.1 目标范围

MVP 目标：

- Spring Boot 2.7；
- Spring Boot 3.x；
- Servlet/MVC 和 WebFlux 产生的常见 Actuator JSON；
- 有无 Spring AI；
- 有无 Springdoc OpenAPI；
- Flyway 或 Liquibase 二选一或同时存在；
- HTTP Basic、Bearer Token 和自定义 Header。

Spring Boot 4 或未来版本作为实验性兼容目标，不能在首版 README 中承诺完整支持，除非有真实集成测试。

### 10.2 版本兼容原则

- 解析器不依赖固定字段顺序；
- 缺少可选字段时返回 `partial`，不直接崩溃；
- 解析失败时保留安全的字段级错误，不原样把响应送给模型；
- 每个版本差异放入兼容层，而不是散落在工具实现中；
- 用真实 Spring Boot Demo 生成 fixture，而不是只手写 JSON；
- README 明确列出测试过的 Spring Boot、Java、Actuator 和 MCP SDK 版本。

### 10.3 测试矩阵

| 维度 | 最小矩阵 |
| --- | --- |
| Python | 3.11、3.12、3.13 |
| Java | 17、21 |
| Spring Boot | 2.7、3.2、3.3/3.4 或当前维护线 |
| Web stack | MVC、WebFlux |
| Auth | none、Basic、Bearer、custom header |
| Migration | Flyway、Liquibase、none |
| MCP client | MCP protocol fixture；至少一个实际 Codex/桌面客户端验证 |
| 响应状态 | 200、401、403、404、429、500、超时、非法 JSON、超大响应 |

---

## 11. 测试与质量门槛

### 11.1 单元测试

必须覆盖：

- URL 规范化；
- host allowlist；
- 重定向拒绝；
- endpoint allowlist；
- Bearer/Basic/custom header 注入；
- timeout 和连接错误；
- HTTP 状态到错误代码的映射；
- Actuator JSON 解析；
- Boot 2/3 fixture 差异；
- 字段脱敏；
- 响应体大小限制；
- 不把 secret 写入异常和日志；
- 工具参数校验；
- 空结果、部分结果和 malformed response。

### 11.2 集成测试

建立一个最小 Spring Boot 示例项目，包含：

- 一个健康检查；
- 两个 Service Bean；
- 一个 Controller；
- 一个数据库连接模拟或 Testcontainers 可选配置；
- Flyway 或 Liquibase 示例；
- OpenAPI 示例；
- Actuator 安全配置。

集成测试验证：

1. 应用启动；
2. Python MCP Server 可以连接；
3. 每个工具返回预期结构；
4. 未暴露端点能被清晰识别；
5. 错误和凭据不会泄露；
6. 目标服务停止后 MCP Server 仍能返回可解释错误，而不是挂死。

### 11.3 安全测试

至少包括：

- SSRF host bypass；
- IPv4/IPv6 localhost 处理；
- 重定向到内网和外部地址；
- Basic Auth 中冒号、Unicode 和特殊字符；
- token 出现在 URL、异常、日志和 JSON 中；
- JSON 嵌套字段脱敏；
- 超大响应和重复嵌套 JSON；
- 恶意字段名和异常 Unicode；
- Actuator 响应中嵌入类似模型指令的文本；
- 工具不能执行非 GET 操作。

### 11.4 发布门槛

每个版本发布前必须通过：

- CI 全部测试；
- Ruff 和类型检查；
- 依赖漏洞扫描；
- secret scan；
- 构建 sdist 和 wheel；
- TestPyPI 安装验证；
- 从干净环境使用 `uvx` 启动；
- 最小 Spring Boot demo 端到端验证；
- CHANGELOG 和迁移说明更新。

---

## 12. 开发路线图

### 阶段 0：需求验证（1–2 周）

目标：确认痛点不是凭空推断。

任务：

- 访谈至少 8–10 位 Java/Spring Boot 开发者或维护者；
- 收集至少 20 个真实排障任务，记录是否需要运行时信息；
- 选择 5 个公开 Spring Boot 项目做兼容性实验；
- 在本地构造 health、beans、mappings、migration fixture；
- 与现有项目比较安装、权限和输出差异；
- 让至少 3 位开发者看一份脱敏诊断结果并给出反馈。

退出条件：

- 至少 5 个真实任务中，2 个以上可由运行时工具明显减少人工步骤；
- 至少 3 位受访者愿意试用；
- 首批工具范围固定，不继续扩展成通用 APM。

### 阶段 1：MVP 核心（2–3 周）

任务：

- 创建 Python 包和 CLI；
- 接入 MCP Python SDK；
- 实现目标 URL、认证和 HTTP client；
- 实现 `get_application_health`；
- 实现 `find_bean` 和 `get_bean_dependencies`；
- 实现 `find_request_mapping`；
- 建立统一响应和错误模型；
- 添加本地 mock server 和 fixture。

退出条件：

- 新用户可以在 10 分钟内启动 demo；
- 所有默认工具不执行写操作；
- 失败场景均有稳定错误代码；
- 没有敏感 header 出现在日志和返回值中。

### 阶段 2：诊断覆盖和安全加固（2–3 周）

任务：

- 加入 `get_migration_status`；
- 加入 OpenAPI 可选适配器；
- 加入 `get_startup_summary`；
- 完成端点 allowlist、host allowlist 和重定向限制；
- 完成脱敏规则和安全测试；
- 完成 Boot 2.7/3.x fixture；
- 编写安全模型和威胁模型文档。

退出条件：

- 至少 30 个安全和兼容性测试；
- 至少 5 个真实或公开 demo 项目验证；
- 明确列出不支持的端点和版本；
- 安全审查没有高危未解释项。

### 阶段 3：公开发布（1 周）

任务：

- 发布 GitHub 仓库；
- 发布 Apache-2.0 许可证版本；
- 发布到 TestPyPI，再发布 PyPI；
- 发布 GitHub Release 和 CHANGELOG；
- 提供 Codex、Claude Desktop、Cursor、VS Code 示例；
- 发布最小可运行 demo；
- 公开兼容性矩阵和安全边界。

退出条件：

- 干净机器可用 `uvx` 启动；
- README 的首个示例在 5–10 分钟内可运行；
- Issue 模板可以收集版本、端点、脱敏日志和复现步骤。

### 阶段 4：外部采用（4–8 周）

任务：

- 在 Spring AI Discussions 发布技术说明；
- 在 Stack Overflow 回答真实的 Actuator/MCP/Spring AI 排障问题；
- 邀请 5 个真实项目试用；
- 收集外部 Issue、PR 和适配器贡献；
- 每两周发布一个小版本或明确维护更新；
- 建立公开案例和兼容性测试贡献流程。

目标不是刷下载量，而是形成：

- 外部项目试用；
- 可复现的诊断案例；
- 外部 Issue 和 PR；
- 真实维护工作流中的使用记录。

---

## 13. 发布渠道与社区进入方式

### 13.1 核心渠道

1. **GitHub**：源码、Issue、讨论、Release、兼容性记录。
2. **PyPI**：标准 Python 安装和版本分发。
3. **uvx/pipx**：面向 MCP 客户端的无污染启动方式。
4. **GitHub Pages 或 MkDocs**：安全模型、配置和故障排查文档。
5. **MCP 生态目录**：在项目成熟后提交，而不是首日只追求目录收录。
6. **Spring AI 社区**：分享技术实现、兼容性案例和安全边界。
7. **Stack Overflow**：用真实问题回答引导工具，而不是无关推广。

### 13.2 推广原则

不要向大型项目的 Issue 中批量发布推广评论。应当：

- 先验证工具确实复现了公开问题；
- 在允许的 Discussion 或 Show and Tell 渠道分享；
- 给出最小复现、限制和安全边界；
- 不声称得到上游项目认可；
- 不将一次下载或一个点赞当作采用证据；
- 对真实用户反馈建立公开 Issue 或 Discussion。

### 13.3 README 首屏必须包含

- 一句话定位；
- 30 秒安装命令；
- Codex 配置示例；
- 一个 `find_bean` 或 `find_request_mapping` 实例；
- 默认只读和本地优先说明；
- 支持和不支持的 Actuator 端点；
- 安全警告；
- 测试状态和兼容性矩阵；
- 如何报告安全问题。

---

## 14. 开源治理与仓库质量

初始仓库应至少包含：

- `README.md`；
- `LICENSE`；
- `CONTRIBUTING.md`；
- `CODE_OF_CONDUCT.md`；
- `SECURITY.md`；
- `CHANGELOG.md`；
- GitHub Issue 模板；
- Pull Request 模板；
- CI；
- 发布 workflow；
- 依赖更新策略；
- 兼容性矩阵；
- 安全模型；
- 端点行为文档；
- 公开 roadmap。

### 14.1 许可证建议

建议初始采用 Apache License 2.0：

- 与 Java/Spring 企业生态的接受度较好；
- 包含明确的专利授权条款；
- 适合被公司内部工具和平台集成；
- 与后续 Maven/Java 生态扩展更协调。

许可证一旦发布，应在项目稳定后避免随意变更。

### 14.2 版本策略

- `0.x`：允许非破坏性调整，但必须维护 CHANGELOG；
- `1.0`：稳定工具名称、输入输出结构和安全策略；
- 工具重命名或字段删除需要迁移说明；
- 支持的 Spring Boot 版本应在每个 release 标注；
- MCP SDK 升级需要运行客户端兼容性测试。

### 14.3 Issue 标签建议

```text
bug
security
compatibility
spring-boot-2
spring-boot-3
webmvc
webflux
actuator
openapi
flyway
liquibase
mcp-client
documentation
good-first-issue
needs-reproduction
```

---

## 15. 成功指标

### 15.1 早期验证指标

| 指标 | 目标 |
| --- | ---: |
| 访谈人数 | >= 8 |
| 收集的真实排障任务 | >= 20 |
| 认为工具有用的受访者 | >= 3 |
| 可复现的运行时问题 | >= 5 |
| 支持的公开 Spring Boot demo | >= 5 |

### 15.2 发布后指标

| 指标 | 目标或观察方式 |
| --- | --- |
| PyPI 下载 | 仅作辅助趋势，不作为唯一目标 |
| 外部试用项目 | >= 5 个可核验项目 |
| 外部 Issue | 有明确版本、环境和复现信息 |
| 外部 PR | 至少有不属于作者的贡献 |
| 兼容性案例 | 每个主要 Spring Boot 版本至少一个 |
| 安全修复 | 有公开处理流程和时间记录 |
| 诊断报告 | 用户愿意在授权后公开脱敏案例 |
| 维护连续性 | 每月至少一次有效发布、修复或兼容性更新 |

### 15.3 质量指标

- 默认工具全部只读；
- 高危安全测试为零未解释失败；
- 目标不可达时不会挂死 MCP 进程；
- 默认响应不包含凭据；
- 重要错误都能映射为稳定错误代码；
- 端到端 demo 可在新环境运行；
- 版本升级不破坏已公开的工具响应契约，除非有迁移说明。

---

## 16. 风险与应对

| 风险 | 可能性 | 影响 | 应对 |
| --- | --- | --- | --- |
| Spring Boot 已有官方 MCP 支持 | 高 | 高 | 不做 MCP 基础设施，聚焦安全诊断和运行时语义 |
| Actuator 数据过于敏感 | 高 | 高 | 白名单、只读、脱敏、大小限制、明确威胁模型 |
| Actuator JSON 跨版本差异 | 高 | 中 | fixture、兼容层、`partial` 状态和版本矩阵 |
| Python 不符合 Java 用户习惯 | 中 | 中 | `uvx` 一键运行，后续根据需求提供 starter |
| 目标项目没有暴露 Actuator | 高 | 中 | 清晰返回未暴露提示；提供安全配置文档，不自动修改目标应用 |
| MCP 客户端配置频繁变化 | 中 | 中 | 提供多个客户端示例，锁定协议测试，不绑定单一客户端 |
| 工具与 APM/Observability 重叠 | 中 | 中 | 聚焦 Agent 可调用的诊断契约，不做长期指标存储和云平台 |
| 项目变成通用 HTTP 代理 | 中 | 高 | 禁止任意路径工具，固定工具和端点解析器 |
| 只有作者一人维护 | 高 | 高 | 尽早公开 fixture、Issue 模板、贡献指南和适配器边界 |

---

## 17. 进入和退出决策门槛

### 17.1 进入 MVP 开发

必须满足：

- 至少 8 位目标用户完成访谈；
- 至少 20 个真实排障任务被记录；
- 至少 5 个任务需要运行时信息；
- 至少 3 位用户愿意试用；
- 至少 2 个任务可以用固定工具显著减少人工复制步骤；
- 安全模型得到至少 1 位安全或平台工程师评审；
- 能明确说出本项目与 Spring AI MCP starter、APM、通用 MCP proxy 的区别。

### 17.2 进入公开发布

必须满足：

- 6 个 MVP 工具中至少 4 个在真实 demo 中可用；
- 目标应用停止、认证失败、端点未暴露和响应异常均有稳定处理；
- 至少 30 项安全/兼容性测试通过；
- 包可以从干净环境安装；
- README 首个示例可运行；
- 不存在已知的高危凭据泄露问题。

### 17.3 暂停或转向

出现以下情况时，应暂停扩展功能并重新评估：

- 访谈中多数用户认为直接用 `curl` 已经足够；
- 用户只关心写操作或部署，而项目坚持只读；
- 无法在没有真实凭据的情况下构造测试；
- 目标 Actuator 结构差异大到无法形成稳定契约；
- 现有维护良好的项目已覆盖相同安全和用户体验；
- 连续 6–8 周没有外部试用、Issue 或贡献者；
- 项目主要使用者变成作者自己，无法证明生态价值。

---

## 18. 首批 GitHub Issue 规划

初始化仓库后，建议建立以下 Issue，而不是直接创建大量功能：

1. `RFC: project scope and security boundary`
2. `Implement MCP stdio server skeleton`
3. `Implement Actuator HTTP client with timeout and size limits`
4. `Implement host allowlist and redirect policy`
5. `Implement get_application_health`
6. `Implement find_bean with Boot 2/3 fixtures`
7. `Implement find_request_mapping`
8. `Implement Flyway and Liquibase adapters`
9. `Implement OpenAPI summary adapter`
10. `Add secret redaction test suite`
11. `Add Spring Boot MVC demo`
12. `Add Spring Boot WebFlux demo`
13. `Document Codex MCP configuration`
14. `Publish TestPyPI package`
15. `Security review: SSRF, auth, and response handling`

每个 Issue 都应写清：

- 用户问题；
- 非目标；
- 输入和输出契约；
- 测试要求；
- 安全影响；
- 是否需要真实 Spring Boot 集成测试。

---

## 19. 推荐的第一周行动清单

### Day 1：确认定位

- 创建项目名和临时 GitHub 仓库；
- 写出一页 README 草案；
- 明确“只读、安全、本地优先”的边界；
- 不开始实现通用 Actuator 代理。

### Day 2：建立测试样本

- 创建 Spring Boot MVC demo；
- 创建 `/actuator/health`、`/actuator/beans`、`/actuator/mappings` fixture；
- 配置一个受保护端点和一个未暴露端点；
- 记录预期 MCP 工具响应。

### Day 3：用户访谈

- 联系 Java/Spring 开发者；
- 询问最近一次 Bean、路由、迁移或启动问题；
- 记录他们如何获取运行时信息；
- 不先推销项目，先确认工作流。

### Day 4：最小 CLI 原型

- 实现目标 URL 配置；
- 实现 health 请求；
- 实现超时和错误分类；
- 实现一个 MCP tool；
- 使用本地 fixture 运行。

### Day 5：安全边界

- 加入 host allowlist；
- 加入响应大小限制；
- 加入 header 脱敏；
- 加入敏感字段 fixture；
- 写第一版 `SECURITY.md`。

### 第一周结束判断

如果一周结束时仍无法让一个开发者通过工具更快定位一个真实运行时问题，就不应继续添加更多工具，而应回到需求验证。

---

## 20. 项目名称候选

名称应避免直接使用 Spring 官方商标造成官方背书误解，也应清楚表达运行时诊断用途。

候选：

- `spring-runtime-mcp`
- `spring-runtime-inspector`
- `spring-boot-runtime-mcp`
- `boot-context-mcp`
- `spring-diagnostics-mcp`

推荐暂用：

> `spring-runtime-mcp`

理由：

- 直观表达目标生态和协议；
- 便于 PyPI、GitHub 和 MCP 目录理解；
- 不把项目限制为 Actuator 分析；
- 后续可以扩展 OpenAPI、迁移状态和运行时报告。

在正式发布前需要确认 PyPI、GitHub、MCP 生态目录和相关商标使用情况。

---

## 21. 参考资料

### 官方文档

- [Spring AI MCP Overview](https://docs.spring.io/spring-ai/reference/api/mcp/mcp-overview.html)
- [Spring AI Getting Started with MCP](https://docs.spring.io/spring-ai/reference/guides/getting-started-mcp.html)
- [Spring Boot Actuator Endpoints](https://docs.spring.io/spring-boot/reference/actuator/endpoints.html)
- [Model Context Protocol Java SDK](https://github.com/modelcontextprotocol/java-sdk)
- [OpenAI Agents SDK Usage](https://openai.github.io/openai-agents-python/usage/)
- [OpenAI Agents SDK Guardrails](https://openai.github.io/openai-agents-python/guardrails/)

### 相关项目

- [Dmitriusan/mcp-spring-boot-actuator](https://github.com/Dmitriusan/mcp-spring-boot-actuator)
- [sigachev/spring-ops-mcp](https://github.com/sigachev/spring-ops-mcp)
- [internet-dot/spring-inspector](https://github.com/internet-dot/spring-inspector)
- [satya-anguluri/capstead](https://github.com/satya-anguluri/capstead)
- [Spring AI](https://github.com/spring-projects/spring-ai)
- [Spring Boot](https://github.com/spring-projects/spring-boot)

### 公开问题样本

- [Spring AI #2732](https://github.com/spring-projects/spring-ai/issues/2732)
- [Spring AI #4458](https://github.com/spring-projects/spring-ai/issues/4458)
- [LangChain #34328](https://github.com/langchain-ai/langchain/issues/34328)
- [browser-use #4579](https://github.com/browser-use/browser-use/issues/4579)
- [Open WebUI #23339](https://github.com/open-webui/open-webui/issues/23339)
- [Mem0 #4956](https://github.com/mem0ai/mem0/issues/4956)

---

## 22. 最终决策

**推荐推进，但先进入需求验证阶段，不立即承诺完整产品。**

优先选择这个方向的理由：

1. 目标生态清晰，Spring Boot 用户和项目边界明确；
2. 运行时诊断是编码 Agent 的实际缺口，而不是单纯增加一个聊天入口；
3. 现有官方能力可以复用，不需要从零实现 MCP 或 Spring Boot 运行时；
4. 安全、兼容性和结构化输出形成了真实技术壁垒；
5. Python CLI + PyPI + `uvx` 可以低侵入地验证需求；
6. 后续能通过 GitHub Issue、PR、兼容性 fixture、真实集成和安全修复积累维护者证据。

**最重要的执行原则**：

> 持续维护一个 Spring 开发者真的愿意在排障时安装和依赖的工具。
