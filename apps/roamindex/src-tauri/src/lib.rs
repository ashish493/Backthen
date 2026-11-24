//! RoamIndex desktop shell.
//!
//! Starts the RoamEngine local API (`python -m roamengine serve`) as a child process, reads its one-line
//! `{"event":"ready","port":..,"token":..}` handshake, and hands port + token to the UI. The UI talks to the engine
//! over 127.0.0.1 only; it never imports engine internals (architecture doc, stage 7).
//! The engine exits when this process does: it watches its stdin, which we hold open.

use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Manager, State};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[derive(Clone, Serialize, Deserialize, Default)]
pub struct AppConfig {
    pub python: String,
    pub library: String,
}

#[derive(Clone, Serialize, Default)]
pub struct EngineInfo {
    pub status: String, // starting | ready | failed | stopped
    pub port: Option<u16>,
    pub token: Option<String>,
    pub library: String,
    pub python: String,
    pub error: Option<String>,
    pub log: Option<String>,
}

struct Inner {
    info: EngineInfo,
    child: Option<Child>,
    generation: u64,
}

#[derive(Clone)]
pub struct EngineState(Arc<Mutex<Inner>>);

/// Repository root at build time (apps/roamindex/src-tauri -> ../../..): used for development defaults only.
fn repo_root() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    for _ in 0..3 {
        p.pop();
    }
    p
}

fn default_config(app: &AppHandle) -> AppConfig {
    let repo = repo_root();
    let python = std::env::var("ROAM_PYTHON").unwrap_or_else(|_| {
        let p = repo.join(".venv").join("Scripts").join("python.exe");
        if p.exists() {
            p.to_string_lossy().into_owned()
        } else {
            "python".into()
        }
    });
    let library = std::env::var("ROAM_LIBRARY").unwrap_or_else(|_| {
        // Development default: the index already built in this repository, if there is one.
        let existing = repo.join("out").join("archive355");
        if existing.join("manifest.sqlite").exists() {
            return existing.to_string_lossy().into_owned();
        }
        app.path()
            .app_data_dir()
            .map(|d| d.join("library"))
            .unwrap_or_else(|_| repo.join("out").join("library"))
            .to_string_lossy()
            .into_owned()
    });
    AppConfig { python, library }
}

fn config_path(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("config.json"))
}

fn load_config(app: &AppHandle) -> AppConfig {
    if let Some(p) = config_path(app) {
        if let Ok(s) = std::fs::read_to_string(&p) {
            if let Ok(c) = serde_json::from_str::<AppConfig>(&s) {
                if !c.python.is_empty() && !c.library.is_empty() {
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

fn log_path(app: &AppHandle) -> Option<PathBuf> {
    let d = app.path().app_log_dir().ok()?;
    std::fs::create_dir_all(&d).ok()?;
    Some(d.join("engine.log"))
}

fn stop_child(g: &mut Inner) {
    if let Some(mut c) = g.child.take() {
        let _ = c.kill();
        let _ = c.wait();
    }
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
        log: log.as_ref().map(|p| p.to_string_lossy().into_owned()),
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
    cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
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
    if cfg.python.is_empty() || cfg.library.is_empty() {
        return Err("Python and library folder are both required".into());
    }
    save_config(&app, &cfg)?;
    spawn_engine(state.inner(), &cfg, log_path(&app));
    Ok(state.0.lock().unwrap().info.clone())
}

#[tauri::command]
fn restart_engine(app: AppHandle, state: State<'_, EngineState>) -> EngineInfo {
    let cfg = load_config(&app);
    spawn_engine(state.inner(), &cfg, log_path(&app));
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
            spawn_engine(app.state::<EngineState>().inner(), &cfg, log_path(&handle));
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
