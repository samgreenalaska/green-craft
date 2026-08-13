/* Self-test for cfg_get() in updater/bootstrap.c.
 *
 *     python tools/test_bootstrap.py
 *
 * bootstrap.c is #included rather than linked, to reach its statics. Its wWinMain
 * comes along unused; this builds as a console exe with its own main.
 *
 * cfg_get is the installer's only parser. Everything else fails loudly, but a parser
 * that returns the wrong value would download and install the wrong thing while
 * looking like it worked -- so it gets tested even though it is twenty lines. It
 * replaced a hand-rolled JSON reader that was ten times the size and did ship a bug.
 */
#include "../updater/bootstrap.c"

static int fails = 0;

static void check(int ok, const char *what) {
    printf("%s  %s\n", ok ? "ok   " : "FAIL ", what);
    if (!ok) fails++;
}

static void expect(const char *text, const char *key, const char *want, const char *what) {
    char got[512];
    int ok = cfg_get(text, strlen(text), key, got, sizeof(got));
    if (want == NULL) {
        check(!ok, what);
    } else {
        check(ok && strcmp(got, want) == 0, what);
        if (ok && strcmp(got, want) != 0) printf("        got '%s', want '%s'\n", got, want);
    }
}

int main(int argc, char **argv) {
    expect("url=https://x/y\n", "url", "https://x/y", "single line");
    expect("a=1\nurl=https://x/y\nb=2\n", "url", "https://x/y", "line among others");
    expect("url=https://x/y", "url", "https://x/y", "no trailing newline");
    expect("url=https://x/y\r\n", "url", "https://x/y", "CRLF is stripped");
    expect("  url=https://x/y  \n", "url", "https://x/y", "surrounding whitespace");

    /* The failure that matters: silently reading a neighbouring key. */
    expect("urlx=wrong\nurl=right\n", "url", "right", "longer key does not match");
    expect("ur=wrong\nurl=right\n", "url", "right", "shorter key does not match");
    expect("myurl=wrong\nurl=right\n", "url", "right", "suffix key does not match");
    expect("#url=commented\nurl=right\n", "url", "right", "comment line is skipped");
    expect("sha512=aaa\nurl=bbb\n", "sha512", "aaa", "first of two keys");
    expect("sha512=aaa\nurl=bbb\n", "url", "bbb", "second of two keys");

    /* Absent, empty and malformed all have to fail rather than return junk. */
    expect("url=https://x\n", "sha512", NULL, "missing key fails");
    expect("url=\n", "url", NULL, "empty value fails");
    expect("url\n", "url", NULL, "key with no '=' fails");
    expect("", "url", NULL, "empty input fails");
    expect("\n\n\n", "url", NULL, "blank lines only");
    expect("=novalue\n", "", NULL, "empty key fails");

    {   /* Value longer than the caller's buffer must fail, not truncate.
         * `small` is a macro for `char` in the Windows RPC headers -- do not
         * name a local that. */
        char tiny[8];
        char text[] = "url=https://a-very-long-url-indeed\n";
        check(!cfg_get(text, strlen(text), "url", tiny, sizeof(tiny)),
              "oversized value fails rather than truncating");
    }

    /* The real pointer file, read exactly as the installer reads it. */
    if (argc > 1) {
        FILE *f = NULL;
        static char text[65536];
        size_t n = 0;
        if (fopen_s(&f, argv[1], "rb") == 0 && f) {
            n = fread(text, 1, sizeof(text) - 1, f);
            fclose(f);
        }
        text[n] = 0;
        check(n > 0, "read the real bootstrap.txt");

        char sha[256], url[512];
        check(cfg_get(text, n, "sha512", sha, sizeof(sha)) && strlen(sha) == 128,
              "real file: sha512 is 128 hex chars");
        check(cfg_get(text, n, "url", url, sizeof(url)) &&
              strncmp(url, "https://github.com/", 19) == 0,
              "real file: url is an https github URL");
        printf("        sha512  %.16s...\n        url     %s\n", sha, url);
    }

    printf(fails ? "\n%d FAILED\n" : "\nall passed\n", fails);
    return fails ? 1 : 0;
}
