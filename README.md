# Multimedia Scenario DB

Android SoC 멀티미디어 파이프라인을 구조화하고 인터랙티브 HTML 뷰로 시각화하는 Python 툴체인입니다.
Video Recording / Playback / YouTube Streaming 등의 시나리오를 YAML로 정의하고,
Cytoscape.js 기반의 DAG 다이어그램과 PPA(Power/Performance/Area) 리스크 요약을 생성합니다.

---

## 요구 사항

- Python 3.10 이상
- `static/cytoscape.min.js` (로컬 번들, 아래 설치 방법 참고)

---

## 설치

```bash
# 1. 가상환경 생성 및 활성화
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash / MSYS2)
# source .venv/bin/activate     # Linux / macOS

# 2. 패키지 설치 (개발 모드)
pip install -e .

# 3. Cytoscape.js 번들 다운로드 (최초 1회)
mkdir -p static
curl -L https://unpkg.com/cytoscape/dist/cytoscape.min.js -o static/cytoscape.min.js
```

> **모든 CLI 명령은 반드시 `.venv` 활성화 상태에서 실행하세요.**

---

## CLI 사용법

### 1. 시나리오 검증 (`validate`)

```bash
python cli.py validate scenarios/usecase/projectA/video_recording.yaml
```

- Pydantic 스키마 검증 (필드 타입·필수 값)
- 참조 무결성 (edge source/target → node id)
- 순환 참조 감지, 고립 노드 경고
- ip_activity.yaml 자동 로드 및 variant ip_modes 참조 확인
- 검증 통과 시 `0 error(s), N warning(s).` 출력

**예시 출력:**
```
[WARNING] ip_activity.ip_instances[MFC].default.source: BW value is 'estimated'
0 error(s), 8 warning(s).
```

---

### 2. HTML 뷰 생성 (`render`)

```bash
# 단일 시나리오 — variant가 있으면 variant별 HTML 자동 생성
python cli.py render scenarios/usecase/projectA/video_recording.yaml
# → output/projectA/video_recording_fhd30.html
# → output/projectA/video_recording_uhd30.html
# → output/projectA/video_recording_fhd60.html
# → output/projectA/video_recording_uhd60.html
# → output/projectA/video_recording_8k30.html

# 출력 경로 직접 지정 (단일 파일 모드)
python cli.py render scenarios/usecase/projectA/video_recording.yaml --output out/rec.html
```

**출력 파일명 규칙:**
- variant 없음: `output/<project>/<slug>.html`
- variant 있음: `output/<project>/<slug>_<variant_id_lower>.html` × N개

**HTML 뷰 기능:**

| 기능 | 설명 |
|------|------|
| Variant 선택기 | 헤더 드롭다운으로 동일 시나리오의 다른 variant로 이동 |
| Executive 탭 | 시나리오 설명·메트릭·리스크 요약 패널, IP 수치 숨김 |
| Architect 탭 | IP 동작 조건·주파수·전력·BW·exec time 상세, variant별 IP 모드 |
| 레이어 토글 | App / Framework / HAL / Kernel / HW / Buffer 개별 on/off |
| 노드 클릭 | 타입·레이어·배지 정보; Architect 모드에서 IP 상세 추가 |
| 엣지 클릭 | 해당 경로 하이라이트, 나머지 dimmed |
| IP Focus | Architect 툴바 드롭다운으로 특정 IP 집중 하이라이트 |
| DPU Composition | DPU 노드 클릭 시 WinScope 스타일 레이어 미리보기 + 스케일 상세 |
| Flow 애니메이션 | `▶ Flow` 버튼으로 data edge에 흐름 애니메이션 |
| 스냅샷 | `⎙ Snapshot` 버튼 → 브라우저 인쇄 다이얼로그 |

**노드 배지:**

| 배지 | 색상 | 의미 |
|------|------|------|
| `C` | 파란색 | Compression (AFBC / SBWC) 활성화 |
| `L` | 초록색 | Last Level Cache (LLC) 사용 |
| `R` | 빨간색 | DPU Rotation 적용 |
| `S` | 보라색 | DPU Scale-down 적용 (source_crop ≠ display_frame) |

---

### 3. GitHub Pages 사이트 빌드 (`build-site`)

```bash
python cli.py build-site
# → docs/index.html  (프로젝트·시나리오 카드 + variant 버튼)
# → docs/scenarios/projectA/video_recording_fhd30.html 등
```

- `scenarios/usecase/` 전체 스캔 → project 단위 그룹화
- index.html: 시나리오 카드 + 첫 번째 variant = 기본 링크, 나머지 variant 버튼 표시
- `_compact.yaml` / `draft_*.yaml` 파일은 기본 제외 (`--include-all`로 포함)

---

### 4. Perfetto 트레이스 파싱 (`parse-trace`)

```bash
python cli.py parse-trace uhd30.perfetto-trace \
  --tp-path /path/to/trace_processor_shell \
  --scenario uhd30_recording \
  --output-dir scenarios/usecase/
```

