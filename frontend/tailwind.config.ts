import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        pastel: {
          cream: "#F9F7F2",
          sand: "#EFEBE3",
          blue: "#B9D4E3",
          bluedeep: "#8FB4D2",
          green: "#B5CDB6",
          sage: "#B2C2B2",
          sagedeep: "#A3B5A3",
          lavender: "#C9C4EA",
          purple: "#B3A8DC",
          peach: "#F2D5C4",
        },
        background: "var(--background)",
        foreground: "var(--foreground)",
        card: "var(--card)",
        "card-foreground": "var(--card-foreground)",
        popover: "var(--popover)",
        "popover-foreground": "var(--popover-foreground)",
        primary: "var(--primary)",
        "primary-foreground": "var(--primary-foreground)",
        secondary: "var(--secondary)",
        "secondary-foreground": "var(--secondary-foreground)",
        muted: "var(--muted)",
        "muted-foreground": "var(--muted-foreground)",
        accent: "var(--accent)",
        "accent-foreground": "var(--accent-foreground)",
        destructive: "var(--destructive)",
        "destructive-foreground": "var(--destructive-foreground)",
        border: "var(--border)",
        input: "var(--input)",
        ring: "var(--ring)",
        "chart-1": "var(--chart-1)",
        "chart-2": "var(--chart-2)",
        "chart-3": "var(--chart-3)",
        "chart-4": "var(--chart-4)",
        "chart-5": "var(--chart-5)",
      },
      boxShadow: {
        pillow:
          "12px 12px 28px rgba(120,100,80,0.18), 4px 4px 10px rgba(44,62,80,0.08), -8px -8px 20px rgba(255,255,255,0.95), inset 1px 1px 0 rgba(255,255,255,0.9)",
        "pillow-sm":
          "6px 6px 14px rgba(120,100,80,0.16), 2px 2px 5px rgba(44,62,80,0.07), -5px -5px 12px rgba(255,255,255,0.95), inset 1px 1px 0 rgba(255,255,255,0.85)",
        "pillow-lg":
          "18px 18px 40px rgba(120,100,80,0.20), 6px 6px 14px rgba(44,62,80,0.08), -12px -12px 28px rgba(255,255,255,0.95), inset 1px 1px 0 rgba(255,255,255,0.9)",
        "pillow-inset":
          "inset 5px 5px 12px rgba(120,100,80,0.12), inset -5px -5px 12px rgba(255,255,255,0.9)",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-geist-mono)", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
