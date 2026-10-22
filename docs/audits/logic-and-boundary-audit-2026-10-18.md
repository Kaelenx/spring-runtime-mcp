# 逻辑漏洞与边界条件审计报告

审计日期：2026-10-18。

本轮为纯静态只读审计，审计期间未修改文件、执行测试或连接目标；随后按用户要求新增本报告文件，未修改被审计源码。以下复现载荷均为源码推导，未实际运行。路径均相对于仓库根目录，行号对应审计时的文件内容。编号仅对应本轮清单，不对应仓库历史回归测试中的同名编号。

共确认 **15 项缺陷：9 项 High、6 项 Medium**。没有把单纯使用 Fixture/Mock 的测试判为无效；仅指出替换了关键防护层或遗漏关键断言的具体情况。

## TC-BUG-01: OpenAPI 共享引用可触发指数级展开，输出预算无法保护进程

- **严重级别**：High
- **精确位置**：`src/spring_runtime_mcp/adapters/openapi.py:82-107`、`src/spring_runtime_mcp/adapters/openapi.py:109-144`
- **触发机理与最小复现载荷**：

  `_Expander` 只检查引用深度和当前祖先链，没有全局节点、访问次数或内存预算。不同属性引用同一个 Schema 时，每次重新构造整棵子树。

  以下是构造上游文档的载荷生成逻辑，**未执行**：

  ```python
  schemas = {"S8": {"type": "string"}}
  for i in range(8):
      schemas[f"S{i}"] = {
          "type": "object",
          "properties": {
              f"p{j}": {"$ref": f"#/components/schemas/S{i + 1}"}
              for j in range(50)
          },
      }

  body = {
      "openapi": "3.0.3",
      "paths": {},
      "components": {"schemas": schemas},
  }
  arguments = {"schemaName": "S0", "maxRefDepth": 8}
  ```

  小型输入可以产生约 `50^8` 条叶路径。引用没有环，因此环检测不会阻止展开。

- **预期行为 vs 实际错误行为**：预期在全局分析预算内停止并返回 partial/truncated；实际同步展开先消耗 CPU 和内存，之后才进入脱敏和回包检查，可能使进程失去响应或内存耗尽。
- **测试交叉证据**：`tests/conformance/test_multiversion_fixtures.py:218-235` 验证线性引用深度，没有覆盖共享引用扇出。
- **修复方案草案**：为 `_Expander` 注入共享分析预算，在每次 `expand/_project` 分配节点前扣减；限制累计输出节点和字节，并检查截止时间。命中预算后保留引用占位及明确截断原因。

## TC-BUG-02: 配置能力默认全部启用，调用者筛选被当成授权范围

- **严重级别**：High
- **精确位置**：`src/spring_runtime_mcp/server.py:29-46`、`src/spring_runtime_mcp/tools/inspect_config.py:36-40`、`src/spring_runtime_mcp/adapters/config.py:48-53`
- **触发机理与最小复现载荷**：

  在默认宿主装配下，只要目标开放 env/configprops，调用：

  ```json
  {
    "propertyKeyPattern": "*",
    "includeValues": true
  }
  ```

  `build_services` 将全部 `ENDPOINTS` 作为已启用能力；筛选器仅使用调用者参数，没有与操作者登记的安全属性集合取交集。

- **预期行为 vs 实际错误行为**：`docs/contracts/tool-calling-policy.md:24`、`:44` 要求默认关闭、显式登记字段范围。实际会读取两个完整端点，并允许未登记的、未被启发式规则识别为敏感的标量出站。
- **测试交叉证据**：`tests/integration/test_mcp_host_and_http.py:208-210`、`:235-240` 使用默认 `build_services`，并把配置请求成功作为预期，未验证默认禁用和字段授权。
- **修复方案草案**：`build_services` 默认排除 env/configprops；在可信配置中登记允许属性/前缀，由 `inspect_config` 在读取和输出前执行交集检查。`includeValues` 不能授予新权限。

## TC-BUG-03: 登记 hostname 后缺少 DNS/IP 防护，“重绑定测试”绕过了真实连接器

