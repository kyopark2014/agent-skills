#!/usr/bin/env python3
"""
기상청(weather.go.kr) 날씨 조회 스크립트

사용법:
    python get_weather.py --location "서울 강남구"
    python get_weather.py --location "부산 해운대구"
    python get_weather.py --location "제주시"
    python get_weather.py --location "서울" --type current
    python get_weather.py --location "서울" --type forecast
    python get_weather.py --location "서울" --type all

출력 형식:
    --format text  (기본값, 사람이 읽기 쉬운 텍스트)
    --format json  (JSON 형식)
"""

import argparse
import json
import re
import sys
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.weather.go.kr"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": "https://www.weather.go.kr/w/index.do",
}
JSON_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


def search_location(query: str) -> dict | None:
    """지역명으로 위치 정보(dongCode, lat, lon) 검색"""
    url = f"{BASE_URL}/w/renew2021/rest/main/place-search.do"
    params = {"query": query, "start": 1, "src": "A2"}
    resp = requests.get(url, params=params, headers=JSON_HEADERS, timeout=10)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    r = results[0]
    return {
        "name": r.get("address", query),
        "title": r.get("title", ""),
        "dongCode": r.get("dongCode", ""),
        "lat": r.get("latitude"),
        "lon": r.get("longitude"),
        "x": r.get("x"),
        "y": r.get("y"),
    }


def get_current_weather(code: str, lat: float, lon: float) -> dict:
    """현재 날씨 조회"""
    url = f"{BASE_URL}/w/wnuri-fct2021/main/current-weather.do"
    params = {"code": code, "unit": "m/s", "aws": "N", "lat": lat, "lon": lon}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    lines = [line.strip() for line in soup.get_text().split("\n") if line.strip()]

    result = {}

    for i, line in enumerate(lines):
        # 관측 시각: "04.05.(일) 15:00 현재" 패턴
        if re.match(r"\d{2}\.\d{2}\.\([가-힣]\) \d{2}:\d{2} 현재", line):
            result["observation_time"] = line

        # 날씨 상태 (날씨: 다음 줄)
        if line == "날씨:" and i + 1 < len(lines):
            result["sky"] = lines[i + 1]

        # 기온
        if line.startswith("기온:"):
            # "기온: 16.4℃ 최저-최고-" 에서 기온만 추출
            temp_match = re.search(r"([\d.]+℃)", line)
            if temp_match:
                result["temperature"] = temp_match.group(1)

        # 체감온도
        if line.startswith("체감(") and "℃" in line:
            feels_match = re.search(r"체감\(([\d.]+℃)\)", line)
            if feels_match:
                result["feels_like"] = feels_match.group(1)

        # 어제 대비
        if "어제보다" in line:
            result["vs_yesterday"] = line

        # 습도
        if line == "습도" and i + 1 < len(lines):
            result["humidity"] = lines[i + 1]

        # 바람
        if line == "바람" and i + 1 < len(lines):
            result["wind"] = lines[i + 1]

        # 1시간 강수량
        if line == "1시간강수량" and i + 1 < len(lines):
            result["precipitation_1h"] = lines[i + 1]

        # 일출
        if line == "일출" and i + 1 < len(lines):
            result["sunrise"] = lines[i + 1]

        # 일몰
        if line == "일몰" and i + 1 < len(lines):
            result["sunset"] = lines[i + 1]

        # 초미세먼지
        if "초미세먼지(PM2.5)" in line and i + 1 < len(lines):
            result["pm25"] = lines[i + 1]

        # 미세먼지
        if "미세먼지(PM10)" in line and i + 1 < len(lines):
            result["pm10"] = lines[i + 1]

        # 오존
        if "오존(O3)" in line and i + 1 < len(lines):
            result["ozone"] = lines[i + 1]

    return result


def get_forecast(code: str, lat: float, lon: float) -> dict:
    """단기/중기 예보 조회 (일별)"""
    url = f"{BASE_URL}/w/wnuri-fct2021/main/digital-forecast.do"
    params = {"code": code, "unit": "m/s", "hr1": "N", "lat": lat, "lon": lon}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    lines = [line.strip() for line in soup.get_text().split("\n") if line.strip()]

    daily = []
    current_day = None
    last_period = None

    for i, line in enumerate(lines):
        # 날짜 패턴: "5일(일)오늘", "6일(월)내일", "7일(화)모레", "8일(수)"
        date_match = re.match(r"(\d+일\([가-힣]+\)(?:오늘|내일|모레)?)", line)
        if date_match:
            if current_day and current_day.get("date"):
                daily.append(current_day)
            current_day = {"date": date_match.group(1)}
            last_period = None
            continue

        if current_day is None:
            continue

        if line in ("오전", "오후"):
            last_period = line
            continue

        # 날씨 상태 (오전/오후 다음에 오는 날씨 키워드)
        sky_keywords = ["맑음", "구름많음", "흐림", "비", "눈", "소나기", "안개", "황사", "가끔", "한때", "종일"]
        if last_period and any(kw in line for kw in sky_keywords):
            current_day[f"{last_period}_sky"] = line
            last_period = None
            continue

        # 최저/최고 기온
        if line.startswith("최저"):
            current_day["min_temp"] = re.search(r"[-\d]+℃", line)
            current_day["min_temp"] = current_day["min_temp"].group() if current_day["min_temp"] else line
        if line.startswith("최고"):
            current_day["max_temp"] = re.search(r"[-\d]+℃", line)
            current_day["max_temp"] = current_day["max_temp"].group() if current_day["max_temp"] else line

        # 강수확률
        if "오전 강수확률" in line:
            prob_match = re.search(r"(\d+%|-)", line)
            current_day["am_rain_prob"] = prob_match.group(1) if prob_match else line
        if "오후 강수확률" in line:
            prob_match = re.search(r"(\d+%|-)", line)
            current_day["pm_rain_prob"] = prob_match.group(1) if prob_match else line

    if current_day and current_day.get("date"):
        daily.append(current_day)

    # 중복 날짜 제거 (날짜가 같은 항목 중 첫 번째만 유지)
    seen_dates = set()
    unique_daily = []
    for day in daily:
        date_key = re.match(r"(\d+일)", day["date"])
        if date_key:
            key = date_key.group(1)
            if key not in seen_dates:
                seen_dates.add(key)
                unique_daily.append(day)

    return {"daily": unique_daily}


