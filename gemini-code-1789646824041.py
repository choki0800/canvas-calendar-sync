import os
from datetime import datetime, timezone
import requests
from icalendar import Calendar, Event

# ================= Configuration =================
CANVAS_BASE_URL = "https://learning.hanyang.ac.kr"
# 발급받은 Canvas Access Token 입력 (또는 환경변수 설정)
API_TOKEN = os.getenv("CANVAS_API_TOKEN", "YOUR_CANVAS_API_TOKEN_HERE")
OUTPUT_ICS_PATH = "canvas_attendance.ics"
# =================================================

headers = {
    "Authorization": f"Bearer {API_TOKEN}"
}

def get_paginated_data(url, params=None):
    """Canvas API의 페이지네이션(Link 헤더)을 처리하여 전체 목록을 가져옵니다."""
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
        
        # Link 헤더에서 next URL 파싱
        links = res.links
        if 'next' in links:
            url = links['next']['url']
            params = {}  # 다음 URL에는 파라미터가 이미 포함되어 있음
        else:
            url = None
    return results

def parse_iso_datetime(dt_str):
    if not dt_str:
        return None
    # 'Z' 타임존 처리
    dt_str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(dt_str)

def main():
    print("수강 중인 활성 강좌 목록을 불러옵니다...")
    courses_url = f"{CANVAS_BASE_URL}/api/v1/courses"
    courses = get_paginated_data(courses_url, params={"enrollment_state": "active"})

    cal = Calendar()
    cal.add('prodid', '-//Hanyang Canvas Attendance Calendar//ko//')
    cal.add('version', '2.0')

    event_count = 0

    for course in courses:
        course_id = course.get("id")
        course_name = course.get("name", "Unknown Course")
        if not course_id:
            continue

        print(f"[{course_name}] 모듈 및 항목 분석 중...")
        modules_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/modules"
        modules = get_paginated_data(modules_url)

        for module in modules:
            module_id = module.get("id")
            module_name = module.get("name", "")
            
            # 모듈 자체의 잠금 해제/마감 기한 확인
            module_unlock = parse_iso_datetime(module.get("unlock_at"))

            # 모듈 내부 항목(items) 조회
            items_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/modules/{module_id}/items"
            items = get_paginated_data(items_url)

            for item in items:
                title = item.get("title", "")
                item_type = item.get("type", "")
                content_id = item.get("content_id")
                
                due_at = None

                # 1. Assignment / ExternalTool(동영상 출석 등) 기한 조회
                if item_type == "Assignment" and content_id:
                    detail_url = f"{CANVAS_BASE_URL}/api/v1/courses/{course_id}/assignments/{content_id}"
                    res = requests.get(detail_url, headers=headers)
                    if res.status_code == 200:
                        assignment_data = res.json()
                        due_at = parse_iso_datetime(assignment_data.get("due_at"))
                        if not due_at:
                            due_at = parse_iso_datetime(assignment_data.get("lock_at"))

                # 2. 기한이 명시되지 않은 동영상/외부도구의 경우 모듈 마감 또는 완료 조건 확인
                completion_req = item.get("completion_requirement", {})
                
                # 마감일이 있는 경우 캘린더 이벤트 생성
                if due_at:
                    event = Event()
                    event.add('summary', f"[{course_name}] {title} (출석/마감)")
                    event.add('dtstart', due_at)
                    event.add('dtend', due_at)
                    event.add('dtstamp', datetime.now(timezone.utc))
                    event.add('description', f"강좌: {course_name}\n주차/모듈: {module_name}\n항목: {title}\nURL: {item.get('html_url', '')}")
                    event.add('uid', f"canvas-item-{item.get('id')}@hanyang.ac.kr")

                    cal.add_component(event)
                    event_count += 1

    with open(OUTPUT_ICS_PATH, 'wb') as f:
        f.write(cal.to_ical())

    print(f"\n완료: 총 {event_count}개의 마감 일정을 '{OUTPUT_ICS_PATH}' 파일로 저장했습니다.")

if __name__ == "__main__":
    main()