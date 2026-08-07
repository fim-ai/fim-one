import { defineConfig } from "vitest/config"
import path from "path"

export default defineConfig({
  // tsconfig sets jsx: "preserve" for Next.js; vitest needs a real transform.
  esbuild: { jsx: "automatic" },
  test: {
    environment: "jsdom",
    globals: false,
    include: ["src/**/__tests__/**/*.test.ts", "src/**/__tests__/**/*.test.tsx"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
})
