/* Test-only stand-in for mscms.dll under Wine, which doesn't implement
 * WcsCreateIccProfile; Qt's qwindows.dll calls it while enumerating screens
 * and then aborts with "no screens available". Reports "no colour profile"
 * so Qt assumes sRGB. Used only by smoke-test.sh, never shipped. */
#include <stddef.h>

int __stdcall DllEntry(void *inst, unsigned long reason, void *reserved)
{
    (void)inst; (void)reason; (void)reserved;
    return 1;
}

__declspec(dllexport) void *__stdcall OpenColorProfileW(void *p, unsigned long a,
                                                       unsigned long s, unsigned long c)
{
    (void)p; (void)a; (void)s; (void)c;
    return NULL;
}

__declspec(dllexport) void *__stdcall WcsCreateIccProfile(void *h, unsigned long f)
{
    (void)h; (void)f;
    return NULL;
}

__declspec(dllexport) int __stdcall CloseColorProfile(void *h) { (void)h; return 1; }

__declspec(dllexport) int __stdcall GetColorProfileFromHandle(void *h, void *buf, unsigned long *n)
{
    (void)h; (void)buf; (void)n;
    return 0;
}
