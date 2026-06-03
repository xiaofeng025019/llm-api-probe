import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { DashboardPage } from "./pages/DashboardPage";
import { ProvidersPage } from "./pages/ProvidersPage";
import { ProviderDetailPage } from "./pages/ProviderDetailPage";
import { ModelsPage } from "./pages/ModelsPage";
import { SettingsPage } from "./pages/SettingsPage";
import "./styles.css";

function App() {
  return (
    <BrowserRouter>
      <div className="app">
        <header className="topbar">
          <h1>LLM 可用性</h1>
          <nav>
            <NavLink to="/" end>
              Dashboard
            </NavLink>
            <NavLink to="/providers">Providers</NavLink>
            <NavLink to="/models">Favorites</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </nav>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/providers" element={<ProvidersPage />} />
            <Route path="/providers/:id" element={<ProviderDetailPage />} />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
