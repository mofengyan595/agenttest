import { api } from "./client";
import { intakeApi } from "./tasks";
test("invalid run ID is rejected before fetch", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  await expect(api.run("../.env")).rejects.toThrow("32 位");
  expect(fetcher).not.toHaveBeenCalled();
});
test("network errors never expose credentials or raw error details", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new Error("Authorization secret-token")),
  );
  await expect(api.health()).rejects.toThrow("无法连接 API");
});
test("malformed JSON fails with a fixed message", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("<html>secret</html>")),
  );
  await expect(api.health()).rejects.toThrow("数据格式无效");
});
test.each([
  ["CSV_ASSERTION_COLUMN_UNKNOWN", "断言列名不在已核对的 CSV 表头中"],
  ["CSV_HEADER_EVIDENCE_REQUIRED", "缺少已核对的 CSV 表头"],
])("CSV preflight shows the exact operation and selector for %s", async (code, message) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({detail: {
    code, fields: ["exportResults", "$.rows[0].评价内容"],
  }}, {status: 422})));
  await expect(intakeApi.save("session", {name: "导出", document: "{}", profile_id: null,
    profile_identity: null, confirmed: true, version: 0})).rejects.toThrow(
    `exportResults $.rows[0].评价内容：${message}`);
});
test("timeout cancels the request at 15 seconds", async () => {
  vi.useFakeTimers();
  try {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        (_url, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      ),
    );
    const check = expect(api.health()).rejects.toThrow("15 秒");
    await vi.advanceTimersByTimeAsync(15000);
    await check;
  } finally {
    vi.useRealTimers();
  }
});