def format_current_weather(location_name: str, weather: dict) -> str:
    """현재 날씨를 텍스트로 포맷"""
    output = [f"📍 {location_name} 현재 날씨"]
    output.append("=" * 45)

    if weather.get("observation_time"):
        output.append(f"🕐 {weather['observation_time']}")

    if weather.get("sky"):
        output.append(f"🌤 날씨 상태: {weather['sky']}")

    if weather.get("temperature"):
        temp_line = f"🌡 기온: {weather['temperature']}"
        if weather.get("feels_like"):
            temp_line += f"  (체감 {weather['feels_like']})"
        output.append(temp_line)

    if weather.get("vs_yesterday"):
        output.append(f"   {weather['vs_yesterday']}")

    if weather.get("humidity"):
        output.append(f"💧 습도: {weather['humidity']}")

    if weather.get("wind"):
        output.append(f"💨 바람: {weather['wind']}")

    if weather.get("precipitation_1h"):
        output.append(f"🌧 1시간 강수량: {weather['precipitation_1h']}")

    if weather.get("sunrise") and weather.get("sunset"):
        output.append(f"🌅 일출: {weather['sunrise']}  일몰: {weather['sunset']}")

    # 대기질
    air_parts = []
    if weather.get("pm25"):
        air_parts.append(f"초미세먼지: {weather['pm25']}")
    if weather.get("pm10"):
        air_parts.append(f"미세먼지: {weather['pm10']}")
    if weather.get("ozone"):
        air_parts.append(f"오존: {weather['ozone']}")
    if air_parts:
        output.append(f"😷 대기질: {' | '.join(air_parts)}")

    return "\n".join(output)


def format_forecast(location_name: str, forecast: dict) -> str:
    """예보를 텍스트로 포맷"""
    output = [f"📍 {location_name} 날씨 예보 (10일)"]
    output.append("=" * 45)

    for day in forecast.get("daily", []):
        date = day.get("date", "")
        if not date:
            continue

        min_t = day.get("min_temp", "-")
        max_t = day.get("max_temp", "-")
        am_sky = day.get("오전_sky", "-")
        pm_sky = day.get("오후_sky", "-")
        am_rain = day.get("am_rain_prob", "-")
        pm_rain = day.get("pm_rain_prob", "-")

        output.append(f"\n📅 {date}")
        output.append(f"   🌡 최저 {min_t} / 최고 {max_t}")
        output.append(f"   오전: {am_sky} (강수확률 {am_rain})")
        output.append(f"   오후: {pm_sky} (강수확률 {pm_rain})")

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(description="기상청 날씨 조회")
    parser.add_argument("--location", "-l", required=True, help="조회할 지역명 (예: 서울 강남구, 부산 해운대)")
    parser.add_argument(
        "--type",
        "-t",
        choices=["current", "forecast", "all"],
        default="all",
        help="조회 유형: current(현재날씨), forecast(예보), all(전체, 기본값)",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["text", "json"],
        default="text",
        help="출력 형식: text(기본값), json",
    )
    args = parser.parse_args()

    # 1. 지역 검색
    print(f"🔍 '{args.location}' 지역 검색 중...", file=sys.stderr)
    location = search_location(args.location)
    if not location:
        print(f"❌ '{args.location}' 지역을 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    dong_code = location["dongCode"]
    lat = location["lat"]
    lon = location["lon"]
    location_name = location["name"]

    print(f"✅ 지역 확인: {location_name} (dongCode: {dong_code})", file=sys.stderr)

    result = {"location": location_name, "dong_code": dong_code}

    # 2. 날씨 조회
    if args.type in ("current", "all"):
        print("🌤 현재 날씨 조회 중...", file=sys.stderr)
        current = get_current_weather(dong_code, lat, lon)
        result["current_weather"] = current

    if args.type in ("forecast", "all"):
        print("📅 예보 조회 중...", file=sys.stderr)
        forecast = get_forecast(dong_code, lat, lon)
        result["forecast"] = forecast

    # 3. 출력
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if args.type in ("current", "all") and "current_weather" in result:
            print(format_current_weather(location_name, result["current_weather"]))
            print()
        if args.type in ("forecast", "all") and "forecast" in result:
            print(format_forecast(location_name, result["forecast"]))


if __name__ == "__main__":
    main()
