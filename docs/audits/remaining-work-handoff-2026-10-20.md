# 项目剩余工作交接

交接日期：2026-10-20。适用目录：`spring-runtime-mcp`。

## 1. 接手结论与基线

项目已具备八工具、STDIO 宿主、只读 HTTP 连接器及脱敏输出链路，是可运行的本地诊断 MVP，尚未完成完整产品验收。以完整 SPEC 为分母的完成度约 70%、本地 MVP 约 90%，均为工程估算，没有逐项加权，不得用测试通过率代替完成度。实现范围见 `CHANGELOG.md:9`，验收目标见 `SPEC.md:162`。

- 当前 HEAD：`445414d chore: finalize v0.1.0`。审计修复仍在未提交的工作区，HEAD 本身不包含这些修复。
- 最近一次测试证据日期为 **2026-10-18**；本次交接仅核对文件和工作区，**未重新运行测试、连接目标或执行构建**。
- TC-BUG-01 至 TC-BUG-15 已修改相关实现，专项含补充用例 33 passed；不能据此宣称完整安全或兼容性验收完成。
- 当前唯一已记录的全量测试失败是安全静态清单与连接器 socket 用途冲突；不是当前唯一的产品缺口。

| 最近一次检查 | 结果 | 退出码 | 证据 |
| --- | --- | --- | --- |
| 审计专项回归 | 33 passed | 0 | `docs/audits/logic-and-boundary-fix-verification-2026-10-18.md:62` |
| 全量 pytest | 705 passed、1 failed | 1 | 同文件 `:64` |
| mypy | 49 个源码文件通过 | 0 | 同文件 `:65` |
| 契约校验 | 八组独立 Schema、144+ 合成断言通过 | 0 | 同文件 `:66` |
| Ruff lint / format | 通过 / 80 个文件通过 | 0 | 同文件 `:67`、`:68` |
| git diff --check | 通过 | 0 | 同文件 `:69` |

接手资料：

- [已确认的 15 项逻辑与边界缺陷](logic-and-boundary-audit-2026-10-18.md)。
- [原修复计划及正确行为约束](logic-and-boundary-remediation-plan-2026-10-18.md)。
- [修复结果、测试调整理由与剩余限制](logic-and-boundary-fix-verification-2026-10-18.md)。
- [最初的架构审计](project-state-and-architecture-audit-2026-10-18.md)：这是修复前证据，部分源码行号和缺陷状态已变化，不能直接当成当前状态。
- [产品规格](../../SPEC.md)、[仓库约束](../../AGENTS.md)、[安全规格](../architecture/security-and-sanitization.md)。

## 2. 当前可用入口与边界

从仓库根目录启动真实 STDIO 宿主，命令如下。此处为交接命令，交接时未执行；依赖须已缓存，实际工具调用仅可访问明确获权的目标。

```bash
PYTHONPATH=src uv run --offline --no-project --with jsonschema==4.25.1 \
  python -m spring_runtime_mcp.cli --base-url http://127.0.0.1:18081
```

CLI 当前只有 `--base-url`、`--target-id`、`--timeout` 三个业务参数；`--url`、`--config`、`--token-env`、`--version` 仍是规格目标，不能当作现有入口。`replay` 是一次性固定场景重放，不能作为 MCP 客户端的宿主命令。依据：`src/spring_runtime_mcp/cli.py:13`、`SPEC.md:141`、`README.md:32`。

| 当前边界 | 源码依据 | 交接要求 |
| --- | --- | --- |
| 仅允许回环地址；已有 DNS 全组检查、数字 IP 建连和 peer 校验 | `src/spring_runtime_mcp/transport/actuator_http.py:81`、`:270` | 保留现有守卫；远程白名单不属于本轮已交付能力 |
| 默认关闭配置诊断；授权只支持内部策略注入 | `src/spring_runtime_mcp/server.py:35`、`:43`；`src/spring_runtime_mcp/policy/configuration.py:10` | 不得为演示而默认启用 env/configprops |
| 线上 SourceCatalog 仍为空 | `src/spring_runtime_mcp/server.py:50` | 不把离线源码工厂推导声称为线上自动发现 |
| 管理端点和 OpenAPI 路径固定 | `src/spring_runtime_mcp/transport/actuator_http.py:24` | 不声称已支持自定义管理路径及 `/v3/api-docs` 回退 |
| 尚无目标凭据绑定入口 | `src/spring_runtime_mcp/cli.py:13`；`docs/audits/logic-and-boundary-fix-verification-2026-10-18.md:86` | 401/403 保持安全失败，不绕过目标鉴权 |

