"""Playwright browser that the apply agent drives.

Uses a persistent profile directory, so once you log in to LinkedIn / Naukri /
Indeed / Internshala in this browser, the login is remembered next time.
Pages are described to Claude as a numbered list of interactive elements
(a "snapshot"); Claude acts on them by number ("ref").
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SNAPSHOT_JS = r"""
(args) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const cut = (s, n) => { s = clean(s); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  const visible = el => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && parseFloat(st.opacity || '1') > 0.05;
  };
  document.querySelectorAll('[data-ja-ref]').forEach(e => e.removeAttribute('data-ja-ref'));

  // If a modal dialog is open (e.g. LinkedIn Easy Apply), only describe what is inside it.
  const modals = Array.from(document.querySelectorAll('[role=dialog],[role=alertdialog],dialog[open],[aria-modal=true]')).filter(visible);
  const root = modals.length ? modals[modals.length - 1] : document.body;
  if (!root) return {elements: [], text: '', alerts: [], modal: false, title: document.title};

  const textOf = id => { const x = document.getElementById(id); return x ? x.innerText : ''; };
  const labelOf = el => {
    let t = el.getAttribute('aria-label'); if (clean(t)) return clean(t);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { t = lb.split(/\s+/).map(textOf).join(' '); if (clean(t)) return clean(t); }
    if (el.labels && el.labels.length) { t = Array.from(el.labels).map(l => l.innerText).join(' '); if (clean(t)) return clean(t); }
    const wrap = el.closest('label'); if (wrap && clean(wrap.innerText)) return clean(wrap.innerText);
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || '';
    if (tag === 'button' || tag === 'a' || tag === 'summary' || ['button','link','option','menuitem','tab','checkbox','radio','switch'].includes(role)) {
      t = el.innerText || el.value || el.getAttribute('title'); if (clean(t)) return clean(t);
      const img = el.querySelector('img[alt],svg[aria-label]'); if (img) return clean(img.getAttribute('alt') || img.getAttribute('aria-label'));
    }
    if (tag === 'input' && ['submit','button'].includes(el.type)) return clean(el.value);
    return clean(el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || el.id || '');
  };
  const questionOf = (el, weakOk) => {
    const fs = el.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg && clean(lg.innerText)) return clean(lg.innerText); }
    const grp = el.closest('[role=radiogroup],[role=group]');
    if (grp) {
      const l = grp.getAttribute('aria-label') || (grp.getAttribute('aria-labelledby') || '').split(/\s+/).map(textOf).join(' ');
      if (clean(l)) return clean(l);
    }
    if (!weakOk) return '';
    let p = el.parentElement;
    for (let i = 0; i < 5 && p && p !== root; i++, p = p.parentElement) {
      const prev = p.previousElementSibling;
      if (prev) { const t = clean(prev.innerText); if (t && t.length < 250) return t; }
    }
    return '';
  };

  const sel = 'input, textarea, select, button, a[href], summary, [role=button], [role=link], [role=checkbox], [role=radio], [role=combobox], [role=option], [role=menuitem], [role=tab], [role=switch], [role=textbox], [contenteditable=""], [contenteditable=true]';
  const fields = [], buttons = [], links = [];
  const seen = new Set();
  for (const el of root.querySelectorAll(sel)) {
    if (seen.has(el)) continue; seen.add(el);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && type === 'hidden') continue;
    const role = el.getAttribute('role') || '';
    const isFile = tag === 'input' && type === 'file';
    const isToggle = (tag === 'input' && (type === 'checkbox' || type === 'radio'));
    let vis = visible(el);
    if (!vis && isToggle) { const lab = (el.labels && el.labels[0]) || el.closest('label'); vis = !!(lab && visible(lab)); }
    if (!vis && !isFile) continue;
    const rec = {tag, type, role, label: cut(labelOf(el), 120)};
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') rec.disabled = true;
    if (el.required || el.getAttribute('aria-required') === 'true') rec.required = true;
    if (el.getAttribute('aria-invalid') === 'true') rec.invalid = true;
    if (isToggle || role === 'checkbox' || role === 'radio' || role === 'switch') {
      rec.checked = isToggle ? el.checked : el.getAttribute('aria-checked') === 'true';
      // Nearby-text guesses only for short labels like "Yes"/"No"; long labels stand alone.
      const q = questionOf(el, rec.label.length < 30); if (q && q !== rec.label) rec.question = cut(q, 160);
      fields.push([el, rec]);
    } else if (tag === 'select') {
      const opts = Array.from(el.options).map(o => clean(o.text)).filter(Boolean);
      rec.options = opts.slice(0, 40); if (opts.length > 40) rec.more_options = opts.length - 40;
      rec.value = el.selectedOptions.length ? clean(el.selectedOptions[0].text) : '';
      fields.push([el, rec]);
    } else if (tag === 'input' || tag === 'textarea' || role === 'textbox' || role === 'combobox' || el.isContentEditable) {
      if (isFile) { rec.files = Array.from(el.files || []).map(f => f.name); rec.accept = el.getAttribute('accept') || ''; }
      else if (type === 'password') rec.value = el.value ? '••••' : '';
      else if (type === 'submit' || type === 'button') { buttons.push([el, rec]); continue; }
      else rec.value = cut(el.isContentEditable ? el.innerText : el.value, 200);
      if (!rec.label || rec.label === el.getAttribute('name') || rec.label === el.id) { const q = questionOf(el, true); if (q) rec.question = cut(q, 160); }
      if (role === 'combobox' || el.getAttribute('aria-autocomplete')) rec.autocomplete = true;
      fields.push([el, rec]);
    } else if (tag === 'a' && role !== 'button') {
      rec.href = cut(el.getAttribute('href'), 100);
      if (rec.label) links.push([el, rec]);
    } else {
      if (rec.label || role === 'option') buttons.push([el, rec]);
    }
  }
  const picked = fields.concat(buttons).concat(links).slice(0, args.maxEls);
  let n = args.start;
  const elements = picked.map(([el, rec]) => { el.setAttribute('data-ja-ref', String(n)); rec.ref = n; n++; return rec; });
  const omitted = fields.length + buttons.length + links.length - picked.length;

  const alerts = Array.from(root.querySelectorAll('[role=alert], [aria-live=assertive], .error, .errors, [class*="error-message"], [class*="field-error"], [class*="invalid-feedback"]'))
    .filter(visible).map(e => cut(e.innerText, 200)).filter(Boolean).slice(0, 10);
  let text = (root.innerText || '').replace(/[ \t\u00a0]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim();
  if (text.length > args.maxText) text = text.slice(0, args.maxText) + '…';
  return {elements, omitted, text, alerts, modal: modals.length > 0, title: document.title};
}
"""

LOGIN_URLS = {
    "linkedin": "https://www.linkedin.com/login",
    "naukri": "https://www.naukri.com/nlogin/login",
    "indeed": "https://secure.indeed.com/auth?hl=en_IN",
    "internshala": "https://internshala.com/login/user",
    "glassdoor": "https://www.glassdoor.co.in/profile/login_input.htm",
    "foundit": "https://www.foundit.in/rio/login",
    "wellfound": "https://wellfound.com/login",
}


class BrowserError(RuntimeError):
    pass


@dataclass
class RefTarget:
    frame: Any
    ref: int


class Browser:
    def __init__(
        self,
        profile_dir: Path,
        *,
        headless: bool = False,
        slow_mo_ms: int = 0,
        channel: str | None = None,
        executable_path: str | None = None,
    ):
        self.profile_dir = profile_dir
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.channel = channel
        self.executable_path = executable_path
        self._pw = None
        self.context = None
        self.page = None
        self._refs: dict[int, RefTarget] = {}

    # ----------------------------------------------------------- lifecycle ---
    def start(self) -> None:
        if self.context is not None:
            return
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        kwargs: dict[str, Any] = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            slow_mo=self.slow_mo_ms,
            viewport={"width": 1280, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            args=["--disable-blink-features=AutomationControlled"],
        )
        if self.channel:
            kwargs["channel"] = self.channel
        if self.executable_path:
            kwargs["executable_path"] = self.executable_path
        try:
            self.context = self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            self._pw.stop()
            self._pw = None
            raise BrowserError(
                f"Could not start the browser ({e}). Run `playwright install chromium` once, "
                "or set browser_channel to 'chrome' to use your installed Chrome."
            ) from e
        self.context.on("page", self._on_new_page)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def close(self) -> None:
        try:
            if self.context is not None:
                self.context.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
            self.context = self._pw = self.page = None

    def _on_new_page(self, page) -> None:
        # "Apply on company website" usually opens a new tab: follow it.
        self.page = page

    def _current(self):
        self.start()
        if self.page is None or self.page.is_closed():
            open_pages = [p for p in self.context.pages if not p.is_closed()]
            self.page = open_pages[-1] if open_pages else self.context.new_page()
        return self.page

    def _settle(self, timeout_ms: int = 6000) -> None:
        page = self._current()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        time.sleep(0.6)

    # -------------------------------------------------------------- pages ---
    def goto(self, url: str) -> str:
        page = self._current()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self._settle()
        return f"Opened {page.url}"

    def tabs(self) -> list[str]:
        self.start()
        return [f"{i}: {p.title()[:60]} — {p.url[:100]}" for i, p in enumerate(self.context.pages) if not p.is_closed()]

    def switch_tab(self, index: int) -> str:
        self.start()
        pages = [p for p in self.context.pages if not p.is_closed()]
        if not 0 <= index < len(pages):
            raise BrowserError(f"No tab {index}. Open tabs: {len(pages)}")
        self.page = pages[index]
        self.page.bring_to_front()
        return f"Switched to tab {index}: {self.page.url}"

    def snapshot(self, max_text: int = 3500, max_elements: int = 220) -> str:
        page = self._current()
        self._settle(3000)
        self._refs = {}
        lines: list[str] = []
        next_ref = 1
        text_parts: list[str] = []
        all_alerts: list[str] = []
        modal = False
        omitted = 0
        for i, frame in enumerate(page.frames):
            if frame.is_detached():
                continue
            if i > 0 and (not frame.url or frame.url == "about:blank" or frame.url.startswith("javascript:")):
                continue
            try:
                data = frame.evaluate(
                    SNAPSHOT_JS,
                    {"start": next_ref, "maxEls": max_elements - (next_ref - 1), "maxText": max_text if i == 0 else 1500},
                )
            except Exception:
                continue  # cross-origin frame that is still loading, ad frames, etc.
            if not data or not data.get("elements") and not data.get("text"):
                continue
            if i > 0 and not data.get("elements"):
                continue
            modal = modal or data.get("modal", False)
            omitted += data.get("omitted", 0)
            all_alerts += data.get("alerts", [])
            if i > 0:
                lines.append(f"-- inside embedded frame: {frame.url[:100]}")
            for el in data["elements"]:
                self._refs[el["ref"]] = RefTarget(frame, el["ref"])
                lines.append(_format_element(el))
                next_ref = el["ref"] + 1
            if data.get("text"):
                text_parts.append(data["text"])
            if next_ref > max_elements:
                break
        header = [f"URL: {page.url}", f"Title: {page.title()[:120]}"]
        tabs = [p for p in self.context.pages if not p.is_closed()]
        if len(tabs) > 1:
            header.append(f"Open tabs: {len(tabs)} (this is tab {tabs.index(page)}; use switch_tab to change)")
        if modal:
            header.append("A dialog/modal is open: only its contents are listed.")
        if all_alerts:
            header.append("Alerts/errors on page: " + " | ".join(dict.fromkeys(all_alerts)))
        body = "\n".join(lines) if lines else "(no interactive elements found)"
        if omitted:
            body += f"\n(+{omitted} more elements not listed; scroll or close dialogs to see others)"
        text = "\n".join(text_parts)[: max_text + 1500]
        return "\n".join(header) + "\n\nInteractive elements:\n" + body + "\n\nVisible text:\n" + text

    def _locator(self, ref: int):
        target = self._refs.get(ref)
        if target is None:
            raise BrowserError(f"Unknown ref {ref}. Take a fresh page_snapshot first.")
        loc = target.frame.locator(f'[data-ja-ref="{ref}"]')
        if loc.count() == 0:
            raise BrowserError(f"Element {ref} is gone (the page changed). Take a fresh page_snapshot.")
        return loc.first

    def describe(self, ref: int) -> str:
        loc = self._locator(ref)
        return loc.evaluate(
            "el => (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') || '').trim().slice(0, 120)"
        )

    # ------------------------------------------------------------ actions ---
    def click(self, ref: int) -> str:
        page = self._current()
        loc = self._locator(ref)
        before_url, before_tabs = page.url, len(self.context.pages)
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
            loc.click(timeout=8000)
        except Exception:
            try:
                loc.click(timeout=4000, force=True)
            except Exception:
                loc.evaluate("el => el.click()")
        self._settle()
        notes = []
        if len(self.context.pages) > before_tabs:
            notes.append(f"a new tab opened and is now active: {self._current().url}")
        elif self._current().url != before_url:
            notes.append(f"page navigated to {self._current().url}")
        return "Clicked." + (" " + "; ".join(notes) if notes else "")

    def type_text(self, ref: int, text: str, press_enter: bool = False) -> str:
        loc = self._locator(ref)
        tag = loc.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            raise BrowserError("That is a dropdown; use select_option instead.")
        editable = loc.evaluate("el => el.isContentEditable")
        if editable:
            loc.click()
            self._current().keyboard.press("Control+A")
            self._current().keyboard.type(text, delay=10)
        else:
            try:
                loc.fill(text, timeout=8000)
            except Exception:
                loc.click(timeout=4000)
                loc.press_sequentially(text, delay=20)
        if press_enter:
            loc.press("Enter")
            self._settle()
        return f"Typed into {ref}."

    def select_option(self, ref: int, option: str) -> str:
        loc = self._locator(ref)
        tag = loc.evaluate("el => el.tagName.toLowerCase()")
        if tag != "select":
            raise BrowserError(
                "Not a native <select>. For custom dropdowns, click it, take a snapshot, then click the option."
            )
        for kwargs in ({"label": option}, {"value": option}):
            try:
                loc.select_option(**kwargs, timeout=4000)
                return f"Selected '{option}'."
            except Exception:
                pass
        options = loc.evaluate("el => Array.from(el.options).map(o => o.text.trim())")
        wanted = option.strip().lower()
        match = next((o for o in options if o.lower() == wanted), None) or next(
            (o for o in options if wanted in o.lower() or o.lower() in wanted and o), None
        )
        if not match:
            raise BrowserError(f"No option like '{option}'. Options: {options[:40]}")
        loc.select_option(label=match)
        return f"Selected '{match}'."

    def set_checked(self, ref: int, checked: bool) -> str:
        loc = self._locator(ref)
        try:
            loc.set_checked(checked, timeout=5000)
        except Exception:
            try:
                loc.set_checked(checked, timeout=3000, force=True)
            except Exception:
                is_checked = loc.evaluate(
                    "el => el.checked === undefined ? el.getAttribute('aria-checked') === 'true' : el.checked"
                )
                if is_checked != checked:
                    loc.evaluate("el => (el.labels && el.labels[0] ? el.labels[0] : el).click()")
        return f"{'Checked' if checked else 'Unchecked'} {ref}."

    def upload(self, ref: int, path: Path) -> str:
        if not path.exists():
            raise BrowserError(f"File not found: {path}")
        loc = self._locator(ref)
        is_file_input = loc.evaluate("el => el.tagName.toLowerCase() === 'input' && el.type === 'file'")
        if is_file_input:
            loc.set_input_files(str(path), timeout=10000)
        else:
            with self._current().expect_file_chooser(timeout=10000) as fc:
                loc.click()
            fc.value.set_files(str(path))
        self._settle()
        return f"Uploaded {path.name}."

    def press_key(self, key: str) -> str:
        self._current().keyboard.press(key)
        self._settle(3000)
        return f"Pressed {key}."

    def scroll(self, direction: str = "down", ref: int | None = None) -> str:
        if ref is not None:
            self._locator(ref).scroll_into_view_if_needed(timeout=5000)
            return f"Scrolled to {ref}."
        delta = {"down": 700, "up": -700}.get(direction, 700)
        self._current().mouse.wheel(0, delta)
        time.sleep(0.5)
        return f"Scrolled {direction}."

    def screenshot_b64(self) -> str:
        data = self._current().screenshot(type="jpeg", quality=55, full_page=False)
        return base64.standard_b64encode(data).decode()


def _format_element(el: dict) -> str:
    kind = el["tag"]
    if el.get("type") and el["tag"] == "input":
        kind = f"input[{el['type']}]"
    if el.get("role") and el["role"] not in (el["tag"],):
        kind += f"(role={el['role']})"
    parts = [f"[{el['ref']}] {kind} \"{el.get('label', '')}\""]
    if el.get("question"):
        parts.append(f"Q: \"{el['question']}\"")
    if el.get("required"):
        parts.append("*required")
    if el.get("disabled"):
        parts.append("disabled")
    if el.get("invalid"):
        parts.append("INVALID")
    if "checked" in el:
        parts.append("checked" if el["checked"] else "unchecked")
    if "value" in el and el["value"] != "":
        parts.append(f"value=\"{el['value']}\"")
    if el.get("options"):
        more = f" +{el['more_options']} more" if el.get("more_options") else ""
        parts.append(f"options={el['options']}{more}")
    if el.get("files") is not None and "files" in el:
        parts.append(f"files={el['files']}" + (f" accept={el['accept']}" if el.get("accept") else ""))
    if el.get("autocomplete"):
        parts.append("autocomplete")
    if el.get("href"):
        parts.append(f"-> {el['href']}")
    return " ".join(parts)
