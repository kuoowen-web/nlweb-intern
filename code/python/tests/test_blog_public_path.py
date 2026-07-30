"""
守護 middleware /blog/<slug> 放行 regex（_BLOG_PUBLIC_PATH_RE）與 slug 白名單
（_BLOG_SLUG_RE）同構、且只該放合法 slug。純 regex 單元測試，不需 server/DB。
"""
from webserver.middleware.auth import _BLOG_PUBLIC_PATH_RE, _BLOG_SLUG_RE


def test_valid_slug_path_allowed():
    assert _BLOG_PUBLIC_PATH_RE.match('/blog/hello-dubao')
    assert _BLOG_SLUG_RE.match('hello-dubao')


def test_traversal_and_bad_slug_rejected():
    # 🔧 R2（nit-1）：補尾隨換行樣本——decode 後的 'hello\n'（來自 %0a）與 path
    # '/blog/hello\n' 必須被拒（\Z 錨才封得住；$ 會誤放）。
    for bad in ['/blog/..%2f..%2fetc%2fpasswd', '/blog/../secret',
                '/blog/Foo_Bar', '/blog/a/b', '/blog/', '/blog',
                '/blog/UPPER', '/blog/has space', '/blog/hello\n']:
        assert not _BLOG_PUBLIC_PATH_RE.match(bad), f"不該放行：{bad!r}"
    # slug 層尾隨換行（decode 後）也不過白名單
    assert not _BLOG_SLUG_RE.match('hello\n'), "尾隨換行 slug 不該過白名單"


def test_path_regex_is_slug_regex_isomorphic():
    """放行 path 的 slug 段規則必須等同 slug 白名單（單一權威點防漂移）。"""
    # 同一組 slug 樣本：通過 _BLOG_SLUG_RE 者，其 /blog/<slug> 必通過 path regex；反之亦然。
    for slug in ['abc', 'a-b-c', 'x1', 'hello-dubao', 'A', 'a_b', 'a.b', '', 'a b', 'hello\n']:
        slug_ok = bool(_BLOG_SLUG_RE.match(slug))
        path_ok = bool(_BLOG_PUBLIC_PATH_RE.match(f'/blog/{slug}'))
        assert slug_ok == path_ok, f"slug={slug!r} 不同構：slug_ok={slug_ok} path_ok={path_ok}"
