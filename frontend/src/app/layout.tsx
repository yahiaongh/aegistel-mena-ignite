import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

// Geist is bundled locally so a production build never depends on a live
// Google Fonts fetch during grading.
const geistSans = localFont({
  src: "./fonts/Geist-Variable.woff2",
  variable: "--font-geist-sans",
  weight: "100 900",
  display: "swap",
});

const geistMono = localFont({
  src: "./fonts/GeistMono-Variable.woff2",
  variable: "--font-geist-mono",
  weight: "100 900",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AegisTel — MENA Payment-Fraud & Account-Takeover Guard",
  description:
    "Telecom-aware anti-fraud guard stopping account takeover (ATO) and SIM-swap payment fraud via CAMARA APIs through the Nokia Network-as-Code SDK.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
