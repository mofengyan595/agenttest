type Schema = {
  type?: string;
  enum?: unknown[];
  properties?: Record<string, Schema>;
  items?: Schema;
  minItems?: number;
  maxItems?: number;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
};

function initialValue(schema: Schema = {}): unknown {
  if (schema.enum?.length) return schema.enum[0];
  if (schema.type === "object")
    return Object.fromEntries(
      Object.entries(schema.properties || {}).map(([name, child]) => [
        name,
        initialValue(child),
      ]),
    );
  if (schema.type === "array")
    return Array.from({ length: Math.min(schema.minItems || 0, 8) }, () =>
      initialValue(schema.items),
    );
  if (schema.type === "boolean") return false;
  if (schema.type === "integer" || schema.type === "number")
    return schema.minimum ?? 0;
  return "";
}

export function InputValue({
  name,
  value,
  schema = {},
  onChange,
}: {
  name: string;
  value: unknown;
  schema?: Schema;
  onChange: (value: unknown) => void;
}) {
  if (
    schema.type === "object" ||
    (value !== null && typeof value === "object" && !Array.isArray(value))
  ) {
    const object = (value || {}) as Record<string, unknown>;
    return (
      <fieldset>
        <legend>{name}</legend>
        {Object.entries(object).map(([k, v]) => (
          <InputValue
            key={k}
            name={`${name} / ${k}`}
            value={v}
            schema={schema.properties?.[k]}
            onChange={(next) => onChange({ ...object, [k]: next })}
          />
        ))}
      </fieldset>
    );
  }
  if (schema.type === "array" || Array.isArray(value)) {
    const list = Array.isArray(value) ? value : [];
    return (
      <fieldset>
        <legend>{name}</legend>
        {list.map((v, i) => (
          <div key={i}>
            <InputValue
              name={`${name} 第 ${i + 1} 项`}
              value={v}
              schema={schema.items}
              onChange={(next) =>
                onChange(list.map((v, j) => (j === i ? next : v)))
              }
            />
            <button
              type="button"
              disabled={list.length <= (schema.minItems || 0)}
              onClick={() => onChange(list.filter((_, j) => j !== i))}
            >
              移除此项
            </button>
          </div>
        ))}
        <button
          type="button"
          disabled={list.length >= Math.min(schema.maxItems ?? 8, 8)}
          onClick={() => onChange([...list, initialValue(schema.items)])}
        >
          添加一项
        </button>
      </fieldset>
    );
  }
  if (schema.enum)
    return (
      <label>
        {name}
        <select
          value={JSON.stringify(value)}
          onChange={(e) => onChange(JSON.parse(e.target.value))}
        >
          {schema.enum.map((v, i) => (
            <option key={i} value={JSON.stringify(v)}>
              {String(v)}
            </option>
          ))}
        </select>
      </label>
    );
  if (schema.type === "boolean" || typeof value === "boolean")
    return (
      <label>
        {name}
        <select
          value={String(value)}
          onChange={(e) => onChange(e.target.value === "true")}
        >
          <option value="true">是</option>
          <option value="false">否</option>
        </select>
      </label>
    );
  const numeric =
    schema.type === "integer" ||
    schema.type === "number" ||
    typeof value === "number";
  return (
    <label>
      {name}
      <input
        type={numeric ? "number" : "text"}
        value={String(value ?? "")}
        min={schema.minimum}
        max={schema.maximum}
        minLength={schema.minLength}
        maxLength={schema.maxLength}
        onChange={(e) =>
          onChange(numeric ? Number(e.target.value) : e.target.value)
        }
      />
    </label>
  );
}
