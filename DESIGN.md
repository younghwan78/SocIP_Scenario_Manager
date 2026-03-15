# DESIGN.md — Multimedia Scenario DB 설계 문서

현재 구현된 시스템의 아키텍처, 데이터 모델, 모듈 설계를 기록합니다.

---

## 1. 시스템 개요

Android 멀티미디어 파이프라인(카메라 녹화, 동영상 재생 등)을 SoC PPA 리스크 분석 목적으로
구조화하는 Python 툴체인입니다. 파이프라인을 4계층으로 모델링하고, 인터랙티브 HTML 뷰로
시각화합니다.

```
YAML (L0~L3)
    │
    ├─ validate  → SchemaValidator → 오류/경고 리포트
    ├─ render    → ScenarioPipeline + ViewRenderer → 자기완결 HTML
    └─ parse-trace → PerfettoParser → draft YAML + _review_flags
```

---

## 2. 4계층 데이터 모델

### L0 — Scenario / Task Graph
시나리오 메타데이터. YAML 최상위 `scenario:` 섹션.

| 필드 | 설명 |
|------|------|
| `name` | 시나리오 명칭 (HTML 파일명 기준) |
| `description` | Executive 뷰 우측 패널에 표시되는 자유 텍스트 |
| `output_period_ms` | 프레임 주기 (예: 33.3ms = 30fps) |
| `budget_ms` | SW+HW 합산 레이턴시 예산 |
| `pipeline_latency_frames` | ISP M2M 등 HW 파이프라인 고정 지연 (프레임 단위) |
| `risks` | severity(high/medium/low) + description 목록 |

### L1 — Pipeline DAG
파이프라인 노드와 엣지. YAML `pipeline:` 섹션. 수동 작성.

**Node 타입:**

| `type` | 모양 | 용도 |
|--------|------|------|
| `sw_task` | round-rectangle | Android SW 컴포넌트 (App / Framework / HAL / Kernel) |
| `hw_ip` | rectangle | on-SoC HW IP (ISP, MFC, DPU) 또는 external (sensor, display) |
| `buffer` | barrel (cylinder) | HW IP 간 DMA-buf 공유 버퍼 |

**Node 레이어 (y축):**

```
y=0   App       ← CameraApp
y=160 Framework ← Camera2 API, MediaCodec FW, SurfaceFlinger
y=320 HAL       ← CameraHAL3, Codec2 HAL
y=480 Kernel    ← V4L2 Driver, MFC Driver, DRM/KMS, Storage Write
──────────────── SW ↔ HW 경계 (굵은 구분선)
y=680 HW        ← ISP_0, MFC, DPU, Sensor*, Display*
y=860 Memory    ← enc_buf, preview_buf, bitstream
```

`external: true` 노드(sensor, display)는 같은 HW 레이어에 위치하지만 회색 점선 테두리 + 이탤릭 레이블로 SoC 외부임을 표시합니다.

**Edge 역할:**

