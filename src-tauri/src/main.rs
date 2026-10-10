#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Mutex, mpsc},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::Manager;

#[derive(Default)]
struct BridgeState(Mutex<Option<BridgeProcess>>);
static PROJECT_WRITE_LOCK: Mutex<()> = Mutex::new(());

struct BridgeProcess {
    child: Child,
    info: BridgeInfo,
    #[cfg(windows)]
    _job: ScopedJob,
}

#[cfg(windows)]
struct ScopedJob(windows_sys::Win32::Foundation::HANDLE);
#[cfg(windows)]
unsafe impl Send for ScopedJob {}
#[cfg(windows)]
impl ScopedJob {
    fn own(child: &Child) -> Result<Self, String> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() { return Err(format_io_error(std::io::Error::last_os_error())); }
            let job = Self(handle);
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(handle, JobObjectExtendedLimitInformation, &limits as *const _ as *const _,
                std::mem::size_of_val(&limits) as u32) == 0 || AssignProcessToJobObject(handle, child.as_raw_handle()) == 0 {
                return Err(format_io_error(std::io::Error::last_os_error()));
            }
            Ok(job)
        }
    }
}
#[cfg(windows)]
impl Drop for ScopedJob {
    fn drop(&mut self) { unsafe { windows_sys::Win32::Foundation::CloseHandle(self.0); } }
}

fn retire_bridge(mut process: BridgeProcess) {
    if let Some(mut input) = process.child.stdin.take() {
        let _ = input.write_all(b"shutdown\n");
        let _ = input.flush();
    }
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        if process.child.try_wait().ok().flatten().is_some() { return; }
        thread::sleep(Duration::from_millis(50));
    }
    let _ = process.child.kill();
    let _ = process.child.wait();
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
    let _guard = PROJECT_WRITE_LOCK.lock().map_err(|error| error.to_string())?;
    let file_path = PathBuf::from(path);
    if file_path.is_file() {
        let previous = fs::read_to_string(&file_path).map_err(format_io_error)?;
        ensure_initial_configuration_unchanged(&previous,&data)?;
    }
    atomic_write(&file_path, data.as_bytes())
}

fn ensure_initial_configuration_unchanged(previous: &str, next: &str) -> Result<(), String> {
    let previous=serde_json::from_str::<serde_json::Value>(previous).ok();
    if let Some(previous)=previous.filter(|value| value["schema"]=="cr-native-replay.v2") {
        let next=serde_json::from_str::<serde_json::Value>(next).map_err(|error| error.to_string())?;
        if next["schema"]!=previous["schema"] || next["projectId"]!=previous["projectId"] || next["match"]!=previous["match"] {
            return Err("存档创建后初始卡组、牌序、等级和随机种子不可修改，请新建存档。".to_string());
        }
    }
    Ok(())
}

fn application_data_directory(app: &tauri::AppHandle) -> Result<PathBuf,String> {
    if let Ok(directory)=std::env::var("CR_DESKTOP_TEST_HOME") {
        let path=PathBuf::from(directory);
        if !path.is_absolute() || path.file_name().is_none() { return Err("Test workspace must be an absolute directory".to_string()); }
        return Ok(path);
    }
    app.path().app_local_data_dir().map_err(|error| error.to_string())
}

fn runtime_directory(app: &tauri::AppHandle) -> Result<PathBuf,String> {
    if std::env::var_os("CR_DESKTOP_TEST_HOME").is_some() { return Ok(application_data_directory(app)?.join("runtime")); }
    if cfg!(debug_assertions) {
        return Ok(PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().ok_or("Cannot resolve repository root")?.join(".local-runtime"));
    }
    Ok(application_data_directory(app)?.join("runtime"))
}

#[tauri::command]
fn native_test_workspace(app: tauri::AppHandle) -> Result<Option<String>,String> {
    if std::env::var_os("CR_DESKTOP_TEST_HOME").is_none() { return Ok(None); }
    Ok(Some(application_data_directory(&app)?.to_string_lossy().into_owned()))
}

