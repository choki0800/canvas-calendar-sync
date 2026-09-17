import os
from datetime import datetime, timezone
import requests
from icalendar import Calendar, Event

# ================= Configuration =================
CANVAS_BASE_URL = "https://learning.hanyang.ac.kr"
API_TOKEN = os.getenv("CANVAS_API_TOKEN", "YOUR_CANVAS_API_TOKEN_HERE")
OUTPUT_ICS_PATH = "canvas_attendance.ics"

# 처음에 질문자님이 주셨던 한양대 공식 캘린더 ICS URL
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
            print(f"API 호출 실패 ({res.status_code}): {url}")
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

    # ---------------------------------------------------------
    # 1. 기존 공식 캘린더(ICS)를 다운로드하여 그대로 병합하기
    # ---------------------------------------------------------
    print("기존 공식 캘린더 일정을 가져옵니다...")
    try:
        res_ics = requests.get(ORIGINAL_ICS_URL)
        if res_ics.status_code == 200:
            # 가져온 원본 ics 파일을 읽어서 이벤트(VEVENT)만 추출
            original_cal = Calendar.from_ical(res_ics.content)
            original_count = 0
            for component in original_cal.walk():
                if component.name == "VEVENT":
                    cal.add_component(component)
                    original_count += 1
                    total_events += 1
            print(f"-> 기본 공식 일정 {original_count}개 병합 완료.")
        else:
            print(f"-> 기본 캘린더 다운로드 실패: {res_ics.status_code}")
    except Exception as e:
        print(f"-> 기본 캘린더 병합 중 오류 발생: {e}")

    # ---------------------------------------------------------
    # 2. 캘린더에 안 뜨는 API 기반 주차별 마감 기한 추가하기
    # ---------------------------------------------------------
    print("\nAPI로 주차별 출석/마감 일정을 분석합니다...")
    courses_url = f"{CANVAS_BASE_URL}/api/v1/courses"
    courses = get_paginated_data(courses_url, params={"enrollment_state": "active"})

    api_count = 0
    for course in courses:
        course_id = course.get("id")
        course_name = course.get("name", "Unknown Course")
        if not course_id:
            continue

        modules_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/modules"
        modules = get_paginated_data(modules_url)

        for module in modules:
            module_id = module.get("id")
            module_name = module.get("name", "")
            
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
                    event = Event()
                    event.add('summary', f"[{course_name}] {title} (출석/마감)")
                    event.add('dtstart', due_at)
                    event.add('dtend', due_at)
                    event.add('dtstamp', datetime.now(timezone.utc))
                    event.add('description', f"강좌: {course_name}\n주차/모듈: {module_name}\n항목: {title}\nURL: {item.get('html_url', '')}")
                    event.add('uid', f"canvas-item-{item.get('id')}@hanyang.ac.kr")

                    cal.add_component(event)
                    api_count += 1
                    total_events += 1

    print(f"-> 추가된 주차별 마감 일정: {api_count}개")

    # ---------------------------------------------------------
    # 3. 최종 통합 파일 저장
    # ---------------------------------------------------------
    with open(OUTPUT_ICS_PATH, 'wb') as f:
        f.write(cal.to_ical())

    print(f"\n완료: 총 {total_events}개의 일정이 '{OUTPUT_ICS_PATH}'에 통합 저장되었습니다.")

if __name__ == "__main__":
    main()
