import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, PenTool, Workflow, List,
  Clock, Loader, CheckCircle2, XCircle, AlertTriangle, Ban,
} from "lucide-react";
import { useServerConfig } from "../hooks/useServerConfig";
import { useStats } from "../hooks/useStats";

const STATUS_CFG = [
  { key: "pending",       icon: <Clock size={16} />,         label: "Pending" },
  { key: "running",       icon: <Loader size={16} />,        label: "Running" },
  { key: "completed",     icon: <CheckCircle2 size={16} />,  label: "Completed" },
  { key: "failed",        icon: <XCircle size={16} />,       label: "Failed" },
  { key: "needs_review",  icon: <AlertTriangle size={16} />, label: "Review" },
  { key: "cancelled",     icon: <Ban size={16} />,           label: "Cancelled" },
] as const;

export function AppShell() {
  const config = useServerConfig();
  const title = config?.server_title ?? "Workchain";
  const stats = useStats();
  const navigate = useNavigate();

  return (
    <div className="shell">
      <nav className="nav-bar">
        <Link to="/" className="nav-bar__brand">
          <Workflow size={22} /> {title}
        </Link>
        <div className="nav-bar__links">
          <NavLink to="/dashboard" className={({ isActive }) => `nav-bar__link ${isActive ? "nav-bar__link--active" : ""}`}>
            <LayoutDashboard size={16} /> Dashboard
          </NavLink>
          <NavLink to="/workflows" className={({ isActive }) => `nav-bar__link ${isActive ? "nav-bar__link--active" : ""}`}>
            <List size={16} /> Workflows
          </NavLink>
          <NavLink to="/designer" className={({ isActive }) => `nav-bar__link ${isActive ? "nav-bar__link--active" : ""}`}>
            <PenTool size={16} /> Designer
          </NavLink>
        </div>
        {stats && (
          <div className="nav-bar__stats">
            {STATUS_CFG.map(({ key, icon, label }) => {
              const count = stats[key as keyof typeof stats] ?? 0;
              return (
                <button
                  key={key}
                  className={`nav-stat nav-stat--${key}`}
                  onClick={() => navigate(`/workflows?status=${key}`)}
                  title={`View ${label.toLowerCase()} workflows`}
                >
                  {icon}
                  <span className="nav-stat__count">{count}</span>
                </button>
              );
            })}
          </div>
        )}
      </nav>
      <main className="shell__content">
        <Outlet />
      </main>
    </div>
  );
}
