#!/usr/bin/env node
/**
 * Validate MDX files with @mdx-js/mdx compiler.
 * Usage: node validate.mjs file1.mdx file2.mdx ...
 * Exit code 1 if any file has parse errors.
 */
import { compile } from '@mdx-js/mdx';
import { readFileSync } from 'fs';

const files = process.argv.slice(2);
if (!files.length) process.exit(0);

let failed = 0;
for (const file of files) {
  try {
    const content = readFileSync(file, 'utf8');
    await compile(content);
  } catch (e) {
    failed++;
    const loc = e.line ? ` (line ${e.line}, col ${e.column})` : '';
    const msg = e.message.split('\n')[0];
    console.error(`  ✗ ${file}${loc}: ${msg}`);
  }
}

if (failed) {
  console.error(`\nMDX validation failed: ${failed} file(s) with errors.`);
  process.exit(1);
}
