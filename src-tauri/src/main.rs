#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::Manager;

#[derive(Default)]
struct BridgeState(Mutex<Option<BridgeProcess>>);

struct BridgeProcess {
    child: Child,
    info: BridgeInfo,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct BridgeInfo {
    port: u16,
    token: String,
    protocol: String,
}

#[derive(Deserialize)]
struct BridgeGreeting {
    port: u16,
    protocol: String,
}

#[derive(Serialize)]
struct SavedProjectFile {
    name: String,
    path: String,
    updated_at: Option<u64>,
}

#[tauri::command]
fn ensure_writable_directory(path: String) -> Result<(), String> {
    let directory = PathBuf::from(path);
    fs::create_dir_all(&directory).map_err(format_io_error)?;

    let probe = directory.join(format!(
        ".write-test-{}-{}.tmp",
        timestamp_ms(),
        std::process::id()
    ));
    fs::write(&probe, b"ok").map_err(format_io_error)?;
    let _ = fs::remove_file(probe);

    Ok(())
}

#[tauri::command]
fn executable_directory() -> Result<String, String> {
    let exe = std::env::current_exe().map_err(format_io_error)?;
    let directory = exe
        .parent()
        .ok_or_else(|| "Cannot resolve executable directory.".to_string())?;
    Ok(directory.to_string_lossy().into_owned())
}

#[tauri::command]
fn list_project_files(path: String) -> Result<Vec<SavedProjectFile>, String> {
    let directory = PathBuf::from(path);
    fs::create_dir_all(&directory).map_err(format_io_error)?;

    let mut files = Vec::new();
    for entry in fs::read_dir(directory).map_err(format_io_error)? {
        let entry = entry.map_err(format_io_error)?;
        let entry_path = entry.path();
        if !entry_path.is_file() || !has_json_extension(&entry_path) {
            continue;
        }

        let metadata = entry.metadata().ok();
        files.push(SavedProjectFile {
            name: entry.file_name().to_string_lossy().into_owned(),
            path: entry_path.to_string_lossy().into_owned(),
            updated_at: metadata
                .and_then(|value| value.modified().ok())
                .and_then(system_time_to_ms),
        });
    }

    files.sort_by(|a, b| b.updated_at.unwrap_or(0).cmp(&a.updated_at.unwrap_or(0)));
    Ok(files)
}

#[tauri::command]
fn read_project_file(path: String) -> Result<String, String> {
    fs::read_to_string(path).map_err(format_io_error)
}

#[tauri::command]
fn write_project_file(path: String, data: String) -> Result<(), String> {
    let file_path = PathBuf::from(path);
    atomic_write(&file_path, data.as_bytes())
}

fn atomic_write(file_path: &Path, data: &[u8]) -> Result<(), String> {
    if let Some(parent) = file_path.parent() {
        fs::create_dir_all(parent).map_err(format_io_error)?;
    }
    let temporary = file_path.with_extension(format!("json.{}.tmp", timestamp_ms()));
    let mut file = fs::File::create(&temporary).map_err(format_io_error)?;
    file.write_all(data).map_err(format_io_error)?;
    file.sync_all().map_err(format_io_error)?;
    fs::rename(&temporary, &file_path).map_err(|error| {
        let _ = fs::remove_file(&temporary);
        format_io_error(error)
    })
}

fn journal_path(app: &tauri::AppHandle, project_id: &str) -> Result<PathBuf, String> {
    if project_id.len() > 64
        || project_id.is_empty()
        || !project_id.chars().all(|value| value.is_ascii_alphanumeric() || value == '-')
    {
        return Err("Invalid journal project id".to_string());
    }
    let directory = app.path().app_local_data_dir().map_err(|error| error.to_string())?;
    Ok(directory.join("native-journals").join(format!("{project_id}.json")))
}

#[tauri::command]
fn write_native_journal(app: tauri::AppHandle, project_id: String, data: String) -> Result<(), String> {
    let path = journal_path(&app, &project_id)?;
    atomic_write(&path, data.as_bytes())
}

#[tauri::command]
fn read_native_journal(app: tauri::AppHandle, project_id: String) -> Result<Option<String>, String> {
    let path = journal_path(&app, &project_id)?;
    if !path.exists() {
        return Ok(None);
    }
    fs::read_to_string(path).map(Some).map_err(format_io_error)
}

#[tauri::command]
fn clear_native_journal(app: tauri::AppHandle, project_id: String) -> Result<(), String> {
    let path = journal_path(&app, &project_id)?;
    if path.exists() {
        fs::remove_file(path).map_err(format_io_error)?;
    }
    Ok(())
}

#[tauri::command]
fn start_native_bridge(state: tauri::State<BridgeState>) -> Result<BridgeInfo, String> {
    let mut guard = state.0.lock().map_err(|error| error.to_string())?;
    if let Some(process) = guard.as_mut() {
        if process.child.try_wait().map_err(format_io_error)?.is_none() {
            return Ok(process.info.clone());
        }
        *guard = None;
    }

    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("Cannot resolve repository root")?
        .to_path_buf();
    if !root.join("bridge").join("server.py").exists() {
        return Err("Native bridge files are unavailable.".to_string());
    }
    let mut secret = [0u8; 32];
    getrandom::getrandom(&mut secret).map_err(|error| error.to_string())?;
    let token: String = secret.iter().map(|byte| format!("{byte:02x}")).collect();
    let python = std::env::var("CR_BRIDGE_PYTHON").unwrap_or_else(|_| "python".to_string());
    let mut command = Command::new(python);
    command
        .arg("-m")
        .arg("bridge.server")
        .current_dir(root)
        .env("CR_BRIDGE_TOKEN", &token)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .stdin(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = command.spawn().map_err(format_io_error)?;
    let stdout = child.stdout.take().ok_or("Bridge stdout unavailable")?;
    let mut line = String::new();
    BufReader::new(stdout)
        .read_line(&mut line)
        .map_err(format_io_error)?;
    let greeting: BridgeGreeting = serde_json::from_str(&line)
        .map_err(|_| "Native bridge failed to start; check Python and bridge dependencies.".to_string())?;
    let info = BridgeInfo {
        port: greeting.port,
        token,
        protocol: greeting.protocol,
    };
    *guard = Some(BridgeProcess {
        child,
        info: info.clone(),
    });
    Ok(info)
}

#[tauri::command]
fn stop_native_bridge(state: tauri::State<BridgeState>) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|error| error.to_string())?;
    if let Some(mut process) = guard.take() {
        process.child.kill().map_err(format_io_error)?;
        process.child.wait().map_err(format_io_error)?;
    }
    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        .manage(BridgeState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![
            ensure_writable_directory,
            executable_directory,
            list_project_files,
            read_project_file,
            write_project_file,
            write_native_journal,
            read_native_journal,
            clear_native_journal,
            start_native_bridge,
            stop_native_bridge
        ])
        .build(tauri::generate_context!())
        .expect("failed to build tauri app");
    app.run(|handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(state) = handle.try_state::<BridgeState>() {
                if let Ok(mut guard) = state.0.lock() {
                    if let Some(mut process) = guard.take() {
                        let _ = process.child.kill();
                        let _ = process.child.wait();
                    }
                }
            }
        }
    });
}

fn has_json_extension(path: &Path) -> bool {
    path.extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("json"))
}

fn system_time_to_ms(value: SystemTime) -> Option<u64> {
    value
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|duration| u64::try_from(duration.as_millis()).ok())
}

fn timestamp_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}

fn format_io_error(error: std::io::Error) -> String {
    error.to_string()
}
