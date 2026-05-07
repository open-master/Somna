import type { SessionAttachmentRef } from "@/lib/api/sessions";

export function UserMessage({
  text,
  attachments,
}: {
  text: string;
  attachments?: SessionAttachmentRef[];
}) {
  return (
    <div className="flex flex-col items-end gap-1.5 animate-fade-in">
      <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-primary text-primary-foreground px-4 py-2 text-sm whitespace-pre-wrap">
        {text}
      </div>
      {attachments?.length ? (
        <div className="flex max-w-[75%] flex-wrap justify-end gap-1">
          {attachments.map((a) => (
            <span
              key={a.s3_key}
              className="rounded-md border border-primary/30 bg-primary/10 px-2 py-0.5 text-[11px] text-primary"
            >
              {a.filename}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
