import { useMemo, useState } from "react";
import { useDashboard } from "../hooks/useDashboard";
import { formatTime, modelTypeColor } from "../lib/format";

export function ModelsPage() {
  const { providers, modelsByProvider, refresh } = useDashboard();
  const [filter, setFilter] = useState<string>("all");

  const allFavorites = useMemo(() => {
    const rows: Array<{
      model_id: number;
      model: string;
      type: string;
      provider_id: number;
      provider_name: string;
      last_seen_at: string;
    }> = [];
    for (const [pid, ms] of Object.entries(modelsByProvider)) {
      const p = providers.find((x) => x.id === Number(pid));
      for (const m of ms) {
        if (m.is_favorite) {
          rows.push({
            model_id: m.id,
            model: m.model_id,
            type: m.type,
            provider_id: Number(pid),
            provider_name: p?.name ?? String(pid),
            last_seen_at: m.last_seen_at,
          });
        }
      }
    }
    rows.sort((a, b) => a.model.localeCompare(b.model));
    return rows;
  }, [modelsByProvider, providers]);

  const filtered = useMemo(
    () => (filter === "all" ? allFavorites : allFavorites.filter((r) => r.type === filter)),
    [allFavorites, filter],
  );

  return (
    <div>
      <div className="toolbar">
        <strong>收藏模型 ({allFavorites.length})</strong>
        <span style={{ flex: 1 }} />
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">all types</option>
          <option value="chat">chat</option>
          <option value="vision">vision</option>
          <option value="audio">audio</option>
          <option value="image">image</option>
          <option value="embedding">embedding</option>
          <option value="code">code</option>
        </select>
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Model</th>
              <th>Type</th>
              <th>Last seen</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.model_id}>
                <td>{r.provider_name}</td>
                <td>{r.model}</td>
                <td>
                  <span className="badge" style={{ background: modelTypeColor(r.type) }}>
                    {r.type}
                  </span>
                </td>
                <td className="muted">{formatTime(r.last_seen_at)}</td>
                <td>
                  <button
                    className="secondary"
                    onClick={() =>
                      api.patchModel(r.model_id, { is_favorite: false }).then(refresh)
                    }
                  >
                    Unfavorite
                  </button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  没有收藏的模型。在 Provider 详情或 Dashboard 的模型表里点 ★。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

import { api } from "../api/types";