| `role` | 시각 스타일 | 의미 |
|--------|-------------|------|
| `data` (기본) | 실선 회색 | HW IP 간 DMA 버퍼 전송 |
| `control` | 점선 보라색 (#8e44ad) | SW가 HW를 구동 — V4L2 ioctl / Codec2 / DRM atomic commit |

`fan_out: true` 엣지는 dashed 스타일 + JS post-init staggered taxi-turn으로 겹침 방지.

### L2 — IP Activity
HW IP별 동작 조건. `scenarios/traces/<name>/l2_ip_activity.yaml`. Perfetto 파싱 또는 수동 작성.

- `freq_mhz`, `voltage_mv`, `active_ratio`
- `variants`: 조건별(codec=H.265 / AV1) 속성 변화 테이블
- `source`: `measured | estimated | calculated`
- `_review_flags`: 불확실 항목 표시

### L3 — Bus / Memory
버스 대역폭 예산. `scenarios/traces/<name>/l3_bus_memory.yaml`.

- `bw_read_gbps`, `bw_write_gbps`, `latency_budget_us`
- `source`: estimated이면 validator가 WARNING 발생

---

## 3. 파일 분리 원칙

```
scenarios/usecase/<name>.yaml        ← L0+L1 (수동 작성, git 관리)
scenarios/traces/<name>/
    l2_ip_activity.yaml              ← L2 (Perfetto 자동 생성 → 수동 보정)
    l3_bus_memory.yaml               ← L3 (Perfetto 자동 생성 → 수동 보정)
```

**Variant 규칙:**
- 토폴로지가 바뀌면 → 별도 시나리오 파일
- 속성만 바뀌면 (codec 타입, 주파수 등) → 동일 파일 내 `variants`

---

## 4. 모듈 설계

### `schema/models.py` — Pydantic v2 모델
순수 데이터 모델만 정의. 로딩 로직 없음.

```
ScenarioFile
  ├── scenario: L0Scenario
  ├── pipeline: L1Pipeline
  │     ├── nodes: list[L1Node]
  │     └── edges: list[L1Edge]
  ├── ip_activity: Optional[L2Activity]
  └── bus_memory: Optional[L3Memory]
```

### `schema/loader.py` — YAML 로딩
- `load_scenario(path)` — L0+L1만 로드
- `load_traces(traces_dir, name)` — L2+L3 로드 (파일 없으면 None)
- `load_full_scenario(path, traces_dir=None)` — L0~L3 자동 병합 (`model_copy`)

### `schema/validator.py` — 검증
`SchemaValidator.validate()` 실행 순서:
1. Pydantic 파싱 (타입·필수 필드)
2. 참조 무결성: edge.source/target → node.id 존재 여부
3. 순환 참조: `ScenarioPipeline.detect_cycles()` 위임
4. 고립 노드: `detect_isolated_nodes()` → WARNING
5. `_override.reason` 비어있으면 ERROR
6. L3 source `estimated` → WARNING
7. Variant 조건 중복 → WARNING

### `dag/pipeline.py` — networkx DAG
`ScenarioPipeline` 클래스:

**레이아웃 계산 (`compute_layout`):**
- `networkx.topological_generations()` → x 좌표 (위상 정렬 순서 × 200px)
- 레이어 → y 좌표 (LAYER_Y 딕셔너리)
- 동일 (레벨, 레이어) 그룹 내 노드는 Y_STEP(80px) 간격으로 수직 분산

```python
LAYER_Y = {
    "app": 0, "framework": 160, "hal": 320, "kernel": 480,
    "hw": 680, "memory": 860
}
X_STEP = 200.0
Y_STEP = 80.0
```

순환 참조 존재 시 spring layout fallback 자동 적용.

**탐색 API:** `upstream()`, `downstream()`, `fanout_downstream()`, `nodes_by_layer()`

### `view/data_prep.py` — 모델 → Cytoscape 변환
렌더링과 분리된 순수 데이터 변환 레이어.

- `build_cytoscape_elements(pipeline, layout)` → Cytoscape.js elements 리스트
- `build_scenario_dict(scenario)` → JSON 직렬화 dict
- `LAYER_STYLE` / `NODE_TYPE_SHAPE` / `EXTERNAL_COLOR` — 시각 스타일 단일 소스

### `view/renderer.py` — HTML 생성
- `slugify(name)` — 시나리오명을 파일명으로 변환 ("UHD30 Video Recording" → "uhd30_video_recording")
- `ViewRenderer.render()` — Jinja2 템플릿 렌더링 후 UTF-8 HTML 저장
- Cytoscape.js를 `static/cytoscape.min.js`에서 읽어 inline 삽입 → 완전한 self-contained HTML

### `view/templates/base.html.j2` — 단일 HTML 템플릿
Executive / Architect 뷰를 하나의 파일에 통합. 탭 전환은 JavaScript로 처리.

**Executive 모드:**
- 우측 패널: 시나리오 설명, 메트릭 테이블, 리스크 요약 (기본 표시)
- 노드 클릭: 타입·레이어·코멘트 (IP 수치 숨김)
- IP/BW 요약 테이블 숨김

**Architect 모드:**
- 툴바: IP Focus 드롭다운 표시
- 우측 패널: IP Summary + BW Summary 테이블 상시 표시
- 노드 클릭: IP Activity (freq, active_ratio, variants) 추가 표시

**레이아웃 시각화:**
- x축: 위상 정렬 순서 (왼쪽 = 이른 단계, 오른쪽 = 늦은 단계) — Perfetto timeline 철학
- y축: 레이어 구분선 + 컬러 레이블 (App→Framework→HAL→Kernel // HW // Buffer)
- 엣지: `curve-style: taxi` (직각 라우팅), fan-out 엣지는 JS로 taxi-turn 분산

### `perfetto/queries.py` — SQL 상수
`parser.py`에서 사용하는 모든 SQL을 분리 관리.

| 상수 | 감지 대상 |
|------|----------|
| `SQL_ACTIVE_PROCESSES` | SW 레이어별 활성 프로세스 |
| `SQL_HWC_COMPOSITION` | HW overlay vs GPU 합성 모드 |
| `SQL_NPU_ACTIVE` | NPU 활성화 여부 |
| `SQL_ISP_CONFIG` | Single / Dual ISP |
| `SQL_CODEC_TYPE` | MediaCodec 타입·방향 |

### `perfetto/parser.py` — 트레이스 파싱
`trace_processor_shell --batch-mode --query-file` subprocess 방식.

감지 결과(`ParseResult`)에서 `generate_draft_yaml()` 호출 시 L0+L1 초안 YAML 생성.
감지 실패 항목은 `_review_flags`에 자동 기록하여 수동 보정 위치를 명시.

---

## 5. 인터랙션 설계

| ID | 트리거 | 동작 |
|----|--------|------|
| I1 | 노드 클릭 | 우측 패널에 노드 속성 표시 (모드별 IP 상세 여부 결정) |
| I2 | 엣지 클릭 | source/target 노드 하이라이트, 나머지 dimmed |
| I3 | IP Focus 드롭다운 | 선택된 IP + 인접 노드 하이라이트 (Architect 전용) |
| I4 | 레이어 토글 버튼 | 해당 레이어 노드 표시/숨김 |
| I5 | 배경 클릭 | 하이라이트 해제, 패널 초기 상태 복구 |
| I6 | ⎙ Snapshot | `window.print()` — @media print CSS로 툴바/패널 숨김 |

---

## 6. 검증 흐름

```
cli.py validate <yaml>
    │
    ├─ load_scenario()        (L0+L1 Pydantic 파싱)
    ├─ _check_referential_integrity()
    ├─ ScenarioPipeline.detect_cycles()
    ├─ ScenarioPipeline.detect_isolated_nodes()
    ├─ load_traces()          (L2+L3 병합)
    ├─ _check_override_reasons()
    ├─ _check_variant_consistency()
    └─ _check_l3_sources()
```

---

## 7. 확장 포인트

| 확장 항목 | 위치 | 비고 |
|-----------|------|------|
| 새 시나리오 추가 | `scenarios/usecase/` | 기존 YAML 복사 후 수정 |
| 새 레이어 타입 | `models.py` L1Node.layer Literal, `pipeline.py` LAYER_Y, `data_prep.py` LAYER_STYLE | 3곳 동기화 필요 |
| Dual-ISP 시나리오 | 별도 YAML (토폴로지 변경) | variant 아님 |
| AV1 codec | 동일 YAML의 `variants` 섹션 (속성 변경만) | |
| GPU composition | 별도 risk 시나리오 또는 risk 항목으로 기술 | |
| 새 Perfetto SQL | `perfetto/queries.py`에 상수 추가 → `parser.py`에서 호출 | |
