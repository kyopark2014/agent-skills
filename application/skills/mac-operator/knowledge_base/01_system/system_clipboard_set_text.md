---
title: "Set Clipboard Text"
category: 01_system
id: system_clipboard_set_text
description: Sets the system clipboard to the provided text.
keywords:
  - clipboard
  - copy
  - set clipboard
  - pasteboard
language: applescript
argumentsPrompt: Provide text via input_data.text (or input_data.clipboard_text)
---

```applescript
set newText to "--MCP_INPUT:text"
if newText is "" then
  set newText to "--MCP_INPUT:clipboard_text"
end if
set the clipboard to newText
return "Clipboard updated (" & (length of newText) & " chars)"
```

END_TIP
