import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ArrowRight, FolderPlus, RefreshCw } from "lucide-react";
import {
  intakeApi,
  taskApi,
  type IntakeCatalog,
  type IntakeProject,
  type IntakeProfile,
  type GenericSetup,
} from "../api/tasks";
import { Badge, Notice, PageTitle, Panel } from "../components/ui";
import { GenericProject } from "../components/GenericProject";
import { ProjectInventory } from "../components/ProjectInventory";

export function ProfileSummary({ profile }: { profile: IntakeProfile }) {
  return (
    <div className="profile-summary">
      <p>
        <strong>{profile.environment}</strong>
        {profile.address && ` · ${profile.address}`}
      </p>
      <p>
        测试身份：
        {profile.identities.length
          ? profile.identities
              .map((i) => `${i.alias}（${i.ready ? "已准备" : "缺少配置"}）`)
              .join("、")
          : "无需登录"}
      </p>
      {profile.identities
        .filter((i) => !i.ready)
        .map((i) => (
          <Notice key={i.alias} error>
            {i.alias}：{i.instruction}。配置后重启服务，再点击重新检查。
          </Notice>
        ))}
      <p>以下预期来自服务端授权配置。请核对它们是否适用于你的测试项目：</p>
      <ul className="rule-list">
        {profile.rules.map((r, n) => (
          <li key={`${r.id}-${n}`}>
            <strong>{r.behavior}</strong> · {r.role || "匿名身份"}：
            {r.permitted
              ? `响应状态应为 ${r.statuses.join(" / ") || "按字段检查"}`
              : "当前环境不允许执行此操作"}
            {r.fields.map((f, i) => (
              <span key={i}>
                ；{f.field.replace(/^\$\.?/, "响应的 ")}{" "}
                {(
                  {
                    equals: "应等于",
                    eq: "应等于",
                    in: "应属于",
                    exists: "应存在",
                    count: "数量应为",
                    contains: "应包含",
                  } as Record<string, string>
                )[f.operator] || f.operator}
                {f.operator === "exists"
                  ? ""
                  : ` ${JSON.stringify(f.expected)}`}
              </span>
            ))}
            <details>
              <summary>规则依据</summary>
              <p>{r.source}</p>
              <code>
                {r.operation} · {r.id}
              </code>
            </details>
          </li>
        ))}
      </ul>
      {profile.require_review && (
        <Notice>
          此项目原有策略要求审阅执行范围，开始测试后会保留该步骤。
        </Notice>
      )}
    </div>
  );
}

