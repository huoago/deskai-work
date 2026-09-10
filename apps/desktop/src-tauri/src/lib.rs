use std::{
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener},
    sync::Mutex,
};

use serde::Serialize;
use tauri::{Manager, State};
use tauri_plugin_shell::ShellExt;
use uuid::Uuid;

#[derive(Clone, Serialize)]
struct EngineBootstrap {
    endpoint: String,
    session_token: String,
}

struct EngineState(Mutex<Option<EngineBootstrap>>);

#[tauri::command]
fn get_engine_bootstrap(state: State<'_, EngineState>) -> Option<EngineBootstrap> {
    state.0.lock().ok()?.clone()
}

fn reserve_free_port() -> std::io::Result<u16> {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 0);
    let listener = TcpListener::bind(address)?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(EngineState(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![get_engine_bootstrap])
        .setup(|app| {
            let port = reserve_free_port()?;
            let port_arg = port.to_string();
            let session_token = Uuid::new_v4().to_string();
            let command = app
                .shell()
                .sidecar("deskai-engine")?
                .args([
                    "--host",
                    "127.0.0.1",
                    "--port",
                    port_arg.as_str(),
                    "--session-token",
                    session_token.as_str(),
                ]);

            match command.spawn() {
                Ok((_rx, _child)) => {
                    let bootstrap = EngineBootstrap {
                        endpoint: format!("http://127.0.0.1:{port}"),
                        session_token,
                    };
                    *app.state::<EngineState>().0.lock().expect("engine state poisoned") = Some(bootstrap);
                }
                Err(error) => {
                    eprintln!("Failed to start deskai-engine sidecar: {error}");
                }
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running DeskAI Work");
}
