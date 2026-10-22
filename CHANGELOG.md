# 版本记录

## 未发布 — 2026-10-22 综合审查修复

- 关闭 [综合审查](docs/audits/comprehensive-review-2026-10-22.md)的 CR-01 至 CR-13；逐项证据见[修复验收报告](docs/audits/comprehensive-remediation-2026-10-22.md)。超长请求 ID 在 SDK 前拒绝并以 null ID 结束会话；真实 SDK 工具出口再次检查 16 KiB 上限。
- 下沉共享 `Services` 与安全诊断信封，八工具共用每目标两个读取槽和一个 15 秒 deadline；排队、读取、协作分析及输出检查使用同一预算。
- 修复 OpenAPI 组合/字典 Schema、本地 PathItem/Response 引用及 3.0/3.1 `$ref` 语义；未知 Schema 语义明确 partial。修复迁移缺证据推导、checksum 可比性、Bean 类型查询的父子遮蔽、成功迁移状态及隐藏 UP 组件的可见性结论。
- STDIO 进程测试按回复完成再关闭输入，并使用有时限的管道读取。CI/发布复用校验工作流，发布依赖全部门禁且校验版本标签；没有执行发布。
- 增加 `uv.lock`、固定开发工具版本及 wheel 的 `py.typed`。隔离安装通过官方 SDK 2.3.0 客户端的 legacy / 2026-07-28 发现及工具错误检查。
- 增加本地受信 `--token-env` Bearer 绑定，限制令牌格式并纳入已知秘密脱敏；不增加任意头透传或 Basic 支持，不改变工具公开 Schema、运行依赖或回环边界。
- 明确批准后，针对 `<TDD checkout>` 的已提交 S8 副本完成 Boot 3.5.16 / Java 21 / MVC / JWT / 临时 PostgreSQL 16 验收。TDD 原工作区保留未完成业务代码，只新增已批准的可选 Actuator Maven profile。六个诊断端点均只读，匿名拒绝，环境和配置值端点关闭；结果见 [Shop 摘要](examples/tdd-runtime/verification.json)。这不代表全版本矩阵或 TDD 当前红阶段业务已完成。
- 离线全量 **782 passed**，mypy 51 文件、Ruff、八工具契约、wheel/sdist 构建及安装验证通过。
- 交付审计追加修复：drift 在 startup 证据缺失时返回显式 `STARTUP_EVIDENCE_UNAVAILABLE` 而不是 `ok`；mappings 对不可读 patterns/methods 标记 partial 并禁止伪造 unrestricted；Bean 缺失类型/scope、health details/startup tags 截断、迁移预算耗尽及 date-time 输出契约均显式标注；配置诊断新增 `--config-prefix/--config-property/--config-profiles/--config-allow-values`；replay fixtures 随包分发，安装 wheel 后可重放。修复与验证证据见 [交付审计修复与验证](docs/audits/delivery-audit-remediation-2026-10-22.md)。

## v0.2.0 — 2026-10-22

- 关闭 2026-10-18 逻辑与边界审计的 TC-BUG-01 至 15；连接器增加数字地址固定与 peer 校验，安全静态清单仅对 `transport/actuator_http.py` 放行 `socket`/`http.client` 导入并新增禁止原始套接字写入。全量 730 passed。
- 工具契约 JSON 移至 `src/spring_runtime_mcp/schemas/`，通过 `importlib.resources` 加载，随 wheel 分发。
- 增加 hatchling 构建后端、Apache-2.0 许可证、英文 README、`SECURITY.md` 与 GitHub Actions CI（Python 3.11–3.14 测试、mypy/ruff、wheel 构建及干净环境 stdio 握手）。`uv build` 产物已在隔离虚拟环境安装并完成 `initialize`/`tools/list`。
- STDIO 宿主迁移到官方 `mcp` Python SDK 2.x 的 lowlevel `Server`，新增运行依赖 `mcp>=2.3,<3`。握手协商、初始化门禁、JSON-RPC 错误映射与取消由 SDK 负责；工具描述符、outputSchema 校验、脱敏与 16 KiB 预算是本项目逻辑。
- 协议行为变化，客户端可能感知：
  - `initialize` 之前只接受 `ping`；提前的 `tools/*` 返回 JSON-RPC `-32602`。
  - 未知工具返回 `-32602`（此前为 `-32600` 或失败信封）。
  - 不支持握手版本（如 `2026-07-28`）协商到 SDK 的最新 handshake 版本 `2025-11-25`，不回显请求版本。
  - 传输层无法解析的帧（非法 JSON、`jsonrpc` 非 `"2.0"`、非字符串/布尔/浮点 id、`params` 非对象）被直接丢弃而不回应；原有“不触达处理器、零 IO”的安全性质由 SDK 解析边界保证，对应测试改为断言该性质。
  - 16 KiB 预算现在包含 2026-era 响应的 `io.modelcontextprotocol/serverInfo` `_meta` 戳。
