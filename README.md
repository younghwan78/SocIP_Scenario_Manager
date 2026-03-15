# Multimedia Scenario DB

Android SoC 멀티미디어 파이프라인을 구조화하고 인터랙티브 HTML 뷰로 시각화하는 Python 툴체인입니다.
UHD 녹화, 4K 스트리밍 등의 시나리오를 YAML로 정의하고, Cytoscape.js 기반의 DAG 다이어그램과 PPA(Power/Performance/Area) 리스크 요약을 생성합니다.

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
python cli.py validate scenarios/usecase/uhd30_recording.yaml
```

- Pydantic 스키마 검증 (필드 타입·필수 값)
- 참조 무결성 (edge source/target → node id)
- 순환 참조 감지
- L2/L3 trace 파일 자동 로드 (`scenarios/traces/<name>/`) 및 보고
- 검증 통과 시 `OK - no issues found.` 출력

**예시 출력 (이슈 있을 때):**
```
[ERROR] pipeline.edges[2].source: Node 'nonexistent' not found in pipeline.nodes
[WARNING] bus_memory.bus_entries[ISP_read].source: BW value is 'estimated' — confirm with measurement when possible
1 error(s), 1 warning(s).
```

---

### 2. HTML 뷰 생성 (`render`)

```bash
python cli.py render scenarios/usecase/uhd30_recording.yaml
```

- 출력 파일: `output/<시나리오명>.html` (YAML의 `scenario.name` 필드 기반 자동 명명)
  - 예: `scenario.name: "UHD30 Video Recording"` → `output/uhd30_video_recording.html`
- `--output <path>` 옵션으로 경로 지정 가능
- L2 IP Activity / L3 Bus Memory trace 데이터 자동 병합

```bash
# 출력 경로 직접 지정
python cli.py render scenarios/usecase/uhd30_recording.yaml --output my_output/uhd30.html
```

**HTML 뷰 기능:**
| 기능 | 설명 |
|------|------|
| Executive 탭 | 시나리오 설명·메트릭·리스크 요약 패널, IP 수치 숨김 |
| Architect 탭 | IP 동작 조건·주파수·활성 비율·BW 테이블, variant 비교 |
| 레이어 토글 | App / Framework / HAL / Kernel / HW / Buffer 개별 on/off |
| 노드 클릭 | 타입·레이어·코멘트 표시; Architect 모드에서 IP 상세 추가 |
| 엣지 클릭 | 해당 경로 하이라이트, 나머지 dimmed |
| IP Focus | Architect 툴바의 드롭다운으로 특정 IP 집중 하이라이트 |
| 스냅샷 | `⎙ Snapshot` 버튼 → 브라우저 인쇄 다이얼로그 |

---

### 3. Perfetto 트레이스 파싱 (`parse-trace`)

```bash
python cli.py parse-trace uhd30.perfetto-trace \
  --tp-path /path/to/trace_processor_shell \
  --scenario uhd30_recording \
  --output-dir scenarios/usecase/
```

- `trace_processor_shell` 바이너리 필요 ([Perfetto 릴리즈](https://github.com/google/perfetto/releases) 다운로드)
- process/thread 테이블 + android_logs만 사용 (slice 이름 미사용 — 벤더간 비호환)
- 감지 항목: 활성 프로세스(SW 레이어 분류), HWC 합성 모드, NPU, ISP 설정, 코덱 타입
- `draft_<name>.yaml` 생성 + 감지 실패 항목은 `_review_flags`에 자동 기록

---

## 시나리오 YAML 구조

파일 위치: `scenarios/usecase/<name>.yaml` (L0 + L1 수동 작성)

```yaml
scenario:                          # L0 — 시나리오 메타
  category: video_recording
  name: UHD30 Video Recording      # HTML 파일명의 기준
  version: "0.3"
  description: >                   # Executive 뷰 우측 패널에 표시
    시나리오 설명 (multi-line 가능)
  sw_thread: hal_kernel            # app | framework | hal_kernel
  output_period_ms: 33.3
  budget_ms: 30.0
  pipeline_latency_frames: 2
  risks:
    - severity: medium             # high | medium | low
      description: "리스크 설명"

pipeline:                          # L1 — DAG 노드 + 엣지
  nodes:
    - id: isp
      type: hw_ip                  # sw_task | hw_ip | buffer
      label: ISP_0
      layer: hw                    # app | framework | hal | kernel | hw | memory
      external: false              # true이면 off-SoC (회색 점선 테두리, 이탤릭)
      comment: "설명"

  edges:
    - id: e_isp_enc
      source: isp
      target: enc_buf
      role: data                   # data (solid) | control (dotted purple)
      format: NV12                 # data edge 속성
      resolution: "3840x2160"
      fps: 30
      fan_out: true                # true이면 dashed + fan-out 분기 표현
      branch_condition: "조건"     # control edge 레이블
```

### L2 / L3 trace 데이터 (자동 생성, 별도 파일)

```
scenarios/traces/<yaml-stem>/
  l2_ip_activity.yaml   # IP 주파수·활성 비율·variant
  l3_bus_memory.yaml    # 버스 BW·레이턴시 예산
```

L2/L3는 `validate` 및 `render` 시 자동 병합됩니다. 직접 작성하거나 `parse-trace`로 초안 생성 후 편집하세요.

---

## 프로젝트 구조

```
.
├── cli.py                          # CLI 진입점
├── pyproject.toml
├── requirements.txt
├── scenarios/
│   ├── usecase/                    # L0+L1 YAML (수동 작성)
│   │   └── uhd30_recording.yaml
│   └── traces/                    # L2+L3 YAML (Perfetto 파싱 결과)
│       └── uhd30_recording/
│           ├── l2_ip_activity.yaml
│           └── l3_bus_memory.yaml
├── src/mmscenario/
│   ├── schema/                     # Pydantic v2 모델 + 검증
│   │   ├── models.py
│   │   ├── loader.py
│   │   └── validator.py
│   ├── dag/                        # networkx DAG + 레이아웃
│   │   └── pipeline.py
│   ├── view/                       # Jinja2 HTML 렌더러
│   │   ├── renderer.py
│   │   ├── data_prep.py
│   │   └── templates/
│   │       └── base.html.j2
│   └── perfetto/                   # Perfetto 트레이스 파서
│       ├── parser.py
│       └── queries.py
├── static/
│   └── cytoscape.min.js            # 로컬 번들 (HTML inline 삽입)
└── output/                         # 생성된 HTML (git 제외)
```
