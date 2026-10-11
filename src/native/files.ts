import { NativeUiError, text } from "./i18n.ts";
import { getDefaultSavesDirectory, isTauriDesktop, listSavedProjects } from "../utils/file.ts";
import { validateNativeProject, type NativeReplayProject } from "../domain/nativeReplay.ts";

export async function openNativeProject(): Promise<{ project: NativeReplayProject; path: string } | null> {
  if (!isTauriDesktop()) throw new NativeUiError("desktopRequired");
  const { open } = await import("@tauri-apps/plugin-dialog");
  const path = await open({ multiple: false,
    defaultPath: await getDefaultSavesDirectory() ?? undefined,
    filters: [{ name: text("battle"), extensions: ["json"] }] });
  if (!path || Array.isArray(path)) return null;
  return readNativeProject(path);
}

export async function readNativeProject(path: string): Promise<{ project: NativeReplayProject; path: string }> {
  if (!isNativeProjectPath(path)) throw new NativeUiError("unsupported");
  const { invoke } = await import("@tauri-apps/api/core");
  const raw = JSON.parse(await invoke<string>("read_project_file", { path }));
  return { project: validateNativeProject(raw), path };
}

export function isNativeProjectPath(path: string): boolean {
  return /\.cr-native-replay\.json$/i.test(path);
}

export interface SavedReplayEntry {
  path: string;
  title: string;
  name: string;
  updatedAt?: number;
  nodeCount: number;
}

export async function listReplayArchives(): Promise<SavedReplayEntry[]> {
  const files = (await listSavedProjects()).filter(file => isNativeProjectPath(file.name));
  const entries = await Promise.all(files.map(async file => {
    try {
      const { project } = await readNativeProject(file.path);
      return { ...file, title: project.title, nodeCount: Object.keys(project.nodes).length };
    } catch { return null; }
  }));
  return entries.filter((entry): entry is SavedReplayEntry => entry !== null);
}

export async function saveNativeProject(project: NativeReplayProject, currentPath: string | null): Promise<string> {
  if (!isTauriDesktop()) throw new NativeUiError("desktopRequired");
  validateNativeProject(project);
  if (currentPath && !isNativeProjectPath(currentPath)) throw new NativeUiError("unsupported");
  const [{ invoke }, { join }] = await Promise.all([
    import("@tauri-apps/api/core"), import("@tauri-apps/api/path")
  ]);
  const directory = currentPath ? null : await getDefaultSavesDirectory();
  if (!currentPath && !directory) throw new NativeUiError("saveFolderMissing");
  const filename = `${project.title.replace(/[\\/:*?"<>|]/g, "_") || "native-replay"}_${project.projectId.slice(0, 8)}.cr-native-replay.json`;
  const path = currentPath ?? await join(directory!, filename);
  await invoke("write_project_file", { path, data: JSON.stringify(project, null, 2) });
  return path;
}

const LAST_SESSION = "clash-native.last-session";

export function rememberNativeSession(path: string, nodeId: string): void {
  localStorage.setItem(LAST_SESSION, JSON.stringify({ path, nodeId }));
}

export async function reopenNativeSession(): Promise<{ project: NativeReplayProject; path: string; nodeId: string } | null> {
  const text = localStorage.getItem(LAST_SESSION);
  if (!text) return null;
  const session = JSON.parse(text) as { path: string; nodeId: string };
  if (typeof session.path !== "string" || typeof session.nodeId !== "string") throw new NativeUiError("sessionInvalid");
  const { project } = await readNativeProject(session.path);
  return { project, path: session.path, nodeId: project.nodes[session.nodeId] ? session.nodeId : project.rootNodeId };
}
