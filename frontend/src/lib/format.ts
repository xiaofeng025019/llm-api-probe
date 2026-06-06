import { t as i18nT } from "./i18n";

export function parseApiDate(iso: string): Date {
  // SQLite/FastAPI currently returns UTC timestamps without a timezone suffix
  // (for example "2026-06-04T07:28:13"). Browsers parse that as local time,
  // so append Z only when the value has no explicit timezone.
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso);
  return new Date(hasTimezone ? iso : `${iso}Z`);
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return parseApiDate(iso).toLocaleString();
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return i18nT("time.never");
  const diff = Date.now() - parseApiDate(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 0) return i18nT("time.justNow");
  if (sec < 60) return i18nT("time.secondsAgo", { n: sec });
  const min = Math.floor(sec / 60);
  if (min < 60) return i18nT("time.minutesAgo", { n: min });
  const hr = Math.floor(min / 60);
  if (hr < 24) return i18nT("time.hoursAgo", { n: hr });
  const day = Math.floor(hr / 24);
  return i18nT("time.daysAgo", { n: day });
}

export function statusColor(status: "ok" | "degraded" | "fail" | null | undefined): string {
  if (status === "ok") return "var(--ok)";
  if (status === "degraded") return "var(--warn)";
  if (status === "fail") return "var(--fail)";
  return "var(--unknown)";
}

export function modelHealthClass(status: string | null | undefined, enabled = true): string {
  if (!enabled) return "warn";
  if (status === "online") return "ok";
  if (status === "offline" || status === "unauthorized" || status === "not_found") return "fail";
  if (status === "suspect" || status === "rate_limited") return "warn";
  // "stale" is informational: not a failure (the model is
  // presumably still online), but also not "ok" — we haven't
  // actually checked recently. Same visual tier as "suspect".
  if (status === "stale") return "warn";
  return "unknown";
}

export function modelHealthLabel(status: string | null | undefined, enabled = true): string {
  if (!enabled) return i18nT("status.disabled");
  if (status === "online") return i18nT("status.online");
  if (status === "suspect") return i18nT("status.suspect");
  if (status === "stale") return i18nT("status.stale");
  if (status === "offline") return i18nT("status.offline");
  if (status === "rate_limited") return i18nT("status.rateLimited");
  if (status === "unauthorized") return i18nT("status.unauthorized");
  if (status === "not_found") return i18nT("status.notFound");
  return i18nT("common.unknown");
}

export function modelTypeColor(type: string): string {
  switch (type) {
    case "chat":
      return "#2563eb";
    case "vision":
      return "#7c3aed";
    case "audio":
      return "#db2777";
    case "image":
      return "#ea580c";
    case "embedding":
      return "#0891b2";
    case "code":
      return "#65a30d";
    default:
      return "#6b7280";
  }
}

export function modelTypeBg(type: string): string {
  return modelTypeColor(type);
}

export function formatPercent(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${n.toFixed(1)}%`;
}

export function formatMs(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1000) return `${(n / 1000).toFixed(2)}s`;
  return `${Math.round(n)}ms`;
}

export function formatInterval(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds >= 3600 && seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60}m`;
  return `${Math.round(seconds)}s`;
}
