import { useCallback, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  Braces,
  ClipboardList,
  History as HistoryIcon,
  LayoutDashboard,
  ShieldCheck,
} from "lucide-react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router";
import { api } from "./api/client";
import { useQuery } from "./useQuery";
import { Workbench } from "./pages/Workbench";
import { History } from "./pages/History";
import { Inspect, RunDetail } from "./pages/RunDetail";
import { Empty } from "./components/ui";
import { GoalTasks } from "./pages/GoalTasks";
export function App() {
  const [draft, setDraft] = useState("");
  const { pathname } = useLocation();
  const detailActive = pathname === "/inspect" || pathname.startsWith("/runs/");
  const health = useQuery(
    useCallback((signal: AbortSignal) => api.health(signal), []),
  );
  return (
    <div className="app">
      <a className="skip-link" href="#main">
        跳转到主内容
      </a>
      <aside className="sidebar">
        <Link to="/" className="brand">
          <span className="brand-icon">
            <Braces size={23} />
          </span>
          <div>
            AgentTest<small>API TESTING CONSOLE</small>
          </div>
        </Link>
        <div className="workspace-label">
          <span className="workspace-avatar">A</span>
          <div>
            本地工作空间<small>Local workspace</small>
          </div>
          <span className="workspace-mark">⌘</span>
        </div>
        <div className="nav-label">工作空间</div>
        <nav aria-label="主导航">
          <NavLink to="/" end>
            <LayoutDashboard size={18} />
            测试工作台
          </NavLink>
          <NavLink to="/tasks">
            <ClipboardList size={18} />
            受控测试任务
          </NavLink>
          <Link
            to="/inspect"
            className={detailActive ? "active" : undefined}
            aria-current={detailActive ? "page" : undefined}
          >
            <Activity size={18} />
            运行详情
          </Link>
          <NavLink
            to="/runs"
            end
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            <HistoryIcon size={18} />
            历史测试报告
          </NavLink>
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-info">
            <ShieldCheck size={20} />
            <strong>安全来自明确的边界</strong>
            <p>
              可信策略授权
              <br />
              确定性断言与脱敏证据
            </p>
          </div>
          <div className="api-health">
            <span
              className={`health-dot ${health.data?.status === "ok" ? "online" : ""}`}
            />
            <span>
              {health.loading
                ? "正在连接 API"
                : health.data?.status === "ok"
                  ? "API 已连接"
                  : "API 未连接"}
            </span>
            {health.error && (
              <button aria-label="重试 API 连接" onClick={health.retry}>
                重试
              </button>
            )}
          </div>
          <small className="version">LOCAL CONSOLE · v0.1</small>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div>
            <ClipboardList size={16} />
            <span>AgentTest Console</span>
            <span className="topbar-slash">/</span>
            <span className="muted">可信测试，真实证据</span>
          </div>
          <span className="topbar-label">
            <ShieldCheck size={14} />
            {pathname === "/tasks"
              ? "受控任务 · 项目授权"
              : "READ-ONLY EVIDENCE"}
          </span>
        </header>
        <main id="main" tabIndex={-1}>
          <Routes>
            <Route
              path="/"
              element={<Workbench draft={draft} setDraft={setDraft} />}
            />
            <Route path="/runs" element={<History />} />
            <Route path="/tasks" element={<GoalTasks />} />
            <Route path="/inspect" element={<Inspect />} />
            <Route path="/runs/:runId" element={<RunDetail />} />
            <Route
              path="*"
              element={
                <Empty title="页面不存在">
                  <Link className="inline-link" to="/">
                    返回测试工作台
                    <ArrowUpRight size={15} />
                  </Link>
                </Empty>
              }
            />
          </Routes>
          <footer className="page-footer">
            <span>AgentTest · Agent 决策 + 确定性执行</span>
            <span>不确定的预期，留给审阅。</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
