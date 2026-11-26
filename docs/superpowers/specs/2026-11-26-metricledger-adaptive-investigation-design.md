# MetricLedger：自适应可信调查 Agent 设计

日期：2026-11-26

## 1. 结论

本轮把 MetricLedger 从“一次规划、顺序执行”的有界调查器，升级为“最多两轮规划、依据受控证据重排假设”的自适应调查 Agent。

升级不改变可信边界：

- 模型仍然只选择允许的维度，不生成 SQL、数字、Claim 或最终答案。
- 每次查询仍然只访问准入阶段固定的语义包和快照。
- 答案仍然只由通过证据校验的 Claim 渲染。
- 模型、查询、Token 和时间预算仍然由执行层控制。
- 模型失败时降级为确定性规划，不把技术失败伪装成业务结论。

本轮解决以下已确认缺口：

1. 首轮诊断结果没有反馈给 planner。
2. `ObservationSummary.evidence_summaries` 实际为空。
3. `BudgetUsage.planner_tokens_used` 被写死为 `0`。
4. 模型延迟、Token、回退原因和每轮决策没有进入 API、账本和监控。
5. `CONTEXT_DRIFT` 仅存在于设计文档，没有完整运行时校验。
6. SQLite 不可变账本存在“先查再插”的并发竞态。
7. 真实模型评测缺少重复假设、越界维度、异常输出、超时和预算耗尽场景。

## 2. 目标

- 调查最多执行两轮 planner。
- 首轮执行后，用受控观测摘要触发第二轮重排。
- 第二轮只能从尚未执行的候选维度中选择。
- 记录每轮 planner 的真实结果、延迟、Token 和回退原因。
- 记录每个调查步骤的维度、状态、证据引用和停止原因。
- 查询预算按“当前周期 + 对比周期”成对预留。
- 在执行层校验语义包和快照上下文没有漂移。
- 把不可变审计账本写入改为原子事务。
- 在 API、账本、Prometheus 和 React 工作台中展示调查轨迹。
- 增加自适应、异常、安全和预算评测。

## 3. 非目标

- 不引入多 Agent 角色扮演。
- 不允许模型生成自由 SQL、归因权重或答案数字。
- 不实现无限轮自主循环。
- 不引入 Redis、任务队列、分布式 planner 或跨节点调度。
- 不实现通用数据源连接器、语义发布平台或字段级血缘。
- 不把一次模型调用成功解释为通用模型能力或线上 SLA。
- 不硬杀正在执行的 DuckDB 查询；时间预算采用同步调用边界上的协作式检查。

## 4. 核心不变量

1. 一次分析只使用准入阶段固化的语义哈希、快照 manifest、权限范围和时区。
2. planner 每轮最多返回三个候选维度，且只能来自当前未执行的允许维度。
3. planner 不能新增指标、维度、过滤值、表名、字段名、SQL 或答案数字。
4. 模型观测不得包含原始事件行、SQL、租户标识或敏感维度分组值。
5. 第二轮 planner 只能重排剩余维度，不能重复已完成维度。
6. 每个维度执行前必须同时具备当前周期和对比周期的查询预算。
7. 查询失败、模型失败和空结果不得转换成业务零值。
8. 上下文漂移后不得继续使用旧上下文完成分析或返回答案。
9. Claim 只能在同一次分析的固定上下文中引用已完成证据。
10. 模型 Token 使用未知时不得把未知值记录为 `0`。

## 5. 架构

### 5.1 `InvestigationLoop`

负责状态机、轮次和预算控制，不负责模型通信、SQL 生成或 Claim 渲染。

职责：

- 创建首轮观察。
- 调用 planner。
- 校验并执行 planner 返回的假设。
- 根据执行结果决定继续、触发第二轮或停止。
- 汇总调查轨迹。

依赖：

- `SemanticRegistry`
- `QueryCompiler`
- `DuckDbEngine`
- `HypothesisPlanner`
- `ContextIntegrityGuard`
- `ObservationBuilder`
- `AnalysisContext`

### 5.2 `ObservationBuilder`

把已完成步骤转换为受控 `InvestigationObservation`。

每条 `EvidenceObservation` 包含：

- `dimension_id`
- `status`
- `result_ids`
- `material_change`
- `concentration_share`
- `group_count`
- `top_group`
- `issue_codes`

