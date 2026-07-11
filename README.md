# Yet Another Speed Dial 북마크 이름 갱신

[Yet Another Speed Dial](https://chromewebstore.google.com/detail/yet-another-speed-dial/imohnlganmafcmidafklgkgfgaagiohn?hl=ko) Chrome 확장 프로그램용 북마크 이름 갱신 도구다.

Chrome 기본 프로필의 `기타 북마크 > Speed Dial` 아래에 있는 URL 북마크 이름을 웹 페이지의 HTML `title`로 갱신한다. `Speed Dial`의 모든 하위 폴더를 재귀적으로 처리하며, 다른 폴더의 북마크는 수정하지 않는다.

## 준비

- Python 3.13
- uv
- 실행 중인 Chrome이 있으면 종료 여부를 묻고, 동의한 경우 모든 Chrome 프로세스를 종료한 뒤 작업함

```powershell
uv sync
```

## 실행

이름과 URL이 정확히 같은 북마크만 갱신한다.

```powershell
uv run bookmark-rename
```

`Speed Dial` 아래의 모든 URL 북마크를 갱신한다.

```powershell
uv run bookmark-rename --all
```

Chrome이 실행 중이면 다음 확인 메시지가 나타난다.

```text
Chrome이 실행 중입니다. Chrome을 종료하고 계속하시겠습니까? (y/n):
```

`y`를 입력하면 Chrome 프로세스를 모두 종료하고 북마크 작업을 계속한다. `n`을 입력하면 Chrome을 종료하지 않고 스크립트를 끝낸다.

변경된 항목은 북마크 ID, 기존 이름과 변경된 이름을 로그로 출력한다. HTTP 오류, 타임아웃, 지원하지 않는 URL 스킴 또는 HTML `title`이 없는 항목은 기존 이름을 유지한다. 저장할 변경이 있으면 원본과 같은 디렉터리에 `Bookmarks.backup-날짜-시간` 형식의 백업을 먼저 생성한다. 처리 중 원본 파일이 바뀌면 저장을 중단한다.

## 검증

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
