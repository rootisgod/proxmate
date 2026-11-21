import {build} from 'esbuild';
import {readFile, writeFile, cp} from 'node:fs/promises';

const out = 'static/editor-assets';
const result = await build({
  entryPoints: ['editor/code.mjs'], bundle: true, format: 'esm', minify: true,
  outfile: out + '/code.js', metafile: true, target: 'es2022'
});
const roots = new Set(Object.keys(result.metafile.inputs)
  .map(path => path.match(/^node_modules\/(?:@[^/]+\/)?[^/]+/u)?.[0]).filter(Boolean));
const licenses = [];
for (const root of [...roots].sort()) {
  const {name} = JSON.parse(await readFile(root + '/package.json', 'utf8'));
  const license = await readFile(root + '/LICENSE', 'utf8');
  licenses.push(name + '\n' + license);
}
await writeFile(out + '/licenses.txt', licenses.join('\n'));

// Serve the workspace icons locally on isolated homelab networks.
await cp('node_modules/bootstrap-icons/font', out + '/icons', {recursive:true});
await cp('node_modules/bootstrap-icons/LICENSE', out + '/icons/LICENSE');
await cp('node_modules/@fontsource-variable/geist/files/geist-latin-wght-normal.woff2', out + '/fonts/geist-latin.woff2');
await cp('node_modules/@fontsource-variable/geist/LICENSE', out + '/fonts/LICENSE');
