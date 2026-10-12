import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { History } from "./History";
import { ContractImport } from "../components/ContractImport";
import { App } from "../App";

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
test("history loading, error, retry and empty results", async () => {
  let resolve!: (value: Response) => void;
  const fetcher = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise<Response>((r) => {
          resolve = r;
        }),
    )
    .mockResolvedValueOnce(
      json({ items: [], total: 0, offset: 0, limit: 12, skipped: 0 }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter>
      <History />
    </MemoryRouter>,
  );
  expect(screen.getByRole("status")).toHaveTextContent("正在读取真实数据");
  resolve(json({}, 503));
  expect(await screen.findByRole("alert")).toHaveTextContent("超过扫描预算");
  await userEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByText("没有符合条件的报告")).toBeInTheDocument();
  expect(screen.getByText("0 条记录")).toBeInTheDocument();
});
test("pagination uses server totals, filter resets offset", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      json({ items: [], total: 30, offset: 0, limit: 12, skipped: 0 }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter>
      <History />
    </MemoryRouter>,
  );
  await screen.findByText("没有符合条件的报告");
  await userEvent.click(screen.getByLabelText("下一页"));
  await waitFor(() =>
    expect(fetcher.mock.calls.at(-1)?.[0]).toContain("offset=12"),
  );
  await userEvent.selectOptions(screen.getByLabelText("结果"), "NEEDS_REVIEW");
  await waitFor(() =>
    expect(fetcher.mock.calls.at(-1)?.[0]).toContain("offset=0"),
  );
  expect(fetcher.mock.calls.at(-1)?.[0]).toContain("outcome=NEEDS_REVIEW");
});
test("OpenAPI text import success and failure never echoes remote error text", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      json({
        version: "3.1.0",
        operations: [
          { method: "get", path: "/resources", operation_id: "listResources" },
        ],
      }),
    )
    .mockResolvedValueOnce(json({ detail: "password=server-secret" }, 422));
  vi.stubGlobal("fetch", fetcher);
  render(<ContractImport />);
  await userEvent.type(
    screen.getByLabelText("或粘贴契约文本"),
    "openapi: 3.1.0",
  );
  await userEvent.click(screen.getByRole("button", { name: "解析契约" }));
  expect(await screen.findByText("listResources")).toBeInTheDocument();
  expect(screen.getByText("/resources")).toBeInTheDocument();
  expect(fetcher.mock.calls[0][1].body).toBe("openapi: 3.1.0");
  await userEvent.click(screen.getByRole("button", { name: "解析契约" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("OpenAPI");
  expect(screen.queryByText(/server-secret/)).not.toBeInTheDocument();
});
test("OpenAPI file import handles selection and oversize without sending a request", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(json({ version: "3.0.3", operations: [] }));
  vi.stubGlobal("fetch", fetcher);
  render(<ContractImport />);
  const file = new File(["openapi: 3.0.3"], "sample.yaml", {
    type: "text/yaml",
  });
  file.text = async () => "openapi: 3.0.3";
  await userEvent.upload(screen.getByLabelText("上传 OpenAPI 契约"), file);
  expect(screen.getByLabelText("或粘贴契约文本")).toHaveValue("openapi: 3.0.3");
  await userEvent.click(screen.getByRole("button", { name: "解析契约" }));
  expect(
    await screen.findByText("契约没有记录 Operation。"),
  ).toBeInTheDocument();
  const huge = new File(["x".repeat(1_000_001)], "huge.json", {
    type: "application/json",
  });
  await userEvent.upload(screen.getByLabelText("上传 OpenAPI 契约"), huge);
  expect(screen.getByRole("alert")).toHaveTextContent("超过 1 MB");
  expect(fetcher).toHaveBeenCalledTimes(1);
});
test("workbench has no execute button and draft does not write browser storage", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(json({ status: "ok", items: [], total: 0 })),
  );
  const store = vi.spyOn(Storage.prototype, "setItem");
  render(
    <MemoryRouter>
      <App />
    </MemoryRouter>,
  );
  await userEvent.type(
    screen.getByLabelText("自然语言测试目标"),
    "验证资源属性",
  );
  await userEvent.click(screen.getByRole("link", { name: "历史测试报告" }));
  await userEvent.click(screen.getByRole("link", { name: "测试工作台" }));
  expect(screen.getByLabelText("自然语言测试目标")).toHaveValue("验证资源属性");
  expect(store).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "开始测试" }),
  ).not.toBeInTheDocument();
});
