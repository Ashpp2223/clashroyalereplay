import { isTauriDesktop } from "../utils/file.ts";
import type { EngineIdentity, NativeBattleAction } from "../domain/nativeReplay.ts";

export interface CropProfile {
  id: string;
  nativeWidth: number;
  nativeHeight: number;
  enemyHud: [number, number, number, number];
  arena: [number, number, number, number];
  myHud: [number, number, number, number];
  world: [number, number, number, number];
  handCentersX: number[];
  enemyHandReversed: boolean;
}

export interface BridgeStatus {
  stage: string;
  ready: boolean;
  playing: boolean;
  error: string | null;
  identity: EngineIdentity;
  cropProfile: CropProfile;
  capabilities: Record<string, boolean> | null;
}

export interface NativeObservation {
  tick: number;
  digest: string;
  finalized: boolean;
  result: number | null;
  crowns: number[];
  players: { owner: number; elixir: number; hand: { slot: number; card: number; cost: number }[] }[];
  entities: unknown[];
}

interface BridgeInfo {
  port: number;
  token: string;
  protocol: string;
}

export class NativeBridge {
  private constructor(private info: BridgeInfo) {}

  static async launch(): Promise<NativeBridge> {
    if (!isTauriDesktop()) throw new Error("原生战斗需要桌面版应用");
    const { invoke } = await import("@tauri-apps/api/core");
    const info = await invoke<BridgeInfo>("start_native_bridge");
    if (info.protocol !== "native-bridge.v1") throw new Error("桥接协议版本不匹配");
    return new NativeBridge(info);
  }

  async get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }

  async post<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  async frame(): Promise<Blob> {
    const response = await fetch(this.url("/v1/frame"), {
      headers: { Authorization: `Bearer ${this.info.token}` }, cache: "no-store"
    });
    if (!response.ok) throw new Error(`画面读取失败：${response.status}`);
    return response.blob();
  }

  async replay(seed: number, actions: NativeBattleAction[], targetTick: number,
               expectedDigest?: string, snapshotHandle?: number, baseTick?: number,
               baseDigest?: string): Promise<NativeObservation> {
    return this.post("/v1/replay", {
      seed, actions, targetTick, expectedDigest, snapshotHandle, baseTick, baseDigest
    });
  }

  private url(path: string): string {
    return `http://127.0.0.1:${this.info.port}${path}`;
  }

  private async request<T>(method: string, path: string, body?: Record<string, unknown>): Promise<T> {
    const response = await fetch(this.url(path), {
      method,
      headers: {
        Authorization: `Bearer ${this.info.token}`,
        ...(body ? { "Content-Type": "application/json" } : {})
      },
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store"
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `桥接请求失败：${response.status}`);
    return result as T;
  }
}
