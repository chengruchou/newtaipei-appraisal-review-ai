Sanitized previews use the bundled PDF.js renderer rather than the browser's
native PDF plugin. Every page must finish rendering before the explicit approval
checkbox becomes available; a failed page keeps approval blocked. The exact PDF
is hash-checked before its local blob URL is given to the renderer. This addresses
the real Chromium failure where a native PDF iframe never finished loading.
