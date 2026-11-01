import { useCallback } from "react";
import {
  ArrowUpRight,
  Braces,
  ClipboardList,
  History as HistoryIcon,
  BookmarkCheck,
  LayoutDashboard,
  ShieldCheck,
} from "lucide-react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router";
import { api } from "./api/client";
import { useQuery } from "./useQuery";
import { Projects } from "./pages/Projects";
import { History } from "./pages/History";
import { Inspect, RunDetail } from "./pages/RunDetail";
import { Empty } from "./components/ui";
import { GoalTasks } from "./pages/GoalTasks";
export function App() {
  const { pathname } = useLocation();
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
            AgentTest<small>接口测试</small>
          </div>
        </Link>
        <div className="workspace-label">
          <span className="workspace-avatar">A</span>
          <div>本地工作空间</div>
        </div>
        <div className="nav-label">工作空间</div>
        <nav aria-label="主导航">
          <NavLink to="/" end>
            <LayoutDashboard size={18} />
            项目
          </NavLink>
          <NavLink to="/tasks">
            <ClipboardList size={18} />
            当前测试
          </NavLink>
          <NavLink
            to="/runs"
            end
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            <HistoryIcon size={18} />
            结果
          </NavLink>
          <NavLink to="/saved">
            <BookmarkCheck size={18} />
            已保存测试
          </NavLink>
        </nav>
        <div className="sidebar-bottom">
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
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div>
            <ClipboardList size={16} />
            <span>AgentTest</span>
          </div>
          <span className="topbar-label">
            <ShieldCheck size={14} />
            {pathname === "/tasks"
              ? "当前测试"
              : pathname === "/saved"
                ? "已保存测试"
                : "项目与结果"}
          </span>
        </header>
        <main id="main" tabIndex={-1}>
          <Routes>
            <Route path="/" element={<Projects />} />
            <Route
              path="/runs"
              element={
                <>
                  <GoalTasks recordsOnly />
                  <details>
                    <summary>全部执行报告与问题筛选</summary>
                    <History />
                  </details>
                </>
              }
            />
            <Route path="/tasks" element={<GoalTasks />} />
            <Route
              path="/saved"
              element={<GoalTasks recordsOnly savedOnly />}
            />
            <Route path="/inspect" element={<Inspect />} />
            <Route path="/runs/:runId" element={<RunDetail />} />
            <Route
              path="*"
              element={
                <Empty title="页面不存在">
                  <Link className="inline-link" to="/">
                    返回项目
                    <ArrowUpRight size={15} />
                  </Link>
                </Empty>
              }
            />
          </Routes>
          <footer className="page-footer">
            <span>AgentTest</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
