---
title: "Quit Application"
category: 02_apps
id: app_quit
description: Quits an application by name. Confirm with the user before quitting apps with unsaved work.
keywords:
  - quit
  - close app
  - exit
language: applescript
argumentsPrompt: input_data.app_name
notes: Destructive for unsaved documents — confirm first.
---

```applescript
set appName to "--MCP_INPUT:app_name"
if appName is "" then set appName to "--MCP_INPUT:appName"
if appName is "" then return "error: provide input_data.app_name"
tell application appName to quit
return "Quit requested: " & appName
```

END_TIP
