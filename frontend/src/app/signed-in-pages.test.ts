import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { expect, test } from "vitest";

// Every page in the signed-in group sits under RequireAuth, which renders nothing on the server, so
// Next's instant-navigation check can never see the page and raises a dev error ("Could not validate
// that a segment in your UI has instant navigation"). Each page opts out; this keeps a new page
// (the memory list, say) from forgetting to.

const GROUP = path.join(process.cwd(), "src/app/(app)");

function pagesUnder(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const full = path.join(directory, name);
    if (statSync(full).isDirectory()) return pagesUnder(full);
    return /^page\.tsx?$/.test(name) ? [full] : [];
  });
}

const pages = pagesUnder(GROUP);

test("the signed-in group has pages to check", () => {
  expect(pages.length).toBeGreaterThanOrEqual(2);
});

test.each(pages.map((file) => [path.relative(GROUP, file), file]))(
  "%s opts out of instant-navigation validation",
  (_, file) => {
    expect(readFileSync(file, "utf8")).toMatch(/^export const instant = false;?$/m);
  },
);
