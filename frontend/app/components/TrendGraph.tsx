"use client";

import {
  Area,
  AreaChart,
} from "@/components/charts";
import { Grid } from "@/components/charts/grid";
import { XAxis } from "@/components/charts/x-axis";
import { ChartTooltip } from "@/components/charts/tooltip/chart-tooltip";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useState } from "react";

export interface TrendSession {
  label: string;
  asymmetry_pct: number;
  fall_risk_score: number;
  stride_length_m: number;
}

interface Props {
  sessions: TrendSession[];
}

const SERIES = [
  { key: "asymmetry", label: "Asymmetry %", color: "var(--chart-1)", active: "bg-pastel-purple text-foreground" },
  { key: "fallRisk", label: "Fall Risk ×100", color: "var(--chart-2)", active: "bg-pastel-sagedeep text-foreground" },
] as const;

type SeriesKey = (typeof SERIES)[number]["key"];

export default function TrendGraph({ sessions }: Props) {
  const [hidden, setHidden] = useState<Set<SeriesKey>>(new Set());
  const toggle = (key: SeriesKey) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else if (next.size < SERIES.length - 1) next.add(key);
      return next;
    });
  };
  // the chart x-axis is time-based; sessions carry only labels, so spread
  // them one day apart from a fixed epoch and keep the label for tooltips
  const epoch = new Date(2026, 0, 1).getTime();
  const data = sessions.map((s, i) => ({
    date: new Date(epoch + i * 86400000),
    label: s.label,
    asymmetry: s.asymmetry_pct,
    fallRisk: Math.round(s.fall_risk_score * 1000) / 10,
  }));

  return (
    <Card>
      <CardHeader className="px-4 pb-0 pt-4">
        <CardTitle className="text-sm font-medium text-card-foreground">
          Session-over-session trend
        </CardTitle>
      </CardHeader>
      <CardContent className="px-4 pb-4 pt-2">
        <div className="mb-2 flex items-center gap-2">
          {SERIES.map((s) => (
            <button
              key={s.key}
              type="button"
              aria-pressed={!hidden.has(s.key)}
              onClick={() => toggle(s.key)}
              className={`rounded-full px-4 py-1.5 text-xs transition-colors ${
                hidden.has(s.key)
                  ? "bg-muted text-muted-foreground shadow-pillow-inset"
                  : `${s.active} shadow-pillow-sm`
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <AreaChart
          data={data}
          xDataKey="date"
          xLabelKey="label"
          aspectRatio="2.5 / 1"
          margin={{ top: 8, right: 12, bottom: 28, left: 12 }}
          className="w-full"
        >
          <Grid horizontal stroke="#E8E3D9" />
          <XAxis numTicks={Math.min(Math.max(sessions.length, 2), 6)} />
          <ChartTooltip
            rows={(point) =>
              SERIES.filter((s) => !hidden.has(s.key)).map((s) => ({
                color: s.color,
                label: s.label,
                value: `${(point[s.key] as number).toFixed(1)}${
                  s.key === "asymmetry" ? "%" : ""
                }`,
              }))
            }
          />
          {!hidden.has("asymmetry") && (
            <Area
              dataKey="asymmetry"
              fill="var(--chart-1)"
              fillOpacity={0.25}
              gradientToOpacity={0}
              stroke="var(--chart-1)"
              showMarkers={sessions.length <= 12}
            />
          )}
          {!hidden.has("fallRisk") && (
            <Area
              dataKey="fallRisk"
              fill="var(--chart-2)"
              fillOpacity={0.25}
              gradientToOpacity={0}
              stroke="var(--chart-2)"
              showMarkers={sessions.length <= 12}
            />
          )}
        </AreaChart>
      </CardContent>
    </Card>
  );
}
