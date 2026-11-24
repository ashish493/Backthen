import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "@fontsource-variable/fraunces";
import "./styles.css";

// Touchpad pinch reaches the page as ctrl+wheel (WebView2 only delivers it with zoomHotkeysEnabled, see
// tauri.conf.json). The globe uses it to zoom; everywhere else it must not zoom the whole window. Keyboard
// Ctrl +/- (scale the interface) stays available.
window.addEventListener("wheel", (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
