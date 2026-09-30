/*
 * winusb-switch: put one USB device on Windows' inbox WinUSB driver
 * (%WINDIR%\INF\winusb.inf, class USBDevice) and give it back.
 *
 *   winusb-switch list [--out FILE]
 *   winusb-switch bind VVVV:PPPP [--out FILE]      (administrator)
 *   winusb-switch unbind VVVV:PPPP [--out FILE]    (administrator)
 *
 * stdout: one JSON object per line; --out writes the same lines to FILE
 * (an elevated run's stdout cannot be read by the program that started it).
 * Exit: 0 done, 3010 done but the device must be replugged (or Windows
 * restarted) to finish, 1 failed, 2 usage, 3 refused, 4 not elevated,
 * 5 in use (a program holds the device; nothing changed).
 *
 * Build (64-bit only; 32-bit on 64-bit Windows cannot install drivers):
 *   x86_64-w64-mingw32-gcc -O2 -Wall -municode -o winusb-switch.exe \
 *       winusb-switch.c -lsetupapi -lcfgmgr32
 */

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#undef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00

#include <windows.h>
#include <setupapi.h>
#include <cfgmgr32.h>
#include <winioctl.h>
#include <usbioctl.h>
#include <fcntl.h>
#include <io.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#define EXIT_DONE      0
#define EXIT_FAILED    1
#define EXIT_USAGE     2
#define EXIT_REFUSED   3
#define EXIT_NOTADMIN  4
#define EXIT_INUSE     5
#define EXIT_REBOOT    3010

/* newdev.dll, resolved at run time */
#define DIIDFLAG_INSTALLNULLDRIVER_ 0x00000004
typedef BOOL (WINAPI *DiInstallDevice_fn)(HWND, HDEVINFO, PSP_DEVINFO_DATA,
                                          PSP_DRVINFO_DATA_W, DWORD, PBOOL);
typedef BOOL (WINAPI *DiUninstallDevice_fn)(HWND, HDEVINFO, PSP_DEVINFO_DATA,
                                            DWORD, PBOOL);

/* Setup classes */
#define CLS_USBDEVICE L"{88bae032-5a81-49f0-bc3d-a4ff138216d6}"
#define CLS_KEYBOARD  L"{4d36e96b-e325-11ce-bfc1-08002be10318}"
#define CLS_MOUSE     L"{4d36e96f-e325-11ce-bfc1-08002be10318}"
#define CLS_DISK      L"{4d36e967-e325-11ce-bfc1-08002be10318}"
#define CLS_BLUETOOTH L"{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}"
#define CLS_USB       L"{36fc9e60-c465-11cf-8056-444553540000}"

/* Class GUID before bind, in the device's hardware key */
#define SAVED_CLASS   L"WinusbSwitchClassGUID"

#define PENDING_MSG "replug the device (or restart Windows) to finish"

/* DEVPKEY_Device_DriverDesc, _DriverInfPath, _BusReportedDeviceDesc */
static const DEVPROPKEY PK_DRIVERDESC = {
    { 0xa8b865dd, 0x2e3d, 0x4094,
      { 0xad, 0x97, 0xe5, 0x93, 0xa7, 0x0c, 0x75, 0xd6 } }, 4 };
static const DEVPROPKEY PK_INFPATH = {
    { 0xa8b865dd, 0x2e3d, 0x4094,
      { 0xad, 0x97, 0xe5, 0x93, 0xa7, 0x0c, 0x75, 0xd6 } }, 5 };
static const DEVPROPKEY PK_BUSDESC = {
    { 0x540b947e, 0x8b40, 0x45bc,
      { 0xa8, 0xa2, 0x6a, 0x0b, 0x89, 0x4c, 0xbd, 0xa2 } }, 4 };

#define WINUSB_HWID L"USB\\MS_COMP_WINUSB"

/* GUID_DEVINTERFACE_USB_HUB */
static const GUID HUB_INTERFACE = {
    0xf18a0e88, 0xc30c, 0x11d0,
    { 0x88, 0x15, 0x00, 0xa0, 0xc9, 0x06, 0xbe, 0xd8 } };

/* ---- output ------------------------------------------------------------ */

static FILE *out_file;

typedef struct {
    char *p;
    size_t n, cap;
} Buf;

static void oom(void)
{
    static const char msg[] = "{\"ok\":false,\"error\":\"out of memory\"}\n";
    fputs(msg, stdout);
    if (out_file) {
        fputs(msg, out_file);
        fclose(out_file);
    }
    exit(EXIT_FAILED);
}

static void b_put(Buf *b, const char *s, size_t len)
{
    if (b->n + len + 1 > b->cap) {
        size_t c = b->cap ? b->cap : 256;
        char *q;
        while (c < b->n + len + 1) {
            c *= 2;
        }
        q = realloc(b->p, c);
        if (!q) {
            oom();
        }
        b->p = q;
        b->cap = c;
    }
    memcpy(b->p + b->n, s, len);
    b->n += len;
    b->p[b->n] = 0;
}

static void b_str(Buf *b, const char *s)
{
    b_put(b, s, strlen(s));
}

static void b_jstr(Buf *b, const char *s)
{
    char t[8];

    b_put(b, "\"", 1);
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') {
            t[0] = '\\';
            t[1] = (char)c;
            b_put(b, t, 2);
        } else if (c < 0x20) {
            snprintf(t, sizeof t, "\\u%04x", c);
            b_put(b, t, 6);
        } else {
            b_put(b, (const char *)&c, 1);
        }
    }
    b_put(b, "\"", 1);
}

static char *utf8(const wchar_t *w)
{
    int n;
    char *s;

    if (!w) {
        w = L"";
    }
    n = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
    if (n <= 0) {
        n = 1;
        w = L"";
    }
    s = malloc((size_t)n);
    if (!s) {
        oom();
    }
    if (WideCharToMultiByte(CP_UTF8, 0, w, -1, s, n, NULL, NULL) <= 0) {
        s[0] = 0;
    }
    return s;
}

/* One JSON object */
typedef struct {
    Buf b;
    int fields;
} J;

static void j_open(J *j)
{
    memset(j, 0, sizeof *j);
    b_str(&j->b, "{");
}

static void j_key(J *j, const char *k)
{
    if (j->fields++) {
        b_str(&j->b, ",");
    }
    b_jstr(&j->b, k);
    b_str(&j->b, ":");
}

static void j_s(J *j, const char *k, const char *v)
{
    j_key(j, k);
    b_jstr(&j->b, v ? v : "");
}

static void j_w(J *j, const char *k, const wchar_t *v)
{
    char *s = utf8(v);
    j_s(j, k, s);
    free(s);
}

static void j_i(J *j, const char *k, long long v)
{
    char t[32];
    snprintf(t, sizeof t, "%lld", v);
    j_key(j, k);
    b_str(&j->b, t);
}

