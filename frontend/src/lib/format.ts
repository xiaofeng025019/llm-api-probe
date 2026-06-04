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
  if (!iso) return "never";
  const diff = Date.now() - parseApiDate(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 0) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

export function statusColor(status: "ok" | "degraded" | "fail" | null | undefined): string {
  if (status === "ok") return "var(--ok)";
  if (status === "degraded") return "var(--warn)";
  if (status === "fail") return "var(--fail)";
  return "var(--unknown)";
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
