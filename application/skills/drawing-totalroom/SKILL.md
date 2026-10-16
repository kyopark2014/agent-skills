---
name: drawing-totalroom
description: >-
  floor_wall_validated.dxf 모델스페이스의 TEXT/MTEXT 실명 라벨을 모두 모아,
  drawing-roomevaluator와 같은 벽 안쪽 면적으로 실마다 공간을 잡습니다.
  같은 폴더에 floor_label_detected.dxf, floor_label_detected.png,
  floor_label_detected.json을 씁니다. DXF는 검증 도면에 라벨별 레이어를
  더한 것이고, 각 레이어에 면적 HATCH와 이름·면적 문자가 있습니다.
  PNG는 floor_label_detected.dxf를 검증 도면과 같은 렌더러로 그린 것입니다.
  각 실의 벽 테두리에 닿는 문은 `--door close`(기본) 또는 `--door open`으로 고릅니다.
  한 층의 전체 실 면적, 라벨별 면적, floor_label_detected 요청 시 사용합니다.
  대상 파일은 artifacts/drawing_list.json으로 찾습니다.
---

# drawing-totalroom (층 전체 실명 면적)

`floor_wall_validated.dxf` 의 실명 라벨마다, 그 글자가 들어 있는 **벽 안쪽 면**을 계산한다.
면적 규칙과 문 열림은 `drawing-roomevaluator` 와 같다. 입력 DXF 는 수정하지 않는다. 결과 PNG 는 결과 DXF 를 렌더한 것이다.

## When to Use

- 한 층의 **모든 실명**에 대해 면적을 구할 때
- 라벨별 레이어로 면적을 확인하고, 층 전체 색면 PNG 가 필요할 때
- 실이 하나뿐이면 `drawing-roomevaluator` 를 쓴다

## 대상 도면 찾기

건물·층 이름이 있으면 폴더를 만들지 말고 `$ARTIFACTS_DIR/drawing_list.json`에서 고른다.

1. `$ARTIFACTS_DIR/drawing_list.json`이 있으면 그 파일. 없으면 `/Users/ksdyb/Documents/src/agent-skills/application/.session_storage/lge/artifacts/drawing_list.json`.
2. 사용자가 말한 건물·프로젝트 이름을 `drawings[].source_filename`에 맞춘다. 공백·대소문자는 무시하고, 그 말이 파일명에 들어 있으면 그 도면이다. `source_filename`에 없으면 `drawing_id`, `folder`를 같은 방식으로 본다. 없거나 둘 이상이면 `source_filename`을 보여주고 고르게 한다.
3. 그 도면의 `floors[].floor`가 요청 층과 같으면 그 항목이다. `1층`은 `1F`, `지하1층`은 `B1F`, `옥상`은 `RF`로 본다. `floor`가 없고 `name_confirmed`가 false이면 `title`에 그 층 표기가 있는 항목이 후보다. 후보가 둘 이상이면 `floor`와 `title`을 보여주고 고르게 한다. 없으면 `discovered_floors`를 알리고 없는 층 폴더는 만들지 않는다.
4. 입력은 `$ARTIFACTS_DIR/<folder>/floors/<floor>/floor_wall_validated.dxf`와 같은 폴더의 `floor_wall_validated_meta.json`이다. validated 파일이 없으면 그 층은 벽 검증 전이라고 알린다. PNG 배경으로 `floor_wall_validated.png`를 쓰지 않는다.

## Critical Rules

