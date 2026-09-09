---
title: "Get macOS System Info"
category: 01_system
id: system_info_basic
description: Returns computer name, user, and macOS version string.
keywords:
  - system info
  - version
  - hostname
  - whoami
language: applescript
---

```applescript
set computerName to computer name of (system info)
set userName to short user name of (system info)
set osVersion to system version of (system info)
return "computer=" & computerName & linefeed & "user=" & userName & linefeed & "macos=" & osVersion
```

END_TIP
