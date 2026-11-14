---
name: qa-adviser
description: >
  app-info.json에 등록된 application(서버·AWS 리소스·git repo 정보)의 상태를 점검하고, 문제를 직접 수정하거나
  repo를 업데이트하여 배포까지 수행하는 QA/운영 skill입니다. 사용자가 "ob-note 상태 확인", "서버 살아있어?",
  "헬스체크", "배포 후 이상 없는지 봐줘", "에러 로그 확인", "버그 고쳐서 배포해줘", "소스 업데이트하고 배포",
  "QA", "qa-adviser", "앱 점검", "ECS 서비스 상태" 등을 요청하거나 등록된 application의 장애·오동작을
  신고할 때 사용합니다. 이름이 app-info.json에 있는 application이면 "점검/수정/배포" 어느 요청이든 이 skill을 사용하세요.
---

# qa-adviser

`app-info.json`에 기록된 application 정보를 근거로 **상태 확인 → 원인 분석 → 수정 → 배포 → 재검증**을 수행합니다.

## app-info.json

위치: `$WORKING_DIR/skills/qa-adviser/app-info.json` (이 SKILL.md와 같은 폴더)

| 필드 | 설명 |
| --- | --- |
| `source_root` | 소스 다운로드 루트. **항상 `/mnt/workspace/src`** |
| `applications[].name` | application 이름 (사용자가 말한 이름과 매칭) |
| `applications[].repo` | `url`, `branch`, `local_path`, `clone_command` |
| `applications[].server` | 서비스 `url`, `health_check`, CloudFront/ALB 정보 |
| `applications[].aws` | account, region, ECS cluster/service, ECR, S3 등 |
| `applications[].deploy` | 배포 `command`, `working_dir`, `config_file` |

새 application을 등록할 때는 `applications` 배열에 같은 구조로 항목을 추가합니다. 비밀값(API key, 토큰)은 기록하지 않습니다.

## 워크플로

### 1. application 선택
- 요청에서 이름을 찾아 `app-info.json`의 항목을 고릅니다. 하나뿐이고 이름이 생략되면 그것을 사용하고, 모호하면 사용자에게 묻습니다.
- 등록되지 않은 application이면 정보를 요청해 `app-info.json`에 먼저 추가합니다.

### 2. 소스 준비
- `repo.local_path`가 없으면 `source_root` 아래에 clone 합니다 (`clone_command`).
  `/mnt/workspace/src` 밖에는 받지 않습니다.
- 있으면 `git status`로 작업 중인 변경이 없는지 확인한 뒤 `git pull --ff-only`로 최신화합니다. 커밋되지 않은 변경이 있으면 덮어쓰지 말고 사용자에게 알립니다.

### 3. 상태 확인 (읽기 전용, 확인 없이 실행 가능)
1. **헬스체크**: `curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" <health_check>`; 본문(JSON)도 확인
2. **사이트 응답**: `server.url` 루트 HTTP 상태
3. **ECS**: 
   ```bash
   aws ecs describe-services --cluster <ecs_cluster> --services <ecs_service> --region <region> \
     --query 'services[0].{status:status,desired:desiredCount,running:runningCount,pending:pendingCount,events:events[:5].message}'
   ```
4. **Target group 헬스**: `aws elbv2 describe-target-health --target-group-arn <arn>` (ARN은 `describe-target-groups --names <target_group>`로 조회)
5. **로그**: task definition의 awslogs group을 조회한 뒤 `aws logs tail <group> --since 30m` 에서 ERROR/Traceback 확인
6. 최근 배포 이미지 태그와 repo 최신 커밋 비교 (`config.json`의 `latest_image_tag` vs `git log -1`)

결과는 항목별 정상/이상 표로 요약합니다.

### 4. 문제 수정
- 로그와 코드로 **원인을 특정**한 뒤 최소 범위로 수정합니다. 추측성 대규모 수정은 하지 않습니다.
- 가능하면 로컬에서 재현·테스트(`run_local.sh`, 단위 테스트 등)로 검증합니다.
- 수정은 `local_path`의 repo에서 새 브랜치 또는 main에 커밋합니다. 커밋 메시지는 원인과 변경을 설명합니다.

### 5. 배포
배포는 외부에 영향을 주므로 **실행 전에 변경 요약(diff, 대상 환경, 명령)을 보여주고 사용자 확인을 받습니다.** 사용자가 이미 "배포까지 해줘"라고 명시했다면 그 요청이 승인입니다. 단, 요청 범위 밖의 변경은 포함하지 않습니다.

1. `git push origin <branch>` (repo 업데이트)
2. `deploy.working_dir`에서 `deploy.command` 실행 (ob-note: `python installer.py`)
   - 오래 걸리면 백그라운드로 실행하고 완료를 기다립니다.
3. 실패하면 출력 전체를 근거로 원인을 보고합니다. 임의로 재시도를 반복하거나 `--force` 류 옵션을 쓰지 않습니다.

### 6. 배포 후 재검증
- 3단계 점검을 다시 실행해 `runningCount == desiredCount`, 헬스체크 200, 새 이미지 태그 반영, 로그에 신규 에러가 없는지 확인합니다.
- 이상이 있으면 원인을 보고하고, 이전 태그로의 롤백이 필요하면 사용자에게 제안합니다 (확인 후 실행).

## 보고 형식
- 점검: 항목별 상태 표 + 이상 시 원인 가설
- 수정/배포: 변경 파일, 커밋 해시, 배포 명령과 결과, 재검증 결과
- 수행하지 못했거나 건너뛴 단계는 그대로 명시합니다.

## 주의사항
- AWS 자격 증명은 환경에 이미 설정된 것을 사용합니다. `app-info.json`의 `account_id`와 `aws sts get-caller-identity` 결과가 다르면 중단하고 알립니다.
- 데이터 삭제, 인프라 제거(`uninstaller.py` 등), S3 vault 데이터 변경은 명시적 요청 없이 하지 않습니다.
- 로그·설정 출력에 비밀값이 있으면 응답에 그대로 옮기지 않습니다.
