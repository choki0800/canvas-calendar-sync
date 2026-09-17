import os
import json
from datetime import datetime, timezone
import requests
from icalendar import Calendar, Event

CANVAS_BASE_URL = "https://learning.hanyang.ac.kr"
API_TOKEN = os.getenv("CANVAS_API_TOKEN", "YOUR_CANVAS_API_TOKEN_HERE")
OUTPUT_ICS_PATH = "canvas_attendance.ics"
ORIGINAL_ICS_URL = "https://learning.hanyang.ac.kr/feeds/calendars/user_4e5iYEV0E33S8Pdjog9xYmlsOaZYHzp4o8MLIzS0.ics"

# 브라우저와 똑같이 위장 (400 에러 우회용)
headers = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest"
}

def get_paginated_data(url, params=None):
    results = []
    if params is None: params = {}
    params["per_page"] = 100
    while url:
        res = requests.get(url, headers=headers, params=params)
        if res.status_code != 200: break
        results.extend(res.json())
        links = res.links
        if 'next' in links: url = links['next']['url']
        else: url = None
    return results

def parse_iso_datetime(dt_str):
    if not dt_str: return None
    dt_str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(dt_str)

def extract_dates(data, current_title="Unknown"):
    found = []
    if isinstance(data, dict):
        title = data.get('title', data.get('name', current_title))
        
        # 날짜가 있을 법한 모든 키 탐색
        due = (data.get('due_at') or data.get('late_at') or 
               data.get('unlock_at') or data.get('lock_at'))
        
        if not due and 'attendance_requirement' in data and isinstance(data['attendance_requirement'], dict):
            due = data['attendance_requirement'].get('due_at') or data['attendance_requirement'].get('late_at')
            
        if not due and 'commons_content' in data and isinstance(data['commons_content'], dict):
            due = data['commons_content'].get('due_at')

        if due:
            found.append((title, due))
            
        for k, v in data.items():
            found.extend(extract_dates(v, title))
            
    elif isinstance(data, list):
        for item in data:
            found.extend(extract_dates(item, current_title))
            
    return found

def main():
    cal = Calendar()
    cal.add('prodid', '-//Hanyang Canvas Combined Calendar//ko//')
    cal.add('version', '2.0')

    total_events = 0
    added_events = set()

    # 1. 기본 공식 ICS 캘린더
    print("1. 기존 공식 캘린더 일정을 가져옵니다...")
    try:
        res_ics = requests.get(ORIGINAL_ICS_URL)
        if res_ics.status_code == 200:
            original_cal = Calendar.from_ical(res_ics.content)
            for component in original_cal.walk():
                if component.name == "VEVENT":
                    cal.add_component(component)
                    added_events.add(str(component.get('summary')))
                    total_events += 1
            print("  -> 완료")
    except Exception as e:
        pass

    # 2. 강좌별 스캔 시작
    print("\n2. 수강 강좌 정밀 스캔 시작...")
    courses_url = f"{CANVAS_BASE_URL}/api/v1/courses"
    courses = get_paginated_data(courses_url, params={"enrollment_state": "active"})

    for course in courses:
        course_id = course.get("id")
        course_name = course.get("name", "Unknown Course")
        if not course_id: continue
        
        print(f"\n[{course_name}] 스캔 중...")

        # --- A. 러닝엑스 우회 스캔 (다양한 엔드포인트 시도) ---
        # 400 에러를 피하기 위해 가장 널리 쓰이는 주소 3가지를 찔러봅니다.
        lx_endpoints = [
            f"{CANVAS_BASE_URL}/learningx/api/v1/courses/{course_id}/allcomponents_with_item",
            f"{CANVAS_BASE_URL}/learningx/api/v1/courses/{course_id}/modules",
            f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/modules?include[]=items" 
        ]
        
        lx_dates_found = []
        for lx_url in lx_endpoints:
            res_lx = requests.get(lx_url, headers=headers)
            if res_lx.status_code == 200:
                lx_dates_found.extend(extract_dates(res_lx.json()))
                if lx_dates_found:
                    break # 하나라도 찾으면 중단

        unique_lx_dates = list({(t, d) for t, d in lx_dates_found})
        
        if unique_lx_dates:
            print(f"  -> [성공] 동영상 마감일 {len(unique_lx_dates)}개 발견!")
            for title, due_str in unique_lx_dates:
                due_dt = parse_iso_datetime(due_str)
                if due_dt:
                    event_key = f"[{course_name}] {title} (마감)"
                    if event_key not in added_events:
                        event = Event()
                        event.add('summary', event_key)
                        event.add('dtstart', due_dt)
                        event.add('dtend', due_dt)
                        event.add('dtstamp', datetime.now(timezone.utc))
                        cal.add_component(event)
                        added_events.add(event_key)
                        total_events += 1
        else:
            print("  -> (러닝엑스 데이터 없음)")

        # --- B. 캔버스 기본 스캔 ---
        modules_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/modules"
        modules = get_paginated_data(modules_url)
        for module in modules:
            items_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/modules/{module.get('id')}/items"
            items = get_paginated_data(items_url)
            for item in items:
                title, content_id = item.get("title", ""), item.get("content_id")
                if item.get("type") == "Assignment" and content_id:
                    res = requests.get(f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments/{content_id}", headers=headers)
                    if res.status_code == 200:
                        due_at = parse_iso_datetime(res.json().get("due_at") or res.json().get("lock_at"))
                        event_key = f"[{course_name}] {title} (마감)"
                        if due_at and event_key not in added_events:
                            event = Event()
                            event.add('summary', event_key)
                            event.add('dtstart', due_at)
                            event.add('dtend', due_at)
                            event.add('dtstamp', datetime.now(timezone.utc))
                            cal.add_component(event)
                            added_events.add(event_key)
                            total_events += 1

    with open(OUTPUT_ICS_PATH, 'wb') as f:
        f.write(cal.to_ical())
    print(f"\n최종 완료: 총 {total_events}개의 일정 저장됨.")

if __name__ == "__main__":
    main()
