import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_PROXY_TARGET ?? env.VITE_API_BASE_URL ?? "http://localhost:8001";
  const voiceTarget = env.VITE_VOICE_PROXY_TARGET ?? env.VITE_VOICE_API_BASE_URL ?? "http://localhost:8010";

  return {
    plugins: [react()],
    // KIKIORIのブランド素材はリポジトリ直下のpublic/imagesを正本とする。
    // React/Viteのアプリルートからも開発・本番ビルドで同じパスを参照できるようにする。
    publicDir: "../../public",
    server: {
      port: 5173,
      strictPort: true,
      watch: {
        usePolling: true,
        interval: 250,
      },
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
        },
        "/voice": {
          target: voiceTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
