---
title: "HPCU Runtime 개발 계획 10 — 안전·보안·정책 엔진"
version: "1.0"
date: "2026-08-21"
parent: "docs/dev-init-001.md (§28)"
language: "ko-KR"
---

# 10. 안전·보안·정책 엔진

## 1. 개요

모든 action은 policy.allow 통과 후에만 실행된다.
고위험 action은 승인 없이 실행되지 않는다.

## 2. 모듈

| 모듈 | 파일 | 설명 |
|---|---|---|
| Risk Engine | `hpcu/policy/risk_engine.py` | action 위험도 평가 |
| Approval | `hpcu/policy/approval.py` | 사람 승인 흐름 |

## 3. Risk Levels

| Level | 예시 | 승인 요구 |
|---|---|---|
| LOW | 텍스트 읽기, 상태 확인 | 불필요 |
| MEDIUM | 클릭, 입력, 스크롤 | 불필요 |
| HIGH | 결제, 전송, 데이터 반출 | 필요 |
| CRITICAL | 삭제, 권한 변경, 시스템 설정 | 필요 |

## 4. 불변식

- policy.allow 없이 action 실행 금지.
- CAPTCHA·보안 확인 우회 금지.
- 시크릿(.env) 커밋 금지.
- 로그에 키·비밀번호 금지.