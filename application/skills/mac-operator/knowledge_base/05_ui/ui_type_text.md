---
title: "Type Text via System Events"
category: 05_ui
id: ui_type_text
description: Types text into the frontmost app using System Events keystrokes.
keywords:
  - type
  - keystroke
  - System Events
  - UI
language: applescript
argumentsPrompt: input_data.text
notes: Requires Accessibility permission. Prefer clipboard paste for unicode-heavy text.
---

```applescript
set typeText to "--MCP_INPUT:text"
if typeText is "" then return "error: provide input_data.text"
tell application "System Events"
  keystroke typeText
end tell
return "Typed " & (length of typeText) & " characters"
```

END_TIP
