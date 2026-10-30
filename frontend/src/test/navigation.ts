import { vi } from "vitest";

/**
 * The pieces of next/navigation the app uses, shared by the tests that mock it. A test file mocks
 * the module with `vi.mock("next/navigation", async () => (await import("@/test/navigation")).navigationMock())`
 * and reads the calls from `nav`.
 */
export const nav = {
  router: { replace: vi.fn(), push: vi.fn() },
  pathname: "/",
};

export const navigationMock = () => ({
  useRouter: () => nav.router,
  usePathname: () => nav.pathname,
});
