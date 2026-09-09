---
name: mac-operator
description: >
  Control macOS via AppleScript / JXA using the mac-operator skill CLI (and optional MCP).
  Use when the user asks to operate Finder, Safari, Notes, Freeform/Whiteboard, clipboard,
  notifications, volume, Spotlight, menus, keystrokes, open/quit apps, or any native Mac
  automation (mac 조작, AppleScript, 자동화, 파인더, 화이트보드, 클립보드, 알림). Prefer
  browser-use for heavy web DOM work and computer-use for pixel drawing / screenshot loops
  when scripting dictionaries are insufficient. For "앱 열고 도형 그리기" use mac-operator
  to open the app, then computer-use to draw.
---

# mac-operator

[macos-automator-mcp](https://github.com/steipete/macos-automator-mcp)를 참고한 **skill**입니다.
에이전트는 MCP를 따로 고르지 않아도, `bash` / `execute_code`로 아래 CLI를 실행해 macOS를 조작합니다.
(skill이 선택되면 백엔드가 mac-operator MCP 도구도 자동으로 붙입니다.)

## When to Use

- 네이티브 앱 실행/종료/활성화 (Freeform, Notes, Safari, Finder…)
- Finder 선택/폴더 생성, Safari·Chrome 탭 URL, 클립보드, 알림, 볼륨, Spotlight, 메뉴/키입력
- “맥에서 ~~ 해줘”, AppleScript/자동화 요청

## When Not to Use

- 웹 DOM 클릭·스크래핑 → `browser-use`
- 픽셀 좌표로 도형 그리기 / 스크린샷 검증 루프 → `computer-use` (앱 실행만 mac-operator)
- 단순 파일·셸만으로 충분 → `bash` / `execute_code`

## Script Location

application working directory 기준 **전체 경로**를 쓰세요.

| 스크립트 | 용도 |
| --- | --- |
| `skills/mac-operator/scripts/mac_operator.py` | doctor / tips / execute / open |

## 빠른 워크플로

```bash
# 0) 환경 점검
python skills/mac-operator/scripts/mac_operator.py doctor

# 1) tip 검색
python skills/mac-operator/scripts/mac_operator.py tips --search "open app" --limit 5

# 2) tip 실행 (앱 열기)
python skills/mac-operator/scripts/mac_operator.py open "Freeform"
# 또는
python skills/mac-operator/scripts/mac_operator.py execute --kb app_open --input app_name=Freeform

# 3) 인라인 AppleScript
python skills/mac-operator/scripts/mac_operator.py execute --script 'return "Hello from mac-operator"'
```

### Whiteboard / Freeform + 도형 그리기

1. mac-operator로 앱 실행: `open "Freeform"` (또는 Microsoft Whiteboard면 `"Microsoft Whiteboard"`)
2. 잠시 대기 후 **computer-use**로 `screenshot` → `click` / `drag`로 삼각형 그리기
3. 다시 `screenshot`으로 결과 확인

## 명령 요약

| 명령 | 설명 |
| --- | --- |
| `doctor` | macOS / osascript / KB 점검 (JSON) |
| `categories` | KB 카테고리 목록 |
| `tips [--search TERM] [--category ID] [--limit N]` | tip 검색 |
| `execute --kb ID [--input k=v ...]` | knowledge-base tip 실행 |
| `execute --script '...'` | 인라인 AppleScript/JXA |
| `execute --file /abs/path` | 스크립트 파일 실행 |
| `open "AppName"` | 앱 활성화 단축 명령 |

공통: `--json` (execute/open/doctor), `--verbose` (치환 로그·실행 스크립트 포함).

## Bundled tip 예시

| ID | 용도 |
| --- | --- |
| `app_open` / `app_quit` / `app_list_running` | 앱 제어 |
| `system_clipboard_get_text` / `system_clipboard_set_text` | 클립보드 |
| `system_display_notification` | 알림 |
| `finder_selected_paths` / `finder_create_new_folder_desktop` | Finder |
| `safari_front_tab_url` / `safari_open_url` | Safari |
| `ui_type_text` / `ui_keystroke_shortcut` / `ui_spotlight_search` | UI |

## MCP (자동 연결)

skill `mac-operator`가 선택되면 서버가 MCP도 붙여 `get_scripting_tips` / `execute_script` 도구를 쓸 수 있습니다.
CLI와 MCP 중 편한 쪽을 쓰면 됩니다. **먼저 tip을 검색한 뒤 실행**하세요.

## Permissions

호스트 앱(터미널/IDE)에 **Automation** / **Accessibility**가 필요합니다.
오류 `-1743`, `-10004`, `-1712` → 시스템 설정 → 개인정보 보호에서 허용.

## Safety

- 앱 종료·파일 삭제 등 파괴적 동작은 사용자 확인 후 실행
- 신뢰할 수 있는 스크립트만 실행

## Local knowledge base

`~/.macos-automator/knowledge_base` 또는 `LOCAL_KB_PATH`에 tip을 추가할 수 있습니다 (동일 id는 로컬이 우선).
