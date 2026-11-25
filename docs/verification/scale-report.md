# MetricLedger Scale Benchmark

> 本报告通过真实 DuckDB 执行参数化只读查询。数据是由仓库脚本生成的事件级快照，不是生产流量。
> 生成时间：`2026-11-25T16:34:24+00:00`

## 真实 DuckDB 查询规模

- 数据库：`data/metricledger-scale.duckdb`
- 文件大小：**19.26 MiB**
- 事件总数：**2,000,000**
- 快照表：`2`

| 快照 | 事件数 | 日期下限 | 日期上限 |
|---|---:|---|---|
| `batch_20261116_2300` | 1,000,000 | `2026-10-01` | `2026-11-16` |
| `batch_20261123_2300` | 1,000,000 | `2026-10-01` | `2026-11-23` |

租户事件分布：`demo`=1,800,000，`tenant_b`=200,000

## 实际分析请求

| 指标 | 实测值 |
|---|---:|
| 准入状态 | `ADMITTED` |
| 完整度 | `PARTIAL` |
| Claim 数 | 2 |
| SQL 查询数 | 4 |
| DuckDB 总查询耗时 | 353.288 ms |
| DuckDB 平均查询耗时 | 88.322 ms |
| 服务端到端耗时 | 388.935 ms |

实际答案：

```text
支付 GMV从 6,992,887.60 变为 6,995,351.00，变化 0.04%。
south 的region分组对全部分组绝对变化的占比为 25.07%；各分组存在正负抵消，该占比不等于对净变化的解释力。这只表示可加指标的分组差异，不代表因果。
```

## SQL 审计与查询计划

| 结果 | 行数 | 引擎耗时 | EXPLAIN ANALYZE 总耗时 |
|---|---:|---:|---:|
| `result_1988f5e7b0884846` | 1 | 80 ms | 11.900 ms |
| `result_e4c20416e10542f1` | 1 | 83 ms | 10.200 ms |
| `result_9c6de943848c4b90` | 4 | 93 ms | 12.600 ms |
| `result_2ff31c629ce74790` | 4 | 98 ms | 12.600 ms |

### `result_1988f5e7b0884846`

```text
analyzed_plan
┌─────────────────────────────────────┐
│┌───────────────────────────────────┐│
││    Query Profiling Information    ││
│└───────────────────────────────────┘│
└─────────────────────────────────────┘
EXPLAIN ANALYZE SELECT SUM(f.paid_gmv) AS metric_value FROM sales_batch_20261123_2300 AS f WHERE f.event_date BETWEEN ? AND ? AND f.region IN (?, ?, ?, ?) AND f.channel IN (?, ?, ?) AND f.category IN (?, ?, ?, ?) AND f.tenant_id = ?
┌────────────────────────────────────────────────┐
│┌──────────────────────────────────────────────┐│
││              Total Time: 0.0119s             ││
│└──────────────────────────────────────────────┘│
└────────────────────────────────────────────────┘
┌───────────────────────────┐
│           QUERY           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│      EXPLAIN_ANALYZE      │
│    ────────────────────   │
│                           │
│           0 rows          │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│    UNGROUPED_AGGREGATE    │
│    ────────────────────   │
│    Aggregates: sum(#0)    │
│                           │
│                           │
│                           │
│           1 row           │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│          paid_gmv         │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│             #3            │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│           FILTER          │
│    ────────────────────   │
│  (((channel = 'app') OR   │
│   (channel = 'web') OR    │
│ (channel = 'mini_program')│
│  ) AND ((region = 'east') │
│  OR (region = 'west') OR  │
│   (region = 'north') OR   │
│ (region = 'south')) AND ( │
│ (category = 'electronics')│
│  OR (category = 'beauty') │
│  OR (category = 'home') OR│
│  (category = 'fashion'))) │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.02s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         TABLE_SCAN        │
│    ────────────────────   │
│           Table:          │
│ "metricledger-scale".main │
│ .sales_batch_20261123_2300│
│                           │
│   Type: Sequential Scan   │
│                           │
│        Projections:       │
│           region          │
│          channel          │
│          category         │
│          paid_gmv         │
│                           │
│          Filters:         │
│ event_date>='2026-11-17': │
│   :DATE AND event_date<=  │
│     '2026-11-23'::DATE    │
│ optional: region IN ('east│
│ ', 'west', 'north', 'south│
│             ')            │
│ optional: channel IN ('app│
│ ', 'web', 'mini_program') │
│  optional: category IN (  │
│ 'electronics', 'beauty',  │
│     'home', 'fashion')    │
│      tenant_id='demo'     │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.03s           │
└───────────────────────────┘

```

