import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "parsnip — Grounded Research & Analysis",
  description: "AI research assistant with knowledge base, Joplin notes, and analysis tools",
  icons: { icon: "/favicon.ico" },
};

export const viewport = {
  themeColor: "#0b0f1a",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css"
          crossOrigin="anonymous"
        />
      </head>
      <body className="antialiased bg-navy-950 text-parsnip-text">
        {children}
      </body>
    </html>
  );
}