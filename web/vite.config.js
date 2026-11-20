import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Dev: `npm run dev` serves on http://localhost:5173 and proxies /api (REST + WebSocket)
// to the API on 127.0.0.1:8100 (server/tools/dev.sh api). localhost counts as a secure
// context, so Web Serial flashing works there.
export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: [".."] }, // contracts/chips.json and examples live outside web/
    proxy: {
      "/api": { target: "http://127.0.0.1:8100", ws: true, changeOrigin: false },
    },
  },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 900 },
  test: { include: ["test/**/*.test.js"] },
});
