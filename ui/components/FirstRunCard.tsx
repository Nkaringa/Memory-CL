"use client";

import Link from "next/link";
import { Boxes, ArrowRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useRepos } from "@/components/RepoSelect";

/** Shown when the repos endpoint succeeds but returns an empty list.
 *  Renders null during loading, on error, or when repos already exist —
 *  so it never blocks a page. */
export function FirstRunCard() {
  const { data, isLoading, isError } = useRepos();

  if (isLoading || isError) return null;
  if (!data || data.repos.length > 0) return null;

  return (
    <Card className="mb-6 border-accent/40 bg-accent/[0.04]">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Boxes size={16} className="text-accent" /> No repositories ingested yet
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted leading-relaxed">
          Memory-CL answers questions from your code. Ingest a repository to get started.
        </p>
        <Link href="/ingest">
          <Button variant="primary" size="sm">
            Go to Ingest <ArrowRight size={14} />
          </Button>
        </Link>
      </CardContent>
    </Card>
  );
}
