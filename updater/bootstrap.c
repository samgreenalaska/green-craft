/* GreenCraftSetup.exe -- fetch the payload, unpack it, hand over.
 *
 *     read bootstrap.txt -> download GreenCraft-<version>.zip -> verify sha512
 *     -> unpack to %LOCALAPPDATA%\GreenCraft\app -> run app\GreenCraft.exe --setup
 *
 * Why C. The PyInstaller onefile build of this shipped an 8.4 MB download that
 * unpacked 17.2 MB across 12 files into %TEMP% on every launch -- an interpreter,
 * OpenSSL and a pile of .pyd files, all scanned on write and again on load, to run
 * 150 lines of glue. That extraction is also what produced
 *
 *     Failed to remove temporary directory: C:\Users\...\Temp\_MEI184802
 *
 * because Defender still held the directory when the bootloader tried to delete it.
 * This binary extracts nothing.
 *
 * Dependencies are Windows itself: WinHTTP for the download (Schannel validates the
 * certificate, and automatic proxy detection is a bonus urllib did not have), BCrypt
 * for sha512, and system tar.exe -- bsdtar, present since Windows 10 1803 -- to read
 * the zip, which is the one thing the C runtime cannot do for us.
 *
 * Why bootstrap.txt and not manifest.json. This used to walk the manifest with a
 * hand-rolled JSON reader: ~160 lines, the only code here that needed its own test
 * suite, and the only code that shipped real bugs (array indices were off, so the
 * second download URL was unreachable). tools/set_version.py now emits a two-key
 * pointer file alongside the manifest, from the same data in the same pass, and
 * tools/verify_release.py refuses to publish if the two disagree. Parsing it is
 * twenty lines that cannot be wrong in an interesting way.
 *
 * Being one version behind is survivable: the app self-updates from the manifest on
 * first run, so the installer only has to deliver something that works.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>
#include <bcrypt.h>
#include <shlwapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>

#pragma comment(lib, "winhttp.lib")
#pragma comment(lib, "bcrypt.lib")
#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "user32.lib")

#define POINTER_URL L"https://raw.githubusercontent.com/samgreenalaska/green-craft/main/bootstrap.txt"
#define UA          L"GreenCraftSetup/1"
#define TITLE       L"GreenCraft Setup"

/* Every host the installer will talk to. A redirect may land on any of these;
 * WinHTTP is configured to refuse an https->http downgrade outright. */
static const wchar_t *ALLOWED_HOSTS[] = {
    L"github.com",
    L"objects.githubusercontent.com",
    L"release-assets.githubusercontent.com",
    L"raw.githubusercontent.com",
};

static wchar_t g_err[512];

static void set_err(const wchar_t *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    _vsnwprintf_s(g_err, _countof(g_err), _TRUNCATE, fmt, ap);
    va_end(ap);
}

static void set_err_win(const wchar_t *what, DWORD code) {
    wchar_t *sys = NULL;
    /* WinHTTP's error range lives in winhttp.dll, not the system table. */
    FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
                       FORMAT_MESSAGE_IGNORE_INSERTS | FORMAT_MESSAGE_FROM_HMODULE,
                   GetModuleHandleW(L"winhttp.dll"), code, 0, (wchar_t *)&sys, 0, NULL);
    if (sys && *sys) {
        wchar_t *nl = wcspbrk(sys, L"\r\n");
        if (nl) *nl = 0;
        set_err(L"%s: %s", what, sys);
    } else {
        set_err(L"%s: Windows error 0x%08lX", what, code);
    }
    if (sys) LocalFree(sys);
}

/* No console and no GUI toolkit here, so failures go through the Win32 message box. */
static void message(const wchar_t *text, UINT flags) {
    MessageBoxW(NULL, text, TITLE, flags | MB_OK | MB_SETFOREGROUND);
}

/* ------------------------------------------------------------------ download */

typedef struct { unsigned char *data; size_t len; } buf_t;

static void buf_free(buf_t *b) {
    if (b->data) HeapFree(GetProcessHeap(), 0, b->data);
    b->data = NULL;
    b->len = 0;
}