- **严重级别**：High
- **精确位置**：`src/spring_runtime_mcp/transport/actuator_http.py:32-52`、`src/spring_runtime_mcp/transport/actuator_http.py:99-107`
- **触发机理与最小复现载荷**：

  登记 origin 为 `http://rebind.example.test:18081`，DNS 返回链路本地地址或其他未获准 IP；随后对已登记 `targetId` 调用任一工具。

  连接器直接把 hostname 交给标准库建连，没有解析结果白名单、数字 IP 固定连接或真实 peer 校验；非回环 HTTP 也未被拒绝。

- **预期行为 vs 实际错误行为**：预期在请求写出前拒绝未获准地址，见 `docs/architecture/security-and-sanitization.md:104-117`；实际连接行为由系统 DNS 和路由决定。
- **测试交叉证据**：`tests/security/test_redteam_network.py:85-110` 使用 `SpyTransport`，传入未登记的 `targetId="rebind.example.test"`，在目标检查阶段就退出。**它是有效的未知目标拒绝测试，但无法验证 DNS 重绑定防护。**
- **修复方案草案**：在真实连接器实现解析全组校验、地址固定及写前 peer 校验；补充使用已登记目标、真实连接层的受控网络测试，断言拒绝时 HTTP 写出次数为零。

## TC-BUG-04: STDIO 帧上限在无界读取之后检查，可导致内存耗尽或长期阻塞

- **严重级别**：High
- **精确位置**：`src/spring_runtime_mcp/server.py:169-177`
- **触发机理与最小复现载荷**：

  向 stdin 连续发送超过 65,536 字节且不包含换行符的数据，例如持续发送 `A`。

  `sys.stdin.buffer.readline()` 未提供读取上限，必须等到换行或 EOF 才返回，之后才检查 `MAX_FRAME_BYTES`。

- **预期行为 vs 实际错误行为**：预期读取过程中执行 64 KiB 帧限制；实际超限输入可持续占用内存，且宿主无法处理后续请求。
- **修复方案草案**：使用有界帧读取器，读取到上限加一字节时立即进入超限处理；明确选择终止会话或有界丢弃当前帧，避免无限等待换行。

## TC-BUG-05: 敏感配置脱敏破坏对象类型及状态约束，线上仍可发出非法结果

- **严重级别**：High
- **精确位置**：`src/spring_runtime_mcp/policy/sanitization.py:437-473`；契约：`src/schemas/tools-contract.json:1255-1265`、`:1289-1292`
- **触发机理与最小复现载荷**：

  工具参数：

  ```json
  {"prefix":"spring.datasource"}
  ```

  上游 env：

  ```json
  {
    "activeProfiles": [],
    "propertySources": [{
      "name": "systemProperties",
      "properties": {
        "spring.datasource.password": {"value":"demo"}
      }
    }]
  }
  ```

  配置投影先生成 `effectiveValue=null`、`valueState="notReported"` 和对象类型的 origin。随后脱敏器发现敏感 propertyKey，将 `effectiveValue`、`bindingValue`、`origin` 全部替换为字符串标记，却不更新状态。

- **预期行为 vs 实际错误行为**：预期保持 origin 对象结构，且 `notReported` 对应 null；实际 origin 变成字符串，值变成标记但状态仍为 notReported，违反 outputSchema。在线宿主没有输出 Schema 校验，可能直接发送。
- **测试交叉证据**：`tests/conformance/test_multiversion_fixtures.py:199-214` 只对普通配置调用 `assert_contract`；敏感分支仅断言秘密不出现和值已屏蔽，漏掉 origin 和 bindingState 的违约。
- **修复方案草案**：脱敏采用字段类型感知投影；origin 保留结构、清空敏感叶子；值替换时同步更新状态。敏感分支也必须完整验证 outputSchema。

## TC-BUG-06: 有效健康 503 被当作端点不可用，最需要诊断时丢失证据

