// Learn My Voice: the desktop shell.
//
// It does three things and nothing else:
//   1. starts the app's core, which is the same Python program the download holds, with the Python that
//      ships inside this app;
//   2. shows the page the core serves on this computer in a window;
//   3. stops the core when the window is closed.
//
// The page gets nothing from this program: no command is exposed to it and no permission is granted to
// it. It can do what a page in a browser tab can do, and talk to the core on 127.0.0.1.
//
// The core is started with its standard input held open by this program. When this program goes away,
// however that happens, the core notices and stops by itself (`--exit-with-parent`).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::VecDeque;
use std::ffi::{OsStr, OsString};
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use tauri::webview::{DownloadEvent, PageLoadEvent};
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder};

/// How long the core gets to say where it listens. It usually does within a second.
const START_TIMEOUT: Duration = Duration::from_secs(60);
/// What the core prints in front of its address.
const ADDRESS: &str = "http://127.0.0.1:";
/// The last lines the core printed are kept, to be shown when it does not start.
const KEPT_LINES: usize = 40;
/// How the core is started. `-E -s`: nothing from the environment or the user's own Python packages gets
/// into it. `--port 0`: any free port. `--exit-with-parent`: see the top of this file.
const CORE_ARGUMENTS: [&str; 12] = [
    "-X", "utf8", "-B", "-u", "-E", "-s", "-m", "studio.server", "--no-browser", "--port", "0", "--exit-with-parent",
];
/// Written into the data folder while the app runs: where the core listens and which processes these are.
const RUNNING_FILE: &str = "desktop-running.json";

#[derive(Default)]
struct Core {
    child: Mutex<Option<Child>>,
    running_file: Mutex<Option<PathBuf>>,
}

/// Read what the core prints, line by line, until it ends. The address it announces is handed on; the
/// last lines are kept. Reading to the end matters: a pipe nobody reads fills up and stops the core.
fn follow<R: Read + Send + 'static>(stream: R, found: Sender<String>, kept: Arc<Mutex<VecDeque<String>>>) {
    thread::spawn(move || {
        let mut reader = BufReader::new(stream);
        let mut raw = Vec::new();
        loop {
            raw.clear();
            match reader.read_until(b'\n', &mut raw) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            let line = String::from_utf8_lossy(&raw).trim_end().to_string();
            if let Some(at) = line.find(ADDRESS) {
                let address = line[at..].split_whitespace().next().unwrap_or("").to_string();
                let _ = found.send(address);
            }
            if let Ok(mut lines) = kept.lock() {
                if lines.len() >= KEPT_LINES {
                    lines.pop_front();
                }
                lines.push_back(line);
            }
        }
    });
}

/// Start the core that ships in `resources/core` and wait for the address it listens on.
fn start_core(resources: &Path, data: &Path) -> Result<(Child, Url), String> {
    let core = resources.join("core");
    let app = core.join("app");
    let python = if cfg!(windows) {
        core.join("python").join("python.exe")
    } else {
        core.join("python").join("bin").join("python3")
    };
    if !python.is_file() || !app.join("studio").is_dir() {
        return Err(format!("Parts of the app are missing from its own folder:\n{}", core.display()));
    }

    let mut command = Command::new(&python);
    command
        .args(CORE_ARGUMENTS)
        .current_dir(&app)
        .env("STUDIO_DATA_DIR", data)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console window next to the app
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("The Python inside the app could not be started:\n{error}"))?;

    let (found, address) = mpsc::channel();
    let kept = Arc::new(Mutex::new(VecDeque::new()));
    if let Some(stream) = child.stdout.take() {
        follow(stream, found.clone(), kept.clone());
    }
    if let Some(stream) = child.stderr.take() {
        follow(stream, found, kept.clone());
    }

    // The wait ends early when the core ends: both of its outputs close, and with them the channel.
    let announced = address.recv_timeout(START_TIMEOUT).ok();
    let parsed = announced.as_deref().and_then(|text| Url::parse(text).ok());
    match parsed {
        Some(url) if url.scheme() == "http" && url.host_str() == Some("127.0.0.1") => Ok((child, url)),
        _ => {
            let _ = child.kill();
            let _ = child.wait();
            let said = kept
                .lock()
                .map(|lines| lines.iter().cloned().collect::<Vec<String>>().join("\n"))
                .unwrap_or_default();
            Err(format!("The app's core did not start. What it printed:\n\n{said}"))
        }
    }
}

/// Where the app keeps the creator's data: the system's folder for application data, or the folder named
/// by LEARN_MY_VOICE_DATA_DIR.
fn data_folder(app: &AppHandle) -> Result<PathBuf, String> {
    let folder = match std::env::var_os("LEARN_MY_VOICE_DATA_DIR") {
        Some(given) if !given.is_empty() => PathBuf::from(given),
        _ => app
            .path()
            .app_data_dir()
            .map_err(|error| format!("The folder for the app's data could not be found:\n{error}"))?
            .join("data"),
    };
    std::fs::create_dir_all(&folder)
        .map_err(|error| format!("The folder for the app's data could not be made:\n{}\n{error}", folder.display()))?;
    Ok(folder)
}