#[tauri::command]
fn finish_desktop_test(app: tauri::AppHandle) -> Result<(),String> {
    if std::env::var_os("CR_DESKTOP_TEST_HOME").is_none() { return Err("Desktop testing is not enabled".to_string()); }
    app.exit(0);
    Ok(())
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

fn seed_existing_runtime(source: &Path, target: &Path) -> Result<bool, String> {
    // Only discover a complete, existing local environment on first use.
    // A user's explicitly selected configuration is never replaced here.
    if target.join("runtime.json").exists() || target.join("prepared.json").exists() { return Ok(false); }
    let settings_text = match fs::read_to_string(source.join("runtime.json")) { Ok(text) => text, Err(_) => return Ok(false) };
    let payload_text = match fs::read_to_string(source.join("prepared.json")) { Ok(text) => text, Err(_) => return Ok(false) };
    let settings: serde_json::Value = match serde_json::from_str(settings_text.trim_start_matches('\u{feff}')) { Ok(value) => value, Err(_) => return Ok(false) };
    let payload: serde_json::Value = match serde_json::from_str(payload_text.trim_start_matches('\u{feff}')) { Ok(value) => value, Err(_) => return Ok(false) };
    let (sdk, avd_home, avd_name, apk) = match (settings["sdkRoot"].as_str(), settings["avdHome"].as_str(), settings["avdName"].as_str(), payload["path"].as_str()) {
        (Some(sdk), Some(home), Some(name), Some(apk)) => (sdk, home, name, apk), _ => return Ok(false),
    };
    if settings["backend"] != "android-sdk" || !avd_name.chars().all(|value| value.is_ascii_alphanumeric() || value == '_' || value == '-')
        || !Path::new(sdk).join("emulator/emulator.exe").is_file()
        || !Path::new(avd_home).join(format!("{avd_name}.ini")).is_file() || !Path::new(apk).is_file() { return Ok(false); }
    atomic_write(&target.join("runtime.json"), settings_text.as_bytes())?;
    atomic_write(&target.join("prepared.json"), payload_text.as_bytes())?;
    Ok(true)
}

fn journal_path(app: &tauri::AppHandle, project_id: &str) -> Result<PathBuf, String> {
    if project_id.len() > 64
        || project_id.is_empty()
        || !project_id.chars().all(|value| value.is_ascii_alphanumeric() || value == '-')
    {
        return Err("Invalid journal project id".to_string());
    }
    let directory = application_data_directory(app)?;
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
async fn start_native_bridge(app: tauri::AppHandle, state: tauri::State<'_, BridgeState>) -> Result<BridgeInfo, String> {
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
    let mut secret = [0u8; 32];
    getrandom::getrandom(&mut secret).map_err(|error| error.to_string())?;
    let token: String = secret.iter().map(|byte| format!("{byte:02x}")).collect();
    let resources = app.path().resource_dir().map_err(|error| error.to_string())?;
    let bundled = resources.join("native-bridge/native-bridge.exe");
    let runtime_home = runtime_directory(&app)?;
    fs::create_dir_all(&runtime_home).map_err(format_io_error)?;
    if std::env::var_os("CR_DESKTOP_TEST_HOME").is_none() { seed_existing_runtime(&root.join(".local-runtime"), &runtime_home)?; }
    // ADB is a shared host service. Start it outside the owned bridge job so
    // retiring this application cannot terminate other emulator connections.
    let settings = fs::read_to_string(runtime_home.join("runtime.json")).ok()
        .and_then(|text| serde_json::from_str::<serde_json::Value>(text.trim_start_matches('\u{feff}')).ok());
    let default_sdk = PathBuf::from(std::env::var("LOCALAPPDATA").unwrap_or_default()).join("Android/Sdk");
    let sdk = settings.as_ref().and_then(|value| value["sdkRoot"].as_str()).map(PathBuf::from).unwrap_or(default_sdk);
    let adb = sdk.join("platform-tools/adb.exe");
    if adb.is_file() {
        let mut adb_start = Command::new(adb);
        adb_start.arg("start-server").stdout(Stdio::null()).stderr(Stdio::null());
        #[cfg(windows)] { use std::os::windows::process::CommandExt; adb_start.creation_flags(0x0800_0000); }
        if let Ok(mut startup) = adb_start.spawn() {
            let deadline = Instant::now() + Duration::from_secs(5);
            while startup.try_wait().ok().flatten().is_none() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(50));
            }
            if startup.try_wait().ok().flatten().is_none() { let _ = startup.kill(); let _ = startup.wait(); }
        }
    }
    let mut command = if bundled.is_file() {
        Command::new(&bundled)
    } else if cfg!(debug_assertions) && root.join("bridge/server.py").is_file() {
        let python = std::env::var("CR_BRIDGE_PYTHON").unwrap_or_else(|_| "python".to_string());
        let mut value = Command::new(python);
        value.args(["-m", "bridge.server"]).current_dir(&root);
        value
    } else {
        return Err("安装中的战斗连接服务缺失，请重新安装应用。".to_string());
    };
    command.arg("--parent-stdin")
        .env("CR_RUNTIME_HOME", &runtime_home)
        .env("PYTHONIOENCODING", "utf-8")
        .env("CR_BRIDGE_TOKEN", &token)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .stdin(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = command.spawn().map_err(format_io_error)?;
    #[cfg(windows)]
    let job = match ScopedJob::own(&child) {
        Ok(value) => value,
        Err(error) => { let _ = child.kill(); let _ = child.wait(); return Err(error); }
    };
    let stdout = child.stdout.take().ok_or("Bridge stdout unavailable")?;
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let mut line = String::new();
        let result = BufReader::new(stdout).read_line(&mut line).map(|_| line);
        let _ = sender.send(result);
    });
    let greeting = receiver.recv_timeout(Duration::from_secs(12))
        .ok().and_then(Result::ok).and_then(|line| serde_json::from_str::<BridgeGreeting>(&line).ok());
    let greeting = match greeting {
        Some(value) if value.port > 0 && value.protocol == "native-bridge.v1" => value,
        _ => { let _ = child.kill(); let _ = child.wait(); return Err("战斗连接服务启动失败，请检查运行环境。".to_string()); }
    };
    let info = BridgeInfo {
        port: greeting.port,
        token,
        protocol: greeting.protocol,
    };
    *guard = Some(BridgeProcess {
        child,
        info: info.clone(),
        #[cfg(windows)]
        _job: job,
    });
    Ok(info)
}

