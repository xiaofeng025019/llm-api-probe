import { useMemo, useState } from "react";
import { useDashboard } from "../hooks/useDashboard";
import { formatRelative, modelTypeColor } from "../lib/format";
import { api } from "../api/types";
import { Icon } from "../components/Icons";
import { withErrorToast } from "../lib/action";
import { useT } from "../hooks/useT";

const TYPES = ["all", "chat", "vision", "audio", "image", "embedding", "code"] as const;
const TYPE_KEYS = {
  all: "models.typeAll",
  chat: "models.typeChat",
  vision: "models.typeVision",
  audio: "models.typeAudio",
  image: "models.typeImage",
  embedding: "models.typeEmbedding",
  code: "models.typeCode",
} as const;

export function ModelsPage() {
  const { providers, modelsByProvider, refresh } = useDashboard();
  const t = useT();
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
    for (const ty of TYPES) {
      if (ty === "all") continue;
      counts[ty] = allFavorites.filter((r) => r.type === ty).length;
    }
    return counts;
  }, [allFavorites]);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{t("models.title")}</h1>
          <div className="subtitle">{t("models.subtitle", { n: allFavorites.length })}</div>
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
            placeholder={t("models.searchPlaceholder")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ paddingLeft: 32 }}
            aria-label={t("models.searchAria")}
            type="search"
          />
        </div>
        <div className="window-tabs" role="tablist" aria-label={t("models.typeFilter")}>
          {TYPES.map((ty) => (
            <button
              key={ty}
              className={filter === ty ? "active" : ""}
              onClick={() => setFilter(ty)}
              title={typeCounts[ty] ? t("models.count", { n: typeCounts[ty] }) : ""}
              role="tab"
              aria-selected={filter === ty}
              aria-label={t("models.typeAria", { type: t(TYPE_KEYS[ty]), n: typeCounts[ty] ?? 0 })}
            >
              {t(TYPE_KEYS[ty])}
              {typeCounts[ty] > 0 && (
                <span
                  style={{
                    marginLeft: 6,
                    fontSize: 10,
                    opacity: 0.6,
                    fontWeight: 600,
                  }}
                >
                  {typeCounts[ty]}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className="table-wrap">
        <table>
          <caption className="sr-only">{t("models.column.caption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("models.column.provider")}</th>
              <th scope="col">{t("models.column.model")}</th>
              <th scope="col">{t("models.column.type")}</th>
              <th scope="col">{t("models.column.lastSeen")}</th>
              <th style={{ width: 120 }} scope="col">
                <span className="sr-only">{t("models.column.actions")}</span>
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
                        t("models.unfavorite"),
                      ).then(refresh)
                    }
                    aria-label={t("models.unfavoriteAria", { name: r.model })}
                  >
                    <Icon.Star filled />
                    {t("models.unfavorite")}
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
                    <h3>{t("models.emptyTitle")}</h3>
                    <p>
                      {allFavorites.length === 0
                        ? t("models.emptyNone")
                        : t("models.emptyFilter")}
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
