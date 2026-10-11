/** Stable error codes shared by the domain and UI; detail is diagnostic data. */
export type NativeErrorCode = "desktopRequired" | "engineMismatch" | "frameFailed" | "interrupted" | "invalidDeal" | "invalidDeck" | "invalidFormCount" | "invalidForms" | "invalidTower" | "invalidProject" | "journalMismatch" | "protocolMismatch" | "replayFailed" | "requestFailed" | "runtimeFailed" | "saveFolderMissing" | "sessionInvalid" | "unsupported";

export class NativeUiError extends Error {
  readonly code: NativeErrorCode;
  constructor(code: NativeErrorCode, detail?: string) { super(detail ?? code); this.code=code; }
}