## 3. 剩余工作及验收标准

以下为交接待办，尚未实施的方案不代表已获批准。P0 是当前修复收尾阻断项；P1/P2 仍是完整产品交付工作。真实实例、发布及扩大权限按第 5 节单独确认。

### RW-01 / P0：解决唯一全量失败，关闭本轮审计修复

- **状态与定位**：待确认安全测试修正；`tests/security/test_redteam_readonly.py:96` 的静态清单在 `:106` 禁止 socket，`:124` 拒绝连接器导入；连接器在 `src/spring_runtime_mcp/transport/actuator_http.py:14` 导入 socket，用于固定地址、校验 peer 和主动关闭连接。
- **待审阅方案**：仅对精确相对路径 `transport/actuator_http.py` 允许 `socket` 与 `http.client` 导入，其他模块仍禁止；保留 GET、重定向、唯一请求构造器、执行和写入入口断言；新增禁止生产代码直接调用 `send/sendall/sendto/sendmsg` 的静态断言。
- **必保动态证据**：禁用地址零连接、混合 DNS 全组拒绝、peer 不匹配零 HTTP 写出。相关用例位于 `tests/unit/test_logic_boundary_audit_regressions.py:795`、`:828`；不能让全局断网夹具提前拒绝并假装验证了连接器。
- **验收**：获得该限定方案的明确确认后再改安全测试；专项、相关 HTTP/STDIO 集成及全量全部退出 0；不删断言、不加 skip/xfail、不倒退 IP/peer 守卫。记录实际新计数，不机械要求仍为 706 个用例。
- **审批上下文**：此前已请求确认，截至交接未获得回复。用户要求编写交接文档不构成批准。完整方案和原因见[修复记录第 43 行起](logic-and-boundary-fix-verification-2026-10-18.md)。

### RW-02 / P1：补齐协议与在线契约执行链

- **当前缺口**：`_descriptors()` 未发布 outputSchema（`src/spring_runtime_mcp/server.py:98`）；`_call_tool()` 仅验证输入（`:124`）；`dispatch()` 缺少严格的版本、初始化状态和执行前请求封装门禁（`:134`）；STDIO 顺序等待单次 dispatch（`:196`），不能把底层主动取消能力等同于协议取消已贯通。
- **实施方向**：先明确支持的协议版本、生命周期及错误映射，验证非法 jsonrpc/id/name 在 Handler 前拒绝且零 IO；补齐工具描述器输出契约、脱敏后输出校验和安全失败信封；让取消通知能取消对应任务。先对照 `SPEC.md:67` 与既有调用策略处理规格冲突，不凭版本标签声称合规。
- **验收**：真实进程测试覆盖初始化顺序、版本协商、非法请求零 IO、未知工具、取消、错误封装及双副本 16,384 字节上限；使用选定第三方 MCP 客户端完成握手、八工具发现和只读调用，记录具体客户端及版本。
- **依赖与边界**：可在 P0 等待确认期间准备失败用例及方案。若迁移官方 SDK或新增依赖、改变公开 Schema，先准备可审阅变更并确认；当前未安装或接入 SDK。

### RW-03 / P1：补齐可信本地配置、能力登记与路径接入

