"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface TrendSession {
  label: string;
  asymmetry_pct: number;
  fall_risk_score: number;
  stride_length_m: number;
}

interface Props {
  sessions: TrendSession[];
}

export default function TrendGraph({ sessions }: Props) {
  const data = sessions.map((s) => ({
    label: s.label,
    "Asymmetry %": s.asymmetry_pct,
    "Fall Risk ×100": Math.round(s.fall_risk_score * 1000) / 10,
  }));

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900 p-4">
      <h2 className="mb-1 text-sm font-medium text-slate-200">
        Session-over-session trend
      </h2>
      <p className="mb-2 text-xs text-slate-400">
        Asymmetry % vs Fall Risk ×100 across sessions
      </p>
      <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="#334155" strokeDasharray="3 3" />
          <XAxis dataKey="label" stroke="#94a3b8" fontSize={12} />
          <YAxis stroke="#94a3b8" fontSize={12} />
          <Tooltip
            contentStyle={{
              backgroundColor: "#0f172a",
              border: "1px solid #334155",
              borderRadius: 8,
              color: "#e2e8f0",
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line
            type="monotone"
            dataKey="Asymmetry %"
            stroke="#f472b6"
            strokeWidth={2}
            dot={{ r: 4 }}
          />
          <Line
            type="monotone"
            dataKey="Fall Risk ×100"
            stroke="#60a5fa"
            strokeWidth={2}
            dot={{ r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>
      </div>
    </div>
  );
}
