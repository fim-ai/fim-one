import path from "node:path"
import { defineConfig, devices } from "@playwright/test"

/**
 * Workflow editor smoke. `pnpm e2e` starts everything it needs:
 *
 *   1. e2e/stubs/server.mjs  fake model, embeddings and connector target
 *   2. e2e/backend.py        the real backend on a throwaway SQLite database
 *   3. the Next.js app       built into .next-e2e and served
 *
 * Ports and the Python interpreter can be overridden:
 *
 *   E2E_PYTHON     interpreter for the backend (default: `uv run python`).
 *                  In a git worktree without its own venv, point it at one:
 *                  E2E_PYTHON=~/Workspace/fim-one/.venv/bin/python pnpm e2e
 *   E2E_WEB_MODE   `build` (default) or `dev` (next dev, quicker to start,
 *                  compiles pages on first visit)
 *   E2E_API_PORT / E2E_WEB_PORT / E2E_STUB_PORT   default 8765 / 3765 / 8766
 *
 * None of the three servers is ever reused: if something already listens on
 * one of the ports, Playwright stops with "... is already used" instead of
 * testing against a process it did not start (another checkout's app, a
 * stale stub, a backend on someone's real database).
 */

const repoRoot = path.resolve(__dirname, "..")
const apiPort = Number(process.env.E2E_API_PORT || 8765)
const webPort = Number(process.env.E2E_WEB_PORT || 3765)
const stubPort = Number(process.env.E2E_STUB_PORT || 8766)
const python = process.env.E2E_PYTHON || "uv run python"
const webMode = process.env.E2E_WEB_MODE === "dev" ? "dev" : "build"

const apiUrl = `http://127.0.0.1:${apiPort}`
const webUrl = `http://127.0.0.1:${webPort}`
const stubUrl = `http://127.0.0.1:${stubPort}`

// Read by the spec and its helpers.
process.env.E2E_API_URL = apiUrl
process.env.E2E_STUB_URL = stubUrl
process.env.E2E_MCP_STUB_PATH = path.join(__dirname, "e2e", "stubs", "mcp-stdio.mjs")
process.env.E2E_STATE_DIR = path.join(__dirname, ".e2e-state")
process.env.E2E_REPO_ROOT = repoRoot

const webEnv = {
  NEXT_PUBLIC_API_URL: apiUrl,
  NEXT_DIST_DIR: ".next-e2e",
  NEXT_TELEMETRY_DISABLED: "1",
}

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: !!process.env.CI,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: webUrl,
    locale: "en-US",
    viewport: { width: 1600, height: 1000 },
    actionTimeout: 15_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } } }],
  webServer: [
    {
      name: "stub",
      command: "node e2e/stubs/server.mjs",
      url: `${stubUrl}/health`,
      env: { E2E_STUB_PORT: String(stubPort) },
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      name: "backend",
      command: `${python} frontend/e2e/backend.py`,
      cwd: repoRoot,
      url: `${apiUrl}/api/version`,
      env: {
        E2E_API_PORT: String(apiPort),
        E2E_STUB_PORT: String(stubPort),
        E2E_STATE_DIR: path.join(__dirname, ".e2e-state"),
      },
      reuseExistingServer: false,
      timeout: 180_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      name: "web",
      command:
        webMode === "dev"
          ? `pnpm exec next dev --turbopack --hostname 127.0.0.1 --port ${webPort}`
          : `pnpm exec next build && pnpm exec next start --hostname 127.0.0.1 --port ${webPort}`,
      url: `${webUrl}/login`,
      env: webEnv,
      reuseExistingServer: false,
      timeout: 600_000,
    },
  ],
})
