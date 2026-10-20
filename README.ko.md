# AQR (AI Queue Runner)

[English](README.md) | **한국어**

Claude Code, Codex CLI, Gemini CLI에 맡길 코딩 작업을 미리 등록해 두면, 에이전트 사용 가능 여부·우선순위·선행 작업·사용량 제한 리셋 시각에 맞춰 자동으로 실행하는 Windows용 AI 코딩 에이전트 작업 대기열입니다. 같은 저장소의 작업을 git worktree로 병렬 실행하고, 결과를 자동 커밋하거나 draft PR로 올릴 수도 있습니다.

AI Queue Runner의 저장소 진입점입니다.

- [사용법과 현재 상태](ai-queue-runner/README.md)
- [요구사항과 개발 계획](AI_QUEUE_RUNNER_PLAN.md)
- [아키텍처](ai-queue-runner/docs/ARCHITECTURE.md)
- [AI 운영 가이드](ai-queue-runner/docs/AI_GUIDE.md)
- [테스트·검증 요약](experiments/README.md) (영문, CC BY 4.0)
- [기여 안내](CONTRIBUTING.md) (영문)

## 내 프로젝트에서 AQR 사용하기

AQR은 [Apache License 2.0](LICENSE)에 따라 상업적 용도를 포함해 자유롭게 사용·수정·재배포할 수 있습니다. 대신 출처 표기를 부탁드립니다.

- 복사본이나 파생 저작물에는 라이선스가 요구하는 대로 [`NOTICE`](NOTICE) 파일을 함께 포함해 주세요.
- README나 문서에 프로젝트를 언급해 주세요. 예:

  > [AI Queue Runner (AQR)](https://github.com/jasonwpgml/AQR) by Jason ([@jasonwpgml](https://github.com/jasonwpgml))을 기반으로 만들었습니다.

- GitHub의 **"Cite this repository"** 버튼은 [`CITATION.cff`](CITATION.cff)를 사용합니다.
- [`experiments/`](experiments/)의 검증 자료는 CC BY 4.0이며 같은 출처 표기가 필요합니다.

도움이 되었다면 스타도 눌러 주세요.

## 라이선스

[Apache-2.0](LICENSE) © 2026 Jason ([@jasonwpgml](https://github.com/jasonwpgml)). [NOTICE](NOTICE)를 참고하세요. PySide6/Qt(LGPLv3) 등 서드파티 구성 요소는 [THIRD_PARTY_NOTICES.md](ai-queue-runner/THIRD_PARTY_NOTICES.md)에 정리되어 있습니다.
