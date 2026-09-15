export type PatternType = "3x4" | "2x5" | "4x3" | "2x2";

export type JobStatus =
  | "queued"
  | "searching"
  | "verifying"
  | "ready"
  | "ownership_verified"
  | "failed"
  | "canceled"
  | "timed_out";

export type User = {
  id: string;
  username: string;
  is_active: boolean;
  created_at: string;
};

export type TokenResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
};

export type GenerationResult = {
  address: string;
  private_key: string;
  verified_at: string;
};

export type GenerationJob = {
  id: string;
  pattern: PatternType;
  prefix: string;
  suffix: string;
  status: JobStatus;
  attempts: number;
  search_rate: number;
  observed_rate: number | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  failure_code: string | null;
  failure_message: string | null;
  result: GenerationResult | null;
};

export type JobList = {
  items: GenerationJob[];
  total: number;
  limit: number;
  offset: number;
};

export type GpuDevice = {
  uuid: string;
  device_index: number;
  name: string;
  memory_total_mb: number;
  status: "starting" | "self_testing" | "ready" | "searching" | "unhealthy" | "offline";
  kernel_version: string;
  benchmark_rate: number;
  utilization_percent: number;
  temperature_c: number | null;
  current_job_id: string | null;
  last_heartbeat: string | null;
  last_error: string | null;
};

export type GpuFleet = {
  mode: string;
  total: number;
  ready: number;
  searching: number;
  unhealthy: number;
  combined_benchmark_rate: number;
  devices: GpuDevice[];
};

type ApiOptions = Omit<RequestInit, "body"> & {
  token?: string;
  body?: unknown;
};

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1").replace(
  /\/$/,
  "",
);
let accessToken: string | null = null;
let accessExpiresAt = 0;
let refreshPromise: Promise<TokenResponse> | null = null;

function rememberToken(token: TokenResponse) {
  accessToken = token.access_token;
  accessExpiresAt = Date.now() + token.expires_in * 1_000;
  return token;
}

function clearAccessToken() {
  accessToken = null;
  accessExpiresAt = 0;
}

function refreshSession(): Promise<TokenResponse> {
  if (!refreshPromise) {
    refreshPromise = request<TokenResponse>("/auth/refresh", {
      method: "POST",
      credentials: "include",
    })
      .then(rememberToken)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

function errorMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) return String(item.msg);
        return String(item);
      })
      .join(" ");
  }
  return "The API could not complete this request.";
}

async function request<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  const protectedRequest = Boolean(options.token);
  if (protectedRequest) {
    if (!accessToken || Date.now() >= accessExpiresAt - 60_000) {
      await refreshSession();
    }
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  let response: Response;
  const fetchRequest = () =>
    fetch(`${API_URL}${path}`, {
      ...options,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
    });
  try {
    response = await fetchRequest();
    if (protectedRequest && response.status === 401) {
      try {
        await refreshSession();
      } catch {
        clearAccessToken();
        throw new ApiError("Your browser session ended. Please sign in again.", 401);
      }
      headers.set("Authorization", `Bearer ${accessToken}`);
      response = await fetchRequest();
    }
  } catch (caught) {
    if (caught instanceof ApiError) throw caught;
    throw new ApiError("Cannot reach the TronForge API.", 0);
  }

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(errorMessage(detail), response.status);
  }
  return (await response.json()) as T;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const api = {
  async login(username: string, password: string) {
    const token = await request<TokenResponse>("/auth/token", {
      method: "POST",
      body: { username, password },
      credentials: "include",
    });
    return rememberToken(token);
  },

  refreshSession,

  async logout() {
    try {
      if (refreshPromise) {
        try {
          await refreshPromise;
        } catch {
          // A failed renewal still leaves a cookie that logout should clear.
        }
      }
      await request<{ signed_out: boolean }>("/auth/logout", {
        method: "POST",
        credentials: "include",
      });
    } finally {
      clearAccessToken();
    }
  },

  me(token: string) {
    return request<User>("/auth/me", { token });
  },

  createJob(
    token: string,
    payload: {
      pattern: PatternType;
      prefix: string;
      suffix: string;
    },
  ) {
    return request<GenerationJob>("/jobs", {
      method: "POST",
      token,
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: payload,
    });
  },

  listJobs(token: string, limit = 100) {
    return request<JobList>(`/jobs?limit=${limit}&offset=0`, { token });
  },

  getJob(token: string, jobId: string) {
    return request<GenerationJob>(`/jobs/${jobId}`, { token });
  },

  cancelJob(token: string, jobId: string) {
    return request<GenerationJob>(`/jobs/${jobId}/cancel`, {
      method: "POST",
      token,
    });
  },

  getGpuFleet(token: string) {
    return request<GpuFleet>("/gpus", { token });
  },
};