static void j_b(J *j, const char *k, int v)
{
    j_key(j, k);
    b_str(&j->b, v ? "true" : "false");
}

static void j_raw(J *j, const char *k, const char *json)
{
    j_key(j, k);
    b_str(&j->b, json);
}

static void j_id(J *j, unsigned vid, unsigned pid)
{
    char t[16];
    snprintf(t, sizeof t, "%04x:%04x", vid & 0xffff, pid & 0xffff);
    j_s(j, "id", t);
}

/* Close into a string owned by the caller. */
static char *j_take(J *j)
{
    b_str(&j->b, "}");
    return j->b.p;
}

/* key: {"code":"0x...","message":"..."} */
static void j_err(J *j, const char *key, DWORD code)
{
    wchar_t *msg = NULL;
    DWORD fm = code;
    char t[16];
    J e;
    char *s;

    j_open(&e);
    snprintf(t, sizeof t, "0x%08lx", (unsigned long)code);
    j_s(&e, "code", t);
    /* SetupAPI errors (0xE000xxxx) have text under their HRESULT form */
    if ((code & 0xE0000000u) == 0xE0000000u) {
        fm = (code & 0xFFFFu) | (15u << 16) | 0x80000000u;
    }
    if (FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER |
                       FORMAT_MESSAGE_FROM_SYSTEM |
                       FORMAT_MESSAGE_IGNORE_INSERTS,
                       NULL, fm, 0, (LPWSTR)&msg, 0, NULL) && msg) {
        size_t n = wcslen(msg);
        while (n && (msg[n - 1] == L'\r' || msg[n - 1] == L'\n' ||
                     msg[n - 1] == L' ' || msg[n - 1] == L'.')) {
            msg[--n] = 0;
        }
        j_w(&e, "message", msg);
        LocalFree(msg);
    }
    s = j_take(&e);
    j_raw(j, key, s);
    free(s);
}

/* Close, print one line, free. */
static void j_print(J *j)
{
    b_str(&j->b, "}\n");
    fwrite(j->b.p, 1, j->b.n, stdout);
    if (out_file) {
        fwrite(j->b.p, 1, j->b.n, out_file);
        fflush(out_file);
    }
    fflush(stdout);
    free(j->b.p);
    j->b.p = NULL;
}

/* ---- strings ----------------------------------------------------------- */

static const wchar_t *wcsistr(const wchar_t *h, const wchar_t *n)
{
    size_t k = wcslen(n);
    for (; *h; h++) {
        if (_wcsnicmp(h, n, k) == 0) {
            return h;
        }
    }
    return NULL;
}

static int msz_has_prefix(const wchar_t *m, const wchar_t *prefix)
{
    size_t k = wcslen(prefix);
    if (!m) {
        return 0;
    }
    for (; *m; m += wcslen(m) + 1) {
        if (_wcsnicmp(m, prefix, k) == 0) {
            return 1;
        }
    }
    return 0;
}

static int hex4(const wchar_t *s, unsigned *v)
{
    unsigned r = 0;
    int i;

    for (i = 0; i < 4; i++) {
        wchar_t c = s[i];
        r <<= 4;
        if (c >= L'0' && c <= L'9') {
            r |= (unsigned)(c - L'0');
        } else if (c >= L'a' && c <= L'f') {
            r |= (unsigned)(c - L'a' + 10);
        } else if (c >= L'A' && c <= L'F') {
            r |= (unsigned)(c - L'A' + 10);
        } else {
            return 0;
        }
    }
    *v = r;
    return 1;
}

/*
 * 1 when the hardware IDs name a whole USB device: USB\VID_v&PID_p with an
 * optional &REV_r, and no &MI_xx (a function of a composite device).
 */
static int usb_ids(const wchar_t *hwids, unsigned *vid, unsigned *pid)
{
    const wchar_t *s;

    if (!hwids) {
        return 0;
    }
    for (s = hwids; *s; s += wcslen(s) + 1) {
        if (wcsistr(s, L"&MI_")) {
            return 0;
        }
    }
    for (s = hwids; *s; s += wcslen(s) + 1) {
        if (_wcsnicmp(s, L"USB\\VID_", 8) != 0 || !hex4(s + 8, vid) ||
            _wcsnicmp(s + 12, L"&PID_", 5) != 0 || !hex4(s + 17, pid)) {
            continue;
        }
        if (s[21] == 0 || _wcsnicmp(s + 21, L"&REV_", 5) == 0) {
            return 1;
        }
    }
    return 0;
}

static int parse_id(const wchar_t *s, unsigned *vid, unsigned *pid)
{
    return wcslen(s) == 9 && hex4(s, vid) && s[4] == L':' &&
           hex4(s + 5, pid);
}

static int is_winusb(const wchar_t *service)
{
    return _wcsicmp(service, L"WinUSB") == 0;
}

/* ---- device properties ------------------------------------------------- */

/* REG_SZ / REG_MULTI_SZ SetupAPI property, double-NUL terminated; free() */
static wchar_t *dev_prop(HDEVINFO h, SP_DEVINFO_DATA *d, DWORD prop)
{
    DWORD type = 0, need = 0;
    BYTE *buf;

    if (!SetupDiGetDeviceRegistryPropertyW(h, d, prop, &type, NULL, 0, &need)
        && GetLastError() != ERROR_INSUFFICIENT_BUFFER) {
        return NULL;
    }
    if (need == 0) {
        return NULL;
    }
    buf = calloc(need + 2 * sizeof(wchar_t), 1);
    if (!buf) {
        oom();
    }
    if (!SetupDiGetDeviceRegistryPropertyW(h, d, prop, &type, buf, need,
                                           NULL) ||
        (type != REG_SZ && type != REG_MULTI_SZ && type != REG_EXPAND_SZ)) {
        free(buf);
        return NULL;
    }
    return (wchar_t *)buf;
}

static void dev_prop_into(HDEVINFO h, SP_DEVINFO_DATA *d, DWORD prop,
                          wchar_t *out, size_t cch)
{
    wchar_t *v = dev_prop(h, d, prop);
    out[0] = 0;
    if (v) {
        wcsncpy(out, v, cch - 1);
        out[cch - 1] = 0;
        free(v);
    }
}

static void dev_pkey_into(HDEVINFO h, SP_DEVINFO_DATA *d, const DEVPROPKEY *k,
                          wchar_t *out, size_t cch)
{
    DEVPROPTYPE t = 0;

    if (!SetupDiGetDevicePropertyW(h, d, k, &t, (PBYTE)out,
                                   (DWORD)(cch * sizeof(wchar_t)), NULL, 0) ||
        t != DEVPROP_TYPE_STRING) {
        out[0] = 0;
    }
    out[cch - 1] = 0;
}

