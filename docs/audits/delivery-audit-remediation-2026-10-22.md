# 交付审计修复与验证 — 2026-10-22

本记录接续 [综合审查](comprehensive-review-2026-10-22.md)与[修复验收报告](comprehensive-remediation-2026-10-22.md)，记录一次独立交付审计发现的证据诚实度问题、修复和验证结果。审计对象为 `master` 提交 `86ac041` 及其工作树；本轮没有连接真实诊断目标、没有发布或部署。

## 审计发现与修复

| 严重度 | 发现 | 修复后的行为 | 回归 |
| --- | --- | --- | --- |
| P0 | `detect_drift` 在 `sourceModifiedAt` 已提供、但 startup 端点未暴露/未启用时静默返回 `ok` | 添加 `STARTUP_EVIDENCE_UNAVAILABLE`，返回 `partial`、`totalMatched=None`，不再把缺失启动时间证据当成完整比较 | `tests/unit/test_delivery_audit_regressions.py` |
| P0 | `verify_mappings` 静默丢弃非字符串/超长 patterns 和未知 HTTP method，可能返回 `ok` 的空结果或 `methodsUnrestricted=true` | 添加 `MAPPING_PATTERN_UNREADABLE` / `MAPPING_METHOD_UNREADABLE`，设置 `matchingComplete=false`，未知 method 不再被解释为 unrestricted | 同上 |
| P1 | Bean 缺失 `type`/`scope` 被输出为 null，但结果仍可能是 `ok` 且 `totalMatched` 具体 | 显式 `TYPE_NOT_REPORTED` / `SCOPE_NOT_REPORTED`，`totalMatched=None`；`resource=null` 保留为程序化 Bean 的可选正常事实 | 同上 |
| P1 | 输出 schema 声明 `startedAt` 为 `date-time`，但服务端校验未启用 format checker | 输出/输入校验使用 RFC3339 checker；adapter 对非法 `startTime` 置空并标记 `STARTUP_TIMING_UNOBSERVED` | 同上 |
| P1 | `server._result` 把脱敏/序列化拒绝一律改写成 `RESPONSE_TOO_LARGE` | 保留 `NormalizationError.code`，只有真实超限才返回 `RESPONSE_TOO_LARGE` | 同上 |
| P1 | health details 与 startup tags 截断后没有 `truncated`/gap | 添加 `HEALTH_DETAILS_LIMIT`、`STARTUP_TAG_LIMIT`，设置 `truncated=true`、`totalMatched=None` | 同上 |
| P1 | 迁移分析预算耗尽被 `except ValueError` 误判为 `*_EVIDENCE_INVALID` | 单独捕获 `AnalysisLimit` 并标记 `GRAPH_LIMIT_REACHED` | 同上 |
| P1 | 配置诊断与 drift profile 在公共 CLI 没有启用入口 | 新增 `--config-prefix`、`--config-property`、`--config-profiles`、`--config-allow-values`；无选择器时启动失败 | `tests/integration/test_cli_config_wiring.py` |
| P1 | `replay.py` 引用 `tests/fixtures`，安装 wheel 后无法运行 | fixtures 移入包内 `replay_data/`，安装后 replay 可运行；`src` 不再引用 `tests/` | `test_packaged_replay_data_is_not_read_from_tests` |
| P2 | `coverage.scope` 被误脱敏、README 的 16KiB 表述过宽、SPEC 使用不存在的 `--url`/脚本路径、文档含本机路径 | 放行本地 scope；限定 tool result 帧；修正命令与路径；清理本机路径 | 静态检查与文档链接检查 |

公开 inputSchema/outputSchema、八工具排序、只读 GET、回环目标、16KiB tool result 预算和脱敏边界未变。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| 默认全量测试 | **782 passed / 0 failed / 0 skipped**，35.29 秒 |
| 类型 | mypy strict，51 source files 通过 |
| Lint / 格式 | `ruff check`、`ruff format --check` 通过，92 files |
| 契约 | `scripts/validate_tool_contract.py` 通过，8 工具、144+ 断言 |
| 构建 | `uv build` 成功；wheel 含 schema、`py.typed`、`replay_data` |
| 隔离安装 | 干净 venv 安装 wheel；8 工具 `tools/list`、schema parity、工具错误信封通过 |
| 官方 SDK | legacy 与 `2026-07-28` 模式均通过 discovery 和工具错误检查 |
| replay | 安装后运行成功，输出 `REQUIRED_BEAN_MISSING → SOURCE_FACTORY_NOT_CREATED → PROPERTY_NOT_FOUND` |
| 配置 CLI | 默认 `CAPABILITY_DISABLED`；带 `--config-prefix` 进入真实读取路径（无目标时为 `TARGET_CONNECTION_REFUSED`） |
| 红队 | Node 入口 5/5 通过，0 skip |
| 启发式审计 | `python3 ai_audit.py`：0 命中，exit 0（Protocol/overload 与 `self._*` 引用已精确排除） |
| 安全/一致性 | 仅 `actuator_http.py` 导入 socket/http.client；`git diff --check`、`uv lock --check` 通过 |

## 残余风险与说明

- 审计早期在并发修改前观察到一次 `test_rw06_startup_node_limit...` 超时形态失败；修复后连续多次全量通过，未能复现。保留为稳定性观察项。
- `replay_data/` 是 `tests/fixtures/` 的打包副本，后续修改 fixture 时需要同步。
- 本轮没有重新运行真实 Boot/Java/PostgreSQL Shop 验收，也没有执行真实 TLS/SNI 或第三方桌面客户端矩阵；这些沿用仓库已有验收记录，不等同于新的真实环境证明。
- 配置诊断仍默认关闭；只有显式登记的 prefix/property 才可读取，值输出还需要 `--config-allow-values`。
- `ai_audit.py` 是启发式扫描工具；本轮已修正 Protocol/overload、属性方法引用和 `partial` 状态断言的识别，但启发式扫描仍不能替代本记录中的定向复现和运行时测试。
