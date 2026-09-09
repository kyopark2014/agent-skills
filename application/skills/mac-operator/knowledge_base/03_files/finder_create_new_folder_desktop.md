---
title: "Create New Folder on Desktop"
category: 03_files
id: finder_create_new_folder_desktop
description: Creates a new folder on the Desktop with a given name.
keywords:
  - Finder
  - folder
  - desktop
  - create
  - mkdir
language: applescript
argumentsPrompt: input_data.folder_name (or folderName)
---

```applescript
set folderName to "--MCP_INPUT:folder_name"
if folderName is "" then set folderName to "--MCP_INPUT:folderName"
if folderName is "" then set folderName to "New Folder"

tell application "Finder"
  set desktopPath to path to desktop folder
  try
    if exists folder folderName of desktopPath then
      return "Error: A folder named '" & folderName & "' already exists on the desktop."
    end if
    set newFolder to make new folder at desktopPath with properties {name:folderName}
    return "Created folder: " & (POSIX path of (newFolder as alias))
  on error errMsg
    return "Error creating folder: " & errMsg
  end try
end tell
```

END_TIP
