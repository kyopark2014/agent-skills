---
title: "Open Spotlight and Search"
category: 05_ui
id: ui_spotlight_search
description: Opens Spotlight (Cmd+Space) and types a search query.
keywords:
  - Spotlight
  - search
  - Cmd+Space
  - launch
language: applescript
argumentsPrompt: input_data.query
notes: Requires Accessibility. Wait briefly after opening Spotlight before typing.
---

```applescript
set queryText to "--MCP_INPUT:query"
if queryText is "" then return "error: provide input_data.query"
tell application "System Events"
  key code 49 using {command down} -- Space
end tell
delay 0.4
tell application "System Events"
  keystroke queryText
end tell
return "Spotlight query typed: " & queryText
```

END_TIP
