from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import psutil
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

LOGGER = logging.getLogger(__name__)
TARGET_FOLDER_NAME = "Speed Dial"
ROOT_KEYS = ("bookmark_bar", "other", "synced")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
)

type JsonObject = dict[str, Any]


class BookmarkNode(BaseModel):
    """Chrome 북마크 노드의 필수 구조를 검증한다."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    type: Literal["folder", "url"]
    url: str | None = None
    children: list[BookmarkNode] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_node_data(self) -> BookmarkNode:
        """노드 유형에 필요한 필드를 검증한다."""
        if self.type == "url" and self.url is None:
            raise ValueError("URL 노드에 url 필드가 없음")
        return self


class BookmarkRoots(BaseModel):
    """Chrome의 영구 북마크 루트 구조를 검증한다."""

    model_config = ConfigDict(extra="allow")

    bookmark_bar: BookmarkNode
    other: BookmarkNode
    synced: BookmarkNode


class BookmarkFile(BaseModel):
    """Chrome Bookmarks 파일의 처리 필드를 검증한다."""

    model_config = ConfigDict(extra="allow")

    version: int
    roots: BookmarkRoots


def default_bookmarks_path() -> Path:
    """현재 Windows 사용자의 기본 Chrome 북마크 경로를 반환한다."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA environment variable is not set")
    return Path(local_app_data) / "Google" / "Chrome" / "User Data" / "Default" / "Bookmarks"


def find_chrome_processes() -> list[psutil.Process]:
    """현재 실행 중인 Chrome 프로세스를 반환한다."""
    processes: list[psutil.Process] = []
    for process in psutil.process_iter(["name"]):
        try:
            name = process.info.get("name")
            if name and name.casefold() in {"chrome", "chrome.exe"}:
                processes.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return processes


def kill_chrome_processes(processes: list[psutil.Process]) -> None:
    """Chrome 프로세스를 강제 종료하고 종료 완료를 기다린다."""
    failed_process_ids: list[int] = []
    for process in processes:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            failed_process_ids.append(process.pid)

    if failed_process_ids:
        failed_ids = ", ".join(str(process_id) for process_id in failed_process_ids)
        raise RuntimeError(f"Access denied while killing Chrome process ids: {failed_ids}")

    _, alive = psutil.wait_procs(processes, timeout=5)
    if alive:
        alive_ids = ", ".join(str(process.pid) for process in alive)
        raise RuntimeError(f"Chrome processes did not exit: {alive_ids}")


