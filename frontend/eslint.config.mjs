import { dirname } from "path"
import { fileURLToPath } from "url"
import { FlatCompat } from "@eslint/eslintrc"

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const compat = new FlatCompat({
  baseDirectory: __dirname,
})

const eslintConfig = [
  // Generated files: build output (wherever a build put it), the type stub
  // Next.js writes next to it, and test artifacts.
  {
    ignores: [
      ".next/**",
      ".next-build/**",
      ".next-e2e/**",
      "next-env.d.ts",
      "playwright-report/**",
      "test-results/**",
    ],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
]

export default eslintConfig
