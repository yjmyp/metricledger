import { FormEvent, useMemo, useState } from "react";

import {
  AnalysisPayload,
  ClaimPayload,
  runAnalysis,
} from "./api";

const DEMO_AS_OF = "2026-11-23T23:59:59+08:00";

const EXAMPLES = [
  "2026-11-17 到 2026-11-23 支付GMV为什么下降",
  "2026-11-17 到 2026-11-23 支付转化率为什么下降",
  "2026-11-17 到 2026-11-23 各地区的支付GMV变化",
];

const STATUS_LABELS = {
  ADMITTED: "准入通过",
  CLARIFY: "等待澄清",
  REFUSED: "请求已拒绝",
  UNAVAILABLE: "当前不可用",
} as const;

const COMPLETENESS_LABELS = {
  COMPLETE: "结论完整",
  PARTIAL: "部分完整",
  NONE: "无可用结论",
} as const;

const CLAIM_LABELS: Record<string, string> = {
  VALUE: "指标值",
  CHANGE: "变化",
  CONTRIBUTION: "贡献",
  DIAGNOSTIC: "诊断",
  QUALITY: "质量",
  LIMITATION: "限制",
};

function formatNumber(value: string | number): string {
  if (typeof value === "string") {
    return value;
  }
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 2,
  }).format(value);
}

function shortHash(value?: string): string {
  if (!value) {
    return "未提供";
  }
  return value;
}