/* Same as dev_prop, by devnode; free() */
static wchar_t *cm_prop(DEVINST dn, ULONG prop)
{
    ULONG type = 0, len = 0;
    BYTE *buf;

    if (CM_Get_DevNode_Registry_PropertyW(dn, prop, &type, NULL, &len, 0) !=
        CR_BUFFER_SMALL || len == 0) {
        return NULL;
    }
    buf = calloc(len + 2 * sizeof(wchar_t), 1);
    if (!buf) {
        oom();
    }
    if (CM_Get_DevNode_Registry_PropertyW(dn, prop, &type, buf, &len, 0) !=
        CR_SUCCESS) {
        free(buf);
        return NULL;
    }
    return (wchar_t *)buf;
}

static int dn_present(DEVINST dn, ULONG *problem)
{
    ULONG st = 0, pr = 0;
    if (CM_Get_DevNode_Status(&st, &pr, dn, 0) != CR_SUCCESS) {
        if (problem) {
            *problem = 0;
        }
        return 0;
    }
    if (problem) {
        *problem = (st & DN_HAS_PROBLEM) ? pr : 0;
    }
    return 1;
}

/* Device info set holding one device; destroy with SetupDiDestroyDeviceInfoList */
static HDEVINFO open_dev(const wchar_t *inst, SP_DEVINFO_DATA *d)
{
    HDEVINFO h = SetupDiCreateDeviceInfoList(NULL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        return h;
    }
    d->cbSize = sizeof *d;
    if (!SetupDiOpenDeviceInfoW(h, inst, NULL, 0, d)) {
        DWORD e = GetLastError();
        SetupDiDestroyDeviceInfoList(h);
        SetLastError(e);
        return INVALID_HANDLE_VALUE;
    }
    return h;
}

typedef struct {
    wchar_t inst[MAX_DEVICE_ID_LEN];
    DEVINST dn;
    unsigned vid, pid;
    int present;
} Dev;

/* USB devices (not functions); present only, or also absent ones; free() */
static int enum_usb(int present_only, Dev **out)
{
    HDEVINFO h;
    SP_DEVINFO_DATA d;
    Dev *v = NULL;
    int n = 0, cap = 0;
    DWORD i;

    *out = NULL;
    h = SetupDiGetClassDevsW(NULL, L"USB", NULL,
                             DIGCF_ALLCLASSES |
                             (present_only ? DIGCF_PRESENT : 0));
    if (h == INVALID_HANDLE_VALUE) {
        return -1;
    }
    d.cbSize = sizeof d;
    for (i = 0; SetupDiEnumDeviceInfo(h, i, &d); i++) {
        wchar_t *hw = dev_prop(h, &d, SPDRP_HARDWAREID);
        unsigned vid = 0, pid = 0;
        int ok = usb_ids(hw, &vid, &pid);
        Dev *e;

        free(hw);
        if (!ok) {
            continue;
        }
        if (n == cap) {
            Dev *q;
            cap = cap ? cap * 2 : 16;
            q = realloc(v, (size_t)cap * sizeof *v);
            if (!q) {
                oom();
            }
            v = q;
        }
        e = &v[n];
        memset(e, 0, sizeof *e);
        if (!SetupDiGetDeviceInstanceIdW(h, &d, e->inst, MAX_DEVICE_ID_LEN,
                                         NULL)) {
            continue;
        }
        e->dn = d.DevInst;
        e->vid = vid;
        e->pid = pid;
        e->present = dn_present(d.DevInst, NULL);
        n++;
    }
    SetupDiDestroyDeviceInfoList(h);
    *out = v;
    return n;
}

/*
 * The speed the device runs at, from its port on the parent hub
 * (IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX, and _V2 for SuperSpeed):
 * "low", "full", "high", "super", or "" when unknown.
 */
static const char *usb_speed(HDEVINFO h, SP_DEVINFO_DATA *d)
{
    union {
        USB_NODE_CONNECTION_INFORMATION_EX ci;
        BYTE raw[sizeof(USB_NODE_CONNECTION_INFORMATION_EX) +
                 32 * sizeof(USB_PIPE_INFO)];
    } u;
    USB_NODE_CONNECTION_INFORMATION_EX_V2 v2;
    wchar_t hub_id[MAX_DEVICE_ID_LEN], *ifs;
    DEVINST parent;
    DWORD port = 0, type = 0, got = 0;
    ULONG len = 0;
    HANDLE hub;
    const char *r = "";

    if (!SetupDiGetDeviceRegistryPropertyW(h, d, SPDRP_ADDRESS, &type,
                                           (PBYTE)&port, sizeof port, NULL) ||
        type != REG_DWORD || port == 0) {
        return "";
    }
    if (CM_Get_Parent(&parent, d->DevInst, 0) != CR_SUCCESS ||
        CM_Get_Device_IDW(parent, hub_id, MAX_DEVICE_ID_LEN, 0) !=
        CR_SUCCESS) {
        return "";
    }
    if (CM_Get_Device_Interface_List_SizeW(&len, (LPGUID)&HUB_INTERFACE,
                                           hub_id,
                                           CM_GET_DEVICE_INTERFACE_LIST_PRESENT)
        != CR_SUCCESS || len < 2) {
        return "";
    }
    ifs = calloc(len + 1, sizeof(wchar_t));
    if (!ifs) {
        oom();
    }
    if (CM_Get_Device_Interface_ListW((LPGUID)&HUB_INTERFACE, hub_id, ifs,
                                      len,
                                      CM_GET_DEVICE_INTERFACE_LIST_PRESENT) !=
        CR_SUCCESS || !ifs[0]) {
        free(ifs);
        return "";
    }
    hub = CreateFileW(ifs, GENERIC_WRITE, FILE_SHARE_WRITE, NULL,
                      OPEN_EXISTING, 0, NULL);
    free(ifs);
    if (hub == INVALID_HANDLE_VALUE) {
        return "";
    }
    memset(&u, 0, sizeof u);
    u.ci.ConnectionIndex = port;
    if (DeviceIoControl(hub, IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX,
                        &u, sizeof u, &u, sizeof u, &got, NULL) &&
        u.ci.ConnectionStatus == DeviceConnected) {
        switch (u.ci.Speed) {
        case UsbLowSpeed:
            r = "low";
            break;
        case UsbFullSpeed:
            r = "full";
            break;
        case UsbHighSpeed:
            r = "high";
            break;
        case UsbSuperSpeed:
            r = "super";
            break;
        }
        /* _EX reports at most high speed */
        memset(&v2, 0, sizeof v2);
        v2.ConnectionIndex = port;
        v2.Length = sizeof v2;
        v2.SupportedUsbProtocols.ul = 0x4;        /* Usb300 */
        if (u.ci.Speed == UsbHighSpeed &&
            DeviceIoControl(hub,
                            IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2,
                            &v2, sizeof v2, &v2, sizeof v2, &got, NULL) &&
            (v2.Flags.ul & 0x5)) {   /* operating at SuperSpeed or SS+ */
            r = "super";
        }
    }
    CloseHandle(hub);
    return r;
}

