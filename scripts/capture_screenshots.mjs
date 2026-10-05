// Screenshots of the nine screens for the report appendix (Table 4.3), from headless Chrome or Edge.
//
//   1. start the app:        .\.venv\Scripts\python.exe -m flask --app app run
//   2. capture:              node scripts/capture_screenshots.mjs [output-folder]
//
// Signs in as the seeded Administrator (the demo login in the README) so every control is visible, renders in the
// light colour scheme at 1440 px wide and 2x pixel density, and waits for each screen's data and charts first.
// Needs Node 22 (built-in WebSocket and fetch) and an installed Chrome or Edge. No packages are downloaded.
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const BASE = process.env.BASE_URL || 'http://localhost:5000';
const OUT = process.argv[2] || 'report/screenshots';
const EMAIL = process.env.SHOT_EMAIL || 'admin@fleet.local';
const PASSWORD = process.env.SHOT_PASSWORD || 'Admin@123';
const PORT = 9223;
const WIDTH = 1440;
const MAX_HEIGHT = 2600;

const BROWSERS = [
  process.env.BROWSER,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].filter(Boolean);

const charts = (n) => `document.querySelectorAll('.chart .main-svg').length >= ${n}`;
const rows = (sel) => `!!document.querySelector('${sel} tbody tr')`;
const SCREENS = [
  ['01_login', '/login', 'true', 700, false],
  ['02_dashboard', '/dashboard', charts(4), MAX_HEIGHT, true],
  ['03_vehicles', '/vehicles', rows('#list'), 900, true],
  ['04_drivers', '/drivers', `${rows('#list')} && ${rows('#a-list')}`, 1500, true],
  ['05_trips', '/trips', `${rows('#list')} && document.querySelectorAll('#t-vehicle_id option').length > 1`, 1300, true],
  ['06_fuel', '/fuel', `${charts(2)} && ${rows('#list')}`, 1900, true],
  ['07_maintenance', '/maintenance', `${rows('#list')} && ${rows('#scheduled')}`, 1900, true],
  ['08_analytics', '/analytics', `${charts(4)} && ${rows('#by-type')}`, MAX_HEIGHT, true],
  ['09_predictive_maintenance', '/predict', `${charts(1)} && ${rows('#list')}`, 1500, true],
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
  constructor(socket) {
    this.socket = socket; this.id = 0; this.pending = new Map();
    socket.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(`${msg.error.message}`)) : resolve(msg.result);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  async evaluate(expression) {
    const r = await this.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' ' + (r.exceptionDetails.exception?.description || ''));
    return r.result.value;
  }
  async waitFor(expression, what, timeout = 20000) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      try { if (await this.evaluate(`document.readyState === 'complete' && (${expression})`)) return; } catch { /* page is navigating */ }
      await sleep(250);
    }
    throw new Error(`timed out waiting for ${what}`);
  }
}

async function main() {
  const exe = BROWSERS.find((p) => existsSync(p));
  if (!exe) throw new Error('No Chrome or Edge found; set BROWSER to its path');
  const profile = mkdtempSync(path.join(tmpdir(), 'fleet-shots-'));
  const browser = spawn(exe, ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
  try {
    let targets;
    for (let i = 0; i < 60; i++) {
      try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if (targets.some((t) => t.type === 'page')) break; } catch { /* starting */ }
      await sleep(250);
    }
    const page = targets?.find((t) => t.type === 'page');
    if (!page) throw new Error('the browser did not start');
    const socket = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve); socket.addEventListener('error', reject); });
    const cdp = new Cdp(socket);
    await cdp.send('Page.enable');
    await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });
    mkdirSync(OUT, { recursive: true });

    const capture = async (name, height) => {
      await cdp.send('Emulation.setDeviceMetricsOverride', { width: WIDTH, height, deviceScaleFactor: 2, mobile: false });
      await sleep(900);   // charts redraw when the viewport changes
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
      writeFileSync(path.join(OUT, `${name}.png`), Buffer.from(data, 'base64'));
    };

    let signedIn = false;
    for (const [name, route, ready, maxHeight, needsLogin] of SCREENS) {
      if (needsLogin && !signedIn) {
        await cdp.send('Page.navigate', { url: `${BASE}/login` });
        await cdp.waitFor('true', 'the login page');
        const status = await cdp.evaluate(`fetch('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({email: ${JSON.stringify(EMAIL)}, password: ${JSON.stringify(PASSWORD)}})}).then(r => r.status)`);
        if (status !== 200) throw new Error(`sign-in failed with status ${status}`);
        signedIn = true;
      }
      await cdp.send('Emulation.setDeviceMetricsOverride', { width: WIDTH, height: 900, deviceScaleFactor: 2, mobile: false });
      await cdp.send('Page.navigate', { url: `${BASE}${route}` });
      await cdp.waitFor(ready, `${route} to load`);
      await sleep(700);
      const content = await cdp.evaluate('Math.ceil(document.documentElement.scrollHeight)');
      const height = needsLogin ? Math.min(Math.max(content, 700), maxHeight) : maxHeight;
      await capture(name, height);
      console.log(`${name}.png  ${WIDTH}x${height} @2x`);
    }
    socket.close();
  } finally {
    browser.kill();
    await sleep(500);
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* the browser may still hold a file */ }
  }
}

main().catch((e) => { console.error(e.message); process.exit(1); });
