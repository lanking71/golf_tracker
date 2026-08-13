"""
골프 테마 앱 아이콘(assets/icon.ico)을 생성하는 일회성 스크립트.

PyInstaller 빌드에서 --icon 옵션으로 쓸 .ico 파일을 만든다.
그림 리소스를 따로 구하지 않고, 프로젝트 QSS와 같은 색(배경
#1B4332 짙은 그린, 포인트 #D4A017 골드)으로 "퍼팅 그린 위의 골프공"을
간단히 그려서 만든다. 아이콘을 다시 만들 때만 실행하면 되고,
빌드 자체에는 필요 없다 (assets/icon.ico만 있으면 됨).

실행: venv\\Scripts\\python.exe assets\\make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

BG_COLOR = "#1B4332"  # 짙은 그린
GOLD_COLOR = "#D4A017"  # 골드
BALL_COLOR = "#F7F5EF"  # 골프공 (아이보리 화이트)
DIMPLE_COLOR = "#D8D4C6"  # 공 표면 딤플(살짝 어두운 음영)

SIZE = 256  # 가장 큰 해상도 기준으로 그린 뒤 .ico에 여러 크기로 함께 저장


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 배경: 둥근 모서리 사각형 (짙은 그린) - 앱 QSS의 패널 느낌과 통일
    margin = 8
    radius = 48
    draw.rounded_rectangle(
        [margin, margin, SIZE - margin, SIZE - margin],
        radius=radius,
        fill=BG_COLOR,
    )

    # 퍼팅 라인: 시작점에서 공까지 이어지는 얇은 골드 점선 느낌의 곡선 궤적
    # (앱 화면의 궤적 표시를 아이콘에도 반영)
    line_points = [(60, 190), (100, 150), (150, 110), (188, 90)]
    draw.line(line_points, fill=GOLD_COLOR, width=5, joint="curve")
    for x, y in line_points[:-1]:
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=GOLD_COLOR)

    # 골프공 (아이보리색 원 + 딤플 몇 개)
    ball_center = (188, 90)
    ball_radius = 44
    bx, by = ball_center
    draw.ellipse(
        [bx - ball_radius, by - ball_radius, bx + ball_radius, by + ball_radius],
        fill=BALL_COLOR,
        outline=GOLD_COLOR,
        width=4,
    )
    dimple_radius = 5
    dimple_offsets = [
        (0, 0), (-16, -14), (16, -14), (-16, 14), (16, 14),
        (-22, 0), (22, 0), (0, -22), (0, 22),
    ]
    for dx, dy in dimple_offsets:
        cx, cy = bx + dx, by + dy
        draw.ellipse(
            [cx - dimple_radius, cy - dimple_radius, cx + dimple_radius, cy + dimple_radius],
            fill=DIMPLE_COLOR,
        )

    # 홀(구멍): 좌하단에 작은 골드 링으로 표시해 "퍼팅" 테마를 완성
    hole_center = (56, 196)
    hole_radius = 18
    hx, hy = hole_center
    draw.ellipse(
        [hx - hole_radius, hy - hole_radius, hx + hole_radius, hy + hole_radius],
        fill=BG_COLOR,
        outline=GOLD_COLOR,
        width=5,
    )

    return img


def main() -> None:
    icon = draw_icon()
    out_path = Path(__file__).resolve().parent / "icon.ico"
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon.save(out_path, format="ICO", sizes=sizes)
    print(f"아이콘 생성 완료: {out_path}")


if __name__ == "__main__":
    main()