- **严重级别**：High
- **精确位置**：`src/spring_runtime_mcp/transport/response_gate.py:145-153`、`src/spring_runtime_mcp/tools/inspect_beans.py:272-288`
- **触发机理与最小复现载荷**：

  对健康工具调用 `{}`，目标返回：

  ```http
  HTTP/1.1 503 Service Unavailable
  Content-Type: application/json

  {"status":"DOWN","components":{"db":{"status":"DOWN"}}}
  ```

- **预期行为 vs 实际错误行为**：`SPEC.md:38`、`:85` 要求保留有效 503 健康证据；实际 gate 在解析前返回 `ENDPOINT_UNAVAILABLE`。如果只修 gate 并保留真实 503，后续 `_check` 仍会拒绝。
- **测试交叉证据**：`tests/integration/test_diagnostic_pairs.py:36-55` 通过 FixtureReader 注入 DOWN JSON，没有经过 503 状态码；`tests/integration/test_mcp_host_and_http.py:231-233` 的正常场景统一返回 200。
- **修复方案草案**：按逻辑健康端点允许有效 503 JSON；观察对象保留真实状态码；同步修改 `_check` 的健康语义判断，禁止全局放行其他端点的 503。

## TC-BUG-07: OpenAPI 输出伪造参数类型、丢弃请求体，却可能宣称展开完整

- **严重级别**：High
- **精确位置**：`src/spring_runtime_mcp/adapters/openapi.py:147-166`、`src/spring_runtime_mcp/adapters/openapi.py:264-277`
- **触发机理与最小复现载荷**：

  参数为 `{"pathPattern":"/x","httpMethod":"POST"}`，文档为：

  ```json
  {
    "openapi":"3.0.3",
    "paths":{
      "/x":{
        "post":{
          "parameters":[{
            "name":"n","in":"query",
            "schema":{"type":"integer"}
          }],
          "requestBody":{
            "content":{
              "application/json":{"schema":{"type":"object"}}
            }
          },
          "responses":{"204":{"description":"ok"}}
        }
      }
    }
  }
  ```

- **预期行为 vs 实际错误行为**：预期返回 integer 参数和请求体；实际参数固定为 string，requestBody 固定为空数组，而且 `expansionComplete` 可以为 true。
- **测试交叉证据**：`tests/conformance/test_multiversion_fixtures.py:218-235` 检查响应 Schema 引用展开，没有检查请求体和参数真实类型。
- **修复方案草案**：`_parameters` 读取并展开真实 schema；实现 requestBody 投影。尚不支持的结构必须记录缺口并使完整性为 false，不能填入确定性假值。

## TC-BUG-08: 配置合并忽略 Context 身份，后遍历的绑定值污染前一个 Context

- **严重级别**：High
- **精确位置**：`src/spring_runtime_mcp/adapters/config.py:180-221`
- **触发机理与最小复现载荷**：

  参数为 `{"prefix":"app","includeValues":true}`。

  env 中存在生效属性 `app.mode=dev`；configprops 为：

  ```json
  {
    "contexts":{
      "a":{"beans":{"cfgA":{
        "prefix":"app","properties":{"mode":"alpha"}
      }}},
      "b":{"beans":{"cfgB":{
        "prefix":"app","properties":{"mode":"bravo"}
      }}}
    }
  }
  ```

  合并只匹配 propertyKey 和 effective=yes。处理 a 后，处理 b 仍命中同一 env 行；绑定值与 Bean 被覆盖，但 `contextId` 因 `old or new` 保留 a。

- **预期行为 vs 实际错误行为**：预期保留两个独立绑定记录；实际可能生成 `contextId=a`、`configurationBean=cfgB`、`bindingValue=bravo` 的混合证据，且不报告冲突。
- **测试交叉证据**：`tests/conformance/test_multiversion_fixtures.py:183-214` 检查单条可见值与屏蔽值，没有验证多个 Context 的同名属性。
- **修复方案草案**：以 `(contextId, configurationBean, propertyKey)` 标识绑定；全局 env 生效事实与 Context 绑定事实分开保存，不能以 propertyKey 唯一合并。

## TC-BUG-09: 超时只取消线程等待，未终止真实 HTTP 读取