typedef struct {
    wchar_t desc[256], product[256], drvdesc[256];
    wchar_t cls[64], clsguid[64], service[64], inf[MAX_PATH];
    int composite, present;
    const char *speed;
    ULONG problem;
} Info;

static int get_info(const wchar_t *inst, Info *in)
{
    SP_DEVINFO_DATA d;
    HDEVINFO h;
    wchar_t *compat;

    memset(in, 0, sizeof *in);
    h = open_dev(inst, &d);
    if (h == INVALID_HANDLE_VALUE) {
        return 0;
    }
    dev_prop_into(h, &d, SPDRP_FRIENDLYNAME, in->desc, 256);
    if (!in->desc[0]) {
        dev_prop_into(h, &d, SPDRP_DEVICEDESC, in->desc, 256);
    }
    dev_pkey_into(h, &d, &PK_BUSDESC, in->product, 256);
    dev_pkey_into(h, &d, &PK_DRIVERDESC, in->drvdesc, 256);
    dev_pkey_into(h, &d, &PK_INFPATH, in->inf, MAX_PATH);
    dev_prop_into(h, &d, SPDRP_CLASS, in->cls, 64);
    dev_prop_into(h, &d, SPDRP_CLASSGUID, in->clsguid, 64);
    dev_prop_into(h, &d, SPDRP_SERVICE, in->service, 64);
    compat = dev_prop(h, &d, SPDRP_COMPATIBLEIDS);
    in->composite = msz_has_prefix(compat, L"USB\\COMPOSITE") &&
                    !_wcsicmp(in->service, L"usbccgp");
    free(compat);
    in->present = dn_present(d.DevInst, &in->problem);
    in->speed = in->present ? usb_speed(h, &d) : "";
    SetupDiDestroyDeviceInfoList(h);
    return 1;
}

static void j_info(J *j, const Info *in)
{
    j_w(j, "description", in->desc);
    j_w(j, "product", in->product);
    j_w(j, "class", in->cls);
    j_w(j, "class_guid", in->clsguid);
    j_w(j, "service", in->service);
    j_w(j, "inf", in->inf);
    j_w(j, "driver", in->drvdesc);
    j_s(j, "speed", in->speed);
    j_b(j, "composite", in->composite);
    j_b(j, "winusb", is_winusb(in->service));
    j_i(j, "problem", (long long)in->problem);
}

/* ---- what may never be switched ---------------------------------------- */

typedef struct {
    const char *refuse;
    Buf funcs;
    int nfuncs;
} Walk;

static void refuse(Walk *w, const char *why)
{
    if (!w->refuse) {
        w->refuse = why;
    }
}

/* The node and everything below it: keyboards, mice, hubs, Bluetooth, disks */
static void walk(DEVINST dn, int depth, Walk *w)
{
    wchar_t *guid, *svc, *compat;
    DEVINST child, next;

    if (depth > 0) {
        /* A whole USB device below a hub is not a function of this one */
        wchar_t *hw = cm_prop(dn, CM_DRP_HARDWAREID);
        unsigned v, p;
        int other = usb_ids(hw, &v, &p);
        free(hw);
        if (other) {
            return;
        }
    }
    guid = cm_prop(dn, CM_DRP_CLASSGUID);
    svc = cm_prop(dn, CM_DRP_SERVICE);
    compat = cm_prop(dn, CM_DRP_COMPATIBLEIDS);

    if (guid) {
        if (!_wcsicmp(guid, CLS_KEYBOARD) || !_wcsicmp(guid, CLS_MOUSE)) {
            refuse(w, "keyboard or mouse");
        } else if (!_wcsicmp(guid, CLS_BLUETOOTH)) {
            refuse(w, "bluetooth");
        } else if (!_wcsicmp(guid, CLS_DISK)) {
            refuse(w, "disk");
        }
    }
    if (msz_has_prefix(compat, L"USB\\Class_03&SubClass_01&Prot_01") ||
        msz_has_prefix(compat, L"USB\\Class_03&SubClass_01&Prot_02")) {
        refuse(w, "keyboard or mouse");
    }
    if (msz_has_prefix(compat, L"USB\\Class_09") ||
        msz_has_prefix(compat, L"USB\\DevClass_09") ||
        (svc && (!_wcsicmp(svc, L"usbhub") || !_wcsicmp(svc, L"USBHUB3")))) {
        refuse(w, "hub");
    }
    if (msz_has_prefix(compat, L"USB\\Class_E0&SubClass_01&Prot_01")) {
        refuse(w, "bluetooth");
    }
    if (depth == 1) {
        J f;
        wchar_t *cls = cm_prop(dn, CM_DRP_CLASS);
        char *s;

        j_open(&f);
        j_w(&f, "class", cls);
        j_w(&f, "service", svc);
        s = j_take(&f);
        b_str(&w->funcs, w->nfuncs++ ? "," : "");
        b_str(&w->funcs, s);
        free(s);
        free(cls);
    }
    free(guid);
    free(svc);
    free(compat);

    if (depth >= 8) {
        return;
    }
    if (CM_Get_Child(&child, dn, 0) != CR_SUCCESS) {
        return;
    }
    for (;;) {
        walk(child, depth + 1, w);
        if (CM_Get_Sibling(&next, child, 0) != CR_SUCCESS) {
            break;
        }
        child = next;
    }
}

/* Refusal reason or NULL; functions as a JSON array, free() */
static const char *check(DEVINST dn, char **funcs)
{
    Walk w;

    memset(&w, 0, sizeof w);
    b_str(&w.funcs, "[");
    walk(dn, 0, &w);
    b_str(&w.funcs, "]");
    if (funcs) {
        *funcs = w.funcs.p;
    } else {
        free(w.funcs.p);
    }
    return w.refuse;
}

/* ---- list -------------------------------------------------------------- */

static int cmd_list(void)
{
    Dev *v;
    int n = enum_usb(1, &v), i, k;

    if (n < 0) {
        DWORD e = GetLastError();
        J j;
        j_open(&j);
        j_s(&j, "op", "list");
        j_b(&j, "ok", 0);
        j_s(&j, "error", "cannot enumerate USB devices");
        j_err(&j, "detail", e);
        j_print(&j);
        return EXIT_FAILED;
    }
    for (i = 0; i < n; i++) {
        J j;
        Info in;
        char *funcs = NULL;
        const char *why;
        int same = 0;

        for (k = 0; k < n; k++) {
            if (v[k].vid == v[i].vid && v[k].pid == v[i].pid) {
                same++;
            }
        }
        get_info(v[i].inst, &in);
        why = check(v[i].dn, &funcs);
        if (!why && same > 1) {
            why = "ambiguous";
        }
        j_open(&j);
        j_id(&j, v[i].vid, v[i].pid);
        j_w(&j, "instance", v[i].inst);
        j_info(&j, &in);
        j_raw(&j, "functions", funcs);
        j_s(&j, "refuse", why ? why : "");
        j_print(&j);
        free(funcs);
    }
    free(v);
    return EXIT_DONE;
}

