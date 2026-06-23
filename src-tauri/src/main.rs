#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use std::{
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

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
    if let Some(parent) = file_path.parent() {
        fs::create_dir_all(parent).map_err(format_io_error)?;
    }
    fs::write(file_path, data).map_err(format_io_error)
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![
            ensure_writable_directory,
            executable_directory,
            list_project_files,
            read_project_file,
            write_project_file
        ])
        .run(tauri::generate_context!())
        .expect("failed to run tauri app");
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
