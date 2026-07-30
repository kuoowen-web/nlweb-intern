/**
 * 臺灣讀豹 Landing Page 互動：nav smooth-scroll 由 CSS scroll-behavior 處理。
 * 這裡只做：客群 tab 切換、sticky CTA 顯示、表單提交。
 */
function initAudienceTabs() {
  const tabs = document.querySelectorAll('.audience-tab');
  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      tabs.forEach((t) => {
        const active = t === tab;
        t.classList.toggle('is-active', active);
        t.setAttribute('aria-selected', String(active));
        document.getElementById(t.getAttribute('aria-controls')).hidden = !active;
      });
    });
  });
}
function initStickyCta() {
  const cta = document.querySelector('.sticky-cta');
  const hero = document.getElementById('about');
  const contact = document.getElementById('contact');
  if (!cta || !hero || !contact) return;
  let pastHero = false;
  let inContact = false;
  const update = () => { cta.hidden = !(pastHero && !inContact); };
  new IntersectionObserver(([e]) => { pastHero = !e.isIntersecting; update(); }).observe(hero);
  new IntersectionObserver(([e]) => { inContact = e.isIntersecting; update(); }).observe(contact);
}
function initContactForm() {
  const form = document.getElementById('early-bird-form');
  if (!form) return;
  const msg = form.querySelector('.form-msg');
  const btn = form.querySelector('.form-submit');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    msg.className = 'form-msg';
    msg.textContent = '';

    const data = Object.fromEntries(new FormData(form).entries());
    if (!data.name.trim()) { showError('請填寫姓名'); return; }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(data.email.trim())) { showError('請確認 Email 格式'); return; }

    btn.disabled = true;
    try {
      const resp = await fetch('/api/early-bird', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (resp.status === 201) {
        form.querySelectorAll('.form-row, .form-submit').forEach((el) => { el.hidden = true; });
        msg.classList.add('is-ok');
        msg.textContent = '已收到你的資料，我們會主動與你聯絡。';
        return;
      }
      if (resp.status === 429) { showError('提交太頻繁，請稍後再試。'); return; }
      const err = await resp.json().catch(() => ({}));
      showError(err.error || '送出失敗，請稍後再試。');
    } catch {
      showError('連線失敗，請稍後再試。');
    } finally {
      btn.disabled = false;
    }

    function showError(text) {
      msg.classList.add('is-error');
      msg.textContent = text;
    }
  });
}

initAudienceTabs();
initStickyCta();
initContactForm();
