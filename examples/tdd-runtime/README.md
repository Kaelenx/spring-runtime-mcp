# Shop 真实项目隔离验收

2026-10-22 在用户明确批准后完成。实际版本：Spring Boot **3.5.16**、Java **21**、Spring MVC、既有 JWT 安全链、临时 PostgreSQL **16**。结果见 [verification.json](verification.json)，审查关闭证据见[综合修复报告](../../docs/audits/comprehensive-remediation-2026-10-22.md)。

目标来源是 `<TDD checkout>` 的已提交 S8 版本 `b7c54641df80ad9502ee1e3cc95a21d494cdd75d`。原工作区新增的看板 Controller 依赖尚无实现 Bean 的 `DashboardService` 接口，整应用启动失败；源码与测试注明处于红阶段。本次没有实现、屏蔽或删除这些业务代码，改用提交版本的临时副本验收诊断工具。不能据此宣称原工作区的业务或测试已完成。

## 隔离与鉴权

- `actuator-profile.patch` 只在显式 Maven profile `runtime-diagnostics` 下引入 Boot BOM 管理的 Actuator；该补丁已应用到原 TDD 的 `pom.xml`，默认构建仍不含此新增依赖。未修改原业务安全链、启动代码或应用配置。
- `application-runtime-diagnostics.properties` 是外置 overlay。`DiagnosticsLauncher.java` 使用真实 `ShopApplication` 和项目已有 Testcontainers 测试依赖，先创建临时 PostgreSQL，再启动副本；数据库只绑定回环。独立业务/管理端口均绑定 `127.0.0.1`，实际验收由启动器将管理端口也设为随机端口。
- 只暴露 health、beans、conditions、mappings、flyway、startup，设置全局 `read-only`，env/configprops 关闭。`BufferingApplicationStartup` 在启动前设置，MCP 仅 GET startup。
- Python 启动脚本移除既有应用/JVM覆盖变量，临时生成 JWT 密钥与令牌，仅放入子进程环境。现有 JWT 过滤器认证令牌；MCP 通过 `--token-env` 绑定凭据。没有放开匿名权限，也不调用登录或其他业务接口。
- 原始响应和框架日志不写入报告、fixture 或 transcript；保存的仅为通过契约、出口秘密检查和回包预算检查的状态摘要。默认 pytest 不运行本脚本，也不需要 Docker/JDK。

## 实际结果

官方 SDK 发现八个工具；匿名 Bean 查询被拒绝。授权后七次工具调用覆盖六个 Actuator 端点：

| 查询 | 结果与证据边界 |
| --- | --- |
| authService | 找到真实 Bean；图、关联/输出预算和源码目录缺失导致 partial |
| missingReviewBean | 读取条件报告；缺少源码关联及未建模条件语法，保持 partial |
| POST /api/auth/login 路由 | 匹配一条 MVC 注册记录，完整；实际出站只读取 mappings |
| db 健康组件 | 整体状态可见，详情隐藏；partial，totalMatched=null |
| Flyway | 观察到版本 1、APPLIED，完整 |
| 启动步骤 | Boot 版本 3.5.16，观察到步骤；数量/深度边界明确截断 |
| Bean + 路由 + Flyway 漂移 | 预期对象无缺失；Bean 全图不完整，整体 partial |

验收结束后应用进程退出，带 `spring-runtime-mcp.validation=tdd` 标签的容器剩余数量为零。临时源码副本已清理。该样本不覆盖 OpenAPI、配置值诊断、WebFlux、其他 Boot 版本、第三方桌面客户端或真实 TLS/SNI 矩阵；没有运行 TDD 的完整 Maven 测试集，也没有发布部署。

## 复现

这是会启动新实例和临时容器的显式验收，需要相应授权。要求 Java 21、Maven、Docker、可用的 `postgres:16-alpine` 与项目测试依赖。先在 MCP 仓库执行 `uv sync --dev --locked`。以下命令创建已提交源码副本，保留原工作区业务改动：

```bash
MCP_REVIEW_ROOT=/path/to/spring-runtime-mcp
TDD_REVIEW_SOURCE=/path/to/TDD
TDD_REVIEW_REVISION=b7c54641df80ad9502ee1e3cc95a21d494cdd75d
TDD_REVIEW_DIR=$(mktemp -d /tmp/spring-runtime-tdd-XXXXXXXX)
git -C "$TDD_REVIEW_SOURCE" archive "$TDD_REVIEW_REVISION" | tar -x -C "$TDD_REVIEW_DIR"
git -C "$TDD_REVIEW_DIR" apply "$MCP_REVIEW_ROOT/examples/tdd-runtime/actuator-profile.patch"
mvn -f "$TDD_REVIEW_DIR/pom.xml" -Pruntime-diagnostics -DskipTests test-compile dependency:build-classpath \
  -Dmdep.includeScope=test -Dmdep.outputFile="$TDD_REVIEW_DIR/classpath.txt"
uv run --project "$MCP_REVIEW_ROOT" --no-sync python "$MCP_REVIEW_ROOT/scripts/check_tdd_runtime.py" \
  --project "$TDD_REVIEW_DIR" --classpath-file "$TDD_REVIEW_DIR/classpath.txt" \
  --source-revision "$TDD_REVIEW_REVISION"
docker ps -a --filter label=spring-runtime-mcp.validation=tdd --format '{{.ID}} {{.Status}}'
rm -r -- "$TDD_REVIEW_DIR"
```

启动器正常结束或收到终止信号时关闭 Spring 上下文及容器；强制杀死 JVM 后应单独检查上述标签。报告不包含环境令牌、数据库连接串或原始响应。
