import { NativeUiError } from "./i18n.ts";
import { isTauriDesktop } from "../utils/file.ts";
import type { EngineIdentity, NativeBattleAction, NativeMatchConfig, NativeEngineCatalog } from "../domain/nativeReplay.ts";

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
  stageCode?: string;
  ready: boolean;
  playing: boolean;
  playback?: {targetTick:number;completed:boolean}|null;
  error: string | null;
  identity: EngineIdentity;
  cropProfile: CropProfile;
  engineCatalog?: NativeEngineCatalog;
  engineProfile?: string;
  capabilities: Record<string, boolean> | null;
  environmentConfigured?: boolean;
  resourcesConfigured?: boolean;
  setup?: { state: string; stage: string; stageCode?:string; downloaded: number; total: number; error: string | null };
}

export interface NativeObservation {
  cropProfile?: CropProfile;
  tick: number;
  digest: string;
  finalized: boolean;
  result: number | null;
  crowns: number[];
  players: { owner: number; elixir: number; hand: { slot: number; card: number; cost: number; form?:number }[]; cycle:number[]; runtime?: { abilityRuntime: NativeAbility[] | null; evolutionRuntime: unknown[] | null } | null }[];
  entities: unknown[];
}

export interface NativeAbility {
  controllerSlot:number; actionDataGlobalId:number; actionDataName:string;
  remainingCooldownMs:number; remainingChargesRaw:number; buttonState:number; available:boolean;
  championEntityKeys:({owner:number;objectIndex:number;secondaryIndex:number}|null)[];
}

export interface NativeReplayCheckpoint {
  nodeId: string;
  tick: number;
  digest: string;
}

export interface NativeReplaySnapshot extends NativeReplayCheckpoint {
  handle: number;
}

interface BridgeInfo {
  port: number;
  token: string;
  protocol: string;
}

export class NativeBridge {
  private info: BridgeInfo;
  // Survive workspace unmounts so returning from Home keeps verified node frames.
  readonly frameCache = new Map<string, { image: Blob; cropped: boolean }>();
  private constructor(info: BridgeInfo) { this.info=info; }

  static async launch(): Promise<NativeBridge> {
    if (!isTauriDesktop()) throw new NativeUiError("desktopRequired");
    const { invoke } = await import("@tauri-apps/api/core");
    const info = await invoke<BridgeInfo>("start_native_bridge").catch(error=>{throw new NativeUiError("runtimeFailed",String(error));});
    if (info.protocol !== "native-bridge.v1") throw new NativeUiError("protocolMismatch");
    return new NativeBridge(info);
  }

  async get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }

  async post<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  async frame(fresh = false, signal?: AbortSignal): Promise<Blob> {
    const path=fresh?"/v1/frame?fresh=1":"/v1/frame";
    try {
    const response = await fetch(this.url(path), {
      headers: { Authorization: `Bearer ${this.info.token}` }, cache: "no-store",
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(8000)]) : AbortSignal.timeout(8000)
    });
    if (!response.ok) {
      const result=await response.json().catch(()=>null);
      throw new NativeUiError("frameFailed",result?.error||String(response.status));
    }
    return await response.blob();
    } catch(error) {
      if(!signal?.aborted)await this.recordFailure(path,error);
      throw error;
    }
  }

  async recordFailure(path:string,error:unknown):Promise<void> {
    const detail=error instanceof Error?error.message:String(error);
    const data={schema:'cr-native-client-failure.v1',createdAt:new Date().toISOString(),path,
      code:error instanceof NativeUiError?error.code:'clientError',error:detail.split(this.info.token).join('<已隐藏>').slice(0,4000)};
    try {
      const {invoke}=await import('@tauri-apps/api/core');
      await invoke('write_native_diagnostic',{data:JSON.stringify(data)});
    } catch { /* Keep the original failure when diagnostics cannot be saved. */ }
  }

  async replay(seed: number, actions: NativeBattleAction[], targetTick: number,
               expectedDigest?: string, snapshotHandle?: number, baseTick?: number,
               baseDigest?: string, match?: NativeMatchConfig,
               checkpoints?: NativeReplayCheckpoint[]): Promise<NativeObservation & { snapshots?: NativeReplaySnapshot[] }> {
    return this.post("/v1/replay", {
      seed, actions, targetTick, expectedDigest, snapshotHandle, baseTick, baseDigest, checkpoints,
      deck0: match?.deck0, deck1: match?.deck1, forms0:match?.forms0, forms1:match?.forms1, level:match?.level, dealMode:match?.dealMode, tower0:match?.tower0, tower1:match?.tower1
    });
  }

  private url(path: string): string {
    return `http://127.0.0.1:${this.info.port}${path}`;
  }

  private async request<T>(method: string, path: string, body?: Record<string, unknown>): Promise<T> {
    const code=path === "/v1/replay" ? "replayFailed" : path.startsWith("/v1/setup") ? "runtimeFailed" : "requestFailed";
    try {
      const response = await fetch(this.url(path), {
        method,
        headers: {
          Authorization: `Bearer ${this.info.token}`,
          ...(body ? { "Content-Type": "application/json" } : {})
        },
        body: body ? JSON.stringify(body) : undefined,
        cache: "no-store",
        signal: AbortSignal.timeout(method === "GET" ? 8000 : 600000)
      });
      const result = await response.json();
      if (!response.ok) throw new NativeUiError(code,result.error || String(response.status));
      return result as T;
    } catch(error) {
      await this.recordFailure(path,error);
      if(error instanceof NativeUiError)throw error;
      throw new NativeUiError(code,String(error));
    }
  }
}