- **定位**：`src/spring_runtime_mcp/cli.py:13`、`src/spring_runtime_mcp/server.py:30`、`src/spring_runtime_mcp/policy/configuration.py:10`、`src/spring_runtime_mcp/transport/actuator_http.py:24`；规格要求见 `SPEC.md:139`、`:141`。
- **实施方向**：提供可信本地配置装配，登记 target/origin、逐端点能力、配置属性/前缀、值及 Profile 权限；接入管理基路径和最多三个预登记 OpenAPI 候选及规定的回退规则。明确现有三个 CLI 参数与规格入口的兼容策略，保留已有用户配置。
- **验收**：默认配置诊断仍零读取；开启后参数范围与登记范围取交集；漂移工具不能旁路 Profile 权限；跨目标、路径穿越、任意 URL/headers 透传均拒绝；OpenAPI 仅访问预登记候选，认证失败不改试其他主机或业务 URL。
- **边界**：应用端暴露/采集策略变更与实际启用敏感能力需确认。编写方案和离线测试不等于允许修改目标配置。

### RW-04 / P1：完成凭据绑定、网络和资源预算闭环

- **当前基础**：回环 DNS/IP/peer 控制、TLS 默认验证及单次交换绝对时限/主动关闭已有实现；尚未证明完整产品网络状态机、工具总预算和认证链均闭环。`HttpResponse` 未携带编码信息，读取门按单请求计时，见 `src/spring_runtime_mcp/transport/response_gate.py:50`、`:105`；DNS 系统调用取消限制见修复记录 `:87`。
- **实施方向**：按目标登记 Basic/Bearer 或受限认证头，仅从指定环境变量取秘密；将已知秘密策略传入脱敏、缓存及序列化出口；实现跨端点的工具总截止时间、目标并发上限，并对压缩响应给出可验证的双预算处理或安全拒绝策略。
- **远程支持**：默认继续仅回环。完整 SPEC 的非回环 HTTPS 目标须另行设计 origin/IP 规则、DNS 全组验证、数字地址固定、实际 peer 与主机证书校验；不得直接放宽 `_loopback()`。
- **验收**：先用自建回环 HTTP/TLS 服务和合成凭据验证成功/401/403、无跨目标或重定向凭据泄漏、TLS 主机名/SNI、混合 DNS/peer 拒绝、慢流/超时/取消、并发和压缩预算。检查 structuredContent、文本副本、日志、错误、缓存均无秘密。
- **依赖与审批**：依赖 RW-03 的可信配置；扩大目标/IP 白名单和真实外部目标访问须明确确认，不能混入默认测试。

### RW-05 / P1：建立真实版本与客户端验收证据

- **当前证据**：现有测试以合成 JSON 和自身启动的回环 fixture 服务为主，不证明真实 Boot/Java/Web 栈/认证组合兼容，见 `CHANGELOG.md:34`、修复记录 `:89`。
- **交付物**：按 `SPEC.md:162`、`:272` 提供隔离演示应用及矩阵：选定 Boot 2.7/3.x、Java 版本、MVC/WebFlux 和认证组合；Boot 4.x 单列实验性。Python 3.11/3.12/3.13、OpenAPI 3.0/3.1 与选定 MCP 客户端分别记录，不混作一个“兼容”结论。
- **验收**：记录每个组合的确切版本、命令、权限与端点、日期、结果及已知限制；覆盖八工具实际证据读取、健康 503、隐藏详情、双迁移引擎及失败分支。缺少采集条件时应安全报告 unknown/partial，不能伪造成功。
- **执行边界**：先准备合成样例和测试方案；真实进程启动、目标暴露/采集配置变更、重启和接入须取得相应授权。真实矩阵与默认离线门禁分开运行。

### RW-06 / P2：补齐架构审计中未被 15 项专项覆盖的诊断能力

先复核当前代码、编写独立失败回归，再做最小修复；不能因 TC-BUG-01 至 15 已通过而自动关闭以下事项。

