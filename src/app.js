import {
  collectUrlBookmarks,
  decodeHtml,
  extractUrlHost,
  findOtherBookmarksRoot,
  findSpeedDialFolder,
  isHttpUrl,
  isPlaceholderTitle,
  normalizeTitle,
  runConcurrently,
  selectCandidates,
  updateBookmarkTitle,
} from "./bookmarks.js";

const REQUEST_TIMEOUT_MS = 20_000;
const CONCURRENCY = 6;

const elements = {
  modeControl: document.querySelector("#mode-control"),
  startButton: document.querySelector("#start-button"),
  statusBadge: document.querySelector("#status-badge"),
  notice: document.querySelector("#notice"),
  folderState: document.querySelector("#folder-state"),
  resultBody: document.querySelector("#result-body"),
  emptyState: document.querySelector("#empty-state"),
  candidateCount: document.querySelector("#candidate-count"),
  completedCount: document.querySelector("#completed-count"),
  updatedCount: document.querySelector("#updated-count"),
  failedCount: document.querySelector("#failed-count"),
};

let running = false;
let rows = new Map();

function selectedRenameAll() {
  return document.querySelector('input[name="mode"]:checked').value === "all";
}

async function loadCandidates() {
  const tree = await chrome.bookmarks.getTree();
  const otherRoot = findOtherBookmarksRoot(tree);
  const speedDial = findSpeedDialFolder(otherRoot);
  const bookmarks = collectUrlBookmarks(speedDial);
  return {
    speedDial,
    bookmarks,
    candidates: selectCandidates(bookmarks, selectedRenameAll()),
  };
}

function setNotice(message, type = "") {
  elements.notice.textContent = message;
  elements.notice.className = `notice ${type}`.trim();
}

function setStatus(text) {
  elements.statusBadge.textContent = text;
}

function resetSummary(candidateCount) {
  elements.candidateCount.textContent = String(candidateCount);
  elements.completedCount.textContent = "0";
  elements.updatedCount.textContent = "0";
  elements.failedCount.textContent = "0";
}

function createCell(text, className = "") {
  const cell = document.createElement("td");
  cell.textContent = text;
  if (className) {
    cell.className = className;
  }
  cell.title = text;
  return cell;
}

function renderCandidates(candidates) {
  rows = new Map();
  elements.resultBody.replaceChildren();
  elements.emptyState.hidden = candidates.length > 0;
  elements.emptyState.textContent = candidates.length
    ? ""
    : "현재 모드에서 처리할 북마크가 없습니다.";

  for (const bookmark of candidates) {
    const row = document.createElement("tr");
    const statusCell = createCell("대기", "row-status");
    const newNameCell = createCell("-");
    row.append(
      statusCell,
      createCell(bookmark.title),
      newNameCell,
      createCell(bookmark.url, "url-cell"),
    );
    elements.resultBody.append(row);
    rows.set(bookmark.id, { statusCell, newNameCell });
  }
}

function updateRow(bookmark, status, newName = "-", detail = "") {
  const row = rows.get(bookmark.id);
  if (!row) {
    return;
  }
  const labels = {
    pending: "조회 중",
    updated: bookmark.syncing === false ? "변경됨 · 로컬" : "변경됨",
    fallback: bookmark.syncing === false ? "도메인 · 로컬" : "도메인 대체",
    unchanged: "동일",
    skipped: "건너뜀",
    failed: "실패",
  };
  row.statusCell.textContent = labels[status];
  row.statusCell.className = `row-status ${status}`;
  row.statusCell.title = detail || labels[status];
  row.newNameCell.textContent = newName;
  row.newNameCell.title = newName;
}

