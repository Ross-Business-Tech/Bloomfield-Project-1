// Cross-platform entry points; publishing always uses the Ross IT safety-checking script.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('..', import.meta.url));
const mode = process.argv[2];
if (!['build', 'smoke', 'preview'].includes(mode)) throw new Error('Use build, smoke or preview');
function run(command, args, env = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', env: { ...process.env, ...env } });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
run(process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3'), ['tools/build_preview.py'], { PYTHONUTF8: '1' });
if (mode !== 'build') {
  run(process.execPath, ['scripts/smoke-test.cjs'], {
    HERENOW_DIST_DIR: 'preview-dist', SMOKE_PATHS: '/,/#/overview,/#/how,/#/explore,/#/leads/B',
  });
  run(process.execPath, ['tools/check_radar.cjs']);
}
if (mode === 'preview') {
  if (!process.env.npm_execpath) throw new Error('Run npm run preview:radar');
  run(process.execPath, [process.env.npm_execpath, 'run', 'publish:preview'], {
    HERENOW_DIST_DIR: 'preview-dist', HERENOW_SKIP_BUILD: '1',
  });
}
