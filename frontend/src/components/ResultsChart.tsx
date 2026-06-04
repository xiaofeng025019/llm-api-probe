import { useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ProbeResult } from "../api/types";
import { parseApiDate } from "../lib/format";
import { useTheme } from "../hooks/useTheme";

interface Point {
  t: number;
  latency: number | null;
  success: number; // 0/1
}

export function ResultsChart({ results }: { results: ProbeResult[] }) {
  const [theme] = useTheme();
  const isDark = theme === "dark";

  // Theme-aware colors
  const gridColor = isDark ? "#2d3344" : "#e5e7eb";
  const axisColor = isDark ? "#7c8294" : "#9ca3af";
  const latencyColor = "#5b5bd6"; // Use brand primary
  const successColor = isDark ? "#4ade80" : "#16a34a";

  const data = useMemo<Point[]>(
    () =>
      [...results]
        .reverse()
        .map((r) => ({
          t: parseApiDate(r.checked_at).getTime(),
          latency: r.success ? r.latency_ms ?? null : null,
          success: r.success ? 1 : 0,
        })),
    [results],
  );

  if (data.length === 0) {
    return <div className="muted">No probe data in this time window yet.</div>;
  }

  return (
    <div style={{ width: "100%", height: 240 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v) => new Date(v).toLocaleString()}
            stroke={axisColor}
            fontSize={11}
          />
          <YAxis
            yAxisId="lat"
            stroke={axisColor}
            fontSize={11}
            label={{ value: "ms", angle: -90, position: "insideLeft", fill: axisColor }}
          />
          <YAxis
            yAxisId="ok"
            orientation="right"
            domain={[0, 1]}
            hide
          />
          <Tooltip
            labelFormatter={(v) => new Date(Number(v)).toLocaleString()}
            contentStyle={{
              background: isDark ? "#131720" : "#ffffff",
              border: `1px solid ${isDark ? "#2d3344" : "#e5e7eb"}`,
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value: number | string, name: string) => {
              if (name === "latency") return [`${value}ms`, "Latency"];
              if (name === "success") return [value === 1 ? "ok" : "fail", "Status"];
              return [value, name];
            }}
          />
          <Line
            yAxisId="lat"
            type="monotone"
            dataKey="latency"
            stroke={latencyColor}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            yAxisId="ok"
            type="stepAfter"
            dataKey="success"
            stroke={successColor}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
