---
title: "Click Menu Item"
category: 05_ui
id: ui_click_menu_item
description: Clicks a menu bar item path in the frontmost application (menu > item).
keywords:
  - menu
  - click
  - System Events
  - menubar
language: applescript
argumentsPrompt: input_data.menu_bar_item and input_data.menu_item (optional input_data.app_name)
notes: Requires Accessibility + Automation for System Events / target app.
---

```applescript
set menuBarName to "--MCP_INPUT:menu_bar_item"
set menuItemName to "--MCP_INPUT:menu_item"
set appName to "--MCP_INPUT:app_name"
if menuBarName is "" then return "error: provide input_data.menu_bar_item"
if menuItemName is "" then return "error: provide input_data.menu_item"

tell application "System Events"
  if appName is "" then
    set appName to name of first application process whose frontmost is true
  end if
  tell process appName
    click menu item menuItemName of menu menuBarName of menu bar 1
  end tell
end tell
return "Clicked " & appName & " → " & menuBarName & " → " & menuItemName
```

END_TIP
