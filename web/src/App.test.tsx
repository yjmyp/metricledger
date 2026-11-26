import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MetricLedger workbench", () => {
  it("renders a refusal without rendering answer claims", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          analysis_id: "a1",
          admission: {
            status: "REFUSED",
            issue_codes: ["SCOPE_NOT_AUTHORIZED"],
          },
          completeness: "NONE",
          answer: null,
          claims: [],
          issues: [
            {
              code: "SCOPE_NOT_AUTHORIZED",
              message: "请求范围超出权限",
              severity: "ERROR",
              recoverable: false,
            },
          ],
          context: null,
          budget_usage: {
            queries_used: 0,
            planner_tokens_used: 0,
            elapsed_ms: 0,
          },
          evidence: [],
          sql_audit: [],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    render(<App />);
    await userEvent.click(
      screen.getByRole("button", { name: "开始调查" }),
    );

    expect(await screen.findByText("请求范围超出权限")).toBeInTheDocument();
    expect(screen.queryByTestId("claim-list")).not.toBeInTheDocument();
  });

  it("renders supported evidence and keeps SQL collapsed until requested", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          analysis_id: "a2",
          admission: {
            status: "ADMITTED",
            issue_codes: [],
            snapshot: {
              snapshot_id: "batch_20261123_2300",
              period_start: "2026-10-01",
              period_end: "2026-11-23",
            },
          },
          completeness: "COMPLETE",
          answer: "支付GMV从 806400 变为 732400，变化 -9.18%。",
          claims: [
            {
              claim_id: "c1",
              claim_type: "CHANGE",
              template: "受证据约束的结论",
              rendered_text:
                "支付GMV从 806400 变为 732400，变化 -9.18%。",
              evidence_refs: ["r1", "r2"],
              evidence_values: {
                baseline_value: 806400,
                metric_value: 732400,
                change_pct: -9.18,
              },
              scope: {
                metric_id: "paid_gmv",
                period: { start: "2026-11-17", end: "2026-11-23" },
              },
              support: "SUPPORTED",
            },
          ],
          issues: [],
          context: {
            analysis_id: "a2",
            metric_id: "paid_gmv",
            semantic_bundle_hash: "semantic-hash-123",
            snapshot_id: "batch_20261123_2300",
            snapshot_table: "sales_batch_20261123_2300",
            reporting_as_of: "2026-11-23T23:59:59+08:00",
          },
          budget_usage: {
            queries_used: 3,
            planner_tokens_used: 0,
            elapsed_ms: 42,
          },
          evidence: [
            {
              result_id: "r2",
              analysis_kind: "headline",
              row_count: 1,
              result_digest: "digest-r2",
              snapshot_id: "batch_20261123_2300",
            },
          ],
          sql_audit: [
            {
              result_id: "r2",
              sql: "SELECT metric_value FROM sales_batch_20261123_2300",
              parameters: ["2026-11-17", "2026-11-23"],
              row_count: 1,
              snapshot_id: "batch_20261123_2300",
              elapsed_ms: 12,
            },
          ],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    render(<App />);
    await userEvent.click(
      screen.getByRole("button", { name: "开始调查" }),
    );

    expect(
      await screen.findByText(
        "支付GMV从 806400 变为 732400，变化 -9.18%。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByTestId("claim-list")).toBeInTheDocument();
    expect(screen.getByText("semantic-hash-123")).toBeInTheDocument();
    expect(screen.getByTestId("sql-audit")).not.toHaveAttribute("open");

    await userEvent.click(screen.getByText(/SQL 审计/));
    expect(screen.getByTestId("sql-audit")).toHaveAttribute("open");
    expect(
      screen.getByText(
        "SELECT metric_value FROM sales_batch_20261123_2300",
      ),
    ).toBeInTheDocument();
  });

  it("renders model and user text literally instead of as HTML", async () => {
    const unsafeText = '<img src="x" onerror="alert(1)">';
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          analysis_id: "a3",
          admission: { status: "ADMITTED", issue_codes: [] },
          completeness: "COMPLETE",
          answer: unsafeText,
          claims: [
            {
              claim_id: "c1",
              claim_type: "VALUE",
              rendered_text: unsafeText,
              evidence_refs: ["r1"],
              evidence_values: {},
              support: "SUPPORTED",
            },
          ],
          issues: [],
          context: null,
          budget_usage: {},
          evidence: [],
          sql_audit: [],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    render(<App />);
    await userEvent.click(
      screen.getByRole("button", { name: "开始调查" }),
    );

    expect(await screen.findByText(unsafeText)).toBeInTheDocument();
    expect(document.querySelector("img")).not.toBeInTheDocument();
  });

  it("renders investigation rounds, step status, fallback, and usage", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          analysis_id: "a4",
          admission: { status: "ADMITTED", issue_codes: [] },
          completeness: "PARTIAL",
          answer: null,
          claims: [],
          issues: [],
          context: null,
          budget_usage: {
            queries_used: 4,
            planner_tokens_used: 120,
            planner_usage_complete: true,
            elapsed_ms: 88,
          },
          evidence: [],
          sql_audit: [],
          investigation: {
            stop_reason: "all_candidates_observed",
            completed_dimensions: ["region", "channel"],
            remaining_dimensions: [],
            planner_fallback_used: true,
            rounds: [
              {
                round_number: 1,
                observation: {
                  analysis_id: "a4",
                  round_number: 1,
                  metric_id: "paid_gmv",
                  metric_kind: "ADDITIVE",
                  question_kind: "INVESTIGATE",
                  candidate_dimensions: ["region", "channel"],
                  completed_dimensions: [],
                  remaining_queries: 4,
                  remaining_planner_tokens: 1880,
                  evidence: [],
                },
                planner: {
                  round_number: 1,
                  outcome: "MODEL_SUCCESS",
                  model_called: true,
                  fallback_used: false,
                  fallback_reason: null,
                  latency_ms: 42,
                  usage_complete: true,
                  selected_dimensions: ["region"],
                },
                steps: [
                  {
                    round_number: 1,
                    dimension_id: "region",
                    status: "NO_MATERIAL_CHANGE",
                    result_ids: ["r1", "r2"],
                    material_change: 0,
                    concentration_share: null,
                    group_count: 4,
                    top_group: null,
                    issue_codes: [],
                  },
                ],
              },
              {
                round_number: 2,
                observation: {
                  analysis_id: "a4",
                  round_number: 2,
                  metric_id: "paid_gmv",
                  metric_kind: "ADDITIVE",
                  question_kind: "INVESTIGATE",
                  candidate_dimensions: ["region", "channel"],
                  completed_dimensions: ["region"],
                  remaining_queries: 2,
                  remaining_planner_tokens: 1880,
                  evidence: [],
                },
                planner: {
                  round_number: 2,
                  outcome: "FALLBACK_TIMEOUT",
                  model_called: true,
                  fallback_used: true,
                  fallback_reason: "timeout",
                  latency_ms: 10000,
                  usage_complete: false,
                  selected_dimensions: ["channel"],
                },
                steps: [],
              },
            ],
          },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    render(<App />);
    await userEvent.click(
      screen.getByRole("button", { name: "开始调查" }),
    );

    expect(await screen.findByText("调查轨迹")).toBeInTheDocument();
    expect(screen.getByText("第 1 轮")).toBeInTheDocument();
    expect(screen.getByText("第 2 轮")).toBeInTheDocument();
    expect(screen.getByText("FALLBACK_TIMEOUT")).toBeInTheDocument();
    expect(screen.getByText("NO_MATERIAL_CHANGE")).toBeInTheDocument();
    expect(screen.getByText(/Token 120/)).toBeInTheDocument();
  });
});