/* ---- shared by bind and unbind ----------------------------------------- */

static int is_elevated(void)
{
    HANDLE tok = NULL;
    TOKEN_ELEVATION e;
    DWORD len = 0;
    int r = 0;

    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &tok)) {
        return 0;
    }
    if (GetTokenInformation(tok, TokenElevation, &e, sizeof e, &len)) {
        r = e.TokenIsElevated != 0;
    }
    CloseHandle(tok);
    return r;
}

static FARPROC newdev_fn(const char *name)
{
    static HMODULE m;
    if (!m) {
        m = LoadLibraryExW(L"newdev.dll", NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
    }
    return m ? GetProcAddress(m, name) : NULL;
}

static int finish(J *j, int code)
{
    j_print(j);
    return code;
}

static int fail(J *j, const char *msg, DWORD err)
{
    j_b(j, "ok", 0);
    j_s(j, "error", msg);
    if (err) {
        j_err(j, "detail", err);
    }
    return finish(j, EXIT_FAILED);
}

/*
 * The one present device with this id. 0 and a filled Dev, or an exit code
 * after writing the reason into j. Refusals apply.
 */
static int find_target(unsigned vid, unsigned pid, Dev *out, J *j)
{
    Dev *v;
    int n = enum_usb(1, &v), i, hits = 0;
    const char *why;
    Buf list = { 0 };

    if (n < 0) {
        return fail(j, "cannot enumerate USB devices", GetLastError());
    }
    b_str(&list, "[");
    for (i = 0; i < n; i++) {
        if (v[i].vid == vid && v[i].pid == pid) {
            char *s = utf8(v[i].inst);
            if (hits++) {
                b_str(&list, ",");
            }
            b_jstr(&list, s);
            free(s);
            *out = v[i];
        }
    }
    b_str(&list, "]");
    free(v);
    if (hits == 0) {
        free(list.p);
        return fail(j, "no such device present", 0);
    }
    if (hits > 1) {
        j_b(j, "ok", 0);
        j_s(j, "refuse", "ambiguous");
        j_raw(j, "instances", list.p);
        free(list.p);
        return finish(j, EXIT_REFUSED);
    }
    free(list.p);
    j_w(j, "instance", out->inst);
    why = check(out->dn, NULL);
    if (why) {
        j_b(j, "ok", 0);
        j_s(j, "refuse", why);
        return finish(j, EXIT_REFUSED);
    }
    return 0;
}

static void rescan(DEVINST parent)
{
    DEVINST root;

    if (parent) {
        CM_Reenumerate_DevNode(parent, CM_REENUMERATE_SYNCHRONOUS);
    }
    if (CM_Locate_DevNodeW(&root, NULL, CM_LOCATE_DEVNODE_NORMAL) ==
        CR_SUCCESS) {
        CM_Reenumerate_DevNode(root, CM_REENUMERATE_SYNCHRONOUS);
    }
}

static const char *veto_text(int t)
{
    static const char *n[] = {
        "unknown", "legacy device", "pending close", "windows app",
        "windows service", "outstanding open", "device", "driver",
        "illegal device request", "insufficient power", "non-disableable",
        "legacy driver", "insufficient rights", "already removed",
    };
    return t >= 0 && t < (int)(sizeof n / sizeof n[0]) ? n[t] : "unknown";
}

/*
 * Stop the device and its functions as "safely remove" does. 0 done;
 * otherwise an exit code after writing the reason into j. A veto (an open
 * handle, usually) changes nothing.
 */
static int quiesce(DEVINST dn, J *j)
{
    PNP_VETO_TYPE vt = PNP_VetoTypeUnknown;
    wchar_t name[MAX_PATH + 2];
    CONFIGRET cr;
    char t[16];

    /* May come back unterminated or as a multi-sz; the first string only */
    memset(name, 0, sizeof name);
    cr = CM_Query_And_Remove_SubTreeW(dn, &vt, name, MAX_PATH,
                                      CM_REMOVE_UI_NOT_OK);
    if (cr == CR_SUCCESS) {
        return 0;
    }
    j_b(j, "ok", 0);
    if (cr == CR_REMOVE_VETOED) {
        name[MAX_PATH] = 0;
        j_s(j, "refuse", "in use");
        j_i(j, "veto_type", (long long)vt);
        j_s(j, "veto", veto_text((int)vt));
        j_w(j, "veto_name", name);
        j_s(j, "error", "device in use: quit the program using it first "
                        "(QEMU, Camera app, ...)");
        return finish(j, EXIT_INUSE);
    }
    snprintf(t, sizeof t, "0x%02lx", (unsigned long)cr);
    j_s(j, "error", "cannot stop device");
    j_s(j, "cr", t);
    return finish(j, EXIT_FAILED);
}

/* Start a quiesced device again; 1 once it is present. */
static int restart(DEVINST dn)
{
    DEVINST parent = 0;
    int t;

    CM_Get_Parent(&parent, dn, 0);
    CM_Setup_DevNode(dn, CM_SETUP_DEVNODE_READY);
    for (t = 0; t < 40; t++) {
        if (dn_present(dn, NULL)) {
            return 1;
        }
        if (t == 20) {
            rescan(parent);
        }
        Sleep(250);
    }
    return 0;
}

static void save_class(HDEVINFO h, SP_DEVINFO_DATA *d, const wchar_t *guid)
{
    HKEY k = SetupDiCreateDevRegKeyW(h, d, DICS_FLAG_GLOBAL, 0, DIREG_DEV,
                                     NULL, NULL);
    if (k == INVALID_HANDLE_VALUE) {
        return;
    }
    RegSetValueExW(k, SAVED_CLASS, 0, REG_SZ, (const BYTE *)guid,
                   (DWORD)((wcslen(guid) + 1) * sizeof(wchar_t)));
    RegCloseKey(k);
}

static int load_class(HDEVINFO h, SP_DEVINFO_DATA *d, wchar_t *out,
                      DWORD cch)
{
    HKEY k = SetupDiOpenDevRegKey(h, d, DICS_FLAG_GLOBAL, 0, DIREG_DEV,
                                  KEY_READ);
    DWORD type = 0, len = (cch - 1) * (DWORD)sizeof(wchar_t);
    LONG r;

    memset(out, 0, cch * sizeof(wchar_t));
    if (k == INVALID_HANDLE_VALUE) {
        return 0;
    }
    r = RegQueryValueExW(k, SAVED_CLASS, NULL, &type, (BYTE *)out, &len);
    RegCloseKey(k);
    out[cch - 1] = 0;
    return r == ERROR_SUCCESS && type == REG_SZ && out[0];
}

/*
 * Put a USBDevice-class device back in its class from before bind (saved,
 * or USB for a composite device), so a devnode whose removal is deferred
 * loses the WinUSB software key and reinstalls in the right class.
 */
static void restore_class(HDEVINFO h, SP_DEVINFO_DATA *d, J *j)
{
    wchar_t cur[64], orig[64], *compat;

    dev_prop_into(h, d, SPDRP_CLASSGUID, cur, 64);
    if (_wcsicmp(cur, CLS_USBDEVICE) != 0) {
        return;
    }
    if (!load_class(h, d, orig, 64)) {
        compat = dev_prop(h, d, SPDRP_COMPATIBLEIDS);
        if (msz_has_prefix(compat, L"USB\\COMPOSITE")) {
            wcscpy(orig, CLS_USB);
        }
        free(compat);
    }
    if (!orig[0] || !_wcsicmp(orig, CLS_USBDEVICE)) {
        return;
    }
    if (SetupDiSetDeviceRegistryPropertyW(h, d, SPDRP_CLASSGUID,
                                          (const BYTE *)orig,
                                          (DWORD)((wcslen(orig) + 1) *
                                                  sizeof(wchar_t)))) {
        if (j) {
            j_w(j, "class_restored", orig);
        }
    } else if (j) {
        j_err(j, "class_restore", GetLastError());
    }
}

/*
 * Remove the devnode so Windows matches a driver afresh; rescan unless the
 * removal is deferred (*reboot set).
 */
static int remove_dev(const wchar_t *inst, J *j, BOOL *reboot, DWORD *err)
{
    DiUninstallDevice_fn uninstall =
        (DiUninstallDevice_fn)(void (*)(void))newdev_fn("DiUninstallDevice");
    SP_DEVINFO_DATA d;
    HDEVINFO h;
    DEVINST parent = 0;
    BOOL rb = FALSE;

    *err = 0;
    if (!uninstall) {
        *err = GetLastError();
        return 0;
    }
    h = open_dev(inst, &d);
    if (h == INVALID_HANDLE_VALUE) {
        *err = GetLastError();
        return 0;
    }
    if (CM_Get_Parent(&parent, d.DevInst, 0) != CR_SUCCESS) {
        parent = 0;
    }
    restore_class(h, &d, j);
    if (!uninstall(NULL, h, &d, 0, &rb)) {
        *err = GetLastError();
        SetupDiDestroyDeviceInfoList(h);
        return 0;
    }
    SetupDiDestroyDeviceInfoList(h);
    if (rb) {
        *reboot = TRUE;
        return 1;
    }
    rescan(parent);
    return 1;
}

static int wait_back(const wchar_t *inst, unsigned vid, unsigned pid,
                     Info *in, wchar_t *now, int seconds);

/* Undo a half-done bind: remove the devnode, report what Windows put back. */
static void restore(J *j, const Dev *dev, BOOL *reboot)
{
    DWORD err = 0;
    Info in;
    wchar_t now[MAX_DEVICE_ID_LEN];

    if (!remove_dev(dev->inst, j, reboot, &err)) {
        j_b(j, "restored", 0);
        j_err(j, "restore_detail", err);
        return;
    }
    if (*reboot) {
        j_b(j, "restored", 0);
        return;
    }
    j_b(j, "restored", wait_back(dev->inst, dev->vid, dev->pid, &in, now, 30));
    j_w(j, "service_after", in.service);
}

/* ok; "pending_reboot" when Windows finishes the change only on replug */
static int done(J *j, BOOL reboot)
{
    j_b(j, "reboot", reboot);
    j_b(j, "ok", 1);
    if (reboot) {
        j_s(j, "status", "pending_reboot");
        j_s(j, "message", PENDING_MSG);
        return finish(j, EXIT_REBOOT);
    }
    j_s(j, "status", "done");
    return finish(j, EXIT_DONE);
}

/* ---- bind -------------------------------------------------------------- */

/* Is this driver node from winusb.inf? Its hardware ID into hwid. */
static int winusb_node(HDEVINFO h, SP_DEVINFO_DATA *d, SP_DRVINFO_DATA_W *drv,
                       wchar_t *hwid, size_t cch)
{
    SP_DRVINFO_DETAIL_DATA_W *det;
    DWORD need = 0;
    const wchar_t *base;
    int r = 0;

    if (!SetupDiGetDriverInfoDetailW(h, d, drv, NULL, 0, &need) &&
        GetLastError() != ERROR_INSUFFICIENT_BUFFER) {
        return 0;
    }
    if (need < sizeof *det) {
        need = sizeof *det;
    }
    det = calloc(need + 2 * sizeof(wchar_t), 1);
    if (!det) {
        oom();
    }
    det->cbSize = sizeof *det;
    if (SetupDiGetDriverInfoDetailW(h, d, drv, det, need, NULL)) {
        base = wcsrchr(det->InfFileName, L'\\');
        base = base ? base + 1 : det->InfFileName;
        if (!_wcsicmp(base, L"winusb.inf")) {
            wcsncpy(hwid, det->HardwareID, cch - 1);
            hwid[cch - 1] = 0;
            r = 1;
        }
    }
    free(det);
    return r;
}

/*
 * Build a driver list of this type from winusb.inf alone and pick the
 * USB\MS_COMP_WINUSB node (any winusb.inf node if that ID is absent).
 * 1 with drv set and the list kept, 0 not found (list destroyed),
 * -1 error.
 */
static int find_winusb(HDEVINFO h, SP_DEVINFO_DATA *d, DWORD type,
                       const wchar_t *inf, SP_DRVINFO_DATA_W *drv,
                       DWORD *err)
{
    SP_DEVINSTALL_PARAMS_W ip;
    SP_DRVINFO_DATA_W cur;
    wchar_t hwid[128];
    DWORD i;
    int found = 0;

    ip.cbSize = sizeof ip;
    if (!SetupDiGetDeviceInstallParamsW(h, d, &ip)) {
        *err = GetLastError();
        return -1;
    }
    ip.Flags |= DI_ENUMSINGLEINF;
    ip.FlagsEx |= DI_FLAGSEX_ALLOWEXCLUDEDDRVS;
    wcsncpy(ip.DriverPath, inf, MAX_PATH - 1);
    ip.DriverPath[MAX_PATH - 1] = 0;
    if (!SetupDiSetDeviceInstallParamsW(h, d, &ip)) {
        *err = GetLastError();
        return -1;
    }
    if (!SetupDiBuildDriverInfoList(h, d, type)) {
        *err = GetLastError();
        return -1;
    }
    for (i = 0;; i++) {
        cur.cbSize = sizeof cur;
        if (!SetupDiEnumDriverInfoW(h, d, type, i, &cur)) {
            break;
        }
        if (winusb_node(h, d, &cur, hwid, 128)) {
            if (!found || !_wcsicmp(hwid, WINUSB_HWID)) {
                *drv = cur;
                found = 1;
            }
        }
    }
    if (!found) {
        SetupDiDestroyDriverInfoList(h, d, type);
    }
    return found;
}

static int cmd_bind(unsigned vid, unsigned pid)
{
    DiInstallDevice_fn install =
        (DiInstallDevice_fn)(void (*)(void))newdev_fn("DiInstallDevice");
    J j;
    Dev dev;
    Info before, after;
    wchar_t inf[MAX_PATH + 32];
    UINT wl;
    SP_DEVINFO_DATA d;
    SP_DRVINFO_DATA_W drv;
    HDEVINFO h;
    DWORD err = 0, type = SPDIT_COMPATDRIVER;
    BOOL reboot = FALSE, rb;
    const char *how = NULL;
    const char *step = NULL;
    int r, changed = 0;

    j_open(&j);
    j_s(&j, "op", "bind");
    j_id(&j, vid, pid);
    if (!is_elevated()) {
        j_b(&j, "ok", 0);
        j_s(&j, "error", "needs administrator: run elevated");
        return finish(&j, EXIT_NOTADMIN);
    }
    if (!install) {
        return fail(&j, "newdev.dll has no DiInstallDevice", GetLastError());
    }
    r = find_target(vid, pid, &dev, &j);
    if (r) {
        return r;
    }
    get_info(dev.inst, &before);
    j_w(&j, "service_before", before.service);
    j_w(&j, "inf_before", before.inf);
    j_w(&j, "class_before", before.cls);
    if (is_winusb(before.service)) {
        j_b(&j, "ok", 1);
        j_b(&j, "already", 1);
        j_info(&j, &before);
        return finish(&j, EXIT_DONE);
    }

    /* A held device would need a restart to change driver */
    r = quiesce(dev.dn, &j);
    if (r) {
        return r;
    }
    if (!restart(dev.dn)) {
        return fail(&j, "device stopped and did not start again: replug it",
                    0);
    }

    wl = GetWindowsDirectoryW(inf, MAX_PATH);
    if (wl == 0 || wl >= MAX_PATH) {
        return fail(&j, "no Windows directory", GetLastError());
    }
    wcscat(inf, L"\\INF\\winusb.inf");
    if (GetFileAttributesW(inf) == INVALID_FILE_ATTRIBUTES) {
        return fail(&j, "winusb.inf not found", GetLastError());
    }

    h = open_dev(dev.inst, &d);
    if (h == INVALID_HANDLE_VALUE) {
        return fail(&j, "cannot open device", GetLastError());
    }

    /* 1: compatible list (devices that report USB\MS_COMP_WINUSB) */
    r = find_winusb(h, &d, SPDIT_COMPATDRIVER, inf, &drv, &err);
    how = "compatible";
    /* 2: class list; matches when the device is already class USBDevice */
    if (r != 1) {
        type = SPDIT_CLASSDRIVER;
        r = find_winusb(h, &d, type, inf, &drv, &err);
        how = "class";
    }
    /* 3: move the device to class USBDevice, then the class list */
    if (r == 0 && _wcsicmp(before.clsguid, CLS_USBDEVICE) != 0) {
        const wchar_t *g = CLS_USBDEVICE;
        if (before.clsguid[0]) {
            save_class(h, &d, before.clsguid);
        }
        if (SetupDiSetDeviceRegistryPropertyW(h, &d, SPDRP_CLASSGUID,
                                              (const BYTE *)g,
                                              (DWORD)((wcslen(g) + 1) *
                                                      sizeof(wchar_t)))) {
            changed = 1;
            r = find_winusb(h, &d, type, inf, &drv, &err);
            how = "class-guid";
        } else {
            j_err(&j, "class_guid_change", GetLastError());
        }
    }
    /* 4: null driver (device without class settings), then the class list */
    if (r == 0) {
        rb = FALSE;
        if (install(NULL, h, &d, NULL, DIIDFLAG_INSTALLNULLDRIVER_, &rb)) {
            changed = 1;
            if (rb) {
                reboot = TRUE;
            }
            SetupDiDestroyDeviceInfoList(h);
            h = open_dev(dev.inst, &d);
            if (h == INVALID_HANDLE_VALUE) {
                err = GetLastError();
                step = "reopen after null driver";
                r = -1;
            } else {
                r = find_winusb(h, &d, type, inf, &drv, &err);
                how = "null-driver";
            }
        } else {
            j_err(&j, "null_driver", GetLastError());
        }
    }

    if (r == 1) {
        if (!SetupDiSetSelectedDriverW(h, &d, &drv)) {
            err = GetLastError();
            step = "select driver";
            r = -1;
        } else {
            rb = FALSE;
            if (!install(NULL, h, &d, &drv, 0, &rb)) {
                err = GetLastError();
                step = "install driver";
                r = -1;
            } else if (rb) {
                reboot = TRUE;
            }
        }
        SetupDiDestroyDriverInfoList(h, &d, type);
    } else if (r < 0 && !step) {
        step = "build driver list";
    }
    if (h != INVALID_HANDLE_VALUE) {
        SetupDiDestroyDeviceInfoList(h);
    }

    if (r != 1) {
        j_s(&j, "method", how);
        if (changed) {
            restore(&j, &dev, &reboot);
        }
        j_b(&j, "reboot", reboot);
        if (r == 0) {
            return fail(&j, "no WinUSB driver node for this device", 0);
        }
        return fail(&j, step, err);
    }

    get_info(dev.inst, &after);
    j_s(&j, "method", how);
    j_info(&j, &after);
    if (!is_winusb(after.service)) {
        restore(&j, &dev, &reboot);
        j_b(&j, "reboot", reboot);
        return fail(&j, "installed, but the device is not on WinUSB", 0);
    }
    return done(&j, reboot);
}

/* ---- unbind ------------------------------------------------------------ */

/* Absent devnodes with this id still bound to WinUSB; each is removed. */
static int remove_absent(unsigned vid, unsigned pid, J *j, BOOL *reboot)
{
    Dev *v;
    int n = enum_usb(0, &v), i, removed = 0;
    Buf list = { 0 };

    if (n < 0) {
        return 0;
    }
    b_str(&list, "[");
    for (i = 0; i < n; i++) {
        SP_DEVINFO_DATA d;
        SP_REMOVEDEVICE_PARAMS rp;
        SP_DEVINSTALL_PARAMS_W ip;
        HDEVINFO h;
        wchar_t svc[64];

        if (v[i].present || v[i].vid != vid || v[i].pid != pid) {
            continue;
        }
        h = open_dev(v[i].inst, &d);
        if (h == INVALID_HANDLE_VALUE) {
            continue;
        }
        dev_prop_into(h, &d, SPDRP_SERVICE, svc, 64);
        if (is_winusb(svc)) {
            memset(&rp, 0, sizeof rp);
            rp.ClassInstallHeader.cbSize = sizeof(SP_CLASSINSTALL_HEADER);
            rp.ClassInstallHeader.InstallFunction = DIF_REMOVE;
            rp.Scope = DI_REMOVEDEVICE_GLOBAL;
            if (SetupDiSetClassInstallParamsW(h, &d, &rp.ClassInstallHeader,
                                              sizeof rp) &&
                SetupDiCallClassInstaller(DIF_REMOVE, h, &d)) {
                char *s = utf8(v[i].inst);
                if (removed++) {
                    b_str(&list, ",");
                }
                b_jstr(&list, s);
                free(s);
                ip.cbSize = sizeof ip;
                if (SetupDiGetDeviceInstallParamsW(h, &d, &ip) &&
                    (ip.Flags & (DI_NEEDREBOOT | DI_NEEDRESTART))) {
                    *reboot = TRUE;
                }
            }
        }
        SetupDiDestroyDeviceInfoList(h);
    }
    b_str(&list, "]");
    j_raw(j, "removed_absent", list.p);
    free(list.p);
    free(v);
    return removed;
}

/* The device back after the rescan, with a driver other than WinUSB. */
static int wait_back(const wchar_t *inst, unsigned vid, unsigned pid,
                     Info *in, wchar_t *now, int seconds)
{
    int t;

    now[0] = 0;
    memset(in, 0, sizeof *in);
    for (t = 0; t < seconds * 2; t++) {
        Dev *v;
        int n = enum_usb(1, &v), i, pick = -1, hits = 0;

        for (i = 0; i < n; i++) {
            if (v[i].vid != vid || v[i].pid != pid) {
                continue;
            }
            hits++;
            if (pick < 0 || !_wcsicmp(v[i].inst, inst)) {
                pick = i;
            }
        }
        if (pick >= 0 && (hits == 1 || !_wcsicmp(v[pick].inst, inst))) {
            wcsncpy(now, v[pick].inst, MAX_DEVICE_ID_LEN - 1);
            now[MAX_DEVICE_ID_LEN - 1] = 0;
            if (get_info(now, in) && in->service[0] &&
                !is_winusb(in->service) && in->problem == 0) {
                free(v);
                return 1;
            }
        }
        free(v);
        Sleep(500);
    }
    return 0;
}

static int cmd_unbind(unsigned vid, unsigned pid)
{
    J j;
    Dev *v, dev;
    Info before, after;
    wchar_t now[MAX_DEVICE_ID_LEN];
    int n, i, hits = 0, r, absent;
    BOOL reboot = FALSE;
    DWORD err = 0;

    j_open(&j);
    j_s(&j, "op", "unbind");
    j_id(&j, vid, pid);
    if (!is_elevated()) {
        j_b(&j, "ok", 0);
        j_s(&j, "error", "needs administrator: run elevated");
        return finish(&j, EXIT_NOTADMIN);
    }
    if (!newdev_fn("DiUninstallDevice")) {
        return fail(&j, "newdev.dll has no DiUninstallDevice",
                    GetLastError());
    }

    n = enum_usb(1, &v);
    for (i = 0; i < n; i++) {
        if (v[i].vid == vid && v[i].pid == pid) {
            hits++;
        }
    }
    free(v);

    if (hits == 0) {
        absent = remove_absent(vid, pid, &j, &reboot);
        if (absent) {
            rescan(0);
            j_b(&j, "present", 0);
            return done(&j, reboot);
        }
        return fail(&j, "no such device present", 0);
    }

    r = find_target(vid, pid, &dev, &j);
    if (r) {
        return r;
    }
    get_info(dev.inst, &before);
    j_w(&j, "service_before", before.service);
    if (!is_winusb(before.service)) {
        j_b(&j, "ok", 0);
        j_s(&j, "refuse", "not on WinUSB");
        return finish(&j, EXIT_REFUSED);
    }
    /* Held open (QEMU still running): refuse, change nothing */
    r = quiesce(dev.dn, &j);
    if (r) {
        return r;
    }
    remove_absent(vid, pid, &j, &reboot);
    if (!remove_dev(dev.inst, &j, &reboot, &err)) {
        restart(dev.dn);
        j_b(&j, "reboot", reboot);
        return fail(&j, "cannot remove device", err);
    }
    if (reboot) {
        return done(&j, reboot);
    }
    if (!wait_back(dev.inst, vid, pid, &after, now, 30)) {
        if (now[0]) {
            j_w(&j, "instance_after", now);
            j_info(&j, &after);
        }
        j_b(&j, "reboot", reboot);
        return fail(&j, "device did not come back on a Windows driver: "
                        "replug it", 0);
    }
    j_w(&j, "instance_after", now);
    j_info(&j, &after);
    return done(&j, reboot);
}

/* ---- main -------------------------------------------------------------- */

static int usage(void)
{
    fputs("usage: winusb-switch list [--out FILE]\n"
          "       winusb-switch bind VVVV:PPPP [--out FILE]\n"
          "       winusb-switch unbind VVVV:PPPP [--out FILE]\n", stderr);
    return EXIT_USAGE;
}

static int run(int argc, wchar_t **argv)
{
    unsigned vid, pid;

    if (argc == 2 && !wcscmp(argv[1], L"list")) {
        return cmd_list();
    }
    if (argc != 3 || !parse_id(argv[2], &vid, &pid)) {
        return usage();
    }
    if (!wcscmp(argv[1], L"bind")) {
        return cmd_bind(vid, pid);
    }
    if (!wcscmp(argv[1], L"unbind")) {
        return cmd_unbind(vid, pid);
    }
    return usage();
}

int wmain(int argc, wchar_t **argv)
{
    int r;

    _setmode(_fileno(stdout), _O_BINARY);
    if (argc >= 3 && !wcscmp(argv[argc - 2], L"--out")) {
        out_file = _wfopen(argv[argc - 1], L"wb");
        if (!out_file) {
            fputs("winusb-switch: cannot write the --out file\n", stderr);
            return EXIT_USAGE;
        }
        argc -= 2;
    }
    r = run(argc, argv);
    if (out_file) {
        fclose(out_file);
    }
    return r;
}