def ensure_chrome_stopped() -> bool:
    """Chrome 실행 여부를 확인하고 사용자 동의 시 모든 프로세스를 종료한다."""
    processes = find_chrome_processes()
    if not processes:
        return True

    LOGGER.warning("Detected Chrome processes count=%d", len(processes))
    while True:
        try:
            answer = (
                input("Chrome이 실행 중입니다. Chrome을 종료하고 계속하시겠습니까? (y/n): ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            LOGGER.info("Operation cancelled before terminating Chrome")
            return False

        if answer == "n":
            LOGGER.info("Operation cancelled; Chrome was not terminated")
            return False
        if answer == "y":
            kill_chrome_processes(processes)
            remaining_processes = find_chrome_processes()
            if remaining_processes:
                remaining_ids = ", ".join(str(process.pid) for process in remaining_processes)
                raise RuntimeError(f"Chrome processes still running: {remaining_ids}")
            LOGGER.info("Chrome processes terminated count=%d", len(processes))
            return True


def walk_url_nodes(folder: JsonObject) -> Iterable[JsonObject]:
    """폴더의 전체 하위 트리에서 URL 노드를 선순회한다."""
    for child in folder.get("children", []):
        if child.get("type") == "url":
            yield child
        elif child.get("type") == "folder":
            yield from walk_url_nodes(child)


def find_speed_dial(data: JsonObject) -> JsonObject:
    """기타 북마크 바로 아래의 유일한 Speed Dial 폴더를 반환한다."""
    children = data["roots"]["other"].get("children", [])
    matches = [
        child
        for child in children
        if child.get("type") == "folder" and child.get("name") == TARGET_FOLDER_NAME
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one Speed Dial folder, found {len(matches)}")
    return matches[0]


def select_candidates(speed_dial: JsonObject, rename_all: bool) -> list[JsonObject]:
    """실행 모드에 따라 이름 갱신 대상 URL 노드를 선택한다."""
    nodes = list(walk_url_nodes(speed_dial))
    if rename_all:
        return nodes
    return [node for node in nodes if node.get("name") == node.get("url")]


def is_http_url(url: str) -> bool:
    """HTTP 클라이언트로 조회 가능한 URL인지 확인한다."""
    return urlparse(url).scheme.lower() in {"http", "https"}


async def fetch_title(
    client: httpx.AsyncClient,
    node: JsonObject,
    semaphore: asyncio.Semaphore,
) -> tuple[JsonObject, str | None]:
    """단일 북마크 URL을 조회해 정규화된 HTML title을 반환한다."""
    url = str(node["url"])
    node_id = str(node.get("id", "unknown"))
    if not is_http_url(url):
        LOGGER.warning("Skipping unsupported URL scheme for bookmark id=%s", node_id)
        return node, None

    try:
        async with semaphore:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.TimeoutException:
        LOGGER.warning("Request timed out for bookmark id=%s", node_id)
        return node, None
    except httpx.HTTPStatusError as error:
        LOGGER.warning("HTTP %s for bookmark id=%s", error.response.status_code, node_id)
        return node, None
    except httpx.RequestError as error:
        LOGGER.warning("Request failed for bookmark id=%s: %s", node_id, type(error).__name__)
        return node, None

    soup = BeautifulSoup(response.content, "html.parser")
    if soup.title is None:
        LOGGER.warning("HTML title missing for bookmark id=%s", node_id)
        return node, None
    title = " ".join(soup.title.get_text(" ", strip=True).split())
    if not title:
        LOGGER.warning("HTML title empty for bookmark id=%s", node_id)
        return node, None
    return node, title


async def update_titles(candidates: list[JsonObject], concurrency: int = 8) -> int:
    """대상 URL을 병렬 조회하고 성공한 북마크 이름을 갱신한다."""
    semaphore = asyncio.Semaphore(concurrency)
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    ) as client:
        results = await asyncio.gather(
            *(fetch_title(client, node, semaphore) for node in candidates)
        )

    updated = 0
    for node, title in results:
        old_name = str(node["name"])
        if title is None or title == old_name:
            continue
        node["name"] = title
        updated += 1
        LOGGER.info(
            "Renamed bookmark id=%s old_name=%r new_name=%r",
            node.get("id", "unknown"),
            old_name,
            title,
        )
    return updated


def update_checksums(data: JsonObject) -> None:
    """Chromium BookmarkCodec과 동일한 MD5 및 SHA-256 체크섬을 계산한다."""
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()

    def add_text(value: str, *, utf16: bool = False) -> None:
        encoded = value.encode("utf-16-le" if utf16 else "utf-8")
        md5.update(encoded)
        sha256.update(encoded)

    def add_node(node: JsonObject) -> None:
        add_text(str(node["id"]))
        add_text(str(node["name"]), utf16=True)
        node_type = str(node["type"])
        add_text(node_type)
        if node_type == "url":
            add_text(str(node["url"]))
        else:
            for child in node.get("children", []):
                add_node(child)

    roots = data["roots"]
    for root_key in ROOT_KEYS:
        add_node(roots[root_key])
    data["checksum"] = md5.hexdigest()
    if "checksum_sha256" in data:
        data["checksum_sha256"] = sha256.hexdigest()


def load_bookmarks(path: Path) -> tuple[JsonObject, os.stat_result]:
    """북마크 파일을 읽고 전체 구조를 검증한다."""
    stat = path.stat()
    with path.open(encoding="utf-8") as file:
        data: JsonObject = json.load(file)
    BookmarkFile.model_validate(data)
    return data, stat


def save_bookmarks(path: Path, data: JsonObject, original_stat: os.stat_result) -> Path:
    """원본 변경 여부를 검사하고 백업 후 북마크 파일을 원자적으로 교체한다."""
    current_stat = path.stat()
    if (
        current_stat.st_mtime_ns != original_stat.st_mtime_ns
        or current_stat.st_size != original_stat.st_size
    ):
        raise RuntimeError("Bookmarks file changed during processing; save aborted")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = path.with_name(f"{path.name}.backup-{timestamp}")
    shutil.copy2(path, backup_path)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(data, temporary_file, ensure_ascii=False, indent=3)
            temporary_file.write("\n")
            temporary_path = Path(temporary_file.name)
        os.chmod(temporary_path, original_stat.st_mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return backup_path


async def run(path: Path, rename_all: bool) -> tuple[int, int, Path | None]:
    """북마크를 조회하고 변경 사항이 있으면 안전하게 저장한다."""
    data, original_stat = load_bookmarks(path)
    speed_dial = find_speed_dial(data)
    candidates = select_candidates(speed_dial, rename_all)
    if not candidates:
        return 0, 0, None

    updated = await update_titles(candidates)
    if updated == 0:
        return len(candidates), 0, None
    update_checksums(data)
    backup_path = save_bookmarks(path, data, original_stat)
    return len(candidates), updated, backup_path


def parse_args() -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(
        description="Speed Dial 북마크 이름을 웹 페이지 title로 갱신한다."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="rename_all",
        help="모든 URL 북마크를 갱신한다. 기본값은 name과 url이 같은 항목만 갱신한다.",
    )
    parser.add_argument(
        "--bookmarks-file",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def configure_logging() -> None:
    """타임스탬프와 코드 위치를 포함하는 로그 형식을 설정한다."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s:%(lineno)d %(message)s",
    )


def main() -> None:
    """CLI 진입점을 실행하고 처리 결과를 출력한다."""
    configure_logging()
    args = parse_args()
    try:
        if not ensure_chrome_stopped():
            return
        path = args.bookmarks_file or default_bookmarks_path()
        candidates, updated, backup_path = asyncio.run(run(path, args.rename_all))
    except (OSError, RuntimeError, ValueError, ValidationError, json.JSONDecodeError) as error:
        LOGGER.error("Bookmark update failed: %s", error)
        raise SystemExit(1) from error

    LOGGER.info("Processed candidates=%d updated=%d", candidates, updated)
    if backup_path is not None:
        LOGGER.info("Backup created path=%s", backup_path)


if __name__ == "__main__":
    main()
