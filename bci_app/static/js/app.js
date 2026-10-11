/* Small behaviours shared by every page. */
(function () {
  // Mobile menu
  const btn = document.getElementById("menu-btn");
  const side = document.getElementById("sidebar");
  if (btn && side) {
    btn.addEventListener("click", () => {
      const open = side.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("click", (e) => {
      if (side.classList.contains("open") && !side.contains(e.target) && !btn.contains(e.target)) {
        side.classList.remove("open");
        btn.setAttribute("aria-expanded", "false");
      }
    });
  }

  // Ask before destructive actions
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // Stop double submits that would send SMS twice
  document.querySelectorAll("form[data-once]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (e.defaultPrevented) return;
      form.querySelectorAll("button[type=submit], button:not([type])").forEach((b) => {
        b.disabled = true;
        if (b.dataset.busy) b.textContent = b.dataset.busy;
      });
    });
  });

  // SMS length counters: <textarea data-sms-count="#target">
  const GSM = "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà";
  const EXT = "^{}\\[~]|€";
  function parts(text) {
    let gsm = true, len = 0;
    for (const ch of text) {
      if (GSM.includes(ch)) len += 1;
      else if (EXT.includes(ch)) len += 2;
      else { gsm = false; break; }
    }
    if (!gsm) len = text.length; // UTF-16 units, so emoji count double like on the network
    const single = gsm ? 160 : 70, multi = gsm ? 153 : 67;
    return { len, segs: len === 0 ? 0 : len <= single ? 1 : Math.ceil(len / multi) };
  }
  window.BCI_smsParts = parts;

  function sample(text) {
    const s = window.BCI_SAMPLE || {};
    return text.replace(/\{(\w+)\}/g, (m, k) => (k in s ? s[k] : m)).replace(/\s+/g, " ").trim();
  }

  document.querySelectorAll("textarea[data-sms-count]").forEach((ta) => {
    const out = document.querySelector(ta.dataset.smsCount);
    const preview = ta.dataset.preview ? document.querySelector(ta.dataset.preview) : null;
    const update = () => {
      const text = sample(ta.value);
      const p = parts(text);
      if (out) {
        out.textContent = `${p.len} characters, ${p.segs} SMS ${p.segs === 1 ? "unit" : "units"} per person` +
          (p.segs > 1 ? ". Each extra unit is charged, so shorter is cheaper." : "");
        out.classList.toggle("over", p.segs > 1);
      }
      if (preview) preview.textContent = text || "Your message will appear here.";
    };
    ta.addEventListener("input", update);
    update();
  });

  // Placeholder buttons insert {first_name} etc. at the cursor
  document.querySelectorAll("[data-insert]").forEach((b) => {
    b.addEventListener("click", () => {
      const ta = document.querySelector(b.dataset.target);
      if (!ta) return;
      const token = b.dataset.insert;
      const start = ta.selectionStart ?? ta.value.length, end = ta.selectionEnd ?? ta.value.length;
      ta.value = ta.value.slice(0, start) + token + ta.value.slice(end);
      ta.focus();
      ta.selectionStart = ta.selectionEnd = start + token.length;
      ta.dispatchEvent(new Event("input"));
    });
  });
})();
