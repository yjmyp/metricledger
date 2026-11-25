# MetricLedger Mixed-Load Benchmark

> 生成时间：`2026-11-25T16:34:35+00:00`
> 本报告来自真实运行中的 HTTP API、真实 DuckDB 查询和 SQLite 账本；不是模拟请求。

## 负载配置

- Base URL：`http://127.0.0.1:8011`
- 并发：**12**
- 完成请求：**120**
- 端到端持续时间：**11.274s**
- 吞吐：**10.644 req/s**
- HTTP/传输错误数：**0**
- 预期澄清响应数：**12**
- 非预期业务错误数：**0**
- 错误率：**0.00%**

## 延迟

| 指标 | 实测值 |
|---|---:|
| P50 | 155.458 ms |
| P95 | 3893.734 ms |
| P99 | 4823.195 ms |
| Max | 5645.419 ms |

## 各路由

| 路由 | 请求数 | P50 | P95 | P99 | Max |
|---|---:|---:|---:|---:|---:|
| `catalog` | 12 | 17.495 ms | 159.761 ms | 159.761 ms | 159.761 ms |
| `clarify` | 12 | 38.051 ms | 196.704 ms | 196.704 ms | 196.704 ms |
| `eval` | 12 | 23.122 ms | 196.384 ms | 196.384 ms | 196.384 ms |
| `health` | 12 | 20.446 ms | 165.614 ms | 165.614 ms | 165.614 ms |
| `investigate` | 36 | 2827.032 ms | 4823.195 ms | 5645.419 ms | 5645.419 ms |
| `readback` | 12 | 27.382 ms | 156.281 ms | 156.281 ms | 156.281 ms |
| `value` | 24 | 951.193 ms | 2205.076 ms | 2367.043 ms | 2367.043 ms |

## 数据库证据

| 存储 | 实测计数 |
|---|---|
| DuckDB 查询数 | 168 |
| DuckDB 查询耗时 | 113169.791 ms |
| DuckDB 错误数 | 0 |
| SQLite 写入数 | 72 |
| SQLite 写入耗时 | 1477.953 ms |
| SQLite 读取数 | 12 |
| SQLite 读取耗时 | 109.460 ms |
| SQLite 错误数 | 0 |

## 瓶颈判断

- 状态：**MEASURED**
- 首要候选：`duckdb`
- DuckDB 查询耗时 / 请求总耗时：**88.83%**
- SQLite 账本耗时 / 请求总耗时：**1.25%**
- 最慢路由：`investigate`，P95 4823.195 ms

> 该比例是本地归因启发式：用累计请求延迟作分母，不是 profiler、分布式 trace 或线上容量结论。

## 限制

This is a local mixed HTTP measurement against one API process and a local DuckDB/SQLite setup. It is not a production SLA.
