import { getDefaultSavesDirectory, isTauriDesktop } from "../utils/file.ts";
import { validateNativeProject, type NativeReplayProject } from "../domain/nativeReplay.ts";

export async function openNativeProject(): Promise<{ project: NativeReplayProject; path: string } | null> {
  if (!isTauriDesktop()) throw new Error("桌面文件功能不可用");
  const [{ open }, { invoke }] = await Promise.all([
    import("@tauri-apps/plugin-dialog"), import("@tauri-apps/api/core")
  ]);
  const path = await open({ multiple: false,
    defaultPath: await getDefaultSavesDirectory() ?? undefined,
    filters: [{ name: "原生战斗项目", extensions: ["json"] }] });
  if (!path || Array.isArray(path)) return null;
  const raw = JSON.parse(await invoke<string>("read_project_file", { path }));
  return { project: validateNativeProject(raw), path };
}

export async function saveNativeProject(project: NativeReplayProject, currentPath: string | null): Promise<string> {
  if (!isTauriDesktop()) throw new Error("桌面文件功能不可用");
  validateNativeProject(project);
  const [{ invoke }, { join }] = await Promise.all([
    import("@tauri-apps/api/core"), import("@tauri-apps/api/path")
  ]);
  const directory = await getDefaultSavesDirectory();
  if (!directory) throw new Error("未找到项目保存目录");
  const filename = `${project.title.replace(/[\\/:*?"<>|]/g, "_") || "新战斗"}_${project.projectId.slice(0, 8)}.cr-native-replay.json`;
  const path = currentPath ?? await join(directory, filename);
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
  if (typeof session.path !== "string" || typeof session.nodeId !== "string") throw new Error("上次打开记录无效");
  const { invoke } = await import("@tauri-apps/api/core");
  const project = validateNativeProject(JSON.parse(await invoke<string>("read_project_file", { path: session.path })));
  return { project, path: session.path, nodeId: project.nodes[session.nodeId] ? session.nodeId : project.rootNodeId };
}
