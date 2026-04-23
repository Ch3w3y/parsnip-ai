import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        navy: {
          950: "#0b0f1a",
          900: "#111827",
          800: "#1a2332",
          700: "#243044",
          600: "#2d3b4f",
        },
        parsnip: {
          teal: "#23c0a8",
          blue: "#2f6cff",
          text: "#f5f7ff",
          muted: "#9fb3c8",
          error: "#ef4444",
          warning: "#f59e0b",
        },
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "Cascadia Code", "monospace"],
      },
      backgroundImage: {
        "brand-gradient": "linear-gradient(135deg, #23c0a8, #2f6cff)",
      },
    },
  },
  plugins: [],
};

export default config;