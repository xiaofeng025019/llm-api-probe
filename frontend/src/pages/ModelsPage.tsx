import { useMemo, useState } from "react";
import { useDashboard } from "../hooks/useDashboard";
import { formatRelative, modelTypeColor } from "../lib/format";
import { api } from "../api/types";
import { Icon } from "../components/Icons";
import { withErrorToast } from "../lib/action";

const TYPES = ["all", "chat", "vision", "audio", "image", "embedding", "code"] as const;

export function ModelsPage() {
  const { providers, modelsByProvider, refresh } = useDashboard();
  const [filter, setFilter] = useState<(typeof TYPES)[number]>("all");
  const [search, setSearch] = useState("");

  const allFavorites = useMemo(() => {
    const rows: Array<{
      model_id: string;
      model: string;
      type: string;
      provider_id: string;
      provider_name: string;
      last_seen_at: string;
    }> = [];
    for (const [pid, ms] of Object.entries(modelsByProvider)) {
      const p = providers.find((x) => x.id === pid);
      for (const m of ms) {
        if (m.is_favorite) {
          rows.push({
            model_id: m.id,
            model: m.model_id,
            type: m.type,
            provider_id: pid,
            provider_name: p?.name ?? pid,
            last_seen_at: m.last_seen_at,
          });
        }
      }
    }
    rows.sort((a, b) => a.model.localeCompare(b.model));
    return rows;
  }, [modelsByProvider, providers]);

  const filtered = useMemo(() => {
    return allFavorites.filter((r) => {
      if (filter !== "all" && r.type !== filter) return false;
      if (search && !r.model.toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    });
  }, [allFavorites, filter, search]);

  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = { all: allFavorites.length };
    for (const t of TYPES) {
      if (t === "all") continue;
      counts[t] = allFavorites.filter((r) => r.type === t).length;
    }
    return counts;
  }, [allFavorites]);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Favorite Models</h1>
          <div className="subtitle">{allFavorites.length} models under active monitoring</div>
        </div>
      </div>

      <div className="toolbar">
        <div style={{ position: "relative", flex: 1, maxWidth: 320 }}>
          <span
            style={{
              position: "absolute",
              left: 10,
              top: "50%",
              transform: "translateY(-50%)",
              color: "var(--muted)",
              pointerEvents: "none",
            }}
          >
            <Icon.Search />
          </span>
          <input
            placeholder="Search models…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ paddingLeft: 32 }}
            aria-label="Search models"
            type="search"
          />
        </div>
        <div className="window-tabs" role="tablist" aria-label="Filter by type">
          {TYPES.map((t) => (
            <button
              key={t}
              className={filter === t ? "active" : ""}
              onClick={() => setFilter(t)}
              title={typeCounts[t] ? `${typeCounts[t]} items` : ""}
              role="tab"
              aria-selected={filter === t}
              aria-label={`Type ${t} (${typeCounts[t] ?? 0} items)`}
            >
              {t}
              {typeCounts[t] > 0 && (
                <span
                  style={{
                    marginLeft: 6,
                    fontSize: 10,
                    opacity: 0.6,
                    fontWeight: 600,
                  }}
                >
                  {typeCounts[t]}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className="table-wrap">
        <table>
          <caption className="sr-only">Favorite models</caption>
          <thead>
            <tr>
              <th scope="col">Provider</th>
              <th scope="col">Model</th>
              <th scope="col">Type</th>
              <th scope="col">Last seen</th>
              <th style={{ width: 120 }} scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.model_id}>
                <td>{r.provider_name}</td>
                <td>
                  <strong>{r.model}</strong>
                </td>
                <td>
                  <span
                    className="type-badge"
                    style={{ background: modelTypeColor(r.type) }}
                  >
                    {r.type}
                  </span>
                </td>
                <td className="muted">{formatRelative(r.last_seen_at)}</td>
                <td>
                  <button
                    className="secondary sm"
                    onClick={() =>
                      withErrorToast(
                        api.patchModel(r.model_id, { is_favorite: false }),
                        "Unfavorite",
                      ).then(refresh)
                    }
                    aria-label={`Unfavorite ${r.model}`}
                  >
                    <Icon.Star filled />
                    Unfavorite
                  </button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5}>
                  <div className="empty-state">
                    <div className="empty-state-icon">
                      <Icon.Star />
                    </div>
                    <h3>No matching favorites</h3>
                    <p>
                      {allFavorites.length === 0
                        ? "Click the ★ on a model in the Provider detail or Dashboard to favorite it."
                        : "Try a different filter or search term."}
                    </p>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
