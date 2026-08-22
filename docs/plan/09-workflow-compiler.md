---
title: "HPCU Runtime 개발 계획 09 — 워크플로우 컴파일러·경험 축적"
version: "1.0"
date: "2026-08-21"
parent: "docs/dev-init-001.md (§19)"
language: "ko-KR"
---

# 09. 워크플로우 컴파일러·경험 축적

## 1. 개요

성공 trajectory를 재사용 가능한 결정론적 워크플로우로 변환한다.
정상 경로에서 모델 호출 0회가 목표.

## 2. 모듈

`hpcu/compiler/compiler.py`:

| 클래스 | 역할 |
|---|---|
| `TrajectoryParameterizer` | trace → workflow steps |
| `PreconditionInferrer` | pre/postcondition 추론 |
| `ShadowReplayEngine` | offline replay 검증 |
| `WorkflowVersionManager` | 버전 관리, rollback |
| `DriftDetector` | UI 변경 감지 |
| `QualificationReport` | qualified 판정 |

## 3. 승격 규칙

- 한 번 성공: 후보 규칙
- 반복 성공: experimental template
- 여러 해상도/테마/세션: qualified workflow
- drift 감지: 새 버전
- 검증 실패: downgrade 또는 비활성화