static int host_allowed(const wchar_t *host, DWORD hostlen) {
    for (int i = 0; i < _countof(ALLOWED_HOSTS); i++) {
        size_t n = wcslen(ALLOWED_HOSTS[i]);
        if (n == hostlen && _wcsnicmp(host, ALLOWED_HOSTS[i], n) == 0) return 1;
    }
    return 0;
}

/* HTTPS GET into `out`. Returns 1 on success; on failure g_err says why. */
static int http_get(const wchar_t *url, buf_t *out, DWORD timeout_ms) {
    URL_COMPONENTS uc;
    wchar_t host[256], path[2048];
    HINTERNET hs = NULL, hc = NULL, hr = NULL;
    int ok = 0;

    ZeroMemory(&uc, sizeof(uc));
    uc.dwStructSize = sizeof(uc);
    uc.lpszHostName = host;      uc.dwHostNameLength = _countof(host);
    uc.lpszUrlPath = path;       uc.dwUrlPathLength = _countof(path);

    if (!WinHttpCrackUrl(url, 0, 0, &uc)) {
        set_err(L"could not parse the download URL");
        return 0;
    }
    if (uc.nScheme != INTERNET_SCHEME_HTTPS) {
        set_err(L"refusing non-HTTPS URL");
        return 0;
    }
    if (!host_allowed(host, uc.dwHostNameLength)) {
        set_err(L"refusing download from %s", host);
        return 0;
    }

    hs = WinHttpOpen(UA, WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
                     WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!hs) { set_err_win(L"could not start WinHTTP", GetLastError()); goto done; }

    WinHttpSetTimeouts(hs, 30000, 30000, (int)timeout_ms, (int)timeout_ms);
    {   /* Follow redirects, but never let one downgrade us to plaintext. */
        DWORD policy = WINHTTP_OPTION_REDIRECT_POLICY_DISALLOW_HTTPS_TO_HTTP;
        WinHttpSetOption(hs, WINHTTP_OPTION_REDIRECT_POLICY, &policy, sizeof(policy));
    }

    hc = WinHttpConnect(hs, host, uc.nPort, 0);
    if (!hc) { set_err_win(L"could not connect", GetLastError()); goto done; }

    hr = WinHttpOpenRequest(hc, L"GET", path, NULL, WINHTTP_NO_REFERER,
                            WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE);
    if (!hr) { set_err_win(L"could not build the request", GetLastError()); goto done; }

    if (!WinHttpSendRequest(hr, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                            WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(hr, NULL)) {
        set_err_win(L"the download failed", GetLastError());
        goto done;
    }

    {   DWORD status = 0, sz = sizeof(status);
        if (!WinHttpQueryHeaders(hr, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                 WINHTTP_HEADER_NAME_BY_INDEX, &status, &sz,
                                 WINHTTP_NO_HEADER_INDEX)) {
            set_err_win(L"no response from the server", GetLastError());
            goto done;
        }
        if (status != 200) {
            set_err(L"the server returned HTTP %lu", status);
            goto done;
        }
    }

    {   size_t cap = 1 << 16, len = 0;
        unsigned char *data = HeapAlloc(GetProcessHeap(), 0, cap);
        if (!data) { set_err(L"out of memory"); goto done; }
        for (;;) {
            DWORD got = 0;
            if (len + 32768 > cap) {
                size_t ncap = cap * 2;
                unsigned char *nd = HeapReAlloc(GetProcessHeap(), 0, data, ncap);
                if (!nd) { HeapFree(GetProcessHeap(), 0, data);
                           set_err(L"out of memory"); goto done; }
                data = nd;
                cap = ncap;
            }
            if (!WinHttpReadData(hr, data + len, 32768, &got)) {
                DWORD e = GetLastError();
                HeapFree(GetProcessHeap(), 0, data);
                set_err_win(L"the download was interrupted", e);
                goto done;
            }
            if (got == 0) break;
            len += got;
        }
        out->data = data;
        out->len = len;
        ok = 1;
    }

done:
    if (hr) WinHttpCloseHandle(hr);
    if (hc) WinHttpCloseHandle(hc);
    if (hs) WinHttpCloseHandle(hs);
    return ok;
}

/* Same, but ride out a transient failure. Friends install over house wifi. */
static int http_get_retry(const wchar_t *url, buf_t *out, DWORD timeout_ms, int tries) {
    for (int i = 0; i < tries; i++) {
        if (i) Sleep(2000);
        if (http_get(url, out, timeout_ms)) return 1;
    }
    return 0;
}

/* -------------------------------------------------------------------- sha512 */

static int sha512_hex(const unsigned char *data, size_t len, char out[129]) {
    BCRYPT_ALG_HANDLE alg = NULL;
    BCRYPT_HASH_HANDLE h = NULL;
    unsigned char digest[64];
    int ok = 0;

    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA512_ALGORITHM, NULL, 0) != 0) return 0;
    if (BCryptCreateHash(alg, &h, NULL, 0, NULL, 0, 0) != 0) goto done;
    /* BCryptHashData takes a ULONG length; feed a >4 GB buffer in chunks. */
    while (len) {
        ULONG chunk = (ULONG)(len > 0x10000000 ? 0x10000000 : len);
        if (BCryptHashData(h, (PUCHAR)data, chunk, 0) != 0) goto done;
        data += chunk;
        len -= chunk;
    }
    if (BCryptFinishHash(h, digest, sizeof(digest), 0) != 0) goto done;
    for (int i = 0; i < 64; i++) sprintf_s(out + i * 2, 3, "%02x", digest[i]);
    out[128] = 0;
    ok = 1;
