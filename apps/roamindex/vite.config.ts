import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed dev port and no browser auto-open.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420, strictPort: true, host: "127.0.0.1",
    // Rust builds write (and lock) files under src-tauri/target; watching them crashes Vite with EBUSY on Windows.
    watch: { ignored: ["**/src-tauri/**"] },
  },
  build: { target: "es2021", chunkSizeWarningLimit: 1500 },
});
