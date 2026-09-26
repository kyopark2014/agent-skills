---
name: drawing-walldetector
description: >-
  drawing-devider가 만든 층 floor_original DXF에서 벽을 검출하고 빨간색 WALL
  레이어 DXF/PNG로 저장합니다. 벽 검출, wall detect, wall DXF, 평면도 벽체,
  floor_wall_original 요청 시 사용합니다.
---

# drawing-walldetector (벽 검출)

`drawing-devider`가 만든 **층 단위** `floor_original.dxf`에서 벽을 찾아 **빨간색**으로
표시한 DXF(및 검수용 PNG)를 생성한다. 기본 산출은 **`floor_wall_original.*`** 하나다.

## When to Use

- `floors/<F>/floor_original.dxf`에서 층 전체 벽을 뽑을 때
- 벽체 DXF / 빨간 벽 오버레이 / wall detect / `floor_wall_original` 요청 시

## Critical Rules

1. **입력은 층 floor_original** — `$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_original.dxf`만 사용한다. 원본 277MB DXF를 직접 돌리지 않는다. `parts/`·`floor_parts_index.json`은 **필수가 아니다**.
2. **층 구조 먼저** — `$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_original.png`로 전체 구조를 확인한다.
3. **기존 산출 게이트** — `floors/<F>/floor_wall_original.*`가 **이미 있으면** 경로·요약을 보여 주고 **계속(덮어쓰기) / 중단**을 물은 뒤, 허락이 있을 때만 진행한다.
4. **파일럿 게이트** — 다층이면 **1개 층만** `detect_walls_floor` 후 사용자 컨펌 → 나머지 층도 **층당 bash 1회**.
5. **층별 1개씩** — 한 bash에 `for FLOOR in …` 일괄·`detect_walls_all`로 전층 한 번에 돌리는 것을 기본 **금지**한다 (대용량에서 Timeout). 사용자가 일괄을 명시할 때만 `detect_walls_all` 허용.
6. **벽은 빨간색** — 출력 DXF의 `WALL` 레이어(ACI 1). 베이스 기하는 `BASE`(회색).
7. **산출 경로** — `$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_wall_original.*` (기본). 레거시 타일은 `walls/` (선택).
8. **스크립트 사용** — `$WORKING_DIR/skills/drawing-walldetector/scripts/` 로만 수행. ad-hoc 대용량 파싱 금지.
9. 응답은 **한국어**. 경로·JSON 키는 영문/숫자 유지.
10. **타일 검출(레거시)** — `parts/`가 있고 사용자가 명시한 때만 `--with-tiles`.

## Script Location

`bash` / `execute_code`의 cwd는 `artifacts/`이다. 스킬 스크립트는 `$WORKING_DIR/skills/...`로 호출하세요.
(`WORKING_DIR`는 bash 도구가 주입하는 환경변수이며, Runtime에서는 `/app`이다.)

| 스크립트 | 용도 |
| --- | --- |
| `$WORKING_DIR/skills/drawing-walldetector/scripts/detect_walls_floor.py` | **한 층** `floor_wall_original` (기본) |
| `$WORKING_DIR/skills/drawing-walldetector/scripts/detect_walls_tile.py` | 단일 DXF (레거시·선택) |
| `$WORKING_DIR/skills/drawing-walldetector/scripts/detect_walls_all.py` | 다층 일괄 (사용자 명시 시에만) |
| `$WORKING_DIR/skills/drawing-walldetector/scripts/lib_walls.py` | 평행 이중선 기반 벽 분류·DXF/PNG |

**IMPORTANT**: `skills/...` 또는 `scripts/...` 상대경로를 쓰지 마세요. cwd가 `artifacts/`라 실패합니다.  
구경로 `cde-pilot/.../skills/...` 도 쓰지 마세요.

```bash
SCRIPTS="$WORKING_DIR/skills/drawing-walldetector/scripts"
ART="$ARTIFACTS_DIR/<drawing_id>"

# 파일럿 1층 (층 전체만)
python3 "$SCRIPTS/detect_walls_floor.py" --artifacts "$ART" --floor 12F
```

