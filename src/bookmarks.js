export const TARGET_FOLDER_NAME = "Speed Dial";

export function findOtherBookmarksRoot(tree) {
  const root = Array.isArray(tree) ? tree[0] : tree;
  const children = root?.children ?? [];
  const typedRoots = children.filter((node) => node.folderType === "other");
  const fallbackRoots = children.filter(
    (node) => node.id === "2" || ["기타 북마크", "Other bookmarks"].includes(node.title),
  );
  const otherRoots = typedRoots.length > 0 ? typedRoots : fallbackRoots;
  const rootsWithSpeedDial = otherRoots.filter((node) =>
    (node.children ?? []).some(
      (child) => !child.url && child.title === TARGET_FOLDER_NAME,
    ));
  const syncedRoot = rootsWithSpeedDial.find((node) => node.syncing === true);
  const otherRoot = syncedRoot ?? rootsWithSpeedDial[0] ?? otherRoots[0];

  if (!otherRoot) {
    throw new Error("기타 북마크 폴더를 찾을 수 없습니다.");
  }
  return otherRoot;
}

export function findSpeedDialFolder(otherRoot) {
  const matches = (otherRoot.children ?? []).filter(
    (node) => !node.url && node.title === TARGET_FOLDER_NAME,
  );
  if (matches.length !== 1) {
    throw new Error(`Speed Dial 폴더가 ${matches.length}개 발견되었습니다.`);
  }
  return matches[0];
}

export function collectUrlBookmarks(folder) {
  const bookmarks = [];
  for (const child of folder.children ?? []) {
    if (child.url) {
      bookmarks.push(child);
    } else {
      bookmarks.push(...collectUrlBookmarks(child));
    }
  }
  return bookmarks;
}

export function selectCandidates(bookmarks, renameAll) {
  return renameAll
    ? [...bookmarks]
    : bookmarks.filter((bookmark) => bookmark.title === bookmark.url);
}

export function normalizeTitle(title) {
  return title.replace(/\s+/gu, " ").trim();
}

function normalizeCharset(charset) {
  const normalized = charset.trim().replace(/["']/gu, "").toLowerCase();
  const aliases = {
    cp949: "euc-kr",
    euckr: "euc-kr",
    "ks_c_5601-1987": "euc-kr",
    ms949: "euc-kr",
    "x-windows-949": "euc-kr",
  };
  return aliases[normalized] ?? normalized;
}

export function detectHtmlCharset(bytes, contentType = "") {
  const headerMatch = contentType.match(/charset\s*=\s*["']?([^;\s"']+)/iu);
  if (headerMatch) {
    return normalizeCharset(headerMatch[1]);
  }

  const prefix = bytes.subarray(0, 8192);
  const asciiPrefix = String.fromCharCode(...prefix);
  const metaMatch = asciiPrefix.match(/<meta[^>]+charset\s*=\s*["']?\s*([^\s"'/>;]+)/iu);
  return metaMatch ? normalizeCharset(metaMatch[1]) : "utf-8";
}

export function decodeHtml(bytes, contentType = "") {
  const charset = detectHtmlCharset(bytes, contentType);
  try {
    return new TextDecoder(charset).decode(bytes);
  } catch {
    return new TextDecoder("utf-8").decode(bytes);
  }
}

export function isPlaceholderTitle(title) {
  const placeholderPatterns = [
    /\b(?:sign[ -]?in|log[ -]?in|login)\b/iu,
    /로그인|계정에 로그인/iu,
    /\bredirecting\b|리디렉션/iu,
    /\bjust a moment\b|잠시만/iu,
    /checking your browser|봇 검사/iu,
    /access denied|접근 거부/iu,
    /verify you are human|사람인지 확인/iu,
  ];
  return placeholderPatterns.some((pattern) => pattern.test(title));
}

export function isHttpUrl(url) {
  try {
    return ["http:", "https:"].includes(new URL(url).protocol);
  } catch {
    return false;
  }
}

export function extractUrlHost(url) {
  try {
    const parsedUrl = new URL(url);
    return ["http:", "https:"].includes(parsedUrl.protocol) ? parsedUrl.host : "";
  } catch {
    return "";
  }
}

export async function updateBookmarkTitle(bookmarksApi, bookmark, newTitle) {
  return bookmarksApi.update(bookmark.id, { title: newTitle });
}

export async function runConcurrently(items, worker, limit = 6) {
  const results = new Array(items.length);
  let nextIndex = 0;

  async function runWorker() {
    while (nextIndex < items.length) {
      const currentIndex = nextIndex;
      nextIndex += 1;
      results[currentIndex] = await worker(items[currentIndex], currentIndex);
    }
  }

  const workerCount = Math.min(limit, items.length);
  await Promise.all(Array.from({ length: workerCount }, () => runWorker()));
  return results;
}