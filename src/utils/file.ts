import type { ReplayProject } from "../domain/types.ts";

type NativeOpenResult = {
  path: string;
  raw: unknown;
};

export type SavedProjectInfo = {
  name: string;
  path: string;
  updatedAt?: number;
};

type NativeSavedProjectInfo = {
  name: string;
  path: string;
  updated_at?: number | null;
};

const DEFAULT_DATA_DIR = "data";
const DEFAULT_SAVE_DIR = "saves";
const SAVE_DIR_STORAGE_KEY = "clash-replay-tree.savesDirectory";

export function downloadProject(project: ReplayProject): void {
  const filename = `${sanitizeFilename(project.projectName)}.cr-replay.json`;
  const json = JSON.stringify(project, null, 2);
  const blob = new Blob([json], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function readProjectFile(file: File): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        resolve(JSON.parse(String(reader.result)));
      } catch (error) {
        reject(error);
      }
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file, "utf-8");
  });
}

export function isTauriDesktop(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export async function getDefaultSavesDirectory(): Promise<string | null> {
  if (!isTauriDesktop()) {
    return null;
  }

  const storedDirectory = readStoredSavesDirectory();
  if (storedDirectory && (await ensureWritableDirectory(storedDirectory))) {
    return storedDirectory;
  }

  // User projects must survive replacing or uninstalling the program files.
  const fallbackDirectory = await getFallbackSavesDirectory();
  await requireWritableDirectory(fallbackDirectory);
  return fallbackDirectory;
}

export async function chooseSavesDirectory(): Promise<string | null> {
  if (!isTauriDesktop()) {
    return null;
  }

  const [{ open }] = await Promise.all([import("@tauri-apps/plugin-dialog")]);
  const currentDirectory = await getDefaultSavesDirectory();
  const selectedPath = await open({
    directory: true,
    multiple: false,
    defaultPath: currentDirectory ?? undefined
  });

  if (!selectedPath || Array.isArray(selectedPath)) {
    return null;
  }

  await requireWritableDirectory(selectedPath);
  storeSavesDirectory(selectedPath);
  return selectedPath;
}

export async function listSavedProjects(): Promise<SavedProjectInfo[]> {
  if (!isTauriDesktop()) {
    return [];
  }

  const savesDirectory = await getDefaultSavesDirectory();
  if (!savesDirectory) {
    return [];
  }

  const files = await invokeNative<NativeSavedProjectInfo[]>("list_project_files", { path: savesDirectory });
  return files.map((file) => ({
    name: file.name,
    path: file.path,
    updatedAt: file.updated_at ?? undefined
  }));
}

export async function openSavedProject(path: string): Promise<NativeOpenResult> {
  if (!isTauriDesktop()) {
    throw new Error("Native file APIs are unavailable.");
  }

  const text = await readProjectText(path);
  return {
    path,
    raw: JSON.parse(text)
  };
}

export async function openProjectFromNativeDialog(): Promise<NativeOpenResult | null> {
  if (!isTauriDesktop()) {
    return null;
  }

  const [{ open }] = await Promise.all([import("@tauri-apps/plugin-dialog")]);
  const defaultPath = await getDefaultSavesDirectory();
  const selectedPath = await open({
    multiple: false,
    defaultPath: defaultPath ?? undefined,
    filters: [{ name: "Replay File", extensions: ["json"] }]
  });

  if (!selectedPath || Array.isArray(selectedPath)) {
    return null;
  }

  const text = await readProjectText(selectedPath);
  return {
    path: selectedPath,
    raw: JSON.parse(text)
  };
}

export async function saveProjectToNativeFile(
  project: ReplayProject,
  currentPath: string | null
): Promise<string | null> {
  if (!isTauriDesktop()) {
    return null;
  }

  const { join } = await import("@tauri-apps/api/path");
  const savesDirectory = await getDefaultSavesDirectory();
  if (!savesDirectory) {
    return null;
  }

  const defaultFilename = getDefaultProjectFilename(project);
  const targetPath = currentPath ?? (await join(savesDirectory, defaultFilename));

  if (!targetPath) {
    return null;
  }

  const normalizedPath = targetPath.toLowerCase().endsWith(".json") ? targetPath : `${targetPath}.cr-replay.json`;
  const data = JSON.stringify(project, null, 2);
  try {
    await writeProjectText(normalizedPath, data);
    return normalizedPath;
  } catch (error) {
    if (currentPath === null) {
      throw error;
    }
  }

  const fallbackPath = await join(savesDirectory, defaultFilename);
  await writeProjectText(fallbackPath, data);
  return fallbackPath;
}

async function getBundledSavesDirectory(): Promise<string> {
  const { join } = await import("@tauri-apps/api/path");
  return join(await invokeNative<string>("executable_directory", {}), DEFAULT_DATA_DIR, DEFAULT_SAVE_DIR);
}

async function getFallbackSavesDirectory(): Promise<string> {
  const { appLocalDataDir, join } = await import("@tauri-apps/api/path");
  return join(await appLocalDataDir(), DEFAULT_DATA_DIR, DEFAULT_SAVE_DIR);
}

async function requireWritableDirectory(path: string): Promise<void> {
  if (!(await ensureWritableDirectory(path))) {
    throw new Error("Save directory is not writable.");
  }
}

async function ensureWritableDirectory(path: string): Promise<boolean> {
  try {
    await invokeNative("ensure_writable_directory", { path });
    return true;
  } catch {
    return false;
  }
}

async function readProjectText(path: string): Promise<string> {
  return invokeNative<string>("read_project_file", { path });
}

async function writeProjectText(path: string, data: string): Promise<void> {
  await invokeNative("write_project_file", { path, data });
}

async function invokeNative<T = void>(command: string, args: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command, args);
}

function readStoredSavesDirectory(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  return window.localStorage.getItem(SAVE_DIR_STORAGE_KEY)?.trim() || null;
}

function storeSavesDirectory(path: string): void {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(SAVE_DIR_STORAGE_KEY, path);
}

function getDefaultProjectFilename(project: ReplayProject): string {
  const timestamp = formatFilenameTimestamp(project.createdAt);
  return `${sanitizeFilename(project.projectName)}_${timestamp}.cr-replay.json`;
}

function sanitizeFilename(value: string): string {
  return value.replace(/[\\/:*?"<>|]/g, "_").trim() || "clash-replay";
}

function formatFilenameTimestamp(value: string): string {
  const date = new Date(value);
  const safeDate = Number.isNaN(date.getTime()) ? new Date() : date;
  const year = safeDate.getFullYear();
  const month = String(safeDate.getMonth() + 1).padStart(2, "0");
  const day = String(safeDate.getDate()).padStart(2, "0");
  const hour = String(safeDate.getHours()).padStart(2, "0");
  const minute = String(safeDate.getMinutes()).padStart(2, "0");
  const second = String(safeDate.getSeconds()).padStart(2, "0");
  return `${year}${month}${day}-${hour}${minute}${second}`;
}
