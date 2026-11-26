# MetricLedger Planner Comparison

> 生成时间：`2026-11-26T06:36:20+00:00`
> 输入只包含指标观察摘要、允许维度和剩余查询预算；模型不会接触原始事件行。

## 场景

- 支持调查场景：**18**
- 候选维度暴露：`True`
- 原始事件行暴露：`False`

## 启发式 planner（本轮实测）

| 指标 | 值 |
|---|---:|
| 用例数 | 18 |
| 失败率 | 0.00% |
| 回退率 | 0.00% |
| P50 延迟 | 0.014 ms |
| P95 延迟 | 0.037 ms |
| 最大延迟 | 0.037 ms |
| 完整 Token 用量 | 18 |
| Planner Token | 0 |

## OpenAI-compatible planner

**NOT_RUN**

原因：No METRICLEDGER_PLANNER_BASE_URL and METRICLEDGER_PLANNER_MODEL were configured. No model latency or quality can be claimed.

因此本轮没有模型失败率、延迟或回退率结论。

## 口径

- 失败：planner 没有返回任何可清洗的候选维度。
- 回退：模型调用异常、超时、HTTP 错误或输出无有效维度，系统使用启发式结果。
- 场景为仓库内确定性合成场景，不代表外部榜单或生产 SLA。
