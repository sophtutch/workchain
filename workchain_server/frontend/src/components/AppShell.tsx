import { NavLink, Outlet } from "react-router-dom";
import {
  LayoutDashboard, PenTool, Workflow,
  Clock, Loader, CheckCircle2, XCircle, AlertTriangle, Ban,
} from "lucide-react";
import { useServerConfig } from "../hooks/useServerConfig";
import { useStats } from "../hooks/useStats";

const STATUS_CFG = [
  { key: "pending",       icon: <Clock size={14} />,         label: "Pending" },
  { key: "running",       icon: <Loader size={14} />,        label: "Running" },
  { key: "completed",     icon: <CheckCircle2 size={14} />,  label: "Completed" },
  { key: "failed",        icon: <XCircle size={14} />,       label: "Failed" },
  { key: "needs_review",  icon: <AlertTriangle size={14} />, label: "Review" },
  { key: "cancelled",     icon: <Ban size={14} />,           label: "Cancelled" },
] as const;

export function AppShell() {
  const config = useServerConfig();
  const title = config?.server_title ?? "Workchain";
  const stats = useStats();

  return (
    <div className="shell">
      <nav className="nav-bar">
        <div className="nav-bar__brand"><Workflow size={18} /> {title}</div>
        <div className="nav-bar__links">
          <NavLink to="/" end className={({ isActive }) => `nav-bar__link ${isActive ? "nav-bar__link--active" : ""}`}>
            <LayoutDashboard size={14} /> Dashboard
          </NavLink>
          <NavLink to="/designer" className={({ isActive }) => `nav-bar__link ${isActive ? "nav-bar__link--active" : ""}`}>
            <PenTool size={14} /> Designer
          </NavLink>
        </div>
        {stats && (
          <div className="nav-bar__stats">
            {STATUS_CFG.map(({ key, icon, label }) => {
              const count = stats[key as keyof typeof stats] ?? 0;
              return (
                <div key={key} className={`nav-stat nav-stat--${key}`}>
                  {icon}
                  <span className="nav-stat__count">{count}</span>
                  <span className="nav-stat__label">{label}</span>
                </div>
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
