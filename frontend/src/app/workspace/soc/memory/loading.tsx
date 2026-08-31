export default function SocMemoryCenterLoading() {
  return (
    <div
      className="flex size-full min-h-0 flex-col"
      role="status"
      aria-label="正在打开经验中心"
    >
      <div className="border-b px-5 py-4 md:px-7">
        <div className="h-6 w-40 animate-pulse bg-zinc-200" />
        <div className="mt-2 h-4 w-96 max-w-full animate-pulse bg-zinc-100" />
      </div>
      <div className="space-y-4 p-5 md:p-7">
        <div className="h-24 animate-pulse border bg-zinc-50" />
        <div className="h-24 animate-pulse border bg-zinc-50" />
        <div className="h-[34rem] animate-pulse border bg-zinc-50" />
      </div>
    </div>
  );
}