function ClaimCard({ claim }: { claim: ClaimPayload }) {
  const values = Object.entries(claim.evidence_values ?? {});

  return (
    <article className="claim-card">
      <div className="claim-card__topline">
        <span className="claim-type">
          {CLAIM_LABELS[claim.claim_type] ?? claim.claim_type}
        </span>
        <span
          className={`support support--${claim.support.toLowerCase()}`}
        >
          {claim.support === "SUPPORTED" ? "证据支持" : claim.support}
        </span>
      </div>
      <p className="claim-card__statement">{claim.rendered_text}</p>
      {values.length > 0 ? (
        <dl className="claim-values">
          {values.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{formatNumber(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <div className="claim-card__meta">
        <span>{claim.evidence_refs.length} 份证据</span>
        {claim.limitations?.map((limitation) => (
          <span key={limitation}>{limitation}</span>
        ))}
      </div>
    </article>
  );
}

function EmptyClaimState({
  status,
}: {
  status: AnalysisPayload["admission"]["status"];
}) {
  const copy =
    status === "REFUSED"
      ? "请求未通过准入，不产生回答或 Claim。"
      : status === "CLARIFY"
        ? "指标或时间范围不唯一，系统不会猜测答案。"
        : status === "UNAVAILABLE"
          ? "固定快照无法支持本次分析，不返回业务零值。"
          : "当前没有可验证结论。";

  return (
    <div className="empty-state">
      <span className="empty-state__mark">∅</span>
      <p>{copy}</p>
    </div>
  );
}

export default function App() {
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [analysis, setAnalysis] = useState<AnalysisPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const status = analysis?.admission.status;
  const hasClaims =
    status === "ADMITTED" && Boolean(analysis?.claims.length);
  const context = analysis?.context;
  const snapshot =
    analysis?.admission.snapshot?.snapshot_id ??
    context?.snapshot_id ??
    "未绑定";
  const semanticHash = shortHash(context?.semantic_bundle_hash);
  const issues = useMemo(() => analysis?.issues ?? [], [analysis]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!question.trim() || loading) {
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const result = await runAnalysis({
        question: question.trim(),
        as_of: DEMO_AS_OF,
        principal: {
          principal_id: "workbench-analyst",
          allowed_values: {
            region: ["east", "west", "north", "south"],
            channel: ["app", "web", "mini_program"],
            category: ["electronics", "beauty", "home", "fashion"],
          },
        },
        budget: { max_queries: 4 },
      });
      setAnalysis(result);
    } catch (cause) {
      setAnalysis(null);
      setError(
        cause instanceof Error ? cause.message : "未知请求错误",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="MetricLedger 首页">
          <span className="brand__glyph">ML</span>
          <span>
            <strong>MetricLedger</strong>
            <small>Evidence-bound analytics</small>
          </span>
        </a>
        <div className="topbar__meta">
          <span className="live-dot" />
          固定快照演示
          <code>{DEMO_AS_OF.slice(0, 10)}</code>
        </div>
      </header>

      <main className="workspace">
        <aside className="query-panel">
          <div>
            <p className="eyebrow">CONTROLLED ANALYSIS</p>
            <h1>让每个业务数字<br />都有出处。</h1>
            <p className="query-panel__intro">
              语义准入、只读 SQL、固定快照与 Claim
              级证据共同约束答案。系统可以明确说“不知道”，
              但不会把失败伪装成零。
            </p>
          </div>

          <form onSubmit={submit} className="query-form">
            <label htmlFor="question">业务问题</label>
            <textarea
              id="question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={5}
              maxLength={1000}
              spellCheck={false}
            />
            <div className="examples">
              <span>示例</span>
              {EXAMPLES.map((example, index) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setQuestion(example)}
                >
                  0{index + 1}
                </button>
              ))}
            </div>
            <button className="primary-action" disabled={loading}>
              {loading ? "正在执行固定查询…" : "开始调查"}
              <span aria-hidden="true">↗</span>
            </button>
          </form>

          <div className="guardrails">
            <p className="eyebrow">INVARIANTS</p>
            <ul>
              <li>模型不能创建 SQL、指标或数字</li>
              <li>越权请求在查询前拒绝</li>
              <li>比率贡献只做方向性诊断</li>
              <li>审计账本不保存原始明细</li>
            </ul>
          </div>
        </aside>

        <section className="result-surface" aria-live="polite">
          <div className="result-heading">
            <div>
              <p className="eyebrow">ANALYSIS RECEIPT</p>
              <h2>分析回执</h2>
            </div>
            {analysis ? (
              <span className={`status-badge status-badge--${status}`}>
                {status ? STATUS_LABELS[status] : "未知状态"}
              </span>
            ) : (
              <span className="status-badge status-badge--IDLE">
                等待请求
              </span>
            )}
          </div>

          {error ? (
            <div className="request-error" role="alert">
              <strong>请求未完成</strong>
              <p>{error}</p>
            </div>
          ) : null}

          {!analysis && !error ? (
            <div className="result-placeholder">
              <div className="constellation" aria-hidden="true">
                <span />
                <span />
                <span />
                <span />
              </div>
              <h3>尚未生成分析回执</h3>
              <p>
                选择一个示例或输入明确指标、日期和调查意图。
                回答只会建立在固定快照的真实查询结果上。
              </p>
            </div>
          ) : null}

          {analysis ? (
            <>
              <div className="receipt-strip">
                <div>
                  <span>完整性</span>
                  <strong>
                    {COMPLETENESS_LABELS[analysis.completeness]}
                  </strong>
                </div>
                <div>
                  <span>固定快照</span>
                  <strong>{snapshot}</strong>
                </div>
                <div>
                  <span>查询 / 耗时</span>
                  <strong>
                    {analysis.budget_usage.queries_used ?? 0} 次 ·{" "}
                    {analysis.budget_usage.elapsed_ms ?? 0} ms
                  </strong>
                </div>
                <div>
                  <span>语义版本</span>
                  <code>{semanticHash}</code>
                </div>
              </div>

              <div className="result-grid">
                <div className="primary-result">
                  <div className="section-title">
                    <div>
                      <p className="eyebrow">VERIFIED CLAIMS</p>
                      <h3>证据约束结论</h3>
                    </div>
                    <span>
                      {analysis.claims.length.toString().padStart(2, "0")}
                    </span>
                  </div>

                  {hasClaims ? (
                    <div className="claim-list" data-testid="claim-list">
                      {analysis.claims.map((claim) => (
                        <ClaimCard
                          key={claim.claim_id}
                          claim={claim}
                        />
                      ))}
                    </div>
                  ) : (
                    <EmptyClaimState
                      status={analysis.admission.status}
                    />
                  )}
                </div>

                <aside className="diagnostics">
                  <div className="section-title">
                    <div>
                      <p className="eyebrow">BLOCKERS</p>
                      <h3>问题与限制</h3>
                    </div>
                    <span>
                      {issues.length.toString().padStart(2, "0")}
                    </span>
                  </div>
                  {issues.length > 0 ? (
                    <ul className="issue-list">
                      {issues.map((issue) => (
                        <li key={`${issue.code}-${issue.message}`}>
                          <span>{issue.severity ?? "ERROR"}</span>
                          <p>{issue.message}</p>
                          <code>{issue.code}</code>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="quiet-state">
                      未发现准入、质量或预算阻断项。
                    </p>
                  )}
                </aside>
              </div>

              <div className="audit-grid">
                <section className="audit-card">
                  <p className="eyebrow">EVIDENCE ARTIFACTS</p>
                  <h3>证据账本</h3>
                  {analysis.evidence.length > 0 ? (
                    <div className="evidence-list">
                      {analysis.evidence.map((artifact, index) => (
                        <div key={artifact.result_id}>
                          <span>{String(index + 1).padStart(2, "0")}</span>
                          <div>
                            <strong>{artifact.analysis_kind ?? "result"}</strong>
                            <code>{artifact.result_id}</code>
                          </div>
                          <dl>
                            <dt>ROWS</dt>
                            <dd>{artifact.row_count ?? 0}</dd>
                            <dt>DIGEST</dt>
                            <dd>{artifact.result_digest ?? "—"}</dd>
                          </dl>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="quiet-state">没有产生查询证据。</p>
                  )}
                </section>

                <details className="audit-card sql-audit" data-testid="sql-audit">
                  <summary>
                    <span>
                      <span className="eyebrow">READ-ONLY PLAN</span>
                      SQL 审计
                    </span>
                    <b>{analysis.sql_audit.length}</b>
                  </summary>
                  {analysis.sql_audit.length > 0 ? (
                    <div className="sql-list">
                      {analysis.sql_audit.map((audit) => (
                        <article key={audit.result_id}>
                          <header>
                            <code>{audit.result_id}</code>
                            <span>{audit.elapsed_ms} ms</span>
                          </header>
                          <pre>{audit.sql}</pre>
                          <p>
                            PARAMETERS{" "}
                            <code>{JSON.stringify(audit.parameters)}</code>
                          </p>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <p className="quiet-state">没有执行 SQL。</p>
                  )}
                </details>
              </div>
            </>
          ) : null}
        </section>
      </main>
    </div>
  );
}