- 安全静态清单把 `mcp`/`mcp_types` 的导入范围限制在 `server.py` 与 `cli.py`；`transport/stdio.py` 仍只服务重放 CLI。
- 真实应用验证：新增 `examples/spring-boot-demo`（Spring Boot 3.4.5 / Java 21、H2 + Flyway、独立只读管理端口、`BufferingApplicationStartup`）与 `scripts/check_demo_app.py`；提交的 `transcript.jsonl` 覆盖 initialize、tools/list 及六个读工具。
- 修复真实 `/actuator/beans` 互操作缺陷：Boot 仅对子上下文输出 `parentId`，缺省应视为根上下文而不是未知父容器；此前所有真实单上下文应用都会被判为 partial 且缺失 Bean 分析无法给出阻塞链。对应回归见 `tests/unit/test_bean_graph.py` 与 `test_rw06_architecture_regressions.py`。
- 验证计数更新为 730 passed；真实应用验证不属于默认离线门禁，需先启动演示应用且只访问回环管理端口。

## v0.1.0 — 2026-10-17

首次源码封板，版本号与 `pyproject.toml`、MCP `serverInfo.version` 一致。本记录描述本地 Git 提交范围，不代表包发布、部署或生产验收。

### 交付范围

- 八个按名称排序注册的诊断工具：Bean/条件、路由、健康、迁移、启动、配置、OpenAPI 与漂移。
- Bean 别名和父子容器依赖图、嵌套条件三值归因、同文档 OpenAPI 引用展开，以及缺证据时的 partial/unknown 结果。
- 自实现 STDIO JSON-RPC 宿主、启动时登记 origin 的标准库 HTTP 连接器和固定场景离线重放 CLI。
- 固定 GET 端点、不跟随重定向、字段投影和双层脱敏、查询绑定游标，以及包含 structuredContent 与文本副本的 16,384 字节完整回包预算。
- 八工具 JSON 契约、合成多版本 fixtures、离线/回环集成及安全回归，目标配置与客户端接入文档。

### 封板验证

在仓库根目录实际执行，全部使用缓存依赖；未连接真实 Spring Boot 或生产目标。HTTP 集成仅使用测试自行启动的回环 fixture 服务。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 全量测试 | `uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q` | 639 passed，22.35 秒 |
| 类型检查 | `uv run --offline --no-project --with mypy==1.18.2 --with jsonschema==4.25.1 python -m mypy src/spring_runtime_mcp` | 48 个源文件通过 |
| Lint | `uv run --offline --no-project --with ruff==0.14.1 ruff check src tests scripts` | 通过 |
| 格式 | `uv run --offline --no-project --with ruff==0.14.1 ruff format --check src tests scripts` | 75 个文件通过 |
| 契约 | `uv run --offline --no-project --with jsonschema==4.25.1 python scripts/validate_tool_contract.py` | 8 组独立 input/output schemas、144+ 合成断言通过 |

本次未单独执行 Node 包装入口、wheel/sdist 构建、客户端互操作或真实实例版本矩阵。

### 已知限制

- 当前宿主未使用官方 MCP Python SDK；有限 JSON-RPC 方法测试不能证明完整 MCP 协议合规或所有客户端兼容。
- HTTP 连接器尚无完整的 DNS 全组白名单验证、数字 IP 固定建连、实际 peer IP/port 校验和目标凭据绑定；HTTPS 使用默认 TLS 上下文，尚无真实 TLS/SNI 矩阵验收。远程/生产使用仍需完成安全规格的独立验收。
- 上游读取有字节上限和套接字超时，但完整绝对时限、解压预算与在途请求取消仍需独立验收。
- Boot 2.7/3.x/4.x、MVC/WebFlux 的证据来自合成 fixtures，未完成真实 Java/Boot/认证组合验收；源码类型与工厂关联仍依赖显式 SourceCatalog 事实。
- 尚无显式构建后端、依赖锁文件或已验证的分发产物。本版不宣称满足 SPEC 中全部产品目标。
- [红队审计](docs/audits/security-redteam-2026-10-17.md)及其机器清单是较早代码基线的历史证据，不是新增模块的完整审计。

本次封板整理不改变公开 inputSchema/outputSchema、运行依赖、目标白名单或目标配置。
