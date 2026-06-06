import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";
import { DashboardPage } from "./pages/DashboardPage";
import { ProviderDetailPage } from "./pages/ProviderDetailPage";
import { ModelsPage } from "./pages/ModelsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ErrorsPage } from "./pages/ErrorsPage";
import { ThemeToggle } from "./components/ThemeToggle";
import { LanguageToggle } from "./components/LanguageToggle";
import { ToastHost, pushToast } from "./components/Toast";
import { useSse } from "./hooks/useSse";
import { useT } from "./hooks/useT";
import "./styles.css";

/** Wires global SSE -> toasts; rendered once at the app root. */
function shortId(id: string | null | undefined): string {
  return id ? `${id.slice(0, 8)}…` : "";
}

function rawProbeError(message: string | null | undefined, code?: string | null): string {
  const text = (message ?? code ?? "failed").trim();
  return text.length > 220 ? `${text.slice(0, 217)}...` : text;
}

function GlobalSse() {
  const t = useT();
  useSse((event, data) => {
    if (event === "job.error" && data && typeof data === "object") {
      const d = data as {
        provider_id: string;
        provider_name?: string | null;
        model_id: string | null;
        model_name?: string | null;
        model_is_favorite?: boolean | null;
        error_code?: string | null;
        message: string;
      };
      if (!d.model_id || d.model_is_favorite !== true) return;
      const provider = d.provider_name || `provider ${shortId(d.provider_id)}`;
      const model = d.model_name || (d.model_id ? `model ${shortId(d.model_id)}` : "");
      pushToast(
        "fail",
        t("toast.probeFailed"),
        [provider, model, rawProbeError(d.message, d.error_code)].filter(Boolean).join(" · "),
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
            <NavLink to="/errors">{t("nav.errors")}</NavLink>
            <NavLink to="/settings">{t("nav.settings")}</NavLink>
          </nav>
          <div className="spacer" />
          <LanguageToggle />
          <ThemeToggle />
        </header>
        <main id="main" tabIndex={-1}>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            {/* /providers used to render a dedicated page; the page was
                removed 2026-06-06 because the Dashboard already covered
                the same surface. Keep the redirect so old bookmarks and
                external links don't 404. */}
            <Route path="/providers" element={<Navigate to="/" replace />} />
            <Route path="/providers/:id" element={<ProviderDetailPage />} />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/errors" element={<ErrorsPage />} />
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
