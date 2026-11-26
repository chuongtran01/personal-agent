// What the chat says about the tools a reply used. Names in, short phrases out. The names come
// from the servers and the agent's own tools, and the page never sees what a tool was asked or
// returned, so a line like "Read an email" cannot carry an email's text.

interface Label {
  one: string;
  many: (count: number) => string;
}

const LABELS: Record<string, Label> = {
  email_search: { one: "Searched your email", many: (n) => `Searched your email ${n} times` },
  email_read: { one: "Read an email", many: (n) => `Read ${n} emails` },
  calendar_list_events: {
    one: "Looked at your calendar",
    many: (n) => `Looked at your calendar ${n} times`,
  },
  search_memory: { one: "Searched memory", many: (n) => `Searched memory ${n} times` },
};

// A tool this page has no words for yet is still said to have been used, and never by its raw name
const OTHER: Label = { one: "Used a tool", many: (n) => `Used tools ${n} times` };

/** One phrase for each kind of tool used, in the order each was first used, with how many times. */
export function toolPhrases(names: readonly string[] | null | undefined): string[] {
  if (!names?.length) return [];
  const counts = new Map<Label, number>();
  for (const name of names) {
    const label = Object.hasOwn(LABELS, name) ? LABELS[name] : OTHER;
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts].map(([label, count]) => (count === 1 ? label.one : label.many(count)));
}