`top_group` 只在该维度不属于敏感维度时填写。模型看不到 `bounded_rows`。

### 5.3 `HypothesisPlanner`

统一返回 `PlannerDecision`，不再由调用方读取 planner 的可变 `last_trace`。

`PlannerDecision` 包含：

- 有序 `hypotheses`
- `PlannerAttemptTrace`
- 可选的 `ModelUsage`

实现：

- `HeuristicHypothesisPlanner`：固定顺序，Token 为 `0`，用于离线执行和回退。
- `OpenAICompatibleHypothesisPlanner`：调用 OpenAI-compatible `chat/completions`，只接受结构化 JSON。

### 5.4 `ContextIntegrityGuard`

在执行前校验：

- 当前语义包规范化哈希等于 `AnalysisContext.semantic_bundle_hash`。
- 当前快照 manifest 等于 `AnalysisContext.snapshot_manifest_hash`。

校验位置：

- 每次 planner 调用前。
- 每次业务查询前。

发现漂移立即返回 `CONTEXT_DRIFT`，不换语义包或快照续跑。

### 5.5 `InvestigationTrace`

挂载到 `AnalysisResponse`，随 SQLite 账本一起持久化。

包括：

- 每轮输入观察。
- 每轮 planner 尝试。
- 每个执行步骤。
- 已完成和未完成维度。
- 最终停止原因。
- 是否发生 planner 降级。

## 6. 调查状态机

### 6.1 首轮

1. 从 Admission 上下文和主指标结果创建首轮观察。
2. 调用 planner。
3. 清洗输出：去重、去越界、去已执行维度、最多保留三个。
4. 依次执行假设。
5. 每个维度产生一个 `InvestigationStepTrace`。

### 6.2 第二轮触发条件

以下条件全部满足时进入第二轮：

- 当前尚未得到终止结论。
- 已完成首轮 planner。
- `max_planner_rounds >= 2`。
- 至少存在一个未执行候选维度。
- 剩余查询预算至少为 `2`。
- 时间预算尚未耗尽。
- 已报告的 Token 用量没有超过预算。
- 如果上一次模型调用未返回 usage，第二轮仍可执行，但必须使用确定性 planner，不再发起模型调用。

### 6.3 第二轮

1. 用首轮真实观测构建 `round_number=2` 的观察。
2. 调用同一 planner。
3. 只保留尚未执行的维度。
4. 再次按受控顺序执行。
5. 不允许第三轮。

### 6.4 确定性回退

模型超时、HTTP 错误、非法 JSON、越界维度、重复维度或空候选时：

- 记录结构化 `PlannerAttemptTrace`。
- 使用剩余维度的语义包默认顺序。
- 设置 `fallback_used=true`。
- 不因回退中断已有证据。

回退后的顺序仍受轮次、查询和时间预算约束。

### 6.5 终止原因

- `concentrated_contribution`
- `ratio_diagnostic_complete`
- `all_candidates_observed`
- `no_remaining_candidates`
- `planner_round_limit`
- `budget_exhausted`
- `planner_token_budget_exhausted`
- `time_budget_exhausted`
- `context_drift`
- `diagnostic_failure`

## 7. 观测语义

`EvidenceStatus`：

- `MATERIAL_CHANGE`：查询成功，存在可用于继续诊断的变化。
- `NO_MATERIAL_CHANGE`：查询成功，但该维度没有可计算的变化。
- `NO_COMPARABLE_GROUPS`：当前和对比周期没有可比较分组。
- `QUERY_FAILED`：查询或编译失败，没有产生可用证据。

可加指标：

- `concentration_share` 表示最大绝对分组变化占总绝对分组变化的比例。
- 达到 `0.5` 时终止为 `concentrated_contribution`。
- 未达到时记录观测并继续其他假设。

比率指标：

- 完成一个有效维度诊断后终止为 `ratio_diagnostic_complete`。
- 没有可比较分组或查询失败时继续剩余假设。
- 不产生算术贡献 Claim。

`ObservationBuilder` 不复制 `bounded_rows`，不接收 API 密钥，不进入数据库。

## 8. Planner 协议

### 8.1 输入

