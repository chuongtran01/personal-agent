import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { ThemeFollower } from "@/components/theme-follower";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
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
// constant string; lib/theme.ts keeps the class in step with the system afterwards
const THEME_SCRIPT = `document.documentElement.classList.toggle('dark', matchMedia('(prefers-color-scheme: dark)').matches)`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-full flex flex-col">
        <ThemeFollower />
        {children}
      </body>
    </html>
  );
}
