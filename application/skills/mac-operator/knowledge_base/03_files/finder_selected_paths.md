---
title: "Finder Selected Items Paths"
category: 03_files
id: finder_selected_paths
description: Returns POSIX paths of items currently selected in Finder.
keywords:
  - Finder
  - selection
  - path
  - files
language: applescript
---

```applescript
tell application "Finder"
  set sel to selection as alias list
  if sel is {} then return "(no Finder selection)"
  set paths to {}
  repeat with anItem in sel
    set end of paths to POSIX path of anItem
  end repeat
end tell
set AppleScript's text item delimiters to linefeed
set outText to paths as text
set AppleScript's text item delimiters to ""
return outText
```

END_TIP
