# Yet Another Speed Dial 북마크 이름 갱신

[Yet Another Speed Dial](https://chromewebstore.google.com/detail/yet-another-speed-dial/imohnlganmafcmidafklgkgfgaagiohn?hl=ko)의 북마크 이름을 웹 페이지의 HTML `title`로 갱신하는 Chrome 확장 프로그램이다.

현재 Chrome 프로필의 `기타 북마크 > Speed Dial`과 모든 하위 폴더를 처리한다. 이름 변경은 공식 `chrome.bookmarks.update()` API를 사용하므로 Chrome이 변경 이벤트를 감지하고 북마크 동기화 대상으로 등록한다. 다른 북마크 폴더는 수정하지 않는다.

## 설치

1. 주소창에서 `chrome://extensions`를 연다.
2. 우측 상단의 `개발자 모드`를 켠다.
3. `압축해제된 확장 프로그램을 로드합니다`를 선택한다.
4. 이 저장소의 루트 디렉터리를 선택한다.
5. 도구 모음에서 `Yet Another Speed Dial 이름 갱신`을 고정한다.

Chrome 프로필마다 확장 프로그램이 독립적으로 설치되므로, 사용할 프로필에서 위 설치를 진행한다. Python, uv, Chrome 종료 및 `Bookmarks` 파일 직접 수정은 더 이상 필요하지 않다.

## 실행

1. 도구 모음에서 확장 프로그램 아이콘을 누른다.
2. 처리 범위를 선택한다.
3. `갱신 시작`을 누른다.

처리 범위는 다음 두 가지다.

- `URL과 같은 이름만`: 북마크 이름과 URL이 정확히 같은 항목만 갱신한다.
- `전체 북마크`: `Speed Dial` 아래의 모든 URL 북마크를 갱신한다.

화면에는 각 항목의 기존 이름, 변경된 이름, URL과 처리 상태가 표시된다. UTF-8뿐 아니라 HTTP 헤더 또는 HTML `meta` 태그에 선언된 EUC-KR·CP949 문자셋도 감지해 한글 제목을 복원한다.

현재 Chrome 세션 쿠키를 포함해 페이지를 요청한다. 응답 제목이 로그인, 리디렉션, 봇 검사 또는 접근 거부 페이지로 판단되면 기존 이름을 덮어쓰지 않고 `건너뜀`으로 표시한다.

HTTP 오류, 요청 시간 초과, 네트워크 차단 또는 HTML `title` 누락으로 제목 조회에 실패하면 URL의 스킴 뒤에서 첫 경로 앞까지의 호스트를 이름으로 사용한다. 예를 들어 `https://www.coupangplay.com/home`은 `www.coupangplay.com`이 되고, 포트가 있는 `http://yongflix.com:32400/web/`은 `yongflix.com:32400`이 된다. 처리 상태는 `도메인 대체`로 표시한다. 호스트 추출 또는 북마크 저장까지 실패한 경우에만 최종 `실패`로 표시한다.

사이트가 확장 프로그램 요청을 차단하거나 봇 검사를 요구하는 경우, 사설 서버의 인증서가 유효하지 않은 경우 또는 현재 PC에서 서버에 연결할 수 없는 경우에는 제목을 가져올 수 없다.

## 동기화

동기화가 활성화된 북마크는 이름 변경 후 같은 Google 계정을 사용하는 다른 Chrome으로 전파된다. 다음 조건이 충족되어야 한다.

- Chrome 로그인 및 북마크 동기화가 활성화되어 있어야 한다.
- `Speed Dial`과 대상 북마크가 로컬 전용이 아닌 동기화 저장소에 있어야 한다.
- 다른 기기의 Chrome이 동기화를 완료할 시간이 필요할 수 있다.

Chrome이 해당 노드를 로컬 전용으로 보고하면 화면에 경고하고 처리 상태에 `로컬`을 표시한다. 이런 항목은 API로 이름을 변경해도 다른 기기에 동기화되지 않는다.

## 권한

- `bookmarks`: `Speed Dial` 북마크를 조회하고 이름을 변경한다.
- `http://*/*`, `https://*/*`: 각 북마크 URL의 HTML 제목을 조회한다.

## 검증

Node.js 20 이상에서 별도 패키지 설치 없이 검증할 수 있다.

```powershell
npm test
npm run check
```
