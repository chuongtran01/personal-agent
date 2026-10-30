import { ChevronDown } from "lucide-react";

import type { RecalledMemory } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

// Labelled "recalled", not "used": recall puts a memory in the prompt and cannot tell which of them
// the model relied on (docs/PHASE4.md).

function Label({ memory }: { memory: RecalledMemory }) {
  if (memory.status === "deleted") return null;
  if (memory.past) return <Badge variant="secondary">was true</Badge>;
  if (memory.status === "superseded") return <Badge variant="secondary">since replaced</Badge>;
  return null;
}

export function RecalledMemories({ memories }: { memories: RecalledMemory[] | null }) {
  if (memories === null) return null; // the memory event has not arrived yet
  if (memories.length === 0) {
    return <p className="text-xs text-muted-foreground">No memory matched</p>;
  }
  return (
    <Collapsible className="text-xs">
      <CollapsibleTrigger className="group flex items-center gap-1 text-muted-foreground hover:text-foreground">
        Recalled {memories.length} {memories.length === 1 ? "memory" : "memories"}
        <ChevronDown className="size-3 transition-transform group-data-[state=open]:rotate-180" aria-hidden />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="mt-2 flex flex-col gap-2">
          {memories.map((memory) => (
            <li key={memory.id} className="flex items-baseline gap-2">
              <Badge variant="outline" className="shrink-0">
                {memory.kind}
              </Badge>
              <span className="min-w-0 flex-1">
                {memory.content === null ? (
                  <span className="italic text-muted-foreground">
                    A memory that has since been deleted
                  </span>
                ) : (
                  memory.content
                )}{" "}
                <Label memory={memory} />
              </span>
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}