- **严重级别**：High
- **精确位置**：`src/spring_runtime_mcp/transport/actuator_http.py:55-64`、`:79-97`；`src/spring_runtime_mcp/transport/response_gate.py:104-116`
- **触发机理与最小复现载荷**：

  timeout=5；受控目标声明较大 Content-Length，然后每隔 4 秒发送一个字节。

  HTTP 交换在线程中运行。外层 wait_for 超时取消 await，但不能停止该线程；持续小流量又能让 socket 的单次等待超时不断重置。

- **预期行为 vs 实际错误行为**：预期到期关闭 socket、停止读取；实际调用可已返回 TARGET_TIMEOUT，后台仍持有连接和线程。连续调用可积累在途读取，退出也可能等待线程结束。
- **测试交叉证据**：`tests/integration/test_mcp_host_and_http.py:350-364` 只验证立即返回大响应时的读取长度，没有覆盖慢速分块与超时后的资源存活。
- **修复方案草案**：为交换实现绝对截止时间和可关闭的连接所有权；取消时主动中断 socket。增加超时后连接、线程及读取次数停止的断言。

## TC-BUG-10: 非法健康组件和自定义异常状态被静默过滤成完整成功

- **严重级别**：Medium
- **精确位置**：`src/spring_runtime_mcp/adapters/health.py:12-27`、`:90-113`
- **触发机理与最小复现载荷**：

  默认健康查询分别接收：

  ```json
  {"status":"UP","components":{"db":null}}
  ```

  ```json
  {"status":"UP","components":{"db":{"status":"FATAL"}}}
  ```

  第一种被 `_children` 直接过滤，没有产生 gap；第二种因 FATAL 不属于 `_ABNORMAL` 而被隐藏。

- **预期行为 vs 实际错误行为**：非法组件应导致 partial；自定义非正常状态应保留，见 `SPEC.md:85`。实际两种情况都可能返回空 items、`status=ok`、`coverage=complete`。
- **测试交叉证据**：`tests/integration/test_diagnostic_pairs.py:38-55` 只覆盖 UP/DOWN。
- **修复方案草案**：遍历时对非法子节点记录结构缺口；默认只隐藏明确正常状态，保留自定义状态，不将未知状态归入正常集合。

## TC-BUG-11: 启动总耗时被计算成最长步骤，并错误标为 timelineSpan

- **严重级别**：Medium
- **精确位置**：`src/spring_runtime_mcp/adapters/startup.py:158-166`
- **触发机理与最小复现载荷**：

  两个根步骤分别在 `00:00:00–00:00:01`、`00:00:02–00:00:03` 执行，duration 都为 `PT1S`，使用不同 id、无 parentId。

- **预期行为 vs 实际错误行为**：预期时间线跨度为 3,000 ms；实际 `max(durations)` 返回 1,000 ms，并标记 `durationBasis=timelineSpan`。
- **测试交叉证据**：`tests/conformance/test_multiversion_fixtures.py:48-66` 主要验证单步耗时；`tests/fixtures/conformance/boot-3.4-servlet/startup.json:7-19` 的根步骤恰好包围子步骤，无法区分两个算法。
- **修复方案草案**：依据可信开始/结束时间计算跨度；无法确定时返回 unknown。不要把最长单步时长放进 timelineSpan 字段。

## TC-BUG-12: tools/call.name 为数组或对象时触发未捕获 TypeError

- **严重级别**：Medium
- **精确位置**：`src/spring_runtime_mcp/server.py:104-112`
- **触发机理与最小复现载荷**：

  ```json
  {
    "jsonrpc":"2.0",
    "id":1,
    "method":"tools/call",
    "params":{"name":[],"arguments":{}}
  }
  ```

  类型检查进入错误分支后，代码再次执行 `name in catalog`，对不可哈希的 list/dict 抛出 TypeError。

- **预期行为 vs 实际错误行为**：预期输入错误信封；直接调用 dispatch 时异常向外传播，STDIO 下则被外层宽泛捕获成 `-32603 INTERNAL_ERROR`，将客户端输入错误误报为系统故障。
- **测试交叉证据**：`tests/integration/test_mcp_host_and_http.py:459-468` 的未知工具用例仅使用字符串，无法覆盖此分支。
- **修复方案草案**：先单独拒绝非字符串 name，再做字典查询；测试 null、数组、对象与未知字符串的不同错误路径。

