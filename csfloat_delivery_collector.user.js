// ==UserScript==
// @name         CSFloat Delivery Flow Collector
// @namespace    steamauto.local
// @version      0.1.0
// @description  Collects CSFloat delivery-flow UI and network metadata for local analysis.
// @match        https://csfloat.com/*
// @match        https://*.csfloat.com/*
// @run-at       document-start
// @grant        unsafeWindow
// @inject-into  page
// ==/UserScript==

(function () {
  "use strict";

  const pageWindow = typeof unsafeWindow === "undefined" ? window : unsafeWindow;
  const STORAGE_KEY = "steamauto.csfloat.delivery-collector.v1";
  const MAX_EVENTS = 1200;
  const MAX_TEXT_LENGTH = 20000;
  const REDACTED = "[REDACTED]";
  const SECRET_KEY_PATTERN = /(^|_)(authorization|cookie|password|passwd|secret|token|api_?key|trade_?url|email|phone|session)(_|$)/i;
  const SECRET_QUERY_PATTERN = /(token|key|secret|auth|session|code|signature|tradeurl|trade_url)/i;

  function now() {
    return new Date().toISOString();
  }

  function loadState() {
    try {
      const parsed = JSON.parse(pageWindow.localStorage.getItem(STORAGE_KEY) || "null");
      if (parsed && Array.isArray(parsed.events)) return parsed;
    } catch (_) {
      // A broken old recording should not stop collection.
    }
    return {
      collectorVersion: "0.1.0",
      startedAt: now(),
      origin: pageWindow.location.origin,
      userAgent: pageWindow.navigator.userAgent,
      events: [],
    };
  }

  let state = loadState();

  function saveState() {
    try {
      pageWindow.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (_) {
      // Keep collecting in memory if localStorage is temporarily unavailable/full.
    }
  }

  function addEvent(type, data) {
    state.events.push({
      sequence: state.events.length + 1,
      at: now(),
      type,
      pageUrl: sanitizeUrl(pageWindow.location.href),
      ...sanitize(data),
    });
    if (state.events.length > MAX_EVENTS) {
      state.events.splice(0, state.events.length - MAX_EVENTS);
    }
    saveState();
    updateCounter();
  }

  function sanitizeUrl(value) {
    try {
      const url = new URL(String(value), pageWindow.location.href);
      for (const key of [...url.searchParams.keys()]) {
        if (SECRET_QUERY_PATTERN.test(key)) url.searchParams.set(key, REDACTED);
      }
      url.hash = url.hash
        .replace(/(token|key|secret|auth|session|code|signature)=([^&]+)/gi, `$1=${REDACTED}`);
      return url.toString();
    } catch (_) {
      return String(value).slice(0, 1000);
    }
  }

  function sanitize(value, key = "", depth = 0) {
    if (SECRET_KEY_PATTERN.test(key)) return REDACTED;
    if (depth > 8) return "[MAX_DEPTH]";
    if (value === null || value === undefined || typeof value === "boolean" || typeof value === "number") return value;
    if (typeof value === "string") {
      let text = value;
      if (/^https?:\/\//i.test(text)) text = sanitizeUrl(text);
      text = text
        .replace(/(authorization|bearer|token|secret|session|signature)(["'\s:=]+)[^\s,"'}&]+/gi, `$1$2${REDACTED}`)
        .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[REDACTED_EMAIL]");
      return text.slice(0, MAX_TEXT_LENGTH);
    }
    if (Array.isArray(value)) return value.slice(0, 100).map((item) => sanitize(item, "", depth + 1));
    if (typeof value === "object") {
      const output = {};
      for (const [childKey, childValue] of Object.entries(value).slice(0, 150)) {
        output[childKey] = sanitize(childValue, childKey, depth + 1);
      }
      return output;
    }
    return String(value).slice(0, 1000);
  }

  function parseBody(body) {
    if (body === undefined || body === null) return null;
    if (typeof body === "string") {
      try {
        return sanitize(JSON.parse(body));
      } catch (_) {
        try {
          return sanitize(Object.fromEntries(new URLSearchParams(body)));
        } catch (_) {
          return sanitize(body);
        }
      }
    }
    if (body instanceof pageWindow.URLSearchParams || body instanceof pageWindow.FormData) {
      return sanitize(Object.fromEntries(body.entries()));
    }
    return sanitize(String(body));
  }

  function parseResponseText(text, contentType) {
    if (!text) return null;
    if (/json/i.test(contentType || "")) {
      try {
        return sanitize(JSON.parse(text));
      } catch (_) {
        return sanitize(text);
      }
    }
    return sanitize(text.replace(/<script[\s\S]*?<\/script>/gi, "[SCRIPT_REMOVED]").slice(0, MAX_TEXT_LENGTH));
  }

  function cssSelector(element) {
    if (!(element instanceof pageWindow.Element)) return "";
    const parts = [];
    let current = element;
    while (current && current.nodeType === 1 && parts.length < 7) {
      let part = current.tagName.toLowerCase();
      if (current.id) {
        part += `#${pageWindow.CSS.escape(current.id)}`;
        parts.unshift(part);
        break;
      }
      const testId = current.getAttribute("data-testid") || current.getAttribute("data-test") || current.getAttribute("data-cy");
      if (testId) part += `[data-testid="${String(testId).replace(/"/g, "\\\"")}"]`;
      else {
        const stableClasses = [...current.classList].filter((name) => name.length < 60).slice(0, 3);
        if (stableClasses.length) part += stableClasses.map((name) => `.${pageWindow.CSS.escape(name)}`).join("");
        if (current.parentElement) {
          const siblings = [...current.parentElement.children].filter((child) => child.tagName === current.tagName);
          if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
        }
      }
      parts.unshift(part);
      current = current.parentElement;
    }
    return parts.join(" > ");
  }

  function elementSummary(element) {
    const target = element instanceof pageWindow.Element ? element.closest("button, a, [role='button'], input, label, [data-testid], [data-test], [data-cy]") || element : null;
    if (!target) return {};
    return {
      tag: target.tagName.toLowerCase(),
      selector: cssSelector(target),
      text: (target.innerText || target.getAttribute("aria-label") || target.getAttribute("title") || "").trim().slice(0, 500),
      role: target.getAttribute("role"),
      controlType: target.getAttribute("type"),
      name: target.getAttribute("name"),
      testId: target.getAttribute("data-testid") || target.getAttribute("data-test") || target.getAttribute("data-cy"),
      disabled: Boolean(target.disabled || target.getAttribute("aria-disabled") === "true"),
      href: target.tagName === "A" ? sanitizeUrl(target.href) : null,
      html: target.outerHTML.replace(/\svalue=("[^"]*"|'[^']*')/gi, ` value="${REDACTED}"`).slice(0, 2000),
    };
  }

  function installFetchCollector() {
    const originalFetch = pageWindow.fetch;
    if (!originalFetch || originalFetch.__steamautoCollectorWrapped) return;

    async function wrappedFetch(input, init = {}) {
      const requestUrl = typeof input === "string" || input instanceof URL ? String(input) : input.url;
      const method = String(init.method || (input && input.method) || "GET").toUpperCase();
      const started = performance.now();
      try {
        const response = await originalFetch.apply(this, arguments);
        let responseBody = null;
        const sameOrigin = new URL(requestUrl, pageWindow.location.href).origin === pageWindow.location.origin;
        if (sameOrigin) {
          try {
            const clone = response.clone();
            responseBody = parseResponseText(await clone.text(), clone.headers.get("content-type"));
          } catch (_) {}
        }
        addEvent("fetch", {
          request: { method, url: sanitizeUrl(requestUrl), body: parseBody(init.body) },
          response: { status: response.status, ok: response.ok, body: responseBody },
          durationMs: Math.round(performance.now() - started),
        });
        return response;
      } catch (error) {
        addEvent("fetch-error", {
          request: { method, url: sanitizeUrl(requestUrl), body: parseBody(init.body) },
          error: String(error),
          durationMs: Math.round(performance.now() - started),
        });
        throw error;
      }
    }
    wrappedFetch.__steamautoCollectorWrapped = true;
    pageWindow.fetch = wrappedFetch;
  }

  function installXhrCollector() {
    const XHR = pageWindow.XMLHttpRequest;
    if (!XHR || XHR.prototype.__steamautoCollectorWrapped) return;
    const originalOpen = XHR.prototype.open;
    const originalSend = XHR.prototype.send;

    XHR.prototype.open = function (method, url) {
      this.__steamautoMeta = { method: String(method).toUpperCase(), url: String(url) };
      return originalOpen.apply(this, arguments);
    };
    XHR.prototype.send = function (body) {
      const started = performance.now();
      const meta = this.__steamautoMeta || { method: "GET", url: "" };
      this.addEventListener("loadend", () => {
        let responseBody = null;
        try {
          const sameOrigin = new URL(meta.url, pageWindow.location.href).origin === pageWindow.location.origin;
          if (sameOrigin && (!this.responseType || this.responseType === "text")) {
            responseBody = parseResponseText(this.responseText, this.getResponseHeader("content-type"));
          } else if (sameOrigin && this.responseType === "json") {
            responseBody = sanitize(this.response);
          }
        } catch (_) {}
        addEvent("xhr", {
          request: { method: meta.method, url: sanitizeUrl(meta.url), body: parseBody(body) },
          response: { status: this.status, body: responseBody },
          durationMs: Math.round(performance.now() - started),
        });
      }, { once: true });
      return originalSend.apply(this, arguments);
    };
    XHR.prototype.__steamautoCollectorWrapped = true;
  }

  function downloadRecording() {
    state.exportedAt = now();
    state.finalPageUrl = sanitizeUrl(pageWindow.location.href);
    const blob = new Blob([JSON.stringify(state, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `csfloat-delivery-flow-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  function clearRecording() {
    state = {
      collectorVersion: "0.1.0",
      startedAt: now(),
      origin: pageWindow.location.origin,
      userAgent: pageWindow.navigator.userAgent,
      events: [],
    };
    saveState();
    addEvent("recording-cleared", {});
  }

  let counterElement = null;
  function updateCounter() {
    if (counterElement) counterElement.textContent = `已采集 ${state.events.length} 条`;
  }

  function installPanel() {
    if (!document.body || document.getElementById("steamauto-csfloat-collector")) return;
    const panel = document.createElement("div");
    panel.id = "steamauto-csfloat-collector";
    panel.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#111827;color:#fff;padding:10px;border:1px solid #4b5563;border-radius:8px;font:12px/1.4 system-ui;box-shadow:0 4px 20px #0008";
    panel.innerHTML = `
      <div id="steamauto-collector-count" style="margin-bottom:7px">已采集 ${state.events.length} 条</div>
      <button type="button" data-action="export" style="margin-right:6px;cursor:pointer">导出采集数据</button>
      <button type="button" data-action="clear" style="cursor:pointer">清空并重录</button>
    `;
    panel.addEventListener("click", (event) => {
      event.stopPropagation();
      const action = event.target && event.target.getAttribute("data-action");
      if (action === "export") downloadRecording();
      if (action === "clear" && pageWindow.confirm("确定清空当前采集数据吗？")) clearRecording();
    });
    document.body.appendChild(panel);
    counterElement = panel.querySelector("#steamauto-collector-count");
  }

  installFetchCollector();
  installXhrCollector();

  document.addEventListener("click", (event) => {
    if (event.target && event.target.closest && event.target.closest("#steamauto-csfloat-collector")) return;
    addEvent("click", elementSummary(event.target));
  }, true);

  pageWindow.addEventListener("popstate", () => addEvent("navigation", { kind: "popstate" }));
  pageWindow.addEventListener("hashchange", () => addEvent("navigation", { kind: "hashchange" }));
  pageWindow.addEventListener("beforeunload", saveState);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      installPanel();
      addEvent("collector-ready", { title: document.title });
    }, { once: true });
  } else {
    installPanel();
    addEvent("collector-ready", { title: document.title });
  }
})();
