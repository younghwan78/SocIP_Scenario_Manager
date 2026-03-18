# DESIGN.md — Multimedia Scenario DB 설계 문서

현재 구현된 시스템의 아키텍처, 데이터 모델, 모듈 설계를 기록합니다.

---

## 1. 시스템 개요

Android 멀티미디어 파이프라인(카메라 녹화, 동영상 재생 등)을 SoC PPA 리스크 분석 목적으로
구조화하는 Python 툴체인입니다. 파이프라인을 계층 모델로 구조화하고, 인터랙티브 HTML 뷰로
시각화합니다.

```
YAML (scenario + pipeline + ip_activity)
    │
    ├─ validate    → SchemaValidator → 오류/경고 리포트
    ├─ render      → ScenarioPipeline + ViewRenderer → variant별 자기완결 HTML
    ├─ build-site  → site.py → docs/ GitHub Pages 사이트
    └─ parse-trace → PerfettoParser → draft YAML + _review_flags
```

---

## 2. 데이터 모델

### L0 — Scenario 메타데이터
시나리오 메타데이터. YAML 최상위 `scenario:` 섹션.

| 필드 | 설명 |
|------|------|
| `name` | 시나리오 명칭 |
| `description` | Executive 뷰 우측 패널에 표시되는 자유 텍스트 |
| `output_period_ms` | 프레임 주기 (예: 33.3ms = 30fps) |
| `budget_ms` | SW+HW 합산 레이턴시 예산 |
| `pipeline_latency_frames` | HW 파이프라인 고정 지연 (프레임 단위) |
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
y=0    App       ← CameraApp, YouTube App
y=160  Framework ← Camera2 API, MediaCodec, SurfaceFlinger
y=320  HAL       ← CameraHAL3, Codec2 HAL
y=480  Kernel    ← V4L2 Driver, MFC Driver, DRM/KMS
────────────────── SW ↔ HW 경계 (굵은 구분선)
y=640~720  HW    ← ISP_0, MFC, DPU, GPU, Sensor*, Display* (±40 교번 배치)
y=920~1040 Memory← enc_buf, preview_buf, decode_buf (x 순 정렬 후 60px 간격 분산)
```

`external: true` 노드(sensor, display)는 회색 점선 테두리 + 이탤릭 레이블로 SoC 외부임을 표시.

**Buffer 배지:**

| 배지 | 색상 | 의미 | 출처 |
|------|------|------|------|
| `C` | 파란색 | Compression (AFBC/SBWC) 활성화 | YAML `compression: true` |
| `L` | 초록색 | Last Level Cache (LLC) 사용 | YAML `llc: true` |
| `R` | 빨간색 | DPU Rotation 적용 | YAML `rotation: true` |
| `S` | 보라색 | DPU Scale-down 적용 | DPU composition 자동 감지 |

`C`/`L`/`R`은 좌측 배지, `R`/`S`는 우측 배지.

**Edge 역할:**

| `role` | 시각 스타일 | 의미 |
|--------|-------------|------|
| `data` (기본) | 실선 회색 | HW IP 간 DMA 버퍼 전송 |
| `control` | 점선 보라색 | SW가 HW를 구동 (V4L2 ioctl / Codec2 / DRM atomic commit) |

`fan_out: true` 엣지는 dashed 스타일 + JS post-init staggered taxi-turn으로 겹침 방지.

### IP Activity — 통합 IP 스펙
HW IP별 동작 조건 + BW/전력 수치. `scenarios/traces/<project>/<scenario>/ip_activity.yaml`.

```yaml
ip_instances:
  - id: MFC
    default:
      freq_mhz: 200
      power_mA: 80
      bw_read_gbps: 0.8
      bw_write_gbps: 0.5
      exec_time_ms: 5.0
      source: estimated    # calculated | estimated | measured
    modes:
      - id: AV1_UHD30
        condition: "AV1 UHD30"
        freq_mhz: 533
        power_mA: 200
        bw_read_gbps: 2.0
        bw_write_gbps: 1.0
        exec_time_ms: 5.0
        source: estimated
