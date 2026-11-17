#!/usr/bin/env node
// Pre-compiles inline <script type="text/babel"> blocks into plain JS so
// the browser doesn't need babel-standalone. Outputs a deploy-ready `dist/`.
//
// Usage: node build.js
// Then deploy the contents of ./dist to Cloudflare.

const fs = require('fs');
const path = require('path');
const esbuild = require('esbuild');

const ROOT = __dirname;
const OUT = path.join(ROOT, 'dist');

// Files/dirs copied as-is to dist.
const COPY_PATHS = ['css', 'js', 'sheets', 'proctor', '404.html', 'favicon.ico', 'favicon.png', 'og-image.png', 'robots.txt'];
// HTML pages that have inline JSX to compile.
const HTML_GLOBS = [
  'index.html', 'dashboard.html', 'register.html', 'exam.html',
  'news.html', 'simulari.html',
  'admin/index.html', 'admin/exams.html', 'admin/exam-builder.html',
  'admin/sessions.html', 'admin/participants.html', 'admin/results.html',
  'admin/analytics.html', 'admin/settings.html', 'admin/news.html',
  'admin/login.html',
];

function rmrf(p) {
  if (!fs.existsSync(p)) return;
  fs.rmSync(p, { recursive: true, force: true });
}

function cpR(src, dst) {
  const abs = path.join(ROOT, src);
  if (!fs.existsSync(abs)) return;
  const target = path.join(OUT, src);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const st = fs.statSync(abs);
  if (st.isDirectory()) {
    fs.cpSync(abs, target, { recursive: true });
  } else {
    fs.copyFileSync(abs, target);
  }
}

async function compileJsx(code) {
  const result = await esbuild.transform(code, {
    loader: 'jsx',
    jsx: 'transform',
    target: 'es2017',
    minify: true,
    legalComments: 'none',
  });
  return result.code;
}

async function processHtml(relPath) {
  const srcPath = path.join(ROOT, relPath);
  if (!fs.existsSync(srcPath)) {
    console.log(`  skip ${relPath} (not found)`);
    return;
  }
  let html = fs.readFileSync(srcPath, 'utf8');
  const babelRe = /<script\s+type="text\/babel"[^>]*>([\s\S]*?)<\/script>/g;
  const matches = [...html.matchAll(babelRe)];

  for (const m of matches) {
    const compiled = await compileJsx(m[1]);
    const replacement = `<script>${compiled}</script>`;
    html = html.replace(m[0], replacement);
  }

  // Drop the babel-standalone CDN script — no longer needed.
  html = html.replace(/\s*<script\s+src="https:\/\/cdnjs\.cloudflare\.com\/ajax\/libs\/babel-standalone\/[^"]+"[^>]*><\/script>/g, '');

  const outPath = path.join(OUT, relPath);
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, html);
  console.log(`  ✓ ${relPath} (${matches.length} JSX block${matches.length !== 1 ? 's' : ''})`);
}

async function build() {
  console.log('→ Cleaning dist/');
  rmrf(OUT);
  fs.mkdirSync(OUT, { recursive: true });

  console.log('→ Copying static assets');
  for (const p of COPY_PATHS) cpR(p, p);

  console.log('→ Compiling HTML pages');
  for (const h of HTML_GLOBS) {
    await processHtml(h);
  }

  console.log('\n✓ Build complete — deploy ./dist');
}

build().catch(err => {
  console.error('Build failed:', err);
  process.exit(1);
});
