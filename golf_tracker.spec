# -*- mode: python ; coding: utf-8 -*-
"""
볼 트래커 PyInstaller 빌드 설정.

빌드 명령 (프로젝트 루트, venv 활성화한 상태에서):
    venv\\Scripts\\pyinstaller.exe golf_tracker.spec

결과물: dist\\볼트래커.exe (단일 실행 파일)

이 스펙 파일이 하는 일
-----------------------
- main.py를 시작점으로 삼아 src/ 아래 모든 모듈(camera, detector,
  tracker, calibration, storage, ui/*)을 하나의 exe로 묶는다. 이
  모듈들은 전부 코드에서 직접 import하므로 PyInstaller가 알아서
  찾아 담고, 따로 hiddenimports에 적어줄 필요가 없다.
- PySide6(Qt 위젯 GUI)와 OpenCV(cv2)를 빠짐없이 포함한다. 둘 다
  PyInstaller/pyinstaller-hooks-contrib에 표준 훅이 있어서 필요한
  바이너리(Qt 플랫폼 플러그인 DLL, OpenCV DLL 등)를 자동으로 찾아
  담아준다 - 그래서 PySide6.QtCore/QtGui/QtWidgets만 hiddenimports에
  명시적으로 적어 안전판을 둔다.
- 이 프로그램이 전혀 쓰지 않는 PySide6 하위 모듈(QtWebEngine,
  QtQml/Quick, QtMultimedia 등 - 용량이 특히 큰 것들)은 excludes로
  빼서 exe 용량과 빌드 시간을 줄인다.
- config/settings.json·settings.md를 "최초 실행용 기본값"으로 exe
  안에 함께 담는다(datas). 실행 중에는 이 값을 직접 쓰지 않고, exe
  옆에 config/settings.json이 없을 때만 딱 한 번 복사해서 꺼내놓는다
  (src/config.py의 _ensure_settings_file_exists 참고). 그 이후로는
  exe 옆 파일이 진짜 설정 파일이 되어 사용자가 자유롭게 열어 고칠 수
  있고, data/results.db도 exe 옆에 생긴다(src/storage.py, 임시 폴더
  아님) - 둘 다 src/paths.py가 계산하는 "exe가 있는 폴더" 기준이다.
- assets/icon.ico(골프 테마: 짙은 그린 배경 + 골드 퍼팅 라인 + 골프공)를
  exe 아이콘으로 지정한다.

콘솔 창이 꺼져 있어도(console=False) 카메라 진단 print()가 죽지 않도록
main.py 맨 위에서 sys.stdout/stderr가 None이면 더미로 채워준다.
카메라 연결 문제를 디버깅할 때 print() 출력을 직접 보고 싶다면 아래
EXE(...)의 console 값을 True로 바꾸고 다시 빌드하면 콘솔 창이 함께 뜬다.
"""

import os

block_cipher = None

# SPECPATH는 PyInstaller가 이 .spec 파일 실행 시 자동으로 넣어주는
# 변수로, 이 스펙 파일이 있는 폴더(프로젝트 루트)를 가리킨다. 상대
# 경로 대신 이걸 기준으로 절대 경로를 만들면, 다른 폴더에서
# `pyinstaller 경로/golf_tracker.spec`처럼 실행해도(현재 작업 폴더가
# 프로젝트 루트가 아니어도) 항상 올바른 파일을 찾는다.
ROOT_DIR = SPECPATH

a = Analysis(
    [os.path.join(ROOT_DIR, 'main.py')],
    pathex=[ROOT_DIR],
    binaries=[],
    datas=[
        # exe 안에 "최초 실행용 기본 설정"으로 함께 담아둔다.
        # src/config.py가 exe 옆에 config/settings.json이 없을 때만
        # 이걸 복사해서 꺼내놓는다 (그 이후로는 exe 옆 파일이 진짜 설정이 된다).
        (os.path.join(ROOT_DIR, 'config', 'settings.json'), 'config'),
        (os.path.join(ROOT_DIR, 'config', 'settings.md'), 'config'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 이 프로그램은 QtWidgets/QtCore/QtGui만 쓴다. 나머지 무거운
        # PySide6 하위 모듈은 빼서 exe 용량과 빌드 시간을 줄인다.
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuick3D',
        'PySide6.QtQuickWidgets',
        'PySide6.QtQuickControls2',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtNetworkAuth',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtBluetooth',
        'PySide6.QtNfc',
        'PySide6.QtSensors',
        'PySide6.QtPositioning',
        'PySide6.QtLocation',
        'PySide6.QtSerialPort',
        'PySide6.QtSerialBus',
        'PySide6.QtSql',
        'PySide6.QtTest',
        'PySide6.QtDesigner',
        'PySide6.QtHelp',
        'PySide6.QtSpatialAudio',
        'PySide6.QtTextToSpeech',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DExtras',
        'tkinter',
        'matplotlib',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='볼트래커',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX로 압축하면 exe 용량은 줄지만, 일부 백신이 UPX로 압축된 실행
    # 파일을 의심스럽다고 오탐하는 경우가 있어 꺼둔다.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT_DIR, 'assets', 'icon.ico'),
)