| 待办 | 当前定位 | 完成标准 |
| --- | --- | --- |
| 线上 SourceCatalog 事实接入 | `src/spring_runtime_mcp/server.py:50` | 接入可信且有界的本地事实；记录来源/完整性；线上接口、工厂、多跳阻塞链有证据可复现；无事实保持 unknown |
| MVC/WebFlux 函数式路由 | `src/spring_runtime_mcp/adapters/mappings.py:134`、`:165` | 处理受支持 predicate/handlerFunction 形状；定点路径筛选不误丢可解析路由；无法解释时明确不完整 |
| Liquibase 身份及迁移稳定排序 | `src/spring_runtime_mcp/adapters/migrations.py:201`、`:244`、`:317` | 身份纳入 changelog/id/author 及容器范围，不覆盖同 id 的不同 changeSet；按契约先异常、再执行顺序及稳定身份排序，分页可重复 |
| 启动截断与完整计数 | `src/spring_runtime_mcp/adapters/startup.py:54`；`src/spring_runtime_mcp/tools/analyze_startup.py:50` | 超节点预算时明确 truncated；不可完整枚举时 totalMatched 为 null；不把可见数量当总体数量 |
| 嵌套 configprops 字段 | `src/spring_runtime_mcp/adapters/config.py:179` | 登记属性与嵌套叶子语义一致；受深度/节点/授权预算约束；不支持结构必须留缺口，不输出原始值树 |

迁移端点失败时的 totalMatched 已在 `src/spring_runtime_mcp/tools/check_migrations.py:82` 调整，不能照抄旧架构审计为“仍未修复”。其他历史结论同样须逐项核实。

### RW-07 / P2：完成构建、CI 与分发验证

- **当前缺口**：`pyproject.toml:1` 有项目元数据和脚本，尚无显式构建后端、锁文件及验证过的 wheel/sdist，见 `CHANGELOG.md:35`。
- **实施方向**：确定构建后端、开发依赖与锁定策略；确保运行所需 Schema 等资源随包分发，摆脱源码检出目录依赖；建立 Python 版本矩阵和离线默认门禁，真实兼容性任务单列。
- **验收**：构建 wheel/sdist，在干净隔离环境安装后运行控制台脚本、initialize/tools/list 和合成只读调用；确认契约资源可加载、stdout 无杂项。记录产物和验证结果，不把“存在脚本声明”当作“已验证可安装”。
- **审批边界**：新增运行依赖、既定架构变化、发布和部署需确认。构建/安装入口补齐前，不把计划中的 `uv sync --dev`、`uv build`、Maven 命令写为已执行成功。

### RW-08 / P2：同步文档与最终状态声明

- **已发现的时序差异**：`SPEC.md:7`、`CHANGELOG.md:32` 仍描述修复前 DNS/IP/peer 缺口；`docs/architecture/security-and-sanitization.md:510` 甚至描述更早的“无连接器”基线。`HANDOVER.md:75` 附近仍记录旧静态规则，应结合 RW-01 决议阅读。
- **处理方式**：保留历史审计和旧版本封板记录的时间语义；新增有日期的现状说明/变更记录并修正文档入口，明确已实现、已验证、实验性与未完成。不能将所有历史限制直接删除，也不能继续把已修控制写成完全未实现。
- **验收**：README、SPEC 当前状态、架构手册、配置指南、版本记录与真实 CLI/默认值一致；每条兼容性/安全/发布声明可追溯到对应命令和证据。未完成完整 SPEC 时明确交付范围，不使用“生产就绪”或“全版本支持”。

## 4. 推荐接手顺序与阶段出口

1. **保护工作区**：先读 `git status --short`、`git diff --stat` 和相关 diff。不要 reset/clean/stash 覆盖现有修改；当前还有 `.vscode/`、新策略文件、专项/进程/契约测试和审计文档等未跟踪内容，不要统一删除或无差别暂存。
2. **关闭审计修复批次**：按 RW-01 保留审批状态；可先独立复核和准备测试。方案获准后，恢复全量及静态门禁，更新修复验证记录；这一阶段的通过仅代表本轮审计收尾。
3. **完成本地可交付版本**：推进 RW-02、RW-03、RW-04 的回环范围、RW-06 中影响声明范围的项目，以及 RW-07。用 RW-05 建立选定真实回环组合与客户端证据，再按 RW-08 同步文档。
4. **完成完整产品验收**：在明确授权下补齐 RW-04 远程分支及其余 SPEC 验收。若决定缩小产品范围，必须明确修订范围和支持承诺，不能把未实现项默认为已完成。

