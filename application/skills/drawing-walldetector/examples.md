# drawing-walldetector 사용 예

## 0) 경로

`floor_wall_original.*` 가 이미 있어도 묻지 않고 덮어쓴다.

```bash
SCRIPTS="$WORKING_DIR/skills/drawing-walldetector/scripts"
ART="$ARTIFACTS_DIR/sk_yongin_jiwon"
FLOOR=12F

ls "$ART/floors/$FLOOR/floor_original.dxf" "$ART/floors/$FLOOR/floor_original.png"
```

로컬 개발(비 Runtime):

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-walldetector/scripts
ART=/path/to/user/artifacts/sk_yongin_jiwon
```

## 1) 샘플 2장으로 도면별 조건 (wall_samples/wall_conditions.json 이 없을 때만)

```bash
python3 "$SCRIPTS/sample_wall_conditions.py" \
  --artifacts "$ART" \
  --floor 12F \
  --prepare-only
```

`sample_01.png`, `sample_02.png` 를 `view_image` 로 보고 `floors/12F/wall_samples/observations.json` 을 쓴 다음:

```bash
python3 "$SCRIPTS/sample_wall_conditions.py" \
  --artifacts "$ART" \
  --floor 12F
```

이미 `wall_samples/wall_conditions.json` 이 있으면 이 단계를 건너뛴다.

## 2) 전 층 (확인 없음, 층당 bash 1회)

```bash
python3 "$SCRIPTS/detect_walls_floor.py" \
  --artifacts "$ART" \
  --floor 12F
```

미리보기: `$ART/floors/12F/floor_wall_original.png` (이미 있으면 덮어씀)

## 3) 다음 층도 바로 (층당 bash 1회)

```bash
# ❌ 금지: for FLOOR in 5F 6F …; do detect_walls_floor …; done
# ❌ 기본 금지: detect_walls_all (사용자가 일괄을 명시한 경우만)

python3 "$SCRIPTS/detect_walls_floor.py" --artifacts "$ART" --floor 6F
# 묻지 않고 7F도 별도 bash로 바로 실행
```

## 4) (선택) 레거시 타일 — parts 가 있고 사용자가 명시한 경우만

```bash
python3 "$SCRIPTS/detect_walls_floor.py" \
  --artifacts "$ART" \
  --floor 12F \
  --with-tiles

python3 "$SCRIPTS/detect_walls_tile.py" \
  --dxf "$ART/floors/12F/parts/R0C0.dxf" \
  --out-dir "$ART/floors/12F/walls" \
  --tile-id R0C0 \
  --floor 12F
```

## 5) (선택) 사용자가 일괄을 명시한 경우만

```bash
python3 "$SCRIPTS/detect_walls_all.py" --artifacts "$ART" --skip 12F
# → $ART/walls_all_index.json
```
