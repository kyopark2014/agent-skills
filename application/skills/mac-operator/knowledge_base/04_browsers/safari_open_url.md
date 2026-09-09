---
title: "Safari Open URL"
category: 04_browsers
id: safari_open_url
description: Opens a URL in a new Safari tab (or window).
keywords:
  - Safari
  - open URL
  - browse
language: applescript
argumentsPrompt: input_data.url
---

```applescript
set targetURL to "--MCP_INPUT:url"
if targetURL is "" then return "error: provide input_data.url"
tell application "Safari"
  activate
  if (count of windows) is 0 then
    make new document with properties {URL:targetURL}
  else
    tell front window
      set current tab to (make new tab with properties {URL:targetURL})
    end tell
  end if
end tell
return "Opened in Safari: " & targetURL
```

END_TIP
