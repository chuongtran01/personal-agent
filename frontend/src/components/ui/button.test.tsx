import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { Button } from "./button";

// A smoke test of the tooling: a shadcn component renders and takes a click under jsdom
test("a shadcn button takes a click", async () => {
  const onClick = vi.fn();
  render(<Button onClick={onClick}>Send</Button>);

  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(onClick).toHaveBeenCalledOnce();
});
