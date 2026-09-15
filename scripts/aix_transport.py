#!/usr/bin/env python3
"""
Pluggable in-page-fetch transport for aix.studio scrapers.

WHY: aix.studio sits behind Tencent WAF that resets non-browser TLS handshakes
(JA3 fingerprint filtering). All API requests MUST originate from a real
browser page on https://aix.studio (same-origin in-page fetch).

Backends (both execute the exact same JS — an async IIFE returning a JSON
string — inside a page on the target origin):

  agent-browser  sandbox CLI (`agent-browser eval`). Auto-selected when the
                 binary exists. Returns a JSON-quoted string (we unquote).
  playwright     Chromium via Playwright sync API. Used on GitHub Actions /
                 any machine with `pip install playwright && playwright
                 install chromium`. Headless by default (identical TLS/HTTP2
                 fingerprint to headed Chromium; UA + navigator.webdriver
                 overridden defensively).

Selection order: AIX_TRANSPORT env var > agent-browser on PATH > playwright.
"""
import json
import os
import shutil
import subprocess

BASE_URL = os.environ.get("AIX_BASE_URL", "https://aix.studio")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")


def unquote(s):
    """agent-browser returns a JSON-encoded string result; strip one layer.
    Playwright returns the raw string — this is a no-op for it."""
    if isinstance(s, str) and s.startswith('"') and s.endswith('"'):
        try:
            return json.loads(s)
        except Exception:
            return s[1:-1]
    return s


class AgentBrowserTransport:
    name = "agent-browser"

    def eval_js(self, js, timeout=300):
        r = subprocess.run(["agent-browser", "eval", js],
                           capture_output=True, text=True, timeout=timeout)
        out = r.stdout.strip()
        if not out:
            raise RuntimeError(f"agent-browser empty output. stderr={r.stderr[:300]}")
        return out  # quoted JSON string

    def close(self):
        pass


class PlaywrightTransport:
    name = "playwright"

    def __init__(self, base_url=BASE_URL, headless=None):
        from playwright.sync_api import sync_playwright
        if headless is None:
            headless = os.environ.get("AIX_HEADED", "") != "1"
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
        )
        self._ctx = self._browser.new_context(
            user_agent=UA, locale="en-US", timezone_id="America/Los_Angeles",
            extra_http_headers={"accept-language": "en-US,en;q=0.9"},
        )
        # only override webdriver — overriding plugins/languages gets sessions flagged
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        self._page = self._ctx.new_page()
        self._page.goto(base_url, wait_until="domcontentloaded", timeout=90000)
        self._page.wait_for_timeout(3000)  # let SPA boot / prime session

    def eval_js(self, js, timeout=300):
        # page.evaluate returns the raw string the IIFE returned
        return self._page.evaluate(js)

    def close(self):
        try:
            self._ctx.close()
            self._browser.close()
            self._pw.stop()
        except Exception:
            pass


_TRANSPORT = None


def get_transport():
    """Singleton transport, backend auto-detected (or forced via AIX_TRANSPORT)."""
    global _TRANSPORT
    if _TRANSPORT is not None:
        return _TRANSPORT
    forced = os.environ.get("AIX_TRANSPORT", "").strip().lower()
    if forced == "playwright":
        _TRANSPORT = PlaywrightTransport()
    elif forced == "agent-browser":
        _TRANSPORT = AgentBrowserTransport()
    elif shutil.which("agent-browser"):
        _TRANSPORT = AgentBrowserTransport()
    else:
        _TRANSPORT = PlaywrightTransport()
    return _TRANSPORT


def eval_js(js, timeout=300):
    """Evaluate JS on the aix.studio page; returns the RAW string result."""
    return get_transport().eval_js(js, timeout=timeout)


def eval_json(js, timeout=300):
    """Evaluate JS that returns JSON.stringify(...); returns parsed Python obj.
    Handles both agent-browser's quoted-string layer and playwright's raw."""
    return json.loads(unquote(eval_js(js, timeout=timeout)))


def smoke_test():
    """Quick health check: fetch gallery page 1. Raises on failure."""
    js = ("(async()=>{const r=await fetch('/apinew/comfy/canvas-open-info/listPage"
          "?pageNum=1&current=1&size=1');const j=await r.json();"
          "return JSON.stringify({code:j.code,total:j.data&&j.data.total});})()")
    return eval_json(js, timeout=90)


if __name__ == "__main__":
    t = get_transport()
    print(f"backend: {t.name}")
    print(f"smoke: {smoke_test()}")
    t.close()
