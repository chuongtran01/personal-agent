"use client";

import { useEffect } from "react";

import { followSystemTheme } from "@/lib/theme";

/** Keeps the dark class in step with the system setting while the app is open. */
export function ThemeFollower() {
  useEffect(() => followSystemTheme(), []);
  return null;
}
