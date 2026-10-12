import { useEffect, useRef, useState } from "react";
import { FileJson, Upload } from "lucide-react";
import { api } from "../api/client";
import type { ContractSummary } from "../api/types";
import { Badge, Notice, Panel } from "./ui";
export function ContractImport() {
  const [text, setText] = useState("");
  const [summary, setSummary] = useState<ContractSummary>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [filename, setFilename] = useState("");
  const active = useRef<AbortController | null>(null);
  const revision = useRef(0);
  useEffect(
    () => () => {
      revision.current++;
      active.current?.abort();
    },
    [],
  );
  function update(value: string) {
    revision.current++;
    active.current?.abort();
    setBusy(false);
    setText(value);
    setSummary(undefined);
    setError("");
  }
  async function upload(file?: File) {
    if (!file) return;
    const current = ++revision.current;
    active.current?.abort();
    setBusy(false);
    setSummary(undefined);
    setError("");
    setText("");
    if (file.size > 1_000_000) {
      setError("文件超过 1 MB 大小限制。");
      return;
    }
    try {
      const content = await file.text();
      if (current === revision.current) {
        update(content);
        setFilename(file.name);
      }
    } catch {
      if (current === revision.current) setError("无法读取文件，请重新选择。");
    }
  }
  async function parse() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const current = ++revision.current;
    setBusy(true);
    setError("");
    setSummary(undefined);
    try {
      const result = await api.summary(text, controller.signal);
      if (current === revision.current) setSummary(result);
    } catch (e) {
      if (current === revision.current && !controller.signal.aborted)
        setError(e instanceof Error ? e.message : "契约解析失败。");
    } finally {
      if (current === revision.current) setBusy(false);
    }
  }
  return (
    <Panel
      title="OpenAPI 契约"
      subtitle="导入本地 JSON / YAML，只解析，不执行请求"
      action={<FileJson size={20} className="muted" />}
    >
      <label className="upload">
        <Upload size={19} />
        <strong>选择契约文件</strong>
        <span>{filename || "JSON / YAML · 最大 1 MB"}</span>
        <input
          type="file"
          accept=".json,.yaml,.yml,application/json"
          aria-label="上传 OpenAPI 契约"
          onChange={(e) => {
            void upload(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </label>
      <label htmlFor="contract-text" className="field-label">
        或粘贴契约文本
      </label>
      <textarea
        id="contract-text"
        className="code-input"
        value={text}
        onChange={(e) => {
          update(e.target.value);
          setFilename("");
        }}
        placeholder={
          "openapi: 3.1.0\ninfo:\n  title: Your API\n  version: 1.0.0\npaths: {}"
        }
        spellCheck={false}
      />
      <div className="button-row">
        <button
          className="primary"
          disabled={busy || !text.trim()}
          onClick={() => void parse()}
        >
          <FileJson size={16} />
          {busy ? "解析中…" : "解析契约"}
        </button>
        <span className="caption">不会使用契约 servers 发起请求</span>
      </div>
      {error && <Notice error>{error}</Notice>}
      {summary && (
        <div className="contract-result" role="status">
          <div className="section-line">
            <strong>解析完成</strong>
            <Badge tone="success">OpenAPI {summary.version}</Badge>
            <span>{summary.operations?.length ?? 0} 个 Operation</span>
          </div>
          {!summary.operations?.length ? (
            <p className="muted">契约没有记录 Operation。</p>
          ) : (
            <div className="operation-list">
              {summary.operations.map((op, i) => (
                <div key={i}>
                  <span className={`method ${op.method.toLowerCase()}`}>
                    {op.method.toUpperCase()}
                  </span>
                  <code>{op.path}</code>
                  <small>{op.operation_id || "未记录 operationId"}</small>
                </div>
              ))}
            </div>
          )}
          <p className="caption">
            解析摘要不等于兼容性验证、执行授权或业务测试通过。
          </p>
        </div>
      )}
    </Panel>
  );
}