### `result_e4c20416e10542f1`

```text
analyzed_plan
┌─────────────────────────────────────┐
│┌───────────────────────────────────┐│
││    Query Profiling Information    ││
│└───────────────────────────────────┘│
└─────────────────────────────────────┘
EXPLAIN ANALYZE SELECT SUM(f.paid_gmv) AS metric_value FROM sales_batch_20261123_2300 AS f WHERE f.event_date BETWEEN ? AND ? AND f.region IN (?, ?, ?, ?) AND f.channel IN (?, ?, ?) AND f.category IN (?, ?, ?, ?) AND f.tenant_id = ?
┌────────────────────────────────────────────────┐
│┌──────────────────────────────────────────────┐│
││              Total Time: 0.0102s             ││
│└──────────────────────────────────────────────┘│
└────────────────────────────────────────────────┘
┌───────────────────────────┐
│           QUERY           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│      EXPLAIN_ANALYZE      │
│    ────────────────────   │
│                           │
│           0 rows          │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│    UNGROUPED_AGGREGATE    │
│    ────────────────────   │
│    Aggregates: sum(#0)    │
│                           │
│                           │
│                           │
│           1 row           │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│          paid_gmv         │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│             #3            │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│           FILTER          │
│    ────────────────────   │
│  (((channel = 'app') OR   │
│   (channel = 'web') OR    │
│ (channel = 'mini_program')│
│  ) AND ((region = 'east') │
│  OR (region = 'west') OR  │
│   (region = 'north') OR   │
│ (region = 'south')) AND ( │
│ (category = 'electronics')│
│  OR (category = 'beauty') │
│  OR (category = 'home') OR│
│  (category = 'fashion'))) │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.02s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         TABLE_SCAN        │
│    ────────────────────   │
│           Table:          │
│ "metricledger-scale".main │
│ .sales_batch_20261123_2300│
│                           │
│   Type: Sequential Scan   │
│                           │
│        Projections:       │
│           region          │
│          channel          │
│          category         │
│          paid_gmv         │
│                           │
│          Filters:         │
│ event_date>='2026-11-10': │
│   :DATE AND event_date<=  │
│     '2026-11-16'::DATE    │
│ optional: region IN ('east│
│ ', 'west', 'north', 'south│
│             ')            │
│ optional: channel IN ('app│
│ ', 'web', 'mini_program') │
│  optional: category IN (  │
│ 'electronics', 'beauty',  │
│     'home', 'fashion')    │
│      tenant_id='demo'     │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.02s           │
└───────────────────────────┘

```

### `result_9c6de943848c4b90`

```text
analyzed_plan
┌─────────────────────────────────────┐
│┌───────────────────────────────────┐│
││    Query Profiling Information    ││
│└───────────────────────────────────┘│
└─────────────────────────────────────┘
EXPLAIN ANALYZE SELECT f.region AS dimension_value, SUM(f.paid_gmv) AS metric_value FROM sales_batch_20261123_2300 AS f WHERE f.event_date BETWEEN ? AND ? AND f.region IN (?, ?, ?, ?) AND f.channel IN (?, ?, ?) AND f.category IN (?, ?, ?, ?) AND f.tenant_id = ? GROUP BY f.region
┌────────────────────────────────────────────────┐
│┌──────────────────────────────────────────────┐│
││              Total Time: 0.0126s             ││
│└──────────────────────────────────────────────┘│
└────────────────────────────────────────────────┘
┌───────────────────────────┐
│           QUERY           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│      EXPLAIN_ANALYZE      │
│    ────────────────────   │
│                           │
│           0 rows          │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│__internal_decompress_strin│
│           g(#0)           │
│             #1            │
│                           │
│                           │
│                           │
│           4 rows          │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│       HASH_GROUP_BY       │
│    ────────────────────   │
│         Groups: #0        │
│    Aggregates: sum(#1)    │
│                           │
│                           │
│                           │
│           4 rows          │
│           0.01s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│           region          │
│          paid_gmv         │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│__internal_compress_string_│
│        ubigint(#0)        │
│             #1            │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│             #0            │
│             #3            │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│           FILTER          │
│    ────────────────────   │
│  (((channel = 'app') OR   │
│   (channel = 'web') OR    │
│ (channel = 'mini_program')│
│  ) AND ((region = 'east') │
│  OR (region = 'west') OR  │
│   (region = 'north') OR   │
│ (region = 'south')) AND ( │
│ (category = 'electronics')│
│  OR (category = 'beauty') │
│  OR (category = 'home') OR│
│  (category = 'fashion'))) │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.02s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         TABLE_SCAN        │
│    ────────────────────   │
│           Table:          │
│ "metricledger-scale".main │
│ .sales_batch_20261123_2300│
│                           │
│   Type: Sequential Scan   │
│                           │
│        Projections:       │
│           region          │
│          channel          │
│          category         │
│          paid_gmv         │
│                           │
│          Filters:         │
│ event_date>='2026-11-17': │
│   :DATE AND event_date<=  │
│     '2026-11-23'::DATE    │
│ optional: region IN ('east│
│ ', 'west', 'north', 'south│
│             ')            │
│ optional: channel IN ('app│
│ ', 'web', 'mini_program') │
│  optional: category IN (  │
│ 'electronics', 'beauty',  │
│     'home', 'fashion')    │
│      tenant_id='demo'     │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.02s           │
└───────────────────────────┘

```

