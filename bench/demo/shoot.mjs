// Screenshots of the RoamIndex UI for the README, driven over the Chrome DevTools Protocol (Node 20+, global WebSocket).
// Needs: `roamengine serve --library <travel demo lib> --port 8766 --token demo` (build_travel_demo.py), the UI on 127.0.0.1:1420
// (`npx vite preview --port 1420`), and Edge/Chrome started with --remote-debugging-port=9226 on that URL.
// usage: node bench/demo/shoot.mjs OUT_DIR
import fs from "node:fs";
const out = process.argv[2] ?? "docs/screenshots";
fs.mkdirSync(out, { recursive: true });
const list = await (await fetch("http://127.0.0.1:9226/json")).json();
const ws = new WebSocket(list.find((t) => t.type === "page" && t.url.includes(":1420")).webSocketDebuggerUrl);
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (pending.has(d.id)) { pending.get(d.id)(d.result ?? d.error); pending.delete(d.id); } };
await new Promise((r) => (ws.onopen = r));
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.value;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const W = 1440, H = 900;
await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
await send("Page.reload", { ignoreCache: true });                                   // pick up a fresh build
await wait(2500);

async function nav(label) {
  await js(`[...document.querySelectorAll("nav button")].find(b => b.textContent.trim() === ${JSON.stringify(label)})?.click()`);
  await wait(1500);
}
async function type(selector, text) {
  await js(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); el.focus(); el.select?.(); })()`);
  await send("Input.insertText", { text });
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
}
async function shot(name) {
  await wait(800);
  const s = await send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(`${out}/${name}.png`, Buffer.from(s.data, "base64"));
  console.log("saved", name);
}
async function waitFor(expr, ms = 15000) {
  const t = Date.now();
  while (Date.now() - t < ms) { if (await js(expr)) return true; await wait(300); }
  console.log("timeout waiting for", expr); return false;
}

const steps = (process.env.SHOTS ?? "home,home-dark,search,video,timeline,map,places,welcome,indexing").split(",");
await waitFor(`document.querySelector("nav button") !== null`);
await js(`localStorage.clear()`);                                                   // no recent searches or theme choice
for (const dark of [false, true]) {
  if (!steps.includes(dark ? "home-dark" : "home")) continue;
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: dark ? "dark" : "light" }] });
  await send("Page.reload", { ignoreCache: true });
  await waitFor(`document.querySelectorAll(".postcard img").length > 3 && document.querySelector(".worldstrip") !== null`);
  await wait(3500);                                                                 // count-up and postcard entrance
  await js(`document.activeElement?.blur()`);
  await shot(dark ? "home-dark" : "home");
}
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
if (steps.includes("search")) {
  await nav("Search");
  await type('input[placeholder^="Describe"]', process.env.Q_SEARCH ?? "waterfall");
  await waitFor(`document.querySelectorAll(".tile img, .grid img").length > 3`);
  await wait(2000);
  await shot("search");
}
if (steps.includes("video")) {
  await nav("Search");
  await type('input[placeholder^="Describe"]', process.env.Q_VIDEO ?? "tram in a street type:video");
  await waitFor(`document.querySelectorAll(".tile img, .grid img").length > 0`);
  await wait(1500);
  await js(`(document.querySelector(".tile") || document.querySelector(".grid img"))?.click()`);
  await waitFor(`(() => { const v = document.querySelector("video"); return v && v.readyState >= 2; })()`);
  // hold the frame at the best moment (the viewer starts playback there)
  await js(`(() => { const v = document.querySelector("video"); v.pause(); const m = document.querySelector(".moments button, .moment");
            if (m) m.click(); setTimeout(() => v.pause(), 300); })()`);
  await wait(1500);
  await shot("video");
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await wait(500);
}
if (steps.includes("timeline")) { await nav("Timeline"); await wait(1500); await shot("timeline"); }
if (steps.includes("map")) {
  // zoomed out the app shows a photo on map bubbles of 100+ photos and a number from 21 (zoomed into a region, a photo
  // from 21), a dot below; every place in the small CC0
  // demo library has a handful of photos, so this screenshot lowers the thresholds (?pinmin / ?numbermin, preview only)
  const u = new URL(await js("location.href")); u.searchParams.set("view", "map"); u.searchParams.set("pinmin", "5"); u.searchParams.set("numbermin", "2");
  await send("Page.navigate", { url: u.toString() }); await wait(4000); await shot("map");
  u.searchParams.delete("pinmin"); u.searchParams.delete("numbermin"); u.searchParams.delete("view");
  await send("Page.navigate", { url: u.toString() }); await wait(2500);
}
if (steps.includes("places")) {
  await nav("Add places");
  await wait(2500);
  await js(`document.querySelector(".tilehit")?.click()`);              // select the first photo
  await js(`document.querySelector('input[placeholder^="Type a town"]').focus()`);
  await send("Input.insertText", { text: process.env.Q_PLACE ?? "Berlin" });
  await wait(1500);                                                                    // offline gazetteer results
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await wait(2500);                                                                    // globe flies to the pin
  await shot("places");
}
const base = new URL((await js("location.href")));
for (const k of ["view", "q", "onboarding", "ingestdemo"]) base.searchParams.delete(k);
if (steps.includes("welcome")) {                                        // first run: full-screen onboarding
  const u = new URL(base); u.searchParams.set("onboarding", "1");
  await send("Page.navigate", { url: u.toString() });
  await waitFor(`document.querySelector(".onboard .spinglobe") !== null`);
  await wait(2400);                                                    // the plane part-way along its first route
  await shot("welcome");
}
if (steps.includes("indexing")) {                                       // first indexing run: slideshow with sample progress
  const u = new URL(base);
  u.searchParams.set("onboarding", "1"); u.searchParams.set("ingestdemo", "1");
  await send("Page.navigate", { url: u.toString() });
  await waitFor(`document.querySelectorAll(".ingestshow .photo").length > 0`);
  await wait(9300);                                                    // mid-way through a later slide
  await shot("indexing");
}
ws.close();
