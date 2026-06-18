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

const DEFAULT_SAVE_DIR = "saves";

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

  const { appLocalDataDir, join } = await import("@tauri-apps/api/path");
  return join(await appLocalDataDir(), DEFAULT_SAVE_DIR);
}

export async function listSavedProjects(): Promise<SavedProjectInfo[]> {
  if (!isTauriDesktop()) {
    return [];
  }

  const [{ join }, { BaseDirectory, mkdir, readDir, stat }] = await Promise.all([
    import("@tauri-apps/api/path"),
    import("@tauri-apps/plugin-fs")
  ]);
  await mkdir(DEFAULT_SAVE_DIR, { baseDir: BaseDirectory.AppLocalData, recursive: true });
  const savesDirectory = await getDefaultSavesDirectory();
  const entries = await readDir(DEFAULT_SAVE_DIR, { baseDir: BaseDirectory.AppLocalData });
  const files = entries.filter((entry) => entry.isFile && entry.name.toLowerCase().endsWith(".json"));

  const items = await Promise.all(
    files.map(async (entry) => {
      const relativePath = `${DEFAULT_SAVE_DIR}/${entry.name}`;
      const fileInfo = await stat(relativePath, { baseDir: BaseDirectory.AppLocalData }).catch(() => null);
      return {
        name: entry.name,
        path: savesDirectory ? await join(savesDirectory, entry.name) : relativePath,
        updatedAt: getFileTimestamp(fileInfo)
      };
    })
  );

  return items.sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0));
}

export async function openSavedProject(path: string): Promise<NativeOpenResult> {
  if (!isTauriDesktop()) {
    throw new Error("Native file APIs are unavailable.");
  }

  const { BaseDirectory, readTextFile } = await import("@tauri-apps/plugin-fs");
  const defaultRelativePath = await getDefaultSaveRelativePath(path);
  const text = defaultRelativePath
    ? await readTextFile(defaultRelativePath, { baseDir: BaseDirectory.AppLocalData })
    : await readTextFile(path);
  return {
    path,
    raw: JSON.parse(text)
  };
}

export async function openProjectFromNativeDialog(): Promise<NativeOpenResult | null> {
  if (!isTauriDesktop()) {
    return null;
  }

  const [{ open }, { readTextFile }] = await Promise.all([
    import("@tauri-apps/plugin-dialog"),
    import("@tauri-apps/plugin-fs")
  ]);
  const selectedPath = await open({
    multiple: false,
    filters: [{ name: "Replay JSON", extensions: ["json"] }]
  });

  if (!selectedPath || Array.isArray(selectedPath)) {
    return null;
  }

  const text = await readTextFile(selectedPath);
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

  const [{ appLocalDataDir, join }, { BaseDirectory, mkdir, writeTextFile }] = await Promise.all([
    import("@tauri-apps/api/path"),
    import("@tauri-apps/plugin-fs")
  ]);
  await mkdir(DEFAULT_SAVE_DIR, { baseDir: BaseDirectory.AppLocalData, recursive: true });
  const savesDirectory = await join(await appLocalDataDir(), DEFAULT_SAVE_DIR);
  const defaultFilename = getDefaultProjectFilename(project);
  const defaultRelativePath =
    currentPath === null
      ? `${DEFAULT_SAVE_DIR}/${defaultFilename}`
      : await getDefaultSaveRelativePath(currentPath, savesDirectory);
  const targetPath = currentPath ?? (await join(savesDirectory, defaultFilename));

  if (!targetPath) {
    return null;
  }

  if (defaultRelativePath) {
    const normalizedRelativePath = defaultRelativePath.toLowerCase().endsWith(".json")
      ? defaultRelativePath
      : `${defaultRelativePath}.cr-replay.json`;
    await writeTextFile(normalizedRelativePath, JSON.stringify(project, null, 2), {
      baseDir: BaseDirectory.AppLocalData
    });
    const filename = normalizedRelativePath.split("/").pop() ?? getDefaultProjectFilename(project);
    return join(savesDirectory, filename);
  }

  const normalizedPath = targetPath.toLowerCase().endsWith(".json") ? targetPath : `${targetPath}.cr-replay.json`;
  await writeTextFile(normalizedPath, JSON.stringify(project, null, 2));
  return normalizedPath;
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

async function getDefaultSaveRelativePath(
  path: string,
  knownSavesDirectory?: string
): Promise<string | null> {
  const savesDirectory = knownSavesDirectory ?? (await getDefaultSavesDirectory());
  if (!savesDirectory) {
    return null;
  }

  const normalizedDirectory = normalizePathForCompare(savesDirectory);
  const normalizedPath = normalizePathForCompare(path);
  if (!normalizedPath.startsWith(`${normalizedDirectory}/`)) {
    return null;
  }

  const filename = path.split(/[\\/]/).pop();
  return filename ? `${DEFAULT_SAVE_DIR}/${filename}` : null;
}

function normalizePathForCompare(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/g, "").toLowerCase();
}

function getFileTimestamp(fileInfo: unknown): number | undefined {
  if (!fileInfo || typeof fileInfo !== "object") {
    return undefined;
  }

  const maybeInfo = fileInfo as { mtime?: unknown; modifiedAt?: unknown };
  const rawDate = maybeInfo.mtime ?? maybeInfo.modifiedAt;
  if (rawDate instanceof Date) {
    return rawDate.getTime();
  }
  if (typeof rawDate === "string" || typeof rawDate === "number") {
    const timestamp = new Date(rawDate).getTime();
    return Number.isNaN(timestamp) ? undefined : timestamp;
  }
  return undefined;
}
