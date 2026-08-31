export default function SocWorkspaceLoading() {
  return (
    <div
      className="flex size-full min-h-0 flex-col"
      role="status"
      aria-label="正在打开 SOC 工作区"
    >
      <div className="border-b px-5 py-4 md:px-7">
        <div className="h-6 w-48 animate-pulse bg-zinc-200" />
        <div className="mt-2 h-4 w-96 max-w-full animate-pulse bg-zinc-100" />
      </div>
      <div className="flex h-11 items-center gap-5 border-b px-5">
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className="h-4 w-20 animate-pulse bg-zinc-100" />
        ))}
      </div>
      <div className="space-y-4 p-5 md:p-7">
        <div className="h-24 animate-pulse border bg-zinc-50" />
        <div className="h-20 animate-pulse border bg-zinc-50" />
        <div className="h-[30rem] animate-pulse border bg-zinc-50" />
      </div>
    </div>
  );
}
