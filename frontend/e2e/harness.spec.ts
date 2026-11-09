import { spawnSync } from "node:child_process"
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"

import { expect, test } from "@playwright/test"

/**
 * The launcher of the backend under test (e2e/backend.py) must not be able
 * to delete a directory it did not make, nor let a key or endpoint from the
 * developer's shell reach the app. `--print-env` prepares the state
 * directory and the environment, prints the environment and exits.
 */

const repoRoot = process.env.E2E_REPO_ROOT ?? path.resolve(__dirname, "..", "..")
const liveStateDir = process.env.E2E_STATE_DIR ?? path.join(repoRoot, "frontend", ".e2e-state")
const python = process.env.E2E_PYTHON || "uv run python"

function launcher(stateDir: string, extraEnv: Record<string, string> = {}) {
  const result = spawnSync(`${python} frontend/e2e/backend.py --print-env`, {
    cwd: repoRoot,
    shell: true,
    encoding: "utf8",
    env: { ...process.env, ...extraEnv, E2E_STATE_DIR: stateDir },
  })
  return {
    status: result.status,
    stderr: result.stderr,
    env: result.status === 0 ? (JSON.parse(result.stdout) as Record<string, string>) : {},
  }
}

test.describe("backend launcher", () => {
  let scratch: string

  test.beforeEach(() => {
    scratch = mkdtempSync(path.join(os.tmpdir(), "fim-e2e-harness-"))
  })

  test.afterEach(() => {
    rmSync(scratch, { recursive: true, force: true })
  })

  test("refuses to wipe a directory it did not make", () => {
    const precious = path.join(scratch, "notes.txt")
    writeFileSync(precious, "not the launcher's")
    const run = launcher(scratch)
    expect(run.status).toBe(2)
    expect(run.stderr).toContain("refusing to wipe")
    expect(readFileSync(precious, "utf8")).toBe("not the launcher's")
  })

  test("refuses a marked directory that is outside frontend/.e2e-state", () => {
    // A marker alone is not enough: a copied or guessed marker file must
    // not turn an arbitrary directory into one that gets deleted.
    writeFileSync(path.join(scratch, ".fim-e2e-state"), "copied")
    const precious = path.join(scratch, "notes.txt")
    writeFileSync(precious, "still not the launcher's")
    const run = launcher(scratch)
    expect(run.status).toBe(2)
    expect(existsSync(precious)).toBe(true)
  })

  test("takes an empty directory and marks it", () => {
    const run = launcher(scratch)
    expect(run.status).toBe(0)
    expect(existsSync(path.join(scratch, ".fim-e2e-state"))).toBe(true)
    expect(run.env.DATABASE_URL).toContain(scratch)
  })

  test("wipes a directory of its own under frontend/.e2e-state", () => {
    // A sub-directory of the live state directory: under the allowed root,
    // and away from what the running backend uses.
    const own = path.join(liveStateDir, `harness-check-${process.pid}`)
    mkdirSync(own, { recursive: true })
    try {
      expect(launcher(own).status).toBe(0) // empty: marked
      const leftover = path.join(own, "leftover.db")
      writeFileSync(leftover, "from the previous run")
      expect(launcher(own).status).toBe(0) // marked and under the root: wiped
      expect(existsSync(leftover)).toBe(false)
      expect(existsSync(path.join(own, ".fim-e2e-state"))).toBe(true)
    } finally {
      rmSync(own, { recursive: true, force: true })
    }
  })

  test("lets no key, endpoint or provider of the shell through", () => {
    const run = launcher(scratch, {
      // What a developer's shell or .env typically carries.
      LLM_API_KEY: "sk-LEAKED",
      LLM_BASE_URL: "https://api.LEAKED-provider.example/v1",
      FAST_LLM_API_KEY: "sk-LEAKED-fast",
      FAST_LLM_BASE_URL: "https://fast.LEAKED-provider.example/v1",
      REASONING_LLM_BASE_URL: "https://reasoning.LEAKED-provider.example/v1",
      EMBEDDING_BASE_URL: "https://embeddings.LEAKED-provider.example/v1",
      OPENAI_API_KEY: "sk-LEAKED-openai",
      ANTHROPIC_API_KEY: "sk-LEAKED-anthropic",
      JINA_API_KEY: "jina-LEAKED",
      EXA_API_KEY: "exa-LEAKED",
      TAVILY_API_KEY: "tavily-LEAKED",
      IMAGE_GEN_API_KEY: "img-LEAKED",
      IMAGE_GEN_BASE_URL: "https://images.LEAKED-provider.example",
      LARK_WEBHOOK_URL: "https://open.feishu.cn/hook/LEAKED",
      SLACK_WEBHOOK_URL: "https://hooks.slack.com/LEAKED",
      FEISHU_APP_ID: "cli_LEAKED",
      FEISHU_APP_SECRET: "LEAKED-secret",
      REDIS_URL: "redis://LEAKED:6379/0",
      SMTP_HOST: "smtp.LEAKED.example",
      SMTP_PASS: "LEAKED-pass",
      STRIPE_SECRET_KEY: "sk_live_LEAKED",
      DOCKER_PYTHON_IMAGE: "LEAKED/image",
      CODE_EXEC_BACKEND: "docker",
      DATABASE_URL: "postgresql+asyncpg://LEAKED/prod",
      JWT_SECRET_KEY: "LEAKED-jwt",
      CREDENTIAL_ENCRYPTION_KEY: "LEAKED-credential-key",
      HTTPS_PROXY: "http://proxy.LEAKED.example:8080",
      SOME_UNRELATED_SECRET: "LEAKED",
    })
    expect(run.status).toBe(0)
    const env = run.env
    const stub = `http://127.0.0.1:${process.env.E2E_STUB_PORT || 8766}/v1`

    // Every model role and the embeddings point at the stub.
    for (const name of ["LLM_BASE_URL", "FAST_LLM_BASE_URL", "REASONING_LLM_BASE_URL", "EMBEDDING_BASE_URL"]) {
      expect(env[name], name).toBe(stub)
    }
    for (const name of ["LLM_API_KEY", "FAST_LLM_API_KEY", "REASONING_LLM_API_KEY", "EMBEDDING_API_KEY"]) {
      expect(env[name], name).toBe("sk-e2e-stub")
    }
    // Everything else that names a key, secret, endpoint or provider is
    // set, to nothing: a .env loaded later does not override a set variable.
    for (const name of [
      "OPENAI_API_KEY",
      "ANTHROPIC_API_KEY",
      "JINA_API_KEY",
      "EXA_API_KEY",
      "TAVILY_API_KEY",
      "IMAGE_GEN_API_KEY",
      "IMAGE_GEN_BASE_URL",
      "LARK_WEBHOOK_URL",
      "SLACK_WEBHOOK_URL",
      "WECOM_WEBHOOK_URL",
      "FEISHU_APP_ID",
      "FEISHU_APP_SECRET",
      "REDIS_URL",
      "SMTP_HOST",
      "SMTP_PASS",
      "STRIPE_SECRET_KEY",
      "DOCKER_PYTHON_IMAGE",
      "MCP_SERVERS",
      "WEB_SEARCH_PROVIDER",
    ]) {
      expect(env[name], name).toBe("")
    }
    expect(env.CODE_EXEC_BACKEND).toBe("local")
    expect(env.DATABASE_URL).toMatch(/^sqlite\+aiosqlite:\/\/\//)
    expect(env.JWT_SECRET_KEY).not.toBe("LEAKED-jwt")
    expect(env.CREDENTIAL_ENCRYPTION_KEY).not.toBe("LEAKED-credential-key")
    expect(env.PYTHON_DOTENV_DISABLED).toBe("1")
    // What the launcher does not know is dropped rather than passed on.
    expect(env).not.toHaveProperty("HTTPS_PROXY")
    expect(env).not.toHaveProperty("SOME_UNRELATED_SECRET")
    // No value of the shell survives anywhere.
    expect(JSON.stringify(env)).not.toMatch(/LEAKED/)
  })
})
