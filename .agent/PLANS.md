# 2026-10-22 综合审查修复与 TDD 真实验收

目标：修复 docs/audits/comprehensive-review-2026-10-22.md 中 CR-01 至 CR-13，补齐与本轮有关的工程交付缺口，并使用 `<TDD checkout>` 的真实 Shop 应用独立验收。

边界：公开工具 Schema、八工具排序、只读和脱敏约束保持；不发布、不扩大回环目标边界。TDD 的任务文件和看板业务代码为用户改动，保留；真实隔离方案已获明确批准，不重复请求。

1. [完成] 已准备并校验诊断 profile/隔离方案；用户明确批准新增可选 Actuator、JWT 回环实例和临时 PostgreSQL，已应用仅 Maven profile 补丁。
2. [完成] 统一 Services/诊断信封与调用预算；SDK 最终工具帧、ID、两个读取槽和总时限均有回归。
3. [完成] 修复 OpenAPI 组合、包装引用、3.0/3.1 语义和未知结构/方言降级。
4. [完成] 修复迁移证据/校验和/应用状态、Bean 遮蔽及健康可见性；离线回归通过。
5. [完成] 修复进程测试时序和发布门禁；补齐 py.typed、uv.lock 与当前文档。
6. [完成] 离线全量 782 passed，类型 51 文件及契约/lint/格式通过。隔离安装官方 SDK 两种模式通过。原 TDD 红阶段缺 DashboardService 实现 Bean，改用提交 b7c54641 的临时副本完成 Boot 3.5.16/JWT/PostgreSQL 验收；原工作区仅新增已批准 pom profile。
7. [完成] 综合修复报告已写入；最终 wheel/sdist 资源及安装来源验证通过，官方 SDK 两种模式通过；本地文档链接和差异检查通过。临时源码、classpath、进程和本轮数据库容器均已清理；未发布。
