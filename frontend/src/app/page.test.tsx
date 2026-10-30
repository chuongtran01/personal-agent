import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { Button } from "@/components/ui/button";

import Home from "./page";

test("the home page renders a shadcn button", () => {
  render(<Home />);

  expect(screen.getByRole("heading", { name: "Personal Agent" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Hello" })).toBeInTheDocument();
});

test("a shadcn button takes a click", async () => {
  const onClick = vi.fn();
  render(<Button onClick={onClick}>Send</Button>);

  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(onClick).toHaveBeenCalledOnce();
});
