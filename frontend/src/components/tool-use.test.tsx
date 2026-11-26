import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { ToolUse } from "./tool-use";

describe("the line about the tools a reply used", () => {
  test("joins what was used with a dot", () => {
    render(<ToolUse names={["email_search", "email_read", "email_read"]} />);

    expect(screen.getByText("Searched your email · Read 2 emails")).toBeInTheDocument();
  });

  test.each([[null], [undefined], [[]]])("shows nothing for %j", (names) => {
    const { container } = render(<ToolUse names={names as string[] | null | undefined} />);

    expect(container).toBeEmptyDOMElement();
  });

  test("never puts a raw tool name on the page", () => {
    const { container } = render(<ToolUse names={["email_read", "calendar_create_event"]} />);

    expect(container.textContent).not.toMatch(/email_read|calendar_create_event|_/);
  });
});
