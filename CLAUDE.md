# 볼 트래커 프로젝트
- 퍼팅 골프공 궤적 추적 프로그램 (상세: PROJECT_PLAN.md 참조)
- 기술: Python, OpenCV, NumPy, PySide6, SQLite
- 개발자는 초보자입니다. 코드에 한국어 주석을 자세히 달아주세요.
- 한 번에 한 단계씩만 구현합니다 (PROJECT_PLAN.md의 12단계 순서대로)
- 각 단계 완료 후 실행 방법을 알려주세요.

## 디렉토리 구조 규칙
- main.py는 프로그램 시작만 담당 (짧게 유지)
- 기능별로 src/ 아래 모듈로 분리:
  - src/camera.py (영상 입력)
  - src/detector.py (공 검출)
  - src/tracker.py (상태 관리·추적·정지 판정)
  - src/calibration.py (모서리 지정·원근 변환)
  - src/storage.py (결과 저장)
  - src/ui/ (main_window.py, styles.py)
- 설정값(HSV, 캘리브레이션)은 config/settings.json에 저장
- 새 기능은 반드시 해당 모듈에 추가하고, main.py에 로직을 넣지 않는다

UI 디자인은 골프 테마로: 배경 
#1B4332(짙은 그린), 버튼은 
#D4A017(골드) 포인트, 둥근 모서리, 깔끔하고 심플하게. QSS(스타일시트)로 적용해줘. 버튼은 아이콘 없이 텍스트만, 여백 넉넉하게.