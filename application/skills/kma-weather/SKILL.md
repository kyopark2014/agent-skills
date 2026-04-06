---
name: kma-weather
description: >
  기상청(weather.go.kr) 날씨누리 웹사이트를 이용한 한국 날씨 조회 skill.
  현재 날씨(기온, 체감온도, 습도, 바람, 강수량, 일출/일몰, 대기질)와
  10일간 단기/중기 예보(날씨 상태, 최저/최고 기온, 강수확률)를 제공한다.
  사용자가 특정 지역의 날씨, 기온, 강수 여부, 예보 등을 물어볼 때 사용한다.
  예: "서울 날씨 알려줘", "부산 오늘 비 와?", "제주도 이번 주 날씨는?",
  "강남구 현재 기온", "내일 우산 필요해?", "이번 주말 날씨 어때?"
---

# 기상청 날씨 조회 Skill

## 워크플로우

1. 사용자 요청에서 **지역명**과 **조회 유형**(현재/예보/전체)을 파악
2. `scripts/get_weather.py` 실행
3. 결과를 자연스러운 한국어로 요약하여 답변

## 스크립트 실행

```bash
# 현재 날씨 + 예보 (기본)
python skills/kma-weather/scripts/get_weather.py --location "서울 강남구"

# 현재 날씨만
python skills/kma-weather/scripts/get_weather.py --location "부산 해운대" --type current

# 예보만
python skills/kma-weather/scripts/get_weather.py --location "제주시" --type forecast

# JSON 출력
python skills/kma-weather/scripts/get_weather.py --location "서울" --format json
```

**옵션**:
- `--location` / `-l`: 지역명 (필수). 시/구/동 단위 모두 가능
- `--type` / `-t`: `current` | `forecast` | `all` (기본값: `all`)
- `--format` / `-f`: `text` | `json` (기본값: `text`)

## 출력 예시

```
📍 서울 강남구 역삼동 현재 날씨
=============================================
🕐 04.05.(일) 15:00 현재
🌤 날씨 상태: 맑음
🌡 기온: 16.4℃  (체감 16.4℃)
   어제보다 2℃ 낮아요
💧 습도: 35 %
💨 바람: 남 2.5 m/s
🌅 일출: 06:10  일몰: 18:57
😷 대기질: 초미세먼지: 25㎍/m³보통 | 미세먼지: 45㎍/m³보통

📍 서울 강남구 역삼동 날씨 예보 (10일)
=============================================
📅 5일(일)오늘
   🌡 최저 7℃ / 최고 18℃
   오전: - (강수확률 -)
   오후: 가끔 비 (강수확률 100%)
```

## 답변 가이드

- 현재 날씨 질문 → `--type current` 실행 후 핵심 정보(기온, 날씨 상태, 습도, 바람) 요약
- 예보 질문 → `--type forecast` 실행 후 해당 날짜 정보 강조
- 우산/외출 관련 → 강수확률과 날씨 상태 기반으로 조언
- 지역명이 불명확하면 사용자에게 확인 후 실행

## API 참고

상세 API 문서: `references/api-guide.md`
- 기상청 날씨누리 내부 API 사용 (별도 API 키 불필요)
- 지역 검색 → 행정동 코드 획득 → 날씨 조회 순서
