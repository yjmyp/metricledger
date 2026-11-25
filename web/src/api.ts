export type AdmissionStatus =
  | "ADMITTED"
  | "CLARIFY"
  | "REFUSED"
  | "UNAVAILABLE";

export type Completeness = "COMPLETE" | "PARTIAL" | "NONE";

export interface IssuePayload {
  code: string;
  message: string;
  severity?: "INFO" | "WARNING" | "ERROR" | "BLOCKER";
  recoverable?: boolean;
}

export interface ClaimPayload {
  claim_id: string;
  claim_type: string;
  template?: string;
  rendered_text: string;
  evidence_refs: string[];
  evidence_values?: Record<string, string | number>;
  support: "SUPPORTED" | "PARTIAL" | "INCONCLUSIVE" | "UNSUPPORTED";
  limitations?: string[];
  scope?: Record<string, unknown>;
}

export interface AnalysisContextPayload {
  analysis_id?: string;
  metric_id?: string;
  semantic_bundle_hash?: string;
  snapshot_id?: string;
  snapshot_table?: string;
  reporting_as_of?: string;
  timezone?: string;
  auth_scope_hash?: string;
}

export interface EvidencePayload {
  result_id: string;
  analysis_kind?: string;
  row_count?: number;
  result_digest?: string;
  sql_fingerprint?: string;
  snapshot_id?: string;
  elapsed_ms?: number;
}

export interface SqlAuditPayload {
  result_id: string;
  sql: string;
  parameters: Array<string | number>;
  fingerprint?: string;
  row_count: number;
  snapshot_id: string;
  elapsed_ms: number;
}

export interface AnalysisPayload {
  analysis_id: string;
  admission: {
    status: AdmissionStatus;
    issue_codes: string[];
    snapshot?: {
      snapshot_id?: string;
      period_start?: string;
      period_end?: string;
      complete?: boolean;
    } | null;
    quality_checks?: Array<{
      code: string;
      status: "PASS" | "WARN" | "BLOCK";
      message: string;
    }>;
  };
  completeness: Completeness;
  answer: string | null;
  claims: ClaimPayload[];
  issues: IssuePayload[];
  context: AnalysisContextPayload | null;
  budget_usage: {
    queries_used?: number;
    planner_tokens_used?: number;
    elapsed_ms?: number;
  };
  evidence: EvidencePayload[];
  sql_audit: SqlAuditPayload[];
}

export interface AnalysisRequestPayload {
  question: string;
  as_of: string;
  principal: {
    principal_id: string;
    allowed_values: Record<string, string[]>;
  };
  budget: {
    max_queries: number;
  };
}

export async function runAnalysis(
  request: AnalysisRequestPayload,
): Promise<AnalysisPayload> {
  const response = await fetch("/api/analyses", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      body || `分析请求失败（HTTP ${response.status}）`,
    );
  }

  return (await response.json()) as AnalysisPayload;
}
