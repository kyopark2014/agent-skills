# 기상청 날씨누리 API 가이드

## 기본 정보

- **베이스 URL**: `https://www.weather.go.kr`
- **서비스 경로**: `/w/` (날씨누리 서비스)
- **인증**: 불필요 (공개 API)

## 핵심 API 엔드포인트

### 1. 지역 검색 API
```
GET /w/renew2021/rest/main/place-search.do
```
**파라미터**:
- `query`: 검색어 (예: "서울 강남구", "부산 해운대")
- `start`: 페이지 번호 (기본값: 1)
- `src`: 소스 코드 (고정값: "A2")

**응답 예시**:
```json
[
  {
    "title": "장소명",
    "address": "서울 강남구 역삼동 806",
    "roadAddress": "서울 강남구 강남대로 ...",
    "longitude": 127.036576,
    "latitude": 37.495010,
    "x": 61,
    "y": 125,
    "dongCode": "1168064000"
  }
]
```

### 2. 행정동 정보 조회
```
GET /w/rest/zone/dongInfo.do
```
**파라미터**:
- `dong`: 행정동 코드 (10자리, 예: "1168064000")

**응답**: wide(시도), city(시군구), dong(읍면동) 정보 포함

### 3. 현재 날씨 조회
```
GET /w/wnuri-fct2021/main/current-weather.do
```
**파라미터**:
- `code`: 행정동 코드
- `unit`: 풍속 단위 ("m/s" 또는 "km/h")
- `aws`: AWS 사용 여부 ("N" 권장)
- `lat`: 위도
- `lon`: 경도

**응답**: HTML (BeautifulSoup으로 파싱 필요)
**포함 정보**: 날씨 상태, 기온, 체감온도, 습도, 바람, 강수량, 일출/일몰, 대기질(PM2.5, PM10, 오존)

### 4. 단기/중기 예보 조회 (일별)
```
GET /w/wnuri-fct2021/main/digital-forecast.do
```
**파라미터**:
- `code`: 행정동 코드
- `unit`: 풍속 단위
- `hr1`: 1시간 간격 여부 ("Y": 1시간, "N": 3시간/일별)
- `lat`: 위도
- `lon`: 경도

**응답**: HTML (BeautifulSoup으로 파싱 필요)
**포함 정보**: 10일간 일별 예보 (날씨 상태, 최저/최고 기온, 강수확률)

### 5. 가까운 관측소 실황
```
GET /w/wnuri-fct2021/main/current-aws.do
```
**파라미터**:
- `code`: 행정동 코드
- `lat`: 위도
- `lon`: 경도
- `unit`: 풍속 단위

**응답**: HTML
**포함 정보**: 가장 가까운 AWS 관측소의 기온, 바람, 습도, 강수량

## 행정동 코드 체계

- 10자리 숫자
- 앞 2자리: 시도 코드
- 앞 5자리: 시군구 코드
- 전체 10자리: 읍면동 코드

**주요 시도 코드**:
| 코드 | 지역 |
|------|------|
| 11   | 서울특별시 |
| 21   | 부산광역시 |
| 22   | 대구광역시 |
| 23   | 인천광역시 |
| 24   | 광주광역시 |
| 25   | 대전광역시 |
| 26   | 울산광역시 |
| 29   | 세종특별자치시 |
| 31   | 경기도 |
| 32   | 강원특별자치도 |
| 33   | 충청북도 |
| 34   | 충청남도 |
| 35   | 전라북도 |
| 36   | 전라남도 |
| 37   | 경상북도 |
| 38   | 경상남도 |
| 50   | 제주특별자치도 |

## HTML 파싱 패턴

기상청 API는 HTML을 반환하므로 BeautifulSoup으로 파싱:

```python
from bs4 import BeautifulSoup

soup = BeautifulSoup(resp.text, "html.parser")
lines = [line.strip() for line in soup.get_text().split("\n") if line.strip()]
```

### 현재 날씨 파싱 패턴
```
관측 시각: "04.05.(일) 15:00 현재" 패턴
날씨 상태: "날씨:" 다음 줄
기온: "기온:" 으로 시작하는 줄
체감온도: "체감(" 으로 시작하는 줄
습도: "습도" 다음 줄
바람: "바람" 다음 줄
강수량: "1시간강수량" 다음 줄
일출: "일출" 다음 줄
일몰: "일몰" 다음 줄
대기질: "초미세먼지(PM2.5)", "미세먼지(PM10)", "오존(O3)" 다음 줄
```

### 예보 파싱 패턴
```
날짜: r"(\d+일\([가-힣]+\)(?:오늘|내일|모레)?)" 정규식
오전/오후 구분: "오전", "오후" 키워드
날씨 상태: 맑음, 구름많음, 흐림, 비, 눈, 소나기, 안개, 황사, 가끔, 한때, 종일
최저기온: "최저" 로 시작
최고기온: "최고" 로 시작
강수확률: "오전 강수확률", "오후 강수확률" 포함
```

## 요청 헤더

```python
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": "https://www.weather.go.kr/w/index.do",
}

JSON_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}
```

## 주의사항

1. `/w/` 경로로 시작하는 URL만 날씨누리 서비스 (그 외는 기상청 행정 사이트)
2. HTML 응답 API는 반드시 BeautifulSoup으로 파싱
3. 지역 검색 결과의 첫 번째 항목이 항상 원하는 지역이 아닐 수 있음
4. 행정동 코드는 지역 검색 결과의 `dongCode` 필드 사용
