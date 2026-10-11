import React from "react";
import ReactDOM from "react-dom/client";
import { LanguageProvider } from "./native/i18n.ts";
import App from "./NativeApp.tsx";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <LanguageProvider><App /></LanguageProvider>
  </React.StrictMode>
);
