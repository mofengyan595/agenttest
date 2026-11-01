import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProjectInventory } from "./ProjectInventory";
import { InputValue } from "./InputValue";

test("full denominator and blocked reasons survive pagination", async () => {
  render(
    <ProjectInventory
      value={{
        total: 45,
        counts: { READY: 42, NEEDS_INPUT: 1, UNAUTHORIZED: 1, UNSUPPORTED: 1 },
        operations: Array.from({ length: 45 }, (_, i) => ({
          operation_id: `op${i}`,
          method: "GET",
          path: `/items/${i}`,
          group: "items",
          state: i === 44 ? "NEEDS_INPUT" : "READY",
          reason: i === 44 ? "缺少测试身份" : "已就绪",
          next_step: "补充测试账号",
        })),
      }}
    />,
  );
  expect(screen.getByText("文档共 45 个接口")).toBeInTheDocument();
  await userEvent.click(screen.getByText("查看完整接口清单与下一步"));
  expect(screen.getAllByRole("listitem")).toHaveLength(10);
  await userEvent.selectOptions(
    screen.getByLabelText("按就绪情况查看"),
    "NEEDS_INPUT",
  );
  expect(screen.getByText("GET /items/44")).toBeInTheDocument();
  expect(screen.getByText("下一步：补充测试账号")).toBeInTheDocument();
});

test("nested inputs can be edited through ordinary fields", async () => {
  const changed = vi.fn();
  render(
    <InputValue
      name="details"
      value={{ active: true, labels: ["new"] }}
      onChange={changed}
      schema={{
        type: "object",
        properties: {
          active: { type: "boolean" },
          labels: {
            type: "array",
            items: { type: "string", enum: ["new", "old"] },
          },
        },
      }}
    />,
  );
  await userEvent.selectOptions(
    screen.getByLabelText("details / labels 第 1 项"),
    JSON.stringify("old"),
  );
  expect(changed).toHaveBeenCalledWith({ active: true, labels: ["old"] });
});