```json
{
  "observation": {
    "analysis_id": "an_...",
    "round_number": 2,
    "metric_id": "paid_gmv",
    "metric_kind": "ADDITIVE",
    "question_kind": "INVESTIGATE",
    "candidate_dimensions": ["channel", "category"],
    "completed_dimensions": ["region"],
    "remaining_queries": 4,
    "remaining_planner_tokens": 1500,
    "evidence": []
  },
  "dimensions": [
    {
      "dimension_id": "channel",
      "display_name": "渠道"
    }
  ]
}
```

### 8.2 输出

```json
{
  "hypotheses": [
    {
      "dimension_id": "channel",
      "rationale_code": "follow_up_material_change",
      "priority": 0
    }
  ]
}
```

### 8.3 清洗规则

- 非对象项丢弃。
- 不符合 `Hypothesis` 模型的项丢弃。
- 非当前候选维度丢弃。
- 已执行维度丢弃。
- 重复维度只保留第一次出现。
- 每个 planner 决策最多保留三个维度。
- planner 不返回 `STOP`；安全停止条件由执行层决定。

## 9. 预算与计时

### 9.1 查询预算

调查中的一次维度诊断需要两次查询：

1. 当前周期分组查询。
2. 对比周期分组查询。

执行前一次性检查 `remaining_queries >= 2`，不为无法完成配对的维度启动查询。

实际执行的查询计入 `queries_used`。当前查询失败时不再执行对比查询，失败查询仍计入预算。

### 9.2 Planner 轮次

`BudgetPolicy` 新增：

- `max_planner_rounds: int = 2`，允许值为 `1` 或 `2`。

客户端可以提高首版默认值到 `2`，但不能超过 `2`。

### 9.3 Token 预算

使用模型响应中的 `usage.total_tokens`。

`BudgetUsage` 新增：

- `planner_tokens_used: int | None`
- `planner_usage_complete: bool`

规则：

- 启发式 planner 使用 `0`，并标记 usage 完整。
- 模型返回 usage 时累加真实 Token。
- 模型不返回 usage 时记录 `None` 和 `planner_usage_complete=false`。
- usage 未知后禁止下一次模型调用，但允许确定性 planner 完成剩余工作。
- Token 预算在模型调用后结算；OpenAI-compatible 端点无法保证调用前硬截止。

### 9.4 时间预算

分析开始时记录单调时钟截止时间。

在以下位置检查：

- 每次 planner 调用前。
- 每次业务查询前。
- 每个维度开始前。

DuckDB 调用是同步执行，当前版本不能安全中断正在运行的查询。若查询返回时已经超过预算，不再启动后续 planner 或查询。

## 10. 上下文漂移

### 10.1 语义漂移

运行时从当前 `SemanticBundle` 重新计算规范化哈希，与 `AnalysisContext.semantic_bundle_hash` 比较。

### 10.2 快照漂移

通过 `SnapshotRepository` 按 `snapshot_id` 重新读取 manifest 哈希，与 `AnalysisContext.snapshot_manifest_hash` 比较。

### 10.3 漂移响应

漂移发生后：

- 停止 planner 和查询。
- 返回 `Completeness.NONE`。
- `answer` 为 `null`。
- `claims` 为空。
- `investigation.stop_reason=context_drift`。
- 保留已执行 SQL 审计作为故障取证。
- 不把漂移前产生的部分 Claim 暴露为答案。

## 11. 原子不可变账本

### 11.1 写入

`SqliteLedger.save` 使用单个 `BEGIN IMMEDIATE` 事务完成：

- 分析记录写入或重复校验。
- 证据记录写入或重复校验。
- Claim 证据边写入。

### 11.2 幂等规则

- 相同 `analysis_id` 和相同规范化 payload：幂等成功。
- 相同 `analysis_id` 和不同 payload：抛出不可变错误。
- 相同 `result_id` 和相同 payload：幂等成功。
- 相同 `result_id` 和不同 payload：抛出不可变错误。

### 11.3 并发规则

并发写入不得依赖事务外的“先 SELECT 再 INSERT”。冲突由数据库唯一约束发现，再在同一事务内读取并比较既有 payload。

## 12. API 与前端

### 12.1 API 模型

新增：

- `EvidenceObservation`
- `InvestigationObservation`
- `PlannerAttemptTrace`
- `InvestigationStepTrace`
- `InvestigationRoundTrace`
- `InvestigationTrace`
- `ModelUsage`

`AnalysisResponse` 新增可选 `investigation`。

`BudgetUsage.elapsed_ms` 改为正式分析开始到响应构建完成的墙钟时间，包含 planner 和查询。

