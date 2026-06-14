// Shared feedback utilities used by help.html and news-search-prototype.html.

/**
 * Resize and JPEG-compress an image File, then return the raw base64 string
 * (no data-URL prefix) ready to POST to /api/feedback.
 *
 * @param {File} file
 * @returns {Promise<string>} base64-encoded JPEG
 */
function compressAndEncode(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const MAX = 1024;
      let w = img.width, h = img.height;
      if (w > MAX || h > MAX) {
        if (w > h) { h = Math.round(h * MAX / w); w = MAX; }
        else       { w = Math.round(w * MAX / h); h = MAX; }
      }
      const canvas = document.createElement('canvas');
      canvas.width = w; canvas.height = h;
      canvas.getContext('2d').drawImage(img, 0, 0, w, h);
      resolve(canvas.toDataURL('image/jpeg', 0.8).split(',')[1]);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Image load failed')); };
    img.src = url;
  });
}

/**
 * Read the access_token JWT cookie and return the payload's email field,
 * or an empty string if absent / unparseable.
 *
 * @returns {string}
 */
function getJwtEmail() {
  const tokenCookie = document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith('access_token='));
  if (!tokenCookie) return '';
  try {
    const payload = JSON.parse(atob(tokenCookie.split('=')[1].split('.')[1]));
    return payload.email || '';
  } catch (e) {
    return '';
  }
}
