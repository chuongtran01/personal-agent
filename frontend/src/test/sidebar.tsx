import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { SidebarProvider } from "@/components/ui/sidebar";

/** Render something that belongs inside the sidebar, which needs its provider around it. */
export function renderInSidebar(ui: ReactElement) {
  return render(<SidebarProvider>{ui}</SidebarProvider>);
}

/** Pretend the window is this wide, for the layout that changes on a narrow screen. */
export function setWindowWidth(width: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: width });
}