1. **라벨** — 모델스페이스의 `TEXT`/`MTEXT`만 본다. 블록 안 글자는 펼치지 않는다. 공백을 없앤 뒤 24자 이하이며 `면적`, `천장`, `:`, `=` 이 없는 글자 중, 한글이 있거나 영문 2자 이상에 숫자가 붙은 이름만 실명이다. `X1` 같은 축선, `6300`·`6,300` 같은 치수, `UP`/`DN` 은 빠진다. `MRI1` 은 포함한다. 집기 표기는 실명이 아니다. `미니바`, `미비바`, `옷장`, `신발장`, `화분`, `화장대`, `월풀욕조`, `욕조`, `(장애인)`과, 그 뒤에 번호만 붙은 글자(`옷장1`, `화분#2`)는 뺀다. `EV`, `ELEV`, `ELEV.`, `ELEV. HALL`, `ELEV. 홀`, `수납장`, `하부장`, `상부장`, `옷봉설치`, `장비`, `데스크`, `서랍`, `온수기`, `살균기`, `소독기`, `도어`, `테이블`, `정수기`, `휴대폰장`, `게시판`, `렉장`·`랙장`, `냉장고`, `제빙기`, `머신`, `커피`, `전자랜지`·`전자레인지`, `템블러`·`텀블러`, `세척기`가 들어간 글자도 면적 라벨이 아니다. `탱크`, `실외기`, `마감`, `콘크리트`, `행거`, `클립바`, `수조`, `발전기실`, `집수정`, `ASD`, `W.C`, `세면대`, `ARM실`, `옷장`, `용수대`, `자동문`, `거울`, `세탁기`, `기기배치`, `폐쇄`, `장애인겸용`, `데크`, `비품고`, `전기실`, `충전소`, `급속`, `폐기품`, `폐기물`, `콤프레셔`, `설비실`, `경계선`, `대지안`, `벽부형`, `산책로`, `올라옴`, `수거함`, `소화전`, `배기`, `FSD`, `DORDI`, `승강식`, `OH 4700`, `유모차보관`, `C1A`, `C2A`, `C3A`, `TC1`, `PW 1000`, `AG 1100`, `노출되는`, `자전거`, `피난구`, `DA-`, `재활용`, `급기`, `주문제작`, `BC-`, `부속실`, `보일러실`, `세탁실`, `승강장`, `테라스`, `청결물실`, `수납`, `보일러`, `옷방`, `파우더`, `선큰가든`, `발코니`, `정원`, `배선실`, `병동`, `병동`이 들어간 글자는 면적을 계산하지 않는다. `소파룸`처럼 집기 이름이 아닌 실명은 남긴다. 같은 실명이 50 mm 안에 있으면 한 곳이다. 위·아래 줄 간격이 글자 높이의 1.8배 안이고 가로로 겹치면 위 글자부터 이어 한 실명으로 만든다. 예: `투시영상` 과 `검사실7` → `투시영상검사실7`.
2. **면적** — 라벨이 있는 쪽의 **벽 안쪽 면**까지. `WALL`·`WINDOW`·`COLUMN`·`DOOR` 선을 모두 경계로 읽는다. 문 스윙 호는 경계가 아니다. 문짝으로 그린 얇은 닫힌 사각형은 실 안을 가르지 않는다. 개구를 막는 문선만 경계로 쓴다. 같은 벽선의 문 개구(2.4 m 이하)는 그 벽선으로 이어 실에 포함한다. 다만 틈을 가로지르는 직교 실간 벽이 있으면 잇지 않는다. 여닫이 문이 있는 개구는 그 문선에서 멈춘다.
   `--door` 기본값은 `close`다. 요청에 열림·오픈이 없으면 `close`로 실행한다. 값은 그 층의 모든 실에 같이 적용된다.
   면적은 한 조건으로 자르지 않는다. 앞에서 놓친 라벨만 다음 방안으로 넘긴다.
   방안 1 — 벽이 닫힌 라벨. 각 실은 문을 모두 닫아 벽 테두리를 잡은 뒤, 그 테두리 위의 `DOOR` 직선과 테두리 450 mm 안의 여닫이만 그 실의 문으로 본다. 라벨이 있는데 벽만으로 실이 닫히면 그 면을 그대로 쓴다. 열릴 때만 엔터티로 닫는다. 벽 끝에 닿아 다른 벽까지 이어진 작은 엔터티가 있으면 그 변으로 모서리를 닫는다. 상담실(4) 왼쪽 아래가 그 예다. 같은 벽선이 2.4–18 m 끊긴 개구 안의 선을 벽으로 본다. 그 선들이 1.2 m 안쪽 틈으로 이어져 개구를 덮으면 양끝 벽 사이를 닫아 실을 잡는다. 식생활관 오른쪽이 그 예다. 가로·세로로 긴 벽 끝이 사선 외벽과 2–9 m 떨어지면 그 사이도 닫는다. 식생활관 왼쪽이 그 예다. 짧은 엔터티가 그 테두리 안을 가르면 긴 엔터티로 닫힌 더 큰 면을 쓴다. 벽 끝이 다른 벽에 닿지 않고 그 앞 2.4 m 안에 같은 방향의 선이 있으면, 그 선을 벽으로 보고 벽 끝과 잇는다.
   방안 2 — 방안 1에서 닫히지 않은 라벨만. 라벨에서 방향마다 처음 닿는 이어진 벽을 모아 면을 만든다. 이웃보다 1.8 m 넘게 먼 점은 틈 너머라 빼고 그 사이를 직선으로 막는다. 24°보다 짧은 실 안 선은 경계로 쓰지 않는다. 이 면은 둘레 중 벽·창·기둥·문 선에서 250 mm 안인 길이가 90% 이상일 때만 남긴다. 근생-4가 그 예다. 90%가 안 되면 방안 3으로 넘긴다. 근생-8 옆 테라스처럼 한 변이 열린 면은 여기서 빠진다.
   방안 3 — 방안 2에서도 면을 남기지 못한 라벨만. 한 라벨이면 반경 25 m 안의 벽 선을 100 mm 격자에 두고, 씨앗에서 벽까지 거리를 잰 뒤 벽 두께를 80, 160, 320, 640, 1000, 1400, 1800, 2400 mm로 키운다. 씨앗 공간이 그 25 m 창 테두리에 닿지 않는 가장 얇은 두께에서 실로 확정한다. 씨앗이 벽 칸에 있으면 400 mm 안에서 벽에서 가장 먼 빈 칸을 씨앗으로 쓴다. 두꺼워진 벽이 씨앗을 덮거나, 2.4 m에서도 창 밖으로 새거나, 면이 0.2 m² 미만이면 면적을 만들지 않는다. 그래도 못 잡은 오픈 라벨이 20 m 안에 둘 이상이면 한 묶음으로 본다. 테라스 글자는 묶지 않는다. 묶음 씨앗 주변 벽 밀도(길이/면적)가 0.40 m/m² 미만이면 제목란으로 보고 버린다. 묶음은 씨앗에서 빈 칸으로만 같이 넓히며, 벽선은 150 mm 두께로 막아 벽을 넘지 않는다. 이미 잡힌 실 면도 막아서 그쪽으로 새지 않는다. 씨앗 볼록껍질 밖 8 m까지만 퍼진다. 창 밖으로 새지 않는 범위에서 벽 테두리 비율이 55% 이상으로 가장 높은 거리에서 한 면을 확정하고 그 면 안의 라벨이 면을 공유한다.
   작은 칸 — 위 방안으로 잡은 면이 2 m² 이하이면 라벨이 기둥 옆·벽 주머니 같은 작은 칸에 떨어진 것으로 보고, 씨앗을 왼쪽·오른쪽·위·아래로 100–2000 mm 옮겨 2 m²가 넘는 면을 찾는다. 같은 거리에서 여러 방향이 되면 면적이 큰 쪽을 쓰고, 그 좌표를 라벨 씨앗으로 둔다. 옮겨도 2 m²를 넘지 못하면 처음 면을 그대로 둔다.
   `open`은 실마다 그 문만 연다. 테두리 밖 `DOOR`는 `open`이어도 닫힌 경계로 둔다.
