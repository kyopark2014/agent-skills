---
title: "Press Key / Shortcut"
category: 05_ui
id: ui_keystroke_shortcut
description: Presses a key with optional modifiers (command/option/control/shift).
keywords:
  - shortcut
  - hotkey
  - keystroke
  - command
language: applescript
argumentsPrompt: >-
  input_data.key (single character or special like return/escape/tab),
  optional input_data.modifiers as comma list e.g. command,shift
notes: Requires Accessibility. Special keys supported: return, enter, escape, tab, delete, space.
---

```applescript
set keyName to "--MCP_INPUT:key"
set modsText to "--MCP_INPUT:modifiers"
if keyName is "" then return "error: provide input_data.key"

set modList to {}
if modsText is not "" then
  set AppleScript's text item delimiters to ","
  set rawMods to text items of modsText
  set AppleScript's text item delimiters to ""
  repeat with m in rawMods
    set mTrim to do shell script "echo " & quoted form of (m as text) & " | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]'"
    if mTrim is "command" or mTrim is "cmd" then set end of modList to command down
    if mTrim is "option" or mTrim is "alt" then set end of modList to option down
    if mTrim is "control" or mTrim is "ctrl" then set end of modList to control down
    if mTrim is "shift" then set end of modList to shift down
  end repeat
end if

tell application "System Events"
  if keyName is "return" or keyName is "enter" then
    key code 36 using modList
  else if keyName is "escape" or keyName is "esc" then
    key code 53 using modList
  else if keyName is "tab" then
    key code 48 using modList
  else if keyName is "delete" or keyName is "backspace" then
    key code 51 using modList
  else if keyName is "space" then
    key code 49 using modList
  else
    keystroke keyName using modList
  end if
end tell
return "Pressed key=" & keyName & " modifiers=" & modsText
```

END_TIP
