import { api } from "./client";
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