### `result_2ff31c629ce74790`

```text
analyzed_plan
┌─────────────────────────────────────┐
│┌───────────────────────────────────┐│
││    Query Profiling Information    ││
│└───────────────────────────────────┘│
└─────────────────────────────────────┘
EXPLAIN ANALYZE SELECT f.region AS dimension_value, SUM(f.paid_gmv) AS metric_value FROM sales_batch_20261123_2300 AS f WHERE f.event_date BETWEEN ? AND ? AND f.region IN (?, ?, ?, ?) AND f.channel IN (?, ?, ?) AND f.category IN (?, ?, ?, ?) AND f.tenant_id = ? GROUP BY f.region
┌────────────────────────────────────────────────┐
│┌──────────────────────────────────────────────┐│
││              Total Time: 0.0126s             ││
│└──────────────────────────────────────────────┘│
└────────────────────────────────────────────────┘
┌───────────────────────────┐
│           QUERY           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│      EXPLAIN_ANALYZE      │
│    ────────────────────   │
│                           │
│           0 rows          │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│__internal_decompress_strin│
│           g(#0)           │
│             #1            │
│                           │
│                           │
│                           │
│           4 rows          │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│       HASH_GROUP_BY       │
│    ────────────────────   │
│         Groups: #0        │
│    Aggregates: sum(#1)    │
│                           │
│                           │
│                           │
│           4 rows          │
│           0.02s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│           region          │
│          paid_gmv         │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│__internal_compress_string_│
│        ubigint(#0)        │
│             #1            │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         PROJECTION        │
│    ────────────────────   │
│             #0            │
│             #3            │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.00s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│           FILTER          │
│    ────────────────────   │
│  (((channel = 'app') OR   │
│   (channel = 'web') OR    │
│ (channel = 'mini_program')│
│  ) AND ((region = 'east') │
│  OR (region = 'west') OR  │
│   (region = 'north') OR   │
│ (region = 'south')) AND ( │
│ (category = 'electronics')│
│  OR (category = 'beauty') │
│  OR (category = 'home') OR│
│  (category = 'fashion'))) │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.02s           │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         TABLE_SCAN        │
│    ────────────────────   │
│           Table:          │
│ "metricledger-scale".main │
│ .sales_batch_20261123_2300│
│                           │
│   Type: Sequential Scan   │
│                           │
│        Projections:       │
│           region          │
│          channel          │
│          category         │
│          paid_gmv         │
│                           │
│          Filters:         │
│ event_date>='2026-11-10': │
│   :DATE AND event_date<=  │
│     '2026-11-16'::DATE    │
│ optional: region IN ('east│
│ ', 'west', 'north', 'south│
│             ')            │
│ optional: channel IN ('app│
│ ', 'web', 'mini_program') │
│  optional: category IN (  │
│ 'electronics', 'beauty',  │
│     'home', 'fashion')    │
│      tenant_id='demo'     │
│                           │
│                           │
│                           │
│        116,662 rows       │
│           0.01s           │
└───────────────────────────┘

```

## 口径与限制

- `execution_count` 来自同一个 `DuckDbEngine` 实例，只统计 `AnalysisService` 实际执行次数。
- `EXPLAIN ANALYZE` 会再次执行查询，因此不计入上面的服务查询次数。
- 数据规模可复现，但耗时受本机 CPU、内存、DuckDB 版本和文件缓存影响。
- 本报告不包含并发吞吐结论；并发结论见 load 报告。
