import os
from datetime import datetime, timezone
import requests
from icalendar import Calendar, Event

# ================= Configuration =================
CANVAS_BASE_URL = "https://learning.hanyang.ac.kr"
API_TOKEN = os.getenv("CANVAS_API_TOKEN", "YOUR_CANVAS_API_TOKEN_HERE")
OUTPUT_ICS_PATH = "canvas_attendance.ics"
ORIGINAL_ICS_URL = "https://learning.hanyang.ac.kr/feeds/calendars/user_4e5iYEV0E33S8Pdjog9xYmlsOaZYHzp4o8MLIzS0.ics"
# =================================================

headers = {
    "Authorization": f"Bearer {API_TOKEN}"
}

def get_paginated_data(url, params=None):
    results = []
    if params is None:
        params = {}
    params["per_page"] = 100

    while url:
        res = requests.get(url, headers=headers, params=params)
        if res.status_code != 200:
            break
        results.extend(res.json())
        
        links = res.links
        if 'next' in links:
            url = links['next']['url']
            params = {}
        else:
            url = None
    return results

def parse_iso_datetime(dt_str):
    if not dt_str:
        return None
    dt_str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(dt_str)

def main():
    cal = Calendar()
    cal.add('prodid', '-//Hanyang Canvas Combined Calendar//ko//')
    cal.add('version', '2.0')

    total_events = 0
    api_count = 0
    added_events = set() # 중복 일정 생성 방지용

    # 1. 기존 공식 ICS 병합
    print("기존 공식 캘린더 일정을 가져옵니다...")
    try:
        res_ics = requests.get(ORIGINAL_ICS_URL)
        if res_ics.status_code == 200:
            original_cal = Calendar.from_ical(res_ics.content)
            original_count = 0
            for component in original_cal.walk():
                if component.name == "VEVENT":
                    cal.add_component(component)
                    # 중복을 막기 위해 기존 일정 제목을 세트에 저장
                    added_events.add(str(component.get('summary')))
                    original_count += 1
                    total_events += 1
            print(f"-> 기본 공식 일정 {original_count}개 병합 완료.")
    except Exception as e:
        print(f"-> 기본 캘린더 병합 중 오류 발생: {e}")

    # 2. 강좌 목록 가져오기
    print("\n수강 강좌 목록을 가져옵니다...")
    courses_url = f"{CANVAS_BASE_URL}/api/v1/courses"
    courses = get_paginated_data(courses_url, params={"enrollment_state": "active"})

    for course in courses:
        course_id = course.get("id")
        course_name = course.get("name", "Unknown Course")
        if not course_id:
            continue

        print(f"[{course_name}] 마감 일정 분석 중 (기본 + 러닝엑스)...")

        # --- A. LearningX (러닝엑스) 전용 숨겨진 API 스캔 ---
        lx_url = f"{CANVAS_BASE_URL}/learningx/api/v1/courses/{course_id}/modules"
        res_lx = requests.get(lx_url, headers=headers)
        if res_lx.status_code == 200:
            try:
                lx_modules = res_lx.json()
                for lx_mod in lx_modules:
                    for lx_item in lx_mod.get('module_items', []):
                        title = lx_item.get('title', '')
                        due_at_str = lx_item.get('due_at') # 러닝엑스 내의 마감일 추출
                        
                        if due_at_str:
                            due_at = parse_iso_datetime(due_at_str)
                            event_key = f"[{course_name}] {title} (동영상 마감)"
                            
                            # 중복이 아니라면 캘린더에 추가
                            if due_at and event_key not in added_events:
                                event = Event()
                                event.add('summary', event_key)
                                event.add('dtstart', due_at)
                                event.add('dtend', due_at)
                                event.add('dtstamp', datetime.now(timezone.utc))
                                event.add('description', f"강좌: {course_name}\n항목: {title}\n(LearningX 동영상 시스템 마감)")
                                event.add('uid', f"lx-item-{lx_item.get('id', title)}@hanyang.ac.kr")
                                
                                cal.add_component(event)
                                added_events.add(event_key)
                                api_count += 1
                                total_events += 1
            except Exception as e:
                pass # 러닝엑스 시스템이 아닌 일반 과목은 조용히 패스

        # --- B. 캔버스(Canvas) 기본 API 스캔 (과제, 일반 출석 등) ---
        modules_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/modules"
        modules = get_paginated_data(modules_url)

        for module in modules:
            module_id = module.get("id")
            items_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/modules/{module_id}/items"
            items = get_paginated_data(items_url)

            for item in items:
                title = item.get("title", "")
                item_type = item.get("type", "")
                content_id = item.get("content_id")
                due_at = None

                if item_type == "Assignment" and content_id:
                    detail_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments/{content_id}"
                    res = requests.get(detail_url, headers=headers)
                    if res.status_code == 200:
                        assignment_data = res.json()
                        due_at = parse_iso_datetime(assignment_data.get("due_at"))
                        if not due_at:
                            due_at = parse_iso_datetime(assignment_data.get("lock_at"))
                
                if due_at:
                    event_key = f"[{course_name}] {title} (출석/마감)"
                    if event_key not in added_events:
                        event = Event()
                        event.add('summary', event_key)
                        event.add('dtstart', due_at)
                        event.add('dtend', due_at)
                        event.add('dtstamp', datetime.now(timezone.utc))
                        event.add('description', f"강좌: {course_name}\n항목: {title}")
                        event.add('uid', f"canvas-item-{item.get('id')}@hanyang.ac.kr")

                        cal.add_component(event)
                        added_events.add(event_key)
                        api_count += 1
                        total_events += 1

    print(f"-> API로 새롭게 추가된 마감 일정: {api_count}개")

    with open(OUTPUT_ICS_PATH, 'wb') as f:
        f.write(cal.to_ical())

    print(f"\n완료: 총 {total_events}개의 일정이 '{OUTPUT_ICS_PATH}'에 통합 저장되었습니다.")

if __name__ == "__main__":
    main()
