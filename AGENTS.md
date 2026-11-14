# Project Notes

## Supervised Ralph development loop

The owner has authorized Ralph to continue routine work on its isolated task branch without an interactive pause after one or two coding steps. The general chat guidance to pause during risky procedures does not require each delegated Ralph worker to stop for a new go-ahead. Continue through implementation, supervisor validation, and independent review for already authorized tasks.

Ask for a person only when a specific human-only gate applies, such as credentials, billing, physical acceptance, or an unapproved change to agent authority or a security or verification boundary. A missing tool or evidence in the disposable worker checkout is an operational problem to report to the supervisor, not a request for owner approval. Keep the single-writer lease, generated-lock integrity, all required gates, and review intact. Do not change live Rex, OpenClaw, or Home Assistant services through this development loop.

- For Electron-only verification stories, use a small harness under `gui/tmp_verify_*.cjs` that boots the built app by requiring `gui/dist-electron/main/index.js`, waits for the main `BrowserWindow`, then drives the renderer with `webContents.executeJavaScript()`.
- Run `npm.cmd run build` in `gui/` before those harnesses so `dist-electron` matches the current TypeScript sources.
