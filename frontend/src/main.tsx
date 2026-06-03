import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { DashboardPage } from "./pages/DashboardPage";
import { ProvidersPage } from "./pages/ProvidersPage";
import { ProviderDetailPage } from "./pages/ProviderDetailPage";
import { ModelsPage } from "./pages/ModelsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ThemeToggle } from "./components/ThemeToggle";
import { ToastHost, pushToast } from "./components/Toast";
import { useSse } from "./hooks/useSse";
import "./styles.css";

/** Wires global SSE -> toasts; rendered once at the app root. */
function GlobalSse() {
  useSse((event, data) => {
    if (event === "job.error" && data && typeof data === "object") {
      const d = data as { provider_id: number; model_id: number | null; message: string };
      pushToast("fail", `Provider #${d.provider_id} failed`, d.message ?? "unknown error");
    }
  });
  return null;
}

function App() {
  return (
    <BrowserRouter>
      <a href="#main" className="skip-link">
        跳到主要内容
      </a>
      <div className="app">
        <header className="topbar" role="banner">
          <div className="brand">
            <div className="brand-mark" aria-hidden="true">
              L
            </div>
            <span>LLM 可用性</span>
          </div>
          <nav aria-label="主导航">
            <NavLink to="/" end>
              Dashboard
            </NavLink>
            <NavLink to="/providers">Providers</NavLink>
            <NavLink to="/models">Favorites</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </nav>
          <div className="spacer" />
          <ThemeToggle />
        </header>
        <main id="main" tabIndex={-1}>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/providers" element={<ProvidersPage />} />
            <Route path="/providers/:id" element={<ProviderDetailPage />} />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
      <ToastHost />
      <GlobalSse />
    </BrowserRouter>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
