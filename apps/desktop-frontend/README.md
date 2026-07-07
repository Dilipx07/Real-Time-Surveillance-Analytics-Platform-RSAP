# RSAP Desktop Frontend

Tauri 2 + React console for the local RSAP desktop backend at `http://127.0.0.1:8001`.

The frontend keeps access and session tokens in memory for the running app session and sends both `Authorization: Bearer <JWT>` and `X-Session-Token` on protected desktop-backend calls. The API base URL defaults to localhost and can be overridden for development with `VITE_RSAP_DESKTOP_API_URL`.

## Local Commands

```powershell
npm install
npm run lint
npm run typecheck
npm test -- --run
npm run build
npm run tauri build
```

The UI expects the desktop backend daemon to own SQLCipher, camera orchestration, CV runtime, and central sync. The frontend does not read local databases, camera credentials, Python code, or model files.
