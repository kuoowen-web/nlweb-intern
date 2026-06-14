// ── Feedback Modal (main page) ────────────────────────────────
(function () {
    let _fbCat = null, _fbRating = 0;

    const feedbackModalOverlay = document.getElementById('feedbackModalOverlay');
    const feedbackModalBody = document.getElementById('feedbackModalBody');
    const feedbackModalFooter = document.getElementById('feedbackModalFooter');
    const originalBodyHtml = feedbackModalBody.innerHTML;

    // Cache user email once at startup (getJwtEmail from feedback-utils.js)
    const _jwtEmail = getJwtEmail();

    function setupCategoryChips() {
        document.querySelectorAll('#mainFbCategories .fb-radio-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('#mainFbCategories .fb-radio-btn').forEach(b => b.classList.remove('selected'));
                btn.classList.add('selected');
                _fbCat = btn.dataset.val;
            });
        });
    }

    function setupStarRating() {
        document.querySelectorAll('#mainFbStars .fb-star').forEach(star => {
            star.addEventListener('click', () => {
                _fbRating = parseInt(star.dataset.val);
                document.querySelectorAll('#mainFbStars .fb-star').forEach(s => {
                    s.classList.toggle('lit', parseInt(s.dataset.val) <= _fbRating);
                });
            });
        });
    }

    function openFeedbackModal() {
        // Close settings popover if open
        const pop = document.getElementById('settingsPopover');
        if (pop) pop.style.display = 'none';
        const emailInput = document.getElementById('mainFbEmail');
        if (emailInput && !emailInput.value && _jwtEmail) emailInput.value = _jwtEmail;
        feedbackModalOverlay.style.display = 'flex';
    }

    function closeFeedbackModal() {
        feedbackModalOverlay.style.display = 'none';
        feedbackModalBody.innerHTML = originalBodyHtml;
        feedbackModalFooter.style.display = '';
        _fbCat = null;
        _fbRating = 0;
        setupCategoryChips();
        setupStarRating();
    }

    document.getElementById('btnOpenFeedbackMain').addEventListener('click', openFeedbackModal);
    document.getElementById('btnCloseFeedbackMain').addEventListener('click', closeFeedbackModal);
    document.getElementById('btnCancelFeedbackMain').addEventListener('click', closeFeedbackModal);
    feedbackModalOverlay.addEventListener('click', function(e) {
        if (e.target === feedbackModalOverlay) closeFeedbackModal();
    });

    setupCategoryChips();
    setupStarRating();

    document.getElementById('btnSubmitFeedbackMain').addEventListener('click', async () => {
        const content = document.getElementById('mainFbContent').value.trim();
        const email = document.getElementById('mainFbEmail').value.trim();
        const errEl = document.getElementById('mainFbError');
        errEl.style.display = 'none';

        if (!_fbCat) { errEl.textContent = '請選擇問題類別'; errEl.style.display = 'block'; return; }
        if (!_fbRating) { errEl.textContent = '請給予評分'; errEl.style.display = 'block'; return; }
        if (content.length < 10) { errEl.textContent = '說明至少需要 10 個字元'; errEl.style.display = 'block'; return; }

        let screenshot = '';
        const fileInput = document.getElementById('mainFbScreenshot');
        if (fileInput.files[0]) {
            try { screenshot = await compressAndEncode(fileInput.files[0]); }
            catch (e) { errEl.textContent = '截圖處理失敗'; errEl.style.display = 'block'; return; }
        }

        const btn = document.getElementById('btnSubmitFeedbackMain');
        btn.disabled = true; btn.textContent = '送出中...';

        try {
            const resp = await fetch('/api/help/feedback', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ category: _fbCat, rating: _fbRating, content, email, screenshot }),
            });
            if (resp.ok) {
                feedbackModalBody.innerHTML = '<div class="fb-success">感謝您的回饋！</div>';
                feedbackModalFooter.style.display = 'none';
                setTimeout(closeFeedbackModal, 2000);
            } else {
                const err = await resp.json().catch(() => ({}));
                errEl.textContent = err.error || '送出失敗，請稍後再試';
                errEl.style.display = 'block';
            }
        } catch (e) {
            errEl.textContent = '網路錯誤，請稍後再試';
            errEl.style.display = 'block';
        } finally {
            btn.disabled = false; btn.textContent = '送出意見回饋';
        }
    });
})();
