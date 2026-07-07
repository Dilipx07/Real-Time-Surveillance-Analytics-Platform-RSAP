use sha2::{Digest, Sha256};
use std::{env, fs, path::PathBuf};

#[tauri::command]
fn get_machine_id() -> String {
    let mut hasher = Sha256::new();
    hasher.update(env::var("COMPUTERNAME").unwrap_or_default());
    hasher.update(env::var("USERNAME").unwrap_or_default());
    hasher.update(env::consts::OS);
    format!("{:x}", hasher.finalize())
}

#[tauri::command]
fn read_encrypted_config() -> Result<String, String> {
    let path = config_path()?;
    if !path.exists() {
        return Ok(String::new());
    }
    fs::read_to_string(path).map_err(|_| "Unable to read local RSAP configuration".to_string())
}

#[tauri::command]
fn write_encrypted_config(data: String) -> Result<(), String> {
    let path = config_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|_| "Unable to create local RSAP configuration directory".to_string())?;
    }
    fs::write(path, data).map_err(|_| "Unable to write local RSAP configuration".to_string())
}

fn config_path() -> Result<PathBuf, String> {
    dirs::home_dir()
        .map(|home| home.join(".rsap").join("session.enc"))
        .ok_or_else(|| "Unable to resolve home directory".to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            get_machine_id,
            read_encrypted_config,
            write_encrypted_config
        ])
        .run(tauri::generate_context!())
        .expect("error while running RSAP desktop app");
}