3. **기둥 돌출부는 항상 뺀다.** H-Beam이 벽 안쪽 면보다 실 안으로 들어온 면적은 `area_m2`에서 제외한다. 벽 두께 안에만 있는 부분은 빼지 않는다.
4. **레이어** — `floor_label_detected.dxf` 는 `floor_wall_validated.dxf` 를 복사한 뒤 라벨 레이어를 더한 도면이다. 라벨 하나당 레이어 하나다. 같은 실명이 여러 곳이면 그 레이어에 HATCH 가 여러 개다. 레이어의 HATCH 면적 합이 그 라벨의 `area_m2`다. 각 자리에는 PNG 와 같이 실명과 `면적 ㎡` 문자를 흰 판 위에 둔다. 좌표는 입력 DXF 와 같은 mm 이다.
5. **같은 면** — 화장실·WC·계단실이 다른 실과 한 면이면 끊긴 벽 끝을 바깥 경계까지 이어 그 부분을 면적에서 뺀다. 벽 끝으로 갈라지지 않으면 화장실 글자 쪽 칸을 문 틈으로 새지 않게 잡아 그 실에서 뺀다. 그 글자는 실로 남기지 않는다. 한 면에 실명이 둘 이상이거나 창고·차고·화장실·방풍실·보관실·퇴식대·샤워실·고압·저압·계단실·복도·승강기·EPS·OPEN·오픈·홀-·홈-·입구·출구·양치공간·청소도구함·청소도구실·실외기실·조경·배경·평면도·비품고을·항습기·화물·휠체어·린넨실·오물처리실·구급자·응급환자·의료가스실·기계실·(여)·(남)·문서고·기울기·자동방화·인)·인승·OTIS·Wlqtnwjd·DA#·DA(·올려옴·내려감·DOOR·패드·기저귀·침례탕약·설치·드라이기·유사용·상하부·분전함·세척실·탕비실·물품·스트레쳐·E.V·WC·DA·유도등·단자함·소화전·다용도실·밸브·(근생)·정화조·피난기·보관소·속도·탑승대기공간·주거·배수·비상용·탑승대기·ST( 글자가 같이 있으면, 그 사이를 지나는 `BASE` 선이 면 경계에서 800 mm 안까지 닿을 때 그 선으로 면을 나눈다. 벽이 아닌 이중선이면 실 라벨에 가까운 쪽을 경계로 쓴다. 창고·차고·화장실·방풍실·보관실·퇴식대·샤워실·고압·저압·계단실·복도·승강기·EPS·OPEN·오픈·홀-·홈-·입구·출구·양치공간·청소도구함·청소도구실·실외기실·조경·배경·평면도·비품고을·항습기·화물·휠체어·린넨실·오물처리실·구급자·응급환자·의료가스실·기계실·(여)·(남)·문서고·기울기·자동방화·인)·인승·OTIS·Wlqtnwjd·DA#·DA(·올려옴·내려감·DOOR·패드·기저귀·침례탕약·설치·드라이기·유사용·상하부·분전함·세척실·탕비실·물품·스트레쳐·E.V·WC·DA·유도등·단자함·소화전·다용도실·밸브·(근생)·정화조·피난기·보관소·속도·탑승대기공간·주거·배수·비상용·탑승대기·ST( 쪽은 실로 남기지 않는다. 나눌 선이 없으면 각 레이어에 그 면 전체가 들어가고, `shared_with` 가 있으면 `area_m2`를 서로 더하지 않는다. 같은 실명이 서로 다른 면에 있으면 각 면의 `area_m2`를 더한다.
6. **원본 보존** — validated DXF 는 읽기 전용이다. 결과 DXF 는 그 도면의 사본에 라벨 레이어를 더한 것이고, 검증 파일을 덮어쓰지 않는다. `floor_label_detected.png` 는 그 결과 DXF 를 `render_wall_dxf_png` 로 그린 것이다. HATCH, 흰 판, 실명·면적 문자는 DXF 엔티티에서 온다. 결과는 그 DXF 와 같은 폴더의 `floor_label_detected.dxf`, `floor_label_detected.png`, `floor_label_detected.json` 만 덮어쓴다. 번호 붙은 파일은 만들지 않는다.
7. **스크립트 절대경로** — 이 SKILL.md 옆 `scripts/detect_labels.py`. 층이 둘 이상이면 `scripts/detect_labels_all.py` 한 번. 물리 CPU 코어 수만큼 층을 동시에 계산한다. `for` 루프로 층마다 `detect_labels.py`를 호출하지 않는다.
8. 응답은 **한국어**. 경로·JSON 키·레이어 이름은 산출 그대로 쓴다.

