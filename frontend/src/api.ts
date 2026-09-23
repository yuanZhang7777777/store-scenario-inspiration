export interface SalesRow {
  product_name: string;
  product_title: string | null;
  product_id: string | null;
  source_image: string;
  source_row: number;
  raw_text: string;
  period_text: string | null;
  approximate: boolean;
  orders: number | null;
  units_sold: number | null;
  gmv: number | null;
  currency: string | null;
}
export interface BusinessContext {
  schema: string;
  sales_rows: SalesRow[];
  warnings: string[];
  scope: string;
}

export type StageStatus = "pending" | "running" | "ready" | "failed";
export type JobStatus = "pending" | "running" | "ready" | "failed" | "cancelled";

export interface Screenshot {
  filename: string;
  local_path: string;
  sha256: string;
  bytes: number;
}

export interface StageProgress {
  uploaded: boolean;
  recognized: boolean;
  clues: boolean;
  scenes: boolean;
  products: boolean;
  synthesis: boolean;
  expansions: boolean;
  retrieval: boolean;
  rerank: boolean;
}

export interface Stage {
  name: string;
  label: string;
  status: StageStatus;
  detail: string;
  error: string | null;
  /** Whether this stage calls DeepSeek, and therefore costs money to redo. */
  paid: boolean;
  started_at: string | null;
  finished_at: string | null;
  /** How long this stage took, or has been taking so far. */
  seconds: number | null;
}

export interface Job {
  id: string;
  store_id: string;
  status: JobStatus;
  created_at: string;
  finished_at: string | null;
  /** Total wall clock from accepting the job, live while it runs. */
  seconds: number;
  usage: { total_tokens?: number };
  log: { at: string; stage: string; message: string }[];
  stages: Stage[];
}

export interface Params {
  scene_count: number;
  products_per_scene: number;
  expansion_terms: number;
  recall_limit: number;
  stock_filter: string;
  /** Who answers: "jev" (near-free, the default), "deepseek" (paid), "off". */
  rerank_provider: string;
  /** How much freedom the writing model gets. */
  temperature: number;
}

export interface ParamField {
  name: keyof Params;
  default: number | string;
  minimum: number | null;
  maximum: number | null;
  /** How much one arrow press moves a number, so 0.5 is reachable. */
  step: number;
  /** Present for the few knobs that are a choice rather than a number. */
  options: string[] | null;
  labels: Record<string, string> | null;
  description: string;
}

export interface ParamSchema {
  fields: ParamField[];
}

export interface StoreSummary {
  job_status?: JobStatus | null;
  needs_update?: boolean;
  id: string;
  store_name: string;
  country: string;
  images: Screenshot[];
  stages: StageProgress;
}

export interface StoreDetail {
  export_blocked_reason?: string;
  id: string;
  store: { store_name: string; country: string; images: Screenshot[] };
  stages: StageProgress;
  params: Params;
  /** The steps that no longer describe this store, in run order. What the one
   *  button works from, so the operator answers "what changed" by having
   *  changed it rather than by knowing the pipeline. */
  outdated: string[];
  kept_clues: string[];
  excluded_clues: string[];
  job: Job | null;
}

export interface ClueEntry {
  clue: string;
  excluded: boolean;
  /** The screenshot's own wording, when the name above is its translation.
   *  Absent on readings made before the pass was asked for a Chinese name. */
  original?: string;
  role: string | null;
  confidence: number;
  evidence: string;
  image_count: number;
  card_images: number;
  scenery_images: number;
  merged_from: string[];
  /** Typed in by the operator, so no screenshot stands behind it. */
  manual: boolean;
}

export interface CustomProduct {
  name_cn: string;
  name_en: string;
}

/**
 * Every product recognition saw, plus the ones the operator typed in, with the
 * exclusions applied.
 *
 * Recognition tags nearly everything as a product card at the same confidence,
 * so no threshold here separates the store's direction from an item that merely
 * happened to be photographed. The only levers are what the operator rules out
 * and what they add by hand.
 */
export interface Clues {
  counts: { kept: number; excluded: number };
  entries: ClueEntry[];
  excluded: string[];
  custom: CustomProduct[];
}

export interface ProductNeed {
  product_cn: string;
  product_en: string;
  purpose: string;
}

export interface Scene {
  scene_name: string;
  audience: string;
  user_need: string;
  evidence: string;
  product_needs: ProductNeed[];
  /** Product roles the operator ruled out that this scene still carries. */
  excluded: string[];
}

export interface ManagerSummary {
  executive_conclusion: string;
  business_opportunity: string;
  recommended_actions: string[];
  decision_boundary: string;
}

export interface Judgement {
  judgement: string;
  evidence: string;
}

export interface FutureStructure extends Judgement {
  priority_order: string[];
}

export interface Audience {
  audience_name: string;
  description: string;
  evidence: string;
}

export interface Strategy {
  strategy_name: string;
  description: string;
  evidence: string;
}

