/* Build-time stand-in for Windows' system icuuc.dll, which Wine lacks and
 * Qt6Core.dll imports. Every converter "fails to open", so Qt falls back to
 * its built-in codecs. Only installed into the Wine prefix, never shipped:
 * real Windows 10/11 provides icuuc.dll in System32. */
#include <stddef.h>
#define STUB(name) __declspec(dllexport) void *name(void) { return NULL; }

/* linked with -nostdlib, so this is the whole DLL entry point */
int __stdcall DllEntry(void *inst, unsigned long reason, void *reserved)
{
    (void)inst; (void)reason; (void)reserved;
    return 1;
}

__declspec(dllexport) void *ucnv_open(const char *name, int *err)
{
    (void)name;
    if (err)
        *err = 4; /* U_FILE_ACCESS_ERROR */
    return NULL;
}

__declspec(dllexport) int ucnv_countAvailable(void) { return 0; }

STUB(UCNV_FROM_U_CALLBACK_SUBSTITUTE)
STUB(UCNV_TO_U_CALLBACK_SUBSTITUTE)
STUB(ucnv_cbFromUWriteUChars)
STUB(ucnv_cbToUWriteUChars)
STUB(ucnv_close)
STUB(ucnv_fromUCountPending)
STUB(ucnv_fromUnicode)
STUB(ucnv_getAvailableName)
STUB(ucnv_getFromUCallBack)
STUB(ucnv_getMaxCharSize)
STUB(ucnv_getName)
STUB(ucnv_getStandardName)
STUB(ucnv_getToUCallBack)
STUB(ucnv_reset)
STUB(ucnv_setFromUCallBack)
STUB(ucnv_setToUCallBack)
STUB(ucnv_toUCountPending)
STUB(ucnv_toUnicode)
