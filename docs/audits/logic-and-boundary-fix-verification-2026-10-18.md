# 逻辑与边界缺陷修复验证记录

日期：2026-10-18。

> 2026-10-20 更新：RW-01 限定修正已获批准并落地，全量门禁首次通过；见文末「2026-10-20 更新」。

## 当前结论

TC-BUG-01 至 TC-BUG-15 的相关实现已修改，专项回归及补充边界用例 **33 passed**。全量实际结果为 **705 passed、1 failed**；唯一失败是旧安全静态清单禁止唯一 HTTP 连接器导入 socket，与数字 IP 固定建连、实际 peer 校验的实现冲突。**该测试尚未修改，限定修正已请求用户确认；当前不能宣称完整门禁通过。**

本轮未变更公开工具 inputSchema/outputSchema、运行依赖或目标应用配置；没有访问真实 Spring Boot 或生产目标。连接器当前仅允许回环地址，不提供远程地址白名单扩展。已有用户修改和未跟踪文件保留，未提交 Git。

相关依据：[审计报告](logic-and-boundary-audit-2026-10-18.md)、[修复计划](logic-and-boundary-remediation-plan-2026-10-18.md)。

## 实现与回归对应

| 缺陷 | 本轮实现 | 验证情况 |
| --- | --- | --- |
| 01 | OpenAPI 查询共享展开预算；耗尽时向上收敛为有界省略标记，返回 partial/truncated；修复引用别名继续展开 | 原始回归、预算隔离和真实 STDIO/HTTP 扇出用例通过 |
| 02 | ConfigReadPolicy 显式登记属性、前缀、值及 Profile 权限；宿主默认关闭配置；漂移读取共享门禁 | 默认零读取、通配查询不扩大权限、值权限与 Profile 旁路测试通过 |
| 03 | 严格 authority/IP 校验；DNS 全组只允许回环；数字 IP 建连；写前校验 peer；保留 TLS hostname/SNI | 禁用地址、混合 DNS、peer 不匹配零写出、真实 localhost 测试通过；静态清单修正待确认 |
| 04 | 保留已有有界 readline 与超限终止会话实现 | 输入边界及真实进程用例通过 |
| 05 | 敏感配置 origin 保持对象结构；null 不改成字符串；标量脱敏同步值状态 | includeValues 两分支完整 outputSchema 校验通过 |
| 06 | 仅健康及预登记探针接收有效 503 JSON；观察对象保留真实状态；共享检查同步支持 | 健康与两个探针组回归通过 |
| 07 | 参数和请求体使用真实 Schema，支持同文档参数/请求体引用和 Path Item 参数 | 原始失真用例及真实 Handler 引用/契约校验通过 |
| 08 | Context/Bean 绑定分别保存，不覆盖其他绑定的值和身份 | 两 Context 同属性回归通过 |
| 09 | 每次交换绝对截止时间；取消主动 shutdown/close 实际连接并收集工作任务 | 原始线程边界及真实回环慢流的超时/主动取消用例通过 |
| 10 | 非法组件记录缺口；默认仅隐藏明确 UP 状态；自定义异常状态保留 | null 与 FATAL 两分支通过 |
| 11 | 使用时间线起点及事件结束边界计算跨度，非法时间保留 unknown | 分离根步骤及非法时间用例通过 |
| 12 | 保留已有名称类型检查与受控错误分支 | 数组、对象、null、布尔值及未知名称用例通过 |
| 13 | 八工具异常出口传入已验证的请求目标身份 | 非 local 目标授权失败与超时分支通过 |
| 14 | 通用与 Bean 分页继承原快照 expires_at，不续命 | t=59 续页、t=118 过期回归通过 |
| 15 | 两引擎归一化隔离，保留有效结果与端点错误；非法引擎不参与基线缺失推断 | 原始多域回归及漂移不伪造缺失的补充测试通过 |

上述“通过”表示指定离线/回环用例通过，不表示真实 Boot、生产网络或完整 TLS/SNI 矩阵验收完成。

## 测试调整理由与约束

未删除原有回归断言，未增加 xfail/skip，未把错误输出改成预期值。

1. **补全显式授权装配**：专项配置处理器辅助函数、连接故障契约夹具、逐工具回环 HTTP 夹具及错误注入夹具，原先隐含所有配置能力默认可读。这与 TC-BUG-02 的正确契约冲突。仅为这些合成场景登记所需属性范围；原有结果、错误代码、零写请求等断言全部保留。默认宿主零配置读取由独立回归继续检查。
2. **收紧 OpenAPI 进程测试**：删除“整体 unavailable 且 MALFORMED_EVIDENCE 等也算通过”的宽松替代路径，改为必须返回有数据的 partial、明确 REF_EXPANSION_LIMIT 和 truncated、expansionComplete=false。此调整增强正确行为断言。
3. **新增边界用例**：配置授权交集、Profile 旁路、混合 DNS、peer mismatch、双栈 localhost、真实慢流取消、健康探针 503、非法时间、输入对象引用和迁移结构错误隔离，均只使用合成数据或自身启动的回环服务。

### 待确认的静态清单修正

文件：`tests/security/test_redteam_readonly.py`。

当前测试把 socket 导入在所有生产文件中一律拒绝；新的连接器必须在 HTTP 字节写出前固定数字地址并核对真实 peer。拟修改范围仅为：

