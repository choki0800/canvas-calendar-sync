import os
import json
from datetime import datetime, timezone
import requests
from icalendar import Calendar, Event

CANVAS_BASE_URL = "https://learning.hanyang.ac.kr"
API_TOKEN = os.getenv("CANVAS_API_TOKEN", "YOUR_CANVAS_API_TOKEN_HERE")
OUTPUT_ICS_PATH = "canvas_attendance.ics"
ORIGINAL_ICS_URL = "https://learning.hanyang.ac.kr/feeds/calendars/user_4e5iYEV0E33S8Pdjog9xYmlsOaZYHzp4o8MLIzS0.ics"

headers = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Accept": "application/json"
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

# 데이터가 어디에 숨겨져 있든 재귀적으로 모두 파헤쳐서 마감일을 찾는 함수
def find_lx_dates(data, current_title="Unknown"):
    found = []
    if isinstance(data, dict):
        title = data.get('title', data.get('name', current_title))
        
        # 1. 기본 위치 확인
        due = data.get('due_at') or data.get('late_at')
        
        # 2. 러닝엑스 특유의 중첩 위치(출석 요구사항 등) 확인
        if not due and 'attendance_requirement' in data and isinstance(data['attendance_requirement'], dict):
            due = data['attendance_requirement'].get('due_at') or data['attendance_requirement'].get('late_at')
            
        if not due and 'commons_content' in data and isinstance(data['commons_content'], dict):
            due = data['commons_content'].get('due_at')

        if due:
            found.append((title, due))
            
        # 더 깊은 곳 탐색
        for k, v in data.items():
            found.extend(find_lx_dates(v, title))
            
    elif isinstance(data, list):
        for item in data:
            found.extend(find_lx_dates(item, current_title))
            
    return found

def main():
    cal = Calendar()
    cal.add('prodid', '-//Hanyang Canvas Combined Calendar//ko//')
    cal.add('version', '2.0')

    total_events = 0
    added_events = set()

    # 1. 기본 ICS 캘린더
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
        print(f"  -> 오류: {e}")

    # 2. 강좌별 심층 스캔
    print("\n2. 수강 강좌 API 정밀 스캔 시작...")
    courses_url = f"{CANVAS_BASE_URL}/api/v1/courses"
    courses = get_paginated_data(courses_url, params={"enrollment_state": "active"})

    for course in courses:
        course_id = course.get("id")
        course_name = course.get("name", "Unknown Course")
        if not course_id: continue

        print(f"\n[{course_name}] 스캔 중...")

        # --- 러닝엑스(LearningX) 딥 스캔 ---
        lx_url = f"{CANVAS_BASE_URL}/learningx/api/v1/courses/{course_id}/modules"
        res_lx = requests.get(lx_url, headers=headers)
        
        if res_lx.status_code == 200:
            lx_data = res_lx.json()
            lx_dates = find_lx_dates(lx_data)
            unique_lx_dates = list({(t, d) for t, d in lx_dates}) # 중복 제거
            
            if unique_lx_dates:
                print(f"  -> [성공] 러닝엑스 마감일 {len(unique_lx_dates)}개 발견!")
                for title, due_str in unique_lx_dates:
                    due_dt = parse_iso_datetime(due_str)
                    if due_dt:
                        event_key = f"[{course_name}] {title} (동영상 마감)"
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
                print("  -> [실패] 러닝엑스 접속은 성공했으나, 마감일 데이터를 찾을 수 없습니다.")
                print("  -> 데이터 구조 일부:", str(lx_data)[:300]) # 원인 파악용 출력
        else:
            print(f"  -> [접근 거부] 러닝엑스 API 접근 차단됨 (에러 코드: {res_lx.status_code})")

        # --- 기본 캔버스 API 스캔 ---
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
                        event_key = f"[{course_name}] {title} (출석/마감)"
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
