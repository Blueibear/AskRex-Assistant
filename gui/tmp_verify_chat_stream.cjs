const path = require('path');
const { app, BrowserWindow, ipcMain } = require('electron');

const guiDir = process.cwd();
app.setAppPath(guiDir);
require(path.join(guiDir, 'dist-electron', 'main', 'index.js'));

const streamTokens = ['Alpha', ' Beta', ' Gamma'];
const conversation = {
  id: '11111111-1111-4111-8111-111111111111',
  title: 'Electron verification conversation',
  created_at: '2026-01-01T00:00:00+00:00',
  updated_at: '2026-01-01T00:00:00+00:00',
  archived_at: null
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function registerDeterministicAppHandlers() {
  ipcMain.removeHandler('rex:getSetupStatus');
  ipcMain.handle('rex:getSetupStatus', async () => ({ ok: true, needs_setup: false }));
  ipcMain.removeHandler('rex:getStatus');
  ipcMain.handle('rex:getStatus', async () => ({ ok: true, status: 'ready' }));
  ipcMain.removeHandler('rex:getUnreadNotificationCount');
  ipcMain.handle('rex:getUnreadNotificationCount', async () => 0);
  ipcMain.removeHandler('rex:getProfile');
  ipcMain.handle('rex:getProfile', async () => ({ ok: true }));
  ipcMain.removeHandler('rex:conversation');
  ipcMain.handle('rex:conversation', async (_event, action) => {
    if (action === 'list') return [conversation];
    if (action === 'open') return { conversation, messages: [] };
    throw new Error('Unexpected deterministic conversation action: ' + action);
  });
  ipcMain.removeHandler('rex:startChatStream');
  ipcMain.handle('rex:startChatStream', async (event, { message, streamId, conversationId }) => {
    if (conversationId !== conversation.id) {
      throw new Error('Chat did not continue the selected canonical conversation');
    }
    process.stdout.write(JSON.stringify({ handler: 'rex:startChatStream', message }) + '\n');
    for (const token of streamTokens) {
      await sleep(240);
      event.sender.send('rex:chatToken', { streamId, token });
    }
    event.sender.send('rex:chatDone', { streamId });
    return { ok: true };
  });
}

async function waitForWindow() {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const win = BrowserWindow.getAllWindows()[0];
    if (win && !win.isDestroyed()) {
      return win;
    }
    await sleep(200);
  }
  throw new Error('Timed out waiting for Electron window');
}

app.whenReady().then(async () => {
  let ok = false;
  try {
    const win = await waitForWindow();
    registerDeterministicAppHandlers();
    await win.reload();
    await sleep(1500);
    const result = await win.webContents.executeJavaScript(`
      (async () => {
        const prompt = 'Smoke test message';
        const tokens = ${JSON.stringify(streamTokens)};
        const finalReply = tokens.join('');

        window.location.hash = '#/chat';
        await new Promise((resolve) => setTimeout(resolve, 700));

        window.__chatVerify = {
          partialSnapshots: [],
          finalReply,
          preloadStreamApiAvailable: typeof window.rex.sendChatStream === 'function'
        };
        if (!window.__chatVerify.preloadStreamApiAvailable) {
          throw new Error('Preload sendChatStream API is unavailable');
        }

        const textarea = document.querySelector('textarea[aria-label="Chat message input"]');
        if (!textarea) {
          throw new Error('Could not find chat textarea');
        }
        const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
        if (!nativeSetter) {
          throw new Error('Could not resolve textarea value setter');
        }
        nativeSetter.call(textarea, prompt);
        textarea.dispatchEvent(new Event('input', { bubbles: true }));

        const sendButton = document.querySelector('button[aria-label="Send message"]');
        if (!sendButton) {
          throw new Error('Could not find send button');
        }
        for (let attempt = 0; attempt < 20 && sendButton.disabled; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 50));
        }
        if (sendButton.disabled) {
          throw new Error('Send button did not enable after entering a message');
        }
        sendButton.click();

        let sawUserMessage = false;
        let sawFinal = false;
        let lastText = '';

        for (let attempt = 0; attempt < 60; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 120));
          lastText = document.body.innerText;
          window.__chatVerify.partialSnapshots.push(lastText);
          if (lastText.includes(prompt)) sawUserMessage = true;
          if (lastText.includes(finalReply)) {
            sawFinal = true;
            break;
          }
        }

        const partialSnapshots = window.__chatVerify?.partialSnapshots || [];
        const sawFirstPartial = partialSnapshots.some((text) => text.includes('Alpha') && !text.includes(finalReply));
        const sawSecondPartial = partialSnapshots.some((text) => text.includes('Alpha Beta') && !text.includes(finalReply));

        if (!sawUserMessage) {
          throw new Error('User message was not rendered in chat UI');
        }
        if (!sawFirstPartial || !sawSecondPartial) {
          throw new Error(
            'Streaming partial reply was not rendered token-by-token: ' + JSON.stringify({
              partialSnapshots,
              lastText
            })
          );
        }
        if (!sawFinal) {
          throw new Error('Final streamed reply was not rendered');
        }

        return {
          prompt,
          finalReply,
          sawUserMessage,
          sawFirstPartial,
          sawSecondPartial,
          sawFinal,
          excerpt: lastText.slice(0, 800)
        };
      })();
    `, true);
    process.stdout.write(JSON.stringify({ ok: true, result }) + '\n');
    ok = true;
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(error) }) + '\n');
  } finally {
    setTimeout(() => app.quit(), 500);
    if (!ok) {
      process.exitCode = 1;
    }
  }
});