#[tauri::command]
fn stop_native_bridge(state: tauri::State<BridgeState>) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|error| error.to_string())?;
    if let Some(process) = guard.take() {
        retire_bridge(process);
    }
    Ok(())
}

#[tauri::command]
fn configure_native_runtime(app: tauri::AppHandle, state: tauri::State<BridgeState>, directory: String) -> Result<(), String> {
    let source = PathBuf::from(directory);
    let config = source.join("runtime.json");
    let prepared = source.join("prepared.json");
    let config_text = fs::read_to_string(config).map_err(format_io_error)?;
    let prepared_text = fs::read_to_string(prepared).map_err(format_io_error)?;
    let settings: serde_json::Value = serde_json::from_str(config_text.trim_start_matches('\u{feff}')).map_err(|_| "运行环境设置无效".to_string())?;
    let payload: serde_json::Value = serde_json::from_str(prepared_text.trim_start_matches('\u{feff}')).map_err(|_| "游戏准备记录无效".to_string())?;
    if settings["backend"] != "android-sdk" || !settings["sdkRoot"].is_string() || !settings["avdName"].is_string()
        || !settings["avdHome"].is_string() || !payload["path"].is_string() || !payload["apkSha256"].is_string() {
        return Err("请选择含运行环境设置和游戏准备记录的目录。".to_string());
    }
    let target = runtime_directory(&app)?;
    let mut guard = state.0.lock().map_err(|error| error.to_string())?;
    if let Some(process) = guard.take() { retire_bridge(process); }
    atomic_write(&target.join("runtime.json"), config_text.as_bytes())?;
    atomic_write(&target.join("prepared.json"), prepared_text.as_bytes())?;
    Ok(())
}

#[tauri::command]
fn native_runtime_directory(app: tauri::AppHandle) -> Result<String, String> {
    let path = runtime_directory(&app)?;
    Ok(path.to_string_lossy().into_owned())
}

