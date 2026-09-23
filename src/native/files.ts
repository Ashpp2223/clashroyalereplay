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
