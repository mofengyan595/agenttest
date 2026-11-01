import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InputValue } from "./InputValue";

test("an empty object array can add an item and edit its nested fields", async () => {
  function Editor() {
    const [value, setValue] = useState<unknown>([]);
    return (
      <>
        <InputValue
          name="成员"
          value={value}
          onChange={setValue}
          schema={{
            type: "array",
            maxItems: 2,
            items: {
              type: "object",
              properties: {
                姓名: { type: "string" },
                数量: { type: "integer", minimum: 1 },
                等级: { type: "string", enum: ["普通", "优先"] },
              },
            },
          }}
        />
        <output aria-label="当前输入">{JSON.stringify(value)}</output>
      </>
    );
  }
  const user = userEvent.setup();
  render(<Editor />);
  await user.click(screen.getByRole("button", { name: "添加一项" }));
  await user.type(screen.getByLabelText("成员 第 1 项 / 姓名"), "张三");
  await user.selectOptions(
    screen.getByLabelText("成员 第 1 项 / 等级"),
    JSON.stringify("优先"),
  );
  expect(screen.getByLabelText("当前输入")).toHaveTextContent(
    JSON.stringify([{ 姓名: "张三", 数量: 1, 等级: "优先" }]),
  );
  await user.click(screen.getByRole("button", { name: "移除此项" }));
  await user.click(screen.getByRole("button", { name: "添加一项" }));
  expect(screen.getByLabelText("成员 第 1 项 / 姓名")).toHaveValue("");
});
