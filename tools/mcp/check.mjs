import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile, mkdtemp } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, join, resolve, sep } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';

const directory = dirname(fileURLToPath(import.meta.url));
const artifacts = join(directory, '.cache', 'checks');
await mkdir(artifacts, { recursive: true });
const workdir = await mkdtemp(join(artifacts, 'run-'));
const results = [];
const textContent = result => (result.content ?? []).filter(c => c.type === 'text').map(c => c.text).join('\n');
const parameters = { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'ratatouille-tooling-check', version: '0.1.0' } };

function stdio(server, env = {}) {
  const child = spawn('bash', [join(directory, 'run.sh'), server], { env: { ...process.env, ...env }, cwd: directory, stdio: ['pipe', 'pipe', 'pipe'] });
  const pending = new Map();
  let nextId = 0;
  let stderr = '';
  child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-4000); });
  const lines = createInterface({ input: child.stdout });
  lines.on('line', line => {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    clearTimeout(request.timer);
    if (message.error) request.reject(new Error(message.error.message));
    else request.resolve(message.result);
  });
  child.on('exit', code => {
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(new Error(`${server} exited ${code}: ${stderr}`));
    }
    pending.clear();
  });
  function request(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${server}: timeout ${method}: ${stderr}`)); }, 45000);
      pending.set(id, { resolve, reject, timer });
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    });
  }
  return {
    request,
    async initialize() {
      const response = await request('initialize', parameters);
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
      return response;
    },
    close() { lines.close(); child.stdin.end(); child.kill('SIGTERM'); },
  };
}

function httpMcp(url, headers = {}) {
  let nextId = 0;
  let session;
  async function request(method, params = {}) {
    const id = ++nextId;
    const response = await fetch(url, {
      method: 'POST', headers: {
        'Content-Type': 'application/json', Accept: 'application/json, text/event-stream',
        'MCP-Protocol-Version': parameters.protocolVersion,
        ...(session ? { 'Mcp-Session-Id': session } : {}), ...headers,
      },
      body: JSON.stringify({ jsonrpc: '2.0', id, method, params }), signal: AbortSignal.timeout(45000),
    });
    session = response.headers.get('mcp-session-id') ?? session;
    const body = await response.text();
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${body.slice(0, 500)}`);
    let message;
    if (response.headers.get('content-type')?.includes('text/event-stream')) {
      for (const line of body.split('\n')) {
        if (!line.startsWith('data:')) continue;
        const item = JSON.parse(line.slice(5));
        if (item.id === id) message = item;
      }
    } else message = JSON.parse(body);
    if (!message) throw new Error('MCP response missing');
    if (message.error) throw new Error(message.error.message);
    return message.result;
  }
  return { request, initialize: () => request('initialize', parameters) };
}

async function scenario(name, run) {
  try {
    const evidence = await run();
    results.push({ name, status: 'passed', evidence });
    console.log(`${name}: PASS`);
  } catch (error) {
    // Never persist Authorization headers or token values in test reports.
    let message = String(error.message);
    if (process.env.GITHUB_MCP_TOKEN) message = message.split(process.env.GITHUB_MCP_TOKEN).join('[REDACTED]');
    results.push({ name, status: 'failed', error: message });
    console.log(`${name}: FAIL ${message}`);
  }
}

async function sqlite() {
  const path = join(workdir, 'fixture.sqlite');
  const database = new DatabaseSync(path);
  database.exec('CREATE TABLE sample (id INTEGER PRIMARY KEY, quantity INTEGER); INSERT INTO sample VALUES (1, 2), (2, 5);');
  database.close();
  const fingerprint = () => readFile(path).then(bytes => createHash('sha256').update(bytes).digest('hex'));
  const before = await fingerprint();
  const client = stdio('sqlite', { RATATOUILLE_SQLITE_DSN: `sqlite://${path}` });
  try {
    await client.initialize();
    const { tools } = await client.request('tools/list');
    const queryTool = tools.find(t => t.name.startsWith('execute_sql'));
    const schemaTool = tools.find(t => t.name.startsWith('search_objects'));
    assert(queryTool && schemaTool, 'Expected SQL and schema tools');
    const query = sql => client.request('tools/call', { name: queryTool.name, arguments: { sql } });
    const total = await query('SELECT SUM(quantity) AS total FROM sample');
    assert(!total.isError, textContent(total));
    const data = JSON.parse(textContent(total));
    assert.equal(data.data.statements[0].rows[0].total, 7, 'Expected total 7');
    const schemaArgs = { object_type: 'table', pattern: 'sample', detail_level: 'names' };
    const schema = await client.request('tools/call', { name: schemaTool.name, arguments: schemaArgs });
    assert(!schema.isError && /sample/.test(textContent(schema)), 'Schema inspection failed');
    const rejected = [];
    for (const sql of [
      'INSERT INTO sample VALUES (3, 9)', 'UPDATE sample SET quantity = 99',
      'DELETE FROM sample', 'CREATE TABLE injected (id INTEGER)', 'DROP TABLE sample',
      'PRAGMA user_version = 99', 'PRAGMA writable_schema = ON',
      'PRAGMA journal_mode = OFF', 'PRAGMA query_only = OFF',
    ]) {
      const result = await query(sql);
      assert(result.isError, `Write was accepted: ${sql}`);
      assert.equal(JSON.parse(textContent(result)).code, 'READONLY_VIOLATION', `Expected read-only policy rejection: ${sql}`);
      assert.equal(await fingerprint(), before, `Database changed: ${sql}`);
      rejected.push(sql);
    }
    return { tools: tools.map(t => t.name), total: 7, rejected, unchanged: await fingerprint() === before };
  } finally { client.close(); }
}

