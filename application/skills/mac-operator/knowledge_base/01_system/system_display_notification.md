---
title: "Display Notification"
category: 01_system
id: system_display_notification
description: Shows a macOS notification banner with title and body.
keywords:
  - notification
  - alert
  - banner
  - notify
language: applescript
argumentsPrompt: input_data.title and input_data.body (subtitle optional)
---

```applescript
set notifTitle to "--MCP_INPUT:title"
set notifBody to "--MCP_INPUT:body"
set notifSubtitle to "--MCP_INPUT:subtitle"
if notifTitle is "" then set notifTitle to "mac-operator"
if notifBody is "" then set notifBody to "Notification"
if notifSubtitle is "" then
  display notification notifBody with title notifTitle
else
  display notification notifBody with title notifTitle subtitle notifSubtitle
end if
return "Notification shown"
```

END_TIP
