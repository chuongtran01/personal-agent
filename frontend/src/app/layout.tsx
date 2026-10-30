import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Script from "next/script";

import { ThemeFollower } from "@/components/theme-follower";

import "./globals.css";

// The theme in globals.css reads the sans font from --font-sans, so that is the variable to define
const geistSans = Geist({
  variable: "--font-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Personal Agent",
  description: "A personal assistant that remembers",
};

// Sets the dark class before the first paint so a dark system never flashes light. It is our own
// constant string, run by next/script before hydration (a bare <script> in a component makes React
// warn); lib/theme.ts keeps the class in step with the system afterwards
const THEME_SCRIPT = `document.documentElement.classList.toggle('dark', matchMedia('(prefers-color-scheme: dark)').matches)`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <ThemeFollower />
        {children}
        <Script id="theme" strategy="beforeInteractive">
          {THEME_SCRIPT}
        </Script>
      </body>
    </html>
  );
}
