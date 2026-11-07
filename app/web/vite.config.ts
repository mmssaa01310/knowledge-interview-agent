import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_PROXY_TARGET ?? env.VITE_API_BASE_URL ?? "http://localhost:8001";

  return {
    plugins: [react()],
    // 公開画像をアプリルートとは分離したリポジトリ直下のpublicディレクトリから配信する。
    // 開発・本番ビルドで同じパスを参照できるようにする。
    publicDir: "../../public",
    server: {
      port: 5173,
      strictPort: true,
      watch: {
        usePolling: true,
        interval: 250,
        // WSL/DrvFsでは、エージェント用の隠しディレクトリをchokidarが
        // 再帰走査するとEIOになることがある。ソース監視の対象外にする。
        ignored: ["**/.git/**", "**/.codex/**", "**/.agents/**"],
      },
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
