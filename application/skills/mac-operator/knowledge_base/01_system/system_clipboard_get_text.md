---
title: "Get Clipboard Text"
category: 01_system
id: system_clipboard_get_text
description: Returns the current clipboard contents as text.
keywords:
  - clipboard
  - pasteboard
  - copy
  - text
language: applescript
notes: Read-only. Empty clipboard may return an empty string or error text.
---

```applescript
try
  return the clipboard as text
on error errMsg
  return "error: " & errMsg
end try
```

END_TIP