```

`source: estimated`이면 validator가 WARNING 발생. 기존 `l2_ip_activity.yaml` + `l3_bus_memory.yaml`
의 L2/L3 분리 구조를 하나의 `ip_activity.yaml`로 통합.

### DPU Compositions — DPU 레이어 합성 정보
YAML 최상위 `dpu_compositions:` 섹션 (선택). 디스플레이에 출력되는 시나리오에 추가.

```yaml
dpu_compositions:
  - display_id: display
    display_name: "Main Display (FHD+)"
    display_size: {w: 1080, h: 2340}
    planes:
      - name: "Video Frame"
        buffer: decode_buf          # 해당 buffer node id
        source_crop:   {x: 0, y: 0, w: 1920, h: 1080}
        display_frame: {x: 0, y: 866, w: 1080, h: 608}
        transform: NONE             # NONE | ROT_90 | ROT_180 | ROT_270 | FLIP_H | FLIP_V
        z_order: 0
        plane_alpha: 1.0
```

`source_crop` 크기 ≠ `display_frame` 크기이면 `data_prep.py`가 해당 buffer에 `scaling=True` 자동 마킹.
ROT_90 / ROT_270 transform은 w/h를 교환한 후 비교.

### Variants — 해상도/모드별 파생 시나리오
YAML 최상위 `variants:` 섹션. 토폴로지는 동일하고 속성만 다른 경우에 사용.

```yaml
variants:
  - id: FHD30
    name: "FHD 30fps (1920×1080)"
    output_period_ms: 33.3
    budget_ms: 30.0
    buffers:
      decode_buf:
        label: "Decode Buffer\nNV12 · 1920×1080"
    edges:
      e_mfc_decode:
        format: NV12
        resolution: "1920x1080"
        fps: 30
    ip_modes:
      MFC: default      # ip_activity.ip_instances[MFC].modes에서 선택
      DPU: default
```

렌더링 시 variant별로 별도 HTML 생성: `<slug>_<variant_id_lower>.html`.

---

## 3. 파일 구조

```
scenarios/
  usecase/
    projectA/                       ← 과제(project) 단위 디렉토리
      video_recording.yaml          ← L0+L1 + variants (수동 작성)
      video_playback.yaml
      youtube_playback.yaml
  traces/
    projectA/
      video_recording/
        ip_activity.yaml            ← IP 통합 스펙 (Perfetto 자동 → 수동 보정)
      video_playback/
        ip_activity.yaml
      youtube_playback/
        ip_activity.yaml
```

**Traces 경로 자동 추론 (loader.py):**

`scenarios/usecase/projectA/video_recording.yaml`
→ `scenarios/traces/projectA/video_recording/ip_activity.yaml`

경로 중 `usecase` 세그먼트를 `traces`로 교체하고, yaml stem을 서브디렉토리로 추가.

**Variant 규칙:**
- 토폴로지 변경(IP 구성, node 수) → 별도 시나리오 파일
- 속성만 변경(codec 타입, 해상도) → 동일 파일 내 `variants` 섹션

---

## 4. 모듈 설계

### `schema/models.py` — Pydantic v2 모델
순수 데이터 모델만 정의. 로딩 로직 없음.

```
ScenarioFile
  ├── scenario: L0Scenario
  ├── pipeline: L1Pipeline
  │     ├── nodes: list[L1Node]    (type, layer, external, compression, llc, rotation, ...)
  │     └── edges: list[L1Edge]    (role, format, resolution, fps, fan_out, branch_condition)
  ├── ip_activity: Optional[IPActivityDB]
  │     └── ip_instances: list[IPSpec]
  │           ├── default: IPModeSpec   (freq_mhz, power_mA, bw_read/write_gbps, exec_time_ms, source)
  │           └── modes: list[IPModeSpec]  (id, condition, + same fields)
  ├── dpu_compositions: Optional[list[DpuComposition]]
  │     └── planes: list[DpuPlane]   (buffer, source_crop, display_frame, transform, z_order, ...)
  └── variants: list[ScenarioVariant]
        ├── buffers: dict[node_id, field_overrides]
        ├── edges: dict[edge_id, field_overrides]
        └── ip_modes: dict[ip_id, mode_id]
