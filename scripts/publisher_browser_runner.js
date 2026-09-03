/*
Paste this file into the developer console of an authenticated ACS PDF opened through
CU Boulder's EZProxy. Replace the token below with the one printed by:

  uv run sbol-visual-data serve-publisher-bridge

The browser keeps all authentication cookies. Only PDF bytes and public source URLs are
posted to the loopback bridge. Stop safely with:

  window.sbolPublisherRunner.running = false
*/

(() => {
  const bridge = "http://127.0.0.1:8765";
  const token = "REPLACE_WITH_BRIDGE_TOKEN";
  const delayMs = 1250;
  const maximumConsecutiveFailures = 5;

  if (token === "REPLACE_WITH_BRIDGE_TOKEN") {
    throw new Error("Replace REPLACE_WITH_BRIDGE_TOKEN before starting the runner");
  }
  if (window.sbolPublisherRunner?.running) {
    return window.sbolPublisherRunner;
  }

  const state = {
    running: true,
    done: false,
    completed: 0,
    succeeded: 0,
    failed: 0,
    consecutiveFailures: 0,
    current: null,
    lastError: null,
    startedAt: new Date().toISOString(),
  };
  window.sbolPublisherRunner = state;
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  const reportFailure = async (item, response, error) => {
    try {
      const url = `${bridge}/failure?token=${encodeURIComponent(token)}&doi=${encodeURIComponent(item.doi)}`;
      await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_url: item.url,
          resolved_url: response?.url ?? null,
          error,
        }),
      });
    } catch (_) {
      // The bridge may have stopped; the outer loop will stop at its next status request.
    }
  };

  (async () => {
    while (state.running) {
      let next;
      try {
        const response = await fetch(
          `${bridge}/next?token=${encodeURIComponent(token)}&limit=1`,
          { cache: "no-store" },
        );
        if (!response.ok) {
          throw new Error(`bridge next HTTP ${response.status}`);
        }
        next = await response.json();
      } catch (error) {
        state.lastError = String(error);
        state.running = false;
        state.stopReason = "bridge_error";
        break;
      }

      const item = next.items?.[0];
      if (!item) {
        state.running = false;
        state.done = true;
        state.stopReason = "complete";
        break;
      }
      state.current = item.doi;
      let response = null;
      try {
        response = await fetch(item.url, {
          credentials: "include",
          redirect: "follow",
          cache: "no-store",
        });
        const bytes = await response.arrayBuffer();
        const signature = new TextDecoder("latin1").decode(bytes.slice(0, 5));
        if (!response.ok || !signature.startsWith("%PDF")) {
          throw new Error(
            `publisher HTTP ${response.status}; content-type=${response.headers.get("content-type")}; signature=${signature}`,
          );
        }
        const upload = `${bridge}/upload?token=${encodeURIComponent(token)}&doi=${encodeURIComponent(item.doi)}&source_url=${encodeURIComponent(item.url)}&resolved_url=${encodeURIComponent(response.url)}`;
        const saved = await fetch(upload, {
          method: "POST",
          headers: { "Content-Type": "application/pdf" },
          body: bytes,
        });
        if (!saved.ok) {
          throw new Error(`bridge upload HTTP ${saved.status}: ${(await saved.text()).slice(0, 300)}`);
        }
        state.succeeded += 1;
        state.consecutiveFailures = 0;
      } catch (error) {
        state.failed += 1;
        state.consecutiveFailures += 1;
        state.lastError = `${item.doi}: ${String(error)}`;
        await reportFailure(item, response, state.lastError);
        if (state.consecutiveFailures >= maximumConsecutiveFailures) {
          state.running = false;
          state.stopReason = "consecutive_failures";
        }
      }
      state.completed += 1;
      state.current = null;
      if (state.running) {
        await sleep(delayMs);
      }
    }
    state.finishedAt = new Date().toISOString();
  })().catch((error) => {
    state.running = false;
    state.stopReason = "runner_exception";
    state.lastError = String(error);
    state.finishedAt = new Date().toISOString();
  });

  return state;
})();
