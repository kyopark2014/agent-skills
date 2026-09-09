---
title: "Get / Set Output Volume"
category: 01_system
id: system_volume_get_set
description: Get current output volume (0-100) or set it when input_data.volume is provided.
keywords:
  - volume
  - sound
  - audio
  - mute
language: applescript
argumentsPrompt: Optional input_data.volume (0-100). Omit to only read current volume.
---

```applescript
set volText to "--MCP_INPUT:volume"
if volText is not "" then
  try
    set volNum to volText as integer
    if volNum < 0 then set volNum to 0
    if volNum > 100 then set volNum to 100
    set volume output volume volNum
    return "Output volume set to " & volNum
  on error errMsg
    return "error: " & errMsg
  end try
else
  return "output_volume=" & (output volume of (get volume settings))
end if
```

END_TIP
