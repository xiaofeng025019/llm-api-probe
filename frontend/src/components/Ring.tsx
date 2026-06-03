interface RingProps {
  /** 0-100, or null for indeterminate */
  value: number | null;
  size?: number;
  stroke?: number;
  /** Color for the progress arc */
  color?: string;
  /** Track color */
  trackColor?: string;
  label?: React.ReactNode;
}

/** SVG circular progress / availability ring. */
export function Ring({
  value,
  size = 64,
  stroke = 6,
  color,
  trackColor = "var(--divider)",
  label,
}: RingProps) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  const offset = c * (1 - pct / 100);
  const arcColor = color ?? ringColor(pct);
  const isIndet = value == null;

  return (
    <div
      style={{
        position: "relative",
        width: size,
        height: size,
        display: "inline-grid",
        placeItems: "center",
      }}
      role={isIndet ? "progressbar" : "meter"}
      aria-valuenow={value ?? undefined}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={trackColor}
          strokeWidth={stroke}
          fill="none"
        />
        {isIndet ? (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            stroke="var(--primary)"
            strokeWidth={stroke}
            fill="none"
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={c * 0.6}
            style={{
              animation: "ring-spin 1.2s linear infinite",
            }}
          />
        ) : (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            stroke={arcColor}
            strokeWidth={stroke}
            fill="none"
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={offset}
            style={{
              transition: "stroke-dashoffset 600ms cubic-bezier(0.4,0,0.2,1), stroke 200ms",
            }}
          />
        )}
      </svg>
      {label && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "grid",
            placeItems: "center",
            fontSize: size * 0.22,
            fontWeight: 600,
            color: "var(--text)",
          }}
        >
          {label}
        </div>
      )}
    </div>
  );
}

function ringColor(pct: number): string {
  if (pct >= 99) return "var(--ok)";
  if (pct >= 90) return "var(--warn)";
  return "var(--fail)";
}