- `trace_processor_shell` 바이너리 필요 ([Perfetto 릴리즈](https://github.com/google/perfetto/releases))
- process/thread 테이블 + android_logs만 사용 (slice 이름 미사용)
- 감지 항목: 활성 프로세스(SW 레이어 분류), HWC 합성 모드, NPU, ISP 설정, 코덱 타입
- `draft_<name>.yaml` 생성 + 감지 실패 항목은 `_review_flags`에 자동 기록

---

## 시나리오 YAML 구조

파일 위치: `scenarios/usecase/<project>/<name>.yaml`

```yaml
scenario:                          # L0 — 시나리오 메타
  category: video_recording
  name: Video Recording
  version: "1.0"
  description: >
    시나리오 설명 (Executive 뷰 우측 패널에 표시)
  sw_thread: hal_kernel
  output_period_ms: 33.3           # 33.3ms = 30fps
  budget_ms: 30.0
  pipeline_latency_frames: 2
  risks:
    - severity: medium             # high | medium | low
      description: "리스크 설명"

pipeline:                          # L1 — DAG 노드 + 엣지
  nodes:
    - id: isp
      type: hw_ip                  # sw_task | hw_ip | buffer
      label: "ISP_0\n(Exynos ISP)"
      layer: hw                    # app | framework | hal | kernel | hw | memory
      external: false
    - id: enc_buf
      type: buffer
      layer: memory
      label: "Encode Buffer\nNV12 · 3840×2160"
      compression: false           # buffer 전용 배지 필드
      llc: false
      # rotation: true             # DPU가 이 버퍼를 회전하는 경우 (배지 'R')
      # scaling은 DPU composition 기반 자동 감지 (배지 'S')

  edges:
    - id: e_isp_enc
      source: isp
      target: enc_buf
      role: data                   # data | control
      format: NV12
      resolution: "3840x2160"
      fps: 30
      fan_out: true

dpu_compositions:                  # DPU 레이어 합성 구성 (선택)
  - display_id: display
    display_name: "Main Display (FHD+)"
    display_size: {w: 1080, h: 2340}
    planes:
      - name: "Video Frame"
        buffer: decode_buf
        source_crop:   {x: 0, y: 0, w: 1920, h: 1080}
        display_frame: {x: 0, y: 866, w: 1080, h: 608}
        transform: NONE            # NONE | ROT_90 | ROT_180 | ROT_270 | FLIP_H | FLIP_V
        z_order: 0
        plane_alpha: 1.0

variants:                          # 해상도/모드별 variant (선택)
  - id: FHD30
    name: "FHD 30fps (1920×1080)"
    output_period_ms: 33.3
    budget_ms: 30.0
    buffers:                       # buffer 노드 label override
      decode_buf:
        label: "Decode Buffer\nNV12 · 1920×1080"
    edges:                         # edge 속성 override
      e_mfc_decode:
        format: NV12
        resolution: "1920x1080"
        fps: 30
    ip_modes:                      # IP별 활성 모드 선택
      MFC: default
      DPU: default
```

### IP Activity (traces)

```
scenarios/traces/<project>/<scenario_name>/
  ip_activity.yaml    # IP별 default + 해상도/코덱 modes
```

```yaml
ip_instances:
  - id: MFC
    default:
      freq_mhz: 200
      power_mA: 80
      bw_read_gbps: 0.8
      bw_write_gbps: 0.5
      exec_time_ms: 5.0
      source: estimated            # calculated | estimated | measured
    modes:
      - id: H265_UHD30
        condition: "H.265 UHD30"
        freq_mhz: 333
        power_mA: 140
        bw_read_gbps: 1.5
        bw_write_gbps: 0.8
        exec_time_ms: 4.0
        source: estimated
```

traces 경로는 usecase 경로에서 자동 추론합니다:
- `scenarios/usecase/projectA/video_recording.yaml`
- → `scenarios/traces/projectA/video_recording/ip_activity.yaml`

---

## 프로젝트 구조

```
.
├── cli.py                              # CLI 진입점
├── pyproject.toml
├── scenarios/
│   ├── usecase/
│   │   └── projectA/                  # 과제 단위 디렉토리
│   │       ├── video_recording.yaml   # FHD30/UHD30/FHD60/UHD60/8K30 variants
│   │       ├── video_playback.yaml    # FHD30/UHD30/8K30 variants
│   │       └── youtube_playback.yaml  # FHD30 VP9 / UHD30 AV1 / 8K30 AV1 variants
│   └── traces/
│       └── projectA/
│           ├── video_recording/
│           │   └── ip_activity.yaml   # ISP/MFC/DPU/GPU 통합 IP spec
│           ├── video_playback/
│           │   └── ip_activity.yaml
│           └── youtube_playback/
│               └── ip_activity.yaml
├── src/mmscenario/
│   ├── schema/
│   │   ├── models.py                  # Pydantic v2 모델
│   │   ├── loader.py                  # YAML 로딩 + traces 경로 자동 추론
│   │   └── validator.py               # 검증 로직
│   ├── dag/
│   │   └── pipeline.py                # networkx DAG + 레이아웃 계산
│   ├── view/
│   │   ├── renderer.py                # HTML 렌더링 + variant 처리
│   │   ├── data_prep.py               # 모델 → Cytoscape.js elements 변환
│   │   ├── site.py                    # build-site: 전체 사이트 생성
│   │   └── templates/
│   │       └── base.html.j2           # 단일 HTML 템플릿
│   └── perfetto/
│       ├── parser.py
│       └── queries.py
├── static/
│   └── cytoscape.min.js               # 로컬 번들 (HTML inline 삽입)
├── output/                            # 로컬 렌더링 결과 (git 제외)
│   └── projectA/
└── docs/                              # GitHub Pages 배포 대상
    ├── index.html
    └── scenarios/
        └── projectA/
```