async function playwright() {
  const server = createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end('<!doctype html><html lang="en"><title>MCP check</title><label>Name <input aria-label="Name"></label><button onclick="document.getElementById(\'result\').textContent=\'Saved: \'+document.querySelector(\'input\').value">Save</button><p id="result" aria-live="polite"></p></html>');
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const client = stdio('playwright');
  try {
    await client.initialize();
    const { tools } = await client.request('tools/list');
    const call = async (name, args) => {
      const result = await client.request('tools/call', { name, arguments: args });
      assert(!result.isError, textContent(result));
      const text = textContent(result);
      const snapshot = text.match(/\[Snapshot\]\(([^)]+)\)/)?.[1];
      if (!snapshot) return text;
      const path = resolve(directory, snapshot);
      assert(path.startsWith(join(directory, '.cache', 'playwright') + sep), 'Snapshot outside the MCP output directory');
      return text + '\n' + await readFile(path, 'utf8');
    };
    const page = await call('browser_navigate', { url: `http://127.0.0.1:${server.address().port}` });
    const input = page.match(/textbox "Name".*?\[ref=([^\]]+)\]/)?.[1];
    assert(input, `Input not found in accessibility snapshot: ${page}`);
    const typed = await call('browser_type', { target: input, text: 'Ratatouille MCP' });
    const button = typed.match(/button "Save".*?\[ref=([^\]]+)\]/)?.[1] ?? page.match(/button "Save".*?\[ref=([^\]]+)\]/)?.[1];
    assert(button, 'Button not found');
    const clicked = await call('browser_click', { target: button });
    assert(clicked.includes('Saved: Ratatouille MCP'), 'Form result missing');
    await call('browser_take_screenshot', { type: 'png', scale: 'css', filename: join(workdir, 'playwright.png') });
    await call('browser_close', {});
    return { tools: tools.map(t => t.name), input: 'Ratatouille MCP', result: 'Saved: Ratatouille MCP', screenshot: join(workdir, 'playwright.png') };
  } finally { client.close(); await new Promise(resolve => server.close(resolve)); }
}

async function context7() {
  const client = httpMcp('https://mcp.context7.com/mcp');
  await client.initialize();
  const { tools } = await client.request('tools/list');
  const resolved = await client.request('tools/call', { name: 'resolve-library-id', arguments: { libraryName: 'Python', query: 'Python sqlite3 read-only database URI mode=ro official documentation' } });
  assert(!resolved.isError, textContent(resolved));
  const libraryId = textContent(resolved).match(/(?:Library ID|libraryId)[:\s]+([^\s]+)/i)?.[1];
  assert(libraryId?.startsWith('/'), 'Library ID not found');
  const docs = await client.request('tools/call', { name: 'query-docs', arguments: { libraryId, query: 'sqlite3 connect URI mode=ro read-only database' } });
  assert(!docs.isError && textContent(docs).length > 50, 'Documentation retrieval failed');
  return { tools: tools.map(t => t.name), libraryId, documentationReceived: true };
}

async function github() {
  const client = httpMcp('https://api.githubcopilot.com/mcp/', {
    Authorization: `Bearer ${process.env.GITHUB_MCP_TOKEN}`,
    'X-MCP-Toolsets': 'context,repos,issues,pull_requests,actions',
  });
  await client.initialize();
  const { tools } = await client.request('tools/list');
  assert(tools.some(t => t.name === 'get_me'), 'get_me unavailable');
  const account = await client.request('tools/call', { name: 'get_me', arguments: {} });
  assert(!account.isError, 'GitHub account check failed');
  return { accountVerified: true, tools: tools.map(t => t.name), repositoryAccess: 'deferred to T03' };
}

if (process.argv[2] === 'remote') {
  await scenario('context7', context7);
  if (process.env.GITHUB_MCP_TOKEN) await scenario('github', github);
  else { results.push({ name: 'github', status: 'pending', reason: 'GITHUB_MCP_TOKEN not set' }); console.log('github: PENDING GITHUB_MCP_TOKEN'); }
} else {
  await scenario('sqlite', sqlite);
  await scenario('playwright', playwright);
}
await writeFile(join(workdir, 'results.json'), JSON.stringify({ checkedAt: new Date().toISOString(), results }, null, 2) + '\n');
console.log(`Report: ${join(workdir, 'results.json')}`);
process.exitCode = results.some(r => r.status === 'failed') ? 1 : 0;
