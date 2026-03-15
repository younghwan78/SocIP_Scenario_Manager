# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**멀티미디어 시나리오 DB** — Android 멀티미디어 시나리오를 구조화하여 SoC PPA(Power/Performance/Area) 리스크 분석 및 팀 공유를 목적으로 하는 데이터베이스 시스템.

- **우선 시나리오**: UHD30 Video Recording, 4K60 YouTube Playback
- **분석 관점**: Latency/RT 위반 · Memory BW 병목 · Power/Thermal 스파이크
- **공유 대상**: Executive(리스크 요약), SoC Architect(IP 구성, BW/전력 수치)

---

## Planned Directory Structure

```
scenarios/
  ├── rules/      # HW 한계값, RT budget — Architect 수동 관리
  ├── usecase/    # L0/L1 정의 — 자동 초안 + Manual 검토
  └── traces/     # L2/L3 파싱 결과 — Perfetto 자동 생성
```

---

## Data Architecture (4-Layer Model)

### Level 0 · Scenario / Task Graph
SW task 집합 단위. 주요 필드:
- `sw_thread`: Android process/thread (App / Framework / HAL·Kernel)
- `output_period_ms`: throughput 요구사항 (SW+HW 합산 output 주기)
- `budget_ms`: SW+HW 전체 허용 처리시간. `margin = budget - 실행시간`
- `pipeline_latency`: 첫 output까지의 지연 (속성으로만 기록, 분석 대상 아님)
- `dependency[]`: task 간 선후관계 및 buffer 공유 관계

### Level 1 · Pipeline (DAG)
- Node = task, Edge = buffer
- 버퍼 정의: 포맷(NV12/P010 등), 해상도, fps → BW 계산 근거
- SW/HW 경계 명시, fan-out(하나의 buffer → 복수 IP), 분기 조건(overlay vs GPU) 포함

### Level 2 · IP Activity
- IP 인스턴스 구분 (ISP_0, ISP_1 등)
- 동작 주파수, 전압, active_ratio
- `variant` 조건: codec 종류, ISP 구성 등 속성이 달라지는 조건 명시

### Level 3 · Bus/Memory
- BW Read/Write (GB/s), latency budget
- **출처 필드 필수**: `calculated` / `estimated` / `measured`

---

## Key Design Decisions

| 항목 | 결정 |
|------|------|
| Variant 기준 | topology 변경(IP 구성, node 수) → 별도 시나리오 / 속성만 변경(codec 종류) → 같은 시나리오 내 variant |
| Buffer 표현 | L0/L1: Subsystem 경계 node만 표현, 내부 OTF/M2M/DMA는 속성으로 기록. L2/L3: IP spec + BW 수치 직접 기술 |
| timing 우선순위 | 주기성 > 실행시간 > 의존관계 |
| Phase 범위 | Steady-state 중심. Startup/Transition phase는 OUT scope |
| Perfetto slice name | 사용하지 않음. process/thread table, android_logs 기반으로만 판별 |
| `_override` 필드 | 수동 보정 시 변경 이유 기록 필수 |

---

## View Layer (3-layer Timeline)

같은 Model 데이터에서 독자별 View를 렌더링 (Perfetto timeline 철학 동일):

- **SW Layer**: App(CameraApp, YouTube App) / Framework(Camera2 API, MediaCodec, SurfaceFlinger) / HAL·Kernel(CameraHAL, V4L2, DRM/KMS)
- **HW Layer**: ISP_0/ISP_1 · MFC(H.265/AV1 variant) · NPU/GPU · DPU(overlay/blending)
- **Memory Layer**: Buffer 포맷/해상도/fps/사이즈, fan-out, BW 계산 근거

| View | 내용 |
|------|------|
| Executive View | 시나리오 구조 + 리스크 요약 |
| Architect View | 수치 상세 + variant 비교 |

---

## Implementation Roadmap

| Step | 내용 |
|------|------|
| Step 1 | YAML 스키마 초안 작성 — 파일 구조 확정, 필드 정의, 검증 도구 |
| Step 2 | UHD30 Recording 파일럿 + Executive/Architect View 확정 |
| Step 3 | 4K60 YouTube Playback 파일럿 — codec variant, View 포맷 적용 검증 |
| Step 4 | Perfetto 파싱 — 구성 요소 자동 감지 (process/thread/IP, `needs_manual` 자동 표시) |
| Step 5 | Perfetto 파싱 — `usecase/draft.yaml` 자동 생성 (template 매칭) |

---

## Controller / Data Collection

### Perfetto 자동 추출 가능 항목
- IP 활성 구간: ftrace / atrace 기반
- 주파수 샘플: cpufreq, devfreq counter
- Memory BW: vendor PMU counter (있을 때만)

### Manual 보정 필요 항목
- 시나리오 경계(L0): atrace tag 없으면 수동 정의
- Pipeline latency: IP 내부 구조 기반 — 완전 수동
- RT budget: Architect 팀 정의

### DB 수집 전략 단계
- **단기**: Perfetto + Exynos logcat → 구성 요소 자동 감지
- **중기**: template 매칭 → L0/L1 초안 자동 생성
- **장기**: Perfetto counter → L2/L3 PPA 수치 자동 추출
