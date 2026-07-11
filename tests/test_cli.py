from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import psutil

from bookmark_rename.cli import (
    fetch_title,
    find_speed_dial,
    ensure_chrome_stopped,
    discover_chrome_profiles,
    load_bookmarks,
    save_bookmarks,
    select_candidates,
    select_bookmarks_path,
    update_checksums,
    update_titles,
)

type JsonObject = dict[str, Any]


class FakeProcess:
    def __init__(self, process_id: int, name: str = "chrome.exe") -> None:
        self.pid = process_id
        self.info = {"name": name}
        self.killed = False

    def kill(self) -> None:
        self.killed = True


def url_node(node_id: str, name: str, url: str) -> JsonObject:
    return {"id": node_id, "name": name, "type": "url", "url": url}


def folder_node(node_id: str, name: str, children: list[JsonObject]) -> JsonObject:
    return {
        "id": node_id,
        "name": name,
        "type": "folder",
        "children": children,
    }


def bookmark_data() -> JsonObject:
    speed_dial = folder_node(
        "4",
        "Speed Dial",
        [
            url_node("5", "https://same.example", "https://same.example"),
            url_node("6", "기존 이름", "https://named.example"),
            folder_node(
                "7",
                "하위 폴더",
                [url_node("8", "https://nested.example", "https://nested.example")],
            ),
        ],
    )
    return {
        "version": 1,
        "checksum": "old",
        "roots": {
            "bookmark_bar": folder_node("1", "북마크바", []),
            "other": folder_node(
                "2",
                "기타 북마크",
                [speed_dial, url_node("9", "외부", "https://outside.example")],
            ),
            "synced": folder_node("3", "모바일 북마크", []),
        },
    }


def create_profile(user_data_path: Path, directory_name: str) -> Path:
    profile_path = user_data_path / directory_name
    profile_path.mkdir(parents=True)
    bookmarks_path = profile_path / "Bookmarks"
    bookmarks_path.write_text("{}", encoding="utf-8")
    return bookmarks_path


def test_profile_discovery_sorts_default_and_numeric_profiles(tmp_path: Path) -> None:
    create_profile(tmp_path, "Profile 10")
    create_profile(tmp_path, "Default")
    create_profile(tmp_path, "Profile 2")
    create_profile(tmp_path, "Guest Profile")
    (tmp_path / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Default": {"name": "개인"},
                        "Profile 2": {"name": "업무"},
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    profiles = discover_chrome_profiles(tmp_path)

    assert [profile.directory_name for profile in profiles] == [
        "Default",
        "Profile 2",
        "Profile 10",
    ]
    assert [profile.display_name for profile in profiles] == ["개인", "업무", "Profile 10"]


def test_profile_selection_uses_single_profile_without_prompt(monkeypatch, tmp_path: Path) -> None:
    bookmarks_path = create_profile(tmp_path, "Profile 1")
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(AssertionError))

    assert select_bookmarks_path(tmp_path) == bookmarks_path


def test_profile_selection_prompts_for_multiple_profiles(monkeypatch, tmp_path: Path) -> None:
    create_profile(tmp_path, "Profile 1")
    selected_path = create_profile(tmp_path, "Profile 2")
    answers = iter(["invalid", "3", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert select_bookmarks_path(tmp_path) == selected_path


def test_chrome_check_continues_without_prompt_when_not_running(monkeypatch) -> None:
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter([]))
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(AssertionError))

    assert ensure_chrome_stopped() is True


def test_chrome_check_cancels_without_killing_on_n(monkeypatch) -> None:
    process = FakeProcess(100)
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter([process]))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    assert ensure_chrome_stopped() is False
    assert process.killed is False


def test_chrome_check_kills_all_processes_on_y(monkeypatch) -> None:
    processes = [FakeProcess(100), FakeProcess(101)]
    process_snapshots = iter([processes, []])
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter(next(process_snapshots)))
    monkeypatch.setattr(psutil, "wait_procs", lambda items, timeout: (items, []))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    assert ensure_chrome_stopped() is True
    assert all(process.killed for process in processes)


def test_select_candidates_respects_mode_and_nested_folders() -> None:
    speed_dial = find_speed_dial(bookmark_data())

    default_candidates = select_candidates(speed_dial, rename_all=False)
    all_candidates = select_candidates(speed_dial, rename_all=True)

    assert [node["id"] for node in default_candidates] == ["5", "8"]
    assert [node["id"] for node in all_candidates] == ["5", "6", "8"]


def test_find_speed_dial_only_uses_other_root() -> None:
    data = bookmark_data()
    data["roots"]["bookmark_bar"]["children"].append(
        folder_node("10", "Speed Dial", [url_node("11", "외부", "https://outside.example")])
    )

    assert find_speed_dial(data)["id"] == "4"


async def test_fetch_title_normalizes_html_title() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html><title>  Example &amp;\n Test  </title></html>",
            request=request,
        )

    node = url_node("5", "https://example.com", "https://example.com")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        _, title = await fetch_title(client, node, asyncio.Semaphore(1))

    assert title == "Example & Test"


async def test_update_titles_logs_old_and_new_names(monkeypatch, caplog) -> None:
    async def fake_fetch_title(client, node, semaphore):
        del client, semaphore
        return node, "변경된 이름"

    monkeypatch.setattr("bookmark_rename.cli.fetch_title", fake_fetch_title)
    node = url_node("5", "기존 이름", "https://example.com")

    with caplog.at_level(logging.INFO):
        updated = await update_titles([node])

    assert updated == 1
    assert node["name"] == "변경된 이름"
    assert "old_name='기존 이름' new_name='변경된 이름'" in caplog.text


async def test_update_titles_does_not_count_unchanged_name(monkeypatch, caplog) -> None:
    async def fake_fetch_title(client, node, semaphore):
        del client, semaphore
        return node, "같은 이름"

    monkeypatch.setattr("bookmark_rename.cli.fetch_title", fake_fetch_title)
    node = url_node("5", "같은 이름", "https://example.com")

    with caplog.at_level(logging.INFO):
        updated = await update_titles([node])

    assert updated == 0
    assert "Renamed bookmark" not in caplog.text


def test_checksum_changes_when_name_changes() -> None:
    original = bookmark_data()
    changed = deepcopy(original)

    update_checksums(original)
    find_speed_dial(changed)["children"][0]["name"] = "새 이름"
    update_checksums(changed)

    assert len(original["checksum"]) == 32
    assert original["checksum"] != changed["checksum"]


def test_save_bookmarks_creates_backup_and_replaces_file(tmp_path: Path) -> None:
    path = tmp_path / "Bookmarks"
    original = bookmark_data()
    path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    loaded, stat = load_bookmarks(path)
    find_speed_dial(loaded)["children"][0]["name"] = "새 이름"
    update_checksums(loaded)

    backup_path = save_bookmarks(path, loaded, stat)

    assert json.loads(backup_path.read_text(encoding="utf-8")) == original
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert find_speed_dial(saved)["children"][0]["name"] == "새 이름"
