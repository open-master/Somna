"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Copy, FileText, Folder, Upload, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  createSkill,
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
    <div
      role="button"
      tabIndex={0}
      className="w-full rounded-xl border bg-background p-3 text-left shadow-sm transition hover:border-primary/40 hover:shadow-md"
      onClick={() => onPreview(skill)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onPreview(skill);
        }
      }}
    >
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

function SkillPreviewDialog({
  skill,
  onClose,
}: {
  skill: SkillDetail | null;
  onClose: () => void;
}) {
  const [selectedPath, setSelectedPath] = useState("SKILL.md");
  const files = skill?.files ?? {};
  const filePaths = Object.keys(files);
  const safeSelected = selectedPath in files ? selectedPath : "SKILL.md";
  const selectedContent = files[safeSelected] ?? skill?.skill_md ?? "";
  const isSkillMd = safeSelected === "SKILL.md";
  const parsed = splitSkillMarkdown(selectedContent);
  const tree = groupedFiles(files);

  useEffect(() => {
    if (skill) setSelectedPath("SKILL.md");
  }, [skill?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function copyCurrent() {
    try {
      await navigator.clipboard.writeText(selectedContent);
    } catch {
      window.alert("复制失败");
    }
  }

  return (
    <Dialog.Root open={!!skill} onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[70] bg-black/45 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-[50%] top-[50%] z-[71] flex h-[min(760px,calc(100vh-1.5rem))] w-[min(1120px,calc(100vw-1.5rem))] translate-x-[-50%] translate-y-[-50%] overflow-hidden rounded-2xl border bg-background shadow-2xl outline-none">
          {skill ? (
            <>
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
                    <Dialog.Title className="truncate text-base font-semibold">{safeSelected}</Dialog.Title>
                    <Dialog.Description className="mt-1 truncate text-xs text-muted-foreground">
                      {VISIBILITY_LABEL[skill.visibility]} · {filePaths.length} 个文件 · {skill.description}
                    </Dialog.Description>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Button type="button" size="sm" variant="outline" onClick={() => void copyCurrent()}>
                      <Copy className="mr-1.5 size-3.5" />
                      复制
                    </Button>
                    <Dialog.Close asChild>
                      <Button type="button" size="icon" variant="ghost" aria-label="关闭">
                        <X className="size-4" />
                      </Button>
                    </Dialog.Close>
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
            </>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function SettingsSkillManagement({ isAdmin, currentUserId }: { isAdmin: boolean; currentUserId: string | null }) {
  const [tab, setTab] = useState("mine");
  const [mine, setMine] = useState<SkillRow[]>([]);
  const [market, setMarket] = useState<SkillRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [skillMd, setSkillMd] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [visibility, setVisibility] = useState<"private" | "shared">("private");
  const [creating, setCreating] = useState(false);
  const [preview, setPreview] = useState<SkillDetail | null>(null);

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

  async function submit() {
    setCreating(true);
    try {
      if (file) {
        await uploadSkill(file, visibility);
        setFile(null);
      } else {
        await createSkill(skillMd, visibility);
        setSkillMd("");
      }
      await reload();
      setTab("mine");
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "创建 Skill 失败");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
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

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="max-w-md">
          <TabsTrigger value="mine">我的技能</TabsTrigger>
          <TabsTrigger value="market">技能市场</TabsTrigger>
          <TabsTrigger value="create">创建/上传</TabsTrigger>
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

        <TabsContent value="create" className="space-y-3">
          <div className="rounded-xl border bg-muted/20 p-4">
            <p className="text-sm font-medium">上传 Claude 标准 Skill</p>
            <p className="mt-1 text-xs text-muted-foreground">支持单个 SKILL.md 或包含 SKILL.md 的 .zip 包。</p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Input
                type="file"
                accept=".md,.zip"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="max-w-sm"
              />
              {!isAdmin ? (
                <select
                  value={visibility}
                  onChange={(e) => setVisibility(e.target.value as "private" | "shared")}
                  className="h-10 rounded-md border border-input bg-background px-2 text-sm"
                >
                  <option value="private">私有</option>
                  <option value="shared">共享</option>
                </select>
              ) : (
                <Badge variant="success">管理员上传将成为官方 Skill</Badge>
              )}
            </div>
          </div>

          <div className="rounded-xl border bg-muted/20 p-4">
            <p className="text-sm font-medium">或粘贴 SKILL.md</p>
            <Textarea
              value={skillMd}
              onChange={(e) => setSkillMd(e.target.value)}
              placeholder={"---\nname: example-skill\ndescription: ...\n---\n\n# Example Skill\n\n## Instructions\n..."}
              className="mt-3 min-h-[220px] font-mono text-xs"
            />
          </div>

          <Button type="button" disabled={creating || (!file && !skillMd.trim())} onClick={() => void submit()}>
            <Upload className="mr-2 size-4" />
            {creating ? "保存中…" : isAdmin ? "保存为官方 Skill" : "保存 Skill"}
          </Button>
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
