// Record a short scene of the app for a README GIF: Chrome DevTools screencast frames of one page area.
// Needs the same setup as shoot.mjs (demo engine, UI on 127.0.0.1:1420, Edge/Chrome with --remote-debugging-port=9226).
// usage: node bench/demo/record.mjs SCENE OUT_DIR       SCENE = memories | veil | replay | indexing
// writes OUT_DIR/<scene>/frame-*.jpg and meta.json (frame times, crop box); make_gif.py turns that into a GIF.
import fs from "node:fs";
import path from "node:path";

const [scene, outRoot] = process.argv.slice(2);
const base = process.env.BASE ?? "http://127.0.0.1:1420/?port=8766&token=demo";
const SCENES = {
  // a fresh home screen: the polaroids shoot travel scenes, then flash and develop into the real group photos
  memories: { url: base, area: ".memsection", seconds: 6.5, scroll: true },
  // the "Getting everyone in the picture" veil (held on with the preview flag; in the app it shows while covers improve)
  veil: { url: base + "&veil=1", area: ".memsection", seconds: 4.5, scroll: true, wait: 4500 },
  // Replay my travels: the globe flies trip to trip and a postcard lands at each stop
  replay: { url: base + "&view=map&pinmin=5&numbermin=2", area: ".mapwrap", seconds: 40, wait: 4000,
    act: `[...document.querySelectorAll("button")].find(b => b.textContent.includes("Replay"))?.click()` },
  // the first-run indexing slideshow with sample progress
  indexing: { url: base + "&onboarding=1&ingestdemo=1", area: ".ingestshow", seconds: 10, wait: 2500 },
};
const sc = SCENES[scene];
const out = path.join(outRoot, scene);
fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(out, { recursive: true });

const list = await (await fetch("http://127.0.0.1:9226/json")).json();
const ws = new WebSocket(list.find((t) => t.type === "page").webSocketDebuggerUrl);
let id = 0; const pending = new Map(); const frames = []; let recording = false, t0 = 0;
ws.onmessage = (m) => {
  const d = JSON.parse(m.data);
  if (pending.has(d.id)) { pending.get(d.id)(d.result); pending.delete(d.id); }
  if (d.method === "Page.screencastFrame") {
    ws.send(JSON.stringify({ id: ++id, method: "Page.screencastFrameAck", params: { sessionId: d.params.sessionId } }));
    if (recording) {
      const f = path.join(out, `frame-${String(frames.length).padStart(4, "0")}.jpg`);
      fs.writeFileSync(f, Buffer.from(d.params.data, "base64"));
      frames.push({ file: path.basename(f), t: (Date.now() - t0) / 1000 });
    }
  }
};
await new Promise((r) => (ws.onopen = r));
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async (e) => (await send("Runtime.evaluate", { expression: e, returnByValue: true }))?.result?.value;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
await send("Page.startScreencast", { format: "jpeg", quality: 85, everyNthFrame: 1 });
await send("Page.navigate", { url: sc.url });
for (let i = 0; i < 300 && !(await js(`!!document.querySelector(${JSON.stringify(sc.area)})`)); i++) await wait(20);
if (sc.scroll) {                                        // only this row on screen: nothing above it may load and push it down
  await js(`(() => { const c = document.createElement("style"); c.textContent = ".hero,.worldstrip,.sectionhead{display:none!important}"; document.head.append(c); })()`);
  await wait(120);
}
if (sc.wait) await wait(sc.wait);
const box = await js(`(() => { const b = document.querySelector(${JSON.stringify(sc.area)}).getBoundingClientRect(); return [b.x, b.y, b.width, Math.min(b.height, 900 - b.y)]; })()`);
recording = true; t0 = Date.now();
if (sc.act) await js(sc.act);
await wait(sc.seconds * 1000);
recording = false;
await send("Page.stopScreencast");
fs.writeFileSync(path.join(out, "meta.json"), JSON.stringify({ scene, box, frames }, null, 1));
console.log(scene, frames.length, "frames over", sc.seconds, "s, area", box.map(Math.round));
ws.close();