export interface Analysis {
  business_context?: BusinessContext | null;
  store: Record<string, string | null>;
  manager_summary: ManagerSummary;
  store_profile: Judgement;
  audiences: Audience[];
  current_product_structure: Judgement;
  scenes: Scene[];
  future_product_structure: FutureStructure;
  operation_strategy: Strategy[];
  reintroduced: { scene_name: string; product_cn: string; excluded_clue: string }[];
}

export interface Candidate {
  rank: number;
  main_sku: string;
  standard_name_cn: string;
  standard_name_en: string;
  rrf_score: number;
  channels: string[];
  matched_queries: string[];
  country_available: boolean | null;
  country_available_quantity: number | null;
  /**
   * The rerank model's verdict: "related", "unrelated", or null when it was
   * never asked. Absent entirely when the rerank step has not run. Judged per
   * scene, so the same SKU carries the same word into every role that recalled it.
   */
  rerank?: string | null;
  /** Where pure similarity had this row, before the verdict pass reordered it. */
  recall_rank?: number;
}

/** One product role in one scene, with the catalogue SKUs it recalled. */
export interface RetrievalProduct {
  scene_name: string;
  product_cn: string;
  product_en: string;
  /** The terms that actually went to the catalogue, per language. */
  queries: { cn: string[]; en: string[] };
  candidates: Candidate[];
  /** SKUs the rerank step was sure enough about to remove. Kept for the audit. */
  dropped?: Candidate[];
}

export interface RerankSummary {
  asked: number;
  answered: number;
  dropped: number;
  failed: number;
  notes: string[];
  mode: string;
  cutoff: number;
  /** Which model answered: "jev" or "deepseek". */
  provider: string;
}

export interface Retrieval {
  country: string;
  /** "available" means country stock was read; "unavailable" means it was not. */
  inventory: string;
  scenes: RetrievalProduct[];
  /** Present only once the rerank step has run. */
  rerank?: RerankSummary;
}

const BASE = "/api";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, init);
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* the response was not JSON; the status is all we have */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function send<T>(path: string, body: unknown): Promise<T> {
  return call<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** One ticked row, as the three names that identify it. */
export interface Pick {
  scene_name: string;
  product_cn: string;
  main_sku: string;
}

/** Save a response to disk under the name the server chose for it. */
async function save(path: string, body: unknown, fallback: string): Promise<void> {
  const response = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
    } catch {
      /* the response was not JSON; the status is all we have */
    }
    throw new Error(detail);
  }
  const named = /filename\*=UTF-8''([^;]+)/.exec(response.headers.get("Content-Disposition") ?? "");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = named ? decodeURIComponent(named[1]) : fallback;
  link.click();
  URL.revokeObjectURL(url);
}

export const api = {
  health: () =>
    call<{ data_dir: string; api_key_configured: boolean; countries: string[]; max_upload_bytes?: number }>("/health"),

  stores: () => call<{ stores: StoreSummary[] }>("/stores").then((r) => r.stores),

  store: (id: string) => call<StoreDetail>(`/stores/${id}`),

  createStore: (storeName: string, country: string, files: File[]) => {
    const body = new FormData();
    body.append("store_name", storeName);
    body.append("country", country);
    files.forEach((file) => body.append("files", file));
    return call<StoreSummary>("/stores", { method: "POST", body });
  },

  startJob: (id: string, stages?: string[], params?: Params) =>
    call<Job>(`/stores/${id}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(stages ? { stages } : {}), ...(params ? { params } : {}) }),
    }),

  job: (jobId: string) => call<Job>(`/jobs/${jobId}`),

  cancelJob: (jobId: string) => call<Job>(`/jobs/${jobId}/cancel`, { method: "POST" }),

  clues: (id: string) => call<Clues>(`/stores/${id}/clues`),

  setExcluded: (id: string, excluded: string[]) => send<Clues>(`/stores/${id}/clues`, { excluded }),

  setProducts: (id: string, products: CustomProduct[]) =>
    send<Clues>(`/stores/${id}/products`, { products }),

  paramSchema: () => call<ParamSchema>("/params/schema"),

  params: (id: string) => call<Params>(`/stores/${id}/params`),

  setParams: (id: string, params: Params) => send<Params>(`/stores/${id}/params`, params),

  analysis: (id: string) => call<Analysis>(`/stores/${id}/analysis`),

  retrieval: (id: string) => call<Retrieval>(`/stores/${id}/retrieval`),

  exportAdoption: (id: string, picks: Pick[], fallback: string, relatedOnly: boolean) =>
    save(`/stores/${id}/adoption/export`, { picks, related_only: relatedOnly },
         fallback),

  imageUrl: (id: string, filename: string) =>
    `${BASE}/stores/${id}/images/${encodeURIComponent(filename)}`,

  addImages: (id: string, files: File[]) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    return call<{ added: string[]; store: StoreDetail }>(`/stores/${id}/images`, {
      method: "POST",
      body,
    });
  },

  removeImage: (id: string, filename: string) =>
    call<StoreDetail>(`/stores/${id}/images/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    }),
};
