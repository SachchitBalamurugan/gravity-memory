export interface GravityOptions { url?: string; namespace?: string; key?: string }
export interface Run { run_id: string; title: string }
export interface Evidence { kind: string; source_id: string; source: string; quote: string; start: number; stop: number; key?: string; value?: string }
export interface Recall { trace_id: string; status: "grounded" | "abstained" | "evidence_only"; items: Evidence[]; bytes: number; budget_bytes: number; route: string; reason?: string }
export class GravityClient {
  constructor(options?: GravityOptions);
  call<T = Record<string, unknown>>(operation: string, args?: Record<string, unknown>): Promise<T>;
  startRun(title: string, options?: {requestId?: string; parentRunId?: string}): Promise<Run>;
  recall(runId: string | null, query: string, options?: {requestId?: string; budgetBytes?: number; filter?: Record<string, unknown>}): Promise<Recall>;
}
