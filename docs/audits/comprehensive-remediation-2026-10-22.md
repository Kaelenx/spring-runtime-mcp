# 综合审查修复与真实项目验收 — 2026-10-22

本报告接续[综合审查](comprehensive-review-2026-10-22.md)，记录 CR-01 至 CR-13 的修复与验收。原审查保留当时的失败结果，不能当作当前检查状态。代码仍为 v0.2.0 工作树，尚未发布或部署；公开 inputSchema/outputSchema 与运行依赖未变。

## 逐项关闭证据

| 编号 | 修复后的行为 | 回归/核查位置 |
| --- | --- | --- |
| CR-01 / P1 | SDK 前校验请求 ID：字符串最多 128 UTF-8 字节，整数限安全范围。违规以 null ID 返回固定 INVALID_REQUEST 并结束会话。工具结果及兜底都重新测量完整帧；SDK 最终序列化后再检查 16,384 字节，含元数据和两份内容 | `server.py`；`tests/integration/test_stdio_host_process.py` 超长 ID 与真实管道测试 |
| CR-02 / P1 | 共享 Services 对每目标统一两个读取槽，八工具共用；等待计入调用时限，取消等待不打开连接，异常后释放额度 | `services.py`；八调用并发/排队取消回归；真实 STDIO + 鉴权回环 fixture 的峰值并发断言 |
| CR-03 / P1 | 调用共享一个 15 秒绝对 deadline，覆盖排队、多端点读取、协作分析与输出检查；单请求最多 5 秒。分析访问量也计入调用预算，取消传至传输层关闭 socket | `models/common.py`、`tools/support.py`、`server.py`；多端点总超时与在途取消回归 |
| CR-04 | 有界递归投影 allOf/oneOf/anyOf/additionalProperties；不支持的语义明确 partial | `adapters/openapi.py`；四种组合/字典 Schema 和不支持语义回归 |
| CR-05 | 同文档 PathItem/Response 包装引用先有界解析，再提取操作/响应；包装深度与 Schema 引用深度分开计数 | 同上；maxRefDepth=0 下路径与响应引用回归 |
| CR-06 | 区分 OAS 3.0/3.1；3.1 引用相邻约束以 allOf 保留共同作用。未知 `$schema`/jsonSchemaDialect 明确 partial，Schema 的显式方言覆盖文档默认；远程引用与方言都不下载 | 同上；3.0.3/3.1.0、文档/Schema 方言和覆盖规则回归 |
| CR-07 | 基线推导仅使用成功读取且完整解析的迁移引擎；403、超时或能力关闭不再生成该引擎的 PENDING | `adapters/migrations.py`；三类失败引擎回归 |
| CR-08 | 缺失 checksum、未知算法、不同算法与可信不一致分别处理；不可比较时不报 CHECKSUM_MISMATCH | 同上；Flyway 缺值、Liquibase 算法缺失/不兼容回归 |
| CR-09 | 类型候选使用共享父链可见性投影，子容器同名 Bean/别名屏蔽父候选；缺父节点与环保留 unknown | `analyzers/bean_graph.py`、`adapters/drift.py`；名称/别名遮蔽回归 |
| CR-10 | Flyway SUCCESS、OUT_OF_ORDER、MISSING_SUCCESS、FUTURE_SUCCESS 都参与已应用版本选择，不误报 absent | `adapters/drift.py`；四种成功状态回归 |
| CR-11 | 根健康响应只有 UP 时保留整体状态，但组件枚举不可见；组件查询 partial、totalMatched=null。显式空 components 与真正叶节点分别处理 | `adapters/health.py`、`tools/diagnose_health.py`；合成回归及真实隐藏健康响应 |
| CR-12 | 进程测试按每条请求读取回复后才关闭 stdin；管道读取有单调时限与最大字节量，最后阶段不再与 EOF 竞争 | `tests/integration/test_stdio_host_process.py`；全量进程测试通过 |
| CR-13 | CI 与标签发布复用 checks.yml；发布 needs 整体 validate，依赖测试、契约、类型、lint、构建和 SDK 安装验收；发布标签须等于包版本 | `.github/workflows/{checks,ci,release}.yml`；本地 YAML 解析与任务依赖核查，未执行发布 |

主要新增合成回归位于 [test_comprehensive_review_regressions.py](../../tests/unit/test_comprehensive_review_regressions.py)，STDIO/凭据边界位于 [test_stdio_host_process.py](../../tests/integration/test_stdio_host_process.py)。没有删去、跳过或降低失败安全断言。AST 的 `.send` 禁令只为 `server._BoundedWriteStream` 的唯一 `self._stream.send` 增加精确例外：目的地是 SDK 内存协议流，stdout 与 framing 仍由 SDK 负责；原始套接字 send 仍禁止。

