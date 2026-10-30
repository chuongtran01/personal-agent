import { useSyncExternalStore } from "react";

const MOBILE_BREAKPOINT = 768;

function subscribe(onChange: () => void) {
  const query = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

const isNarrow = () => window.innerWidth < MOBILE_BREAKPOINT;

/** Whether the screen is narrow. The server has no screen, so it renders the wide layout. */
export function useIsMobile() {
  return useSyncExternalStore(subscribe, isNarrow, () => false);
}
