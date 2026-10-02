# drawing-devider 사용 예

## 0) 기존 폴더

폴더가 이미 있어도 묻지 않고 추출한다. `floor_original.*` 는 덮어쓴다.

## 1) 전 층 추출 (확인 없음)

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-devider/scripts
# Runtime: SCRIPTS="$WORKING_DIR/skills/drawing-devider/scripts"
ART="$ARTIFACTS_DIR/sk_yongin_jiwon"

python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/input.dxf" \
  --floor 12F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id sk_yongin_jiwon

# 미리보기: $ART/floors/12F/floor_original.png
# 다음 층은 묻지 않고 바로 별도 bash로 실행
```

다른 층도 **한 층씩, 확인 없이** (일괄 for-루프 금지):

```bash
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/input.dxf" \
  --floor 6F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id sk_yongin_jiwon
# 이어서 7F도 같은 패턴. 사용자 승인을 기다리지 않는다
```

## 1b) XA-S 블록이 없는 도면 (도곽·층 제목)

```bash
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/input.dxf" \
  --drawing-id <drawing_id> \
  --list-floors

# 목록의 층을 확인 없이 한 층씩 끝까지. 예: 1F 다음도 바로
# 층 이름을 확정하지 못한 도곽은 sheet_01, sheet_02 로 목록에 있다. 이것도 한 장씩 추출한다.
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/input.dxf" \
  --floor 1F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id <drawing_id>
```

`floors/<F>/floor_original.png` 가 이미 있으면 덮어쓴다. 나머지 층도 승인 없이 한 층씩 이어서 실행한다.

## 2) 구조 분석

```bash
python3 "$SCRIPTS/analyze_drawing.py" \
  --drawing-id sk_yongin_jiwon \
  --out "$ART" \
  --raw-dxf "$ARTIFACTS_DIR/input.dxf" \
  --floor-dir "$ART/floors"
```

## 3) work_log.md

에이전트가 `structure.md` / 각 `floors/<F>/floor_original.*`를 모아
`$ART/work_log.md`에 작업 내용·파일 표를 작성해 사용자에게 전달한다.

층 미리보기는 `floors/<F>/floor_original.png`만 첨부한다 (`floor_structure.*` / `floor_overview.*` / `floor_*_2d.png` / `parts/` 없음).

## (선택) 타일 분할 — 사용자가 명시한 경우만

```bash
python3 "$SCRIPTS/plan_split.py" \
  --artifacts "$ART" \
  --max-tile-m 60 \
  --overlap-m 1 \
  --pilot-floor 12F

python3 "$SCRIPTS/split_floor.py" \
  --artifacts "$ART" \
  --floor 12F \
  --source original \
  --max-tile-m 60 --overlap-m 1
```