export function Projects() {
  const [catalog, setCatalog] = useState<IntakeCatalog | null>(null);
  const [csrf, setCsrf] = useState("");
  const [editing, setEditing] = useState<IntakeProject | null>(null);
  const [name, setName] = useState("");
  const [businessNotes, setBusinessNotes] = useState("");
  const [document, setDocument] = useState("");
  const [filename, setFilename] = useState("");
  const [profileId, setProfileId] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [generic, setGeneric] = useState<GenericSetup | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [uploading, setUploading] = useState(false);
  const fileRevision = useRef(0);
  const loadRevision = useRef(0);
  const profile = catalog?.profiles.find((p) => p.id === profileId);
  const empty = !!catalog && !catalog.projects.length && !catalog.registered_projects?.length;

  async function load() {
    const revision = ++loadRevision.current;
    setLoading(true);
    setError("");
    try {
      const session = await taskApi.session();
      const result = await intakeApi.read();
      if (revision !== loadRevision.current) return;
      setCsrf(session.csrf_token);
      setCatalog(result);
      setProfileId(
        (current) => current || (result.generic_intake ? "__generic__" : ""),
      );
      setConfirmed(false);
    } catch (e) {
      if (revision === loadRevision.current)
        setError(e instanceof Error ? e.message : "项目服务暂不可用。");
    } finally {
      if (revision === loadRevision.current) setLoading(false);
    }
  }
  useEffect(() => {
    void load();
    return () => {
      loadRevision.current++;
      fileRevision.current++;
    };
  }, []);

  async function upload(file?: File) {
    if (!file) return;
    const revision = ++fileRevision.current;
    setUploading(true);
    setError("");
    setSaved("");
    setDocument("");
    setConfirmed(false);
    setGeneric(null);
    setFilename("");
    if (file.size > 1_000_000) {
      setError("文件超过 1 MB，请缩小接口文档后重新选择。");
      setUploading(false);
      return;
    }
    try {
      const content = await file.text();
      if (revision === fileRevision.current) {
        setDocument(content);
        setFilename(file.name);
      }
    } catch {
      if (revision === fileRevision.current)
        setError("无法读取接口文档，请重新选择。");
    } finally {
      if (revision === fileRevision.current) setUploading(false);
    }
  }

  async function save() {
    if (busy || uploading) return;
    setBusy(true);
    setError("");
    setSaved("");
    try {
      const result = await intakeApi.save(
        csrf,
        {
          name,
          business_notes: businessNotes,
          document,
          profile_id: profileId === "__generic__" ? null : profileId || null,
          profile_identity: profile?.identity || null,
          confirmed,
          version: editing?.version || 0,
          generic: profileId === "__generic__" ? generic : null,
        },
        editing?.id,
      );
      setEditing(result);
      setDocument("");
      setFilename("");
      setCatalog(await intakeApi.read());
      setSaved(
        result.ready
          ? "已保存并登记，可以开始测试。"
          : "接入进度已保存，请补充下方缺失项。",
      );
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "保存失败，填写内容仍保留，请检查后重试。",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageTitle
        eyebrow="项目"
        title={empty ? "接入项目" : "项目"}
        description="接入接口文档和测试环境，查看建议，再开始一轮测试。"
      >
        {catalog && (!empty || catalog.profiles.length > 0) && <button
          onClick={() => void load()} disabled={busy || loading}>
          <RefreshCw size={16} />
          重新检查
        </button>}
      </PageTitle>
      {loading && <p role="status">正在整理项目资料…</p>}
      {error && (
        <Notice error>
          {error}
          <button onClick={() => void load()}>重新连接</button>
        </Notice>
      )}
      {catalog && (
        <>
          {catalog.model_source === "SIMULATED" ? <Notice>
            模拟模型 / 内存模拟目标，仅验证工程流程。
          </Notice> : catalog.generic_intake && (catalog.model_source === "CONFIGURED_MODEL"
            ? <p className="caption" role="status">已读取真实模型配置；开始测试时调用，导入文档不调用。</p>
            : <Notice>真实模型尚未配置。请在本机 .env 或启动环境中设置模型配置；
                不会自动改用模拟模型。</Notice>)}
          {catalog.registered_projects?.map((p) => (
            <Panel
              key={p.id}
              title={p.name || p.id}
              subtitle="已由操作员在服务端登记"
            >
              <Badge tone={p.ready ? "success" : "warning"}>
                {p.ready ? "可提交测试" : "待补充"}
              </Badge>
              {p.missing?.map((m) => (
                <p key={m}>{m}</p>
              ))}
              {p.profile && (
                <details>
                  <summary>环境、身份和业务范围</summary>
                  <ProfileSummary profile={p.profile} />
                </details>
              )}
              {p.ready && (
                <Link className="inline-link" to={`/tasks?project=${p.id}`}>
                  开始测试 →
                </Link>
              )}
            </Panel>
          ))}
          <div className="project-cards">
            {catalog.projects.map((p) => (
              <Panel
                key={p.id}
                title={p.name}
                subtitle={`配置版本 ${p.version}`}
              >
                <Badge tone={p.ready ? "success" : "warning"}>
                  {p.ready
                    ? "可提交测试"
                    : p.state === "DRAFT"
                      ? "接入草稿"
                      : "待补充"}
                </Badge>
                {p.profile && (
                  <p>
                    {p.profile.environment} · {p.profile.name}
                  </p>
                )}
                {p.inventory && <ProjectInventory value={p.inventory} />}
                {!!p.missing.length && (
                  <ul>
                    {p.missing.map((m) => (
                      <li key={m}>{m}</li>
                    ))}
                  </ul>
                )}
                <div className="button-row">
                  {p.ready && (
                    <Link
                      className="primary button-link"
                      to={`/tasks?project=${p.id}`}
                    >
                      开始一轮测试
                      <ArrowRight size={16} />
                    </Link>
                  )}
                  <button
                    disabled={busy}
                    onClick={() => {
                      setEditing(p);
                      setName(p.name);
                      setBusinessNotes(p.business_notes || "");
                      setDocument("");
                      setFilename("");
                      setProfileId(
                        p.generic ? "__generic__" : p.profile_id || "",
                      );
                      setGeneric(p.generic || null);
                      setConfirmed(false);
                      setSaved("");
                    }}
                  >
                    补充信息
                  </button>
                </div>
                <details>
                  <summary>登记详情</summary>
                  <p>项目编号：{p.id}</p>
                  <p>
                    已记录确认：
                    {p.confirmation
                      ? `网页操作者会话，${new Date(p.confirmation.at * 1000).toLocaleString("zh-CN")}`
                      : "尚未确认"}
                  </p>
                  <code>{p.identity}</code>
                </details>
              </Panel>
            ))}
          </div>
          <Panel
            title={editing ? `继续配置：${editing.name}` : "接入新项目"}
            subtitle={empty ? "还没有接入项目。上传文档后查看建议，再确认测试环境。"
              : "导入接口文档，查看建议，再确认测试环境。"}
          >
            <form
              className="intake-form"
              onSubmit={(e) => {
                e.preventDefault();
                void save();
              }}
            >
              <label htmlFor="project-name">项目名称</label>
              <input
                id="project-name"
                required
                maxLength={80}
                value={name}
                disabled={busy}
                onChange={(e) => setName(e.target.value)}
              />
              <label htmlFor="project-notes">业务说明（可选）</label>
              <textarea
                id="project-notes"
                value={businessNotes}
                maxLength={2000}
                disabled={busy}
                placeholder="例如项目用途、测试身份的职责、需要特别注意的业务行为。不要填写凭据。"
                onChange={(e) => setBusinessNotes(e.target.value)}
              />
              <label htmlFor="project-document">
                接口文档（OpenAPI 3.0 / 3.1，JSON 或 YAML）
              </label>
              <input
                id="project-document"
                type="file"
                accept=".json,.yaml,.yml"
                disabled={busy}
                onChange={(e) => void upload(e.target.files?.[0])}
              />
              <small>
                {filename ||
                  (editing
                    ? "已保存原文档；不上传时沿用。"
                    : "最大 1 MB；请勿包含密码、令牌或密钥。")}
              </small>
              <details>
                <summary>或粘贴接口文档</summary>
                <textarea
                  aria-label="接口文档文本"
                  className="code-input"
                  value={document}
                  disabled={busy}
                  maxLength={1_000_000}
                  onChange={(e) => {
                    fileRevision.current++;
                    setUploading(false);
                    setFilename("");
                    setDocument(e.target.value);
                    setConfirmed(false);
                    setGeneric(null);
                  }}
                />
              </details>
              <details open={!catalog.generic_intake}>
                <summary>其他接入方式</summary>
                <label htmlFor="project-environment">服务端授权配置</label>
                <select
                  id="project-environment"
                  value={profileId}
                  disabled={busy}
                  onChange={(e) => {
                    setProfileId(e.target.value);
                    setConfirmed(false);
                  }}
                >
                  <option value="">稍后配置，先保存草稿</option>
                  {catalog.generic_intake && (
                    <option value="__generic__">
                      接入自己的接口（通用配置）
                    </option>
                  )}
                  {catalog.profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} · {p.environment}
                    </option>
                  ))}
                </select>
              </details>
              {profileId === "__generic__" ? (
                <>
                  <GenericProject
                    document={document}
                    projectId={editing?.id}
                    csrf={csrf}
                    value={generic}
                    disabled={busy || uploading}
                    onChange={(value) => {
                      setGeneric(value);
                      setConfirmed(false);
                    }}
                  />
                  {generic && (
                    <label className="task-option">
                      <input
                        type="checkbox"
                        checked={confirmed}
                        disabled={busy}
                        onChange={(e) => setConfirmed(e.target.checked)}
                      />
                      我已核对所选接口、输入和检查规则，确认用于此隔离测试环境
                    </label>
                  )}
                </>
              ) : (
                <Notice>
                  这里只能选择操作员已授权的环境与规则。没有合适配置时，先保存文档；请操作员在服务端登记环境、身份别名与业务映射，再重新检查。网页不接收密钥或任意目标地址。
                </Notice>
              )}
              {profile && (
                <>
                  <ProfileSummary profile={profile} />
                  <label className="task-option">
                    <input
                      type="checkbox"
                      checked={confirmed}
                      disabled={busy}
                      onChange={(e) => setConfirmed(e.target.checked)}
                    />
                    我已核对以上行为和预期，确认适用于此项目
                  </label>
                </>
              )}
              <div className="button-row">
                <button
                  className="primary"
                  disabled={
                    busy ||
                    uploading ||
                    !name.trim() ||
                    (!editing && !document.trim())
                  }
                >
                  <FolderPlus size={16} />
                  {uploading
                    ? "正在读取文档…"
                    : busy
                      ? "正在保存…"
                      : "保存并检查接入"}
                </button>
                {editing && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setEditing(null);
                      setName("");
                      setBusinessNotes("");
                      setDocument("");
                      setFilename("");
                      setProfileId("");
                      setConfirmed(false);
                      setGeneric(null);
                      setSaved("");
                    }}
                  >
                    接入另一个项目
                  </button>
                )}
              </div>
              {saved && <p role="status">{saved}</p>}
              {editing && (
                <div className="intake-feedback">
                  {editing.missing.map((m) => (
                    <p key={m}>{m}</p>
                  ))}
                  {editing.ready && (
                    <Link
                      className="inline-link"
                      to={`/tasks?project=${editing.id}`}
                    >
                      开始一轮测试 →
                    </Link>
                  )}
                </div>
              )}
            </form>
          </Panel>
        </>
      )}
      <p className="caption">
        支持路径、查询、嵌套 JSON、受控凭据、单个 CSV 文件及有限异步等待。
        接口依赖和成功条件需要先核对，超出等待范围会明确保留为未完成。
      </p>
    </>
  );
}