### 12.2 React 工作台

现有单页增加：

- 调查轮次时间线。
- 每轮选择的维度。
- 每个维度的观测状态。
- Planner 成功、回退和原因。
- 查询、Token、usage 完整性和实际耗时。
- `CONTEXT_DRIFT` 和预算耗尽提示。

不新增管理后台、报表页或多页面导航。

### 12.3 Prometheus

新增：

- `metricledger_planner_calls_total{outcome}`
- `metricledger_planner_tokens_total`
- `metricledger_investigation_steps_total{status}`
- `metricledger_context_drift_total`

## 13. 测试设计

### 13.1 调查循环

- 首轮无集中结论，第二轮重排剩余维度后得到集中结论。
- 第二轮不能重复首轮维度。
- 第三轮不会被调用。
- 所有候选维度完成后返回 `all_candidates_observed`。
- 仅剩一次查询时不启动维度诊断。
- 当前查询失败时不执行对比查询，并从剩余维度继续。
- 无变化维度不会错误地终止其他维度调查。
- 比率指标不产生算术贡献。

### 13.2 Planner

- 模型返回合法假设。
- 模型返回越界、重复、非法字段和多余维度。
- 模型返回空候选。
- 模型超时。
- 模型 HTTP 错误。
- 模型非法 JSON。
- 模型 usage 正常返回。
- 模型 usage 缺失后禁止第二次模型调用。
- 回退使用确定性剩余顺序。
- 敏感维度观测不包含具体分组值。

### 13.3 上下文与预算

- planner 调用前语义哈希变化产生 `CONTEXT_DRIFT`。
- 查询前快照 manifest 变化产生 `CONTEXT_DRIFT`。
- 漂移后答案、Claim 为空，SQL 审计保留。
- 查询、planner 轮次和墙钟预算不越界。
- Token 使用未知不记录为 `0`。

### 13.4 账本

- 相同分析重复写入幂等。
- 相同分析不同 payload 拒绝。
- 相同证据重复写入幂等。
- 相同证据不同 payload 拒绝。
- 两个线程并发写相同分析不产生未处理竞态。
- 多线程写相同证据只产生一条记录。

### 13.5 评测

离线评测在现有场景上至少增加以下类别：

- 自适应第二轮重排。
- 重复假设。
- 越界假设。
- planner 超时。
- usage 缺失。
- Token 预算耗尽。
- 上下文漂移。
- 敏感观测脱敏。
- 原子重复写入。

场景总数至少达到 `72`，原有安全判定继续生效。

## 14. 验收标准

- 后端测试全绿，覆盖率不低于本轮开始前的 `93%`。
- 新增测试覆盖本规格第 13 节全部行为。
- 离线评测至少 `72` 个场景且完整系统全部成功。
- 不安全答案、越权、空结果冒充零、证据违规和预算违规均为 `0`。
- `npm --prefix web test -- --run` 通过。
- `npm --prefix web run build` 通过。
- `InvestigationTrace` 能在 API 响应、账本读取和前端测试中验证。
- 配置真实 OpenAI-compatible 端点时，至少运行一次双轮 planner 冒烟；没有凭据时报告 `NOT_RUN`。
- 新增 Prometheus 指标通过文本格式和现有监控测试。
- GitHub Actions 的 backend、frontend、config 和 container 全部通过。
- 同步更新 stale acceptance 数字；只写最终实际运行的证据。

## 15. 兼容与迁移

- Pydantic 新字段使用默认值，旧账本记录仍可读取。
- `ObservationSummary` 的字符串证据列表改为结构化观测；旧字段不再作为模型输入。
- `HypothesisPlanner.propose` 改为返回 `PlannerDecision`，测试和所有实现同步迁移。
- 现有启发式顺序保持为默认回退顺序。
- 现有评测标签和安全断言不降级。

## 16. 交付边界

本轮完成后，MetricLedger 将具备可验证的两轮自适应调查、真实模型用量、结构化降级、运行时上下文漂移防护、原子审计和可视化调查轨迹。

仍不能据此声称：

- 所有自然语言问题都能正确规划。
- 模型结果优于其他产品或公开榜单。
- SQLite 单机账本和单进程限流满足分布式生产环境。
- 合成数据评测等同于真实企业收益。
- 协作式时间预算能够中断已经进入 DuckDB 的同步查询。
