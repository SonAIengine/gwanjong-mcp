"""Asset library 테스트."""

from __future__ import annotations

from pathlib import Path

from gwanjong_mcp.asset import AssetLibrary


def _make_lib(tmp_path: Path) -> AssetLibrary:
    return AssetLibrary(db_path=tmp_path / "test.db")


def test_save_and_get(tmp_path: Path) -> None:
    lib = _make_lib(tmp_path)
    asset = lib.save(
        {
            "content": "Try graph-tool-call for LLM tool routing",
            "asset_type": "cta",
            "platform": "devto",
            "tags": ["mcp", "tools"],
        }
    )

    assert asset.id.startswith("asset_")
    assert asset.asset_type == "cta"
    assert asset.usage_count == 0

    loaded = lib.get(asset.id)
    assert loaded is not None
    assert loaded.content == asset.content


def test_search_by_query(tmp_path: Path) -> None:
    lib = _make_lib(tmp_path)
    lib.save({"content": "MCP is the future of tool integration", "tags": ["mcp"]})
    lib.save({"content": "React hooks are powerful", "tags": ["react"]})

    results = lib.search(query="MCP")
    assert len(results) == 1
    assert "MCP" in results[0].content


def test_search_by_type(tmp_path: Path) -> None:
    lib = _make_lib(tmp_path)
    lib.save({"content": "Hook 1", "asset_type": "hook"})
    lib.save({"content": "CTA 1", "asset_type": "cta"})

    hooks = lib.search(asset_type="hook")
    assert len(hooks) == 1
    assert hooks[0].asset_type == "hook"


def test_use_increments_count(tmp_path: Path) -> None:
    lib = _make_lib(tmp_path)
    asset = lib.save({"content": "test content"})

    assert asset.usage_count == 0

    updated = lib.use(asset.id)
    assert updated is not None
    assert updated.usage_count == 1
    assert updated.last_used != ""

    updated2 = lib.use(asset.id)
    assert updated2 is not None
    assert updated2.usage_count == 2


def test_list_top(tmp_path: Path) -> None:
    lib = _make_lib(tmp_path)
    a1 = lib.save({"content": "popular"})
    lib.save({"content": "unpopular"})

    # a1을 여러 번 사용
    lib.use(a1.id)
    lib.use(a1.id)

    top = lib.list_top(limit=1)
    assert len(top) == 1
    assert top[0].id == a1.id


def test_list_recent(tmp_path: Path) -> None:
    lib = _make_lib(tmp_path)
    lib.save({"content": "first"})
    lib.save({"content": "second"})

    recent = lib.list_recent(limit=2)
    assert len(recent) == 2
    assert recent[0].content == "second"


def test_get_nonexistent(tmp_path: Path) -> None:
    lib = _make_lib(tmp_path)
    assert lib.get("nonexistent") is None
    assert lib.use("nonexistent") is None
