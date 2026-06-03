interface SkeletonProps {
  width?: number | string;
  height?: number | string;
  rounded?: boolean;
  className?: string;
}

export function Skeleton({ width, height = 16, rounded = false, className }: SkeletonProps) {
  return (
    <span
      className={`skeleton ${className ?? ""}`}
      style={{
        display: "inline-block",
        width: width ?? "100%",
        height,
        borderRadius: rounded ? "999px" : "var(--r-sm)",
      }}
    />
  );
}

export function SkeletonCard() {
  return (
    <div className="card">
      <Skeleton width="40%" height={18} />
      <div style={{ marginTop: 12 }}>
        <Skeleton width="100%" height={14} />
      </div>
      <div style={{ marginTop: 8 }}>
        <Skeleton width="70%" height={14} />
      </div>
    </div>
  );
}

export function SkeletonStat() {
  return (
    <div className="stat">
      <Skeleton width={80} height={11} />
      <div style={{ marginTop: 8 }}>
        <Skeleton width={60} height={28} />
      </div>
    </div>
  );
}

export function SkeletonProviderCard() {
  return (
    <div className="provider-card" aria-hidden="true">
      <div className="head">
        <div>
          <Skeleton width={140} height={18} />
          <div style={{ marginTop: 8 }}>
            <Skeleton width={90} height={11} />
          </div>
        </div>
      </div>
      <div className="metrics">
        <div className="metric">
          <Skeleton width={50} height={10} />
          <div style={{ marginTop: 6 }}>
            <Skeleton width={60} height={18} />
          </div>
        </div>
        <div className="metric">
          <Skeleton width={50} height={10} />
          <div style={{ marginTop: 6 }}>
            <Skeleton width={60} height={18} />
          </div>
        </div>
      </div>
    </div>
  );
}
