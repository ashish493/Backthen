//! RoamIndex desktop shell.
//!
//! Starts the RoamEngine local API (`python -m roamengine serve`) as a child process, reads its one-line
//! `{"event":"ready","port":..,"token":..}` handshake, and hands port + token to the UI. The UI talks to the engine
//! over 127.0.0.1 only; it never imports engine internals (architecture doc, stage 7).
//! The engine exits when this process does: it watches its stdin, which we hold open.
//!
//! Python: an empty `python` in the config means the managed runtime. On first launch (or after an update) the
//! installer's `resources/runtime` (uv, wheels, pinned requirements; see tools/prepare_runtime.py) sets up Python,
//! PyTorch (CUDA when an NVIDIA GPU is present) and the engine in the app's local data folder, once.

use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Manager, State};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Clone, Serialize, Deserialize, Default)]
pub struct AppConfig {
    pub python: String, // empty: the managed runtime
    pub library: String,
}

#[derive(Clone, Serialize, Default)]
pub struct EngineInfo {
    pub status: String, // setup | starting | ready | failed | stopped
    pub port: Option<u16>,
    pub token: Option<String>,
    pub library: String,
    pub python: String,
    pub error: Option<String>,
    pub log: Option<String>,
    pub setup_step: Option<u32>,
    pub setup_total: Option<u32>,
    pub setup_label: Option<String>,
}

struct Inner {
    info: EngineInfo,
    child: Option<Child>,
    generation: u64,
}

#[derive(Clone)]
pub struct EngineState(Arc<Mutex<Inner>>);

/// Development defaults (debug builds only, so no local path is compiled into a release): the repository's venv and
/// the index already built in it, if there is one.
#[cfg(debug_assertions)]
fn default_config(app: &AppHandle) -> AppConfig {
    let mut repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    for _ in 0..3 {
        repo.pop();
    }
    let python = std::env::var("ROAM_PYTHON").unwrap_or_else(|_| {
        let p = repo.join(".venv").join("Scripts").join("python.exe");
        if p.exists() { p.to_string_lossy().into_owned() } else { String::new() }
    });
    let library = std::env::var("ROAM_LIBRARY").unwrap_or_else(|_| {
        let existing = repo.join("out").join("archive355");
        if existing.join("manifest.sqlite").exists() {
            return existing.to_string_lossy().into_owned();
        }
        default_library(app)
    });
    AppConfig { python, library }
}

#[cfg(not(debug_assertions))]
fn default_config(app: &AppHandle) -> AppConfig {
    AppConfig {
        python: std::env::var("ROAM_PYTHON").unwrap_or_default(),
        library: std::env::var("ROAM_LIBRARY").unwrap_or_else(|_| default_library(app)),
    }
}

fn default_library(app: &AppHandle) -> String {
    app.path()
        .app_data_dir()
        .map(|d| d.join("library"))
        .unwrap_or_else(|_| PathBuf::from("library"))
        .to_string_lossy()
        .into_owned()
}

fn config_path(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("config.json"))
}

fn load_config(app: &AppHandle) -> AppConfig {
    if let Some(p) = config_path(app) {
        if let Ok(s) = std::fs::read_to_string(&p) {
            if let Ok(c) = serde_json::from_str::<AppConfig>(&s) {
                if !c.library.is_empty() {
                    return c;
                }
            }
        }
    }
    default_config(app)
}