每个批次保留“问题 → 正确行为失败回归 → 最小实现改动 → 实际验证 → 限制”记录；禁止删减断言、放宽校验或跳过失败来获得绿色。既有测试确有 Bug 时，单独说明触发条件、改写理由及审批状态。

## 5. 操作授权和未决事项

- 现有授权涵盖 TC-BUG-01 至 15 的必要修复和离线/自建回环验证；本文件只交接其余工作，不授权自动实施全部产品路线图。
- **未决批准**：RW-01 安全静态清单的限定修正。依据 `AGENTS.md:70`：“弱化或删除失败的安全测试来通过检查”属于禁止行为；未经明确确认，不直接扩大导入例外。
- **需另行确认**：当前范围外的公开 inputSchema/outputSchema、新增运行依赖、扩大目标/IP 白名单、改变目标暴露/采集配置、真实实例集成、重启、发布部署及合并。先准备具体方案、验证和影响说明；保留此前同范围已获授权，避免重复询问。
- **永久约束**：仅固定只读方法；不跟随重定向、不继承环境代理、不绕过 TLS/鉴权；不读取 heapdump/原始 logfile；不输出原始 env/configprops、秘密、原文异常或远端指令；配置诊断默认关闭；stdout 仅 JSON-RPC；完整工具回包双副本合计不超过 16,384 UTF-8 字节。

## 6. 接手后的验证命令

下面是仓库现有入口，**本次文档交接未执行**。均从仓库根目录运行；`--offline` 依赖缓存可用。默认测试只允许合成数据及测试自身启动的回环服务，不能借复测连接真实目标。失败只复跑受影响检查；相关检查通过后停止无依据的重复验证。

```bash
# 审计专项
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q -p no:cacheprovider tests/unit/test_logic_boundary_audit_regressions.py --tb=short

# 安全静态规则与连接器/宿主相关验证
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q -p no:cacheprovider tests/security/test_redteam_readonly.py tests/integration/test_mcp_host_and_http.py tests/integration/test_stdio_host_process.py --tb=short

# 最终全量门禁；当前历史结果仍是退出 1，不能写成已通过
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q -p no:cacheprovider --tb=short
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with mypy==1.18.2 --with jsonschema==4.25.1 python -m mypy --cache-dir=/dev/null src/spring_runtime_mcp
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with jsonschema==4.25.1 python scripts/validate_tool_contract.py
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with ruff==0.14.1 ruff check --no-cache src tests scripts
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with ruff==0.14.1 ruff format --check --no-cache src tests scripts
git diff --check
git status --short
git diff --stat
```

真实 Boot、TLS/SNI、第三方客户端、wheel/sdist 和远程白名单分别建立证据；上面的离线命令全部变绿也不能代替这些验收。新测试命令、构建和 CI 配置落地后，再写入实际结果及退出码。

## 7. 最终交接完成清单

- [ ] RW-01 形成明确决议，未弱化断言，全量及相关静态门禁通过。
- [ ] 协议生命周期、在线输出契约和协议取消贯通，选定客户端互操作有记录。
- [ ] 可信配置、逐端点权限、凭据和路径接入闭环，默认配置诊断仍关闭。
- [ ] 网络、工具总预算、并发、TLS、压缩及取消有独立证据，剩余 OS DNS 限制准确披露。
- [ ] 架构审计遗留项逐项修复或有经确认的范围决议，不与已修复项混淆。
- [ ] 真实版本矩阵与 Python 矩阵按明确支持范围验收，实验组合单列。
- [ ] wheel/sdist 可构建、隔离安装和运行，运行资源完整，锁定及 CI 就绪。
- [ ] 当前文档与源码一致；未完成项、批准状态、实际命令/退出码和变更范围可追溯。
- [ ] 发布、部署、合并或目标扩权如有需要，另行完成明确授权。
