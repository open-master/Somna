"use client";

import { Copy, FileText, Folder, Upload, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  deleteSkill,
  getSkill,
  listSkills,
  setSkillEnabled,
  setSkillVisibility,
  uploadSkill,
  type SkillDetail,
  type SkillRow,
} from "@/lib/api/skills";
import { cn } from "@/lib/utils/cn";

const VISIBILITY_LABEL: Record<string, string> = {
  private: "私有",
  shared: "共享",
  official: "官方",
};

function SkillBadge({ skill }: { skill: SkillRow }) {
  if (skill.visibility === "official") return <Badge variant="success">官方</Badge>;
  if (skill.visibility === "shared") return <Badge variant="secondary">共享</Badge>;
  return <Badge variant="outline">私有</Badge>;
}

function SkillCard({
  skill,
  mine,
  isAdmin,
  onReload,
  onPreview,
}: {
  skill: SkillRow;
  mine: boolean;
  isAdmin: boolean;
  onReload: () => void;
  onPreview: (skill: SkillRow) => void;
}) {
  const [busy, setBusy] = useState(false);
  const canShare = mine && skill.visibility !== "official";

  async function toggleEnabled() {
    setBusy(true);
    try {
      await setSkillEnabled(skill.id, !skill.enabled);
      onReload();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function share(next: "private" | "shared" | "official") {
    setBusy(true);
    try {
      await setSkillVisibility(skill.id, next);
      onReload();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm(`确定删除 Skill ${skill.name}？`)) return;
    setBusy(true);
    try {
      await deleteSkill(skill.id);
      onReload();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="w-full cursor-pointer rounded-xl border bg-background p-3 text-left shadow-sm transition hover:border-primary/40 hover:shadow-md" onClick={() => onPreview(skill)}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate font-mono text-sm font-semibold">{skill.name}</p>
            <SkillBadge skill={skill} />
            {skill.status === "draft" ? <Badge variant="warning">草稿</Badge> : null}
          </div>
          <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{skill.description}</p>
          <p className="mt-2 text-[11px] text-muted-foreground">
            {skill.owner_email ? `作者：${skill.owner_email}` : "作者未知"} · v{skill.version}
          </p>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={(e) => {
            e.stopPropagation();
            void toggleEnabled();
          }}
          className={cn(
            "h-6 w-11 shrink-0 rounded-full border p-0.5 transition",
            skill.enabled ? "border-primary bg-primary" : "border-border bg-muted",
          )}
          aria-label={skill.enabled ? "停用技能" : "启用技能"}
        >
          <span
            className={cn(
              "block size-4 rounded-full bg-background shadow transition",
              skill.enabled ? "translate-x-5" : "translate-x-0",
            )}
          />
        </button>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={(e) => {
            e.stopPropagation();
            onPreview(skill);
          }}
        >
          预览
        </Button>
        {canShare ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              void share(skill.visibility === "shared" ? "private" : "shared");
            }}
          >
            {skill.visibility === "shared" ? "取消共享" : "共享到市场"}
          </Button>
        ) : null}
        {isAdmin && mine && skill.visibility !== "official" ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              void share("official");
            }}
          >
            设为官方
          </Button>
        ) : null}
        {mine ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              void remove();
            }}
          >
            删除
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function splitSkillMarkdown(skillMd: string): { yaml: string; body: string } {
  const match = skillMd.match(/^---\s*\n([\s\S]*?)\n---\s*(?:\n|$)([\s\S]*)$/);
  if (!match) return { yaml: "", body: skillMd };
  return { yaml: match[1] ?? "", body: match[2] ?? "" };
}

function groupedFiles(files: Record<string, string>): { dirs: Record<string, string[]>; roots: string[] } {
  const paths = Object.keys(files).sort((a, b) => {
    if (a === "SKILL.md") return -1;
    if (b === "SKILL.md") return 1;
    return a.localeCompare(b);
  });
  const roots: string[] = [];
  const dirs: Record<string, string[]> = {};
  for (const path of paths) {
    const parts = path.split("/");
    if (parts.length === 1) {
      roots.push(path);
      continue;
    }
    const dir = parts.slice(0, -1).join("/");
    dirs[dir] = [...(dirs[dir] ?? []), path];
  }
  return { dirs, roots };
}

function normalizeSkillFiles(raw: unknown): Record<string, string> {
  let value = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value) as unknown;
    } catch {
      return {};
    }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    if (typeof v === "string") out[k] = v;
    else if (v != null) out[k] = typeof v === "object" ? JSON.stringify(v, null, 2) : String(v);
  }
  return out;
}

function asMarkdownString(value: unknown, fallback: string): string {
  if (typeof value === "string") return value;
  if (value == null) return fallback;
  return String(value);
}

