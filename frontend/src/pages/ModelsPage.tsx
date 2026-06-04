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
          <h1>收藏模型</h1>
          <div className="subtitle">{allFavorites.length} 个模型被重点监测</div>
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
            placeholder="搜索模型…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ paddingLeft: 32 }}
            aria-label="搜索模型"
            type="search"
          />
        </div>
        <div className="window-tabs" role="tablist" aria-label="按类型过滤">
          {TYPES.map((t) => (
            <button
              key={t}
              className={filter === t ? "active" : ""}
              onClick={() => setFilter(t)}
              title={typeCounts[t] ? `${typeCounts[t]} 个` : ""}
              role="tab"
              aria-selected={filter === t}
              aria-label={`类型 ${t}（${typeCounts[t] ?? 0} 个）`}
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
          <caption className="sr-only">收藏模型列表</caption>
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
                    aria-label={`取消收藏 ${r.model}`}
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
                    <h3>没有匹配的收藏模型</h3>
                    <p>
                      {allFavorites.length === 0
                        ? "在 Provider 详情或 Dashboard 的模型表里点 ★ 收藏。"
                        : "试试其他过滤条件。"}
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
