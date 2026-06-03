export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function statusColor(status: "ok" | "fail" | null | undefined): string {
  if (status === "ok") return "#16a34a";
  if (status === "fail") return "#dc2626";
  return "#9ca3af";
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
