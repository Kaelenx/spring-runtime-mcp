# 版本记录

## Unreleased

- 关闭 2026-10-18 逻辑与边界审计的 TC-BUG-01 至 15；连接器增加数字地址固定与 peer 校验，安全静态清单仅对 `transport/actuator_http.py` 放行 `socket`/`http.client` 导入并新增禁止原始套接字写入。全量 729 passed。
- 工具契约 JSON 移至 `src/spring_runtime_mcp/schemas/`，通过 `importlib.resources` 加载，随 wheel 分发。
- 增加 hatchling 构建后端、Apache-2.0 许可证、英文 README、`SECURITY.md` 与 GitHub Actions CI（Python 3.11–3.14 测试、mypy/ruff、wheel 构建及干净环境 stdio 握手）。`uv build` 产物已在隔离虚拟环境安装并完成 `initialize`/`tools/list`。

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
