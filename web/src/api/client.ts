import type { ContractSummary, Outcome, RunPage, RunReport } from "./types";

export const validRunId = (id: string) => /^[a-f0-9]{32}$/.test(id);
const base = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");
export class ApiError extends Error {}

/** Errors are fixed local messages: never echo server bodies, URLs or uploaded content. */
async function request<T>(
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, 15000);
  try {
    const response = await fetch(`${base}${path}`, {
      ...init,
      signal: controller.signal,
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
    });
    if (!response.ok) {
      const messages: Record<number, string> = {
        404: "未找到报告，请核对 Run ID 和服务端报告目录。",
        413: "契约超过 1 MB 大小限制。",
        415: "请使用 JSON 或 YAML 文本。",
        422: "输入格式无效或存在不支持的 OpenAPI 约束，请检查后重试。",
        503: "报告目录不可用或超过扫描预算，请由操作员缩小报告目录。",
      };
      throw new ApiError(
        messages[response.status] ||
          `服务暂不可用（HTTP ${response.status}）。`,
      );
    }
    try {
      return (await response.json()) as T;
    } catch {
      throw new ApiError("服务返回的数据格式无效。");
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted)
      throw new ApiError("请求已取消或超过 15 秒，请重试。");
    throw new ApiError("无法连接 API，请检查后端服务与代理配置。");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export const api = {
  health: (signal?: AbortSignal) =>
    request<{ status: string }>("/health", {}, signal),
  runs: (
    offset: number,
    outcome: Outcome | "",
    order: "asc" | "desc",
    signal?: AbortSignal,
  ) =>
    request<RunPage>(
      `/api/runs?limit=12&offset=${offset}&order=${order}${outcome ? `&outcome=${outcome}` : ""}`,
      {},
      signal,
    ),
  run: (id: string, signal?: AbortSignal) =>
    validRunId(id)
      ? request<RunReport>(`/api/runs/${id}`, {}, signal)
      : Promise.reject(new ApiError("Run ID 必须为 32 位小写十六进制标识。")),
  summary: (text: string, signal?: AbortSignal) => {
    if (new TextEncoder().encode(text).length > 1_000_000)
      return Promise.reject(new ApiError("契约超过 1 MB 大小限制。"));
    return request<ContractSummary>(
      "/api/spec/summary",
      { method: "POST", headers: { "Content-Type": "text/plain" }, body: text },
      signal,
    );
  },
};