## Script Location

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-totalroom/scripts
# Runtime: SCRIPTS="$WORKING_DIR/skills/drawing-totalroom/scripts"

ART="${ARTIFACTS_DIR:-/Users/ksdyb/Documents/src/agent-skills/application/.session_storage/lge/artifacts}"
python3.13 "$SCRIPTS/detect_labels.py" \
  --dxf "$ART/<folder>/floors/<floor>/floor_wall_validated.dxf" \
  --meta "$ART/<folder>/floors/<floor>/floor_wall_validated_meta.json" \
  --door close
```

`--door`를 생략하면 `close`다. 사용자가 각 실의 인접 문을 열어서 계산하라고 하면 `--door open`을 붙인다.

층이 둘 이상이면 한 층씩 호출하지 않는다. `--workers`를 생략하면 물리 CPU 코어 수다.

```bash
python3.13 "$SCRIPTS/detect_labels_all.py" \
  --artifacts "$ART/<folder>" \
  --door close
```

고른 층만이면 `--floors 1F,3F`. 한 층이면 `detect_labels.py`.

| 인자 | 의미 |
| --- | --- |
| `--dxf` | `floor_wall_validated.dxf` |
| `--meta` | `floor_wall_validated_meta.json` (렌더 범위·폭) |
| `--door` | `close`(기본) 또는 `open`. 실마다 그 실의 벽 테두리에 닿는 문만 연다 |

## Workflow

```
drawing_list.json 에서 건물 · 층 결정
  ↓