```

### `schema/loader.py` — YAML 로딩
- `load_scenario(path)` — L0+L1만 로드
- `load_full_scenario(path)` — L0+L1+ip_activity 자동 병합 (traces 경로 자동 추론)
- `_traces_dir_for(usecase_path)` — usecase → traces 경로 변환

### `schema/validator.py` — 검증
`SchemaValidator.validate()` 실행 순서:
1. `load_full_scenario()` — ip_activity 포함 전체 로드 (ip_modes 참조 검증을 위해)
2. 참조 무결성: edge.source/target → node.id
3. 순환 참조: `ScenarioPipeline.detect_cycles()` 위임
4. 고립 노드: `detect_isolated_nodes()` → WARNING
5. ip_activity.ip_instances `source: estimated` → WARNING
6. variants[*].ip_modes 키 → ip_activity.ip_instances 존재 확인 → WARNING

### `dag/pipeline.py` — networkx DAG
`ScenarioPipeline` 클래스:

**레이아웃 계산 (`compute_layout`):**
1. `networkx.topological_generations()` → x 좌표 (위상 정렬 순서 × 200px)
2. 레이어 → y 좌표 (LAYER_Y 딕셔너리)
3. **HW 레이어 후처리**: x 순 정렬 → 홀/짝 ±40 교번 배치 (겹침 방지)
4. **Memory 레이어 후처리**: x 순 정렬 → base_y 중심으로 60px 간격 균등 분산

```python
LAYER_Y = {
    "app": 0, "framework": 160, "hal": 320, "kernel": 480,
    "hw": 680, "memory": 980        # memory는 후처리로 실제 y가 달라짐
}
X_STEP     = 200.0
Y_STEP     = 80.0
Y_MEM_STEP = 60.0   # memory 노드 간 수직 간격
Y_HW_ALT   = 40.0   # HW 노드 교번 오프셋
```

순환 참조 존재 시 spring layout fallback 자동 적용.

**탐색 API:** `upstream()`, `downstream()`, `fanout_downstream()`, `nodes_by_layer()`

### `view/data_prep.py` — 모델 → Cytoscape 변환
렌더링과 분리된 순수 데이터 변환 레이어.

- `_scaled_buffer_ids(dpu_compositions)` — DPU scaling 적용 buffer id 집합 반환
  - source_crop vs display_frame 크기 비교 (ROT_90/270은 w/h 교환 후 비교)
- `build_cytoscape_elements(pipeline, layout, dpu_compositions)` → Cytoscape.js elements 리스트
  - `scaling=True` 자동 마킹 (dpu_compositions 기반)
- `build_scenario_dict(scenario)` → JSON 직렬화 dict
- `LAYER_STYLE` / `NODE_TYPE_SHAPE` / `EXTERNAL_COLOR` — 시각 스타일 단일 소스

### `view/renderer.py` — HTML 생성
- `slugify(name)` — 시나리오명을 파일명으로 변환 ("UHD30 Video Recording" → "uhd30_video_recording")
- `_apply_variant(scenario, variant)` — variant 오버라이드 적용 후 새 ScenarioFile 반환 (원본 불변)
  - buffers / edges / L0 timing 오버라이드
- `ViewRenderer.render()` — Jinja2 템플릿 렌더링 후 UTF-8 HTML 저장
- `ViewRenderer.render_all_variants()` — variant별 HTML 생성 + manifest 반환
  - variant 없음: `<slug>.html` 1개
  - variant 있음: `<slug>_<variant_id_lower>.html` × N개
- Cytoscape.js를 `static/cytoscape.min.js`에서 읽어 inline 삽입 → 완전한 self-contained HTML

### `view/site.py` — GitHub Pages 사이트 생성
- `build_site(scenarios_dir, docs_dir, include_all)` — 전체 시나리오 스캔 → 프로젝트별 그룹화
- `_compact.yaml` / `draft_*.yaml` 기본 제외 (`--include-all` 플래그로 포함)
- 출력: `docs/index.html` (프로젝트·시나리오 카드 + variant 버튼) + `docs/scenarios/<project>/*.html`

### `view/templates/base.html.j2` — 단일 HTML 템플릿
Executive / Architect 뷰를 하나의 파일에 통합. 탭 전환 / 인터랙션은 JavaScript로 처리.

**Executive 모드:**
- 우측 패널: 시나리오 설명, 메트릭 테이블, 리스크 요약
- 노드 클릭: 타입·레이어·코멘트 (IP 수치 숨김)

**Architect 모드:**
- 툴바: IP Focus 드롭다운
- 우측 패널: IP Summary + BW Summary 테이블
- 노드 클릭: IPModeSpec 상세 (freq/power/BW/exec_time) 추가 표시
- variant별 활성 ip_mode 하이라이트

**레이아웃 시각화:**
- x축: 위상 정렬 순서 (왼쪽 = 이른 단계, 오른쪽 = 늦은 단계)
- y축: 레이어 구분선 + 컬러 레이블 (App→Kernel // HW // Memory)
- 엣지: `curve-style: taxi` (직각 라우팅), fan-out 엣지는 JS로 taxi-turn 분산
- 레이어 밴드: `hw` y=640~830, `memory` y=830~1300

**DPU Composition 패널:**
- DPU 노드 클릭 시 WinScope 스타일 레이어 미리보기 + 평면 목록
- 각 plane: 이름, buffer, z_order, transform, source_crop → display_frame, alpha
- Scale-down 검출 시: `↓ 0.56x` 표시 + 'S' 배지 + 보라색 강조

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
| I7 | Variant 드롭다운 | 동일 시나리오의 다른 variant HTML로 이동 |
| I8 | DPU 노드 클릭 | DPU Composition 패널 표시 (plane 미리보기 + scale 정보) |
| I9 | ▶ Flow 버튼 | data edge에 흐름 방향 애니메이션 (3초 loop) |

---

## 6. 검증 흐름

```
cli.py validate <yaml>
    │
    ├─ load_full_scenario()       (L0+L1+ip_activity 전체 로드)
    ├─ _check_referential_integrity()   (edge source/target → node.id)
    ├─ ScenarioPipeline.detect_cycles()
    ├─ ScenarioPipeline.detect_isolated_nodes()
    ├─ _check_ip_activity_sources()    (estimated → WARNING)
    └─ _check_ip_modes_refs()          (variants ip_modes → ip_instances id)
```

---

## 7. 확장 포인트

| 확장 항목 | 위치 | 비고 |
|-----------|------|------|
| 새 시나리오 추가 | `scenarios/usecase/<project>/` | 기존 YAML 복사 후 수정 |
| 새 project | `scenarios/usecase/<project>/` + `scenarios/traces/<project>/` | build-site 자동 인식 |
| 새 레이어 타입 | `models.py` L1Node.layer Literal, `pipeline.py` LAYER_Y, `data_prep.py` LAYER_STYLE | 3곳 동기화 필요 |
| Dual-ISP 시나리오 | 별도 YAML (토폴로지 변경) | variant 아님 |
| 새 codec variant | 동일 YAML의 `variants` 섹션 + `ip_activity.yaml` modes 추가 | |
| 새 DPU plane | `dpu_compositions.planes` 추가 — scaling/rotation 자동 감지 | |
| 새 Perfetto SQL | `perfetto/queries.py`에 상수 추가 → `parser.py`에서 호출 | |
