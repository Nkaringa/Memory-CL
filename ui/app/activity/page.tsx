import { PageHeader } from "@/components/shell/primitives";

export default function Page() {
  return (
    <div className="mx-auto max-w-[1080px]">
      <PageHeader title="Live Activity" subtitle="Building this page next." />
      <div className="rounded-xl border border-border bg-bg px-5 py-10 text-center text-[13px] text-muted">
        Live Activity — coming in this build.
      </div>
    </div>
  );
}
