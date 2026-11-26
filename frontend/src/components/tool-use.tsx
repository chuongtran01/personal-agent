import { toolPhrases } from "@/lib/tool-labels";

/** A quiet line under a reply saying which tools it used: "Searched your email · Read 2 emails". */
export function ToolUse({ names }: { names: readonly string[] | null | undefined }) {
  const phrases = toolPhrases(names);
  if (phrases.length === 0) return null;
  return <p className="text-xs text-muted-foreground">{phrases.join(" · ")}</p>;
}