fn main() {
    let app = tauri::Builder::default()
        .manage(BridgeState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .setup(|app| {
            if std::env::var_os("CR_DESKTOP_TEST_HOME").is_some() {
                if let Some(window)=app.get_webview_window("main") { window.hide()?; }
            }
            Ok(())
        })
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
            configure_native_runtime,
            native_runtime_directory,
            stop_native_bridge,
            native_test_workspace,
            finish_desktop_test
        ])
        .build(tauri::generate_context!())
        .expect("failed to build tauri app");
    app.run(|handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(state) = handle.try_state::<BridgeState>() {
                if let Ok(mut guard) = state.0.lock() {
                    if let Some(process) = guard.take() { retire_bridge(process); }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_archive_allows_node_edits_but_never_initial_configuration_changes() {
        let original=serde_json::json!({"schema":"cr-native-replay.v2","projectId":"same","match":{"seed":41,"level":16,"deck0":[1,2],"dealMode":"ordered"},"nodes":{}});
        let mut edited=original.clone();edited["nodes"]=serde_json::json!({"one":{"note":"updated"}});
        assert!(ensure_initial_configuration_unchanged(&original.to_string(),&edited.to_string()).is_ok());
        for field in ["seed","level","deck0","dealMode"] {
            let mut changed=original.clone();changed["match"][field]=serde_json::Value::Null;
            assert!(ensure_initial_configuration_unchanged(&original.to_string(),&changed.to_string()).is_err());
        }
        assert!(ensure_initial_configuration_unchanged(&original.to_string(),"{}").is_err());
    }

    #[test]
    fn first_run_discovers_complete_bom_configuration_without_overwriting_user_settings() {
        let folder = std::env::temp_dir().join(format!("native-environment-test-{}-{}", std::process::id(), timestamp_ms()));
        let source = folder.join("source");
        let target = folder.join("user-runtime");
        let sdk = folder.join("sdk");
        let avd = folder.join("avd");
        fs::create_dir_all(sdk.join("emulator")).unwrap();
        fs::create_dir_all(&avd).unwrap();
        fs::write(sdk.join("emulator/emulator.exe"), b"fixture").unwrap();
        fs::write(avd.join("CR_Dev_API31.ini"), b"fixture").unwrap();
        let apk = folder.join("prepared.apk");
        fs::write(&apk, b"fixture").unwrap();
        let config = serde_json::json!({"backend":"android-sdk", "sdkRoot":sdk, "avdHome":avd, "avdName":"CR_Dev_API31"});
        let payload = serde_json::json!({"path":apk, "apkSha256":"fixture"});
        atomic_write(&source.join("runtime.json"), format!("\u{feff}{config}").as_bytes()).unwrap();
        atomic_write(&source.join("prepared.json"), payload.to_string().as_bytes()).unwrap();
        assert!(seed_existing_runtime(&source, &target).unwrap());
        assert!(target.join("prepared.json").is_file());
        atomic_write(&target.join("runtime.json"), b"user-selected settings").unwrap();
        assert!(!seed_existing_runtime(&source, &target).unwrap());
        assert_eq!(fs::read(target.join("runtime.json")).unwrap(), b"user-selected settings");
        fs::remove_dir_all(folder).unwrap();
    }

    #[test]
    fn repeated_atomic_saves_replace_the_existing_file() {
        let folder = std::env::temp_dir().join(format!("native-save-test-{}-{}", std::process::id(), timestamp_ms()));
        let file = folder.join("project.json");
        atomic_write(&file, b"first").unwrap();
        atomic_write(&file, b"second confirmed version").unwrap();
        assert_eq!(fs::read(&file).unwrap(), b"second confirmed version");
        assert_eq!(fs::read_dir(&folder).unwrap().count(), 1);
        fs::remove_file(file).unwrap();
        fs::remove_dir(folder).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn closing_the_owned_job_terminates_its_child() {
        use std::os::windows::process::CommandExt;
        let ping = PathBuf::from(std::env::var("SystemRoot").unwrap()).join("System32/ping.exe");
        let mut child = Command::new(ping).args(["-n", "30", "127.0.0.1"])
            .stdout(Stdio::null()).stderr(Stdio::null()).creation_flags(0x0800_0000).spawn().unwrap();
        let job = ScopedJob::own(&child).unwrap();
        assert!(child.try_wait().unwrap().is_none());
        drop(job);
        let deadline = Instant::now() + Duration::from_secs(3);
        while child.try_wait().unwrap().is_none() && Instant::now() < deadline { thread::sleep(Duration::from_millis(20)); }
        assert!(child.try_wait().unwrap().is_some());
    }
}