async function fetchPageTitle(url) {
  if (!isHttpUrl(url)) {
    throw new Error("지원하지 않는 URL 형식");
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      cache: "no-store",
      credentials: "include",
      headers: {
        Accept: "text/html,application/xhtml+xml",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
      },
      redirect: "follow",
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    const html = decodeHtml(bytes, response.headers.get("content-type") ?? "");
    const documentNode = new DOMParser().parseFromString(html, "text/html");
    const title = normalizeTitle(documentNode.title);
    if (!title) {
      throw new Error("HTML title 없음");
    }
    return title;
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("요청 시간 초과", { cause: error });
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function refreshPreview() {
  if (running) {
    return;
  }
  setStatus("확인 중");
  setNotice("");
  try {
    const { speedDial, bookmarks, candidates } = await loadCandidates();
    renderCandidates(candidates);
    resetSummary(candidates.length);
    elements.folderState.textContent = `Speed Dial · URL ${bookmarks.length}개`;
    const localCount = candidates.filter((bookmark) => bookmark.syncing === false).length;
    if (speedDial.syncing === false || localCount > 0) {
      setNotice(
        "선택한 Speed Dial에 Chrome 동기화 대상이 아닌 로컬 북마크가 포함되어 있습니다.",
        "warning",
      );
    } else {
      setNotice("현재 Chrome 프로필의 동기화 가능한 북마크를 찾았습니다.");
    }
    setStatus("준비");
  } catch (error) {
    renderCandidates([]);
    resetSummary(0);
    elements.folderState.textContent = "Speed Dial 확인 실패";
    setNotice(error.message, "error");
    setStatus("오류");
  }
}

async function processBookmark(bookmark) {
  updateRow(bookmark, "pending");
  let newTitle;
  try {
    newTitle = await fetchPageTitle(bookmark.url);
  } catch (error) {
    const detail = error instanceof TypeError
      ? "네트워크 오류 또는 사이트 요청 차단"
      : error.message;
    const fallbackTitle = extractUrlHost(bookmark.url);
    if (!fallbackTitle) {
      updateRow(bookmark, "failed", detail, detail);
      return "failed";
    }
    if (fallbackTitle === bookmark.title) {
      updateRow(bookmark, "unchanged", fallbackTitle, `제목 조회 실패: ${detail}`);
      return "unchanged";
    }
    try {
      await updateBookmarkTitle(chrome.bookmarks, bookmark, fallbackTitle);
      updateRow(bookmark, "fallback", fallbackTitle, `제목 조회 실패: ${detail}`);
      return "fallback";
    } catch (updateError) {
      const updateDetail = `도메인 대체 저장 실패: ${updateError.message}`;
      updateRow(bookmark, "failed", updateDetail, updateDetail);
      return "failed";
    }
  }

  if (newTitle === bookmark.title) {
    updateRow(bookmark, "unchanged", newTitle);
    return "unchanged";
  }
  if (isPlaceholderTitle(newTitle)) {
    updateRow(bookmark, "skipped", newTitle, "로그인·리디렉션·차단 페이지 제목 보호");
    return "skipped";
  }
  try {
    await updateBookmarkTitle(chrome.bookmarks, bookmark, newTitle);
    updateRow(bookmark, "updated", newTitle);
    return "updated";
  } catch (error) {
    const detail = `북마크 저장 실패: ${error.message}`;
    updateRow(bookmark, "failed", detail, detail);
    return "failed";
  }
}

async function startUpdate() {
  if (running) {
    return;
  }
  running = true;
  elements.startButton.disabled = true;
  elements.modeControl.disabled = true;
  setStatus("처리 중");
  setNotice("");

  try {
    const { bookmarks, candidates } = await loadCandidates();
    renderCandidates(candidates);
    resetSummary(candidates.length);
    elements.folderState.textContent = `Speed Dial · URL ${bookmarks.length}개`;

    let completed = 0;
    let updated = 0;
    let failed = 0;
    await runConcurrently(candidates, async (bookmark) => {
      const result = await processBookmark(bookmark);
      completed += 1;
      updated += ["updated", "fallback"].includes(result) ? 1 : 0;
      failed += result === "failed" ? 1 : 0;
      elements.completedCount.textContent = String(completed);
      elements.updatedCount.textContent = String(updated);
      elements.failedCount.textContent = String(failed);
      return result;
    }, CONCURRENCY);

    const localUpdates = candidates.filter((bookmark) => bookmark.syncing === false).length;
    if (localUpdates > 0) {
      setNotice(
        `처리가 끝났습니다. 로컬 전용 대상 ${localUpdates}개는 다른 기기에 동기화되지 않습니다.`,
        "warning",
      );
    } else {
      setNotice(`처리가 끝났습니다. ${updated}개 이름을 Chrome 북마크 API로 변경했습니다.`);
    }
    setStatus(failed > 0 ? "일부 실패" : "완료");
  } catch (error) {
    setNotice(error.message, "error");
    setStatus("오류");
  } finally {
    running = false;
    elements.startButton.disabled = false;
    elements.modeControl.disabled = false;
  }
}

elements.modeControl.addEventListener("change", refreshPreview);
elements.startButton.addEventListener("click", startUpdate);
refreshPreview();