fn save_config(app: &AppHandle, cfg: &AppConfig) -> Result<(), String> {
    let p = config_path(app).ok_or("no config directory")?;
    if let Some(d) = p.parent() {
        std::fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    std::fs::write(&p, serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}

fn log_path(app: &AppHandle, name: &str) -> Option<PathBuf> {
    let d = app.path().app_log_dir().ok()?;
    std::fs::create_dir_all(&d).ok()?;
    Some(d.join(name))
}

fn stop_child(g: &mut Inner) {
    if let Some(mut c) = g.child.take() {
        let _ = c.kill();
        let _ = c.wait();
    }
}

// ---------------------------------------------------------------- managed runtime (first-launch setup)

struct Runtime {
    res: PathBuf, // bundled: uv.exe, wheels/, requirements.txt, torch.json
    dir: PathBuf, // installed: python/, venv/, cache/, ready.json
}

impl Runtime {
    fn new(app: &AppHandle) -> Result<Self, String> {
        let res = app.path().resource_dir().map_err(|e| e.to_string())?.join("resources").join("runtime");
        let dir = app.path().app_local_data_dir().map_err(|e| e.to_string())?.join("runtime");
        Ok(Runtime { res, dir })
    }
    fn python(&self) -> PathBuf {
        self.dir.join("venv").join("Scripts").join("python.exe")
    }
    fn marker(&self) -> PathBuf {
        self.dir.join("ready.json")
    }
    /// Set up for this app version already?
    fn ready(&self, version: &str) -> bool {
        std::fs::read_to_string(self.marker())
            .ok()
            .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
            .map(|v| v.get("version").and_then(|x| x.as_str()) == Some(version) && self.python().exists())
            .unwrap_or(false)
    }
    fn uv(&self, log: &mut Option<std::fs::File>, args: &[&str]) -> Result<(), String> {
        let uv = self.res.join("uv.exe");
        if let Some(f) = log.as_mut() {
            let _ = writeln!(f, "\n$ uv {}", args.join(" "));
        }
        let mut cmd = Command::new(&uv);
        cmd.args(args)
            .env("UV_PYTHON_INSTALL_DIR", self.dir.join("python"))
            .env("UV_CACHE_DIR", self.dir.join("cache"))
            .env("UV_PYTHON_PREFERENCE", "only-managed")
            .env("UV_NO_PROGRESS", "1")
            .env("UV_LINK_MODE", "copy")
            .stdin(Stdio::null());
        match log.as_ref().and_then(|f| f.try_clone().ok().zip(f.try_clone().ok())) {
            Some((a, b)) => cmd.stdout(Stdio::from(a)).stderr(Stdio::from(b)),
            None => cmd.stdout(Stdio::null()).stderr(Stdio::null()),
        };
        #[cfg(windows)]
        cmd.creation_flags(CREATE_NO_WINDOW);
        let st = cmd.status().map_err(|e| format!("could not run the setup tool ({}): {e}", uv.display()))?;
        if st.success() { Ok(()) } else { Err(format!("`uv {}` failed ({st})", args.first().unwrap_or(&""))) }
    }
}

fn has_nvidia_gpu() -> bool {
    let mut cmd = Command::new("nvidia-smi");
    cmd.arg("-L").stdin(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.output().map(|o| o.status.success() && String::from_utf8_lossy(&o.stdout).contains("GPU")).unwrap_or(false)
}

fn path_str(p: &Path) -> String {
    p.to_string_lossy().into_owned()
}

/// The one-time setup. `step(k, label)` reports progress; returns the venv's python on success.
fn setup_runtime(rt: &Runtime, version: &str, log: Option<PathBuf>, step: &dyn Fn(u32, &str)) -> Result<PathBuf, String> {
    std::fs::create_dir_all(&rt.dir).map_err(|e| format!("cannot create {}: {e}", rt.dir.display()))?;
    let _ = std::fs::remove_file(rt.marker());
    let mut lf = log.and_then(|p| std::fs::File::create(p).ok());
    let torch: serde_json::Value = std::fs::read_to_string(rt.res.join("torch.json"))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .ok_or("the installer's runtime files are missing; please reinstall Backthen")?;
    let s = |k: &str| torch.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
    let gpu = has_nvidia_gpu();
    let venv = rt.dir.join("venv");
    let py = rt.python();
    let py_s = path_str(&py);

    step(1, "Downloading Python");
    rt.uv(&mut lf, &["python", "install", &s("python")])?;
    step(2, "Preparing a private Python environment");
    rt.uv(&mut lf, &["venv", "--clear", "--python", &s("python"), &path_str(&venv)])?;
    step(3, if gpu { "Downloading PyTorch with NVIDIA GPU support (about 2.5 GB)" } else { "Downloading PyTorch (about 200 MB)" });
    let index = if gpu { s("cuda_index") } else { s("cpu_index") };
    let tv = format!("torch=={}", s("torch"));
    let vv = format!("torchvision=={}", s("torchvision"));
    rt.uv(&mut lf, &["pip", "install", "--python", &py_s, &tv, &vv, "--index-url", "https://pypi.org/simple",
                     "--extra-index-url", &index, "--index-strategy", "first-index"])?;
    step(4, "Installing Backthen's search engine");
    let wheels: Vec<String> = std::fs::read_dir(rt.res.join("wheels"))
        .map_err(|e| e.to_string())?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().map(|x| x == "whl").unwrap_or(false))
        .map(|p| path_str(&p))
        .collect();
    let req = path_str(&rt.res.join("requirements.txt"));
    let mut args: Vec<&str> = vec!["pip", "install", "--python", &py_s, "-r", &req];
    args.extend(wheels.iter().map(|w| w.as_str()));
    rt.uv(&mut lf, &args)?;
    std::fs::write(rt.marker(), serde_json::json!({ "version": version, "gpu": gpu }).to_string())
        .map_err(|e| e.to_string())?;
    let _ = std::fs::remove_dir_all(rt.dir.join("cache")); // downloads only (installed by copy): ~2.5 GB with CUDA
    Ok(py)
}

// ---------------------------------------------------------------- engine process

/// Start the engine with this config: runs the one-time setup first when the managed runtime is not ready.
fn start(app: &AppHandle, state: &EngineState, cfg: AppConfig) {
    if !cfg.python.is_empty() {
        spawn_engine(state, &cfg, log_path(app, "engine.log"));
        return;
    }
    let version = app.package_info().version.to_string();
    let rt = match Runtime::new(app) {
        Ok(r) => r,
        Err(e) => return fail(state, cfg, format!("no folder for the engine runtime: {e}")),
    };
    if rt.ready(&version) {
        let cfg = AppConfig { python: path_str(&rt.python()), library: cfg.library };
        spawn_engine(state, &cfg, log_path(app, "engine.log"));
        return;
    }
    let gen = {
        let mut g = state.0.lock().unwrap();
        stop_child(&mut g);
        g.generation += 1;
        let setup_log = log_path(app, "setup.log");
        g.info = EngineInfo {
            status: "setup".into(),
            library: cfg.library.clone(),
            log: setup_log.as_ref().map(|p| path_str(p)),
            setup_step: Some(0),
            setup_total: Some(4),
            setup_label: Some("Getting ready".into()),
            ..Default::default()
        };
        g.generation
    };
    let (st, app2) = (state.clone(), app.clone());
    std::thread::spawn(move || {
        let report = |k: u32, label: &str| {
            let mut g = st.0.lock().unwrap();
            if g.generation == gen {
                g.info.setup_step = Some(k);
                g.info.setup_label = Some(label.into());
            }
        };
        let res = setup_runtime(&rt, &version, log_path(&app2, "setup.log"), &report);
        if st.0.lock().unwrap().generation != gen {
            return; // restarted meanwhile
        }
        match res {
            Ok(py) => spawn_engine(&st, &AppConfig { python: path_str(&py), library: cfg.library }, log_path(&app2, "engine.log")),
            Err(e) => {
                let mut g = st.0.lock().unwrap();
                g.info.status = "failed".into();
                g.info.error = Some(format!("First-time setup did not finish: {e}. Check the internet connection and try again."));
            }
        }
    });
}

fn fail(state: &EngineState, cfg: AppConfig, error: String) {
    let mut g = state.0.lock().unwrap();
    stop_child(&mut g);
    g.generation += 1;
    g.info = EngineInfo { status: "failed".into(), library: cfg.library, error: Some(error), ..Default::default() };
}

fn spawn_engine(state: &EngineState, cfg: &AppConfig, log: Option<PathBuf>) {
    let mut g = state.0.lock().unwrap();
    stop_child(&mut g);
    g.generation += 1;
    let gen = g.generation;
    g.info = EngineInfo {
        status: "starting".into(),
        library: cfg.library.clone(),
        python: cfg.python.clone(),
        log: log.as_ref().map(|p| path_str(p)),
        ..Default::default()
    };
    if let Err(e) = std::fs::create_dir_all(&cfg.library) {
        g.info.status = "failed".into();
        g.info.error = Some(format!("cannot create the library folder: {e}"));
        return;
    }
    let stderr = log
        .as_ref()
        .and_then(|p| std::fs::File::create(p).ok())
        .map(Stdio::from)
        .unwrap_or_else(Stdio::null);
    let mut cmd = Command::new(&cfg.python);
    cmd.args(["-m", "roamengine", "serve", "--library", &cfg.library, "--exit-with-parent"])
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(stderr);
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            g.info.status = "failed".into();
            g.info.error = Some(format!("could not start Python ({}): {e}", cfg.python));
            return;
        }
    };
    let stdout = child.stdout.take();
    g.child = Some(child);
    drop(g);

    let st = state.clone();
    std::thread::spawn(move || {
        if let Some(out) = stdout {
            for line in BufReader::new(out).lines() {
                let Ok(line) = line else { break };
                let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
                if v.get("event").and_then(|e| e.as_str()) == Some("ready") {
                    let mut g = st.0.lock().unwrap();
                    if g.generation == gen {
                        g.info.status = "ready".into();
                        g.info.port = v.get("port").and_then(|p| p.as_u64()).map(|p| p as u16);
                        g.info.token = v.get("token").and_then(|t| t.as_str()).map(String::from);
                    }
                }
            }
        }
        // stdout closed: the engine exited
        let mut g = st.0.lock().unwrap();
        if g.generation == gen && g.info.status != "stopped" {
            let code = g
                .child
                .as_mut()
                .and_then(|c| c.wait().ok())
                .map(|s| s.to_string())
                .unwrap_or_else(|| "unknown status".into());
            g.info.status = "failed".into();
            g.info.port = None;
            g.info.error = Some(format!("the engine exited ({code}); details are in the engine log"));
        }
    });
}

