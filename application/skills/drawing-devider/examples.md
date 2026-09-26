# drawing-devider 사용 예

## 0) 기존 폴더 확인 (항상 먼저)

```bash
ART="$ARTIFACTS_DIR/sk_yongin_jiwon"
if [ -d "$ART" ]; then
  echo "EXISTING: $ART — 사용자에게 계속/중단 확인 후 진행"
  ls "$ART"
  # STOP: 허락 전 extract/analyze 금지
fi
```

## 1) 파일럿 층 추출 → 사용자 승인 대기

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
```

승인 후 다른 층도 **한 층씩** (일괄 for-루프 금지):

```bash
# 예: 6F만 — bash 1회
python3 "$SCRIPTS/extract_2d.py" \
  --dxf "$ARTIFACTS_DIR/input.dxf" \
  --floor 6F \
  --out "$ARTIFACTS_DIR" \
  --drawing-id sk_yongin_jiwon
# 보고 후 7F는 별도 bash로 동일 패턴
```

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

층 미리보기는 `floors/<F>/floor_original.png`만 첨부한다 (`floor_overview.*` / `floor_*_2d.png` / `parts/` 없음).

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
