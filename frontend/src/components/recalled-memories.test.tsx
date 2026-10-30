import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import type { RecalledMemory } from "@/api/types";

import { RecalledMemories } from "./recalled-memories";

const memory = (overrides: Partial<RecalledMemory> = {}): RecalledMemory => ({
  id: 1,
  kind: "fact",
  content: "The owner lives in Porto.",
  past: false,
  status: "active",
  ...overrides,
});

describe("RecalledMemories", () => {
  test("shows nothing before the memory event has arrived", () => {
    const { container } = render(<RecalledMemories memories={null} />);

    expect(container).toBeEmptyDOMElement();
  });

  test("says when no memory matched, which is what the agent was told too", () => {
    render(<RecalledMemories memories={[]} />);

    expect(screen.getByText("No memory matched")).toBeInTheDocument();
  });

  test("counts the memories, in the singular and the plural", () => {
    const { rerender } = render(<RecalledMemories memories={[memory()]} />);
    expect(screen.getByRole("button", { name: /Recalled 1 memory/ })).toBeInTheDocument();

    rerender(<RecalledMemories memories={[memory(), memory({ id: 2 })]} />);
    expect(screen.getByRole("button", { name: /Recalled 2 memories/ })).toBeInTheDocument();
  });

  test("is closed until opened, then lists the kind and the text", async () => {
    render(<RecalledMemories memories={[memory({ kind: "preference", content: "Still water only." })]} />);
    const trigger = screen.getByRole("button", { name: /Recalled 1 memory/ });

    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Still water only.")).not.toBeInTheDocument();

    await userEvent.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Still water only.")).toBeInTheDocument();
    expect(screen.getByText("preference")).toBeInTheDocument();
  });

  test("a memory that was true once says so", async () => {
    render(<RecalledMemories memories={[memory({ past: true, content: "Lived in Lisbon." })]} />);
    await userEvent.click(screen.getByRole("button"));

    expect(screen.getByText("was true")).toBeInTheDocument();
    expect(screen.queryByText("since replaced")).not.toBeInTheDocument();
  });

  test("a current memory that has been replaced since says so", async () => {
    render(<RecalledMemories memories={[memory({ status: "superseded" })]} />);
    await userEvent.click(screen.getByRole("button"));

    expect(screen.getByText("since replaced")).toBeInTheDocument();
  });

  test("a current, active memory carries no label", async () => {
    render(<RecalledMemories memories={[memory()]} />);
    await userEvent.click(screen.getByRole("button"));

    expect(screen.queryByText("was true")).not.toBeInTheDocument();
    expect(screen.queryByText("since replaced")).not.toBeInTheDocument();
  });

  test("a memory deleted since shows no text, only that it was deleted", async () => {
    render(<RecalledMemories memories={[memory({ content: null, status: "deleted", past: true })]} />);
    await userEvent.click(screen.getByRole("button"));

    expect(screen.getByText("A memory that has since been deleted")).toBeInTheDocument();
    expect(screen.queryByText("was true")).not.toBeInTheDocument();
  });
});
