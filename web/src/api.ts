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

export interface InvestigationStepPayload {
  round_number: number;
  dimension_id: string;
  status:
    | "MATERIAL_CHANGE"
    | "NO_MATERIAL_CHANGE"
    | "NO_COMPARABLE_GROUPS"
    | "QUERY_FAILED";
  result_ids: string[];
  material_change: number | null;
  concentration_share: number | null;
  group_count: number;
  top_group: string | null;
  issue_codes: string[];
}

export interface InvestigationRoundPayload {
  round_number: number;
  planner: {
    round_number: number;
    outcome: string;
    model_called: boolean;
    fallback_used: boolean;
    fallback_reason: string | null;
    latency_ms: number;
    usage_complete: boolean;
    selected_dimensions: string[];
  };
  steps: InvestigationStepPayload[];
  observation: Record<string, unknown>;
}

export interface InvestigationPayload {
  rounds: InvestigationRoundPayload[];
  completed_dimensions: string[];
  remaining_dimensions: string[];
  stop_reason: string;
  planner_fallback_used: boolean;
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
    planner_tokens_used?: number | null;
    planner_usage_complete?: boolean;
    elapsed_ms?: number;
  };
  evidence: EvidencePayload[];
  sql_audit: SqlAuditPayload[];
  investigation?: InvestigationPayload | null;
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