- 导入例外限定到仓库内精确路径 `transport/actuator_http.py`，允许 `http.client` 和 `socket`；其他文件仍禁止。
- 保留“唯一 HTTP 请求构造器”“字面 GET”“禁止重定向”和全部执行/写入入口断言。
- 增加禁止生产代码直接调用 `send/sendall/sendto/sendmsg` 的断言，HTTP framing 仍只交给 http.client。
- 保留并运行禁用地址零连接、peer 不匹配零 HTTP 写出的动态测试，不能用全局断网夹具提前拒绝来代替真实守卫验证。

此项已通过异步确认问题提交，**在明确批准前不修改该安全测试**。依据根目录 `AGENTS.md:70`：“弱化或删除失败的安全测试来通过检查”属于禁止行为。本修正需与新增约束共同审阅，不能只扩大例外而不验证访问边界。

## 实际检查

命令均在仓库根目录执行。Python 禁止写入字节码，pytest 禁用缓存；验证仅使用离线合成数据和测试自行启动的回环服务。

| 检查 | 结果 | 退出码 |
| --- | --- | --- |
| 专项文件，含补充边界 | 33 passed，0.49 秒 | 0 |
| 专项 + 回环 HTTP + STDIO 进程（前一轮） | 54 passed，10.92 秒；之后增加两个专项用例且单独通过 | 0 |
| 最终全量 | 705 passed，1 failed，22.20 秒；失败为上述静态清单 | 1 |
| mypy | 49 个源码文件通过 | 0 |
| 契约校验 | 八组独立 Schema、144+ 合成断言通过 | 0 |
| Ruff lint | 通过 | 0 |
| Ruff format | 80 个文件通过 | 0 |
| git diff --check | 通过 | 0 |

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q -p no:cacheprovider tests/unit/test_logic_boundary_audit_regressions.py --tb=short
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with mypy==1.18.2 --with jsonschema==4.25.1 python -m mypy --cache-dir=/dev/null src/spring_runtime_mcp
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with jsonschema==4.25.1 python scripts/validate_tool_contract.py
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with ruff==0.14.1 ruff check --no-cache src tests scripts
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-project --with ruff==0.14.1 ruff format --check --no-cache src tests scripts
git diff --check
```

最终全量实际使用了 `--tb=short` 以定位失败；去掉该显示参数不改变收集范围。

## 剩余限制

- 安全静态清单的限定修正待确认，当前全量门禁尚未通过。
- 网络策略有意收紧为回环；远程目标、IP 白名单扩展及凭据绑定未在本轮新增。
- 系统 DNS 解析调用本身不能由 Python 工作线程强制终止；取消后禁止新建连接并回收已建立 socket，DNS 系统调用仍受操作系统控制。
- 配置授权当前通过内部 ConfigReadPolicy 显式注入；未新增 CLI 启用参数或工具级权限字段，默认 CLI 配置诊断保持关闭。
- 未执行真实 Spring Boot、真实 TLS/SNI 矩阵、第三方 MCP 客户端互操作或发布部署。

## 2026-10-20 更新：RW-01 限定修正已批准并落地

依据[项目剩余工作交接](remaining-work-handoff-2026-10-20.md) 的 RW-01。用户已明确批准限定方案；本节取代上文「当前结论」与「剩余限制」中关于静态清单待批准、全量未通过的两条陈述，此前文本作为历史证据保留。

### 修改范围

仅 `tests/security/test_redteam_readonly.py`；未改动业务源码、动态断言、公开 Schema 或依赖。

- 导入例外由 basename 匹配收紧为精确相对路径 `transport/actuator_http.py`，仅放行 `http.client` 与 `socket`；其他文件继续禁止全部网络栈。
- 新增禁止生产代码直接调用 `send`/`sendall`/`sendto`/`sendmsg` 的静态断言；HTTP framing 仍只经 `http.client`。
- 保留唯一 request 构造器（`response_gate.py` 与 `actuator_http.py` 各 1）、字面 GET、`follow_redirects=False`、print/eval/exec/compile 与执行/写入入口禁令；未删除断言，未新增 skip/xfail。

### 规则正反两向验证

以真实测试函数对临时合成目录树执行：精确路径内 `socket` + `http.client` 通过；`transport` 下其他模块导入 `socket` 被拒绝；精确路径连接器内调用 `sock.sendall` 或 `sock.sendto` 被拒绝。

### 实际结果（2026-10-20）

| 检查 | 结果 | 退出码 |
| --- | --- | --- |
| 安全静态清单文件 | 65 passed，0.31 秒 | 0 |
| 静态清单 + 回环 HTTP + STDIO 进程 | 88 passed，10.63 秒 | 0 |
| 审计专项（含补充边界） | 33 passed，0.48 秒 | 0 |
| 最终全量 | **706 passed，0 failed**，22.36 秒 | 0 |
| mypy（`--cache-dir=/dev/null`） | 49 个源码文件通过 | 0 |
| 契约校验 | 八组独立 Schema、144+ 合成断言通过 | 0 |
| Ruff lint / format（`--no-cache`） | 通过 / 80 个文件通过 | 0 |
| git diff --check | 通过 | 0 |

动态守卫（禁用地址零连接、混合 DNS 全组拒绝、peer 不匹配零 HTTP 写出）保持通过；未用全局断网夹具替代真实连接器验证。

### 仍未解决

远程目标、真实 TLS/SNI 矩阵、真实 Spring Boot、第三方 MCP 客户端、wheel/sdist、凭据绑定与发布部署仍未验证；上述绿色仅代表本轮审计修复收尾，不代表完整产品验收。后续事项见交接文档 RW-02 至 RW-08。
