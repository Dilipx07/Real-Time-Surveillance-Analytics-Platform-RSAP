import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "RSAP Console",
  description: "Central administration console for RSAP"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