로컬 개발(비 Runtime) 예:

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-walldetector/scripts
ART=/path/to/user/artifacts/<drawing_id>
```

---

## Workflow (필수 순서)

```
drawing-devider 산출물
  $ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_original.{dxf,png}
  ↓
⓪ floor_wall_original.* 존재 여부 확인 → 있으면 계속/중단 확인
  ↓
① 파일럿 층: floor_original.png 확인
  ↓
② detect_walls_floor.py --floor <FIRST>   ← bash 1회
     → floors/<F>/floor_wall_original.*
  ↓
③ 사용자 컨펌 (나머지 층)
  ↓
④ 허락 시 다음 층만 detect_walls_floor   ← 층당 1회
  ↓
⑤ walls_all_index.json / work_log 갱신 (선택)
```

### ⓪ 기존 산출 확인

```bash
ART="$ARTIFACTS_DIR/<drawing_id>"
FLOOR=12F
if [ -f "$ART/floors/$FLOOR/floor_wall_original.dxf" ]; then
  echo "EXISTING: $ART/floors/$FLOOR/floor_wall_original.*"
  ls "$ART/floors/$FLOOR"/floor_wall_original.* 2>/dev/null
  # STOP: 허락 전 detect 금지
fi
```

### 검출 개요

레이어가 `0arch` 단일인 경우가 많아, **축정렬 세그먼트의 평행 이중선(벽 두께 대역)** 으로 벽을 판정한다.

- 두께 기본: 50–420 mm
- 최소 세그먼트 길이: 500 mm
- 계단 해칭(다수 평행선) · ARC/CIRCLE(문·설비) 제외
- **H-Beam 기둥**(중첩 정사각, 변 ≤ 1.2 m)은 WALL로 유지 — 가구 사각만 제외

---

## Artifacts Layout

산출물은 **사용자 artifacts** (`$ARTIFACTS_DIR/<drawing_id>/`) 아래에만 둔다.

```text
$ARTIFACTS_DIR/<drawing_id>/
├── floors/<FLOOR>/
│   ├── floor_original.dxf / .png          # (devider) 입력 · 미리보기
│   ├── floor_wall_original.dxf / .png / _meta.json  # 층 전체 벽 (기본 산출)
│   └── floor_wall_index.json              # 층 요약
└── walls_all_index.json                   # (선택) 다층 요약
```

레거시 타일(`--with-tiles`) 시만 `walls/R*C*_walls.*` · `walls/walls_index.json` 생성.

---

## Decision Checklist

- [ ] `$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_original.dxf`가 있는가
- [ ] `floor_wall_original.*`가 이미 있으면 **계속/중단**을 물었는가
- [ ] 스크립트를 `$WORKING_DIR/skills/drawing-walldetector/scripts/...`로 호출하는가
- [ ] 파일럿 1층만 돌렸고 사용자 허락을 기다리는가
- [ ] 나머지 층도 **층당 bash 1회**인가

---

## Failure Modes

| 증상 | 대응 |
|------|------|
| `floor_original.dxf` 없음 | 먼저 `drawing-devider` `extract_2d.py --floor <F>` |
| `skills/...` / `cde-pilot/...` 경로 실패 | `$WORKING_DIR/skills/drawing-walldetector/scripts/...` 사용 |
| Timeout / 전층 일괄 실패 | `detect_walls_all` 금지 → `detect_walls_floor --floor <F>` 층당 1회 |
| 과검출·미검출 | `min_len_mm` / `thick_min_mm` / `thick_max_mm` 조정 (reference.md) |

---

## 사용자에게 전달할 내용

- 층별 `$ARTIFACTS_DIR/<drawing_id>/floors/<F>/floor_wall_original.*` 경로
- 샘플 `floor_wall_original.png` (벽=빨강)
- 파일럿 시: **나머지 층 진행 여부** / 완료 시: 층별 벽 통계

## Related

- `drawing-devider` — `floor_original` 층 추출 선행 스킬
- `scripts/lib_walls.py` — 벽 분류·렌더 구현
