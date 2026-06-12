import Link from "next/link";
import { ArrowRight, Sparkles, GitGraph, ScrollText, ShieldCheck } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FirstRunCard } from "@/components/FirstRunCard";

const PILLARS = [
  {
    title: "Retrieval is auditable",
    body:
      "Every result carries its ranking breakdown, the channels it came from, " +
      "and the pipeline trace that produced it.",
    Icon: Sparkles,
    href: "/retrieve",
  },
  {
    title: "Graph is inspectable",
    body:
      "Bounded BFS over the project graph, with EXTERNAL nodes dimmed and " +
      "edges typed by the Phase-2 EDGE_RULES.",
    Icon: GitGraph,
    href: "/graph",
  },
  {
    title: "Audit chain is verifiable",
    body:
      "Hash-chained tamper-evident audit trail. Every governance + tool " +
      "call leaves a link you can verify.",
    Icon: ScrollText,
    href: "/audit",
  },
  {
    title: "System posture is honest",
    body:
      "Boot-stage tracker, safe-mode controller, feature-flag registry — " +
      "exposed as a single deterministic /status payload.",
    Icon: ShieldCheck,
    href: "/status",
  },
];

export default function HomePage() {
  return (
    <div className="max-w-5xl mx-auto space-y-12">
      <FirstRunCard />
      <header className="pt-8 pb-4 border-b border-border">
        <div className="text-xs font-mono muted uppercase tracking-wider mb-2">
          memory-cl · transparency layer
        </div>
        <h1 className="text-3xl font-semibold tracking-tight">
          What was retrieved, why it was retrieved,
          <br />
          and how the system decided it.
        </h1>
        <p className="mt-4 max-w-2xl text-sm text-muted leading-relaxed">
          Memory-CL ships a deterministic AI memory engine. This UI is the
          cognitive interface over it — every page exposes the underlying
          modules, scores, and pipeline trace so an engineer can reason about
          the answer, not just consume it.
        </p>
        <div className="mt-6 flex gap-3">
          <Link href="/retrieve">
            <Button variant="primary">
              Open Retrieve <ArrowRight size={14} />
            </Button>
          </Link>
          <Link href="/dashboard">
            <Button variant="secondary">Open Dashboard</Button>
          </Link>
        </div>
      </header>

      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wider muted mb-4">
          Surfaces
        </h2>
        <div className="grid sm:grid-cols-2 gap-4">
          {PILLARS.map(({ title, body, Icon, href }) => (
            <Link key={href} href={href as never} className="group block">
              <Card className="transition-colors group-hover:border-accent/50">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Icon size={14} className="text-accent" /> {title}
                  </CardTitle>
                  <ArrowRight size={14} className="text-muted group-hover:text-accent" />
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-muted leading-relaxed">{body}</p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </section>

      <footer className="text-xs muted font-mono">
        ⌘K to jump anywhere · g r → /retrieve · g d → /dashboard
      </footer>
    </div>
  );
}
