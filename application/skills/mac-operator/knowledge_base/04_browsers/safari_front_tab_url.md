---
title: "Safari Front Tab URL"
category: 04_browsers
id: safari_front_tab_url
description: Returns the URL of the front Safari tab.
keywords:
  - Safari
  - URL
  - front tab
  - browser
language: applescript
notes: Requires Automation permission for Safari.
---

```applescript
tell application "Safari"
  if not (exists front window) then return "error: no Safari window"
  return URL of current tab of front window
end tell
```

END_TIP
