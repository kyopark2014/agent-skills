---
title: "List Running Applications"
category: 02_apps
id: app_list_running
description: Lists visible running application process names.
keywords:
  - running apps
  - process list
  - System Events
language: applescript
---

```applescript
tell application "System Events"
  set appNames to name of every application process whose background only is false
end tell
set AppleScript's text item delimiters to linefeed
set outText to appNames as text
set AppleScript's text item delimiters to ""
return outText
```

END_TIP
