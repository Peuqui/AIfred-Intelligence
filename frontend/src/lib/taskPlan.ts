// The plan of a job split into parts, as the job form edits it: the same structure the server stores
// (aifred/lib/task_plan.py); the trend is the name of a number column, empty for none.
import type { JobRow, PlanColumn, PlanPart } from "../api/types";

export interface PlanForm {
  frame: string;
  parts: PlanPart[];
  columns: PlanColumn[];
  trend: string;
}

type PlanFields = Pick<JobRow, "frame" | "parts" | "columns" | "trend">;

// A copy to edit (the job of the store stays as it is until it is saved).
export function planFormOf(job: PlanFields | null): PlanForm {
  return {
    frame: job?.frame ?? "",
    parts: (job?.parts ?? []).map((part) => ({ ...part, positions: part.positions.map((position) => ({ ...position })) })),
    columns: (job?.columns ?? []).map((column) => ({ ...column })),
    trend: job?.trend?.value ?? "",
  };
}

export const hasPlan = (job: PlanFields | null): boolean => !!job && (job.parts.length > 0 || job.columns.length > 0 || !!job.frame || !!job.trend);

export const positionCount = (parts: PlanPart[]): number => parts.reduce((sum, part) => sum + part.positions.length, 0);

// What the server takes: the trend only when one is chosen.
export function planWrite(plan: PlanForm): { frame: string; parts: PlanPart[]; columns: PlanColumn[]; trend: { value: string } | null } {
  return { frame: plan.frame, parts: plan.parts, columns: plan.columns, trend: plan.trend ? { value: plan.trend } : null };
}
