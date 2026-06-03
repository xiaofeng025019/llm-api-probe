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

interface Point {
  t: number;
  latency: number | null;
  success: number; // 0/1
}

export function ResultsChart({ results }: { results: ProbeResult[] }) {
  const data = useMemo<Point[]>(
    () =>
      [...results]
        .reverse()
        .map((r) => ({
          t: new Date(r.checked_at).getTime(),
          latency: r.success ? r.latency_ms ?? null : null,
          success: r.success ? 1 : 0,
        })),
    [results],
  );

  if (data.length === 0) {
    return <div className="muted">该时间窗内还没有探测数据。</div>;
  }

  return (
    <div style={{ width: "100%", height: 240 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v) => new Date(v).toLocaleString()}
            stroke="#9ca3af"
            fontSize={11}
          />
          <YAxis
            yAxisId="lat"
            stroke="#9ca3af"
            fontSize={11}
            label={{ value: "ms", angle: -90, position: "insideLeft", fill: "#9ca3af" }}
          />
          <YAxis
            yAxisId="ok"
            orientation="right"
            domain={[0, 1]}
            hide
          />
          <Tooltip
            labelFormatter={(v) => new Date(Number(v)).toLocaleString()}
            formatter={(value: number | string, name: string) => {
              if (name === "latency") return [`${value}ms`, "latency"];
              if (name === "success") return [value === 1 ? "ok" : "fail", "status"];
              return [value, name];
            }}
          />
          <Line
            yAxisId="lat"
            type="monotone"
            dataKey="latency"
            stroke="#2563eb"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            yAxisId="ok"
            type="stepAfter"
            dataKey="success"
            stroke="#16a34a"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
