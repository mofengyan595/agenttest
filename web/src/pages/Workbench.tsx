import { useCallback, useState } from "react";
import {
  ArrowRight,
  Download,
  FlaskConical,
  LockKeyhole,
  ShieldCheck,
  Terminal,
  Trash2,
} from "lucide-react";
import { Link } from "react-router";
import { api } from "../api/client";
import { useQuery } from "../useQuery";
import { ContractImport } from "../components/ContractImport";
import { RunTable } from "../components/RunTable";
import {
  Badge,
  Empty,
  Failure,
  Loading,
  Notice,
  PageTitle,
  Panel,
} from "../components/ui";

export function Workbench({
  draft,
  setDraft,
}: {
  draft: string;
  setDraft: (value: string) => void;
}) {
  const recent = useQuery(
    useCallback((signal: AbortSignal) => api.runs(0, "", "desc", signal), []),
  );
  const [saved, setSaved] = useState(false);
  function save() {
    const url = URL.createObjectURL(
      new Blob([draft], { type: "text/plain;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "agenttest-task-draft.txt";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setSaved(true);
  }
  return (
    <>
      <PageTitle
        eyebrow="WORKSPACE / 测试工作台"
        title="从目标出发，让证据作答。"
        description="导入 API 契约，梳理测试目标，查看可信执行留下的每一份证据。"
      >
        <Badge tone="success">
          <ShieldCheck size={13} />
          受控 · 本地控制台
        </Badge>
      </PageTitle>
      <div className="boundary-strip">
        <ShieldCheck size={18} />
        <strong>LLM 规划 · 策略授权 · 确定性执行</strong>
        <span>面向不同 HTTP API 业务系统</span>
      </div>
      <div className="work-grid">
        <div>
          <Panel
            title="你想验证什么？"
            subtitle="测试目标草稿"
            action={
              <Link className="inline-link" to="/tasks">
                发起受控测试
              </Link>
            }
          >
            <label className="sr-only" htmlFor="task-draft">
              自然语言测试目标
            </label>
            <textarea
              id="task-draft"
              className="task-input"
              maxLength={8000}
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                setSaved(false);
              }}
              placeholder="例如：使用管理员账号创建一个资源，再查询并验证其属性一致性。"
            />
            <div className="draft-meta">
              <span>仅草稿 · 未执行</span>
              <span>{draft.length} / 8000</span>
            </div>
            <Notice>
              填写自然语言不等于完成规划，也不等于获得执行授权。请勿输入密码、JWT
              或 API Key。
            </Notice>
            <div className="button-row">
              <button disabled={!draft.trim()} onClick={save}>
                <Download size={16} />
                保存本地草稿
              </button>
              <button
                className="text-button"
                disabled={!draft}
                onClick={() => {
                  setDraft("");
                  setSaved(false);
                }}
              >
                <Trash2 size={15} />
                清空
              </button>
              {saved && (
                <span role="status" className="success-text">
                  已生成 .txt 草稿文件
                </span>
              )}
            </div>
            <p className="caption">
              草稿仅保留在当前页面会话；保存会下载文本文件，不写入浏览器存储。
            </p>
          </Panel>
          <Panel
            title="安全离线演示"
            subtitle="由操作员启动，在隔离内存目标中运行"
            action={<FlaskConical size={20} className="muted" />}
          >
            <div className="demo-scenarios">
              <div>
                <span className="demo-number">01</span>
                <strong>正常测试</strong>
                <small>批准策略通过</small>
              </div>
              <div>
                <span className="demo-number">02</span>
                <strong>故障检测</strong>
                <small>保留断言失败</small>
              </div>
              <div>
                <span className="demo-number">03</span>
                <strong>只读调查</strong>
                <small>不修改原业务结果</small>
              </div>
            </div>
            <div className="terminal-note">
              <Terminal size={17} />
              <div>
                <strong>使用现有 CLI 生成报告</strong>
                <code>python -m agenttest showcase --case all</code>
              </div>
            </div>
            <p className="caption">
              基础依赖可选 normal / fault；all 含可选 Agent / MCP
              依赖。输出到后端配置的同一报告目录后，在历史中刷新查看。
            </p>
            <Notice>
              <LockKeyhole size={14} /> Web
              受控任务入口复用操作员注册的项目、授权与预算；在任务页查看计划、进度及脱敏结果。
            </Notice>
          </Panel>
        </div>
        <ContractImport />
      </div>
      <Panel
        title="最近的测试运行"
        subtitle="来自当前 ReportStore 的真实记录"
        action={
          <Link className="inline-link" to="/runs">
            全部报告
            <ArrowRight size={15} />
          </Link>
        }
      >
        {recent.loading ? (
          <Loading />
        ) : recent.error ? (
          <Failure message={recent.error} retry={recent.retry} />
        ) : recent.data?.items.length ? (
          <RunTable items={recent.data.items.slice(0, 5)} />
        ) : (
          <Empty title="还没有测试报告">
            <p>运行离线 showcase，并确认 API 指向相同的报告目录。</p>
          </Empty>
        )}
      </Panel>
    </>
  );
}
