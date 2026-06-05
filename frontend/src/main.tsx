import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { DashboardPage } from "./pages/DashboardPage";
import { ProvidersPage } from "./pages/ProvidersPage";
import { ProviderDetailPage } from "./pages/ProviderDetailPage";
import { ModelsPage } from "./pages/ModelsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ThemeToggle } from "./components/ThemeToggle";
import { LanguageToggle } from "./components/LanguageToggle";
import { ToastHost, pushToast } from "./components/Toast";
import { useSse } from "./hooks/useSse";
import { useT } from "./hooks/useT";
import "./styles.css";

/** Wires global SSE -> toasts; rendered once at the app root. */
function GlobalSse() {
  const t = useT();
  useSse((event, data) => {
    if (event === "job.error" && data && typeof data === "object") {
      const d = data as { provider_id: string; model_id: string | null; message: string };
      pushToast(
        "fail",
        `${t("appName")} — provider ${d.provider_id.slice(0, 8)}… failed`,
        d.message ?? t("errors.action.title"),
      );
    }
  });
  return null;
}

function App() {
  const t = useT();
  return (
    <BrowserRouter>
      <a href="#main" className="skip-link">
        {t("common.skipToMain")}
      </a>
      <div className="app">
        <header className="topbar" role="banner">
          <div className="brand">
            <div className="brand-mark" aria-hidden="true">
              L
            </div>
            <span>{t("appName")}</span>
          </div>
          <nav aria-label={t("common.primaryNav")}>
            <NavLink to="/" end>
              {t("nav.dashboard")}
            </NavLink>
            <NavLink to="/providers">{t("nav.providers")}</NavLink>
            <NavLink to="/settings">{t("nav.settings")}</NavLink>
          </nav>
          <div className="spacer" />
          <LanguageToggle />
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
