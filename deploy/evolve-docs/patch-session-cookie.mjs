// Repackage the pinned upstream image with the same expiry fix as session.ts,
// without rebuilding or updating unrelated application code during recovery.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const pattern = /maxAge:\s*([^,\n]+),\s*expires:\s*new Date\(Date\.now\(\)\s*\+\s*([^)]*)\),\s*path:\s*(['"])\/\3/;
const candidates = [];
function walk(root) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const file = path.join(root, entry.name);
    if (entry.isDirectory()) walk(file);
    else if (file.endsWith('.js')) {
      const text = fs.readFileSync(file, 'utf8');
      const match = pattern.exec(text);
      if (match) candidates.push({ file, text, match });
    }
  }
}
walk('/zipline/build');
assert.equal(candidates.length, 1, 'Expected exactly one upstream session cookie block');
const { file, text, match } = candidates[0];
for (const expression of [match[1], match[2]]) {
  assert.match(expression, /^[\d.eE+* ()-]+$/, 'Unexpected cookie lifetime expression');
}
assert.equal(vm.runInNewContext(match[1]), 14 * 86400);
assert.equal(vm.runInNewContext(match[2]), 14 * 86400 * 1000);
const fixed = text.replace(pattern, `maxAge: ${match[1]}, path: ${match[3]}/${match[3]}`);
assert.notEqual(fixed, text);
fs.writeFileSync(file, fixed);
console.log(`Removed the fixed session cookie expiry in ${file}`);