OpenAPI 的方言默认与覆盖规则依据 [OAS 3.1.1 Schema Object](https://spec.openapis.org/oas/v3.1.1.html#schema-object)及 [JSON Schema 2020-12 Core](https://json-schema.org/draft/2020-12/json-schema-core)。实现只投影已支持的约束；认识方言 URI 不代表支持其全部关键字，未建模结构仍返回缺口。

## 架构与工程交付

`Services`、`RuntimeReader` 和安全诊断信封从 Bean 工具下沉到独立模块，其余工具不再反向依赖 Bean 实现。服务统一协调读取，`ContextVar` 传播每调用预算，协议、HTTP、脱敏与纯分析维持各自边界。

新增 `uv.lock` 与固定的 pytest/mypy/Ruff 开发组；CI 和安装冒烟使用同一依赖解析结果，实际 SDK 为 2.3.0。wheel 含 `schemas/tools-contract.json` 与 PEP 561 的 `py.typed`，sdist 另包含示例和锁文件。使用官方 `Client` 的合法模式名 `legacy` / `2026-07-28` 完成握手、八工具按字母序发现及 INVALID_ARGUMENT 结果验证。

`--token-env` 只接受环境变量名，令牌由进程环境绑定到登记 origin。格式、长度、缺失值在启动时校验；令牌加入已知秘密集合，HTTP 调试输出关闭。合成回环测试验证认证有效、上游反射令牌被脱敏、八并发最多两个读取，以及非法凭据不产生网络 IO。没有添加任意请求头、Basic 或远程目标支持。

## TDD 项目的实际验收

用户明确批准了可选 Actuator、现有 JWT 安全链、回环隔离实例及临时 PostgreSQL。原 `<TDD checkout>/pom.xml` 仅增加显式 `runtime-diagnostics` Maven profile，版本沿用 Boot BOM。未修改其安全链、正常启动配置或业务实现。

直接启动原工作区时，新增的 `DashboardController` 依赖接口 `DashboardService`，没有实现 Bean；源码/测试注明处于商家看板红阶段。这是已观察到的启动阻塞。本轮保留全部未提交业务任务与代码，使用真实项目已提交 S8 版本 **b7c54641df80ad9502ee1e3cc95a21d494cdd75d** 的临时源码副本应用诊断补丁，避免将未完成业务写作已修复。

真实组合为 **Spring Boot 3.5.16 / Java 21 / MVC / 既有 JWT / PostgreSQL 16**。使用原 ShopApplication，临时密钥及诊断令牌，两个随机回环监听端口和临时 Testcontainers 数据库；没有接入既有业务数据库。MCP 只读六个登记的 Actuator 端点，不调用登录或其他业务 URL。匿名 Bean 查询被现有安全链拒绝；授权后七次调用覆盖 Bean、条件、路由、健康、Flyway、启动和跨维度漂移。

观察到 authService、POST /api/auth/login 注册与 Flyway 版本 1 APPLIED；启动端点提供 Boot 3.5.16 及步骤。健康详情隐藏时返回 partial/null；缺少源码目录、未建模条件、基础设施依赖和关联/深度/记录预算触发的图、启动与漂移缺口保留在摘要中。验收通过表示这些预期的安全/证据行为成立，不表示所有工具结果完整或业务功能正确。

复现和范围见 [examples/tdd-runtime/README.md](../../examples/tdd-runtime/README.md)，脱敏摘要见 [verification.json](../../examples/tdd-runtime/verification.json)。默认 pytest 不导入真实验收脚本。应用进程已停止，检查带本轮标签的 PostgreSQL 容器剩余为零，临时源码已清理；没有保存原始响应、框架异常栈、JWT、数据库连接串或原始日志。Maven 实际执行的是 `-Pruntime-diagnostics -DskipTests test-compile dependency:build-classpath`，没有将编译成功写作 TDD 测试全绿。

## 本次实际验证

| 检查 | 实际方法 | 结果 |
| --- | --- | --- |
| 默认全量测试 | `uv run --no-sync python -m pytest -q` | **782 passed，35.07 秒** |
| 类型 | `uv run --no-sync python -m mypy src/spring_runtime_mcp` | **51 文件通过** |
| 契约 | `python scripts/validate_tool_contract.py` | 八工具、独立 Schema 和 144+ 断言通过；公开 Schema 无变更 |
| 静态/格式 | `ruff check src tests scripts`、`ruff format --check src tests scripts` | 通过，全部文件格式正确 |
| 锁文件 | `uv lock --check --offline` | 通过 |
| 构建/资源 | `uv build`，检查 ZIP/TAR 资源 | wheel/sdist 成功；Schema、py.typed 及源码分发锁文件存在 |
| 安装互操作 | 临时 venv、锁定约束离线安装 wheel，从仓库外运行官方 SDK 客户端 | legacy / 2026-07-28 均通过发现与工具错误验证 |
| workflow | Ruby YAML 解析三份 workflow，核查复用依赖/版本门禁 | 解析通过；未运行远端 Actions，未发布 |
| 真实目标 | 显式运行 `scripts/check_tdd_runtime.py` | 上述 Boot 3.5.16 组合通过，安全摘要已保存 |
| 仓库 | `git diff --check`，核对 TDD pom 与业务改动 | 空白检查通过，仅批准的诊断 profile 为本轮对 TDD 的源码修改 |

## 未解决限制

非回环白名单、Basic/自定义头、自定义管理基路径、OpenAPI 路径回退、完整压缩/响应头预算、真实 TLS/SNI 与第三方桌面客户端矩阵尚未完成。OpenAPI 未支持的方言/关键字、动态引用等保持 partial。配置诊断已通过 `--config-prefix`/`--config-property` 开放，`--config-profiles` 与 `--config-allow-values` 默认关闭；真实 Shop 也未暴露配置值或 OpenAPI。

默认测试、真实样本与静态安全规则证明上述具体行为，不能替代全部 Boot 2.7/3.x/4.x、MVC/WebFlux 或 Python 多版本运行。CI 已配置 Python 3.11–3.14，本机本轮只执行 Python 3.12.15。总时限采用 asyncio 取消与同步遍历协作检查；操作系统 DNS 系统调用本身不能强制中断，已取消连接不会继续写 HTTP。未提交的 TDD 看板红阶段仍需在其业务任务中完成。
