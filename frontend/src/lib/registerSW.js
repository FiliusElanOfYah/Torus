export function registerServiceWorker() {
  if (typeof window === "undefined") return;
  if (!("serviceWorker" in navigator)) return;
  // Only register once the page has loaded, and only on same-origin production-style serving.
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/service-worker.js", { scope: "/" })
      .catch((err) => {
        // Non-fatal; log for debugging.
        // eslint-disable-next-line no-console
        console.warn("[torus] service worker registration failed:", err?.message || err);
      });
  });
}
