/*
Paste this file into the developer console of a public PMC article page. The runner
prompts for the ephemeral token printed by the PMC browser bridge, so the token is
never written into this tracked file.

The browser fetches only a same-origin main-article PDF selected by the loopback bridge.
It starts at most one PDF request per second and does not attempt to solve a CAPTCHA or
other browser challenge. Stop safely with:

  window.sbolPmcRunner.stop()
*/

(() => {
  const bridge = "http://127.0.0.1:8766";
  const token = window.prompt("Paste the ephemeral SBOL PMC bridge token:");
  const minimumRequestIntervalMs = 1100;
  const maximumConsecutiveFailures = 5;
  const maximumPdfBytes = 120 * 1024 * 1024;
  const pdfFetchTimeoutMs = 90 * 1000;

  if (window.location.origin !== "https://pmc.ncbi.nlm.nih.gov") {
    throw new Error("Run this only from a public https://pmc.ncbi.nlm.nih.gov article page");
  }
  if (!token) {
    throw new Error("A bridge token is required");
  }
  if (window.sbolPmcRunner?.running) {
    return window.sbolPmcRunner;
  }

  const state = {
    running: true,
    done: false,
    completed: 0,
    succeeded: 0,
    failed: 0,
    transientStops: 0,
    released: 0,
    transientReleaseFailed: false,
    requiresBridgeRestart: false,
    consecutiveFailures: 0,
    current: null,
    lastError: null,
    lastPdfRequestStartedAt: null,
    startedAt: new Date().toISOString(),
    activePdfAbortController: null,
    stop() {
      this.running = false;
      this.stopReason = "user_stopped";
      this.activePdfAbortController?.abort("runner stopped by user");
    },
  };
  window.sbolPmcRunner = state;
  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  const reportFailure = async (item, response, error) => {
    try {
      const url = `${bridge}/failure?doi=${encodeURIComponent(item.doi)}`;
      await fetch(url, {
        method: "POST",
        credentials: "omit",
        headers: {
          "Content-Type": "application/json",
          "X-SBOL-Bridge-Token": token,
          "X-SBOL-Queue-Nonce": item.queue_nonce,
        },
        body: JSON.stringify({
          source_url: item.url,
          resolved_url: response?.url ?? null,
          article_url: item.article_url,
          error,
        }),
      });
    } catch (_) {
      // If the loopback bridge has stopped, the next /next request ends the runner.
    }
  };

  const reportTransientChallenge = async (item, response, error) => {
    const url = `${bridge}/transient?doi=${encodeURIComponent(item.doi)}`;
    const released = await fetch(url, {
      method: "POST",
      credentials: "omit",
      headers: {
        "Content-Type": "application/json",
        "X-SBOL-Bridge-Token": token,
        "X-SBOL-Queue-Nonce": item.queue_nonce,
      },
      body: JSON.stringify({
        source_url: item.url,
        resolved_url: response?.url ?? null,
        article_url: item.article_url,
        error,
      }),
    });
    if (!released.ok) {
      throw new Error(`bridge transient release HTTP ${released.status}`);
    }
    return released.json();
  };

  const reportUserRelease = async (item, response, reason) => {
    const url = `${bridge}/release?doi=${encodeURIComponent(item.doi)}`;
    const released = await fetch(url, {
      method: "POST",
      credentials: "omit",
      headers: {
        "Content-Type": "application/json",
        "X-SBOL-Bridge-Token": token,
        "X-SBOL-Queue-Nonce": item.queue_nonce,
      },
      body: JSON.stringify({
        source_url: item.url,
        resolved_url: response?.url ?? null,
        article_url: item.article_url,
        reason,
      }),
    });
    if (!released.ok) {
      throw new Error(`bridge user release HTTP ${released.status}`);
    }
    return released.json();
  };

  const validatedArticlePdfUrl = (rawUrl, item) => {
    const parsed = new URL(rawUrl);
    if (parsed.pathname.includes("%")) {
      throw new Error("bridge returned an encoded PMC article PDF path");
    }
    const decodedPath = parsed.pathname;
    const expectedPrefix = `/articles/${item.pmcid}/pdf/`.toLowerCase();
    const relativePath = decodedPath.slice(expectedPrefix.length);
    if (
      parsed.origin !== window.location.origin ||
      !decodedPath.toLowerCase().startsWith(expectedPrefix) ||
      !decodedPath.toLowerCase().endsWith(".pdf") ||
      !relativePath ||
      relativePath.includes("/") ||
      relativePath.includes("\\") ||
      decodedPath.split("/").some((segment) => segment === "." || segment === "..") ||
      parsed.search ||
      parsed.hash
    ) {
      throw new Error("bridge returned a noncanonical PMC article PDF URL");
    }
    return parsed;
  };

  const readBoundedPdf = async (response, signal) => {
    const declaredLength = response.headers.get("content-length");
    if (declaredLength !== null) {
      const parsedLength = Number(declaredLength);
      if (!Number.isSafeInteger(parsedLength) || parsedLength < 0 || parsedLength > maximumPdfBytes) {
        throw new Error(`PMC PDF Content-Length is invalid or exceeds ${maximumPdfBytes} bytes`);
      }
    }
    if (!response.body) {
      throw new Error("PMC PDF response has no readable body");
    }
    const reader = response.body.getReader();
    const cancelReader = () => {
      void reader.cancel(signal.reason ?? "PDF fetch aborted").catch(() => {});
    };
    signal.addEventListener("abort", cancelReader, { once: true });
    const chunks = [];
    let total = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > maximumPdfBytes) {
          await reader.cancel("PDF exceeds bridge safety limit");
          throw new Error(`PMC PDF exceeds ${maximumPdfBytes} bytes`);
        }
        chunks.push(value);
      }
    } finally {
      signal.removeEventListener("abort", cancelReader);
    }
    const body = new Blob(chunks, { type: "application/pdf" });
    const signature = await body.slice(0, 1024).text();
    if (!signature.includes("%PDF")) {
      const preview = await body.slice(0, 32 * 1024).text();
      const title = preview.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1]?.toLowerCase() ?? "";
      const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
      const challengeMarker =
        title.includes("checking your browser") ||
        title.includes("preparing to download") ||
        title.includes("recaptcha") ||
        /g-recaptcha|data-sitekey|recaptcha\/api\.js/i.test(preview);
      const error = new Error("PMC response does not contain a PDF header");
      if (challengeMarker || contentType.includes("text/html")) {
        error.pmcAccessChallenge = true;
      }
      throw error;
    }
    return body;
  };

  (async () => {
    let lastPdfRequestStartedAt = 0;
    while (state.running) {
      let next;
      try {
        const response = await fetch(
          `${bridge}/next?limit=1`,
          {
            cache: "no-store",
            credentials: "omit",
            headers: { "X-SBOL-Bridge-Token": token },
          },
        );
        if (response.status === 503) {
          const detail = await response.json().catch(() => ({}));
          state.lastError = "PMC discovery paused after an access challenge";
          state.retryAfterSeconds = detail.retry_after_seconds ?? null;
          state.running = false;
          state.stopReason = "pmc_discovery_challenge";
          break;
        }
        if (!response.ok) {
          throw new Error(`bridge next HTTP ${response.status}`);
        }
        next = await response.json();
      } catch (error) {
        state.lastError = String(error);
        if (state.stopReason === "user_stopped") {
          state.requiresBridgeRestart = true;
        } else {
          state.running = false;
          state.stopReason = "bridge_error";
        }
        break;
      }

      const item = next.items?.[0];
      if (!item) {
        if (state.running) {
          state.running = false;
          state.done = true;
          state.stopReason = "complete";
        }
        break;
      }

      state.current = item.doi;
      // Space the server-side article request that just completed from this PDF request.
      await sleep(minimumRequestIntervalMs);
      if (!state.running) {
        try {
          await reportUserRelease(item, null, "browser runner stopped before PDF request");
          state.released += 1;
        } catch (releaseError) {
          state.lastError = String(releaseError);
          state.requiresBridgeRestart = true;
        }
        state.current = null;
        break;
      }
      const waitMs = Math.max(
        0,
        lastPdfRequestStartedAt + minimumRequestIntervalMs - Date.now(),
      );
      if (waitMs > 0) {
        await sleep(waitMs);
      }
      lastPdfRequestStartedAt = Date.now();
      state.lastPdfRequestStartedAt = new Date(lastPdfRequestStartedAt).toISOString();

      let response = null;
      const pdfAbortController = new AbortController();
      state.activePdfAbortController = pdfAbortController;
      const pdfTimeout = setTimeout(
        () => pdfAbortController.abort("PMC PDF fetch timed out"),
        pdfFetchTimeoutMs,
      );
      try {
        validatedArticlePdfUrl(item.url, item);
        response = await fetch(item.url, {
          credentials: "same-origin",
          redirect: "error",
          cache: "no-store",
          signal: pdfAbortController.signal,
        });
        if ([403, 429, 503].includes(response.status)) {
          const error = new Error(`PMC PDF endpoint returned transient HTTP ${response.status}`);
          error.pmcAccessChallenge = true;
          throw error;
        }
        if (!response.ok) {
          throw new Error(
            `PMC HTTP ${response.status}; content-type=${response.headers.get("content-type")}`,
          );
        }
        validatedArticlePdfUrl(response.url, item);
        const body = await readBoundedPdf(response, pdfAbortController.signal);
        if (!state.running) {
          const error = new Error("browser runner stopped before upload");
          error.userStopped = true;
          throw error;
        }
        const upload = `${bridge}/upload?doi=${encodeURIComponent(item.doi)}&source_url=${encodeURIComponent(item.url)}&resolved_url=${encodeURIComponent(response.url)}&article_url=${encodeURIComponent(item.article_url)}`;
        const saved = await fetch(upload, {
          method: "POST",
          credentials: "omit",
          headers: {
            "Content-Type": "application/pdf",
            "X-SBOL-Bridge-Token": token,
            "X-SBOL-Queue-Nonce": item.queue_nonce,
          },
          body,
        });
        if (!saved.ok) {
          throw new Error(`bridge upload HTTP ${saved.status}: ${(await saved.text()).slice(0, 300)}`);
        }
        state.succeeded += 1;
        state.consecutiveFailures = 0;
      } catch (error) {
        state.lastError = `${item.doi}: ${String(error)}`;
        if (state.stopReason === "user_stopped" || error.userStopped) {
          try {
            await reportUserRelease(item, response, state.lastError);
            state.released += 1;
          } catch (releaseError) {
            state.lastError = `${state.lastError}; ${String(releaseError)}`;
            state.requiresBridgeRestart = true;
          }
        } else if (error.pmcAccessChallenge) {
          state.transientStops += 1;
          try {
            const released = await reportTransientChallenge(item, response, state.lastError);
            state.retryAfterSeconds = released.retry_after_seconds ?? null;
          } catch (releaseError) {
            state.lastError = `${state.lastError}; ${String(releaseError)}`;
            state.transientReleaseFailed = true;
            state.requiresBridgeRestart = true;
          }
          state.running = false;
          state.stopReason = "pmc_pdf_challenge";
        } else {
          state.failed += 1;
          state.consecutiveFailures += 1;
          await reportFailure(item, response, state.lastError);
          if (state.consecutiveFailures >= maximumConsecutiveFailures) {
            state.running = false;
            state.stopReason = "consecutive_failures";
          }
        }
      } finally {
        clearTimeout(pdfTimeout);
        if (state.activePdfAbortController === pdfAbortController) {
          state.activePdfAbortController = null;
        }
      }
      state.completed += 1;
      state.current = null;
      if (state.running) {
        // Space this PDF request from the next server-side PMC article request.
        await sleep(minimumRequestIntervalMs);
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