## TC-BUG-13: 已登记非 local 目标失败后，错误信封错误归属 local

- **严重级别**：Medium
- **精确位置**：`src/spring_runtime_mcp/tools/diagnose_health.py:61-72`、`src/spring_runtime_mcp/tools/check_migrations.py:74-85`、`src/spring_runtime_mcp/tools/inspect_beans.py:667-678`
- **触发机理与最小复现载荷**：

  宿主登记 `targetId=demo`，调用：

  ```json
  {"targetId":"demo"}
  ```

  目标返回 401，或发生超时。异常分支调用 `failed(...)` 时未传 query.target_id；`failed` 的默认目标为 local，见 `src/spring_runtime_mcp/tools/inspect_beans.py:239-251`。

- **预期行为 vs 实际错误行为**：预期错误归属于 demo；实际 `source.targetId=local`。聚合诊断或审计日志会关联错误实例。
- **测试交叉证据**：`tests/conformance/test_server_contract.py:44` 使用 local，且错误断言 `:109-119` 未验证非默认目标身份。
- **修复方案草案**：验证成功后保留目标身份，所有异常分支显式传入；补充非 local 目标的成功、授权失败和超时测试。

## TC-BUG-14: 续页重置快照 TTL，可读取超过绝对有效期的旧证据

- **严重级别**：Medium
- **精确位置**：`src/spring_runtime_mcp/tools/support.py:106-120`、`src/spring_runtime_mcp/snapshots.py:93-95`
- **触发机理与最小复现载荷**：

  同一查询有三条结果，maxResults=1：

  1. t=0 读取第一页，生成游标 A。
  2. t=59 使用 A，第二页生成游标 B。
  3. t=118 使用 B，仍能读取第三页。

  续页把剩余旧证据重新 put，并重新计算 `expires_at=now+60`。

- **预期行为 vs 实际错误行为**：快照 TTL 60 秒应约束同一份观察证据，见 `docs/contracts/tool-calling-policy.md:32`；实际每次续页续命，原快照超过 60 秒仍可读取。
- **测试交叉证据**：`tests/unit/test_responses_snapshots.py:59-76` 验证单 token 的绝对过期，没有经过 Handler 的重新 put 链路。
- **修复方案草案**：continuation 继承原快照截止时间，或所有游标引用同一个不可变快照及分页偏移；续页不得重置观察有效期。

## TC-BUG-15: 一个迁移端点结构异常，会丢弃另一端点已取得的有效结果

- **严重级别**：Medium
- **精确位置**：`src/spring_runtime_mcp/tools/check_migrations.py:62-85`、`src/spring_runtime_mcp/adapters/migrations.py:290-302`
- **触发机理与最小复现载荷**：

  参数 `{"engine":"auto"}`。两个端点均返回 HTTP 200：

  Flyway：

  ```json
  {
    "contexts":{
      "application":{
        "flywayBeans":{
          "fw":{"migrations":[{"version":"1","state":"SUCCESS"}]}
        }
      }
    }
  }
  ```

  Liquibase：

  ```json
  {"contexts":null}
  ```

  HTTP 读取均成功，随后统一归一化。Liquibase 解析抛 ValueError，最外层捕获直接返回整体失败。

- **预期行为 vs 实际错误行为**：预期保留 Flyway 记录，Liquibase 标为缺失/非法证据并返回 partial；实际 Flyway 结果一起被丢弃，data=null，且解析失败分支没有准确端点身份。
- **测试交叉证据**：`tests/conformance/test_multiversion_fixtures.py:70-91` 覆盖两个引擎同时正常；现有读取异常隔离位于 `src/spring_runtime_mcp/tools/check_migrations.py:37-51`，未覆盖“HTTP 成功但单域结构异常”。
- **修复方案草案**：按引擎分别读取和归一化，在每个引擎边界记录结构化错误，再合并可用结果；只有所有域均不可用时才返回整体失败。