done:
    if (h) BCryptDestroyHash(h);
    if (alg) BCryptCloseAlgorithmProvider(alg, 0);
    return ok;
}

/* -------------------------------------------------------------- pointer file
 *
 * key=value, one per line, '#' comments, no quoting and no escapes. Deliberately
 * the dullest format that can carry a URL and a hash.
 */
static int cfg_get(const char *text, size_t len, const char *key, char *out, size_t outsz) {
    size_t keylen = strlen(key);
    const char *p = text, *end = text + len;

    if (keylen == 0) return 0;      /* would otherwise match any "=value" line */
    while (p < end) {
        const char *eol = p;
        while (eol < end && *eol != '\n') eol++;
        {
            const char *line = p, *stop = eol;
            while (line < stop && (*line == ' ' || *line == '\t')) line++;
            while (stop > line && (stop[-1] == '\r' || stop[-1] == ' ' || stop[-1] == '\t'))
                stop--;
            if (line < stop && *line != '#' &&
                (size_t)(stop - line) > keylen &&
                memcmp(line, key, keylen) == 0 && line[keylen] == '=') {
                const char *v = line + keylen + 1;
                size_t n = (size_t)(stop - v);
                if (n == 0 || n >= outsz) return 0;
                memcpy(out, v, n);
                out[n] = 0;
                return 1;
            }
        }
        p = eol + 1;
    }
    return 0;
}

/* ------------------------------------------------------------------ file i/o */

static int path_exists(const wchar_t *p) {
    return GetFileAttributesW(p) != INVALID_FILE_ATTRIBUTES;
}

/* Recursive delete. Best effort, like shutil.rmtree(ignore_errors=True). */
static void rm_rf(const wchar_t *dir) {
    WIN32_FIND_DATAW fd;
    wchar_t pat[MAX_PATH * 2], child[MAX_PATH * 2];
    HANDLE h;

    if (!path_exists(dir)) return;
    swprintf_s(pat, _countof(pat), L"%s\\*", dir);
    h = FindFirstFileW(pat, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
            swprintf_s(child, _countof(child), L"%s\\%s", dir, fd.cFileName);
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                rm_rf(child);
            } else {
                SetFileAttributesW(child, FILE_ATTRIBUTE_NORMAL);
                DeleteFileW(child);
            }
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    RemoveDirectoryW(dir);
}

