import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { DesignerPage } from "./pages/DesignerPage";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="designer" element={<DesignerPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
