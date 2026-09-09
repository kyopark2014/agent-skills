---
title: "Open Application"
category: 02_apps
id: app_open
description: Launches or activates an application by name.
keywords:
  - open
  - launch
  - activate
  - app
language: applescript
argumentsPrompt: input_data.app_name (e.g. Notes, Safari, Finder)
---

```applescript
set appName to "--MCP_INPUT:app_name"
if appName is "" then set appName to "--MCP_INPUT:appName"
if appName is "" then return "error: provide input_data.app_name"
tell application appName to activate
return "Activated: " & appName
```

END_TIP
