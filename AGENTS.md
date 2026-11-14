# Project Notes

- For Electron-only verification stories, use a small harness under `gui/tmp_verify_*.cjs` that boots the built app by requiring `gui/dist-electron/main/index.js`, waits for the main `BrowserWindow`, then drives the renderer with `webContents.executeJavaScript()`.
- Run `npm.cmd run build` in `gui/` before those harnesses so `dist-electron` matches the current TypeScript sources.