fn note_running(path: &Path, url: &Url, core_pid: u32, page_loaded: bool) {
    let note = serde_json::json!({
        "url": url.as_str(),
        "shell_pid": std::process::id(),
        "core_pid": core_pid,
        "page_loaded": page_loaded,
    });
    let _ = std::fs::write(path, note.to_string());
}

/// Start the core and remember it. Returns the address to show and the file that says the app is running.
fn prepare(app: &AppHandle) -> Result<(Url, PathBuf, u32), String> {
    let resources = app
        .path()
        .resource_dir()
        .map_err(|error| format!("The app's own folder could not be found:\n{error}"))?;
    let data = data_folder(app)?;
    let (child, url) = start_core(&resources, &data)?;
    let core_pid = child.id();
    let running = data.join(RUNNING_FILE);
    note_running(&running, &url, core_pid, false);
    let core = app.state::<Core>();
    if let Ok(mut slot) = core.child.lock() {
        *slot = Some(child);
    };
    if let Ok(mut slot) = core.running_file.lock() {
        *slot = Some(running.clone());
    };
    Ok((url, running, core_pid))
}

fn stop_core(app: &AppHandle) {
    let core = app.state::<Core>();
    let child = core.child.lock().ok().and_then(|mut slot| slot.take());
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
    let running = core.running_file.lock().ok().and_then(|mut slot| slot.take());
    if let Some(path) = running {
        let _ = std::fs::remove_file(path);
    }
}

/// A name in `folder` that is not taken: `name.zip`, then `name (2).zip`, and so on.
fn free_name(folder: &Path, wanted: &OsStr) -> PathBuf {
    let first = folder.join(wanted);
    if !first.exists() {
        return first;
    }
    let asked = Path::new(wanted);
    let stem = asked.file_stem().and_then(OsStr::to_str).unwrap_or("download").to_string();
    let ending = asked
        .extension()
        .and_then(OsStr::to_str)
        .map(|text| format!(".{text}"))
        .unwrap_or_default();
    for number in 2..1000 {
        let next = folder.join(format!("{stem} ({number}){ending}"));
        if !next.exists() {
            return next;
        }
    }
    first
}

/// The window on the core's page.
fn open_window(app: &AppHandle, url: Url, running: PathBuf, core_pid: u32) -> tauri::Result<()> {
    let home = url.origin();
    let shown = url.clone();
    let downloads = app.path().download_dir().ok();
    WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
        .title("Learn My Voice")
        .inner_size(1280.0, 860.0)
        .min_inner_size(960.0, 640.0)
        // The window only ever shows the core's own pages. A link to anywhere else opens in the browser.
        .on_navigation(move |target| {
            if target.origin() == home || matches!(target.scheme(), "about" | "blob" | "data") {
                return true;
            }
            if matches!(target.scheme(), "http" | "https") {
                let _ = open::that(target.as_str());
            }
            false
        })
        // What the app exports goes to the Downloads folder, under a name that is not taken.
        .on_download(move |_webview, event| {
            if let DownloadEvent::Requested { url, destination } = event {
                if let Some(folder) = &downloads {
                    let suggested = destination.file_name().map(|name| name.to_os_string());
                    let from_address = url
                        .path()
                        .rsplit('/')
                        .next()
                        .filter(|text| !text.is_empty())
                        .map(OsString::from);
                    let name = suggested.or(from_address).unwrap_or_else(|| OsString::from("download"));
                    *destination = free_name(folder, &name);
                }
            }
            true
        })
        .on_page_load(move |_webview, payload| {
            if payload.event() == PageLoadEvent::Finished {
                note_running(&running, &shown, core_pid, true);
            }
        })
        .build()?;
    Ok(())
}

/// A window that says why the app could not start, in English and Chinese (the page is in ../ui).
fn open_problem(app: &AppHandle, problem: &str) -> tauri::Result<()> {
    let text = serde_json::to_string(problem).unwrap_or_else(|_| String::from("\"\""));
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Learn My Voice")
        .inner_size(820.0, 560.0)
        .initialization_script(&format!("window.LMV_PROBLEM = {text};"))
        .build()?;
    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        // Opening the app a second time brings the window of the first to the front.
        .plugin(tauri_plugin_single_instance::init(|app, _arguments, _folder| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .manage(Core::default())
        .setup(|app| {
            let handle = app.handle().clone();
            match prepare(&handle) {
                Ok((url, running, core_pid)) => open_window(&handle, url, running, core_pid)?,
                Err(problem) => open_problem(&handle, &problem)?,
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("the app could not be put together");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_core(handle);
        }
    });
}
