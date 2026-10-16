const BASE: string = import.meta.env.VITE_API_BASE_URL ?? "/api";

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|; )csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

let refreshing: Promise<boolean> | null = null;

function refreshSession(): Promise<boolean> {
  // One refresh at a time even if several requests expire together.
  refreshing ??= fetch(`${BASE}/v1/auth/refresh`, {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrfToken() },
  })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

type Options = { method?: string; body?: unknown; form?: FormData };

export async function api<T = unknown>(path: string, { method = "GET", body, form }: Options = {}): Promise<T> {
  const send = () =>
    fetch(`${BASE}/v1${path}`, {
      method,
      credentials: "include",
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" ? { "X-CSRF-Token": csrfToken() } : {}),
      },
      body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
    });

  let res = await send();
  if (res.status === 401 && !path.startsWith("/auth/") && (await refreshSession())) {
    res = await send();
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data?.error;
    if (err) throw new ApiError(res.status, err.code, err.message);
    const detail = Array.isArray(data?.detail) ? data.detail[0]?.msg : undefined;
    throw new ApiError(res.status, "REQUEST_FAILED", detail ?? "Something went wrong. Please try again.");
  }
  return data as T;
}

/** Turns an API-relative media path (/api/v1/media/...) into a URL usable from the browser. */
export function mediaUrl(path: string | null | undefined): string | undefined {
  if (!path) return undefined;
  return BASE.startsWith("http") ? new URL(BASE).origin + path : path;
}
