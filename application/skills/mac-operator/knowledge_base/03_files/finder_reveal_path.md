---
title: "Reveal Path in Finder"
category: 03_files
id: finder_reveal_path
description: Reveals a POSIX path in Finder and selects it.
keywords:
  - Finder
  - reveal
  - show in finder
  - open folder
language: applescript
argumentsPrompt: input_data.path (absolute POSIX path)
---

```applescript
set targetPath to "--MCP_INPUT:path"
if targetPath is "" then return "error: provide input_data.path"
try
  set targetAlias to POSIX file targetPath as alias
  tell application "Finder"
    reveal targetAlias
    activate
  end tell
  return "Revealed: " & targetPath
on error errMsg
  return "error: " & errMsg
end try
```

END_TIP
