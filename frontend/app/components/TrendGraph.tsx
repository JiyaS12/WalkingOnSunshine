"use client";

import {
  Area,
  AreaChart,
} from "@/components/charts";
import { Grid } from "@/components/charts/grid";
import { XAxis } from "@/components/charts/x-axis";
import { ChartTooltip } from "@/components/charts/tooltip/chart-tooltip";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

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
    <Card className="border-border">
      <CardHeader className="px-4 pb-0 pt-4">
        <CardTitle className="text-sm font-medium text-card-foreground">
          Session-over-session trend
        </CardTitle>
      </CardHeader>
      <CardContent className="px-4 pb-4 pt-2">
        <div className="mb-2 flex items-center gap-4 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-chart-1" />
            Asymmetry %
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-chart-2" />
            Fall Risk ×100
          </span>
        </div>
        <AreaChart
          data={data}
          xDataKey="date"
          xLabelKey="label"
          aspectRatio="2.5 / 1"
          className="w-full"
        >
          <Grid horizontal stroke="#334155" />
          <XAxis numTicks={Math.min(Math.max(sessions.length, 2), 6)} />
          <ChartTooltip
            rows={(point) => [
              {
                color: "var(--chart-1)",
                label: "Asymmetry %",
                value: `${(point.asymmetry as number).toFixed(1)}%`,
              },
              {
                color: "var(--chart-2)",
                label: "Fall Risk ×100",
                value: `${(point.fallRisk as number).toFixed(1)}`,
              },
            ]}
          />
          <Area
            dataKey="asymmetry"
            fill="var(--chart-1)"
            fillOpacity={0.25}
            gradientToOpacity={0}
            stroke="var(--chart-1)"
            showMarkers={sessions.length <= 12}
          />
          <Area
            dataKey="fallRisk"
            fill="var(--chart-2)"
            fillOpacity={0.25}
            gradientToOpacity={0}
            stroke="var(--chart-2)"
            showMarkers={sessions.length <= 12}
          />
        </AreaChart>
      </CardContent>
    </Card>
  );
}