static int mkdir_p(const wchar_t *path) {
    wchar_t tmp[MAX_PATH * 2];
    wcscpy_s(tmp, _countof(tmp), path);
    for (wchar_t *p = tmp + 3; *p; p++) {          /* skip past "C:\" */
        if (*p == L'\\') {
            *p = 0;
            CreateDirectoryW(tmp, NULL);
            *p = L'\\';
        }
    }
    return CreateDirectoryW(path, NULL) || GetLastError() == ERROR_ALREADY_EXISTS;
}

static int write_all(const wchar_t *path, const unsigned char *data, size_t len) {
    HANDLE h = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    while (len) {
        DWORD chunk = (DWORD)(len > (1u << 24) ? (1u << 24) : len), wrote = 0;
        if (!WriteFile(h, data, chunk, &wrote, NULL) || wrote != chunk) {
            CloseHandle(h);
            return 0;
        }
        data += wrote;
        len -= wrote;
    }
    CloseHandle(h);
    return 1;
}

/* Run a command to completion. Returns its exit code, or -1 if it would not start. */
static int run_wait(wchar_t *cmdline, const wchar_t *cwd) {
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD code = (DWORD)-1;

    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));

    if (!CreateProcessW(NULL, cmdline, NULL, NULL, FALSE,
                        CREATE_NO_WINDOW, NULL, cwd, &si, &pi))
        return -1;
    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return (int)code;
}

/* Replace `dest` with the contents of the zip in `data`.
 *
 * The zip is written next to the destination rather than into %TEMP%: same volume,
 * so nothing depends on TEMP existing, being writable, surviving a cleaner, or being
 * on the same disk. Staged in a sibling directory and swapped, so an interrupted
 * download cannot leave a half-written app folder behind.
 */
static int unpack(const unsigned char *data, size_t len,
                  const wchar_t *dest, const wchar_t *workdir) {
    wchar_t staging[MAX_PATH * 2], old[MAX_PATH * 2], zip[MAX_PATH * 2];
    wchar_t tar[MAX_PATH], cmd[MAX_PATH * 5];
    int rc, ok = 0;

    swprintf_s(staging, _countof(staging), L"%s.new", dest);
    swprintf_s(old, _countof(old), L"%s.old", dest);
    swprintf_s(zip, _countof(zip), L"%s\\payload.zip", workdir);

    if (!GetSystemDirectoryW(tar, _countof(tar))) {
        set_err(L"could not locate the Windows system directory");
        return 0;
    }
    wcscat_s(tar, _countof(tar), L"\\tar.exe");
    if (!path_exists(tar)) {
        set_err(L"this version of Windows is too old: %s is missing", tar);
        return 0;
    }

    rm_rf(staging);
    if (!mkdir_p(staging)) {
        set_err(L"could not create %s", staging);
        return 0;
    }
    if (!write_all(zip, data, len)) {
        set_err(L"could not write %s -- is the disk full?", zip);
        goto cleanup;
    }

    /* bsdtar reads zip archives. Shipping an inflate implementation to avoid one
     * CreateProcess of a Microsoft-signed system binary is not a good trade. */
    swprintf_s(cmd, _countof(cmd), L"\"%s\" -x -f \"%s\" -C \"%s\"", tar, zip, staging);
    rc = run_wait(cmd, staging);
    if (rc != 0) {
        set_err(rc < 0 ? L"could not run %s" : L"%s could not read the download (exit %d)",
                tar, rc);
        goto cleanup;
    }

    rm_rf(old);
    if (path_exists(dest) && !MoveFileExW(dest, old, 0)) {
        set_err(L"could not replace the previous install -- is GreenCraft still running?");
        goto cleanup;
    }
    if (!MoveFileExW(staging, dest, 0)) {
        DWORD e = GetLastError();
        MoveFileExW(old, dest, 0);            /* put it back */
        set_err_win(L"could not move the new install into place", e);
        goto cleanup;
    }
    rm_rf(old);
    ok = 1;

cleanup:
    DeleteFileW(zip);
    if (!ok) rm_rf(staging);
    return ok;
}