floor_wall_validated.dxf 의 TEXT/MTEXT 실명 목록
  ↓
라벨마다 WALL·WINDOW·COLUMN·DOOR 안쪽 면 (drawing-roomevaluator 와 같은 규칙)
  `--door open` 이면 그 실의 벽 테두리 문만 연다. 생략하면 close
  ↓
floor_label_detected.dxf    # 검증 도면 + 라벨별 레이어, HATCH, 이름·면적 문자
floor_label_detected.png    # 위 DXF 를 렌더한 층 전체
floor_label_detected.json   # 라벨·인스턴스·빠진 라벨
```

## 출력

- `labels[].layer` — 검증 도면 위에 더한 DXF 레이어 이름. 그 레이어에 면적, 실명, 면적 문자가 있다.
- `rules.door` — 이번 층의 `close` 또는 `open`
- `instances[].boundary_doors` — 그 자리의 벽 테두리 문. `x`,`y`와 `state`
- `labels[].area_m2` — 그 라벨의 HATCH 면적 합 (m²). 같은 실명이 여러 면이면 더한 값이다.
- `labels[].instances[]` — 글자 좌표, 그곳의 `area_m2`, `width_m`, `height_m`.
- `instances[].shared_with` — 같은 면을 가리키는 다른 실명. 있으면 그 면은 한 번만 말한다.
- `unique_face_area_m2` — 서로 다른 면을 한 번씩만 더한 값.
- `skipped` — 실명으로 봤으나 벽이 닫히지 않은 글자.
- `png` — `floor_label_detected.dxf` 를 검증 도면과 같은 렌더러로 그린다. 색면은 라벨 레이어 HATCH, 자리 글자는 그 레이어의 TEXT 다. 오른쪽 범례는 붙이지 않는다.

벽이 닫히지 않은 라벨은 레이어를 만들지 않는다. 도면의 `면적` 문구는 `drawing_area_m2` 참고값이다. 계산값과 다르면 계산값을 기준으로 설명한다.

## Related

- `drawing-llmvalidator` — `floor_wall_validated` 선행
- `drawing-areasizing` — 추출 → 벽 → 검증 다음에 이 스킬로 라벨 실 면적을 낸다
