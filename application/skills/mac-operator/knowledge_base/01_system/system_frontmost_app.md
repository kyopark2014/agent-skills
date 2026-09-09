---
title: "Get Frontmost Application"
category: 01_system
id: system_frontmost_app
description: Returns the name of the frontmost application.
keywords:
  - frontmost
  - active app
  - System Events
language: applescript
---

```applescript
tell application "System Events"
  set frontApp to name of first application process whose frontmost is true
end tell
return frontApp
```

END_TIP
