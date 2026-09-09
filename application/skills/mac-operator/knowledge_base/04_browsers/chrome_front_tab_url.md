---
title: "Chrome Front Tab URL"
category: 04_browsers
id: chrome_front_tab_url
description: Returns the URL of the front Google Chrome tab.
keywords:
  - Chrome
  - URL
  - front tab
  - browser
language: applescript
---

```applescript
tell application "Google Chrome"
  if not (exists front window) then return "error: no Chrome window"
  return URL of active tab of front window
end tell
```

END_TIP
