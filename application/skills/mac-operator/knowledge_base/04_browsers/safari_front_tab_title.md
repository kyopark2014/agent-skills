---
title: "Safari Front Tab Title"
category: 04_browsers
id: safari_front_tab_title
description: Returns the title of the front Safari tab.
keywords:
  - Safari
  - title
  - front tab
language: applescript
---

```applescript
tell application "Safari"
  if not (exists front window) then return "error: no Safari window"
  return name of current tab of front window
end tell
```

END_TIP
