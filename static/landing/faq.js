/* static/landing/faq.js — /faq 公開頁：fetch /api/faq → accordion + 分類篩選。
   外部檔（過 CSP script-src 'self'）。無框架。 */
const CAT_LABELS = {
  all: '全部', general: '一般', search: '搜尋',
  account: '帳號', privacy: '隱私', other: '其他',
};

let _allFaqs = [];
let _currentCat = 'all';

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderFilters(cats) {
  const wrap = document.getElementById('faqFilters');
  const ordered = ['all', ...cats];
  wrap.innerHTML = ordered.map(c =>
    `<button class="faq-filter-btn${c === _currentCat ? ' active' : ''}" data-cat="${escHtml(c)}">${escHtml(CAT_LABELS[c] || c)}</button>`
  ).join('');
  wrap.querySelectorAll('.faq-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      _currentCat = btn.dataset.cat;
      wrap.querySelectorAll('.faq-filter-btn').forEach(b => b.classList.toggle('active', b === btn));
      renderList();
    });
  });
}

function renderList() {
  const list = document.getElementById('faqList');
  const items = _currentCat === 'all' ? _allFaqs : _allFaqs.filter(f => f.category === _currentCat);
  if (items.length === 0) {
    list.innerHTML = '<p class="faq-empty">目前沒有相關問題。</p>';
    return;
  }
  // 🔧 R1（nit-a）：.faq-question 帶 aria-expanded，toggle 時同步 true/false（a11y）。
  list.innerHTML = items.map(f => `
    <div class="faq-item">
      <button class="faq-question" aria-expanded="false">
        <span>${escHtml(f.question)}</span>
        <span class="faq-chevron" aria-hidden="true">▼</span>
      </button>
      <div class="faq-answer">${escHtml(f.answer)}</div>
    </div>
  `).join('');
  list.querySelectorAll('.faq-question').forEach(btn => {
    btn.addEventListener('click', () => {
      const open = btn.closest('.faq-item').classList.toggle('open');
      btn.setAttribute('aria-expanded', String(open));
    });
  });
}

async function load() {
  const list = document.getElementById('faqList');
  try {
    const resp = await fetch('/api/faq');
    if (!resp.ok) throw new Error('status ' + resp.status);
    const data = await resp.json();
    _allFaqs = Array.isArray(data.faqs) ? data.faqs : [];
    const cats = [...new Set(_allFaqs.map(f => f.category))];
    renderFilters(cats);
    renderList();
  } catch (e) {
    console.error('FAQ load failed', e);
    list.innerHTML = '<p class="faq-empty">FAQ 載入失敗，請稍後再試。</p>';
  }
}

load();