/* --------------------------------------------------------------------- main */

static void fail(const wchar_t *fmt, ...) {
    wchar_t msg[1024];
    va_list ap;
    va_start(ap, fmt);
    _vsnwprintf_s(msg, _countof(msg), _TRUNCATE, fmt, ap);
    va_end(ap);
    message(msg, MB_ICONERROR);
}

static int install(void) {
    wchar_t install_dir[MAX_PATH * 2], app_dir[MAX_PATH * 2], app_exe[MAX_PATH * 2];
    wchar_t url_w[2048];
    char want[129], got[129], url[2048];
    buf_t pointer = {0}, payload = {0};

    {   const wchar_t *lad = _wgetenv(L"LOCALAPPDATA");
        if (!lad || !*lad) {
            fail(L"LOCALAPPDATA is not set, so there is nowhere to install to.");
            return 1;
        }
        swprintf_s(install_dir, _countof(install_dir), L"%s\\GreenCraft", lad);
        swprintf_s(app_dir, _countof(app_dir), L"%s\\app", install_dir);
        swprintf_s(app_exe, _countof(app_exe), L"%s\\GreenCraft.exe", app_dir);
    }

    if (!http_get_retry(POINTER_URL, &pointer, 60000, 3)) {
        fail(L"Could not reach GitHub to download GreenCraft.\n\n%s\n\n"
             L"Check your internet connection and try again.", g_err);
        return 1;
    }

    if (!cfg_get((const char *)pointer.data, pointer.len, "sha512", want, sizeof(want)) ||
        strlen(want) != 128 ||
        !cfg_get((const char *)pointer.data, pointer.len, "url", url, sizeof(url)) ||
        MultiByteToWideChar(CP_UTF8, 0, url, -1, url_w, _countof(url_w)) == 0) {
        buf_free(&pointer);
        fail(L"This version of the installer is out of date and the download "
             L"information is missing.\n\nAsk Sam for a newer installer.");
        return 1;
    }
    buf_free(&pointer);

    if (!http_get_retry(url_w, &payload, 300000, 3)) {
        fail(L"Could not download GreenCraft.\n\n%s", g_err);
        return 1;
    }
    if (!sha512_hex(payload.data, payload.len, got) || strcmp(got, want) != 0) {
        buf_free(&payload);
        fail(L"The download did not match its checksum, so it was not installed.\n\n"
             L"Try again -- if it keeps happening, tell Sam.");
        return 1;
    }

    if (!mkdir_p(install_dir)) {
        buf_free(&payload);
        fail(L"Could not install GreenCraft.\n\ncould not create %s", install_dir);
        return 1;
    }
    {   int ok = unpack(payload.data, payload.len, app_dir, install_dir);
        buf_free(&payload);
        if (!ok) {
            fail(L"Could not install GreenCraft.\n\n%s", g_err);
            return 1;
        }
    }
    if (!path_exists(app_exe)) {
        fail(L"The download unpacked but GreenCraft.exe is missing.");
        return 1;
    }

    /* Hand our own arguments through, so `GreenCraftSetup.exe --uninstall` reaches the
     * app. With none of our own, ask for the first-run wizard. */
    {   wchar_t cmd[MAX_PATH * 4];
        const wchar_t *tail = PathGetArgsW(GetCommandLineW());
        STARTUPINFOW si;
        PROCESS_INFORMATION pi;

        swprintf_s(cmd, _countof(cmd), L"\"%s\" %s", app_exe,
                   (tail && *tail) ? tail : L"--setup");

        ZeroMemory(&si, sizeof(si));
        si.cb = sizeof(si);
        ZeroMemory(&pi, sizeof(pi));
        if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, 0, NULL, app_dir, &si, &pi)) {
            set_err_win(L"could not start it", GetLastError());
            fail(L"Installed, but could not start GreenCraft.\n\n%s\n\n"
                 L"Try running:\n%s", g_err, app_exe);
            return 1;
        }
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
    return 0;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR cmdline, int show) {
    (void)inst; (void)prev; (void)cmdline; (void)show;
    return install();
}
