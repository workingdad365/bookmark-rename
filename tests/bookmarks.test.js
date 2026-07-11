import assert from "node:assert/strict";
import test from "node:test";

import {
  collectUrlBookmarks,
  decodeHtml,
  detectHtmlCharset,
  extractUrlHost,
  findOtherBookmarksRoot,
  findSpeedDialFolder,
  isHttpUrl,
  isPlaceholderTitle,
  normalizeTitle,
  runConcurrently,
  selectCandidates,
  updateBookmarkTitle,
} from "../src/bookmarks.js";

function fixture() {
  return [{
    id: "0",
    children: [
      { id: "1", folderType: "bookmarks-bar", children: [] },
      {
        id: "2",
        title: "기타 북마크",
        folderType: "other",
        children: [
          {
            id: "4",
            title: "Speed Dial",
            children: [
              { id: "5", title: "https://same.example", url: "https://same.example" },
              { id: "6", title: "기존 이름", url: "https://named.example" },
              {
                id: "7",
                title: "하위 폴더",
                children: [
                  { id: "8", title: "https://nested.example", url: "https://nested.example" },
                ],
              },
            ],
          },
          { id: "9", title: "범위 밖", url: "https://outside.example" },
        ],
      },
    ],
  }];
}

test("Speed Dial 하위 URL만 재귀적으로 수집한다", () => {
  const otherRoot = findOtherBookmarksRoot(fixture());
  const speedDial = findSpeedDialFolder(otherRoot);

  assert.deepEqual(
    collectUrlBookmarks(speedDial).map((bookmark) => bookmark.id),
    ["5", "6", "8"],
  );
});

test("로컬과 계정용 기타 북마크가 함께 있으면 동기화 Speed Dial을 선택한다", () => {
  const tree = fixture();
  const syncedOther = tree[0].children[1];
  syncedOther.syncing = true;
  tree[0].children.splice(1, 0, {
    id: "local-other",
    title: "기타 북마크",
    folderType: "other",
    syncing: false,
    children: [{ id: "local-speed", title: "Speed Dial", syncing: false, children: [] }],
  });

  assert.equal(findOtherBookmarksRoot(tree).id, "2");
});

test("기본 모드와 전체 모드의 대상을 구분한다", () => {
  const speedDial = findSpeedDialFolder(findOtherBookmarksRoot(fixture()));
  const bookmarks = collectUrlBookmarks(speedDial);

  assert.deepEqual(selectCandidates(bookmarks, false).map((bookmark) => bookmark.id), ["5", "8"]);
  assert.deepEqual(selectCandidates(bookmarks, true).map((bookmark) => bookmark.id), ["5", "6", "8"]);
});

test("제목 공백과 URL 스킴을 정규화한다", () => {
  assert.equal(normalizeTitle("  Example &\n Test  "), "Example & Test");
  assert.equal(isHttpUrl("https://example.com"), true);
  assert.equal(isHttpUrl("chrome://bookmarks"), false);
});

test("동시 실행 결과는 입력 순서를 유지한다", async () => {
  const results = await runConcurrently([3, 1, 2], async (value) => value * 2, 2);
  assert.deepEqual(results, [6, 2, 4]);
});

test("Chrome Bookmarks API에 제목 변경만 전달한다", async () => {
  const calls = [];
  const bookmarksApi = {
    async update(id, changes) {
      calls.push({ id, changes });
      return { id, ...changes };
    },
  };

  await updateBookmarkTitle(bookmarksApi, { id: "5" }, "변경된 이름");

  assert.deepEqual(calls, [{ id: "5", changes: { title: "변경된 이름" } }]);
});

test("HTTP 헤더와 meta 태그에서 EUC-KR 문자셋을 감지한다", () => {
  const meta = new TextEncoder().encode('<meta charset="euc-kr">');

  assert.equal(detectHtmlCharset(meta), "euc-kr");
  assert.equal(detectHtmlCharset(meta, "text/html; charset=CP949"), "euc-kr");
});

test("EUC-KR로 인코딩된 한글 HTML을 디코딩한다", () => {
  const prefix = new TextEncoder().encode('<meta charset="euc-kr"><title>');
  const suffix = new TextEncoder().encode("</title>");
  const encodedKorean = Uint8Array.from([0xc7, 0xd1, 0xb1, 0xdb]);
  const bytes = new Uint8Array(prefix.length + encodedKorean.length + suffix.length);
  bytes.set(prefix);
  bytes.set(encodedKorean, prefix.length);
  bytes.set(suffix, prefix.length + encodedKorean.length);

  assert.match(decodeHtml(bytes), /<title>한글<\/title>/u);
});

test("로그인과 리디렉션 같은 중간 페이지 제목을 감지한다", () => {
  assert.equal(isPlaceholderTitle("Sign in - Google Accounts"), true);
  assert.equal(isPlaceholderTitle("리디렉션 중"), true);
  assert.equal(isPlaceholderTitle("국립세종도서관 전자책"), false);
});

test("URL에서 스킴 뒤 첫 경로 앞까지 호스트를 추출한다", () => {
  assert.equal(extractUrlHost("https://www.coupangplay.com/home"), "www.coupangplay.com");
  assert.equal(extractUrlHost("http://yongflix.com:32400/web/"), "yongflix.com:32400");
  assert.equal(extractUrlHost("chrome://bookmarks"), "");
});