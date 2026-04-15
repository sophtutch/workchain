import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { DesignerPage } from "./pages/DesignerPage";
import { LandingPage } from "./pages/LandingPage";
import { WorkflowDetailPage } from "./pages/WorkflowDetailPage";
import { WorkflowsPage } from "./pages/WorkflowsPage";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Landing page is full-bleed and sits outside the AppShell chrome. */}
        <Route index element={<LandingPage />} />
        {/* All product pages share the nav bar via AppShell. */}
        <Route element={<AppShell />}>
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="workflows" element={<WorkflowsPage />} />
          <Route path="workflows/:id" element={<WorkflowDetailPage />} />
          <Route path="designer" element={<DesignerPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
