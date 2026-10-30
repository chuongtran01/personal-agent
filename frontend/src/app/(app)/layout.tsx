import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/auth/require-auth";

// Everything in this group is for the signed-in owner: login lives outside it
export default function SignedInLayout({ children }: { children: ReactNode }) {
  return (
    <RequireAuth>
      <AppShell>{children}</AppShell>
    </RequireAuth>
  );
}