function SkillPreviewDialog({
  skill,
  onClose,
}: {
  skill: SkillDetail | null;
  onClose: () => void;
}) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const [selectedPath, setSelectedPath] = useState("SKILL.md");
  const files = useMemo(() => normalizeSkillFiles(skill?.files), [skill?.files]);
  const skillMdStr = asMarkdownString(skill?.skill_md, "");
  const filePaths = Object.keys(files);
  const safeSelected =
    selectedPath in files
      ? selectedPath
      : "SKILL.md" in files
        ? "SKILL.md"
        : filePaths[0] ?? "SKILL.md";
  const selectedContent = asMarkdownString(files[safeSelected] ?? skillMdStr, skillMdStr);
  const isSkillMd = safeSelected === "SKILL.md";
  const parsed = isSkillMd ? splitSkillMarkdown(selectedContent) : { yaml: "", body: selectedContent };
  const tree = groupedFiles(files);

  useEffect(() => {
    if (!skill) return;
    const keys = Object.keys(files);
    if (keys.includes("SKILL.md")) setSelectedPath("SKILL.md");
    else if (keys.length > 0) setSelectedPath([...keys].sort((a, b) => a.localeCompare(b))[0]!);
  }, [skill?.id, skill, files]);

  useEffect(() => {
    if (!skill) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [skill, onClose]);

  async function copyCurrent() {
    try {
      await navigator.clipboard.writeText(selectedContent);
    } catch {
      window.alert("复制失败");
    }
  }

  if (!mounted || !skill) return null;

  const visLabel = VISIBILITY_LABEL[skill.visibility] ?? skill.visibility ?? "";

  const panel = (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-3" role="presentation">
      <button
        type="button"
        className="absolute inset-0 bg-black/45 backdrop-blur-[2px]"
        aria-label="关闭预览"
        onClick={onClose}
      />
      <div
        className="relative flex h-[min(760px,calc(100vh-1.5rem))] w-[min(1120px,calc(100vw-1.5rem))] overflow-hidden rounded-2xl border bg-background shadow-2xl outline-none"
        role="dialog"
        aria-modal="true"
        aria-labelledby="skill-preview-title"
      >
              <aside className="hidden w-56 shrink-0 border-r bg-muted/30 sm:block">
                <div className="border-b px-3 py-3">
                  <div className="flex items-center gap-2">
                    <FileText className="size-4 text-muted-foreground" />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">{skill.name}</p>
                      <p className="text-xs text-muted-foreground">技能</p>
                    </div>
                  </div>
                </div>
                <div className="space-y-1 p-2 text-sm">
                  {tree.roots.map((path) => (
                    <button
                      key={path}
                      type="button"
                      onClick={() => setSelectedPath(path)}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left",
                        safeSelected === path ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:bg-muted",
                      )}
                    >
                      <FileText className="size-3.5 shrink-0" />
                      <span className="truncate">{path}</span>
                    </button>
                  ))}
                  {Object.entries(tree.dirs).map(([dir, children]) => (
                    <div key={dir} className="pt-1">
                      <div className="flex items-center gap-2 px-2 py-1 text-xs font-medium text-muted-foreground">
                        <Folder className="size-3.5" />
                        <span className="truncate">{dir}</span>
                      </div>
                      <div className="space-y-1 pl-4">
                        {children.map((path) => (
                          <button
                            key={path}
                            type="button"
                            onClick={() => setSelectedPath(path)}
                            className={cn(
                              "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs",
                              safeSelected === path
                                ? "bg-background font-medium shadow-sm"
                                : "text-muted-foreground hover:bg-muted",
                            )}
                          >
                            <FileText className="size-3.5 shrink-0" />
                            <span className="truncate">{path.split("/").pop()}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </aside>

              <div className="flex min-w-0 flex-1 flex-col">
                <header className="flex items-start justify-between gap-3 border-b px-5 py-3">
                  <div className="min-w-0">
                    <h2 id="skill-preview-title" className="truncate text-base font-semibold">
                      {safeSelected}
                    </h2>
                    <p className="mt-1 truncate text-xs text-muted-foreground">
                      {visLabel} · {filePaths.length} 个文件 · {skill.description}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Button type="button" size="sm" variant="outline" onClick={() => void copyCurrent()}>
                      <Copy className="mr-1.5 size-3.5" />
                      复制
                    </Button>
                    <Button type="button" size="icon" variant="ghost" aria-label="关闭" onClick={onClose}>
                      <X className="size-4" />
                    </Button>
                  </div>
                </header>

                <main className="min-h-0 flex-1 overflow-auto px-5 py-5">
                  {isSkillMd ? (
                    <article className="mx-auto max-w-3xl">
                      {parsed.yaml ? (
                        <div className="mb-8 overflow-hidden rounded-xl border bg-muted/30">
                          <div className="flex items-center justify-between border-b px-4 py-2">
                            <span className="text-xs font-medium text-muted-foreground">YAML</span>
                            <Button
                              type="button"
                              size="icon"
                              variant="ghost"
                              className="size-7"
                              onClick={() => void navigator.clipboard.writeText(parsed.yaml)}
                              aria-label="复制 YAML"
                            >
                              <Copy className="size-3.5" />
                            </Button>
                          </div>
                          <pre className="overflow-auto p-4 font-mono text-xs leading-relaxed text-emerald-700 dark:text-emerald-300">
                            {parsed.yaml}
                          </pre>
                        </div>
                      ) : null}
                      <div className="prose prose-sm max-w-none dark:prose-invert prose-headings:font-semibold prose-p:text-foreground/85 prose-li:text-foreground/85">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{parsed.body}</ReactMarkdown>
                      </div>
                    </article>
                  ) : safeSelected.endsWith(".md") ? (
                    <article className="prose prose-sm max-w-none dark:prose-invert">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedContent}</ReactMarkdown>
                    </article>
                  ) : (
                    <pre className="min-h-full overflow-auto rounded-xl bg-muted p-4 font-mono text-xs leading-relaxed">
                      {selectedContent}
                    </pre>
                  )}
                </main>
              </div>
      </div>
    </div>
  );

  return createPortal(panel, document.body);
}

export function SettingsSkillManagement({ isAdmin, currentUserId }: { isAdmin: boolean; currentUserId: string | null }) {
  const [tab, setTab] = useState("mine");
  const [mine, setMine] = useState<SkillRow[]>([]);
  const [market, setMarket] = useState<SkillRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [visibility, setVisibility] = useState<"private" | "shared">("private");
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<SkillDetail | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [myRows, marketRows] = await Promise.all([listSkills("my"), listSkills("market")]);
      setMine(myRows);
      setMarket(marketRows);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "加载技能失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const visibleMine = useMemo(() => filterSkills(mine, query), [mine, query]);
  const visibleMarket = useMemo(() => filterSkills(market, query), [market, query]);

  async function openPreview(row: SkillRow) {
    try {
      setPreview(await getSkill(row.id));
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "读取 Skill 失败");
    }
  }

  async function submitUpload(nextFile: File | null) {
    if (!nextFile) return;
    setUploading(true);
    try {
      await uploadSkill(nextFile, visibility);
      await reload();
      setTab("mine");
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "上传 Skill 失败");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-4">
      <input
        ref={fileInputRef}
        type="file"
        accept=".skill,.zip,.md"
        className="sr-only"
        onChange={(e) => {
          const picked = e.target.files?.[0] ?? null;
          if (picked) void submitUpload(picked);
          e.currentTarget.value = "";
        }}
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索技能"
            className="h-9 max-w-xs rounded-lg"
          />
          <p className="text-xs text-muted-foreground">
            管理 Claude 标准 Skill；管理员创建或上传的 Skill 会自动标记为官方。
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!isAdmin ? (
            <select
              value={visibility}
              onChange={(e) => setVisibility(e.target.value as "private" | "shared")}
              className="h-9 rounded-md border border-input bg-background px-2 text-sm"
            >
              <option value="private">私有</option>
              <option value="shared">共享</option>
            </select>
          ) : (
            <Badge variant="success">官方</Badge>
          )}
          <Button
            type="button"
            size="sm"
            disabled={uploading}
            onClick={() => fileInputRef.current?.click()}
          >
            <Upload className="mr-2 size-4" />
            {uploading ? "上传中…" : "上传技能"}
          </Button>
        </div>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="max-w-md">
          <TabsTrigger value="mine">我的技能</TabsTrigger>
          <TabsTrigger value="market">技能市场</TabsTrigger>
        </TabsList>

        <TabsContent value="mine" className="space-y-3">
          {loading ? <p className="text-sm text-muted-foreground">加载中…</p> : null}
          {!loading && visibleMine.length === 0 ? <p className="text-sm text-muted-foreground">暂无技能。</p> : null}
          <div className="grid gap-3 lg:grid-cols-2">
            {visibleMine.map((skill) => (
              <SkillCard
                key={skill.id}
                skill={skill}
                mine={skill.owner_user_id === currentUserId || isAdmin}
                isAdmin={isAdmin}
                onReload={() => void reload()}
                onPreview={(row) => void openPreview(row)}
              />
            ))}
          </div>
        </TabsContent>

        <TabsContent value="market" className="space-y-3">
          {!loading && visibleMarket.length === 0 ? <p className="text-sm text-muted-foreground">市场暂无可见技能。</p> : null}
          <div className="grid gap-3 lg:grid-cols-2">
            {visibleMarket.map((skill) => (
              <SkillCard
                key={skill.id}
                skill={skill}
                mine={skill.owner_user_id === currentUserId || isAdmin}
                isAdmin={isAdmin}
                onReload={() => void reload()}
                onPreview={(row) => void openPreview(row)}
              />
            ))}
          </div>
        </TabsContent>

      </Tabs>

      <SkillPreviewDialog skill={preview} onClose={() => setPreview(null)} />
    </div>
  );
}

function filterSkills(rows: SkillRow[], query: string): SkillRow[] {
  const q = query.trim().toLowerCase();
  if (!q) return rows;
  return rows.filter((row) =>
    `${row.name} ${row.title ?? ""} ${row.description} ${row.owner_email ?? ""}`.toLowerCase().includes(q),
  );
}
