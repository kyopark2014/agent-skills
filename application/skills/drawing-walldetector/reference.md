# drawing-walldetector — 참고

## 대상 도면

건물·층만 있으면 `$ARTIFACTS_DIR/drawing_list.json`(없으면 `…/lge/artifacts/drawing_list.json`)의 `source_filename`과 `floors[].floor`로 고른다. 입력은 그 층의 `floor_original.dxf`이다. 절차는 SKILL.md [대상 도면 찾기]를 따른다.

## 산출물 위치

bash/`execute_code` cwd는 사용자 `artifacts/`이며, 환경변수 `$ARTIFACTS_DIR`가 동일 경로를 가리킨다.

```text
$ARTIFACTS_DIR/<drawing_id>/
  floors/<F>/
    floor_original.dxf / .png                         # drawing-devider
    floor_wall_original.dxf / .png / _meta.json       # 층 전체 wall (기본)
    floor_wall_index.json                             # 층 요약
  walls_all_index.json                                # 선택(다층 요약)
```

- `floor_walls_overview.*` / `walls/floor_wall_original.*` 는 **생성·사용하지 않음**.
- `parts/` · `walls/R*C*_walls.*` 는 **기본 미생성** (`--with-tiles` 레거시만).

- 스크립트 호출: `$WORKING_DIR/skills/drawing-walldetector/scripts/...`
- 상대 `skills/`·`scripts/`·구경로 `cde-pilot/...` **금지**
- 로컬 개발 시에도 **사용자 artifacts**를 쓴다.

## 기존 산출

`floors/<F>/floor_wall_original.*`가 이미 있어도 묻지 않고 `detect_walls_floor`로 덮어쓴다. 폴더를 통째로 삭제하지 않는다.

## 층별 1개씩 · 확인 없음

1. 발견된 층을 **층당 bash 1회**로 끝까지 검출한다. 파일럿 확인을 받지 않는다
2. 같은 경로의 `floor_wall_original.*` 가 있으면 덮어쓴다
3. **금지(기본):** `for FLOOR in …` 일괄, `detect_walls_all` 한 방에 전층, 층 사이에 사용자 확인
4. `detect_walls_all`은 사용자가 일괄을 **명시한 경우만**

## 왜 기하 휴리스틱인가

지원동 클린 DXF는 벽/가구가 거의 전부 `0arch`에 있다. 레이어 분리가 불가하므로:

1. LINE / LWPOLYLINE 세그먼트 추출
2. 축정렬(H/V) + 최소 길이
3. 직교 방향으로 **벽 두께 대역(기본 30–420 mm)** 안의 평행 쌍 + 길이 방향 overlap
4. 평행선이 여러 겹이면, 인접 간격 중앙값이 160 mm 이하이고 긴 선(≥ 2.8 m)이 있을 때 외벽 포체로 인정. 그보다 성기면 계단 해칭으로 제외
5. 닫힌 사각 ≤ 3.5 m(기둥·가구) · 짧은 다변 폴리(조경·해칭) 제외
6. ARC/CIRCLE은 벽이 아님
7. 폴리라인은 벽 비율 ≥ 75%일 때만 통째 WALL
8. **X자 문** — 교차 대각선(LINE 두 개, 또는 개구 0.5–2.2 m · 짧은 변 ≤ 0.45 m 인 X 폴리라인)은 벽이 아니다. 이중선 사이의 문 궤적(더 짧은 중간선)도 벽 면에서 제외한다.
9. **개구로 잘린 간벽** — 조각 길이가 1.7 m 미만이어도, 같은 직선에서 40 mm 이내로 맞닿은 런이 2.2 m 이상이고 두 면 모두 조각이 둘 이상이며 간격이 120–180 mm이면 벽이다. 옷장에 붙은 150 mm 이중선이 이 경우다. 세로 간벽은 런이 2.2 m에 못 미쳐도, 그 이중선이 X 문과 같은 두 면에 맞닿아 있으면 벽이다. X 획 자체는 벽이 아니다.
10. **창호 중간 벽** — 닫힌 사각 두 개가 1.2 m 이상 변을 맞대고, 양쪽 바깥 변이 이미 벽이면 그 공유 변도 벽이다. 사각이 가구로 제외돼도 이 중간 변은 빨강으로 남긴다. 407호 침실처럼 두께 0으로 겹친 세로 멀리언이 이 경우다.

## 파라미터

| 이름 | 기본 | 의미 |
|------|------|------|
| `min_len_mm` | 500 | 벽 후보 세그먼트 최소 길이 |
| `thick_min_mm` | 30 | 이중선 최소 간격 (외벽 마감선 30–45 mm) |
| `wall_pack_gap_mm` | 160 | 여러 겹 외벽의 인접 간격 중앙값 상한 |
| `thick_max_mm` | 420 | 이중선 최대 간격 |
| `entity_wall_ratio` | 0.75 | 폴리라인 통째 벽 승격 |
| `furniture_box_max_mm` | 3500 | 이하 닫힌 박스 = 비가중 |
| `short_pair_max_mm` | 2800 | 양쪽 모두 짧으면 가구 변으로 제외. 둘 다 1.7 m 이상이고 두께 ≤ 250 mm이면 개구로 잘린 외벽으로 유지. 120–180 mm 간벽은 맞닿은 런이 2.2 m 이상이면 더 짧은 조각도 유지 |

화장실 칸막이·코어 벽은 잡혀야 하고, 문 스윙·기둥·가구·계단 트레드 해칭은 빠져야 한다.  
여닫이문 잎(두께 20–80 mm, 폭 0.65–1.45 m)과 그 잎에 겹친 짧은 평행선은 벽이 아니다. 문끝에 이어진 짧은 벽은 벽이다. 돌출창(45° 볼살–바깥면–반대 볼살, 돌출 0.25–1.0 m)은 벽이다.  
`floor_wall_original.png`에서 과검출/미검출이 보이면 위 값을 조정한다. 조정 전에 사용자 확인으로 나머지 층을 멈추지 않는다.

## devider와의 관계

| 단계 | 스킬 | 산출 |
|------|------|------|
| 층 추출 | `drawing-devider` | `$ARTIFACTS_DIR/<id>/floors/<F>/floor_original.dxf` |
| 벽 | `drawing-walldetector` | `$ARTIFACTS_DIR/<id>/floors/<F>/floor_wall_original.dxf` |

타일 `parts/` 분할은 기본 파이프라인에서 쓰지 않는다.
