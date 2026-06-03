export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

export function statusColor(status: "ok" | "fail" | null | undefined): string {
  if (status === "ok") return "var(--ok)";
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