#[tauri::command]
fn engine_info(state: State<'_, EngineState>) -> EngineInfo {
    state.0.lock().unwrap().info.clone()
}

#[tauri::command]
fn get_config(app: AppHandle) -> AppConfig {
    load_config(&app)
}

#[tauri::command]
fn set_config(app: AppHandle, state: State<'_, EngineState>, python: String, library: String) -> Result<EngineInfo, String> {
    let cfg = AppConfig { python: python.trim().into(), library: library.trim().into() };
    if cfg.library.is_empty() {
        return Err("A library folder is required".into());
    }
    save_config(&app, &cfg)?;
    start(&app, state.inner(), cfg);
    Ok(state.0.lock().unwrap().info.clone())
}

#[tauri::command]
fn restart_engine(app: AppHandle, state: State<'_, EngineState>) -> EngineInfo {
    start(&app, state.inner(), load_config(&app));
    state.0.lock().unwrap().info.clone()
}

pub fn run() {
    let state = EngineState(Arc::new(Mutex::new(Inner { info: EngineInfo::default(), child: None, generation: 0 })));
    let on_exit = state.clone();
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(state)
        .setup(|app| {
            let handle = app.handle().clone();
            let cfg = load_config(&handle);
            start(&handle, app.state::<EngineState>().inner(), cfg);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![engine_info, get_config, set_config, restart_engine])
        .build(tauri::generate_context!())
        .expect("error while building RoamIndex")
        .run(move |_app, event| {
            if let tauri::RunEvent::Exit = event {
                let mut g = on_exit.0.lock().unwrap();
                g.info.status = "stopped".into();
                stop_child(&mut g);
            }
        });
}
